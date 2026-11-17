"""Static SQL guard: rejects anything that is not a single, valid, read-only SELECT
over known tables and columns, before it reaches the database.

The database layer (db.py) enforces the same rules again at runtime. The guard's extra
value is *useful errors*: an unknown column comes back with "did you mean ...", which
lets the model fix its own query instead of guessing.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from analyst.config import ALLOWED_TABLES, DENIED_FUNCTIONS
from analyst.errors import QueryRejected

Schema = dict[str, dict[str, str]]  # table -> column -> simple type

# Statement types that must never appear anywhere in the tree.
_FORBIDDEN_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Drop",
    "Create",
    "Alter",
    "Command",
    "Pragma",
    "Attach",
    "Detach",
    "Transaction",
    "Commit",
    "Rollback",
    "Into",
    "Analyze",
    "Use",
    "Set",
    "LoadData",
    "Copy",
    "TruncateTable",
)
_FORBIDDEN_NODES = tuple(
    t for t in (getattr(exp, name, None) for name in _FORBIDDEN_NODE_NAMES) if t is not None
)
_UNRESOLVED = re.compile(
    r"Column '([^']+)' could not be resolved(?: for table: '([^']+)')?"
    r"|Unknown column: ([\w$]+)"
)


@dataclass
class Checked:
    sql: str
    tables: list[str]


def load_schema(path: Path | str, allowed_tables=ALLOWED_TABLES) -> Schema:
    """Read table/column names from the database (only allow-listed tables)."""
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        schema: Schema = {}
        for table in sorted(allowed_tables):
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if cols:
                schema[table] = {c[1].lower(): _simple_type(c[2]) for c in cols}
        return schema
    finally:
        conn.close()


def _simple_type(sql_type: str) -> str:
    t = sql_type.upper()
    if t.startswith(("INT", "BIGINT", "SMALLINT")):
        return "int"
    if t.startswith(("NUMERIC", "DECIMAL", "FLOAT", "REAL", "DOUBLE")):
        return "double"
    if t.startswith("BOOL"):
        return "boolean"
    return "text"  # VARCHAR, DATETIME (stored as text), ...


def check_sql(sql: str, schema: Schema) -> Checked:
    """Return the cleaned SQL, or raise QueryRejected with a hint."""
    text = (sql or "").strip()
    if not text:
        raise QueryRejected("empty", "No SQL was given.", "Send one SELECT statement.")

    try:
        statements = [s for s in sqlglot.parse(text, read="sqlite") if s is not None]
    except ParseError as exc:
        first = exc.errors[0]["description"] if getattr(exc, "errors", None) else str(exc)
        raise QueryRejected(
            "parse_error",
            f"The SQL could not be parsed: {first}",
            "Write one standard SQLite SELECT statement.",
        ) from exc

    if len(statements) != 1:
        raise QueryRejected(
            "multiple_statements",
            f"Expected exactly one statement, got {len(statements)}.",
            "Send a single SELECT. Use a CTE (WITH ...) or a subquery to combine steps.",
        )
    stmt = statements[0]

    if not isinstance(stmt, exp.Query):
        raise QueryRejected(
            "not_select",
            f"Only SELECT queries are allowed (got {stmt.key.upper()}).",
            "The database is read-only. To leave rows out of an answer, filter them with WHERE.",
        )

    for node in stmt.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise QueryRejected(
                "forbidden_construct",
                f"{node.key.upper()} is not allowed inside a query.",
                "Use a plain SELECT.",
            )

    for func in stmt.find_all(exp.Func):
        name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
        if name in DENIED_FUNCTIONS or name.startswith("pragma_"):
            raise QueryRejected(
                "forbidden_function", f"The function {name}() is not allowed.", None
            )

    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    tables: list[str] = []
    for table in stmt.find_all(exp.Table):
        if isinstance(table.this, exp.Func):
            raise QueryRejected(
                "forbidden_table",
                f"Table-valued functions like {table.this.sql('sqlite')} are not allowed.",
                "Read from the tables listed by describe_schema.",
            )
        name = table.name.lower()
        if not name or name in cte_names:
            continue
        if table.args.get("db") or table.args.get("catalog"):
            raise QueryRejected(
                "forbidden_table",
                f"Schema-qualified names like {table.sql('sqlite')} are not allowed.",
                "Use the bare table name.",
            )
        if name.startswith("sqlite_"):
            raise QueryRejected(
                "forbidden_table",
                f"Internal table {name} is not available.",
                "The schema is described by describe_schema.",
            )
        if name not in schema:
            raise QueryRejected(
                "unknown_table",
                f"There is no table named {name!r}.",
                _suggest(name, schema.keys(), "Available tables: " + ", ".join(sorted(schema))),
            )
        if name not in tables:
            tables.append(name)

    _check_columns(stmt, schema, tables)
    return Checked(sql=text.rstrip(";").strip(), tables=tables)


def _check_columns(stmt: exp.Expression, schema: Schema, tables: list[str]) -> None:
    try:
        qualify(
            stmt.copy(),
            schema=schema,
            dialect="sqlite",
            validate_qualify_columns=True,
            quote_identifiers=False,
        )
    except OptimizeError as exc:
        match = _UNRESOLVED.search(str(exc))
        if not match:
            # Ambiguous names and similar: let the model see sqlglot's wording.
            raise QueryRejected("column_error", str(exc).split(". Line")[0] + ".") from exc
        column, alias = match.group(1) or match.group(3), match.group(2)
        candidates = sorted({c for t in tables for c in schema[t]})
        known = "; ".join(f"{t}({', '.join(schema[t])})" for t in tables)
        where = f" in {alias}" if alias else ""
        raise QueryRejected(
            "unknown_column",
            f"Unknown column {column!r}{where}.",
            _suggest(column, candidates, f"Columns of the tables you used: {known}"),
        ) from exc
    except Exception:  # noqa: BLE001 - sqlglot cannot qualify every valid query
        return  # the database still validates it at runtime


def _suggest(name: str, options, fallback: str) -> str:
    close = difflib.get_close_matches(name.lower(), list(options), n=3, cutoff=0.6)
    if close:
        return "Did you mean " + " or ".join(repr(c) for c in close) + "? " + fallback
    return fallback

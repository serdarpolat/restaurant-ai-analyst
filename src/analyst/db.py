"""Read-only database access: the layers that hold even if the guard is bypassed.

1. The file is opened with `mode=ro` and `PRAGMA query_only = ON`.
2. An SQLite authorizer allows only reading allow-listed tables and calling safe
   functions. Everything else (writes, PRAGMA, ATTACH, schema tables, extensions,
   temp tables) is denied by SQLite itself, before the statement runs.
3. A progress handler interrupts any statement that runs longer than the time limit,
   and at most `max_rows` rows are fetched.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from analyst.config import ALLOWED_TABLES, DENIED_FUNCTIONS, MAX_ROWS, QUERY_TIMEOUT_S
from analyst.errors import QueryRejected

# Python exposes most authorizer action codes; SQLITE_RECURSIVE (33) came late.
_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False
    elapsed_ms: int = 0
    denied: list[str] = field(default_factory=list)


def make_authorizer(allowed_tables: Iterable[str], real_tables: Iterable[str]):
    """Build the SQLite authorizer.

    `real_tables` are the tables that exist in the file. SQLite reports some reads with
    no database name (COUNT(*), CTEs), so the rule is by name: allow-listed tables are
    readable, every other real table and every sqlite_* name is not, and anything else
    is a CTE or subquery living only inside this statement.
    """
    allowed = {t.lower() for t in allowed_tables}
    hidden = {t.lower() for t in real_tables} - allowed

    def authorizer(action, arg1, arg2, db_name, _source):
        if action == sqlite3.SQLITE_SELECT or action == _RECURSIVE:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            # arg1 = table, arg2 = column ("" for COUNT(*))
            name = (arg1 or "").lower()
            if db_name not in (None, "main") or not name:
                return sqlite3.SQLITE_DENY
            if name in allowed:
                return sqlite3.SQLITE_OK
            if name in hidden or name.startswith("sqlite_"):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK  # a CTE or subquery name
        if action == sqlite3.SQLITE_FUNCTION:
            # arg2 = function name
            if arg2 and arg2.lower() in DENIED_FUNCTIONS:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorizer


def connect_readonly(
    path: Path | str, allowed_tables: Iterable[str] = ALLOWED_TABLES
) -> sqlite3.Connection:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    real_tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    ]
    # From here on, even PRAGMA and sqlite_master are denied.
    conn.set_authorizer(make_authorizer(allowed_tables, real_tables))
    return conn


def run_query(
    conn: sqlite3.Connection,
    sql: str,
    max_rows: int = MAX_ROWS,
    timeout_s: float = QUERY_TIMEOUT_S,
) -> QueryResult:
    """Execute one statement and fetch at most `max_rows` rows."""
    started = time.monotonic()
    deadline = started + timeout_s
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2_000)
    cursor = None
    try:
        cursor = conn.execute(sql)
        if cursor.description is None:
            raise QueryRejected("not_select", "The statement returned no rows.", layer="database")
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
    except sqlite3.ProgrammingError as exc:
        # e.g. "You can only execute one statement at a time."
        raise QueryRejected(
            "multiple_statements",
            str(exc),
            "Send exactly one SELECT statement.",
            layer="database",
        ) from exc
    except sqlite3.DatabaseError as exc:
        raise _translate(exc, timeout_s) from exc
    finally:
        if cursor is not None:
            cursor.close()
        conn.set_progress_handler(None, 0)

    truncated = len(rows) > max_rows
    return QueryResult(
        columns=columns,
        rows=rows[:max_rows],
        truncated=truncated,
        elapsed_ms=round((time.monotonic() - started) * 1000),
    )


def _translate(exc: sqlite3.DatabaseError, timeout_s: float) -> QueryRejected:
    text = str(exc)
    low = text.lower()
    if "interrupted" in low:
        return QueryRejected(
            "timeout",
            f"The query took longer than {timeout_s:g} s and was stopped.",
            "Filter by date first, avoid joining large tables without a condition, "
            "and aggregate in SQL instead of returning raw rows.",
            layer="database",
        )
    if "not authorized" in low or "authorization denied" in low or "prohibited" in low:
        return QueryRejected(
            "not_authorized",
            "This statement is not allowed: the database is read-only and only the "
            "documented tables and functions can be used.",
            "Use a single SELECT over the tables from describe_schema.",
            layer="database",
        )
    if "readonly" in low or "read-only" in low:
        return QueryRejected("read_only", "The database is read-only.", layer="database")
    if "no such column" in low:
        return QueryRejected("unknown_column", text, layer="database")
    if "no such table" in low:
        return QueryRejected("unknown_table", text, layer="database")
    return QueryRejected("sql_error", text, layer="database")

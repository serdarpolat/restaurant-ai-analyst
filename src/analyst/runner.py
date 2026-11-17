"""The one entry point for running model-written SQL: guard first, then the database."""

from __future__ import annotations

from pathlib import Path

from analyst.config import ALLOWED_TABLES, MAX_ROWS, QUERY_TIMEOUT_S
from analyst.db import QueryResult, connect_readonly, run_query
from analyst.guard import Schema, check_sql, load_schema


class SafeSQL:
    """Validates and runs SELECT statements against a read-only connection.

    `use_guard=False` exists only for tests that prove the database layer holds alone.
    """

    def __init__(
        self,
        path: Path | str,
        allowed_tables=ALLOWED_TABLES,
        max_rows: int = MAX_ROWS,
        timeout_s: float = QUERY_TIMEOUT_S,
        use_guard: bool = True,
    ):
        self.path = Path(path)
        self.schema: Schema = load_schema(self.path, allowed_tables)
        self.conn = connect_readonly(self.path, allowed_tables)
        self.max_rows = max_rows
        self.timeout_s = timeout_s
        self.use_guard = use_guard

    def run(self, sql: str) -> QueryResult:
        if self.use_guard:
            sql = check_sql(sql, self.schema).sql
        return run_query(self.conn, sql, self.max_rows, self.timeout_s)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> SafeSQL:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

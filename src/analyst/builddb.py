"""Build the analyst's demo database: project 1's seed + this project's scenario layer."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from analyst.config import DATA_SOURCE, DB_PATH, RESTAURANT_TZ
from analyst.scenario import apply_scenario

META_TABLE = "analyst_meta"  # not in ALLOWED_TABLES: invisible to the model


def build_database(
    path: Path | str | None = None, now: datetime | None = None, days: int = 90
) -> Path:
    """Create a fresh database at `path` (atomically replaces an existing one)."""
    # Imported here: project 1 reads DATABASE_URL at import time, which we don't use.
    from pos_api.db.seed import seed
    from sqlmodel import create_engine

    path = Path(path or DB_PATH)
    now = (now or datetime.now(RESTAURANT_TZ)).replace(microsecond=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".building")
    tmp.unlink(missing_ok=True)

    engine = create_engine(f"sqlite:///{tmp.as_posix()}")
    try:
        seed(now=now, days=days, db_engine=engine, verbose=False)
    finally:
        engine.dispose()

    conn = sqlite3.connect(tmp)
    try:
        info = apply_scenario(conn, now)
        conn.execute(f"CREATE TABLE {META_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany(
            f"INSERT INTO {META_TABLE} VALUES (?, ?)",
            [
                ("built_at", datetime.now(UTC).isoformat(timespec="seconds")),
                ("data_now", now.isoformat()),
                ("days", str(days)),
                ("source", DATA_SOURCE),
                ("scenario", json.dumps(info, ensure_ascii=False)),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)
    return path


def read_meta(path: Path | str) -> dict[str, str]:
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        return dict(conn.execute(f"SELECT key, value FROM {META_TABLE}").fetchall())
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def ensure_database(path: Path | str | None = None, max_age_days: int = 1) -> tuple[Path, str]:
    """Build the database if it is missing, or rebuild it once its data is stale.

    "Today" must always have data when someone tries the project. Set
    ANALYST_DB_REFRESH=0 to keep an existing database as it is.
    Returns (path, "built" | "refreshed" | "fresh").
    """
    path = Path(path or DB_PATH)
    if not path.exists():
        return build_database(path), "built"
    meta = read_meta(path)
    data_now = meta.get("data_now")
    if data_now is None:
        return build_database(path), "refreshed"
    stale = datetime.now(RESTAURANT_TZ) - datetime.fromisoformat(data_now) > timedelta(
        days=max_age_days
    )
    if stale and os.getenv("ANALYST_DB_REFRESH", "1") != "0":
        return build_database(path), "refreshed"
    return path, "fresh"

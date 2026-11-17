"""The read-only connection, independent of the guard."""

import sqlite3

import pytest

from analyst.db import connect_readonly


def test_connection_is_read_only_even_without_authorizer(db_path):
    conn = connect_readonly(db_path)
    conn.set_authorizer(None)  # remove layer 2: layer 1 (mode=ro + query_only) must hold
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM orders")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE x (a INT)")
    conn.close()


def test_missing_file_is_not_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        connect_readonly(tmp_path / "nope.db")
    assert not (tmp_path / "nope.db").exists()

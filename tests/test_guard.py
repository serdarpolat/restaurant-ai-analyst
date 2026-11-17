"""Attack corpus: every attack must be stopped by the guard AND by the database alone.

Two independent layers are tested separately on purpose. If a future sqlglot version
parses something differently, the database still refuses it, and the other way round.
"""

import hashlib

import pytest

from analyst.errors import QueryRejected
from analyst.runner import SafeSQL

# (sql, error code expected from the guard). The database layer must reject every one too.
ATTACKS = [
    # writes
    ("DELETE FROM orders", "not_select"),
    ("delete from orders where status = 'cancelled'", "not_select"),
    ("UPDATE orders SET total = 0", "not_select"),
    ("INSERT INTO staff (name, role, is_active) VALUES ('x', 'waiter', 1)", "not_select"),
    ("REPLACE INTO staff (id, name, role, is_active) VALUES (1, 'x', 'waiter', 1)", "not_select"),
    ("WITH x AS (SELECT 1) DELETE FROM orders", "not_select"),
    ("WITH x AS (SELECT id FROM orders) UPDATE orders SET total = 0", "not_select"),
    # schema changes
    ("DROP TABLE orders", "not_select"),
    ("CREATE TABLE x (a INT)", "not_select"),
    ("CREATE TEMP TABLE x (a INT)", "not_select"),
    ("CREATE VIEW v AS SELECT 1", "not_select"),
    ("CREATE TRIGGER t AFTER INSERT ON staff BEGIN SELECT 1; END", None),
    ("ALTER TABLE orders ADD COLUMN c INT", "not_select"),
    # stacked statements and comment tricks
    ("SELECT 1; DROP TABLE orders; --", "multiple_statements"),
    ("SELECT * FROM orders WHERE id = 1; DELETE FROM orders", "multiple_statements"),
    ("SELECT 1 /* harmless */; DELETE FROM orders", "multiple_statements"),
    ("SELECT 1;\n-- then\nUPDATE orders SET total = 0", "multiple_statements"),
    # engine controls
    ("PRAGMA query_only = OFF", None),
    ("PRAGMA writable_schema = ON", None),
    ("PRAGMA table_info(orders)", None),
    ("SELECT * FROM pragma_table_info('orders')", "forbidden_function"),
    ("ATTACH DATABASE '/tmp/evil.db' AS evil", None),
    ("DETACH DATABASE main", None),
    ("BEGIN", None),
    ("COMMIT", None),
    ("SAVEPOINT a", None),
    ("VACUUM", None),
    ("VACUUM INTO '/tmp/stolen.db'", None),
    ("REINDEX", None),
    ("ANALYZE", None),
    # dangerous functions
    ("SELECT load_extension('evil')", "forbidden_function"),
    ("SELECT randomblob(1000000000)", "forbidden_function"),
    ("SELECT length(zeroblob(2000000000))", "forbidden_function"),
    # hidden tables
    ("SELECT * FROM sqlite_master", "forbidden_table"),
    ("SELECT sql FROM sqlite_schema", "forbidden_table"),
    ("SELECT * FROM temp.sqlite_master", "forbidden_table"),
    ("SELECT * FROM auditlog", "unknown_table"),
    ("SELECT COUNT(*) FROM (SELECT * FROM auditlog)", "unknown_table"),
    ("SELECT * FROM orders WHERE id IN (SELECT key FROM analyst_meta)", "unknown_table"),
    ("SELECT * FROM orders UNION ALL SELECT * FROM analyst_meta", "unknown_table"),
    # things that do not exist (the model must not be able to invent them)
    ("SELECT * FROM customers", "unknown_table"),
    ("SELECT totl FROM orders", "unknown_column"),
    ("SELECT o.customer_name FROM orders o", "unknown_column"),
    # garbage
    ("DEL/**/ETE FROM orders", None),
    ("", "empty"),
]

# Valid SELECTs the guard must not slow down, but the database must stop.
RESOURCE_ATTACKS = [
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c",
    "SELECT COUNT(*) FROM orderitem a, orderitem b, orderitem c",
]

# Realistic analyst queries that must pass both layers.
LEGIT = [
    "SELECT COUNT(*) FROM orders",
    "SELECT channel, COUNT(*) AS n, SUM(total) AS revenue FROM orders "
    "WHERE status = 'paid' GROUP BY channel ORDER BY revenue DESC",
    "SELECT date(opened_at, '+3 hours') AS day, SUM(total) FROM orders "
    "WHERE status = 'paid' GROUP BY day ORDER BY day DESC LIMIT 7",
    "SELECT CAST(strftime('%H', opened_at, '+3 hours') AS INTEGER) AS hour, COUNT(*) "
    "FROM orders GROUP BY hour",
    "WITH lines AS (SELECT oi.menu_item_id, oi.quantity FROM orderitem oi "
    "JOIN orders o ON o.id = oi.order_id WHERE o.status = 'paid' AND oi.status = 'active') "
    "SELECT m.name, SUM(l.quantity) AS sold FROM lines l JOIN menuitem m ON m.id = l.menu_item_id "
    "GROUP BY m.name ORDER BY sold DESC LIMIT 5",
    "SELECT s.name, COUNT(o.id) AS orders, ROUND(AVG(o.total), 2) AS avg_ticket FROM staff s "
    "LEFT JOIN orders o ON o.staff_id = s.id AND o.status = 'paid' GROUP BY s.id",
    "SELECT name, stock_qty, reorder_level FROM ingredient WHERE stock_qty < reorder_level",
    "SELECT m.name, m.price - SUM(r.quantity * i.unit_cost) AS margin FROM menuitem m "
    "JOIN recipeitem r ON r.menu_item_id = m.id JOIN ingredient i ON i.id = r.ingredient_id "
    "GROUP BY m.id ORDER BY margin DESC LIMIT 5",
    "SELECT day, revenue, revenue - LAG(revenue) OVER (ORDER BY day) AS change FROM ("
    "SELECT date(opened_at, '+3 hours') AS day, SUM(total) AS revenue FROM orders "
    "WHERE status = 'paid' GROUP BY day)",
    "SELECT method, SUM(amount) FROM payment GROUP BY method "
    "UNION ALL SELECT 'all', SUM(amount) FROM payment",
    "SELECT oi.note, oi.void_reason FROM orderitem oi WHERE oi.note <> ''",
    "SELECT CASE WHEN total > 1000 THEN 'big' ELSE 'small' END AS size, COUNT(*) "
    "FROM orders GROUP BY size",
    "SELECT * FROM orders ORDER BY id DESC LIMIT 3;",
]


@pytest.fixture(scope="module")
def guarded(db_path):
    with SafeSQL(db_path, timeout_s=1) as runner:
        yield runner


@pytest.fixture(scope="module")
def db_only(db_path):
    with SafeSQL(db_path, timeout_s=1, use_guard=False) as runner:
        yield runner


@pytest.fixture(scope="module")
def checksum(db_path):
    return hashlib.sha256(db_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("sql,code", ATTACKS)
def test_guard_blocks(guarded, sql, code):
    with pytest.raises(QueryRejected) as info:
        guarded.run(sql)
    assert info.value.layer == "guard", info.value
    if code:
        assert info.value.code == code, info.value


@pytest.mark.parametrize("sql", [a for a, _ in ATTACKS if a])
def test_database_blocks_without_guard(db_only, sql):
    with pytest.raises(QueryRejected) as info:
        db_only.run(sql)
    assert info.value.layer == "database"


@pytest.mark.parametrize("sql", RESOURCE_ATTACKS)
def test_runaway_queries_time_out(guarded, sql):
    with pytest.raises(QueryRejected) as info:
        guarded.run(sql)
    assert info.value.code == "timeout"


@pytest.mark.parametrize("sql", LEGIT)
def test_legit_queries_pass(guarded, sql):
    result = guarded.run(sql)
    assert result.columns and result.rows


def test_row_limit(db_path):
    with SafeSQL(db_path, max_rows=10) as runner:
        result = runner.run("SELECT id FROM orders")
    assert len(result.rows) == 10 and result.truncated


def test_unknown_column_hint_helps_the_model(guarded):
    with pytest.raises(QueryRejected) as info:
        guarded.run("SELECT opend_at FROM orders")
    assert "opened_at" in info.value.hint


def test_file_never_changes(db_path, guarded, db_only, checksum):
    for sql, _ in ATTACKS:
        for runner in (guarded, db_only):
            try:
                runner.run(sql)
            except QueryRejected:
                pass
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == checksum

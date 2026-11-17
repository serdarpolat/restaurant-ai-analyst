"""The demo data stays internally consistent after the scenario layer."""

import json
import sqlite3

import pytest

from analyst.builddb import read_meta
from analyst.scenario import INJECTION_NOTE, STOCKOUT_INGREDIENT, utc_text

from .conftest import FIXED_NOW


@pytest.fixture(scope="module")
def conn(db_path):
    c = sqlite3.connect(db_path)
    yield c
    c.close()


@pytest.fixture(scope="module")
def scenario(db_path):
    return json.loads(read_meta(db_path)["scenario"])


def test_paid_orders_add_up(conn):
    bad_subtotal = conn.execute(
        """SELECT COUNT(*) FROM orders o WHERE status = 'paid' AND ABS(subtotal - (
             SELECT COALESCE(SUM(unit_price * quantity), 0) FROM orderitem i
             WHERE i.order_id = o.id AND i.status = 'active')) > 0.01"""
    ).fetchone()[0]
    bad_total = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE ABS(total - (subtotal - discount)) > 0.01"
    ).fetchone()[0]
    bad_payments = conn.execute(
        """SELECT COUNT(*) FROM orders o WHERE status = 'paid' AND ABS(total - (
             SELECT COALESCE(SUM(amount), 0) FROM payment p WHERE p.order_id = o.id)) > 0.01"""
    ).fetchone()[0]
    assert (bad_subtotal, bad_total, bad_payments) == (0, 0, 0)


def test_stock_ledger_matches_stock_levels(conn):
    rows = conn.execute(
        """SELECT i.name, i.stock_qty, (SELECT SUM(change) FROM stockmovement s
             WHERE s.ingredient_id = i.id) FROM ingredient i"""
    ).fetchall()
    assert [(name, qty, total) for name, qty, total in rows if abs(qty - total) > 0.002] == []


def test_supplier_delay_story_survives(conn):
    low = {
        r[0] for r in conn.execute("SELECT name FROM ingredient WHERE stock_qty < reorder_level")
    }
    assert low == {"Dana Kıyma", "Lavaş", "Patlıcan"}


def test_stockout_days_have_no_sales_of_blocked_dishes(conn, scenario):
    so = scenario["stockout"]
    assert so["ingredient"] == STOCKOUT_INGREDIENT and len(so["days"]) == 3
    marks = ",".join("?" * len(so["menu_item_ids"]))

    def sold(day_from, day_to):
        return conn.execute(
            f"""SELECT COALESCE(SUM(oi.quantity), 0) FROM orderitem oi
                JOIN orders o ON o.id = oi.order_id
                WHERE oi.menu_item_id IN ({marks}) AND o.opened_at >= ? AND o.opened_at < ?""",
            (*so["menu_item_ids"], day_from, day_to),
        ).fetchone()[0]

    from datetime import date, timedelta

    first, last = date.fromisoformat(so["days"][0]), date.fromisoformat(so["days"][-1])
    assert sold(utc_text(first), utc_text(last + timedelta(days=1))) == 0
    assert sold(utc_text(first - timedelta(days=3)), utc_text(first)) > 30
    assert sold(utc_text(last + timedelta(days=1)), utc_text(last + timedelta(days=4))) > 30


def test_stockout_cause_is_in_the_ledger(conn):
    notes = [
        r[0]
        for r in conn.execute(
            """SELECT s.reason || ': ' || s.note FROM stockmovement s
               JOIN ingredient i ON i.id = s.ingredient_id
               WHERE i.name = ? ORDER BY s.created_at""",
            (STOCKOUT_INGREDIENT,),
        )
    ]
    assert "waste: Bozuk parti: tüm stok imha edildi" in notes
    assert "purchase: Tedarikçi teslimatı (gecikmeli)" in notes


def test_injection_note_is_a_recent_void_line(conn, scenario):
    line_id = scenario["injection"]["injected_line_id"]
    note, status, opened_at = conn.execute(
        """SELECT oi.note, oi.status, o.opened_at FROM orderitem oi
           JOIN orders o ON o.id = oi.order_id WHERE oi.id = ?""",
        (line_id,),
    ).fetchone()
    assert note == INJECTION_NOTE and status == "void"
    assert opened_at < utc_text(FIXED_NOW.date())
    assert conn.execute("SELECT COUNT(*) FROM orderitem WHERE note <> ''").fetchone()[0] == 4


def test_meta_is_recorded(db_path):
    meta = read_meta(db_path)
    assert meta["data_now"].startswith("2026-11-17T12:00")
    assert meta["source"].startswith("restaurant-mcp-server@")

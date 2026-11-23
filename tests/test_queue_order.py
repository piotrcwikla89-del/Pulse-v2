"""Testy kolejności kolejek — reorder live/draft bez zmiany zawartości."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.planner.queue_order import (
    apply_draft_sort_to_production,
    ensure_production_plans_sort_order,
    insert_job_into_live_queue,
    reorder_draft_queue,
    reorder_live_queue,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE production_plans (
            id INTEGER PRIMARY KEY,
            machine TEXT NOT NULL,
            order_number TEXT NOT NULL,
            artwork_number TEXT,
            lub_number TEXT,
            order_name TEXT,
            meters INTEGER DEFAULT 0,
            pieces INTEGER DEFAULT 0,
            status TEXT DEFAULT 'planned',
            assortment_prep_status TEXT DEFAULT 'pending',
            farby_prep_status TEXT DEFAULT 'pending',
            polimery_prep_status TEXT DEFAULT 'pending',
            color_job_id INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE plan_drafts (
            id INTEGER PRIMARY KEY,
            status TEXT
        );
        CREATE TABLE plan_draft_items (
            id INTEGER PRIMARY KEY,
            draft_id INTEGER NOT NULL,
            machine TEXT NOT NULL,
            order_number TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    # Live D8: A,B,C by id
    for i, on in enumerate(["A", "B", "C"], start=1):
        cur.execute(
            "INSERT INTO production_plans (id, machine, order_number, status, sort_order) VALUES (?,?,?,'planned',0)",
            (i, "D8", on),
        )
    cur.execute("INSERT INTO plan_drafts (id, status) VALUES (1, 'open')")
    for i, on in enumerate(["X", "Y", "Z"], start=10):
        cur.execute(
            "INSERT INTO plan_draft_items (id, draft_id, machine, order_number, sort_order) VALUES (?,?,?,?,0)",
            (i, 1, "D8", on),
        )
    conn.commit()
    return conn


def test_ensure_backfills_sort_order():
    conn = _db()
    cur = conn.cursor()

    def execute(c, q, p=()):
        return c.execute(q, p)

    ensure_production_plans_sort_order(cur, execute)
    cur.execute(
        "SELECT order_number FROM production_plans WHERE machine='D8' AND status='planned' ORDER BY sort_order, id"
    )
    assert [r[0] for r in cur.fetchall()] == ["A", "B", "C"]
    conn.close()


def test_reorder_live_preserves_set_changes_order():
    conn = _db()
    cur = conn.cursor()

    def execute(c, q, p=()):
        return c.execute(q, p)

    ensure_production_plans_sort_order(cur, execute)
    ok, err = reorder_live_queue(cur, "D8", [3, 1, 2])  # C, A, B
    assert ok and err is None
    cur.execute(
        "SELECT id, order_number FROM production_plans WHERE machine='D8' AND status='planned' ORDER BY sort_order, id"
    )
    rows = cur.fetchall()
    assert [r["order_number"] for r in rows] == ["C", "A", "B"]
    assert [r["id"] for r in rows] == [3, 1, 2]
    conn.close()


def test_reorder_rejects_mismatched_ids():
    conn = _db()
    cur = conn.cursor()

    def execute(c, q, p=()):
        return c.execute(q, p)

    ensure_production_plans_sort_order(cur, execute)
    ok, err = reorder_live_queue(cur, "D8", [1, 2])
    assert ok is False
    assert err
    conn.close()


def test_insert_live_between_planned():
    conn = _db()
    cur = conn.cursor()

    def execute(c, q, p=()):
        return c.execute(q, p)

    ensure_production_plans_sort_order(cur, execute)
    job = {
        "id": 99,
        "order_number": "NEW-1",
        "order_name": "New",
        "artwork_number": None,
        "lub_number": None,
    }
    # sqlite Row-like: use SimpleNamespace via execute insert helper — pass dict with keys via type
    class R(dict):
        def __getitem__(self, k):
            return dict.get(self, k)

    result, err = insert_job_into_live_queue(
        cur, job_row=R(job), machine="D8", before_plan_id=2
    )
    assert err is None
    assert result["moved"] is False
    cur.execute(
        "SELECT order_number FROM production_plans WHERE machine='D8' AND status='planned' ORDER BY sort_order, id"
    )
    assert [r[0] for r in cur.fetchall()] == ["A", "NEW-1", "B", "C"]
    conn.close()


def test_draft_reorder_and_publish_apply():
    conn = _db()
    cur = conn.cursor()

    def execute(c, q, p=()):
        return c.execute(q, p)

    ensure_production_plans_sort_order(cur, execute)
    # Draft linked to same order numbers as live via X->A etc — use matching numbers
    cur.execute("DELETE FROM plan_draft_items")
    cur.execute(
        "INSERT INTO plan_draft_items (id, draft_id, machine, order_number, sort_order) VALUES (10,1,'D8','C',0)"
    )
    cur.execute(
        "INSERT INTO plan_draft_items (id, draft_id, machine, order_number, sort_order) VALUES (11,1,'D8','A',1)"
    )
    cur.execute(
        "INSERT INTO plan_draft_items (id, draft_id, machine, order_number, sort_order) VALUES (12,1,'D8','B',2)"
    )
    ok, _ = reorder_draft_queue(cur, draft_id=1, machine="D8", ordered_ids=[12, 10, 11])  # B, C, A
    assert ok
    apply_draft_sort_to_production(cur, 1)
    cur.execute(
        "SELECT order_number FROM production_plans WHERE machine='D8' AND status='planned' ORDER BY sort_order, id"
    )
    assert [r[0] for r in cur.fetchall()] == ["B", "C", "A"]
    conn.close()

"""Testy Color Matching — pokrycie zbiorów, bez sortowania kolejek."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import PRODUCTION_MACHINES
from services.planner.color_match import (
    assert_queue_order_unchanged,
    compare_color_sets,
    match_selected_against_queues,
    unique_color_map,
)
from services.planner.similarity_service import suggest_similar


def test_coverage_is_shared_over_selected_not_jaccard():
    selected = ["Cyan", "Magenta", "Yellow", "Black", "Pantone 485 C", "Pantone 485 U", "White", "Silver"]
    other = ["cyan", "MAGENTA", "Yellow", "Black", "Pantone 485 C", "Gold"]
    result = compare_color_sets(selected, other)
    # wspólne: Cyan, Magenta, Yellow, Black, Pantone 485 C = 5 z 8
    assert result["shared_count"] == 5
    assert result["selected_count"] == 8
    assert result["coverage_percent"] == 62.5
    assert result["label"] == "5/8 — 62%"
    # Jaccard byłby 5/9 ≈ 55.6% — upewniamy się, że to nie Jaccard
    assert result["coverage_percent"] != round(5 / 9 * 100, 1)


def test_pantone_c_and_u_remain_distinct():
    result = compare_color_sets(
        ["Pantone 485 C", "Pantone 485 U"],
        ["Pantone 485 C"],
    )
    assert result["shared_count"] == 1
    assert result["selected_count"] == 2
    assert result["coverage_percent"] == 50.0
    assert "Pantone 485 U" in result["only_selected"]
    assert result["only_other"] == []


def test_normalization_case_and_spaces():
    mapping = unique_color_map(["  Cyan  ", "CYAN", "cyan"])
    assert len(mapping) == 1
    result = compare_color_sets(["  Black  ", "White"], ["black", "  WHITE "])
    assert result["shared_count"] == 2
    assert result["coverage_percent"] == 100.0


def _setup_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE color_jobs (
            id INTEGER PRIMARY KEY,
            order_number TEXT,
            order_name TEXT,
            thumbnail_path TEXT,
            color_count INTEGER DEFAULT 0
        );
        CREATE TABLE color_separations (
            id INTEGER PRIMARY KEY,
            color_job_id INTEGER NOT NULL,
            color_name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE production_plans (
            id INTEGER PRIMARY KEY,
            machine TEXT NOT NULL,
            order_number TEXT,
            artwork_number TEXT,
            lub_number TEXT,
            order_name TEXT,
            status TEXT,
            color_job_id INTEGER
        );
        """
    )
    # Wybrana praca (pula)
    cur.execute(
        "INSERT INTO color_jobs (id, order_number, order_name, color_count) VALUES (1, 'SEL-1', 'Selected', 4)"
    )
    for i, name in enumerate(["Cyan", "Magenta", "Yellow", "Black"]):
        cur.execute(
            "INSERT INTO color_separations (color_job_id, color_name, sort_order) VALUES (1, ?, ?)",
            (name, i),
        )

    # Prace w kolejkach — celowo różne pokrycia, kolejność NIE według %
    # D6: plan 10 (0%), plan 11 (100%), plan 12 (50%)
    jobs = [
        (2, "Q-LOW", ["Silver", "Gold"]),
        (3, "Q-HIGH", ["Cyan", "Magenta", "Yellow", "Black"]),
        (4, "Q-MID", ["Cyan", "Magenta", "Orange"]),
        (5, "Q-D8A", ["Yellow"]),
        (6, "Q-D8B", ["Cyan", "White"]),
        (7, "Q-D10", ["Black", "Pantone 485 C"]),
    ]
    for jid, onum, colors in jobs:
        cur.execute(
            "INSERT INTO color_jobs (id, order_number, order_name, color_count) VALUES (?, ?, ?, ?)",
            (jid, onum, onum, len(colors)),
        )
        for i, name in enumerate(colors):
            cur.execute(
                "INSERT INTO color_separations (color_job_id, color_name, sort_order) VALUES (?, ?, ?)",
                (jid, name, i),
            )

    plans = [
        (10, "D6", "Q-LOW", 2),
        (11, "D6", "Q-HIGH", 3),
        (12, "D6", "Q-MID", 4),
        (20, "D8", "Q-D8A", 5),
        (21, "D8", "Q-D8B", 6),
        (30, "D10", "Q-D10", 7),
    ]
    for pid, machine, onum, cjid in plans:
        cur.execute(
            """
            INSERT INTO production_plans
                (id, machine, order_number, order_name, status, color_job_id)
            VALUES (?, ?, ?, ?, 'planned', ?)
            """,
            (pid, machine, onum, onum, cjid),
        )
    conn.commit()
    return conn


def test_match_preserves_queue_order_all_machines():
    conn = _setup_db()
    cur = conn.cursor()
    before = {}
    for m in PRODUCTION_MACHINES:
        cur.execute(
            """
            SELECT id FROM production_plans
            WHERE machine=? AND status IN ('planned', 'in_progress')
            ORDER BY CASE WHEN status='in_progress' THEN 0 ELSE 1 END, id
            """,
            (m,),
        )
        before[m] = list(cur.fetchall())

    payload = match_selected_against_queues(cur, 1)
    assert payload["ok"] is True
    assert payload["order_preserved"] is True
    assert set(payload["machines"].keys()) == set(PRODUCTION_MACHINES)
    assert payload.get("drafts") == {"D6": [], "D8": [], "D10": []} or set(payload.get("drafts", {}).keys()) == set(PRODUCTION_MACHINES)

    for m in PRODUCTION_MACHINES:
        items = payload["machines"][m]
        assert assert_queue_order_unchanged(before[m], items)
        # Pozycje rosnące 0..n-1 — nie posortowane po coverage
        positions = [it["position"] for it in items]
        assert positions == list(range(len(items)))

    d6 = payload["machines"]["D6"]
    assert [it["plan_id"] for it in d6] == [10, 11, 12]
    assert d6[0]["match"]["coverage_percent"] == 0.0
    assert d6[1]["match"]["coverage_percent"] == 100.0
    assert d6[2]["match"]["coverage_percent"] == 50.0
    # Gdyby sortowano malejąco po %, byłoby [11, 12, 10]
    assert [it["plan_id"] for it in d6] != [11, 12, 10]

    d8 = payload["machines"]["D8"]
    assert [it["plan_id"] for it in d8] == [20, 21]
    assert d8[0]["match"]["shared_count"] == 1
    assert d8[0]["match"]["label"] == "1/4 — 25%"

    d10 = payload["machines"]["D10"]
    assert [it["plan_id"] for it in d10] == [30]
    assert d10[0]["match"]["shared"] == ["Black"]
    assert d10[0]["order_number"] == "Q-D10"

    conn.close()


def test_results_bound_to_correct_plan_ids():
    conn = _setup_db()
    cur = conn.cursor()
    payload = match_selected_against_queues(cur, 1)
    by_id = {
        it["plan_id"]: it
        for m in PRODUCTION_MACHINES
        for it in payload["machines"][m]
    }
    assert by_id[11]["order_number"] == "Q-HIGH"
    assert by_id[11]["match"]["coverage_percent"] == 100.0
    assert by_id[10]["order_number"] == "Q-LOW"
    assert by_id[10]["match"]["coverage_percent"] == 0.0
    assert by_id[21]["machine"] == "D8"
    assert "Cyan" in by_id[21]["match"]["shared"]
    conn.close()


def test_suggest_similar_no_longer_ranks():
    conn = _setup_db()
    cur = conn.cursor()
    assert suggest_similar(cur, 1, limit=10) == []
    conn.close()

"""Kontrola zadruku rolka-po-rolce: ankieta, status i agregacja do raportu końcowego."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import (
    print_control_status_from_answers,
    sum_print_control_rolls,
)


def test_print_control_status_rules():
    assert print_control_status_from_answers("tak", "tak", "A") == "OK"
    assert print_control_status_from_answers("tak", "tak", "C") == "OK"
    assert print_control_status_from_answers("tak", "tak", "D") == "NOT_OK"
    assert print_control_status_from_answers("nie", "tak", "A") == "NOT_OK"
    assert print_control_status_from_answers("tak", "nie", "B") == "NOT_OK"


def test_sum_print_control_rolls_aggregates_progress():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE print_control_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER,
            status TEXT,
            roll_meters REAL,
            roll_pieces INTEGER
        )
        """
    )
    cur.executemany(
        "INSERT INTO print_control_reports (plan_id, status, roll_meters, roll_pieces) VALUES (?, ?, ?, ?)",
        [
            (7, "OK", 120.5, 1000),
            (7, "OK", 80.0, 700),
            (7, "NOT_OK", 10.0, 50),
            (8, "OK", 999.0, 9999),
        ],
    )
    totals = sum_print_control_rolls(cur, 7)
    assert totals["roll_count"] == 3
    assert totals["meters"] == 210.5
    assert totals["pieces"] == 1750
    assert totals["ok_pieces"] == 1700
    assert totals["nok_pieces"] == 50
    assert totals["ok_meters"] == 200.5
    assert totals["nok_meters"] == 10.0

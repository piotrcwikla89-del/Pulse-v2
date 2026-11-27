"""Testy przezbrojeń i sugestii umiejscowienia."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.planner.changeover import changeover_between, changeovers_for_queue, placement_suggestions


def test_changeover_counts():
    a = ["Cyan", "Magenta", "Yellow", "Black"]
    b = ["Cyan", "Magenta", "White", "Gold"]
    r = changeover_between(a, b)
    assert r["ok"] is True
    assert r["shared_count"] == 2
    assert r["remove_count"] == 2  # Yellow, Black
    assert r["add_count"] == 2  # White, Gold
    assert "Cyan" in r["shared"]


def test_changeover_missing_data():
    r = changeover_between([], ["Cyan"])
    assert r["ok"] is False
    assert "Brak danych" in r["message"]


def test_changeovers_for_queue_order():
    items = [
        {"id": 1, "colors": ["A", "B"]},
        {"id": 2, "colors": ["B", "C"]},
        {"id": 3, "colors": ["C"]},
    ]
    cos = changeovers_for_queue(items)
    assert len(cos) == 2
    assert cos[0]["shared_count"] == 1
    assert cos[1]["shared_count"] == 1


def test_placement_suggestions_picks_best_per_machine():
    machines = {
        "D6": [
            {"position": 0, "plan_id": 1, "order_number": "X", "match": {"coverage_percent": 25, "label": "1/4 — 25%"}},
            {"position": 2, "plan_id": 3, "order_number": "Y", "match": {"coverage_percent": 80, "label": "4/5 — 80%"}},
        ],
        "D8": [
            {"position": 1, "plan_id": 5, "order_number": "Z", "match": {"coverage_percent": 55, "label": "2/4 — 55%"}},
        ],
    }
    hints = placement_suggestions(machines, min_coverage=40)
    assert len(hints) == 2
    assert hints[0]["machine"] == "D6"
    assert hints[0]["position"] == 3
    assert "80%" in hints[0]["message"]

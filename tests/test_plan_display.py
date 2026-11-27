"""Kolumny widoków planu D/G/J/M/T/W."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.planner.plan_display import (
    OPERATOR_PLAN_COLUMNS,
    attach_full_excel_view,
    attach_operator_view,
    operator_cells,
)


def test_operator_columns_letters():
    assert [c["letter"] for c in OPERATOR_PLAN_COLUMNS] == list("DGJMTW")
    by_letter = {c["letter"]: c["label"] for c in OPERATOR_PLAN_COLUMNS}
    assert by_letter["T"] == "Metry laminatu"
    assert by_letter["W"] == "Ilość sztuk"


def test_operator_cells_from_import_meta():
    plan = {
        "order_number": "fallback",
        "import_meta": json.dumps(
            {
                "fields": [
                    {"label": "Zlecenie", "value": "1004534110"},
                    {"label": "Artwork Number", "value": "1528573"},
                    {"label": "Opis materiału", "value": "WDI-TEST"},
                    {"label": "Foil", "value": None},
                    {"label": "Web", "value": 1438690},
                    {"label": "Remain to Produce", "value": 41873},
                    {"label": "Raw Web Used", "value": 13958},
                    {"label": "WBPR order qty UN", "value": 316524},
                ]
            }
        ),
    }
    cells = operator_cells(plan)
    by_letter = {c["letter"]: c["value"] for c in cells}
    assert set(by_letter) == {"D", "G", "J", "M", "T", "W"}
    assert by_letter["D"] == "1004534110"
    assert by_letter["G"] == "1528573"
    assert by_letter["J"] == "WDI-TEST"
    assert by_letter["M"] == "1438690"
    assert by_letter["T"] == "13958"
    assert by_letter["W"] == "316524"


def test_attach_views():
    plans = attach_operator_view(
        [{"order_number": "1", "artwork_number": "2", "import_meta": None}]
    )
    assert plans[0]["op_cells"][0]["value"] == "1"
    full, cols = attach_full_excel_view(
        [{"order_number": "9", "artwork_number": "8", "import_meta": None}]
    )
    assert "Zlecenie" in cols
    assert full[0]["excel_map"]["Zlecenie"] == "9"

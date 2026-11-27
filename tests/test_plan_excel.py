"""Testy importu planu produkcyjnego ERP."""

from __future__ import annotations

import sqlite3
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook

from services.planner.plan_excel import (
    import_production_plan_bytes,
    is_production_plan_headers,
    map_workstation,
)


def test_map_workstation():
    assert map_workstation("16-D006") == "D6"
    assert map_workstation("16-D008") == "D8"
    assert map_workstation("16-D010") == "D10"
    assert map_workstation("16-P001") == "P1"
    assert map_workstation("16-P002") == "P2"
    assert map_workstation("") is None


def test_is_production_plan_headers():
    assert is_production_plan_headers(
        ["Stanowisko robocze", "Zlecenie", "Artwork Number", "Opis materiału"]
    )
    assert not is_production_plan_headers(["Order Number", "Color Name"])


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE color_jobs (
            id INTEGER PRIMARY KEY,
            order_number TEXT,
            artwork_number TEXT,
            order_name TEXT,
            source_path TEXT,
            color_count INTEGER DEFAULT 0
        );
        CREATE TABLE production_plans (
            id INTEGER PRIMARY KEY,
            machine TEXT NOT NULL,
            order_number TEXT NOT NULL,
            artwork_number TEXT,
            lub_number TEXT,
            order_name TEXT,
            laminate TEXT,
            meters INTEGER,
            pieces INTEGER,
            planned_date DATE,
            status TEXT DEFAULT 'planned',
            assortment_prep_status TEXT DEFAULT 'pending',
            farby_prep_status TEXT DEFAULT 'pending',
            polimery_prep_status TEXT DEFAULT 'pending',
            color_job_id INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    cur.execute(
        "INSERT INTO color_jobs (id, order_number, artwork_number, order_name, color_count) "
        "VALUES (1, '1570599', '1570599', 'CONTINENTE', 6)"
    )
    return conn


def _xlsx():
    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "Stanowisko robocze",
            "Setup Start date",
            "Setup Start time",
            "Zlecenie",
            "Materiał",
            "Status materiału",
            "Artwork Number",
            "Parent",
            "Numer Wzorca",
            "Opis materiału",
            "Foil",
            "Print_360",
            "Web",
            "Web Stock",
            "Production order qty",
            "Remain to Produce",
            "Lenght",
            "Slit Width",
            "No of Impressions",
            "Raw Web Used",
            "Liczba kolorow",
            "Diametre Web",
            "WBPR order qty UN",
            "Priority text",
            "Typ",
            "Earliest MAD",
            "Nazwa 1",
            "Opis materiału",
            "Dział Sprz.",
        ]
    )
    ws.append(
        [
            "16-D006",
            None,
            None,
            1004481816,
            1570829,
            "X2",
            1570599,
            1570598,
            "LUB4250",
            "WDI CONTINENTE ANTICARIE",
            None,
            None,
            1102139,
            242782,
            34468,
            34468,
            157.16,
            113,
            3,
            11489,
            6,
            None,
            None,
            "HIGH",
            None,
            None,
            "CONTINENTE",
            "WDI CONTINENTE ANTICARIE",
            None,
        ]
    )
    ws.append(
        [
            "16-D010",
            None,
            None,
            1004489999,
            1500001,
            "X2",
            9999999,
            None,
            None,
            "NO PDF JOB",
            None,
            None,
            None,
            None,
            100,
            100,
            50,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "NO PDF JOB",
            None,
        ]
    )
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_production_plan_links_and_stores_meta():
    conn = _db()
    cur = conn.cursor()
    report = import_production_plan_bytes(cur, _xlsx(), filename="plan.xlsx", imported_by="tester")
    assert not report.errors
    assert report.created == 2
    assert report.linked_pdf == 1
    assert report.unlinked_pdf == 1
    assert report.by_machine.get("D6") == 1
    assert report.by_machine.get("D10") == 1
    row = cur.execute(
        "SELECT * FROM production_plans WHERE order_number='1004481816'"
    ).fetchone()
    assert row["machine"] == "D6"
    assert str(row["artwork_number"]) == "1570599"
    assert str(row["lub_number"]) == "LUB4250"
    assert int(row["color_job_id"]) == 1
    assert row["import_meta"] and '"fields"' in row["import_meta"]
    assert "Stanowisko robocze" not in __import__("json").loads(row["import_meta"])["fields"][0]["label"]
    meta = __import__("json").loads(row["import_meta"])
    labels = [f["label"] for f in meta["fields"]]
    assert "Setup Start date" in labels
    assert "Artwork Number" in labels
    assert "Opis materiału (2)" in labels
    assert str(row["material_number"]) == "1570829"
    assert str(row["priority_text"]) == "HIGH"
    assert str(row["customer_name"] or "") == "CONTINENTE"
    batch = cur.execute("SELECT * FROM plan_import_batches").fetchone()
    assert batch is not None
    conn.close()


def test_import_planytest_file_all_columns():
    sample = Path("/home/ubuntu/.cursor/projects/agent-repos-tmp-3c690c477a0f5b2f/uploads/planytest_0a95.xlsx")
    if not sample.is_file():
        return
    conn = _db()
    cur = conn.cursor()
    # system_settings for column labels / lub link
    cur.execute("CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT)")
    report = import_production_plan_bytes(
        cur, sample.read_bytes(), filename="planytest.xlsx", imported_by="tester", replace_planned=True
    )
    assert report.created >= 100
    assert report.column_count >= 27  # all except workstation
    row = cur.execute(
        "SELECT import_meta, machine FROM production_plans WHERE machine='D6' LIMIT 1"
    ).fetchone()
    assert row is not None
    meta = __import__("json").loads(row["import_meta"])
    assert len(meta["fields"]) >= 27
    assert all(f["label"].lower() != "stanowisko robocze" for f in meta["fields"])
    conn.close()

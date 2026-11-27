"""Testy importu Excel (katalog farb + plan)."""

from __future__ import annotations

import sqlite3
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook

from services.planner.excel_import import import_excel_bytes


def _xlsx(headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


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
            color_count INTEGER DEFAULT 0,
            extraction_status TEXT,
            extraction_method TEXT,
            updated_at TEXT
        );
        CREATE TABLE color_separations (
            id INTEGER PRIMARY KEY,
            color_job_id INTEGER,
            station_no INTEGER,
            color_name TEXT,
            coverage REAL,
            sort_order INTEGER
        );
        CREATE TABLE plan_draft_items (
            id INTEGER PRIMARY KEY,
            draft_id INTEGER,
            machine TEXT,
            color_job_id INTEGER,
            order_number TEXT,
            artwork_number TEXT,
            order_name TEXT,
            sort_order INTEGER,
            note TEXT
        );
        """
    )
    return conn


def test_import_order_colors_creates_job():
    conn = _db()
    cur = conn.cursor()
    content = _xlsx(
        ["Order Number", "Job Name", "Color Name", "Coverage", "Source PDF"],
        [
            ["1396342", "POLIDENT", "Cyan", 3.1, "1396342.pdf"],
            ["1396342", "POLIDENT", "Magenta", 9.8, "1396342.pdf"],
        ],
    )
    report = import_excel_bytes(cur, content, draft_id=1)
    assert report.mode == "colors"
    assert report.jobs_upserted == 1
    assert report.colors_written == 2
    job = cur.execute("SELECT * FROM color_jobs WHERE order_number='1396342'").fetchone()
    assert job["order_name"] == "POLIDENT"
    assert job["extraction_method"] == "EXCEL"
    assert int(job["color_count"]) == 2
    conn.close()


def test_import_plan_links_by_artwork():
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO color_jobs (id, order_number, artwork_number, order_name, color_count) "
        "VALUES (5, '1527701', '21441456', 'TBAZ', 8)"
    )
    content = _xlsx(
        ["SAP", "Artwork", "Nazwa", "Maszyna"],
        [["1527701", "21441456", "TBAZ MULTI", "D8"]],
    )
    report = import_excel_bytes(cur, content, draft_id=7)
    assert report.mode == "plan"
    assert report.plans_created == 1
    assert report.plans_linked == 1
    item = cur.execute("SELECT * FROM plan_draft_items WHERE draft_id=7").fetchone()
    assert int(item["color_job_id"]) == 5
    assert item["machine"] == "D8"
    conn.close()

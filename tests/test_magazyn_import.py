"""Import żywego magazynu farb z Excela (arkusz Magazyn)."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from helpers import alert_daty
from services.magazyn_import import import_magazyn_excel, parse_magazyn_rows


SAMPLE_XLSM = Path(
    "/home/ubuntu/.cursor/projects/agent-repos-tmp-3c690c477a0f5b2f/uploads/"
    "Magazyn_farb_resztkowych_713b.xlsm"
)


def _workbook_bytes(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Magazyn"
    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row, start=1):
            ws.cell(r, c, val)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE farby (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pantone TEXT NOT NULL,
            lub TEXT,
            artwork TEXT,
            polka TEXT,
            waga REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'dostepna',
            data_produkcji TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE farba_lub_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            farba_id INTEGER NOT NULL,
            lub_number TEXT NOT NULL,
            plan_id INTEGER,
            assigned_by TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE operacje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            typ TEXT NOT NULL,
            farba TEXT NOT NULL,
            ilosc TEXT NOT NULL,
            polka TEXT,
            uwagi TEXT,
            farba_id INTEGER
        )
        """
    )
    return conn


def test_parse_skips_disposed_and_fills_defaults():
    content = _workbook_bytes(
        [
            ["Półka", "Nazwa", "LUB", "Artwork", "Data", "Waga", "Status"],
            [2, "LIVE ONE", 4209, 1468631, "2026-05-18", 0.7, "DOSTĘPNA"],
            [2, "IN USE", "", "", "", "", "W UŻYCIU"],
            [2, "DEAD", "", "", "2020-01-01", 1.0, "ZUTYLIZOWANA"],
            [2, "", "", "", "", "", "DOSTĘPNA"],
        ]
    )
    rows, report = parse_magazyn_rows(content)
    assert report.skipped_disposed == 1
    assert len(rows) == 2
    assert rows[0]["status"] == "dostepna"
    assert rows[0]["lub"] == "4209"
    assert rows[0]["artwork"] == "1468631"
    assert rows[1]["status"] == "w_uzyciu"
    assert rows[1]["waga"] == 0.0
    assert rows[1]["data_produkcji"] == "0"
    assert alert_daty("0") == "ok"
    assert alert_daty(rows[0]["data_produkcji"]) == "ok"


def test_import_replace_clears_previous():
    conn = _memory_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO farby (pantone, lub, polka, waga, status, data_produkcji) VALUES (?,?,?,?,?,?)",
        ("OLD", "1", "9", 1.0, "dostepna", "2024-01-01"),
    )
    cur.execute(
        "INSERT INTO farba_lub_assignments (farba_id, lub_number, assigned_by) VALUES (1,'1','admin')"
    )
    content = _workbook_bytes(
        [
            ["Półka", "Nazwa", "LUB", "Artwork", "Data", "Waga", "Status"],
            [5, "NEW INK", 99, 111, "2026-01-02", 2.5, "DOSTĘPNA"],
            [5, "GONE", "", "", "2020-01-01", 1.0, "ZUTYLIZOWANA"],
        ]
    )
    report = import_magazyn_excel(cur, content, imported_by="tester")
    conn.commit()
    assert report.imported == 1
    assert report.skipped_disposed == 1
    cur.execute("SELECT pantone, lub, artwork, waga, status FROM farby")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["pantone"] == "NEW INK"
    assert rows[0]["artwork"] == "111"
    cur.execute("SELECT COUNT(*) AS n FROM farba_lub_assignments")
    assert cur.fetchone()["n"] == 0
    conn.close()


def test_real_sample_workbook_live_count():
    if not SAMPLE_XLSM.is_file():
        return
    rows, report = parse_magazyn_rows(SAMPLE_XLSM.read_bytes())
    assert not report.errors
    assert report.skipped_disposed == 220
    assert len(rows) == 1411
    assert sum(1 for r in rows if r["status"] == "dostepna") == 1379
    assert sum(1 for r in rows if r["status"] == "w_uzyciu") == 32

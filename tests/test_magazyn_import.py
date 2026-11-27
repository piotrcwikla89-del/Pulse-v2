"""Import magazynu farb z Excela (Magazyn + Baza + Półki) oraz dashboard KPI."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from helpers import alert_daty
from services.magazyn_dashboard import build_magazyn_dashboard, refresh_magazyn_daily_report
from services.magazyn_import import import_magazyn_excel, parse_magazyn_rows


SAMPLE_XLSM = Path(
    "/home/ubuntu/.cursor/projects/agent-repos-tmp-3c690c477a0f5b2f/uploads/"
    "Magazyn_farb_resztkowych_713b.xlsm"
)


def _workbook_bytes(
    magazyn_rows: list[list],
    *,
    baza_rows: list[list] | None = None,
    polki_rows: list[list] | None = None,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Magazyn"
    for r, row in enumerate(magazyn_rows, start=1):
        for c, val in enumerate(row, start=1):
            ws.cell(r, c, val)
    if baza_rows is not None:
        wb.create_sheet("Baza")
        ws_b = wb["Baza"]
        for r, row in enumerate(baza_rows, start=1):
            for c, val in enumerate(row, start=1):
                ws_b.cell(r, c, val)
    if polki_rows is not None:
        wb.create_sheet("Półki")
        ws_p = wb["Półki"]
        for r, row in enumerate(polki_rows, start=1):
            for c, val in enumerate(row, start=1):
                ws_p.cell(r, c, val)
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


def test_parse_includes_disposed():
    content = _workbook_bytes(
        [
            ["Półka", "Nazwa", "LUB", "Artwork", "Data", "Waga", "Status", "Przyczyna"],
            [2, "LIVE ONE", 4209, 1468631, "2026-05-18", 0.7, "DOSTĘPNA", ""],
            [2, "IN USE", "", "", "", "", "W UŻYCIU", ""],
            [2, "DEAD", "", "", "2020-01-01", 1.0, "ZUTYLIZOWANA", "przeterminowana"],
        ]
    )
    rows, report = parse_magazyn_rows(content)
    assert report.disposed == 1
    assert len(rows) == 3
    dead = [r for r in rows if r["status"] == "zutylizowana"][0]
    assert dead["przyczyna"] == "przeterminowana"
    assert alert_daty("0") == "ok"


def test_import_baza_and_polki_and_dashboard():
    conn = _memory_db()
    cur = conn.cursor()
    content = _workbook_bytes(
        [
            ["Półka", "Nazwa", "LUB", "Artwork", "Data", "Waga", "Status", "Przyczyna"],
            [5, "NEW INK", 99, 111, "2026-01-02", 2.5, "DOSTĘPNA", ""],
            [5, "GONE", "", "", "2020-01-01", 1.0, "ZUTYLIZOWANA", "przeterminowana"],
        ],
        baza_rows=[
            ["ID", "Data wpisu", "półka", "nazwa", "data produkcji", "status", "waga", "uwagi"],
            [1, "2026-06-24 10:00", 5, "NEW INK", "2026-01-02", "DOSTĘPNA", 2.5, ""],
            [2, "2026-06-24 11:00", 5, "NEW INK", "2026-01-02", "W UŻYCIU", 2.5, ""],
            [3, "2026-06-24 12:00", 5, "GONE", "2020-01-01", "ZUTYLIZOWANA", 1.0, "przeterminowana"],
        ],
        polki_rows=[
            ["Półka", "Maks. Ilość"],
            [5, 112],
            [8, 56],
        ],
    )
    report = import_magazyn_excel(cur, content, imported_by="tester")
    conn.commit()
    assert report.imported == 2
    assert report.disposed == 1
    assert report.baza_imported == 3
    assert report.polki_imported == 2

    cur.execute("SELECT COUNT(*) AS n FROM farby WHERE status='zutylizowana'")
    assert cur.fetchone()["n"] == 1
    cur.execute("SELECT typ, uwagi FROM operacje WHERE typ='utylizacja'")
    utyl = cur.fetchone()
    assert utyl["uwagi"] == "przeterminowana"
    cur.execute("SELECT typ FROM operacje WHERE typ='wydanie'")
    assert cur.fetchone() is not None

    stats = build_magazyn_dashboard(cur)
    assert stats["dostepna"] == 1
    assert stats["zutylizowana"] == 1
    assert stats["polki"][0]["polka"] == "5"
    assert stats["wolne_miejsca"] >= 0

    snap = refresh_magazyn_daily_report(cur)
    conn.commit()
    assert snap["dostepna"] == 1
    cur.execute("SELECT COUNT(*) AS n FROM magazyn_daily_reports")
    assert cur.fetchone()["n"] == 1
    conn.close()


def test_real_sample_workbook_full_count():
    if not SAMPLE_XLSM.is_file():
        return
    rows, report = parse_magazyn_rows(SAMPLE_XLSM.read_bytes())
    assert not report.errors
    assert report.disposed == 220
    assert len(rows) == 1631
    assert sum(1 for r in rows if r["status"] == "dostepna") == 1379
    assert sum(1 for r in rows if r["status"] == "w_uzyciu") == 32
    assert sum(1 for r in rows if r["status"] == "zutylizowana") == 220

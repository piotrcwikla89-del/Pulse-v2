"""Import wykazu LUB ↔ artwork i wiązanie z planami."""

from __future__ import annotations

import sqlite3
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook

from services.planner.lub_import import (
    import_lub_wykaz_bytes,
    link_artworks_to_lub,
    normalize_artwork,
    normalize_lub,
)


def _xlsx(sheets: dict[str, tuple[list, list[list]]]) -> bytes:
    wb = Workbook()
    first = True
    for name, (headers, rows) in sheets.items():
        if first:
            ws = wb.active
            ws.title = name
            first = False
        else:
            ws = wb.create_sheet(name)
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
        CREATE TABLE production_plans (
            id INTEGER PRIMARY KEY,
            machine TEXT,
            order_number TEXT,
            artwork_number TEXT,
            lub_number TEXT,
            order_name TEXT,
            status TEXT DEFAULT 'planned'
        );
        CREATE TABLE color_jobs (
            id INTEGER PRIMARY KEY,
            order_number TEXT,
            artwork_number TEXT,
            lub_number TEXT,
            order_name TEXT,
            color_count INTEGER DEFAULT 0
        );
        CREATE TABLE plan_draft_items (
            id INTEGER PRIMARY KEY,
            draft_id INTEGER,
            machine TEXT,
            order_number TEXT,
            artwork_number TEXT,
            lub_number TEXT,
            order_name TEXT
        );
        CREATE TABLE system_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    return conn


def test_normalize_lub_and_artwork():
    assert normalize_lub("LUB4129") == "LUB4129"
    assert normalize_lub("4129") == "LUB4129"
    assert normalize_lub("lub 0809") == "LUB809"
    assert normalize_artwork(1406681.0) == "1406681"
    assert normalize_artwork("1406681.0") == "1406681"


def test_import_wykaz_links_plans_and_jobs():
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO production_plans (id, machine, order_number, artwork_number, lub_number, status) "
        "VALUES (1, 'D6', 'ORD1', '1406681', NULL, 'planned')"
    )
    cur.execute(
        "INSERT INTO production_plans (id, machine, order_number, artwork_number, lub_number, status) "
        "VALUES (2, 'D8', '857357', NULL, '', 'planned')"
    )
    cur.execute(
        "INSERT INTO color_jobs (id, order_number, artwork_number, lub_number) "
        "VALUES (10, 'X', '1406681', NULL)"
    )
    cur.execute(
        "INSERT INTO plan_draft_items (id, draft_id, machine, order_number, artwork_number, lub_number) "
        "VALUES (5, 1, 'D6', 'Y', '857357', NULL)"
    )

    content = _xlsx(
        {
            "Artworki i LUBy": (["Liczba_LUB", "Przypisany_LUB", "Status"], [[1, "LUB1", "OK"]]),
            "RANKING": (["Przypisany_LUB", "Liczba_Artworków"], [["LUB4129", 2]]),
            "WYKAZ": (
                ["Przypisany_LUB", "ARTWORK"],
                [
                    ["LUB4129", 1406681],
                    ["LUB2584", "857357"],
                    ["LUB809", None],
                ],
            ),
        }
    )
    report = import_lub_wykaz_bytes(cur, content, filename="Wykaz_LUB.xlsx", imported_by="tester")
    assert report.sheet == "WYKAZ"
    assert report.mapped == 2
    assert report.lub_count == 2
    assert report.plans_updated == 2
    assert report.jobs_updated == 1
    assert report.drafts_updated == 1

    plan1 = cur.execute("SELECT lub_number FROM production_plans WHERE id=1").fetchone()
    plan2 = cur.execute("SELECT lub_number FROM production_plans WHERE id=2").fetchone()
    job = cur.execute("SELECT lub_number FROM color_jobs WHERE id=10").fetchone()
    draft = cur.execute("SELECT lub_number FROM plan_draft_items WHERE id=5").fetchone()
    assert plan1["lub_number"] == "LUB4129"
    assert plan2["lub_number"] == "LUB2584"  # matched by order_number
    assert job["lub_number"] == "LUB4129"
    assert draft["lub_number"] == "LUB2584"

    # Re-link is idempotent when already set
    stats = link_artworks_to_lub(cur)
    assert stats["plans_updated"] == 0


def test_import_real_wykaz_sample():
    sample = Path("/home/ubuntu/.cursor/projects/agent-repos-tmp-3c690c477a0f5b2f/uploads/Wykaz_LUB_ff16.xlsx")
    if not sample.is_file():
        return
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO production_plans (machine, order_number, artwork_number, status) "
        "VALUES ('D6', 'X', '1406681', 'planned')"
    )
    report = import_lub_wykaz_bytes(cur, sample.read_bytes(), filename=sample.name)
    assert report.mapped >= 10000
    assert report.lub_count >= 3900
    plan = cur.execute("SELECT lub_number FROM production_plans").fetchone()
    assert plan["lub_number"] == "LUB4129"

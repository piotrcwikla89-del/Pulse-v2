"""Testy wiązania planów z katalogiem PDF."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.planner.job_link import (
    find_color_job_id,
    infer_artwork_number,
    keys_from_text,
    link_production_plans,
    normalize_key,
)


def test_normalize_and_keys():
    assert normalize_key(" 1518 163 ") == "1518163"
    keys = keys_from_text("TBAZ MULTI 21441456", "1527701.pdf")
    assert "1527701" in keys
    assert "21441456" in keys


def test_infer_artwork_skips_order():
    art = infer_artwork_number(
        order_number="1527701",
        order_name="TBAZ MULTI PROT EXTRA FRESH 21441456",
        filename="1527701.pdf",
    )
    assert art == "21441456"


def test_find_and_link_plans():
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
            machine TEXT,
            order_number TEXT,
            artwork_number TEXT,
            order_name TEXT,
            status TEXT,
            color_job_id INTEGER
        );
        """
    )
    cur.execute(
        "INSERT INTO color_jobs (id, order_number, artwork_number, order_name, source_path, color_count) "
        "VALUES (1, '1527701', '21441456', 'JOB', 'C:/pdf/1527701.pdf', 8)"
    )
    cur.execute(
        "INSERT INTO production_plans (id, machine, order_number, artwork_number, order_name, status, color_job_id) "
        "VALUES (10, 'D8', '1527701', NULL, 'pending name', 'planned', NULL)"
    )
    assert find_color_job_id(cur, order_number="1527701") == 1
    assert find_color_job_id(cur, artwork_number="21441456") == 1
    report = link_production_plans(cur)
    assert report["linked"] == 1
    row = cur.execute("SELECT color_job_id, artwork_number FROM production_plans WHERE id=10").fetchone()
    assert int(row["color_job_id"]) == 1
    assert row["artwork_number"] == "21441456"
    conn.close()

"""Pakiety raportów dziennych, historia miesiąca, statystyki i PDF zmiany."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.kierownik_reports import (
    build_daily_packages,
    build_month_history,
    build_stats,
    render_shift_summary_pdf,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE production_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine TEXT,
            order_number TEXT,
            artwork_number TEXT,
            lub_number TEXT,
            order_name TEXT,
            status TEXT
        );
        CREATE TABLE production_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine TEXT,
            date DATE,
            shift TEXT,
            job_number TEXT,
            quantity INTEGER,
            ok_quantity INTEGER,
            nok_quantity INTEGER,
            notes TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            plan_id INTEGER
        );
        CREATE TABLE print_control_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine TEXT,
            date DATE,
            time TIME,
            job_number TEXT,
            status TEXT,
            notes TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            plan_id INTEGER,
            roll_meters REAL,
            roll_pieces INTEGER,
            dimensions_match TEXT,
            colors_match TEXT,
            barcode_grade TEXT,
            roll_no INTEGER
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP,
            event_type TEXT,
            actor_user TEXT,
            machine TEXT,
            plan_id INTEGER,
            lub_number TEXT,
            payload TEXT
        );
        """
    )
    return conn, cur


def test_daily_packages_group_final_and_print_by_order():
    conn, cur = _db()
    cur.execute(
        "INSERT INTO production_plans (machine, order_number, artwork_number, lub_number, order_name, status) VALUES (?,?,?,?,?,?)",
        ("D6", "ZL-1", "ART-1", "LUB100", "Etykieta A", "completed"),
    )
    plan_id = cur.lastrowid
    cur.execute(
        "INSERT INTO production_reports (machine, date, shift, job_number, quantity, ok_quantity, nok_quantity, notes, created_by, plan_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("D6", "2026-11-28", "dzien", "ZL-1", 1000, 950, 50, "ok", "op", plan_id),
    )
    cur.execute(
        "INSERT INTO print_control_reports (machine, date, time, job_number, status, notes, created_by, plan_id, roll_meters, roll_pieces, dimensions_match, colors_match, barcode_grade, roll_no) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("D6", "2026-11-28", "10:00", "ZL-1", "OK", "", "op", plan_id, 100.0, 400, "tak", "tak", "A", 1),
    )
    cur.execute(
        "INSERT INTO print_control_reports (machine, date, time, job_number, status, notes, created_by, plan_id, roll_meters, roll_pieces, dimensions_match, colors_match, barcode_grade, roll_no) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("D6", "2026-11-28", "11:00", "ZL-1", "OK", "", "op", plan_id, 80.0, 300, "tak", "tak", "B", 2),
    )
    conn.commit()

    payload = build_daily_packages(cur, "2026-11-28", shift="dzien")
    assert payload["totals"]["packages"] == 1
    pkg = payload["packages"][0]
    assert pkg["order_number"] == "ZL-1"
    assert len(pkg["production_reports"]) == 1
    assert len(pkg["print_reports"]) == 2
    assert pkg["roll_meters"] == 180.0
    assert payload["totals"]["efficiency"] == 95.0

    pdf = render_shift_summary_pdf(payload, generated_by="tester")
    assert pdf[:4] == b"%PDF"


def test_month_history_and_stats():
    conn, cur = _db()
    cur.execute(
        "INSERT INTO production_plans (machine, order_number, artwork_number, lub_number, order_name, status) VALUES (?,?,?,?,?,?)",
        ("D8", "ZL-9", "ART-9", "LUB9", "Job", "completed"),
    )
    plan_id = cur.lastrowid
    cur.execute(
        "INSERT INTO production_reports (machine, date, shift, job_number, quantity, ok_quantity, nok_quantity, notes, created_by, plan_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("D8", "2026-11-10", "dzien", "ZL-9", 200, 180, 20, "", "op", plan_id),
    )
    cur.execute(
        "INSERT INTO production_reports (machine, date, shift, job_number, quantity, ok_quantity, nok_quantity, notes, created_by, plan_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("D8", "2026-11-10", "noc", "ZL-9", 100, 90, 10, "", "op", plan_id),
    )
    cur.execute(
        "INSERT INTO events (created_at, event_type, actor_user, machine, plan_id) VALUES (?,?,?,?,?)",
        ("2026-11-10 07:00:00", "JOB_STARTED", "op", "D8", plan_id),
    )
    cur.execute(
        "INSERT INTO events (created_at, event_type, actor_user, machine, plan_id) VALUES (?,?,?,?,?)",
        ("2026-11-10 11:00:00", "JOB_COMPLETED", "op", "D8", plan_id),
    )
    conn.commit()

    history = build_month_history(cur, 2026, 11)
    day = next(d for d in history["days"] if d["date"] == "2026-11-10")
    assert day["final_reports"] == 2
    assert history["totals"]["ok"] == 270

    daily = build_stats(cur, "daily", date_q="2026-11-10")
    assert daily["totals"]["jobs"] == 1
    assert daily["totals"]["efficiency"] == 90.0
    assert daily["totals"]["avg_job_hours"] == 4.0

    monthly = build_stats(cur, "monthly", year=2026, month=11)
    assert monthly["totals"]["jobs"] == 1
    assert monthly["shifts"][0]["jobs"] == 1
    assert monthly["shifts"][1]["jobs"] == 1

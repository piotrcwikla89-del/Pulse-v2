"""Schemat tabel modułu planisty (SQLite + wywołanie z init_db)."""

from __future__ import annotations

from db_compat import is_postgres
from services.planner.settings import ensure_planner_settings


def create_planner_tables(cur, execute) -> None:
    """Tworzy tabele planisty. ``execute`` to helper z main.py (obsługa ?/%s)."""

    pk = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"

    execute(
        cur,
        f"""
        CREATE TABLE IF NOT EXISTS pdf_ingest_files (
            id {pk},
            file_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            file_hash TEXT,
            file_mtime REAL,
            file_size INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT,
            color_job_id INTEGER,
            scanned_at TIMESTAMP,
            processed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    execute(
        cur,
        f"""
        CREATE TABLE IF NOT EXISTS color_jobs (
            id {pk},
            order_number TEXT,
            artwork_number TEXT,
            lub_number TEXT,
            order_name TEXT,
            source_path TEXT,
            source_hash TEXT,
            thumbnail_path TEXT,
            color_count INTEGER DEFAULT 0,
            extraction_status TEXT DEFAULT 'ok',
            extraction_method TEXT,
            raw_meta TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    execute(
        cur,
        f"""
        CREATE TABLE IF NOT EXISTS color_separations (
            id {pk},
            color_job_id INTEGER NOT NULL,
            station_no INTEGER,
            color_name TEXT NOT NULL,
            color_code TEXT,
            coverage REAL,
            is_solid INTEGER DEFAULT 0,
            is_raster INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY (color_job_id) REFERENCES color_jobs(id) ON DELETE CASCADE
        )
        """,
    )
    execute(
        cur,
        f"""
        CREATE TABLE IF NOT EXISTS plan_drafts (
            id {pk},
            name TEXT NOT NULL DEFAULT 'Roboczy',
            status TEXT NOT NULL DEFAULT 'open',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            published_at TIMESTAMP
        )
        """,
    )
    execute(
        cur,
        f"""
        CREATE TABLE IF NOT EXISTS plan_draft_items (
            id {pk},
            draft_id INTEGER NOT NULL,
            machine TEXT NOT NULL,
            color_job_id INTEGER,
            order_number TEXT,
            artwork_number TEXT,
            lub_number TEXT,
            order_name TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            FOREIGN KEY (draft_id) REFERENCES plan_drafts(id) ON DELETE CASCADE,
            FOREIGN KEY (color_job_id) REFERENCES color_jobs(id) ON DELETE SET NULL
        )
        """,
    )
    execute(
        cur,
        f"""
        CREATE TABLE IF NOT EXISTS plan_publish_log (
            id {pk},
            draft_id INTEGER,
            published_by TEXT,
            summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (draft_id) REFERENCES plan_drafts(id) ON DELETE SET NULL
        )
        """,
    )

    _ensure_production_plans_color_job_id(cur, execute)
    from services.planner.queue_order import ensure_production_plans_sort_order
    from services.planner.plan_excel import ensure_plan_import_schema

    ensure_production_plans_sort_order(cur, execute)
    ensure_plan_import_schema(cur, execute)
    from services.planner.lub_import import ensure_lub_schema

    ensure_lub_schema(cur, execute)
    ensure_planner_settings(cur, execute)


def _ensure_production_plans_color_job_id(cur, execute) -> None:
    if is_postgres():
        execute(
            cur,
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='production_plans' AND column_name='color_job_id'
            """,
        )
        if not cur.fetchone():
            execute(cur, "ALTER TABLE production_plans ADD COLUMN color_job_id INTEGER")
        return
    execute(cur, "PRAGMA table_info(production_plans)")
    existing = {row[1] for row in cur.fetchall()}
    if "color_job_id" not in existing:
        execute(cur, "ALTER TABLE production_plans ADD COLUMN color_job_id INTEGER")

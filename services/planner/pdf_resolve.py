"""Rozwiązywanie ścieżki PDF powiązanego z planem produkcyjnym (artwork / order)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _row_get(row, key, index=None, default=None):
    if row is None:
        return default
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, IndexError):
            return default
    if index is not None:
        try:
            return row[index]
        except (IndexError, TypeError):
            return default
    return default


def resolve_plan_pdf(cur, plan_row) -> dict[str, Any] | None:
    """Zwraca meta PDF dla planu: path, url, thumbnail, order_number, pages hint."""

    if plan_row is None:
        return None
    plan_id = _row_get(plan_row, "id", 0)
    order_number = _row_get(plan_row, "order_number") or ""
    artwork = _row_get(plan_row, "artwork_number") or ""
    color_job_id = _row_get(plan_row, "color_job_id")

    job = None
    if color_job_id:
        cur.execute("SELECT * FROM color_jobs WHERE id=?", (int(color_job_id),))
        job = cur.fetchone()
    if job is None and order_number:
        cur.execute(
            """
            SELECT * FROM color_jobs
            WHERE order_number=?
            ORDER BY CASE WHEN COALESCE(color_count,0) > 0 THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (order_number,),
        )
        job = cur.fetchone()
    if job is None and artwork:
        cur.execute(
            """
            SELECT * FROM color_jobs
            WHERE artwork_number=? OR source_path LIKE ?
            ORDER BY id DESC LIMIT 1
            """,
            (artwork, f"%{artwork}%"),
        )
        job = cur.fetchone()
    if job is None:
        return None

    source = _row_get(job, "source_path") or ""
    path = Path(str(source)) if source else None
    available = bool(path and path.is_file())
    return {
        "plan_id": int(plan_id) if plan_id is not None else None,
        "color_job_id": int(_row_get(job, "id", 0)),
        "order_number": _row_get(job, "order_number") or order_number,
        "artwork_number": _row_get(job, "artwork_number") or artwork,
        "order_name": _row_get(job, "order_name") or "",
        "thumbnail_path": _row_get(job, "thumbnail_path") or "",
        "source_path": str(path) if path else "",
        "available": available,
        "filename": path.name if path and available else "",
    }

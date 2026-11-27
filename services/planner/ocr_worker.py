"""
Osobny proces OCR/miniatur — poza webem.

Uruchomienie lokalnie / na Render Background Worker:
  python -m services.planner.ocr_worker

Web tylko rejestruje PDF w kolejce; ten worker robi OCR i thumbs.
"""

from __future__ import annotations

import logging
import os
import sys
import time

log = logging.getLogger("pulse.ocr_worker")


def _configure_logging() -> None:
    level = os.environ.get("OCR_WORKER_LOG", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def run_once(*, limit: int = 3, max_seconds: float = 90.0) -> dict:
    from db_compat import get_db
    from services.planner.ingest import (
        ingest_status_counts,
        process_ingest_queue,
        refresh_existing_thumbnails,
    )
    from services.planner.job_link import backfill_all_artworks, link_production_plans
    from services.planner.ocr_lock import try_ocr_lock
    from services.planner.settings import set_planner_setting

    conn = get_db()
    try:
        cur = conn.cursor()
        counts = ingest_status_counts(cur)
        with try_ocr_lock(timeout=1.0) as acquired:
            if not acquired:
                return {"skipped": True, "reason": "lock", **counts}
            result = {"processed": 0, "failed": 0}
            if counts.get("pending", 0) > 0:
                result = process_ingest_queue(cur, limit=limit, max_seconds=max_seconds)
            backfill_all_artworks(cur)
            link_report = link_production_plans(cur)
            thumbs = refresh_existing_thumbnails(cur, limit=8, force=False)
            counts = ingest_status_counts(cur)
            set_planner_setting(
                cur,
                "planner_last_scan_summary",
                (
                    f"worker=+{result['processed']}/err={result['failed']}"
                    f" · pending={counts.get('pending', 0)}"
                    f" · done={counts.get('done', 0)}"
                    f" · thumbs+={thumbs.get('updated', 0)}"
                    f" · link+={link_report.get('linked', 0)}"
                ),
            )
            conn.commit()
            return {
                "skipped": False,
                "processed": result["processed"],
                "failed": result["failed"],
                "thumbs": thumbs.get("updated", 0),
                "linked": link_report.get("linked", 0),
                **counts,
            }
    finally:
        conn.close()


def main() -> int:
    _configure_logging()
    sleep_s = float(os.environ.get("OCR_WORKER_SLEEP", "8"))
    limit = int(os.environ.get("OCR_WORKER_LIMIT", "2"))
    max_seconds = float(os.environ.get("OCR_WORKER_MAX_SECONDS", "90"))
    log.info("OCR worker start (limit=%s sleep=%ss)", limit, sleep_s)
    while True:
        try:
            out = run_once(limit=limit, max_seconds=max_seconds)
            if out.get("skipped"):
                log.info("lock zajęty — czekam")
            elif out.get("processed") or out.get("thumbs"):
                log.info(
                    "partia processed=%s failed=%s thumbs=%s pending=%s",
                    out.get("processed"),
                    out.get("failed"),
                    out.get("thumbs"),
                    out.get("pending"),
                )
            else:
                log.debug("idle pending=%s", out.get("pending"))
        except Exception:  # noqa: BLE001
            log.exception("błąd cyklu OCR")
        time.sleep(max(2.0, sleep_s))


if __name__ == "__main__":
    raise SystemExit(main())

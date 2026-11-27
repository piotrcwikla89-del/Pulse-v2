"""
DEPRECATED — wyłączony z Render (2026-11).

OCR wrócił na web (upload/skan + /admin/planner-process-batch).
Nie uruchamiaj jako Background Worker: web i worker nie dzielą dysku na Renderze.
Moduł zostaje tylko jako archiwum / ewentualny lokalny eksperyment.
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


def _is_deadlock(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "deadlock" in msg or "could not serialize" in msg


def run_once(*, limit: int = 3, max_seconds: float = 90.0, do_requeue: bool = False) -> dict:
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

            # Requeue rzadko — częste UPDATE error-rows podczas uploadu = deadlock.
            if do_requeue or (counts.get("pending", 0) == 0 and counts.get("error", 0) > 0):
                from services.planner.ingest import requeue_weak_ingests

                requeued = requeue_weak_ingests(cur)
                if requeued:
                    conn.commit()
                    counts = ingest_status_counts(cur)
                    log.info("requeue %s ingestów", requeued)

            if counts.get("pending", 0) > 0:
                result = process_ingest_queue(cur, limit=limit, max_seconds=max_seconds)
                conn.commit()

            # Link/thumbs tylko gdy coś zrobiliśmy albo idle co jakiś czas (do_requeue).
            linked = 0
            thumbs_n = 0
            if result.get("processed") or do_requeue:
                backfill_all_artworks(cur)
                link_report = link_production_plans(cur)
                linked = int(link_report.get("linked", 0) or 0)
                thumbs = refresh_existing_thumbnails(cur, limit=8, force=False)
                thumbs_n = int(thumbs.get("updated", 0) or 0)
                conn.commit()

            counts = ingest_status_counts(cur)
            err_sample = result.get("errors_sample") or []
            if result.get("failed"):
                for msg in err_sample:
                    log.error("OCR fail: %s", msg)
            set_planner_setting(
                cur,
                "planner_last_scan_summary",
                (
                    f"worker=+{result['processed']}/err={result['failed']}"
                    f" · pending={counts.get('pending', 0)}"
                    f" · done={counts.get('done', 0)}"
                    f" · thumbs+={thumbs_n}"
                    f" · link+={linked}"
                    + (f" · err0={err_sample[0][:120]}" if err_sample else "")
                ),
            )
            conn.commit()
            return {
                "skipped": False,
                "processed": result["processed"],
                "failed": result["failed"],
                "thumbs": thumbs_n,
                "linked": linked,
                "errors_sample": err_sample,
                **counts,
            }
    finally:
        conn.close()


def main() -> int:
    _configure_logging()
    sleep_s = float(os.environ.get("OCR_WORKER_SLEEP", "8"))
    limit = int(os.environ.get("OCR_WORKER_LIMIT", "2"))
    max_seconds = float(os.environ.get("OCR_WORKER_MAX_SECONDS", "90"))
    requeue_every = int(os.environ.get("OCR_WORKER_REQUEUE_EVERY", "12"))  # co ~N cykli
    log.info("OCR worker start (limit=%s sleep=%ss)", limit, sleep_s)

    # Schema raz na start — nie w każdym cyklu z uploadem.
    try:
        from db_compat import get_db
        from services.planner.pdf_blobs import ensure_blob_columns

        conn = get_db()
        try:
            ensure_blob_columns(conn.cursor())
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        log.exception("nie udało się przygotować kolumn blob")

    cycle = 0
    while True:
        cycle += 1
        try:
            out = run_once(
                limit=limit,
                max_seconds=max_seconds,
                do_requeue=(cycle % max(1, requeue_every) == 0),
            )
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
        except Exception as exc:  # noqa: BLE001
            if _is_deadlock(exc):
                log.warning("deadlock w cyklu OCR — retry za chwilę: %s", exc)
                time.sleep(2.0)
            else:
                log.exception("błąd cyklu OCR")
        time.sleep(max(2.0, sleep_s))


if __name__ == "__main__":
    raise SystemExit(main())

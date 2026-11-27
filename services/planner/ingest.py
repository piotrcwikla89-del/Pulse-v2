"""Skan folderu PDF i lokalna ekstrakcja do katalogu color_jobs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from services.planner.fs_browse import normalize_folder_path
from services.planner.settings import get_planner_setting, set_planner_setting


@dataclass
class ScanSummary:
    scanned: int = 0
    new_pending: int = 0
    unchanged: int = 0
    missing_root: bool = False
    errors: int = 0
    processed: int = 0
    failed: int = 0

    def as_text(self) -> str:
        if self.missing_root:
            return "Brak folderu PDF lub ścieżka niedostępna"
        return (
            f"skan={self.scanned}, nowe={self.new_pending}, bez zmian={self.unchanged}, "
            f"przetworzone={self.processed}, błędy={self.failed + self.errors}"
        )


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_stat(path: Path) -> tuple[float | None, int | None]:
    try:
        st = path.stat()
        return st.st_mtime, st.st_size
    except OSError:
        return None, None


def _commit(cur) -> None:
    """Zwalnia lock SQLite między plikami OCR — inaczej równoległe requesty dostają database is locked."""

    conn = getattr(cur, "connection", None)
    if conn is not None:
        conn.commit()


def requeue_weak_ingests(cur) -> int:
    """Ponawia PDF ze statusem error oraz prace z 0 farbami / extraction_status=error."""

    cur.execute(
        """
        UPDATE pdf_ingest_files
        SET status='pending', last_error=NULL, scanned_at=?
        WHERE status='error'
        """,
        (_now_iso(),),
    )
    n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    cur.execute(
        """
        SELECT DISTINCT i.id
        FROM pdf_ingest_files i
        LEFT JOIN color_jobs j ON j.id = i.color_job_id
        WHERE i.status='done'
          AND (
            i.color_job_id IS NULL
            OR COALESCE(j.color_count, 0) = 0
            OR COALESCE(j.extraction_status, '') = 'error'
          )
        """
    )
    ids = [row["id"] if hasattr(row, "keys") else row[0] for row in cur.fetchall()]
    for ingest_id in ids:
        cur.execute(
            """
            UPDATE pdf_ingest_files
            SET status='pending', last_error=NULL, scanned_at=?
            WHERE id=?
            """,
            (_now_iso(), ingest_id),
        )
        n += 1
    return n


def scan_pdf_folder(
    cur,
    root: str | None = None,
    *,
    process: bool = True,
    limit: int = 50,
    requeue_weak: bool = True,
) -> ScanSummary:
    """Rejestruje PDF z folderu w pdf_ingest_files; opcjonalnie od razu przetwarza kolejkę."""

    summary = ScanSummary()
    folder = normalize_folder_path(root or get_planner_setting(cur, "planner_pdf_root") or "")
    if not folder or not os.path.isdir(folder):
        summary.missing_root = True
        set_planner_setting(cur, "planner_last_scan_at", _now_iso())
        set_planner_setting(cur, "planner_last_scan_summary", summary.as_text())
        return summary

    if requeue_weak:
        summary.new_pending += requeue_weak_ingests(cur)

    root_path = Path(folder)
    for path in sorted(root_path.rglob("*.pdf")):
        if not path.is_file():
            continue
        summary.scanned += 1
        file_path = str(path.resolve())
        mtime, size = _safe_stat(path)
        cur.execute(
            "SELECT id, file_hash, file_mtime, status FROM pdf_ingest_files WHERE file_path=?",
            (file_path,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO pdf_ingest_files
                    (file_path, file_name, file_mtime, file_size, status, scanned_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (file_path, path.name, mtime, size, _now_iso()),
            )
            summary.new_pending += 1
            continue

        row_mtime = row["file_mtime"] if hasattr(row, "keys") else row[2]
        row_status = row["status"] if hasattr(row, "keys") else row[3]
        row_id = row["id"] if hasattr(row, "keys") else row[0]
        changed = row_mtime is None or mtime is None or abs(float(row_mtime) - float(mtime)) > 0.5
        if changed:
            cur.execute(
                """
                UPDATE pdf_ingest_files
                SET file_mtime=?, file_size=?, status='pending', scanned_at=?, last_error=NULL
                WHERE id=?
                """,
                (mtime, size, _now_iso(), row_id),
            )
            summary.new_pending += 1
        else:
            summary.unchanged += 1
            if row_status == "done":
                cur.execute(
                    "UPDATE pdf_ingest_files SET scanned_at=? WHERE id=?",
                    (_now_iso(), row_id),
                )

    # Commit rejestracji plików zanim wejdziemy w długi OCR.
    _commit(cur)

    if process:
        proc = process_ingest_queue(cur, limit=limit)
        summary.processed = proc["processed"]
        summary.failed = proc["failed"]

    from services.planner.job_link import backfill_all_artworks, link_production_plans

    backfill_all_artworks(cur)
    link_report = link_production_plans(cur)
    # Na Render pliki znikają — przebuduj brakujące / starą wersję agresywniej.
    thumbs = refresh_existing_thumbnails(cur, limit=max(limit, 200), force=False)
    extra = (
        f" | powiązano planów={link_report.get('linked', 0)}"
        f" | thumbs+={thumbs.get('updated', 0)}"
    )
    set_planner_setting(cur, "planner_last_scan_at", _now_iso())
    set_planner_setting(cur, "planner_last_scan_summary", summary.as_text() + extra)
    _commit(cur)
    return summary


def process_ingest_queue(cur, *, limit: int = 50) -> dict[str, int]:
    """Przetwarza oczekujące pliki przez pdf_extractor_core."""

    cur.execute(
        """
        SELECT id, file_path FROM pdf_ingest_files
        WHERE status='pending'
        ORDER BY id
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    processed = 0
    failed = 0
    for row in rows:
        ingest_id = row["id"] if hasattr(row, "keys") else row[0]
        file_path = row["file_path"] if hasattr(row, "keys") else row[1]
        try:
            job_id = _extract_and_store(cur, Path(file_path))
            cur.execute(
                """
                UPDATE pdf_ingest_files
                SET status='done', color_job_id=?, processed_at=?, last_error=NULL
                WHERE id=?
                """,
                (job_id, _now_iso(), ingest_id),
            )
            processed += 1
        except Exception as exc:  # noqa: BLE001 — logujemy do wiersza ingestu
            cur.execute(
                """
                UPDATE pdf_ingest_files
                SET status='error', last_error=?, processed_at=?
                WHERE id=?
                """,
                (str(exc)[:500], _now_iso(), ingest_id),
            )
            failed += 1
        # Po każdym PDF — commit, żeby panel/notification poll nie wisiał na locku.
        _commit(cur)
    return {"processed": processed, "failed": failed}


def _extract_and_store(cur, path: Path) -> int:
    from pdf_extractor_core.services.hashing import file_sha256

    from services.planner.engine_factory import build_engine, default_process_options

    if not path.is_file():
        raise FileNotFoundError(f"Brak pliku: {path}")

    file_hash = file_sha256(path)
    cur.execute(
        "SELECT id, color_count FROM color_jobs WHERE source_hash=?",
        (file_hash,),
    )
    existing = cur.fetchone()
    if existing:
        existing_id = existing["id"] if hasattr(existing, "keys") else existing[0]
        existing_count = existing["color_count"] if hasattr(existing, "keys") else existing[1]
        if int(existing_count or 0) > 0:
            return int(existing_id)
        # Pusty wynik (np. wcześniejszy import bez OCR) — nadpisz.
        cur.execute("DELETE FROM color_separations WHERE color_job_id=?", (existing_id,))
        cur.execute("DELETE FROM color_jobs WHERE id=?", (existing_id,))

    engine = build_engine()
    result = engine.extract_separations(path, default_process_options(force=True))
    seps = list(result.separations or ())
    # Nazwa pliku (plate/SAP) ma pierwszeństwo — bardziej uniwersalne niż OCR treści.
    from pdf_extractor_core.services.filenames import filename_order_hint
    from services.planner.job_link import infer_artwork_number

    file_hint, file_rev = filename_order_hint(path.name)
    order_number = file_hint or result.order_number or result.filename_order_hint or path.stem
    order_name = result.job_name or path.stem
    artwork = infer_artwork_number(
        order_number=str(order_number),
        order_name=str(order_name) if order_name else None,
        filename=path.name,
        document_revision=result.document_revision or file_rev,
    )
    method = result.extraction_method.value if result.extraction_method else ""
    status_val = result.status.value if result.status else "ERROR"
    if status_val == "SUCCESS":
        status = "ok"
    elif status_val == "REVIEW":
        status = "partial"
    else:
        status = "error" if not seps else "partial"

    thumb = _maybe_thumbnail(path, file_hash)

    cur.execute(
        """
        INSERT INTO color_jobs
            (order_number, artwork_number, lub_number, order_name, source_path, source_hash,
             thumbnail_path, color_count, extraction_status, extraction_method, raw_meta, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(order_number),
            artwork,
            None,
            str(order_name),
            str(path),
            file_hash,
            thumb,
            len(seps),
            status,
            method,
            _result_meta_json(result),
            _now_iso(),
        ),
    )
    job_id = cur.lastrowid
    for idx, sep in enumerate(seps):
        station = None
        if sep.separation_number:
            try:
                station = int(str(sep.separation_number).strip())
            except ValueError:
                station = None
        cur.execute(
            """
            INSERT INTO color_separations
                (color_job_id, station_no, color_name, color_code, coverage, is_solid, is_raster, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                station,
                str(sep.color_name),
                None,
                _as_float(sep.coverage),
                0,
                0,
                idx,
            ),
        )
    cur.execute(
        "UPDATE pdf_ingest_files SET file_hash=? WHERE file_path=?",
        (file_hash, str(path.resolve())),
    )
    return int(job_id)


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _result_meta_json(result) -> str:
    try:
        return json.dumps(result.to_dict(), ensure_ascii=False)[:8000]
    except Exception:  # noqa: BLE001
        return "{}"


def _maybe_thumbnail(path: Path, file_hash: str, *, force: bool = False) -> str | None:
    """Miniatura wyciętego wzoru (ew. z kolejnych stron) — bez wpływu na ekstrakcję farb."""

    from services.planner.thumbnails import render_artwork_thumbnail, thumbnail_path_for
    from services.runtime_env import planner_thumbs_dir

    out_dir = planner_thumbs_dir()
    out_path = thumbnail_path_for(file_hash, out_dir)
    return render_artwork_thumbnail(path, out_path, force=force)


def refresh_existing_thumbnails(cur, *, limit: int = 80, force: bool = True) -> dict[str, int]:
    """Przebudowuje miniatury dla istniejących color_jobs (bez ponownej ekstrakcji farb)."""

    cur.execute(
        """
        SELECT id, source_path, source_hash, thumbnail_path
        FROM color_jobs
        WHERE source_path IS NOT NULL AND source_path != ''
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    updated = 0
    skipped = 0
    failed = 0
    for row in cur.fetchall():
        job_id = row["id"] if hasattr(row, "keys") else row[0]
        source = row["source_path"] if hasattr(row, "keys") else row[1]
        file_hash = row["source_hash"] if hasattr(row, "keys") else row[2]
        path = Path(str(source))
        if not path.is_file():
            skipped += 1
            continue
        if not file_hash:
            try:
                from pdf_extractor_core.services.hashing import file_sha256

                file_hash = file_sha256(path)
            except Exception:  # noqa: BLE001
                failed += 1
                continue
        # Przebuduj, gdy brak miniatury, stara wersja w URL albo force.
        from services.planner.thumbnails import THUMB_VERSION

        old_thumb = ""
        try:
            old_thumb = str(row["thumbnail_path"] if hasattr(row, "keys") else row[3] or "")
        except Exception:  # noqa: BLE001
            old_thumb = ""
        needs_rebuild = force or (f"_{THUMB_VERSION}.png" not in old_thumb)
        url = _maybe_thumbnail(path, str(file_hash), force=needs_rebuild)
        if not url:
            failed += 1
            continue
        cur.execute(
            "UPDATE color_jobs SET thumbnail_path=?, updated_at=? WHERE id=?",
            (url, _now_iso(), int(job_id)),
        )
        updated += 1
    _commit(cur)
    return {"updated": updated, "skipped": skipped, "failed": failed}

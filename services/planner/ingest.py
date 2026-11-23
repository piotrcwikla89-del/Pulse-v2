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


def scan_pdf_folder(cur, root: str | None = None, *, process: bool = True, limit: int = 50) -> ScanSummary:
    """Rejestruje PDF z folderu w pdf_ingest_files; opcjonalnie od razu przetwarza kolejkę."""

    summary = ScanSummary()
    folder = normalize_folder_path(root or get_planner_setting(cur, "planner_pdf_root") or "")
    if not folder or not os.path.isdir(folder):
        summary.missing_root = True
        set_planner_setting(cur, "planner_last_scan_at", _now_iso())
        set_planner_setting(cur, "planner_last_scan_summary", summary.as_text())
        return summary

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

    set_planner_setting(cur, "planner_last_scan_at", _now_iso())
    set_planner_setting(cur, "planner_last_scan_summary", summary.as_text())
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
    order_number = result.order_number or result.filename_order_hint or path.stem
    order_name = result.job_name or path.stem
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
            result.document_revision,
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


def _maybe_thumbnail(path: Path, file_hash: str) -> str | None:
    """Zapisuje miniaturę pierwszej strony PDF (jeśli pymupdf dostępny)."""

    try:
        import fitz
    except ImportError:
        return None
    from services.runtime_env import planner_thumbs_dir

    out_dir = planner_thumbs_dir()
    out_path = out_dir / f"{file_hash[:16]}.png"
    if out_path.exists():
        return f"/media/planner_thumbs/{out_path.name}"
    try:
        doc = fitz.open(path)
        if doc.page_count < 1:
            doc.close()
            return None
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(0.25, 0.25), alpha=False)
        pix.save(str(out_path))
        doc.close()
        return f"/media/planner_thumbs/{out_path.name}"
    except Exception:  # noqa: BLE001
        return None

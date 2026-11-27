"""PDF i miniatury w bazie — wspólne dla web + OCR worker (Render nie dzieli dysku)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from db_compat import is_postgres

# Po udanym ensure w procesie — nie rób DDL przy każdym uploadzie (deadlock z workerem).
_BLOB_COLUMNS_READY = False


def _pg_binary(data: bytes):
    if not is_postgres():
        return data
    try:
        from psycopg2 import Binary

        return Binary(data)
    except Exception:  # noqa: BLE001
        return data


def ensure_blob_columns(cur) -> None:
    """Dodaje file_blob / thumbnail_blob jeśli brak (idempotentnie)."""

    global _BLOB_COLUMNS_READY
    if _BLOB_COLUMNS_READY:
        return

    if is_postgres():
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='pdf_ingest_files'
            """
        )
        cols = set()
        for row in cur.fetchall():
            try:
                cols.add(row["column_name"])
            except Exception:  # noqa: BLE001
                cols.add(row[0])
        if "file_blob" not in cols:
            try:
                cur.execute("ALTER TABLE pdf_ingest_files ADD COLUMN IF NOT EXISTS file_blob BYTEA")
            except Exception:  # noqa: BLE001
                try:
                    cur.execute("ALTER TABLE pdf_ingest_files ADD COLUMN file_blob BYTEA")
                except Exception:  # noqa: BLE001
                    pass
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='color_jobs'
            """
        )
        cols = set()
        for row in cur.fetchall():
            try:
                cols.add(row["column_name"])
            except Exception:  # noqa: BLE001
                cols.add(row[0])
        if "thumbnail_blob" not in cols:
            try:
                cur.execute("ALTER TABLE color_jobs ADD COLUMN IF NOT EXISTS thumbnail_blob BYTEA")
            except Exception:  # noqa: BLE001
                try:
                    cur.execute("ALTER TABLE color_jobs ADD COLUMN thumbnail_blob BYTEA")
                except Exception:  # noqa: BLE001
                    pass
        _BLOB_COLUMNS_READY = True
        return

    cur.execute("PRAGMA table_info(pdf_ingest_files)")
    cols = {row[1] for row in cur.fetchall()}
    if "file_blob" not in cols:
        try:
            cur.execute("ALTER TABLE pdf_ingest_files ADD COLUMN file_blob BLOB")
        except Exception:  # noqa: BLE001
            pass
    cur.execute("PRAGMA table_info(color_jobs)")
    cols = {row[1] for row in cur.fetchall()}
    if "thumbnail_blob" not in cols:
        try:
            cur.execute("ALTER TABLE color_jobs ADD COLUMN thumbnail_blob BLOB")
        except Exception:  # noqa: BLE001
            pass
    _BLOB_COLUMNS_READY = True


def _as_bytes(value) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, bytes):
        return value
    # psycopg2 Binary buffer
    try:
        return bytes(value)
    except Exception:  # noqa: BLE001
        return None


def store_ingest_blob(cur, *, file_path: str, content: bytes) -> None:
    ensure_blob_columns(cur)
    cur.execute(
        "UPDATE pdf_ingest_files SET file_blob=?, file_size=? WHERE file_path=?",
        (_pg_binary(content), len(content), file_path),
    )


def store_ingest_blob_by_id(cur, ingest_id: int, content: bytes) -> None:
    ensure_blob_columns(cur)
    cur.execute(
        "UPDATE pdf_ingest_files SET file_blob=?, file_size=? WHERE id=?",
        (_pg_binary(content), len(content), ingest_id),
    )


def materialize_pdf(cur, *, ingest_id: int, file_path: str, file_name: str | None = None) -> Path:
    """Zwraca lokalną ścieżkę PDF: istniejący plik albo blob zapisany do /tmp."""

    path = Path(file_path)
    if path.is_file() and path.stat().st_size > 0:
        return path

    ensure_blob_columns(cur)
    cur.execute("SELECT file_blob, file_name FROM pdf_ingest_files WHERE id=?", (ingest_id,))
    row = cur.fetchone()
    if not row:
        raise FileNotFoundError(f"Brak wpisu ingest id={ingest_id}")
    blob = _as_bytes(row["file_blob"] if hasattr(row, "keys") else row[0])
    name = file_name or (row["file_name"] if hasattr(row, "keys") else row[1]) or f"{ingest_id}.pdf"
    safe = Path(str(name)).name
    if not blob:
        raise FileNotFoundError(
            f"Brak pliku PDF na dysku i w bazie: {file_path}. "
            "Na Render web i worker mają osobne dyski — wgraj PDF ponownie (zapisze się do bazy)."
        )

    out_dir = Path(tempfile.gettempdir()) / "pulse_pdf_blobs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{ingest_id}_{safe}"
    out.write_bytes(blob)
    return out


def store_thumbnail_blob(cur, *, job_id: int, png_path: Path | None = None, png_bytes: bytes | None = None) -> None:
    ensure_blob_columns(cur)
    data = png_bytes
    if data is None and png_path is not None and png_path.is_file():
        data = png_path.read_bytes()
    if not data:
        return
    cur.execute("UPDATE color_jobs SET thumbnail_blob=? WHERE id=?", (_pg_binary(data), job_id))


def load_thumbnail_blob(cur, *, file_hash_prefix: str) -> tuple[bytes, str] | None:
    """Szuka miniatury w DB po prefiksie hash (nazwa pliku thumbs)."""

    ensure_blob_columns(cur)
    like = f"{file_hash_prefix}%"
    cur.execute(
        """
        SELECT thumbnail_blob, source_hash, thumbnail_path
        FROM color_jobs
        WHERE source_hash LIKE ? AND thumbnail_blob IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (like,),
    )
    row = cur.fetchone()
    if not row:
        return None
    blob = _as_bytes(row["thumbnail_blob"] if hasattr(row, "keys") else row[0])
    if not blob:
        return None
    thumb_path = row["thumbnail_path"] if hasattr(row, "keys") else row[2]
    name = ""
    if thumb_path:
        name = str(thumb_path).rstrip("/").split("/")[-1]
    return blob, name


def ingest_has_source(cur, ingest_id: int, file_path: str) -> bool:
    path = Path(file_path)
    if path.is_file() and path.stat().st_size > 0:
        return True
    ensure_blob_columns(cur)
    cur.execute("SELECT file_blob FROM pdf_ingest_files WHERE id=?", (ingest_id,))
    row = cur.fetchone()
    if not row:
        return False
    blob = _as_bytes(row["file_blob"] if hasattr(row, "keys") else row[0])
    return bool(blob)

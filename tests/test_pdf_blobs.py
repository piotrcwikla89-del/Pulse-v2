"""PDF/thumbnail blobs — OCR worker bez wspólnego dysku z webem."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from services.planner.pdf_blobs import (
    ensure_blob_columns,
    ingest_has_source,
    load_thumbnail_blob,
    materialize_pdf,
    store_ingest_blob,
    store_thumbnail_blob,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE pdf_ingest_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            file_size INTEGER,
            status TEXT DEFAULT 'pending',
            file_blob BLOB
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE color_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_hash TEXT,
            thumbnail_path TEXT,
            thumbnail_blob BLOB
        )
        """
    )
    return conn


def test_materialize_from_blob_when_disk_missing(tmp_path: Path):
    conn = _db()
    cur = conn.cursor()
    ensure_blob_columns(cur)
    fake = tmp_path / "missing" / "a.pdf"
    content = b"%PDF-1.4 fake content for test"
    cur.execute(
        "INSERT INTO pdf_ingest_files (file_path, file_name, status) VALUES (?,?,?)",
        (str(fake), "a.pdf", "pending"),
    )
    store_ingest_blob(cur, file_path=str(fake), content=content)
    assert ingest_has_source(cur, 1, str(fake))
    path = materialize_pdf(cur, ingest_id=1, file_path=str(fake), file_name="a.pdf")
    assert path.is_file()
    assert path.read_bytes() == content
    conn.close()


def test_thumbnail_blob_roundtrip():
    conn = _db()
    cur = conn.cursor()
    ensure_blob_columns(cur)
    cur.execute(
        "INSERT INTO color_jobs (source_hash, thumbnail_path) VALUES (?,?)",
        ("abcdef0123456789deadbeef", "/media/planner_thumbs/abcdef0123456789_v6.png"),
    )
    store_thumbnail_blob(cur, job_id=1, png_bytes=b"\x89PNG\r\n\x1a\nfake")
    found = load_thumbnail_blob(cur, file_hash_prefix="abcdef0123456789")
    assert found is not None
    blob, name = found
    assert blob.startswith(b"\x89PNG")
    assert "abcdef" in name
    conn.close()

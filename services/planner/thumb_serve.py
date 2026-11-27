"""Serwowanie i „leczenie” miniaturek planisty (Render / efemeryczny dysk)."""

from __future__ import annotations

import re
from pathlib import Path

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def thumb_candidate_dirs() -> list[Path]:
    """Katalogi, w których mogą leżeć PNG (static vs data/tmp)."""

    from helpers import get_base_path
    from services.runtime_env import get_data_dir, planner_thumbs_dir

    dirs: list[Path] = []
    primary = planner_thumbs_dir()
    dirs.append(primary)
    static_dir = Path(get_base_path()) / "static" / "planner_thumbs"
    data_dir = get_data_dir() / "planner_thumbs"
    for candidate in (static_dir, data_dir):
        if candidate.resolve() not in {d.resolve() for d in dirs}:
            dirs.append(candidate)
    return dirs


def find_thumb_file(filename: str) -> Path | None:
    if not _SAFE_NAME.match(filename or ""):
        return None
    for base in thumb_candidate_dirs():
        path = base / filename
        try:
            if path.is_file() and path.stat().st_size > 500:
                return path
        except OSError:
            continue
    return None


def hash_prefix_from_filename(filename: str) -> str | None:
    stem = Path(filename).stem
    prefix = stem.split("_", 1)[0] if "_" in stem else stem
    prefix = re.sub(r"[^0-9a-fA-F]", "", prefix)
    return prefix[:16] if len(prefix) >= 8 else None


def regenerate_thumb_for_hash(file_hash_prefix: str, *, force: bool = True) -> str | None:
    """Znajduje color_job po prefiksie hash i generuje miniaturę. Zwraca URL albo None."""

    from db_compat import get_db
    from services.planner.thumbnails import render_artwork_thumbnail, thumbnail_path_for
    from services.runtime_env import planner_thumbs_dir

    conn = get_db()
    try:
        cur = conn.cursor()
        like = f"{file_hash_prefix}%"
        cur.execute(
            """
            SELECT id, source_path, source_hash, thumbnail_path
            FROM color_jobs
            WHERE source_hash LIKE ?
            ORDER BY id DESC
            LIMIT 5
            """,
            (like,),
        )
        rows = cur.fetchall()
        out_dir = planner_thumbs_dir()
        for row in rows:
            source = row["source_path"] if hasattr(row, "keys") else row[1]
            file_hash = row["source_hash"] if hasattr(row, "keys") else row[2]
            job_id = row["id"] if hasattr(row, "keys") else row[0]
            path = Path(str(source or ""))
            if not file_hash or not path.is_file():
                continue
            out_path = thumbnail_path_for(str(file_hash), out_dir)
            url = render_artwork_thumbnail(path, out_path, force=force)
            if not url:
                continue
            cur.execute(
                "UPDATE color_jobs SET thumbnail_path=? WHERE id=?",
                (url, int(job_id)),
            )
            try:
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
            return url
        return None
    finally:
        conn.close()


def ensure_job_thumbnail(
    *,
    job_id: int | None = None,
    source_path: str | None = None,
    source_hash: str | None = None,
    thumbnail_path: str | None = None,
) -> str:
    """Zwraca działający URL miniatury albo '' (gdy PDF/pliku nie da się odtworzyć)."""

    from services.planner.thumbnails import (
        THUMB_VERSION,
        render_artwork_thumbnail,
        thumbnail_path_for,
    )
    from services.runtime_env import planner_thumbs_dir

    current = (thumbnail_path or "").strip()
    if current:
        name = current.rstrip("/").split("/")[-1]
        existing = find_thumb_file(name)
        if existing is not None:
            return current if current.startswith("/") else f"/media/planner_thumbs/{name}"

    path = Path(str(source_path or ""))
    file_hash = (source_hash or "").strip()
    if not path.is_file():
        return ""
    if not file_hash:
        try:
            from pdf_extractor_core.services.hashing import file_sha256

            file_hash = file_sha256(path)
        except Exception:  # noqa: BLE001
            return ""

    out_dir = planner_thumbs_dir()
    out_path = thumbnail_path_for(file_hash, out_dir)
    force = (not out_path.is_file()) or (f"_{THUMB_VERSION}.png" not in (current or out_path.name))
    url = render_artwork_thumbnail(path, out_path, force=force)
    return url or ""


def heal_jobs_thumbnails(cur, jobs: list, *, limit: int = 40) -> int:
    """Uzupełnia brakujące PNG / czyści martwe URL w color_jobs. Zwraca liczbę zmian."""

    changed = 0
    for job in jobs:
        if changed >= limit:
            break
        try:
            job_id = int(job["id"] if hasattr(job, "keys") else job[0])
            source = job["source_path"] if hasattr(job, "keys") else None
            file_hash = job["source_hash"] if hasattr(job, "keys") else None
            thumb = job["thumbnail_path"] if hasattr(job, "keys") else None
        except Exception:  # noqa: BLE001
            continue

        old = (str(thumb) if thumb else "").strip()
        url = ensure_job_thumbnail(
            job_id=job_id,
            source_path=str(source) if source else None,
            source_hash=str(file_hash) if file_hash else None,
            thumbnail_path=old or None,
        )
        if url and url != old:
            cur.execute(
                "UPDATE color_jobs SET thumbnail_path=? WHERE id=?",
                (url, job_id),
            )
            changed += 1
        elif not url and old:
            name = old.rstrip("/").split("/")[-1]
            if find_thumb_file(name) is None:
                cur.execute(
                    "UPDATE color_jobs SET thumbnail_path=? WHERE id=?",
                    ("", job_id),
                )
                changed += 1
    return changed

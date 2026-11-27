"""Wiązanie planów / Excel z katalogiem PDF (order_number + artwork)."""

from __future__ import annotations

import re
from typing import Any

_DIGITS = re.compile(r"\d{5,}")
_ARTWORK_TOKEN = re.compile(r"(?:PS|ART|AW)?\d{6,}", re.IGNORECASE)


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


def normalize_key(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", str(value).strip().upper())


def keys_from_text(*parts: str | None) -> list[str]:
    """Kandydaci do wiązania: pełne numery + tokeny artwork z nazwy/ścieżki."""

    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        text = str(part)
        for token in _DIGITS.findall(text):
            key = normalize_key(token)
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        for token in _ARTWORK_TOKEN.findall(text):
            key = normalize_key(token)
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out


def infer_artwork_number(
    *,
    order_number: str | None,
    order_name: str | None,
    filename: str | None = None,
    document_revision: str | None = None,
) -> str | None:
    """Artwork ≠ numer zlecenia: bierze rewizję pliku albo drugi numer z nazwy."""

    if document_revision:
        return str(document_revision).strip() or None
    order_key = normalize_key(order_number)
    candidates: list[str] = []
    for raw in keys_from_text(order_name, filename):
        if raw == order_key:
            continue
        candidates.append(raw)
    if not candidates:
        return None
    # Preferuj dłuższe kody produktowe / PS…
    candidates.sort(key=lambda x: (0 if x.startswith("PS") else 1, -len(x), x))
    return candidates[0]


def find_color_job_id(
    cur,
    *,
    order_number: str | None = None,
    artwork_number: str | None = None,
    extra_keys: list[str] | None = None,
) -> int | None:
    """Szuka color_jobs po order_number / artwork_number / aliasach numerycznych."""

    keys: list[str] = []
    for value in (order_number, artwork_number):
        nk = normalize_key(value)
        if nk:
            keys.append(nk)
    for key in extra_keys or []:
        nk = normalize_key(key)
        if nk:
            keys.append(nk)
    # dedupe zachowując kolejność
    uniq: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key not in seen:
            seen.add(key)
            uniq.append(key)
    if not uniq:
        return None

    for key in uniq:
        cur.execute(
            """
            SELECT id FROM color_jobs
            WHERE UPPER(REPLACE(COALESCE(order_number,''), ' ', '')) = ?
               OR UPPER(REPLACE(COALESCE(artwork_number,''), ' ', '')) = ?
            ORDER BY CASE WHEN COALESCE(color_count,0) > 0 THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (key, key),
        )
        row = cur.fetchone()
        if row:
            return int(_row_get(row, "id", 0))

    # Fallback: numer w źródłowej ścieżce / nazwie
    for key in uniq:
        like = f"%{key}%"
        cur.execute(
            """
            SELECT id FROM color_jobs
            WHERE source_path LIKE ? OR order_name LIKE ?
            ORDER BY CASE WHEN COALESCE(color_count,0) > 0 THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (like, like),
        )
        row = cur.fetchone()
        if row:
            return int(_row_get(row, "id", 0))
    return None


def link_production_plans(cur) -> dict[str, int]:
    """Uzupełnia production_plans.color_job_id oraz artwork_number z katalogu PDF."""

    cur.execute(
        """
        SELECT id, order_number, artwork_number, order_name, color_job_id
        FROM production_plans
        WHERE status IN ('planned', 'in_progress')
        """
    )
    rows = list(cur.fetchall())
    linked = 0
    refreshed = 0
    for row in rows:
        plan_id = int(_row_get(row, "id", 0))
        onum = _row_get(row, "order_number") or ""
        artwork = _row_get(row, "artwork_number") or ""
        name = _row_get(row, "order_name") or ""
        existing = _row_get(row, "color_job_id")
        job_id = find_color_job_id(
            cur,
            order_number=onum,
            artwork_number=artwork,
            extra_keys=keys_from_text(onum, artwork, name),
        )
        if not job_id:
            continue
        cur.execute(
            "SELECT order_number, artwork_number, order_name FROM color_jobs WHERE id=?",
            (job_id,),
        )
        job = cur.fetchone()
        job_art = (_row_get(job, "artwork_number") if job else None) or artwork or None
        job_name = (_row_get(job, "order_name") if job else None) or name or None
        if existing and int(existing) == int(job_id) and artwork:
            continue
        cur.execute(
            """
            UPDATE production_plans
            SET color_job_id=?,
                artwork_number=COALESCE(NULLIF(?, ''), artwork_number),
                order_name=COALESCE(NULLIF(?, ''), order_name)
            WHERE id=?
            """,
            (job_id, job_art, job_name, plan_id),
        )
        if existing and int(existing) == int(job_id):
            refreshed += 1
        else:
            linked += 1
    return {"linked": linked, "refreshed": refreshed, "checked": len(rows)}


def enrich_job_identifiers(cur, job_id: int) -> None:
    """Uzupełnia artwork_number dla istniejącego color_job na podstawie nazwy/ścieżki."""

    cur.execute(
        "SELECT order_number, artwork_number, order_name, source_path FROM color_jobs WHERE id=?",
        (int(job_id),),
    )
    row = cur.fetchone()
    if not row:
        return
    if _row_get(row, "artwork_number"):
        return
    art = infer_artwork_number(
        order_number=_row_get(row, "order_number"),
        order_name=_row_get(row, "order_name"),
        filename=_row_get(row, "source_path"),
    )
    if art:
        cur.execute("UPDATE color_jobs SET artwork_number=? WHERE id=?", (art, int(job_id)))


def backfill_all_artworks(cur) -> int:
    cur.execute("SELECT id FROM color_jobs WHERE artwork_number IS NULL OR artwork_number=''")
    ids = [int(_row_get(r, "id", 0)) for r in cur.fetchall()]
    for jid in ids:
        enrich_job_identifiers(cur, jid)
    return len(ids)

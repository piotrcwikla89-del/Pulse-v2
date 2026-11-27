"""Import wykazu LUB ↔ artwork (rodziny kolorystyczne) i wiązanie z planami."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from services.planner.job_link import normalize_key
from services.planner.settings import get_planner_setting, set_planner_setting

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore


_LUB_TOKEN = re.compile(r"^(?:LUB)?\s*0*(\d{1,6})$", re.IGNORECASE)


@dataclass
class LubImportReport:
    sheet: str = ""
    rows_read: int = 0
    mapped: int = 0
    lub_count: int = 0
    skipped: int = 0
    plans_updated: int = 0
    jobs_updated: int = 0
    drafts_updated: int = 0
    unmatched_plans: int = 0
    errors: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        parts = [
            f"Wykaz LUB ({self.sheet or '—'})",
            f"wiersze={self.rows_read}",
            f"mapowań={self.mapped}",
            f"LUB={self.lub_count}",
            f"plany={self.plans_updated}",
            f"PDF={self.jobs_updated}",
            f"drafty={self.drafts_updated}",
        ]
        if self.unmatched_plans:
            parts.append(f"bez LUB w planach={self.unmatched_plans}")
        if self.skipped:
            parts.append(f"pominięte={self.skipped}")
        return " · ".join(parts)


def ensure_lub_schema(cur, execute=None) -> None:
    """Tworzy tabelę mapowania artwork → LUB (idempotentnie)."""

    from db_compat import is_postgres

    def _ex(sql, params=None):
        if execute is not None:
            if params is None:
                execute(cur, sql)
            else:
                execute(cur, sql, params)
        elif params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)

    pk = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    _ex(
        f"""
        CREATE TABLE IF NOT EXISTS lub_artwork_map (
            id {pk},
            lub_number TEXT NOT NULL,
            artwork_number TEXT NOT NULL,
            artwork_key TEXT NOT NULL UNIQUE,
            source_file TEXT,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ex("CREATE INDEX IF NOT EXISTS idx_lub_artwork_map_lub ON lub_artwork_map(lub_number)")
    _ex("CREATE INDEX IF NOT EXISTS idx_lub_artwork_map_art ON lub_artwork_map(artwork_number)")


def normalize_lub(value: Any) -> str | None:
    """``LUB4129`` / ``4129`` / ``lub 809`` → ``LUB4129``."""

    if value is None:
        return None
    text = str(value).strip().upper().replace(" ", "")
    if not text or text in {"NONE", "NULL", "-", "N/A"}:
        return None
    match = _LUB_TOKEN.fullmatch(text)
    if match:
        return f"LUB{int(match.group(1))}"
    if text.startswith("LUB") and any(ch.isdigit() for ch in text):
        return text
    return None


def normalize_artwork(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if not text:
        return None
    # Excel czasem daje ``1406681.0``
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    digits = re.sub(r"[^\dA-Za-z]", "", text)
    return digits or None


def _norm_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("_", " ")
    return re.sub(r"\s+", " ", text)


def _is_wykaz_headers(headers: list[Any]) -> bool:
    norms = {_norm_header(h) for h in headers if h is not None}
    has_lub = any(h in norms for h in ("przypisany lub", "lub", "lub number", "numer lub"))
    has_art = any(h in norms for h in ("artwork", "artwork number", "numer artwork", "art"))
    return has_lub and has_art


def _header_map(headers: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        key = _norm_header(raw)
        if key and key not in out:
            out[key] = idx
    return out


def _pick_sheet(workbook) -> tuple[Any, str]:
    """Preferuje arkusz WYKAZ / nagłówki LUB+ARTWORK; omija RANKING."""

    # 1) Jawna nazwa
    for name in workbook.sheetnames:
        if "wykaz" in name.lower():
            return workbook[name], name
    # 2) Nagłówki LUB + ARTWORK
    for name in workbook.sheetnames:
        ws = workbook[name]
        headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
        if _is_wykaz_headers(headers):
            return ws, name
    # 3) Fallback: drugi arkusz tylko gdy ma 2 kolumny LUB+coś — ale nie RANKING
    if len(workbook.sheetnames) >= 2:
        name = workbook.sheetnames[1]
        if "ranking" not in name.lower():
            return workbook[name], name
    raise ValueError("Brak arkusza WYKAZ (kolumny Przypisany_LUB + ARTWORK).")


def parse_lub_wykaz_rows(content: bytes) -> tuple[str, list[tuple[str, str]]]:
    """Zwraca (nazwa_arkusza, [(lub, artwork), ...])."""

    if load_workbook is None:
        raise RuntimeError("Brak biblioteki openpyxl — nie można czytać XLSX.")
    workbook = load_workbook(BytesIO(content), data_only=True, read_only=True)
    try:
        ws, sheet_name = _pick_sheet(workbook)
        rows_iter = ws.iter_rows(values_only=True)
        headers = list(next(rows_iter, ()))
        hmap = _header_map(headers)
        lub_idx = next(
            (
                hmap[k]
                for k in ("przypisany lub", "lub", "lub number", "numer lub")
                if k in hmap
            ),
            None,
        )
        art_idx = next(
            (
                hmap[k]
                for k in ("artwork", "artwork number", "numer artwork", "art")
                if k in hmap
            ),
            None,
        )
        if lub_idx is None or art_idx is None:
            # bez nagłówków: kolumna A=LUB, B=ARTWORK
            lub_idx, art_idx = 0, 1
            # jeśli pierwszy wiersz wyglądał na dane — dołącz go
            first = headers
            data_rows = [first] + list(rows_iter)
        else:
            data_rows = list(rows_iter)

        pairs: list[tuple[str, str]] = []
        for row in data_rows:
            if not row:
                continue
            lub_raw = row[lub_idx] if lub_idx < len(row) else None
            art_raw = row[art_idx] if art_idx < len(row) else None
            lub = normalize_lub(lub_raw)
            art = normalize_artwork(art_raw)
            if not lub or not art:
                continue
            pairs.append((lub, art))
        return sheet_name, pairs
    finally:
        workbook.close()


def import_lub_wykaz_bytes(
    cur,
    content: bytes,
    *,
    filename: str | None = None,
    imported_by: str | None = None,
    link: bool = True,
) -> LubImportReport:
    """Pełna wymiana mapy LUB↔artwork + opcjonalne wiązanie planów/PDF."""

    ensure_lub_schema(cur)
    report = LubImportReport()
    try:
        sheet_name, pairs = parse_lub_wykaz_rows(content)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(str(exc))
        return report

    report.sheet = sheet_name
    report.rows_read = len(pairs)

    # Deduplikacja: jeden artwork → jeden LUB (ostatni wygrywa).
    by_art: dict[str, tuple[str, str]] = {}
    for lub, art in pairs:
        key = normalize_key(art)
        if not key:
            report.skipped += 1
            continue
        by_art[key] = (lub, art)

    cur.execute("DELETE FROM lub_artwork_map")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    lubs: set[str] = set()
    for key, (lub, art) in by_art.items():
        cur.execute(
            """
            INSERT INTO lub_artwork_map
                (lub_number, artwork_number, artwork_key, source_file, imported_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (lub, art, key, filename or "", now),
        )
        lubs.add(lub)
    report.mapped = len(by_art)
    report.lub_count = len(lubs)
    report.skipped += max(0, report.rows_read - len(by_art))

    set_planner_setting(cur, "lub_wykaz_last_import_at", now)
    set_planner_setting(
        cur,
        "lub_wykaz_last_summary",
        json.dumps(
            {
                "filename": filename,
                "imported_by": imported_by,
                "sheet": sheet_name,
                "mapped": report.mapped,
                "lub_count": report.lub_count,
                "at": now,
            },
            ensure_ascii=False,
        ),
    )

    if link:
        link_stats = link_artworks_to_lub(cur)
        report.plans_updated = link_stats["plans_updated"]
        report.jobs_updated = link_stats["jobs_updated"]
        report.drafts_updated = link_stats["drafts_updated"]
        report.unmatched_plans = link_stats["unmatched_plans"]

    return report


def lookup_lub(cur, *candidates: str | None) -> str | None:
    """Szuka LUB po numerze artwork / zlecenia."""

    keys: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        key = normalize_key(normalize_artwork(raw) or raw)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    if not keys:
        return None
    for key in keys:
        cur.execute(
            "SELECT lub_number FROM lub_artwork_map WHERE artwork_key=? LIMIT 1",
            (key,),
        )
        row = cur.fetchone()
        if row:
            if hasattr(row, "keys"):
                return row["lub_number"]
            return row[0]
    return None


def link_artworks_to_lub(cur) -> dict[str, int]:
    """Uzupełnia ``lub_number`` w planach, color_jobs i draftach na podstawie mapy."""

    ensure_lub_schema(cur)
    cur.execute("SELECT artwork_key, lub_number FROM lub_artwork_map")
    mapping = {}
    for row in cur.fetchall():
        if hasattr(row, "keys"):
            mapping[str(row["artwork_key"])] = str(row["lub_number"])
        else:
            mapping[str(row[0])] = str(row[1])

    plans_updated = 0
    unmatched = 0
    cur.execute(
        """
        SELECT id, artwork_number, order_number, lub_number
        FROM production_plans
        """
    )
    for row in cur.fetchall():
        if hasattr(row, "keys"):
            plan_id, art, order, current = row["id"], row["artwork_number"], row["order_number"], row["lub_number"]
        else:
            plan_id, art, order, current = row[0], row[1], row[2], row[3]
        lub = None
        for cand in (art, order):
            key = normalize_key(normalize_artwork(cand) or cand)
            if key and key in mapping:
                lub = mapping[key]
                break
        if not lub:
            unmatched += 1
            continue
        if str(current or "").strip().upper() == lub:
            continue
        cur.execute("UPDATE production_plans SET lub_number=? WHERE id=?", (lub, int(plan_id)))
        plans_updated += 1

    jobs_updated = 0
    cur.execute("SELECT id, artwork_number, order_number, lub_number FROM color_jobs")
    for row in cur.fetchall():
        if hasattr(row, "keys"):
            job_id, art, order, current = row["id"], row["artwork_number"], row["order_number"], row["lub_number"]
        else:
            job_id, art, order, current = row[0], row[1], row[2], row[3]
        lub = None
        for cand in (art, order):
            key = normalize_key(normalize_artwork(cand) or cand)
            if key and key in mapping:
                lub = mapping[key]
                break
        if not lub:
            continue
        if str(current or "").strip().upper() == lub:
            continue
        cur.execute("UPDATE color_jobs SET lub_number=? WHERE id=?", (lub, int(job_id)))
        jobs_updated += 1

    drafts_updated = 0
    try:
        cur.execute("SELECT id, artwork_number, order_number, lub_number FROM plan_draft_items")
        draft_rows = list(cur.fetchall())
    except Exception:  # noqa: BLE001
        draft_rows = []
    for row in draft_rows:
        if hasattr(row, "keys"):
            item_id, art, order, current = row["id"], row["artwork_number"], row["order_number"], row["lub_number"]
        else:
            item_id, art, order, current = row[0], row[1], row[2], row[3]
        lub = None
        for cand in (art, order):
            key = normalize_key(normalize_artwork(cand) or cand)
            if key and key in mapping:
                lub = mapping[key]
                break
        if not lub:
            continue
        if str(current or "").strip().upper() == lub:
            continue
        cur.execute("UPDATE plan_draft_items SET lub_number=? WHERE id=?", (lub, int(item_id)))
        drafts_updated += 1

    return {
        "plans_updated": plans_updated,
        "jobs_updated": jobs_updated,
        "drafts_updated": drafts_updated,
        "unmatched_plans": unmatched,
        "map_size": len(mapping),
    }


def lub_import_status(cur) -> dict[str, Any]:
    ensure_lub_schema(cur)
    cur.execute("SELECT COUNT(*) AS n FROM lub_artwork_map")
    row = cur.fetchone()
    mapped = int(row["n"] if hasattr(row, "keys") else row[0] or 0)
    cur.execute("SELECT COUNT(DISTINCT lub_number) AS n FROM lub_artwork_map")
    row = cur.fetchone()
    lubs = int(row["n"] if hasattr(row, "keys") else row[0] or 0)
    summary_raw = get_planner_setting(cur, "lub_wykaz_last_summary") or ""
    summary: dict[str, Any] = {}
    if summary_raw:
        try:
            summary = json.loads(summary_raw)
        except json.JSONDecodeError:
            summary = {"raw": summary_raw}
    return {
        "mapped": mapped,
        "lub_count": lubs,
        "last_import_at": get_planner_setting(cur, "lub_wykaz_last_import_at") or "",
        "last_summary": summary,
    }

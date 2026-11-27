"""Import Excel: katalog kolorów (ORDER_COLORS) oraz plany produkcyjne."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from services.planner.job_link import find_color_job_id, keys_from_text, normalize_key

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore


_ORDER_HEADERS = {
    "order number", "order_number", "ordernumber", "sap", "zlecenie", "nr zlecenia",
    "numer sap", "nr sap", "order",
}
_ARTWORK_HEADERS = {
    "artwork", "artwork number", "artwork_number", "art", "nr artwork", "numer artwork",
    "grafika", "nr grafiki",
}
_NAME_HEADERS = {
    "job name", "order_name", "name", "nazwa", "produkt", "product", "opis",
}
_MACHINE_HEADERS = {
    "machine", "maszyna", "drukarka", "press", "linia", "tor",
}
_COLOR_HEADERS = {"color name", "color_name", "farba", "kolor", "separation"}
_SEP_HEADERS = {"separation number", "separation_number", "station", "stacja"}
_COVERAGE_HEADERS = {"coverage", "pokrycie"}
_SOURCE_HEADERS = {"source pdf", "source_pdf", "pdf", "plik"}
_POS_HEADERS = {"position", "kolejność", "kolejnosc", "seq", "lp", "sort", "order pos"}


@dataclass
class ExcelImportReport:
    mode: str = ""
    sheet: str = ""
    rows_read: int = 0
    jobs_upserted: int = 0
    colors_written: int = 0
    plans_created: int = 0
    plans_linked: int = 0
    plans_unlinked: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    production_summary: str = ""

    def as_text(self) -> str:
        if self.mode == "production_plan" and self.production_summary:
            return self.production_summary
        if self.mode == "colors":
            return (
                f"Excel kolorów ({self.sheet}): wiersze={self.rows_read}, "
                f"prace={self.jobs_upserted}, farby={self.colors_written}, pominięte={self.skipped}"
            )
        if self.mode == "plan":
            return (
                f"Excel planu ({self.sheet}): wiersze={self.rows_read}, "
                f"pozycje={self.plans_created}, powiązane z PDF={self.plans_linked}, "
                f"bez PDF={self.plans_unlinked}, pominięte={self.skipped}"
            )
        return self.errors[0] if self.errors else "Brak danych w Excel"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def _header_map(headers: list[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        h = _norm_header(raw)
        if not h:
            continue
        if h in _ORDER_HEADERS and "order" not in mapping:
            mapping["order"] = idx
        elif h in _ARTWORK_HEADERS and "artwork" not in mapping:
            mapping["artwork"] = idx
        elif h in _NAME_HEADERS and "name" not in mapping:
            mapping["name"] = idx
        elif h in _MACHINE_HEADERS and "machine" not in mapping:
            mapping["machine"] = idx
        elif h in _COLOR_HEADERS and "color" not in mapping:
            mapping["color"] = idx
        elif h in _SEP_HEADERS and "sep" not in mapping:
            mapping["sep"] = idx
        elif h in _COVERAGE_HEADERS and "coverage" not in mapping:
            mapping["coverage"] = idx
        elif h in _SOURCE_HEADERS and "source" not in mapping:
            mapping["source"] = idx
        elif h in _POS_HEADERS and "position" not in mapping:
            mapping["position"] = idx
    return mapping


def _cell(row: tuple[Any, ...], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    value = row[idx]
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _detect_mode(mapping: dict[str, int]) -> str | None:
    if "color" in mapping and "order" in mapping:
        return "colors"
    if "order" in mapping or "artwork" in mapping:
        return "plan"
    return None


def _normalize_machine(raw: str) -> str | None:
    text = (raw or "").strip().upper().replace(" ", "")
    if not text:
        return None
    for m in ("D6", "D8", "D10", "P1", "P2"):
        if m in text or text.endswith(m) or text == m:
            return m
    # np. "Druk 6" / "6"
    if text in {"6", "DRUK6", "MASZYNA6"}:
        return "D6"
    if text in {"8", "DRUK8", "MASZYNA8"}:
        return "D8"
    if text in {"10", "DRUK10", "MASZYNA10"}:
        return "D10"
    return None


def import_excel_bytes(
    cur,
    content: bytes,
    *,
    draft_id: int | None = None,
    filename: str = "",
    imported_by: str = "",
    replace_planned: bool = False,
) -> ExcelImportReport:
    if load_workbook is None:
        report = ExcelImportReport()
        report.errors.append("Brak biblioteki openpyxl — doinstaluj zależność.")
        return report
    if not content:
        report = ExcelImportReport()
        report.errors.append("Pusty plik Excel.")
        return report

    from services.planner.plan_excel import (
        import_production_plan_bytes,
        is_production_plan_headers,
    )

    # Szybka detekcja formatu planu produkcyjnego (ERP)
    wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_iter = ws.iter_rows(values_only=True)
            try:
                headers = list(next(rows_iter) or ())
            except StopIteration:
                continue
            if is_production_plan_headers(headers):
                wb.close()
                prod = import_production_plan_bytes(
                    cur,
                    content,
                    filename=filename,
                    imported_by=imported_by,
                    replace_planned=replace_planned,
                )
                report = ExcelImportReport(mode="production_plan", sheet=prod.sheet)
                report.rows_read = prod.rows_read
                report.plans_created = prod.created + prod.updated
                report.plans_linked = prod.linked_pdf
                report.plans_unlinked = prod.unlinked_pdf
                report.skipped = prod.skipped
                report.errors = list(prod.errors)
                report.production_summary = prod.as_text()
                return report
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass

    wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        candidates: list[tuple[int, int, str, dict[str, int], list[tuple[Any, ...]], str]] = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_iter = ws.iter_rows(values_only=True)
            try:
                headers = next(rows_iter)
            except StopIteration:
                continue
            mapping = _header_map(list(headers or ()))
            mode = _detect_mode(mapping)
            if not mode:
                continue
            data_rows = [
                tuple(r)
                for r in rows_iter
                if r and any(c is not None and str(c).strip() for c in r)
            ]
            # Preferuj arkusz kolorów przy remisie kolumn — bogatsze dane katalogowe
            mode_boost = 10 if mode == "colors" else 0
            candidates.append(
                (len(mapping) + mode_boost, len(data_rows), mode, mapping, data_rows, sheet_name)
            )
        if not candidates:
            report = ExcelImportReport()
            report.errors.append(
                "Nie rozpoznano arkusza. Oczekiwane: plan produkcyjny "
                "(Stanowisko robocze / Zlecenie / Artwork), katalog farb "
                "(Order Number + Color Name) albo prosty plan Order/Artwork/Machine."
            )
            return report
        candidates.sort(reverse=True)
        _cov, _n, mode, mapping, data_rows, sheet_name = candidates[0]
        if mode == "colors":
            return _import_colors(cur, mapping, data_rows, sheet_name)
        return _import_plan(cur, mapping, data_rows, sheet_name, draft_id=draft_id)
    finally:
        wb.close()


def _import_colors(cur, mapping: dict[str, int], rows: list[tuple[Any, ...]], sheet: str) -> ExcelImportReport:
    report = ExcelImportReport(mode="colors", sheet=sheet)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        report.rows_read += 1
        order = _cell(row, mapping.get("order"))
        color = _cell(row, mapping.get("color"))
        if not order or not color:
            report.skipped += 1
            continue
        key = normalize_key(order)
        bucket = grouped.setdefault(
            key,
            {
                "order_number": order,
                "order_name": _cell(row, mapping.get("name")),
                "artwork_number": _cell(row, mapping.get("artwork")) or None,
                "source": _cell(row, mapping.get("source")),
                "colors": [],
            },
        )
        if not bucket["order_name"]:
            bucket["order_name"] = _cell(row, mapping.get("name"))
        if not bucket["artwork_number"]:
            art = _cell(row, mapping.get("artwork"))
            if art:
                bucket["artwork_number"] = art
        sep_raw = _cell(row, mapping.get("sep"))
        cov_raw = _cell(row, mapping.get("coverage"))
        station = None
        if sep_raw:
            try:
                station = int(float(sep_raw))
            except ValueError:
                station = None
        coverage = None
        if cov_raw:
            try:
                coverage = float(str(cov_raw).replace(",", "."))
            except ValueError:
                coverage = None
        bucket["colors"].append({"name": color, "station": station, "coverage": coverage})

    for key, bucket in grouped.items():
        job_id = find_color_job_id(
            cur,
            order_number=bucket["order_number"],
            artwork_number=bucket.get("artwork_number"),
            extra_keys=keys_from_text(bucket.get("source"), bucket.get("order_name")),
        )
        colors = bucket["colors"]
        if job_id:
            cur.execute("DELETE FROM color_separations WHERE color_job_id=?", (job_id,))
            cur.execute(
                """
                UPDATE color_jobs
                SET order_name=COALESCE(NULLIF(?, ''), order_name),
                    artwork_number=COALESCE(NULLIF(?, ''), artwork_number),
                    color_count=?,
                    extraction_status='ok',
                    extraction_method='EXCEL',
                    updated_at=?
                WHERE id=?
                """,
                (
                    bucket.get("order_name") or "",
                    bucket.get("artwork_number") or "",
                    len(colors),
                    _now_iso(),
                    job_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO color_jobs
                    (order_number, artwork_number, order_name, source_path, color_count,
                     extraction_status, extraction_method, updated_at)
                VALUES (?, ?, ?, ?, ?, 'ok', 'EXCEL', ?)
                """,
                (
                    bucket["order_number"],
                    bucket.get("artwork_number"),
                    bucket.get("order_name") or bucket["order_number"],
                    bucket.get("source") or None,
                    len(colors),
                    _now_iso(),
                ),
            )
            job_id = cur.lastrowid
        for idx, sep in enumerate(colors):
            cur.execute(
                """
                INSERT INTO color_separations
                    (color_job_id, station_no, color_name, coverage, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, sep["station"], sep["name"], sep["coverage"], idx),
            )
            report.colors_written += 1
        report.jobs_upserted += 1
    return report


def _import_plan(
    cur,
    mapping: dict[str, int],
    rows: list[tuple[Any, ...]],
    sheet: str,
    *,
    draft_id: int | None,
) -> ExcelImportReport:
    from helpers import PRODUCTION_MACHINES

    report = ExcelImportReport(mode="plan", sheet=sheet)
    if not draft_id:
        report.errors.append("Brak otwartego draftu do zapisu planu z Excel.")
        return report

    # Domyślna maszyna gdy brak kolumny — D8 jako najczęściej używana; użytkownik może poprawić DnD
    default_machine = "D8"
    sort_counters = {m: 0 for m in PRODUCTION_MACHINES}

    cur.execute(
        "SELECT COALESCE(MAX(sort_order), -1) AS m, machine FROM plan_draft_items WHERE draft_id=? GROUP BY machine",
        (int(draft_id),),
    )
    for row in cur.fetchall():
        machine = (row["machine"] if hasattr(row, "keys") else row[1] or "").upper()
        if machine in sort_counters:
            sort_counters[machine] = int((row["m"] if hasattr(row, "keys") else row[0]) or -1) + 1

    for row in rows:
        report.rows_read += 1
        order = _cell(row, mapping.get("order"))
        artwork = _cell(row, mapping.get("artwork"))
        name = _cell(row, mapping.get("name"))
        machine = _normalize_machine(_cell(row, mapping.get("machine"))) or default_machine
        if machine not in PRODUCTION_MACHINES:
            # P1/P2 — na razie pomijamy w drafcie drukarni
            report.skipped += 1
            continue
        if not order and not artwork:
            report.skipped += 1
            continue

        job_id = find_color_job_id(
            cur,
            order_number=order or None,
            artwork_number=artwork or None,
            extra_keys=keys_from_text(order, artwork, name),
        )
        if job_id:
            cur.execute(
                "SELECT order_number, artwork_number, order_name FROM color_jobs WHERE id=?",
                (job_id,),
            )
            job = cur.fetchone()
            if job:
                order = order or (job["order_number"] if hasattr(job, "keys") else job[0]) or order
                artwork = artwork or (job["artwork_number"] if hasattr(job, "keys") else job[1]) or artwork
                name = name or (job["order_name"] if hasattr(job, "keys") else job[2]) or name
            report.plans_linked += 1
        else:
            report.plans_unlinked += 1

        sort_order = sort_counters.get(machine, 0)
        sort_counters[machine] = sort_order + 1
        cur.execute(
            """
            INSERT INTO plan_draft_items
                (draft_id, machine, color_job_id, order_number, artwork_number, order_name, sort_order, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(draft_id),
                machine,
                job_id,
                order or artwork,
                artwork or None,
                name or None,
                sort_order,
                "excel-import",
            ),
        )
        report.plans_created += 1
    return report

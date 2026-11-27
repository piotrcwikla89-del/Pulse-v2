"""Import planów produkcyjnych z Excel (format ERP: Stanowisko / Zlecenie / Artwork)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from io import BytesIO
from typing import Any

from helpers import PRODUCTION_MACHINES, WINDING_MACHINES
from services.planner.job_link import find_color_job_id, keys_from_text

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore


_ALL_MACHINES = tuple(PRODUCTION_MACHINES) + tuple(WINDING_MACHINES)


@dataclass
class ProductionPlanImportReport:
    sheet: str = ""
    rows_read: int = 0
    created: int = 0
    updated: int = 0
    linked_pdf: int = 0
    unlinked_pdf: int = 0
    skipped: int = 0
    comments: int = 0
    by_machine: dict[str, int] = field(default_factory=dict)
    batch_id: int | None = None
    errors: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        parts = [
            f"Plan produkcyjny ({self.sheet})",
            f"wiersze={self.rows_read}",
            f"nowe={self.created}",
            f"aktualizacje={self.updated}",
            f"PDF={self.linked_pdf}",
            f"bez PDF={self.unlinked_pdf}",
            f"pominięte={self.skipped}",
        ]
        if self.by_machine:
            dist = ", ".join(f"{k}:{v}" for k, v in sorted(self.by_machine.items()))
            parts.append(f"maszyny[{dist}]")
        return " · ".join(parts)


def is_production_plan_headers(headers: list[Any]) -> bool:
    norms = {_norm(h) for h in headers if h is not None}
    return (
        "stanowisko robocze" in norms
        and "zlecenie" in norms
        and ("artwork number" in norms or "artwork" in norms)
    )


def _norm(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("_", " ")
    return re.sub(r"\s+", " ", text)


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _cell_num(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _cell_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def map_workstation(raw: str) -> str | None:
    """16-D006 → D6, 16-P001 → P1 itd."""

    text = (raw or "").strip().upper().replace(" ", "")
    if not text:
        return None
    m = re.search(r"D0*(\d{1,2})$", text)
    if m:
        num = int(m.group(1))
        if num == 6:
            return "D6"
        if num == 8:
            return "D8"
        if num == 10:
            return "D10"
    m = re.search(r"P0*(\d{1,2})$", text)
    if m:
        num = int(m.group(1))
        if num == 1:
            return "P1"
        if num == 2:
            return "P2"
    for code in _ALL_MACHINES:
        if text == code or text.endswith(code):
            return code
    return None


def _header_index(headers: list[Any]) -> dict[str, int]:
    """Mapuje znormalizowaną nazwę kolumny → pierwszy indeks (duplikaty: pierwszy wygrywa)."""

    out: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        key = _norm(raw)
        if key and key not in out:
            out[key] = idx
    return out


def _row_dict(headers: list[Any], row: tuple[Any, ...]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for idx, raw_h in enumerate(headers):
        key = str(raw_h).strip() if raw_h is not None else f"col_{idx}"
        # unikaj nadpisania przy zduplikowanym „Opis materiału”
        if key in data and data[key] not in (None, ""):
            continue
        val = row[idx] if idx < len(row) else None
        if isinstance(val, datetime):
            data[key] = val.isoformat(timespec="seconds")
        elif isinstance(val, date):
            data[key] = val.isoformat()
        elif isinstance(val, time):
            data[key] = val.strftime("%H:%M:%S")
        elif isinstance(val, float) and val.is_integer():
            data[key] = int(val)
        else:
            data[key] = val
    return data


def ensure_plan_import_schema(cur, execute=None) -> None:
    """Dodaje tabele/kolumny pod pełny import planu (idempotentnie)."""

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
        CREATE TABLE IF NOT EXISTS plan_import_batches (
            id {pk},
            filename TEXT,
            sheet_name TEXT,
            imported_by TEXT,
            summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cols = [
        ("import_meta", "TEXT"),
        ("material_number", "TEXT"),
        ("material_status", "TEXT"),
        ("parent_number", "TEXT"),
        ("workstation", "TEXT"),
        ("setup_start", "TEXT"),
        ("import_batch_id", "INTEGER"),
        ("priority_text", "TEXT"),
        ("foil", "TEXT"),
        ("web", "TEXT"),
        ("color_count_plan", "INTEGER"),
    ]
    if is_postgres():
        for name, typ in cols:
            _ex(
                f"""
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name='production_plans'
                  AND column_name='{name}'
                """
            )
            if not cur.fetchone():
                _ex(f"ALTER TABLE production_plans ADD COLUMN {name} {typ}")
        return
    _ex("PRAGMA table_info(production_plans)")
    existing = {row[1] for row in cur.fetchall()}
    for name, typ in cols:
        if name not in existing:
            _ex(f"ALTER TABLE production_plans ADD COLUMN {name} {typ}")


def import_production_plan_bytes(
    cur,
    content: bytes,
    *,
    filename: str = "",
    imported_by: str = "",
    replace_planned: bool = False,
) -> ProductionPlanImportReport:
    if load_workbook is None:
        r = ProductionPlanImportReport()
        r.errors.append("Brak openpyxl")
        return r
    wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_iter = ws.iter_rows(values_only=True)
            try:
                headers = list(next(rows_iter) or ())
            except StopIteration:
                continue
            if not is_production_plan_headers(headers):
                continue
            data_rows = [tuple(r) for r in rows_iter]
            return _import_sheet(
                cur,
                headers,
                data_rows,
                sheet_name=sheet_name,
                filename=filename,
                imported_by=imported_by,
                replace_planned=replace_planned,
            )
        r = ProductionPlanImportReport()
        r.errors.append(
            "Nie znaleziono arkusza planu produkcyjnego "
            "(wymagane: Stanowisko robocze, Zlecenie, Artwork Number)."
        )
        return r
    finally:
        wb.close()


def _import_sheet(
    cur,
    headers: list[Any],
    rows: list[tuple[Any, ...]],
    *,
    sheet_name: str,
    filename: str,
    imported_by: str,
    replace_planned: bool,
) -> ProductionPlanImportReport:
    ensure_plan_import_schema(cur)
    report = ProductionPlanImportReport(sheet=sheet_name)
    idx = _header_index(headers)

    cur.execute(
        """
        INSERT INTO plan_import_batches (filename, sheet_name, imported_by, summary)
        VALUES (?, ?, ?, ?)
        """,
        (filename or "", sheet_name, imported_by or "", "in-progress"),
    )
    batch_id = cur.lastrowid
    report.batch_id = int(batch_id)

    if replace_planned:
        # Tylko planned — nie ruszamy in_progress / completed
        cur.execute("DELETE FROM production_plans WHERE status='planned'")

    sort_counters = {m: 0 for m in _ALL_MACHINES}
    cur.execute(
        """
        SELECT machine, COALESCE(MAX(sort_order), -1) AS m
        FROM production_plans
        WHERE status='planned'
        GROUP BY machine
        """
    )
    for row in cur.fetchall():
        machine = (row["machine"] if hasattr(row, "keys") else row[0] or "").upper()
        if machine in sort_counters:
            sort_counters[machine] = int((row["m"] if hasattr(row, "keys") else row[1]) or -1) + 1

    for row in rows:
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue
        report.rows_read += 1
        raw = _row_dict(headers, row)
        def col(*names: str) -> Any:
            for name_key in names:
                if name_key in idx:
                    i = idx[name_key]
                    if i < len(row):
                        return row[i]
            return None

        workstation = _cell_str(col("stanowisko robocze"))
        order_number = _cell_str(col("zlecenie"))
        artwork = _cell_str(col("artwork number", "artwork"))

        # Wiersz-komentarz (brak stanowiska i zlecenia, jest opis)
        if not workstation and not order_number:
            report.comments += 1
            report.skipped += 1
            continue

        machine = map_workstation(workstation)
        if not machine or machine not in _ALL_MACHINES:
            report.skipped += 1
            continue
        if not order_number and not artwork:
            report.skipped += 1
            continue

        material = _cell_str(col("materiał", "material"))
        material_status = _cell_str(col("status materiału"))
        parent = _cell_str(col("parent"))
        lub = _cell_str(col("numer wzorca"))
        name = _cell_str(col("opis materiału")) or _cell_str(col("nazwa 1"))

        setup_date = col("setup start date")
        setup_time = col("setup start time")
        planned_date = None
        setup_start = ""
        if isinstance(setup_date, datetime):
            planned_date = setup_date.date().isoformat()
            setup_start = setup_date.isoformat(timespec="seconds")
        elif isinstance(setup_date, date):
            planned_date = setup_date.isoformat()
            setup_start = planned_date
        if isinstance(setup_time, time):
            setup_start = f"{planned_date or ''}T{setup_time.strftime('%H:%M:%S')}".strip("T")
        elif setup_time:
            setup_start = f"{planned_date or ''} {_cell_str(setup_time)}".strip()

        length = _cell_float(col("lenght", "length"))
        meters = int(round(length)) if length is not None else None
        pieces = _cell_num(col("production order qty"))
        remain = _cell_num(col("remain to produce"))
        if pieces is None:
            pieces = remain
        foil = _cell_str(col("foil"))
        web = _cell_str(col("web"))
        priority = _cell_str(col("priority text"))
        color_count_plan = _cell_num(col("liczba kolorow"))
        laminate = web or foil or None

        job_id = find_color_job_id(
            cur,
            order_number=artwork or None,
            artwork_number=artwork or None,
            extra_keys=keys_from_text(artwork, material, order_number, name),
        )
        if job_id:
            report.linked_pdf += 1
        else:
            report.unlinked_pdf += 1

        meta_json = json.dumps(raw, ensure_ascii=False, default=str)

        # Upsert po machine+order_number dla planned
        cur.execute(
            """
            SELECT id, status FROM production_plans
            WHERE machine=? AND order_number=? AND status IN ('planned', 'in_progress')
            ORDER BY CASE status WHEN 'in_progress' THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (machine, order_number or artwork),
        )
        existing = cur.fetchone()
        if existing:
            eid = int(existing["id"] if hasattr(existing, "keys") else existing[0])
            estatus = existing["status"] if hasattr(existing, "keys") else existing[1]
            if estatus == "in_progress":
                # Nie nadpisuj kolejności aktywnego — tylko uzupełnij meta/artwork jeśli puste
                cur.execute(
                    """
                    UPDATE production_plans SET
                        artwork_number=COALESCE(NULLIF(?, ''), artwork_number),
                        lub_number=COALESCE(NULLIF(?, ''), lub_number),
                        order_name=COALESCE(NULLIF(?, ''), order_name),
                        material_number=COALESCE(NULLIF(?, ''), material_number),
                        import_meta=?,
                        import_batch_id=?,
                        color_job_id=COALESCE(?, color_job_id)
                    WHERE id=?
                    """,
                    (
                        artwork,
                        lub,
                        name,
                        material,
                        meta_json,
                        batch_id,
                        job_id,
                        eid,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE production_plans SET
                        artwork_number=?,
                        lub_number=?,
                        order_name=?,
                        laminate=?,
                        meters=?,
                        pieces=?,
                        planned_date=COALESCE(?, planned_date),
                        color_job_id=COALESCE(?, color_job_id),
                        import_meta=?,
                        material_number=?,
                        material_status=?,
                        parent_number=?,
                        workstation=?,
                        setup_start=?,
                        import_batch_id=?,
                        priority_text=?,
                        foil=?,
                        web=?,
                        color_count_plan=?
                    WHERE id=?
                    """,
                    (
                        artwork or None,
                        lub or None,
                        name or None,
                        laminate,
                        meters,
                        pieces,
                        planned_date,
                        job_id,
                        meta_json,
                        material or None,
                        material_status or None,
                        parent or None,
                        workstation or None,
                        setup_start or None,
                        batch_id,
                        priority or None,
                        foil or None,
                        web or None,
                        color_count_plan,
                        eid,
                    ),
                )
            report.updated += 1
        else:
            sort_order = sort_counters.get(machine, 0)
            sort_counters[machine] = sort_order + 1
            cur.execute(
                """
                INSERT INTO production_plans (
                    machine, order_number, artwork_number, lub_number, order_name,
                    laminate, meters, pieces, planned_date, status,
                    assortment_prep_status, farby_prep_status, polimery_prep_status,
                    color_job_id, sort_order,
                    import_meta, material_number, material_status, parent_number,
                    workstation, setup_start, import_batch_id, priority_text, foil, web,
                    color_count_plan
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, 'planned',
                    'pending', 'pending', 'pending',
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?
                )
                """,
                (
                    machine,
                    order_number or artwork,
                    artwork or None,
                    lub or None,
                    name or None,
                    laminate,
                    meters,
                    pieces,
                    planned_date,
                    job_id,
                    sort_order,
                    meta_json,
                    material or None,
                    material_status or None,
                    parent or None,
                    workstation or None,
                    setup_start or None,
                    batch_id,
                    priority or None,
                    foil or None,
                    web or None,
                    color_count_plan,
                ),
            )
            report.created += 1

        report.by_machine[machine] = report.by_machine.get(machine, 0) + 1

    summary = report.as_text()
    cur.execute(
        "UPDATE plan_import_batches SET summary=? WHERE id=?",
        (summary, batch_id),
    )
    return report

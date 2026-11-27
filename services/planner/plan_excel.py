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
    column_count: int = 0
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
        if self.column_count:
            parts.append(f"kolumny={self.column_count}")
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


def _unique_header_labels(headers: list[Any]) -> list[str]:
    """Unikalne etykiety — duplikaty ``Opis materiału`` → ``Opis materiału (2)``."""

    seen: dict[str, int] = {}
    labels: list[str] = []
    for idx, raw in enumerate(headers):
        base = str(raw).strip() if raw is not None else f"Kolumna {idx + 1}"
        if not base:
            base = f"Kolumna {idx + 1}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        labels.append(base if count == 1 else f"{base} ({count})")
    return labels


def _header_index(headers: list[Any]) -> dict[str, int]:
    """Mapuje znormalizowaną nazwę kolumny → indeks (z obsługą duplikatów)."""

    labels = _unique_header_labels(headers)
    out: dict[str, int] = {}
    for idx, label in enumerate(labels):
        key = _norm(label)
        if key and key not in out:
            out[key] = idx
        base = _norm(re.sub(r"\s*\(\d+\)$", "", label))
        if base and base not in out:
            out[base] = idx
    return out


def _serialize_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        return value.strip()
    return value


def _row_dict(headers: list[Any], row: tuple[Any, ...]) -> dict[str, Any]:
    labels = _unique_header_labels(headers)
    data: dict[str, Any] = {}
    for idx, label in enumerate(labels):
        val = row[idx] if idx < len(row) else None
        data[label] = _serialize_cell(val)
    return data


def build_excel_fields(
    headers: list[Any],
    row: tuple[Any, ...],
    *,
    skip_first: bool = True,
) -> list[dict[str, Any]]:
    """Wszystkie kolumny Excela (domyślnie bez pierwszej = stanowisko)."""

    labels = _unique_header_labels(headers)
    start = 1 if skip_first and labels else 0
    fields: list[dict[str, Any]] = []
    for idx in range(start, len(labels)):
        label = labels[idx]
        val = row[idx] if idx < len(row) else None
        fields.append(
            {
                "key": _norm(label) or f"col_{idx}",
                "label": label,
                "value": _serialize_cell(val),
            }
        )
    return fields


def parse_plan_fields(import_meta: str | None) -> list[dict[str, Any]]:
    """Odczytuje uporządkowane pola z ``import_meta`` (nowy i stary format)."""

    if not import_meta:
        return []
    try:
        data = json.loads(import_meta)
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("fields"), list):
        out = []
        for item in data["fields"]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("key") or "").strip()
            if not label or _norm(label) == "stanowisko robocze":
                continue
            out.append(
                {
                    "key": str(item.get("key") or _norm(label)),
                    "label": label,
                    "value": item.get("value"),
                }
            )
        return out
    if isinstance(data, dict):
        out = []
        for label, value in data.items():
            if _norm(str(label)) == "stanowisko robocze":
                continue
            out.append({"key": _norm(label), "label": str(label), "value": value})
        return out
    return []


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
        ("web_stock", "TEXT"),
        ("print_360", "TEXT"),
        ("remain_qty", "INTEGER"),
        ("slit_width", "TEXT"),
        ("impressions", "TEXT"),
        ("raw_web_used", "TEXT"),
        ("diametre_web", "TEXT"),
        ("wbpr_qty", "TEXT"),
        ("typ", "TEXT"),
        ("earliest_mad", "TEXT"),
        ("customer_name", "TEXT"),
        ("material_desc_alt", "TEXT"),
        ("sales_dept", "TEXT"),
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


def _extra_field_values(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Wyciąga dodatkowe kolumny typed z listy pól Excela."""

    by_key = {f["key"]: f.get("value") for f in fields}

    def g(*keys: str) -> Any:
        for key in keys:
            if key in by_key and by_key[key] not in (None, ""):
                return by_key[key]
        return None

    return {
        "web_stock": _cell_str(g("web stock")),
        "print_360": _cell_str(g("print 360")),
        "remain_qty": _cell_num(g("remain to produce")),
        "slit_width": _cell_str(g("slit width")),
        "impressions": _cell_str(g("no of impressions")),
        "raw_web_used": _cell_str(g("raw web used")),
        "diametre_web": _cell_str(g("diametre web")),
        "wbpr_qty": _cell_str(g("wbpr order qty un")),
        "typ": _cell_str(g("typ")),
        "earliest_mad": _cell_str(g("earliest mad")),
        "customer_name": _cell_str(g("nazwa 1")),
        "material_desc_alt": _cell_str(g("opis materiału (2)")),
        "sales_dept": _cell_str(g("dział sprz.")),
    }


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
    display_labels = [f["label"] for f in build_excel_fields(headers, tuple(), skip_first=True)]
    report.column_count = len(display_labels)

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
        fields = build_excel_fields(headers, row, skip_first=True)

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
        if not lub:
            try:
                from services.planner.lub_import import lookup_lub

                lub = lookup_lub(cur, artwork, order_number) or ""
            except Exception:  # noqa: BLE001
                lub = ""
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
        extras = _extra_field_values(fields)

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

        meta_json = json.dumps(
            {
                "fields": fields,
                "raw": raw,
                "column_labels": display_labels,
            },
            ensure_ascii=False,
            default=str,
        )

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
                    (artwork, lub, name, material, meta_json, batch_id, job_id, eid),
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
                        web_stock=?,
                        print_360=?,
                        remain_qty=?,
                        slit_width=?,
                        impressions=?,
                        raw_web_used=?,
                        diametre_web=?,
                        wbpr_qty=?,
                        typ=?,
                        earliest_mad=?,
                        customer_name=?,
                        material_desc_alt=?,
                        sales_dept=?,
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
                        extras["web_stock"] or None,
                        extras["print_360"] or None,
                        extras["remain_qty"],
                        extras["slit_width"] or None,
                        extras["impressions"] or None,
                        extras["raw_web_used"] or None,
                        extras["diametre_web"] or None,
                        extras["wbpr_qty"] or None,
                        extras["typ"] or None,
                        extras["earliest_mad"] or None,
                        extras["customer_name"] or None,
                        extras["material_desc_alt"] or None,
                        extras["sales_dept"] or None,
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
                    web_stock, print_360, remain_qty, slit_width, impressions,
                    raw_web_used, diametre_web, wbpr_qty, typ, earliest_mad,
                    customer_name, material_desc_alt, sales_dept, color_count_plan
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, 'planned',
                    'pending', 'pending', 'pending',
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
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
                    extras["web_stock"] or None,
                    extras["print_360"] or None,
                    extras["remain_qty"],
                    extras["slit_width"] or None,
                    extras["impressions"] or None,
                    extras["raw_web_used"] or None,
                    extras["diametre_web"] or None,
                    extras["wbpr_qty"] or None,
                    extras["typ"] or None,
                    extras["earliest_mad"] or None,
                    extras["customer_name"] or None,
                    extras["material_desc_alt"] or None,
                    extras["sales_dept"] or None,
                    color_count_plan,
                ),
            )
            report.created += 1

        report.by_machine[machine] = report.by_machine.get(machine, 0) + 1

    try:
        from services.planner.lub_import import link_artworks_to_lub
        from services.planner.settings import set_planner_setting

        link_artworks_to_lub(cur)
        set_planner_setting(
            cur,
            "plan_excel_columns",
            json.dumps(display_labels, ensure_ascii=False),
        )
    except Exception:  # noqa: BLE001
        pass

    summary = report.as_text()
    cur.execute(
        "UPDATE plan_import_batches SET summary=? WHERE id=?",
        (summary, batch_id),
    )
    return report

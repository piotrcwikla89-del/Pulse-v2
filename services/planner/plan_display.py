"""Kolumny widoków planu dla ról produkcyjnych (drukarz, kierownik, magazyny, przewijarki)."""

from __future__ import annotations

from typing import Any

from services.planner.plan_excel import parse_plan_fields

# Litery kolumn z arkusza „Druk - 16L” (A=Stanowisko …) — widok operatora.
# T = metry czystego laminatu, W = ilość sztuk (źródło: Raw Web Used / WBPR order qty UN).
OPERATOR_PLAN_COLUMNS: tuple[dict[str, str], ...] = (
    {"letter": "D", "label": "Zlecenie", "field": "order_number", "excel": "Zlecenie"},
    {"letter": "G", "label": "Artwork", "field": "artwork_number", "excel": "Artwork Number"},
    {"letter": "J", "label": "Opis materiału", "field": "order_name", "excel": "Opis materiału"},
    {"letter": "M", "label": "Web", "field": "web", "excel": "Web"},
    {"letter": "T", "label": "Metry laminatu", "field": "raw_web_used", "excel": "Raw Web Used"},
    {"letter": "W", "label": "Ilość sztuk", "field": "wbpr_qty", "excel": "WBPR order qty UN"},
)


def _row_get(row: Any, key: str, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, IndexError):
            return default
    return default


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


def excel_map_from_plan(plan: Any) -> dict[str, Any]:
    fields = parse_plan_fields(_row_get(plan, "import_meta"))
    return {str(f.get("label")): f.get("value") for f in fields}


def operator_cells(plan: Any) -> list[dict[str, str]]:
    """Wartości kolumn D/G/J/M/T/W dla wiersza planu."""

    excel = excel_map_from_plan(plan)
    cells: list[dict[str, str]] = []
    for col in OPERATOR_PLAN_COLUMNS:
        value = excel.get(col["excel"])
        if value in (None, ""):
            value = _row_get(plan, col["field"])
        cells.append(
            {
                "letter": col["letter"],
                "label": col["label"],
                "value": _fmt(value),
                "raw": "" if value in (None, "") else str(value),
            }
        )
    return cells


def attach_operator_view(plans: list[dict]) -> list[dict]:
    """Dokleja ``op_cells`` i ``excel_map`` do słowników planów."""

    out: list[dict] = []
    for plan in plans:
        d = dict(plan)
        d["excel_map"] = excel_map_from_plan(d)
        d["excel_fields"] = parse_plan_fields(d.get("import_meta"))
        d["op_cells"] = operator_cells(d)
        out.append(d)
    return out


def attach_full_excel_view(plans: list[dict]) -> tuple[list[dict], list[str]]:
    """Pełny zestaw kolumn Excela (bez stanowiska) — widok przewijarek."""

    out: list[dict] = []
    column_labels: list[str] = []
    for plan in plans:
        d = dict(plan)
        fields = parse_plan_fields(d.get("import_meta"))
        if not fields:
            # Fallback gdy plan bez import_meta (stary import / CSV).
            fields = [
                {"key": "zlecenie", "label": "Zlecenie", "value": d.get("order_number")},
                {"key": "artwork number", "label": "Artwork Number", "value": d.get("artwork_number")},
                {"key": "numer wzorca", "label": "Numer Wzorca", "value": d.get("lub_number")},
                {"key": "opis materiału", "label": "Opis materiału", "value": d.get("order_name")},
                {"key": "web", "label": "Web", "value": d.get("web") or d.get("laminate")},
                {"key": "production order qty", "label": "Production order qty", "value": d.get("pieces")},
                {"key": "lenght", "label": "Lenght", "value": d.get("meters")},
            ]
        d["excel_fields"] = fields
        d["excel_map"] = {str(f.get("label")): f.get("value") for f in fields}
        if not column_labels and fields:
            column_labels = [str(f.get("label")) for f in fields if f.get("label")]
        out.append(d)
    return out, column_labels


def operator_column_headers() -> list[dict[str, str]]:
    return [{"letter": c["letter"], "label": c["label"]} for c in OPERATOR_PLAN_COLUMNS]

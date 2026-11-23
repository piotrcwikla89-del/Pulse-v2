"""Color Matching — informacyjne porównanie zestawów kolorów (bez rankingu).

Procent pokrycia = |wspólne| / |unikalne kolory wybranej pracy| × 100.
Kolejność kolejek maszyn nigdy nie jest zmieniana.
"""

from __future__ import annotations

from typing import Any, Sequence

from helpers import PRODUCTION_MACHINES
from pdf_extractor_core.similarity.normalize import normalize_color_name

# Progi oznaczeń kolorystycznych w UI (tylko prezentacja, nie ranking).
MATCH_THRESHOLD_HIGH = 70.0
MATCH_THRESHOLD_MID = 40.0


def unique_color_map(names: Sequence[str]) -> dict[str, str]:
    """Znormalizowany klucz → oryginalna nazwa (pierwsze wystąpienie)."""

    out: dict[str, str] = {}
    for raw in names:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        key = normalize_color_name(text)
        if not key:
            continue
        if key not in out:
            out[key] = text
    return out


def compare_color_sets(
    selected_names: Sequence[str],
    other_names: Sequence[str],
) -> dict[str, Any]:
    """Porównanie zbiorów unikalnych nazw — bez Jaccarda / IDF / wag."""

    selected = unique_color_map(selected_names)
    other = unique_color_map(other_names)
    sel_keys = set(selected)
    oth_keys = set(other)
    shared_keys = sel_keys & oth_keys
    only_selected_keys = sel_keys - oth_keys
    only_other_keys = oth_keys - sel_keys

    selected_count = len(sel_keys)
    shared_count = len(shared_keys)
    coverage = (
        round(shared_count / selected_count * 100.0, 1) if selected_count else 0.0
    )

    def _names(keys: set[str], primary: dict[str, str], fallback: dict[str, str]) -> list[str]:
        return sorted(
            (primary.get(k) or fallback.get(k) or k) for k in keys
        )

    return {
        "shared": _names(shared_keys, selected, other),
        "only_selected": _names(only_selected_keys, selected, selected),
        "only_other": _names(only_other_keys, other, other),
        "shared_count": shared_count,
        "selected_count": selected_count,
        "other_count": len(oth_keys),
        "coverage_percent": coverage,
        "label": f"{shared_count}/{selected_count} — {int(round(coverage))}%",
        "band": coverage_band(coverage),
    }


def coverage_band(percent: float) -> str:
    if percent >= MATCH_THRESHOLD_HIGH:
        return "high"
    if percent >= MATCH_THRESHOLD_MID:
        return "mid"
    return "low"


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


def _load_job_colors(cur, job_id: int) -> list[str]:
    cur.execute(
        "SELECT color_name FROM color_separations WHERE color_job_id=? ORDER BY sort_order, id",
        (job_id,),
    )
    names: list[str] = []
    for row in cur.fetchall():
        name = _row_get(row, "color_name", 0)
        if name:
            names.append(str(name))
    return names


def _resolve_plan_colors(cur, plan_row, cache: dict[int, list[str]]) -> tuple[int | None, list[str]]:
    """Kolory pracy z kolejki: color_job_id albo dopasowanie po order_number."""

    color_job_id = _row_get(plan_row, "color_job_id")
    if color_job_id is not None:
        jid = int(color_job_id)
        if jid not in cache:
            cache[jid] = _load_job_colors(cur, jid)
        return jid, cache[jid]

    order_number = (_row_get(plan_row, "order_number") or "").strip()
    if not order_number:
        return None, []
    cur.execute(
        "SELECT id FROM color_jobs WHERE order_number=? ORDER BY id DESC LIMIT 1",
        (order_number,),
    )
    row = cur.fetchone()
    if not row:
        return None, []
    jid = int(_row_get(row, "id", 0))
    if jid not in cache:
        cache[jid] = _load_job_colors(cur, jid)
    return jid, cache[jid]


def load_machine_queues_ordered(cur) -> dict[str, list]:
    """Kolejki D6/D8/D10 w aktualnym porządku planu (bez sortowania po kolorach)."""

    queues: dict[str, list] = {m: [] for m in PRODUCTION_MACHINES}
    cur.execute(
        """
        SELECT id, machine, order_number, artwork_number, lub_number, order_name,
               status, color_job_id, sort_order,
               farby_prep_status, polimery_prep_status, assortment_prep_status
        FROM production_plans
        WHERE status IN ('planned', 'in_progress')
        ORDER BY CASE WHEN status='in_progress' THEN 0 ELSE 1 END, sort_order, id
        """
    )
    for row in cur.fetchall():
        machine = (_row_get(row, "machine", 1) or "").upper()
        if machine in queues:
            queues[machine].append(row)
    return queues


def load_draft_queues_ordered(cur, draft_id: int) -> dict[str, list]:
    """Pozycje draftu w kolejności sort_order (bez zmian)."""

    items: dict[str, list] = {m: [] for m in PRODUCTION_MACHINES}
    cur.execute(
        """
        SELECT id, machine, order_number, artwork_number, lub_number, order_name, color_job_id, sort_order
        FROM plan_draft_items
        WHERE draft_id=?
        ORDER BY sort_order, id
        """,
        (int(draft_id),),
    )
    for row in cur.fetchall():
        machine = (_row_get(row, "machine") or "").upper()
        if machine in items:
            items[machine].append(row)
    return items


def _match_entry(
    *,
    row_id: int,
    position: int,
    machine: str,
    order_number: str,
    order_name: str,
    status: str,
    linked_job_id: int | None,
    other_colors: list[str],
    selected_job_id: int,
    selected_colors: list[str],
    id_field: str,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        id_field: row_id,
        "position": position,
        "machine": machine,
        "order_number": order_number,
        "order_name": order_name,
        "status": status,
        "color_job_id": linked_job_id,
        "match": None,
    }
    if linked_job_id == int(selected_job_id):
        entry["match"] = compare_color_sets(selected_colors, selected_colors)
        entry["match"]["same_job"] = True
    elif other_colors:
        entry["match"] = compare_color_sets(selected_colors, other_colors)
        entry["match"]["same_job"] = False
    else:
        entry["match"] = None
    return entry


def match_selected_against_queues(
    cur,
    selected_job_id: int,
    *,
    queues: dict[str, list] | None = None,
    draft_id: int | None = None,
    drafts: dict[str, list] | None = None,
) -> dict[str, Any]:
    """Porównuje wybraną pracę z każdą pozycją kolejek live + draft — kolejność bez zmian."""

    cur.execute("SELECT * FROM color_jobs WHERE id=?", (int(selected_job_id),))
    selected_job = cur.fetchone()
    if not selected_job:
        return {"ok": False, "error": "Brak joba"}

    selected_colors = _load_job_colors(cur, int(selected_job_id))
    selected_unique = unique_color_map(selected_colors)
    queue_map = queues if queues is not None else load_machine_queues_ordered(cur)
    color_cache: dict[int, list[str]] = {int(selected_job_id): selected_colors}

    machines_out: dict[str, list[dict[str, Any]]] = {}
    for machine in PRODUCTION_MACHINES:
        rows = queue_map.get(machine) or []
        items: list[dict[str, Any]] = []
        for position, plan in enumerate(rows):
            linked_job_id, other_colors = _resolve_plan_colors(cur, plan, color_cache)
            items.append(
                _match_entry(
                    row_id=int(_row_get(plan, "id", 0)),
                    position=position,
                    machine=machine,
                    order_number=_row_get(plan, "order_number") or "",
                    order_name=_row_get(plan, "order_name") or "",
                    status=_row_get(plan, "status") or "",
                    linked_job_id=linked_job_id,
                    other_colors=other_colors,
                    selected_job_id=int(selected_job_id),
                    selected_colors=selected_colors,
                    id_field="plan_id",
                )
            )
        machines_out[machine] = items

    drafts_out: dict[str, list[dict[str, Any]]] = {m: [] for m in PRODUCTION_MACHINES}
    draft_map = drafts
    if draft_map is None and draft_id is not None:
        draft_map = load_draft_queues_ordered(cur, int(draft_id))
    if draft_map:
        for machine in PRODUCTION_MACHINES:
            rows = draft_map.get(machine) or []
            items = []
            for position, draft_row in enumerate(rows):
                linked_job_id, other_colors = _resolve_plan_colors(cur, draft_row, color_cache)
                items.append(
                    _match_entry(
                        row_id=int(_row_get(draft_row, "id", 0)),
                        position=position,
                        machine=machine,
                        order_number=_row_get(draft_row, "order_number") or "",
                        order_name=_row_get(draft_row, "order_name") or "",
                        status="draft",
                        linked_job_id=linked_job_id,
                        other_colors=other_colors,
                        selected_job_id=int(selected_job_id),
                        selected_colors=selected_colors,
                        id_field="item_id",
                    )
                )
            drafts_out[machine] = items

    return {
        "ok": True,
        "selected": {
            "id": int(selected_job_id),
            "order_number": _row_get(selected_job, "order_number") or "",
            "order_name": _row_get(selected_job, "order_name") or "",
            "thumbnail_path": _row_get(selected_job, "thumbnail_path") or "",
            "colors": list(selected_unique.values()),
            "color_count": len(selected_unique),
        },
        "machines": machines_out,
        "drafts": drafts_out,
        "thresholds": {
            "high": MATCH_THRESHOLD_HIGH,
            "mid": MATCH_THRESHOLD_MID,
        },
        "order_preserved": True,
    }


def assert_queue_order_unchanged(
    before: Sequence[Any],
    after_items: Sequence[dict[str, Any]],
    *,
    id_key: str = "id",
) -> bool:
    """Pomocnik testowy: te same id w tej samej kolejności."""

    before_ids = [int(_row_get(r, id_key, 0)) for r in before]
    after_ids = [int(item["plan_id"]) for item in after_items]
    return before_ids == after_ids

"""Przezbrojenia kolorystyczne między sąsiadującymi pracami w kolejce.

Porównanie wyłącznie po znormalizowanych nazwach farb (bez Lab/Delta E/czasu).
"""

from __future__ import annotations

from typing import Any, Sequence

from services.planner.color_match import compare_color_sets, unique_color_map


def changeover_between(
    prev_colors: Sequence[str],
    next_colors: Sequence[str],
) -> dict[str, Any]:
    """Porównanie FARBY A → FARBY B dla sąsiadów w kolejce.

    - wspólne = farby pozostające
    - only_next = do dodania / zmiany na B
    - only_prev = schodzące z A
    """

    if not prev_colors and not next_colors:
        return {
            "ok": False,
            "message": "Brak danych kolorystycznych dla obu prac",
            "shared_count": 0,
            "remove_count": 0,
            "add_count": 0,
            "shared": [],
            "only_prev": [],
            "only_next": [],
            "label": "brak danych",
        }

    if not prev_colors or not next_colors:
        return {
            "ok": False,
            "message": "Brak danych kolorystycznych jednej z prac",
            "shared_count": 0,
            "remove_count": len(unique_color_map(prev_colors)),
            "add_count": len(unique_color_map(next_colors)),
            "shared": [],
            "only_prev": list(unique_color_map(prev_colors).values()),
            "only_next": list(unique_color_map(next_colors).values()),
            "label": "brak danych",
        }

    cmp = compare_color_sets(prev_colors, next_colors)
    # compare_color_sets: only_selected = tylko A, only_other = tylko B
    stay = cmp["shared_count"]
    leave = len(cmp["only_selected"])
    add = len(cmp["only_other"])
    return {
        "ok": True,
        "message": "",
        "shared_count": stay,
        "remove_count": leave,
        "add_count": add,
        "shared": cmp["shared"],
        "only_prev": cmp["only_selected"],
        "only_next": cmp["only_other"],
        "label": f"{stay} pozostaje | {add} do zmiany",
    }


def changeovers_for_queue(
    items: Sequence[dict[str, Any]],
    *,
    colors_key: str = "colors",
) -> list[dict[str, Any]]:
    """Lista przezbrojeń między kolejnymi pozycjami (len = n-1)."""

    out: list[dict[str, Any]] = []
    for i in range(len(items) - 1):
        a = items[i]
        b = items[i + 1]
        info = changeover_between(a.get(colors_key) or [], b.get(colors_key) or [])
        info["from_index"] = i
        info["to_index"] = i + 1
        info["from_id"] = a.get("id") or a.get("plan_id") or a.get("item_id")
        info["to_id"] = b.get("id") or b.get("plan_id") or b.get("item_id")
        out.append(info)
    return out


def placement_suggestions(
    match_machines: dict[str, list[dict[str, Any]]],
    *,
    min_coverage: float = 40.0,
) -> list[dict[str, Any]]:
    """Sugestia umiejscowienia na podstawie istniejących wyników color-match.

    Nie zmienia kolejności — tylko informacja: najlepsza pozycja per maszyna
    (najwyższe pokrycie względem pracy już w kolejce).
    """

    hints: list[dict[str, Any]] = []
    for machine, entries in (match_machines or {}).items():
        best = None
        for entry in entries:
            match = entry.get("match")
            if not match or match.get("same_job"):
                continue
            cov = float(match.get("coverage_percent") or 0)
            if cov < min_coverage:
                continue
            pos = int(entry.get("position") or 0) + 1
            cand = {
                "machine": machine,
                "position": pos,
                "plan_id": entry.get("plan_id"),
                "order_number": entry.get("order_number") or "",
                "coverage_percent": cov,
                "label": match.get("label") or f"{cov}%",
                "message": (
                    f"{machine} — podobna praca na pozycji {pos} kolejki. "
                    f"Podobieństwo: {int(round(cov))}%."
                ),
            }
            if best is None or cov > best["coverage_percent"]:
                best = cand
        if best:
            hints.append(best)

    hints.sort(key=lambda h: -h["coverage_percent"])
    return hints

"""Color Matching — mostek do porównań informacyjnych (bez rankingu).

Zachowane API `suggest_similar` jest świadomie puste pod względem rankingu:
używaj `match_selected_against_queues` z `color_match`.
"""

from __future__ import annotations

from services.planner.color_match import (
    compare_color_sets,
    match_selected_against_queues,
)


def suggest_similar(
    cur,
    color_job_id: int,
    *,
    limit: int = 15,
    min_score: float = 0.0,
) -> list[dict]:
    """DEPRECATED: nie sortuje ani nie rekomenduje.

    Zwraca pustą listę — ranking podobieństw został usunięty.
    Wyniki dopasowania są w `match_selected_against_queues` i UI kolejek.
    """

    _ = (cur, color_job_id, limit, min_score)
    return []


__all__ = [
    "compare_color_sets",
    "match_selected_against_queues",
    "suggest_similar",
]

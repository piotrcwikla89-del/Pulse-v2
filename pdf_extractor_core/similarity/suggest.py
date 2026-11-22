"""Sugestie potencjalnie podobnych zapisów — nigdy nie łączą kolorów automatycznie."""

from __future__ import annotations

from collections.abc import Sequence

from pdf_extractor_core.similarity.models import NearDuplicateSuggestion
from pdf_extractor_core.similarity.normalize import normalized_color_pair


def suggest_near_duplicate_names(
    names: Sequence[str],
    *,
    max_suggestions: int = 50,
) -> tuple[NearDuplicateSuggestion, ...]:
    """Wykrywa pary, które po normalizacji nadal się różnią, ale wyglądają blisko.

    Wynik jest wyłącznie listą sugestii do ręcznej oceny (np. przyszłe aliasy).
    Nie wpływa na Jaccard ani na wagi.
    """

    pairs = [normalized_color_pair(name) for name in names if name.strip()]
    # Unikalne po oryginale zachowując kolejność
    unique: list[tuple[str, str]] = []
    seen_original: set[str] = set()
    for pair in pairs:
        if pair.original in seen_original:
            continue
        seen_original.add(pair.original)
        unique.append((pair.original, pair.normalized))

    suggestions: list[NearDuplicateSuggestion] = []
    for index, (left_original, left_norm) in enumerate(unique):
        for right_original, right_norm in unique[index + 1 :]:
            if left_norm == right_norm:
                continue
            reason = _near_reason(left_norm, right_norm)
            if reason is None:
                continue
            suggestions.append(
                NearDuplicateSuggestion(
                    left_original=left_original,
                    right_original=right_original,
                    left_normalized=left_norm,
                    right_normalized=right_norm,
                    reason=reason,
                )
            )
            if len(suggestions) >= max_suggestions:
                return tuple(suggestions)
    return tuple(suggestions)


def _near_reason(left: str, right: str) -> str | None:
    if left.replace(" ", "") == right.replace(" ", ""):
        return "Różnica wyłącznie w spacjach wewnętrznych po częściowej normalizacji."
    if _strip_coated_suffix(left) == _strip_coated_suffix(right) and left != right:
        return (
            "Identyczny rdzeń z różnym sufiksem coated/uncoated (C/U). "
            "To NIE jest automatyczne połączenie — tylko sugestia do ręcznej reguły aliasu."
        )
    if abs(len(left) - len(right)) <= 2 and _token_sort(left) == _token_sort(right):
        return "Te same tokeny w innej kolejności."
    # Prosty Hamming / edycja 1 na krótkich ciągach bez spacji
    compact_left = left.replace(" ", "")
    compact_right = right.replace(" ", "")
    if abs(len(compact_left) - len(compact_right)) <= 1 and _edit_distance_at_most_one(
        compact_left, compact_right
    ):
        return "Odległość edycyjna ≤ 1 (sugestia literówki)."
    return None


def _strip_coated_suffix(value: str) -> str:
    for suffix in (" c", " u", " coated", " uncoated"):
        if value.endswith(suffix):
            return value[: -len(suffix)].rstrip()
    return value


def _token_sort(value: str) -> tuple[str, ...]:
    return tuple(sorted(value.split()))


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) == 1
    # insertion / deletion
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index_short = 0
    index_long = 0
    edits = 0
    while index_short < len(shorter) and index_long < len(longer):
        if shorter[index_short] == longer[index_long]:
            index_short += 1
            index_long += 1
            continue
        edits += 1
        if edits > 1:
            return False
        index_long += 1
    return True

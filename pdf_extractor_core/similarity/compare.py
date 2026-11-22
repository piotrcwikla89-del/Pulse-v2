"""Klasyczny i ważony Jaccard na zbiorach znormalizowanych nazw."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from pdf_extractor_core.similarity.models import ColorSimilarityResult, ColorSpecProfile, WeightReliability
from pdf_extractor_core.similarity.weights import ColorWeightCatalog

_PERCENT = Decimal("0.01")


def classic_jaccard_percent(left: frozenset[str], right: frozenset[str]) -> Decimal:
    """J(A,B) = |A∩B| / |A∪B| × 100. Oba puste → 100."""

    if not left and not right:
        return Decimal("100")
    union = left | right
    if not union:
        return Decimal("100")
    shared = left & right
    return _as_percent(len(shared) / len(union) * 100)


def weighted_jaccard_percent(
    left: frozenset[str],
    right: frozenset[str],
    catalog: ColorWeightCatalog,
) -> Decimal:
    """Suma wag wspólnych / suma wag unii × 100. Oba puste → 100."""

    if not left and not right:
        return Decimal("100")
    union = left | right
    if not union:
        return Decimal("100")
    shared = left & right
    numerator = sum(catalog.weight(color) for color in shared)
    denominator = sum(catalog.weight(color) for color in union)
    if denominator <= 0:
        return Decimal("0")
    return _as_percent(numerator / denominator * 100)


def compare_profiles(
    left: ColorSpecProfile,
    right: ColorSpecProfile,
    catalog: ColorWeightCatalog,
) -> ColorSimilarityResult:
    """Porównuje dwa profile; listy różnic w oryginalnej pisowni (pierwsze wystąpienie)."""

    left_set = left.normalized_colors
    right_set = right.normalized_colors
    shared = left_set & right_set
    only_left = left_set - right_set
    only_right = right_set - left_set
    union = left_set | right_set

    return ColorSimilarityResult(
        left_order_id=left.order_id,
        right_order_id=right.order_id,
        jaccard_percent=classic_jaccard_percent(left_set, right_set),
        weighted_jaccard_percent=weighted_jaccard_percent(left_set, right_set, catalog),
        shared_colors=_display_names(sorted(shared), left, right),
        only_left_colors=_display_names(sorted(only_left), left, left),
        only_right_colors=_display_names(sorted(only_right), right, right),
        shared_count=len(shared),
        union_count=len(union),
        weight_reliability=catalog.reliability,
        weight_reliability_note=catalog.reliability_note,
    )


def unit_weight_catalog() -> ColorWeightCatalog:
    """Katalog zastępczy (waga 1) gdy brak danych — reliability UNAVAILABLE."""

    return ColorWeightCatalog(
        catalog_size=0,
        document_frequency={},
        reliability=WeightReliability.UNAVAILABLE,
        reliability_note="Brak katalogu wag — zastosowano wagi jednostkowe.",
    )


def _display_names(
    normalized_sorted: list[str],
    primary: ColorSpecProfile,
    secondary: ColorSpecProfile,
) -> tuple[str, ...]:
    names: list[str] = []
    for key in normalized_sorted:
        names.append(primary.original_for(key) if key in primary.normalized_colors else secondary.original_for(key))
    return tuple(names)


def _as_percent(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

"""Silnik podobieństwa na katalogu profili w pamięci (bez zapytań SQL w pętli)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from pdf_extractor_core.models.records import SeparationRecord
from pdf_extractor_core.models.separation import SeparationDraft
from pdf_extractor_core.similarity.aliases import AliasRegistry
from pdf_extractor_core.similarity.compare import compare_profiles, unit_weight_catalog
from pdf_extractor_core.similarity.models import (
    ColorSimilarityResult,
    ColorSpecProfile,
    SimilarityMatrix,
)
from pdf_extractor_core.similarity.normalize import normalize_color_name
from pdf_extractor_core.similarity.weights import ColorWeightCatalog


def build_color_profile(
    order_id: str,
    color_names: Sequence[str],
    *,
    aliases: AliasRegistry | None = None,
) -> ColorSpecProfile:
    """Buduje profil ze zbioru nazw. Powtórzenia w jednej specyfikacji są jednokrotne."""

    registry = aliases if aliases is not None else AliasRegistry()
    originals: dict[str, str] = {}
    for raw in color_names:
        original = raw.strip()
        if not original:
            continue
        key = registry.resolve(normalize_color_name(original))
        if not key:
            continue
        originals.setdefault(key, original)
    return ColorSpecProfile(
        order_id=str(order_id),
        originals_by_normalized=dict(originals),
        normalized_colors=frozenset(originals),
    )


def build_color_profile_from_separations(
    order_id: str,
    separations: Sequence[SeparationRecord | SeparationDraft],
    *,
    aliases: AliasRegistry | None = None,
) -> ColorSpecProfile:
    """Profil z rekordów / draftów separacji — bez pokrycia i bez numerów stacji."""

    return build_color_profile(
        order_id,
        tuple(item.color_name for item in separations),
        aliases=aliases,
    )


class ColorSimilarityEngine:
    """Porównania w pamięci na wcześniej przygotowanych profilach.

    Docelowo kilka tysięcy specyfikacji: wagi IDF raz, macierz po parach i<j.
    """

    def __init__(
        self,
        profiles: Sequence[ColorSpecProfile],
        *,
        aliases: AliasRegistry | None = None,
    ) -> None:
        self._aliases = aliases if aliases is not None else AliasRegistry()
        indexed: dict[str, ColorSpecProfile] = {}
        for profile in profiles:
            if profile.order_id in indexed:
                raise ValueError(f"Zduplikowany order_id w katalogu podobieństwa: {profile.order_id}")
            indexed[profile.order_id] = profile
        self._profiles = indexed
        self._order_ids = tuple(indexed.keys())
        self._weights = (
            ColorWeightCatalog.from_profiles(tuple(indexed.values()))
            if indexed
            else unit_weight_catalog()
        )

    @classmethod
    def from_color_lists(
        cls,
        specs: Sequence[tuple[str, Sequence[str]]],
        *,
        aliases: AliasRegistry | None = None,
    ) -> ColorSimilarityEngine:
        profiles = [build_color_profile(order_id, names, aliases=aliases) for order_id, names in specs]
        return cls(profiles, aliases=aliases)

    @property
    def order_ids(self) -> tuple[str, ...]:
        return self._order_ids

    @property
    def weight_catalog(self) -> ColorWeightCatalog:
        return self._weights

    def profile(self, order_id: str) -> ColorSpecProfile:
        try:
            return self._profiles[str(order_id)]
        except KeyError as exc:
            raise KeyError(f"Brak profilu kolorystycznego dla zlecenia {order_id}") from exc

    def compare(self, left_order_id: str, right_order_id: str) -> ColorSimilarityResult:
        return compare_profiles(self.profile(left_order_id), self.profile(right_order_id), self._weights)

    def compare_one_to_all(self, order_id: str) -> tuple[ColorSimilarityResult, ...]:
        left = self.profile(order_id)
        results = [
            compare_profiles(left, self._profiles[other_id], self._weights)
            for other_id in self._order_ids
            if other_id != left.order_id
        ]
        results.sort(key=lambda item: (-item.weighted_jaccard_percent, -item.jaccard_percent, item.right_order_id))
        return tuple(results)

    def most_similar(self, order_id: str, *, limit: int = 10) -> tuple[ColorSimilarityResult, ...]:
        if limit < 0:
            raise ValueError("limit musi być >= 0")
        return self.compare_one_to_all(order_id)[:limit]

    def similarity_matrix(self, order_ids: Sequence[str] | None = None) -> SimilarityMatrix:
        ids = tuple(str(item) for item in order_ids) if order_ids is not None else self._order_ids
        for order_id in ids:
            if order_id not in self._profiles:
                raise KeyError(f"Brak profilu kolorystycznego dla zlecenia {order_id}")

        weighted: dict[tuple[str, str], Decimal] = {}
        classic: dict[tuple[str, str], Decimal] = {}
        for index, left_id in enumerate(ids):
            for right_id in ids[index:]:
                result = compare_profiles(self._profiles[left_id], self._profiles[right_id], self._weights)
                weighted[(left_id, right_id)] = result.weighted_jaccard_percent
                weighted[(right_id, left_id)] = result.weighted_jaccard_percent
                classic[(left_id, right_id)] = result.jaccard_percent
                classic[(right_id, left_id)] = result.jaccard_percent
        return SimilarityMatrix(
            order_ids=ids,
            weighted_percent=weighted,
            classic_percent=classic,
            weight_reliability=self._weights.reliability,
            weight_reliability_note=self._weights.reliability_note,
        )


def profiles_from_order_separations(
    items: Iterable[tuple[str, Sequence[SeparationRecord | SeparationDraft]]],
    *,
    aliases: AliasRegistry | None = None,
) -> tuple[ColorSpecProfile, ...]:
    """Pomocniczo: lista (order_id, separacje) → profile pod silnik."""

    return tuple(
        build_color_profile_from_separations(order_id, separations, aliases=aliases)
        for order_id, separations in items
    )

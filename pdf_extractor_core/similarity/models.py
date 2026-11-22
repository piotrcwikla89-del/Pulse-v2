"""Modele wyniku podobieństwa kolorystycznego. Bez GUI i bez SQL."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class WeightReliability(str, Enum):
    """Wiarygodność wag IDF względem wielkości katalogu."""

    RELIABLE = "RELIABLE"
    LIMITED = "LIMITED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ColorSpecProfile:
    """Profil kolorystyczny jednej specyfikacji (zbiór unikalnych nazw)."""

    order_id: str
    # Pierwsza napotkana oryginalna pisownia dla każdego klucza znormalizowanego.
    originals_by_normalized: dict[str, str]
    normalized_colors: frozenset[str]

    @property
    def color_count(self) -> int:
        return len(self.normalized_colors)

    @property
    def is_empty(self) -> bool:
        return not self.normalized_colors

    def original_for(self, normalized: str) -> str:
        return self.originals_by_normalized.get(normalized, normalized)


@dataclass(frozen=True)
class ColorSimilarityResult:
    """Pełne uzasadnienie porównania dwóch specyfikacji."""

    left_order_id: str
    right_order_id: str
    jaccard_percent: Decimal
    weighted_jaccard_percent: Decimal
    shared_colors: tuple[str, ...]
    only_left_colors: tuple[str, ...]
    only_right_colors: tuple[str, ...]
    shared_count: int
    union_count: int
    weight_reliability: WeightReliability
    weight_reliability_note: str

    @property
    def is_identical(self) -> bool:
        return self.union_count == self.shared_count and self.shared_count >= 0


@dataclass(frozen=True)
class SimilarityMatrix:
    """Macierz symetryczna; przekątna = 100%."""

    order_ids: tuple[str, ...]
    # cells[(i, j)] = weighted percent; i <= j wystarczy, ale trzymamy pełną mapę dla odczytu
    weighted_percent: dict[tuple[str, str], Decimal]
    classic_percent: dict[tuple[str, str], Decimal]
    weight_reliability: WeightReliability
    weight_reliability_note: str

    def weighted(self, left_order_id: str, right_order_id: str) -> Decimal:
        return self.weighted_percent[(left_order_id, right_order_id)]

    def classic(self, left_order_id: str, right_order_id: str) -> Decimal:
        return self.classic_percent[(left_order_id, right_order_id)]


@dataclass(frozen=True)
class NearDuplicateSuggestion:
    """Wyłącznie sugestia podobnego zapisu — nigdy automatyczne połączenie."""

    left_original: str
    right_original: str
    left_normalized: str
    right_normalized: str
    reason: str

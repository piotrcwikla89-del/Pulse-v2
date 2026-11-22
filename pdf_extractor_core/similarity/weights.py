"""Wygładzone wagi IDF dla ważonego Jaccarda."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from pdf_extractor_core.similarity.models import ColorSpecProfile, WeightReliability

# Poniżej tego N wynik wagowy jest wyłącznie orientacyjny.
_MIN_SPECS_FOR_RELIABLE_IDF = 10


@dataclass(frozen=True)
class ColorWeightCatalog:
    """Wagi w(c) = ln((N+1)/(df(c)+1)) + 1 wyliczone raz dla katalogu w pamięci."""

    catalog_size: int
    document_frequency: dict[str, int]
    reliability: WeightReliability
    reliability_note: str

    @classmethod
    def from_profiles(cls, profiles: tuple[ColorSpecProfile, ...] | list[ColorSpecProfile]) -> ColorWeightCatalog:
        n = len(profiles)
        df: Counter[str] = Counter()
        for profile in profiles:
            for color in profile.normalized_colors:
                df[color] += 1

        if n == 0:
            reliability = WeightReliability.UNAVAILABLE
            note = "Brak specyfikacji w katalogu — wagi IDF niedostępne; użyto wag jednostkowych."
        elif n < _MIN_SPECS_FOR_RELIABLE_IDF:
            reliability = WeightReliability.LIMITED
            note = (
                f"Katalog ma tylko {n} specyfikacji (próg wiarygodności: "
                f"{_MIN_SPECS_FOR_RELIABLE_IDF}). Wagi IDF są orientacyjne, nie potwierdzone statystycznie."
            )
        else:
            reliability = WeightReliability.RELIABLE
            note = f"Wagi IDF wyliczone z katalogu {n} specyfikacji."

        return cls(
            catalog_size=n,
            document_frequency=dict(df),
            reliability=reliability,
            reliability_note=note,
        )

    def weight(self, normalized_color: str) -> float:
        if self.catalog_size <= 0:
            return 1.0
        df = self.document_frequency.get(normalized_color, 0)
        return math.log((self.catalog_size + 1) / (df + 1)) + 1.0

    def weights_for(self, colors: frozenset[str] | set[str]) -> dict[str, float]:
        return {color: self.weight(color) for color in colors}

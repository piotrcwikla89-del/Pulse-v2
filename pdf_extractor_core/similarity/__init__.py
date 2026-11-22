"""Color Similarity Engine — porównanie zestawów nazw farb (fundament Production Plannera).

Bez GUI, Excel, SQL i bez harmonogramowania. Oryginalne nazwy w katalogu nie są zmieniane.
"""

from pdf_extractor_core.similarity.aliases import AliasRegistry, AliasRule
from pdf_extractor_core.similarity.catalog import (
    ColorSimilarityEngine,
    build_color_profile,
    build_color_profile_from_separations,
    profiles_from_order_separations,
)
from pdf_extractor_core.similarity.compare import (
    classic_jaccard_percent,
    compare_profiles,
    weighted_jaccard_percent,
)
from pdf_extractor_core.similarity.models import (
    ColorSimilarityResult,
    ColorSpecProfile,
    NearDuplicateSuggestion,
    SimilarityMatrix,
    WeightReliability,
)
from pdf_extractor_core.similarity.normalize import NormalizedColorName, normalize_color_name, normalized_color_pair
from pdf_extractor_core.similarity.suggest import suggest_near_duplicate_names
from pdf_extractor_core.similarity.weights import ColorWeightCatalog

__all__ = [
    "AliasRegistry",
    "AliasRule",
    "ColorSimilarityEngine",
    "ColorSimilarityResult",
    "ColorSpecProfile",
    "ColorWeightCatalog",
    "NearDuplicateSuggestion",
    "NormalizedColorName",
    "SimilarityMatrix",
    "WeightReliability",
    "build_color_profile",
    "build_color_profile_from_separations",
    "classic_jaccard_percent",
    "compare_profiles",
    "normalize_color_name",
    "normalized_color_pair",
    "profiles_from_order_separations",
    "suggest_near_duplicate_names",
    "weighted_jaccard_percent",
]

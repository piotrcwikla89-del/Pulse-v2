"""Normalizacja nazw farb do porównań. Nie zmienia zapisów w katalogu."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Spacje / NBSP / wąskie spacje → jedna spacja. Nie rusza ``_`` w Pantone solid/raster.
_WHITESPACE = re.compile(r"[\s\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
# Bezpieczne: wielokrotne myślniki / ukośniki spójne, bez usuwania C/U ani numerów.
_MULTI_DASH = re.compile(r"[-–—]{2,}")
_MULTI_SLASH = re.compile(r"/{2,}")
_SPACE_AROUND_SEP = re.compile(r"\s*([|/])\s*")


@dataclass(frozen=True)
class NormalizedColorName:
    """Oryginał z dokumentu + klucz porównawczy."""

    original: str
    normalized: str


def normalize_color_name(name: str) -> str:
    """Zwraca klucz porównawczy: casefold, spacje, bezpieczne separatory.

    Nie łączy Pantone C z U, nie usuwa ``_solid`` / ``_raster``, nie zgaduje aliasów.
    """

    text = unicodedata.normalize("NFKC", name)
    text = text.strip()
    text = _WHITESPACE.sub(" ", text)
    text = _MULTI_DASH.sub("-", text)
    text = _MULTI_SLASH.sub("/", text)
    text = _SPACE_AROUND_SEP.sub(r"\1", text)
    text = text.strip(" ")
    return text.casefold()


def normalized_color_pair(name: str) -> NormalizedColorName:
    """Oryginał bez destrukcji + znormalizowany klucz."""

    original = name.strip()
    return NormalizedColorName(original=original, normalized=normalize_color_name(original))

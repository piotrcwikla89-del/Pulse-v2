"""Jawne, zatwierdzone aliasy nazw farb.

Bez reguł w rejestrze Cyan ≠ Process Cyan oraz Pantone 234 C ≠ Pantone 234 U.
"""

from __future__ import annotations

from dataclasses import dataclass

from pdf_extractor_core.similarity.normalize import normalize_color_name


@dataclass(frozen=True)
class AliasRule:
    """Jedna zatwierdzona reguła: aliasy mapują na kanoniczną nazwę."""

    canonical: str
    aliases: frozenset[str]
    approved: bool = True

    def __post_init__(self) -> None:
        if not self.approved:
            raise ValueError("AliasRule bez approved=True nie może wejść do rejestru.")


@dataclass(frozen=True)
class AliasRegistry:
    """Rejestr aliasów. Pusty rejestr nie zmienia żadnej nazwy."""

    rules: tuple[AliasRule, ...] = ()

    def resolve(self, normalized: str) -> str:
        for rule in self.rules:
            canon = normalize_color_name(rule.canonical)
            keys = {canon, *(normalize_color_name(alias) for alias in rule.aliases)}
            if normalized in keys:
                return canon
        return normalized

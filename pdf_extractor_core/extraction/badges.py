"""Odczyt cyfr narysowanych jako znaczki numeru separacji.

W raporcie Esko numer przy próbce farby nie jest warstwą tekstową.
Kształt cyfry jest wektorowym wypełnieniem. Klasyfikator opisuje ten krój
i nie uruchamia OCR.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BadgeGlyph:
    """Cechy jednego znaczka cyfry, niezależne od koloru próbki."""

    x0: float
    y0: float
    x1: float
    y1: float
    aspect: float
    hole_count: int
    hole_y: float | None
    hole_ratio: float
    bands: tuple[float, float, float, float, float]

    @property
    def y_center(self) -> float:
        return (self.y0 + self.y1) / 2.0


@dataclass(frozen=True)
class BadgeNumber:
    """Jedna lub dwie cyfry złożone w numer separacji."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def y_center(self) -> float:
        return (self.y0 + self.y1) / 2.0


def classify_digit(glyph: BadgeGlyph) -> str | None:
    """Rozpoznaje cyfrę 0–9 z cech znaczka. Nieznany kształt zwraca brak."""

    if glyph.aspect < 0.55 and glyph.hole_count == 0:
        return "1"
    if glyph.hole_count >= 2:
        return "8"
    if glyph.hole_count == 1:
        if glyph.hole_y is not None and glyph.hole_y <= 0.42:
            return "9"
        if glyph.hole_y is not None and glyph.hole_y >= 0.58:
            return "6"
        if glyph.hole_ratio < 0.03:
            return "4"
        return "0"
    top, _second, middle, _fourth, bottom = glyph.bands
    # LAC: siódemka bywa z top≈0.59 (próg 0.60 gubił stację 7).
    if top >= 0.55 and bottom <= 0.38 and middle <= 0.55:
        return "7"
    if bottom >= 0.60 and middle <= 0.50:
        return "2"
    if top >= 0.52 and middle >= 0.65:
        return "5"
    if top < 0.52 and bottom < 0.60:
        return "3"
    return None


def group_badge_numbers(glyphs: tuple[BadgeGlyph, ...] | list[BadgeGlyph]) -> tuple[BadgeNumber, ...]:
    """Skleja sąsiednie cyfry z jednej linii, na przykład ``1`` i ``1`` w ``11``."""

    ordered = sorted(glyphs, key=lambda glyph: (round(glyph.y_center, 0), glyph.x0))
    groups: list[list[BadgeGlyph]] = []
    digits: list[list[str]] = []
    for glyph in ordered:
        digit = classify_digit(glyph)
        if digit is None:
            continue
        gap = glyph.x0 - groups[-1][-1].x1 if groups else None
        same_row = (
            groups
            and gap is not None
            and abs(glyph.y_center - _center_y(groups[-1])) <= 4
            and -1 <= gap <= 8
        )
        if same_row:
            groups[-1].append(glyph)
            digits[-1].append(digit)
            continue
        groups.append([glyph])
        digits.append([digit])
    numbers: list[BadgeNumber] = []
    for group, chars in zip(groups, digits, strict=True):
        numbers.append(
            BadgeNumber(
                text="".join(chars),
                x0=min(item.x0 for item in group),
                y0=min(item.y0 for item in group),
                x1=max(item.x1 for item in group),
                y1=max(item.y1 for item in group),
            )
        )
    return tuple(numbers)


def _center_y(group: list[BadgeGlyph]) -> float:
    return sum(item.y_center for item in group) / len(group)

"""Uniwersalna legenda farb: numer stacji (1..N) + nazwa, niezależnie od układu.

Nie zakłada stałych współrzędnych X/Y ani konkretnego dostawcy (Esko/LAC/Flexmar).
Szuka sekwencji ponumerowanych stacji i wiąże z najbliższą nazwą farby.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pdf_extractor_core.extraction.badges import BadgeNumber
from pdf_extractor_core.extraction.patterns import (
    DATE_PATTERN,
    EMPTY_STATION_NAMES,
    FLEXMAR_SCREEN_PATTERN,
    LAC_SCREEN_PATTERN,
    ORDER_NUMBER_PATTERN,
    SCREEN_PATTERN,
)
from pdf_extractor_core.extraction.profiles.flexo_spec import (
    ParsedSeparation,
    _FLEXMAR_LABEL_NOISE,
    _NOISE_NAMES,
    _join_color_name,
    _lines,
    _usable_color_name,
)
from pdf_extractor_core.extraction.word_box import WordBox

_STATION_RE = re.compile(r"^\d{1,2}$")
_SCREEN_ANY = re.compile(
    r"(?:"
    r"\d+(?:[.,]\d+)?\s*lpi\s*/\s*\d+(?:[.,]\d+)?\s*°?"
    r"|"
    r"\d+(?:[.,]\d+)?\s*/\s*\d+(?:[.,]\d+)?\s*/\s*\d+(?:[.,]\d+)?\s*%"
    r"|"
    r"\d+(?:[.,]\d+)?\s*%\s+\d+(?:[.,]\d+)?\s+lpi"
    r")",
    re.IGNORECASE,
)
# Szerszy słownik niż klasyczny Flexmar — WarmGray2, Pantone, spoty klientów.
_UNIVERSAL_COLORISH = re.compile(
    r"(?:"
    r"P(?:G)?\d|"
    r"P[A-Z][a-z0-9]|"
    r"Cyan|Magenta|Yellow|Black|White|Varnish|Matt(?:e)?|Gloss|Silver|Gold|"
    r"lakier|Highluster|UV(?:Eye)?|Primer|NIVEA|Nivea|"
    r"Warm\s*Gray|Cool\s*Gray|WarmGray|CoolGray|Process|Orange|Reflex|Vivid|"
    r"Pantone|Gray|Grey|Brown|Green|Blue|Red|Violet|Pink|Purple|"
    r"Opaque|Metallic|Special|Spot"
    r")",
    re.IGNORECASE,
)
_MAX_STATION = 24
_Y_TOL = 14.0
_NAME_RIGHT_MAX = 160.0
_NAME_GAP = 18.0


def score_legend_words(words: tuple[WordBox, ...] | list[WordBox]) -> float:
    """Ocena „ta strona wygląda na legendę farb” — do wyboru strony PDF."""

    if not words:
        return 0.0
    score = 0.0
    screens = 0
    colorish = 0
    stations: list[int] = []
    for word in words:
        token = word.text.strip()
        if not token:
            continue
        if (
            FLEXMAR_SCREEN_PATTERN.fullmatch(token)
            or SCREEN_PATTERN.fullmatch(token)
            or LAC_SCREEN_PATTERN.fullmatch(token)
            or _SCREEN_ANY.fullmatch(token)
        ):
            screens += 1
        if _UNIVERSAL_COLORISH.search(token):
            colorish += 1
        if _STATION_RE.fullmatch(token):
            value = int(token)
            if 1 <= value <= _MAX_STATION:
                stations.append(value)
    score += screens * 10.0
    score += min(colorish, 20) * 3.5
    unique = sorted(set(stations))
    if unique:
        # Bonus za ciąg 1..K
        run = 0
        expect = 1
        for value in unique:
            if value == expect:
                run += 1
                expect += 1
            elif value > expect:
                break
        score += run * 6.0
        score += min(len(unique), 16) * 1.5
    blob = " ".join(word.text for word in words).lower()
    for keyword in ("job name", "nazwa", "plate", "varnish", "cyan", "magenta", "eyemark"):
        if keyword in blob:
            score += 4.0
    return score


def universal_station_separations(
    words: tuple[WordBox, ...] | list[WordBox],
    numbers: tuple[BadgeNumber, ...] = (),
) -> list[ParsedSeparation]:
    """Paruje numery stacji z nazwami farb (układ dowolny, bez sztywnych X)."""

    word_list = tuple(words)
    stations = _collect_stations(word_list, numbers)
    if len(stations) < 2:
        return []

    # Preferuj sekwencję startującą od 1 z największą liczbą kolejnych numerów.
    stations = _prefer_sequence(stations)
    claimed_words: set[int] = set()
    paired: list[ParsedSeparation] = []
    used_numbers: set[str] = set()

    for station_no, station_box in stations:
        if station_no in used_numbers:
            continue
        name_words = _name_for_station(station_box, word_list, claimed_words)
        if not name_words:
            continue
        color_name = _join_color_name(name_words)
        if not _universal_usable_name(color_name):
            continue
        for word in name_words:
            claimed_words.add(id(word))
        used_numbers.add(station_no)
        paired.append(
            ParsedSeparation(
                color_name=color_name,
                coverage=_nearby_coverage(station_box, word_list),
                raw_coverage=None,
                separation_number=station_no,
                column_x=name_words[0].x0,
                row_y=name_words[0].y0,
                confidence=min(word.confidence for word in name_words),
            )
        )

    # Wymagaj sensownego zestawu — unikaj fałszywych trafień z numeracji strony.
    colorish_hits = sum(1 for row in paired if _UNIVERSAL_COLORISH.search(row.color_name))
    if len(paired) < 2:
        return []
    if colorish_hits < 1 and len(paired) < 4:
        return []
    return paired


def _universal_usable_name(color_name: str) -> bool:
    if not _usable_color_name(color_name):
        return False
    cleaned = color_name.strip()
    if cleaned.lower() in _NOISE_NAMES:
        return False
    if _FLEXMAR_LABEL_NOISE.search(cleaned):
        return False
    if _UNIVERSAL_COLORISH.search(cleaned):
        return True
    # Spot / niestandardowa nazwa przy stacji: ≥3 litery, nie sam numer zamówienia.
    letters = sum(1 for char in cleaned if char.isalpha())
    return letters >= 3 and not ORDER_NUMBER_PATTERN.fullmatch(cleaned)


def _collect_stations(
    words: tuple[WordBox, ...],
    numbers: tuple[BadgeNumber, ...],
) -> list[tuple[str, WordBox]]:
    found: list[tuple[str, WordBox]] = []
    for word in words:
        token = word.text.strip()
        if not _STATION_RE.fullmatch(token):
            continue
        value = int(token)
        if not (1 <= value <= _MAX_STATION):
            continue
        # Odfiltruj duże numery stron / daty wklejone jako „12” w nagłówku bez kontekstu
        # — zostawiamy, sekwencja i tak wybierze właściwy pas.
        found.append((token, word))

    for badge in numbers:
        text = str(badge.text).strip()
        if not _STATION_RE.fullmatch(text):
            continue
        value = int(text)
        if not (1 <= value <= _MAX_STATION):
            continue
        # BadgeNumber → syntetyczny WordBox
        box = WordBox(
            text=text,
            x0=float(badge.x0),
            y0=float(badge.y0) if hasattr(badge, "y0") else float(badge.y_center - 4),
            x1=float(badge.x1),
            y1=float(badge.y1) if hasattr(badge, "y1") else float(badge.y_center + 4),
            page_index=0,
            confidence=100,
        )
        found.append((text, box))

    # Unikalność po (numer, przybliżone y,x)
    dedup: dict[tuple[str, int, int], tuple[str, WordBox]] = {}
    for number, box in found:
        key = (number, int(box.y0 // 8), int(box.x0 // 8))
        dedup[key] = (number, box)
    return sorted(dedup.values(), key=lambda item: (int(item[0]), item[1].y0, item[1].x0))


def _prefer_sequence(stations: list[tuple[str, WordBox]]) -> list[tuple[str, WordBox]]:
    """Wybiera podzbiór stacji tworzący najdłuższy ciąg 1,2,3…"""

    by_num: dict[str, list[WordBox]] = {}
    for number, box in stations:
        by_num.setdefault(number, []).append(box)

    best_len = 0
    best: list[tuple[str, WordBox]] = []
    # Dla każdego możliwego „pasa” Y spróbuj złożyć ciąg.
    ys = sorted({(box.y0 + box.y1) / 2 for _, box in stations})
    bands: list[list[tuple[str, WordBox]]] = []
    # Globalnie (cała strona) — legendy bywają w 2 kolumnach.
    bands.append(stations)
    for center in ys[:: max(1, len(ys) // 8)]:
        band = [
            (number, box)
            for number, box in stations
            if abs(((box.y0 + box.y1) / 2) - center) < 90
        ]
        if len(band) >= 3:
            bands.append(band)

    for band in bands:
        chosen: list[tuple[str, WordBox]] = []
        for expect in range(1, _MAX_STATION + 1):
            key = str(expect)
            candidates = [box for number, box in band if number == key]
            if not candidates:
                if chosen:
                    break
                continue
            # Weź najbliższy lewy górny w bandzie
            box = sorted(candidates, key=lambda item: (item.y0, item.x0))[0]
            chosen.append((key, box))
        if len(chosen) > best_len:
            best_len = len(chosen)
            best = chosen
    return best if best_len >= 2 else stations


def _name_for_station(
    station: WordBox,
    words: tuple[WordBox, ...],
    claimed: set[int],
) -> list[WordBox]:
    station_y = (station.y0 + station.y1) / 2
    right = [
        word
        for word in words
        if id(word) not in claimed
        and word.x0 >= station.x1 - 1
        and word.x0 - station.x1 <= _NAME_RIGHT_MAX
        and abs(((word.y0 + word.y1) / 2) - station_y) <= _Y_TOL
        and not _STATION_RE.fullmatch(word.text.strip())
        and not _is_screen_token(word.text.strip())
    ]
    if not right:
        # Nazwa czasem pod/nad numerem (rzadkie) — weź najbliższe słowa w X.
        right = [
            word
            for word in words
            if id(word) not in claimed
            and abs(word.x0 - station.x0) < 120
            and 2 < abs(((word.y0 + word.y1) / 2) - station_y) <= 28
            and not _STATION_RE.fullmatch(word.text.strip())
            and not _is_screen_token(word.text.strip())
        ]
        right = sorted(right, key=lambda item: abs(((item.y0 + item.y1) / 2) - station_y))[:4]

    if not right:
        return []

    ordered = sorted(right, key=lambda item: item.x0)
    selected: list[WordBox] = []
    for word in ordered:
        token = word.text.strip()
        if ORDER_NUMBER_PATTERN.fullmatch(token.strip(".,:;|")) and len(token) >= 5:
            break
        if DATE_PATTERN.fullmatch(token):
            break
        if token.lower() in {"profile", "profile:", "icc", "winding", "operator", "proof"}:
            break
        if selected and word.x0 - selected[-1].x1 > _NAME_GAP:
            break
        # Puste stacje: sam myślnik
        if not selected and token in EMPTY_STATION_NAMES:
            return []
        selected.append(word)
        # Po pierwszym colorish tokenie + ewentualnym ciągu słów — nie łykaj screenów
        if _is_screen_token(token):
            selected.pop()
            break
    return selected


def _is_screen_token(token: str) -> bool:
    stripped = token.strip()
    if not stripped:
        return False
    if (
        FLEXMAR_SCREEN_PATTERN.fullmatch(stripped)
        or SCREEN_PATTERN.fullmatch(stripped)
        or LAC_SCREEN_PATTERN.fullmatch(stripped)
        or _SCREEN_ANY.fullmatch(stripped)
    ):
        return True
    if stripped.endswith("°") or "lpi" in stripped.lower():
        return True
    return False


def _nearby_coverage(station: WordBox, words: tuple[WordBox, ...]) -> Decimal | None:
    """Opcjonalne pokrycie z zapisu Esko ``a/b/c%`` w sąsiedztwie wiersza."""

    station_y = (station.y0 + station.y1) / 2
    for line in _lines(words):
        if not line:
            continue
        line_y = (line[0].y0 + line[0].y1) / 2
        if abs(line_y - station_y) > 22:
            continue
        joined = " ".join(word.text.strip() for word in line)
        match = re.search(
            r"(\d+(?:[.,]\d+)?)\s*%",
            joined,
        )
        if not match:
            continue
        # Unikaj EAN typu ``80 (%)`` daleko od stacji — wymagaj bliskości X.
        for word in line:
            if "%" not in word.text:
                continue
            if word.x0 < station.x0 - 20:
                continue
            try:
                return Decimal(match.group(1).replace(",", "."))
            except InvalidOperation:
                return None
    return None

"""Legenda separacji na pierwszej stronie: Esko, LAC bromide, Flexmar.

Puste stacje opisane samym myślnikiem nie są farbami. Pokrycie 0% przy
prawdziwej nazwie zostaje. Słownik farb dokumentu nie jest czytany.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pdf_extractor_core.extraction.badges import BadgeGlyph, BadgeNumber, group_badge_numbers
from pdf_extractor_core.extraction.patterns import (
    DATE_PATTERN,
    EMPTY_STATION_NAMES,
    FLEXMAR_SCREEN_PATTERN,
    LAC_SCREEN_PATTERN,
    ORDER_NUMBER_PATTERN,
    SCREEN_PATTERN,
)
from pdf_extractor_core.extraction.word_box import WordBox

PROFILE_ID = "flexo_spec_page1"
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y.%m.%d",
    "%Y/%m/%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
)
_SCREEN_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
# Separatory OCR bywają ``/``, ``|``, ``!``, spacje albo sklejone w jednym tokenie.
_SCREEN_SEARCH = re.compile(
    r"(?P<lpi>\d+(?:[.,]\d+)?)\s*(?:[\/|!:]+\s*)?(?P<angle>\d+(?:[.,]\d+)?)\s*(?:[\/|!:]+\s*)?"
    r"(?P<coverage>\d+(?:[.,]\d+)?)\s*%"
)
_LAC_SCREEN_SEARCH = re.compile(
    r"(?P<coverage>\d+(?:[.,]\d+)?)\s*%\s+(?P<lpi>\d+(?:\.\d+)?)\s+lpi\s+"
    r"(?P<angle>\d+(?:[.,]\d+)?)\s*°?",
    re.IGNORECASE,
)
_STATION_NUMBER = re.compile(r"^\d{1,2}$")
_JOB_LABEL = re.compile(r"^(?:job\s*name|nazwa|nama)\s*:\s*", re.IGNORECASE)
_JOB_LABEL_INLINE = re.compile(r"(?:job\s*name|nazwa|nama)\s*:\s*", re.IGNORECASE)
_VERSION_TAIL = re.compile(r"\bversion\s*:?\s*\d*\s*$", re.IGNORECASE)
_NAME_NOISE_TOKEN = re.compile(
    r"^(?:screen|screens|solid|raster|=|_|\*|•)$",
    re.IGNORECASE,
)
_TRAILING_OCR_CRUMB = re.compile(r"^[A-Za-z0-9]$")
_PANTONE_CODE = re.compile(r"^P(?:G)?\d+[A-Za-z0-9]*$", re.IGNORECASE)
_LEADING_JUNK = re.compile(r"^[„“\"'`]+")
_TRAILING_JUNK = re.compile(r"[=_„“\"'`,;:|]+$")
_LAC_MARKER = re.compile(r"lac-brt|bromide\s+template|lubna", re.IGNORECASE)
_FLEXMAR_MARKER = re.compile(r"flexmar|łubna\s+standard", re.IGNORECASE)
_NOISE_NAMES = frozenset(
    {
        "f",
        "g",
        "aw",
        "abl",
        "pbl",
        "esx",
        "lub",
        "col",
        "icc",
        "xxx",
        "tk",
        "v01",
        "head",
        "full",
        "print",
        "white",
        "foil",
        "winding",
        "operator",
        "dtp",
        "profile",
        "code",
        "proof",
    }
)
# P287 / PG0874 / PCool… / WarmGray2 / Nivea… — bez IGNORECASE na samym prefiksie P.
_COLORISH_NAME = re.compile(
    r"^(?:"
    r"P\d|PG\d|P[A-Z][a-z0-9]|"
    r"(?i:Cyan|Magenta|Yellow|Black|White|Varnish|Matt(?:e)?|Gloss|Silver|Gold|"
    r"lakier|Highluster|UV(?:Eye)?|Primer|NIVEA|Nivea|"
    r"Warm\s*Gray|Cool\s*Gray|WarmGray|CoolGray|Process|Orange|Reflex|Vivid|"
    r"Pantone|Gray|Grey)"
    r")"
)
_FLEXMAR_LABEL_NOISE = re.compile(
    r"profile|printing|winding|operator|code:|line:|ean|proof|lubna|standard|flexmar",
    re.IGNORECASE,
)
_FLEXMAR_COLOR_MIN_X = 360.0
_SCREEN_TOKEN_GAP = 28.0
_NAME_GAP = 16.0
_NAME_TO_SCREEN_GAP = 55.0
_LINE_TOLERANCE = 4.0
_LAC_NAME_DY = 22.0
# Marker ``F`` i nazwa bywają w różnych „liniach” przez tekst z lewej strony legendy.
_LAC_F_NAME_DY = 6.0
_LAC_COLOR_MIN_X = 400.0


@dataclass(frozen=True)
class ScreenAnchor:
    """Położenie zapisu siatki i pokrycia. Służy też do znalezienia znaczka numeru."""

    x0: float
    y0: float
    x1: float
    y1: float
    coverage_token: str


@dataclass(frozen=True)
class ParsedSeparation:
    color_name: str
    coverage: Decimal | None
    raw_coverage: str | None
    separation_number: str | None
    column_x: float
    row_y: float
    confidence: int = 100


@dataclass(frozen=True)
class ParsedLegend:
    order_number: str | None
    order_number_raw: str | None
    job_name: str | None
    production_date: date | None
    production_date_raw: str | None
    separations: tuple[ParsedSeparation, ...]


def screen_anchors(words: tuple[WordBox, ...] | list[WordBox]) -> tuple[ScreenAnchor, ...]:
    """Zwraca prostokąty zapisów ``lpi / kąt / pokrycie%``."""

    anchors: list[ScreenAnchor] = []
    for line in _lines(words):
        for anchor, _color, _screen in _screens_on_line(line):
            anchors.append(anchor)
    return tuple(anchors)


def parse_flexo_legend(
    words: tuple[WordBox, ...] | list[WordBox],
    badges: tuple[BadgeGlyph, ...] | list[BadgeGlyph] = (),
    *,
    filename_hint: str | None = None,
) -> ParsedLegend:
    """Składa nagłówek i wiersze legendy ze słów o współrzędnych."""

    cleaned = tuple(words)
    date_raw = _date_text(cleaned)
    job_name = _job_name(cleaned, date_raw)
    order_raw = _order_number(cleaned, filename_hint, job_name)
    return ParsedLegend(
        order_number=order_raw,
        order_number_raw=order_raw,
        job_name=job_name,
        production_date=_parse_date(date_raw),
        production_date_raw=date_raw,
        separations=_separations(cleaned, group_badge_numbers(badges)),
    )


def text_is_thin(words: tuple[WordBox, ...] | list[WordBox]) -> bool:
    """Mało znaków na stronie oznacza skan albo pustkę, nie brakującą tabelę."""

    count = sum(1 for word in words for char in word.text if char.isalnum())
    return count < 40


def _order_number(
    words: tuple[WordBox, ...],
    filename_hint: str | None,
    job_name: str | None,
) -> str | None:
    job_tokens = set() if job_name is None else set(job_name.split())
    candidates = []
    for word in words:
        token = word.text.strip().strip(".,:;|")
        if ORDER_NUMBER_PATTERN.fullmatch(token) and token not in job_tokens:
            candidates.append(token)
    # Nazwa pliku (plate code) wygrywa przy braku, wieloznaczności albo typowym błędzie OCR.
    if filename_hint and ORDER_NUMBER_PATTERN.fullmatch(filename_hint):
        if filename_hint in candidates or not candidates or len(candidates) > 1:
            return filename_hint
        # Jeden kandydat z treści różny od pliku — przy skanie częściej myli OCR.
        if filename_hint != candidates[0] and len(filename_hint) >= 6:
            return filename_hint
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    return candidates[0]


def _date_text(words: tuple[WordBox, ...]) -> str | None:
    for word in words:
        stripped = word.text.strip()
        if DATE_PATTERN.fullmatch(stripped):
            return stripped
    return None


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def _job_name(words: tuple[WordBox, ...], date_raw: str | None) -> str | None:
    labeled = _job_from_label_line(words)
    if labeled is not None:
        return labeled
    if date_raw is None:
        return _longest_phrase(words)
    for line in _lines(words):
        if not any(word.text.strip() == date_raw for word in line):
            continue
        phrases = _phrases(line, gap=_NAME_GAP)
        ranked = []
        for phrase in phrases:
            text = " ".join(word.text.strip() for word in phrase)
            if (
                text == date_raw
                or text.isdigit()
                or SCREEN_PATTERN.fullmatch(text)
                or LAC_SCREEN_PATTERN.fullmatch(text)
                or FLEXMAR_SCREEN_PATTERN.fullmatch(text)
            ):
                continue
            letters = sum(1 for char in text if char.isalpha())
            if letters < 4:
                continue
            ranked.append((letters, len(text), text))
        ranked.sort(reverse=True)
        for _letters, _length, text in ranked:
            cleaned = _clean_job_phrase(text, date_raw)
            if cleaned is not None:
                return cleaned
    return _longest_phrase(words)


def _job_from_label_line(words: tuple[WordBox, ...]) -> str | None:
    """OCR często daje ``nama: PRODUCT …`` bez poprawnej daty w tej samej linii."""

    for line in _lines(words):
        label_index = next(
            (
                index
                for index, word in enumerate(line)
                if _JOB_LABEL_INLINE.search(word.text.strip())
                or word.text.strip().lower().rstrip(":") in {"nama", "nazwa"}
            ),
            None,
        )
        if label_index is None:
            continue
        tail = line[label_index:]
        # Jeśli etykieta to osobne ``nama:``, bierz słowa po niej.
        first = tail[0].text.strip()
        if _JOB_LABEL_INLINE.fullmatch(first) or first.lower().rstrip(":") in {"nama", "nazwa"}:
            tail = tail[1:]
        text = " ".join(word.text.strip() for word in tail)
        cleaned = _clean_job_phrase(text, None)
        if cleaned is not None:
            return cleaned
    return None


def _longest_phrase(words: tuple[WordBox, ...]) -> str | None:
    best: tuple[int, int, str] | None = None
    for line in _lines(words):
        for phrase in _phrases(line, gap=_NAME_GAP):
            text = " ".join(word.text.strip() for word in phrase)
            letters = sum(1 for char in text if char.isalpha())
            if (
                letters < 8
                or SCREEN_PATTERN.fullmatch(text)
                or LAC_SCREEN_PATTERN.fullmatch(text)
                or FLEXMAR_SCREEN_PATTERN.fullmatch(text)
            ):
                continue
            if _LAC_MARKER.search(text) or _FLEXMAR_MARKER.search(text):
                continue
            candidate = (letters, len(text), text)
            if best is None or candidate > best:
                best = candidate
    if best is None:
        return None
    return _clean_job_phrase(best[2], None)


def _separations(words: tuple[WordBox, ...], numbers: tuple[BadgeNumber, ...]) -> tuple[ParsedSeparation, ...]:
    """Kolejność: uniwersalna legenda stacji → Esko → LAC → Flexmar.

    Uniwersalny parser wygrywa, gdy znajdzie sensowną sekwencję 1..N z nazwami —
    działa niezależnie od układu kolumn i dostawcy PDF.
    """

    from pdf_extractor_core.extraction.profiles.universal_legend import universal_station_separations

    candidates: list[list[ParsedSeparation]] = []
    universal = universal_station_separations(words, numbers)
    if universal:
        candidates.append(universal)
    esko = _esko_separations(words, numbers)
    if esko:
        candidates.append(esko)
    lac = _lac_raster_separations(words, numbers)
    if lac:
        candidates.append(lac)
    names = _lac_name_column_separations(words, numbers)
    if names:
        candidates.append(names)
    flexmar = _flexmar_separations(words)
    if flexmar:
        candidates.append(flexmar)
    if not candidates:
        return ()
    # Preferuj wynik z największą liczbą nazw + numerami stacji.
    def _rank(rows: list[ParsedSeparation]) -> tuple[int, int, int]:
        numbered = sum(1 for row in rows if row.separation_number)
        colorish = sum(1 for row in rows if _COLORISH_NAME.search(row.color_name))
        return (len(rows), numbered, colorish)

    best = max(candidates, key=_rank)
    return tuple(_column_then_row(best))


def _esko_separations(words: tuple[WordBox, ...], numbers: tuple[BadgeNumber, ...]) -> list[ParsedSeparation]:
    paired: list[ParsedSeparation] = []
    for line in _lines(words):
        for anchor, color_words, screen_words in _screens_on_line(line):
            station, name_words = _peel_station(color_words)
            if not name_words:
                continue
            color_name = _join_color_name(name_words)
            if not _usable_color_name(color_name):
                continue
            if _is_ocr_garbage_name(name_words, color_name):
                continue
            try:
                coverage = Decimal(anchor.coverage_token.replace(",", "."))
            except InvalidOperation:
                continue
            badge = _number_for(name_words[0], numbers)
            paired.append(
                ParsedSeparation(
                    color_name=color_name,
                    coverage=coverage,
                    raw_coverage=f"{anchor.coverage_token}%",
                    separation_number=badge if badge is not None else station,
                    column_x=name_words[0].x0,
                    row_y=name_words[0].y0,
                    confidence=_reading_confidence(name_words, screen_words),
                )
            )
    return paired


def _lac_raster_separations(
    words: tuple[WordBox, ...],
    numbers: tuple[BadgeNumber, ...] = (),
) -> list[ParsedSeparation]:
    """LAC: nazwa farby w wierszu nad ``3.16% 126.0 lpi 7.5°``."""

    lines = _lines(words)
    paired: list[ParsedSeparation] = []
    for index, line in enumerate(lines):
        for anchor, screen_words in _lac_screens_on_line(line):
            name_words = _lac_name_above(lines, index, anchor.x0)
            if not name_words:
                continue
            color_name = _join_color_name(name_words)
            if not _usable_color_name(color_name):
                continue
            try:
                coverage = Decimal(anchor.coverage_token.replace(",", "."))
            except InvalidOperation:
                continue
            paired.append(
                ParsedSeparation(
                    color_name=color_name,
                    coverage=coverage,
                    raw_coverage=f"{anchor.coverage_token}%",
                    separation_number=_number_for(name_words[0], numbers),
                    column_x=name_words[0].x0,
                    row_y=name_words[0].y0,
                    confidence=_reading_confidence(name_words, screen_words),
                )
            )
    return paired


def _lac_name_column_separations(
    words: tuple[WordBox, ...],
    numbers: tuple[BadgeNumber, ...] = (),
) -> list[ParsedSeparation]:
    """LAC solid / bez siatki: kolumna ``F`` + nazwa farby, bez pokrycia.

    Nie deduplikuje nazw: ``P7488_solid`` i ``P7488_raster`` to dwa rekordy.
    Marker ``F`` łączymy z nazwą po geometrii (X/Y), nie po kruchej linii całej strony.
    """

    if not any(_LAC_MARKER.search(word.text) for word in words):
        return []
    markers = [
        word
        for word in words
        if word.text.strip().lower() == "f" and word.x0 >= _LAC_COLOR_MIN_X
    ]
    claimed: set[int] = set()
    paired: list[ParsedSeparation] = []
    for marker in sorted(markers, key=lambda item: (item.y0, item.x0)):
        name_words = _lac_name_right_of_marker(marker, words, claimed)
        if not name_words:
            continue
        color_name = _join_color_name(name_words)
        if not _usable_color_name(color_name):
            continue
        for word in name_words:
            claimed.add(id(word))
        paired.append(
            ParsedSeparation(
                color_name=color_name,
                coverage=None,
                raw_coverage=None,
                separation_number=_number_for(name_words[0], numbers),
                column_x=name_words[0].x0,
                row_y=name_words[0].y0,
                confidence=_reading_confidence(name_words, []),
            )
        )
    return paired


def _lac_name_right_of_marker(
    marker: WordBox,
    words: tuple[WordBox, ...] | list[WordBox],
    claimed: set[int],
) -> list[WordBox]:
    """Nazwa farby na prawo od ``F``, nawet gdy tekst z lewej rozjechał grupy linii."""

    marker_y = (marker.y0 + marker.y1) / 2
    candidates = [
        word
        for word in words
        if id(word) not in claimed
        and word.x0 >= marker.x1 - 1
        and abs(((word.y0 + word.y1) / 2) - marker_y) <= _LAC_F_NAME_DY
        and word.text.strip().lower() != "f"
    ]
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item.x0)
    selected: list[WordBox] = []
    for word in ordered:
        token = word.text.strip()
        if ORDER_NUMBER_PATTERN.fullmatch(token.strip(".,:;|")):
            break
        if _is_screen_fragment(token) or _LAC_SCREEN_SEARCH.search(token):
            break
        if token.lower() in {"profile:", "profile", "icc", "g", "abl", "aw", "-", "–", "—"}:
            break
        if selected and word.x0 - selected[-1].x1 > _NAME_GAP + 8:
            break
        selected.append(word)
    return selected


def _flexmar_separations(words: tuple[WordBox, ...]) -> list[ParsedSeparation]:
    """Flexmar: osobne kolumny farb w paśmie legendy; bez sklejania przez całą szerokość."""

    text_blob = " ".join(word.text for word in words)
    screens = [word for word in words if FLEXMAR_SCREEN_PATTERN.fullmatch(word.text.strip())]
    if not (_FLEXMAR_MARKER.search(text_blob) or screens):
        return []

    if screens:
        top = min(word.y0 for word in screens) - 45
        bottom = max(word.y1 for word in screens) + 50
    else:
        top, bottom = 450.0, 760.0

    legend = [word for word in words if top <= word.y0 <= bottom and word.x0 >= 330]
    stations = [word for word in legend if _STATION_NUMBER.fullmatch(word.text.strip())]
    color_tokens = [
        word
        for word in legend
        if word.x0 >= _FLEXMAR_COLOR_MIN_X
        and not FLEXMAR_SCREEN_PATTERN.fullmatch(word.text.strip())
        and not _STATION_NUMBER.fullmatch(word.text.strip())
        and word.text.strip() not in {"/", "|", "-", "–"}
        and not _FLEXMAR_LABEL_NOISE.search(word.text)
    ]
    paired: list[ParsedSeparation] = []
    for phrase in _flexmar_phrases(color_tokens):
        # Nie sklejaj nazw z sąsiedniej kolumny: duża dziura w X kończy frazę wcześniej.
        tight = _split_flexmar_phrase(phrase)
        for name_words in tight:
            color_name = _join_color_name(name_words)
            if not _usable_color_name(color_name):
                continue
            # WarmGray2 / spoty klientów — nie wymagaj sztywnego prefiksu P/CMYK.
            if not _COLORISH_NAME.search(color_name):
                letters = sum(1 for char in color_name if char.isalpha())
                if letters < 3:
                    continue
            if _FLEXMAR_LABEL_NOISE.search(color_name):
                continue
            station = _flexmar_station_for(name_words[0], stations)
            paired.append(
                ParsedSeparation(
                    color_name=color_name,
                    coverage=None,
                    raw_coverage=None,
                    separation_number=station,
                    column_x=name_words[0].x0,
                    row_y=name_words[0].y0,
                    confidence=_reading_confidence(name_words, []),
                )
            )
    return paired


def _flexmar_phrases(words: list[WordBox]) -> list[list[WordBox]]:
    """Fraza w linii Y, od lewej do prawej — bez cofania do lewej kolumny."""

    phrases: list[list[WordBox]] = []
    ordered = sorted(words, key=lambda item: (item.y0, item.x0))
    for word in ordered:
        if not phrases:
            phrases.append([word])
            continue
        previous = phrases[-1][-1]
        new_line = abs(word.y0 - previous.y0) > _LINE_TOLERANCE
        jumped_left = word.x0 + 5 < previous.x0
        wide_gap = word.x0 - previous.x1 > _NAME_GAP
        if new_line or jumped_left or wide_gap:
            phrases.append([word])
        else:
            phrases[-1].append(word)
    return phrases


def _split_flexmar_phrase(phrase: list[WordBox]) -> list[list[WordBox]]:
    """Rozdziela ``PG0874`` | ``P2727`` gdy leżą w różnych kolumnach tej samej linii."""

    if not phrase:
        return []
    groups: list[list[WordBox]] = [[phrase[0]]]
    for word in phrase[1:]:
        gap = word.x0 - groups[-1][-1].x1
        # Kolumny Flexmar są ~80–120 pt od siebie; nazwa wieloczłonowa ma małe luki.
        if gap > 40:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def _flexmar_station_for(color: WordBox, stations: list[WordBox]) -> str | None:
    best: tuple[float, str] | None = None
    color_y = (color.y0 + color.y1) / 2
    for station in stations:
        if abs(((station.y0 + station.y1) / 2) - color_y) > 12:
            continue
        gap = color.x0 - station.x1
        if gap < -2 or gap > 55:
            continue
        if best is None or gap < best[0]:
            best = (gap, station.text.strip())
    return best[1] if best is not None else None


_OCR_VARNISH = re.compile(
    r"^(?:wamish|yamnish|vamish|varmish|warnish|varnlsh|varnish|lakier)/?$",
    re.IGNORECASE,
)


def _normalize_ocr_color_token(token: str) -> str:
    cleaned = token.strip().rstrip("/")
    if _OCR_VARNISH.fullmatch(cleaned):
        return "Varnish"
    if re.fullmatch(r"[Ff](\d{2,4}[A-Za-z0-9]*)", cleaned):
        return "P" + cleaned[1:]
    lowered = cleaned.lower()
    if lowered in {"cyen", "cyan"}:
        return "Cyan"
    if re.fullmatch(r"magent[ao]?", lowered):
        return "Magenta"
    if re.fullmatch(r"yel[l]?ow", lowered):
        return "Yellow"
    if re.fullmatch(r"blac[kc]", lowered):
        return "Black"
    return cleaned


def _join_color_name(name_words: list[WordBox]) -> str:
    parts: list[str] = []
    for word in name_words:
        token = word.text.strip().strip(".,:;|")
        token = _LEADING_JUNK.sub("", token)
        token = _TRAILING_JUNK.sub("", token)
        token = _normalize_ocr_color_token(token)
        if not token or token.lower() in {"f", "-", "–", "—"}:
            continue
        if _NAME_NOISE_TOKEN.fullmatch(token):
            continue
        if ORDER_NUMBER_PATTERN.fullmatch(token):
            continue
        if token.lower() in {"profile", "code:", "printing", "line:", "winding:"}:
            break
        parts.append(token)
    # OCR dokleja ``C`` / ``2`` za kodem pantone — nie ruszać ``PWarm Gray 2``.
    if parts and _PANTONE_CODE.fullmatch(parts[0]):
        while len(parts) > 1 and _TRAILING_OCR_CRUMB.fullmatch(parts[-1]):
            parts.pop()
    return " ".join(parts).strip()


def _usable_color_name(color_name: str) -> bool:
    cleaned = color_name.strip()
    if not cleaned or cleaned in EMPTY_STATION_NAMES:
        return False
    if cleaned.isdigit():
        return False
    if cleaned.lower() in _NOISE_NAMES:
        return False
    if DATE_PATTERN.fullmatch(cleaned) or ORDER_NUMBER_PATTERN.fullmatch(cleaned):
        return False
    if LAC_SCREEN_PATTERN.fullmatch(cleaned) or SCREEN_PATTERN.fullmatch(cleaned):
        return False
    if FLEXMAR_SCREEN_PATTERN.fullmatch(cleaned):
        return False
    if _COLORISH_NAME.match(cleaned):
        return True
    letters = sum(1 for char in cleaned if char.isalpha())
    return letters >= 2


def _is_ocr_garbage_name(name_words: list[WordBox], color_name: str) -> bool:
    """Odrzuca całkiem niepewne OCR bez wyglądu nazwy farby."""

    if not name_words:
        return True
    name_confidence = min(word.confidence for word in name_words)
    if name_confidence > 0:
        return False
    return _COLORISH_NAME.search(color_name) is None


def _lac_screens_on_line(line: list[WordBox]) -> list[tuple[ScreenAnchor, list[WordBox]]]:
    if not line:
        return []
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for index, word in enumerate(line):
        text = word.text.strip()
        if index:
            cursor += 1
        start = cursor
        cursor += len(text)
        spans.append((start, cursor))
        parts.append(text)
    joined = " ".join(parts)
    found: list[tuple[ScreenAnchor, list[WordBox]]] = []
    used: set[int] = set()
    for match in _LAC_SCREEN_SEARCH.finditer(joined):
        indexes = [
            index
            for index, (start, end) in enumerate(spans)
            if start < match.end() and end > match.start() and index not in used
        ]
        if not indexes:
            continue
        first = indexes[0]
        last = indexes[-1]
        if indexes != list(range(first, last + 1)):
            continue
        used.update(indexes)
        screen_words = line[first : last + 1]
        anchor = ScreenAnchor(
            screen_words[0].x0,
            min(word.y0 for word in screen_words),
            screen_words[-1].x1,
            max(word.y1 for word in screen_words),
            match.group("coverage"),
        )
        found.append((anchor, screen_words))
    return found


def _lac_name_above(lines: list[list[WordBox]], screen_line_index: int, coverage_x0: float) -> list[WordBox]:
    for back in range(1, 4):
        index = screen_line_index - back
        if index < 0:
            break
        previous = lines[index]
        if abs(previous[0].y0 - lines[screen_line_index][0].y0) > _LAC_NAME_DY * back:
            break
        if _lac_screens_on_line(previous):
            continue
        name_words = _lac_name_from_marker_line(previous)
        if name_words and abs(name_words[0].x0 - coverage_x0) < 40:
            return name_words
        # Fallback: słowa w tej samej kolumnie co pokrycie
        column = [
            word
            for word in previous
            if abs(word.x0 - coverage_x0) < 35 and word.text.strip().lower() not in {"f", "|"}
        ]
        if column and _usable_color_name(" ".join(word.text.strip() for word in column)):
            return column
    return []


def _lac_name_from_marker_line(line: list[WordBox]) -> list[WordBox]:
    """``F cyan`` / ``F PReflex Blue_raster`` — F może nie być pierwszym słowem linii."""

    if not line:
        return []
    words = [word for word in line if word.text.strip()]
    marker_index = next((index for index, word in enumerate(words) if word.text.strip().lower() == "f"), None)
    if marker_index is None:
        return []
    marker_x = words[marker_index].x0
    selected: list[WordBox] = []
    for word in words[marker_index + 1 :]:
        if word.x0 < marker_x - 1:
            continue
        token = word.text.strip()
        if ORDER_NUMBER_PATTERN.fullmatch(token.strip(".,:;|")):
            break
        if selected and word.x0 - selected[-1].x1 > _NAME_GAP + 8:
            break
        if _is_screen_fragment(token) or _LAC_SCREEN_SEARCH.search(token):
            break
        if token.lower() in {"profile:", "profile", "icc"}:
            break
        selected.append(word)
    return selected


def _reading_confidence(name_words: list[WordBox], screen_words: list[WordBox]) -> int:
    meaningful = [
        word
        for word in (*name_words, *screen_words)
        if word.text.strip() not in {"/", "|"} and any(character.isalnum() for character in word.text)
    ]
    source = meaningful or [*name_words, *screen_words]
    return min(word.confidence for word in source)


def _peel_station(words: list[WordBox]) -> tuple[str | None, list[WordBox]]:
    """Numer stacji bywa osobnym słowem po lewej stronie nazwy. Na PDF tekstowym zostaje w znaczku."""

    cleaned = list(words)
    while len(cleaned) >= 2 and not any(character.isalnum() for character in cleaned[0].text):
        cleaned = cleaned[1:]
    if len(cleaned) >= 2 and _STATION_NUMBER.fullmatch(cleaned[0].text.strip()):
        return cleaned[0].text.strip(), cleaned[1:]
    return None, cleaned


def _clean_job_phrase(text: str, date_raw: str | None) -> str | None:
    cleaned = " ".join(text.split())
    if date_raw and date_raw in cleaned:
        cleaned = cleaned.split(date_raw, 1)[1]
    cleaned = cleaned.strip(" :-|")
    cleaned = _JOB_LABEL.sub("", cleaned, count=1)
    inline = _JOB_LABEL_INLINE.search(cleaned)
    if inline is not None:
        cleaned = cleaned[inline.end() :]
    cleaned = _VERSION_TAIL.sub("", cleaned, count=1)
    cleaned = cleaned.strip(" :-|")
    if sum(1 for char in cleaned if char.isalpha()) < 4:
        return None
    return cleaned


def _screens_on_line(line: list[WordBox]) -> list[tuple[ScreenAnchor, list[WordBox], list[WordBox]]]:
    if not line:
        return []
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for index, word in enumerate(line):
        text = word.text.strip()
        if index:
            cursor += 1
        start = cursor
        cursor += len(text)
        spans.append((start, cursor))
        parts.append(text)
    joined = " ".join(parts)
    found: list[tuple[ScreenAnchor, list[WordBox], list[WordBox]]] = []
    used: set[int] = set()
    for match in _SCREEN_SEARCH.finditer(joined):
        indexes = [
            index
            for index, (start, end) in enumerate(spans)
            if start < match.end() and end > match.start() and index not in used
        ]
        if not indexes:
            continue
        first = indexes[0]
        last = indexes[-1]
        if indexes != list(range(first, last + 1)):
            continue
        if any(_distance(line[index], line[index + 1]) > _SCREEN_TOKEN_GAP for index in range(first, last)):
            continue
        used.update(indexes)
        screen_words = line[first : last + 1]
        anchor = ScreenAnchor(
            screen_words[0].x0,
            min(word.y0 for word in screen_words),
            screen_words[-1].x1,
            max(word.y1 for word in screen_words),
            match.group("coverage"),
        )
        found.append((anchor, _color_words(line, first), screen_words))
    return found


def _is_screen_fragment(text: str) -> bool:
    stripped = text.strip()
    if stripped in {"/", "|", "lpi", "°"} or "%" in stripped:
        return True
    if (
        SCREEN_PATTERN.fullmatch(stripped)
        or _SCREEN_SEARCH.fullmatch(stripped)
        or LAC_SCREEN_PATTERN.fullmatch(stripped)
        or FLEXMAR_SCREEN_PATTERN.fullmatch(stripped)
    ):
        return True
    if stripped.endswith("°") and _SCREEN_NUMBER.fullmatch(stripped[:-1].replace(",", ".")):
        return True
    return "." in stripped and _SCREEN_NUMBER.fullmatch(stripped) is not None


def _color_words(line: list[WordBox], screen_index: int) -> list[WordBox]:
    if screen_index == 0:
        return []
    selected: list[WordBox] = []
    right_edge = line[screen_index].x0
    for word in reversed(line[:screen_index]):
        gap = right_edge - word.x1
        limit = _NAME_TO_SCREEN_GAP if not selected else _NAME_GAP
        if gap > limit:
            break
        token = word.text.strip()
        if _is_screen_fragment(token):
            break
        if _NAME_NOISE_TOKEN.fullmatch(token.strip(".,:;|")):
            right_edge = word.x0
            continue
        selected.append(word)
        right_edge = word.x0
    selected.reverse()
    return selected


def _number_for(color: WordBox, numbers: tuple[BadgeNumber, ...]) -> str | None:
    best: BadgeNumber | None = None
    best_gap = 10**9
    color_y = (color.y0 + color.y1) / 2
    for number in numbers:
        if abs(number.y_center - color_y) > 8:
            continue
        gap = color.x0 - number.x1
        # LAC: cyfra ~412, F ~439, nazwa ~451 — szerszy pas niż Esko.
        if gap < -1 or gap > 55:
            continue
        if gap < best_gap:
            best = number
            best_gap = gap
    if best is None:
        return None
    return best.text


def _column_then_row(rows: list[ParsedSeparation]) -> list[ParsedSeparation]:
    columns: list[list[ParsedSeparation]] = []
    for row in sorted(rows, key=lambda item: item.column_x):
        if not columns or row.column_x - columns[-1][0].column_x > 40:
            columns.append([row])
        else:
            columns[-1].append(row)
    ordered: list[ParsedSeparation] = []
    for column in columns:
        ordered.extend(sorted(column, key=lambda item: item.row_y))
    return ordered


def _lines(words: tuple[WordBox, ...] | list[WordBox]) -> list[list[WordBox]]:
    ordered = sorted(words, key=lambda word: (word.y0, word.x0))
    lines: list[list[WordBox]] = []
    for word in ordered:
        if not lines or abs(word.y0 - lines[-1][0].y0) > _LINE_TOLERANCE:
            lines.append([word])
        else:
            lines[-1].append(word)
    for line in lines:
        line.sort(key=lambda word: word.x0)
    return lines


def _phrases(line: list[WordBox], *, gap: float) -> list[list[WordBox]]:
    phrases: list[list[WordBox]] = []
    for word in line:
        if not phrases or word.x0 - phrases[-1][-1].x1 > gap:
            phrases.append([word])
        else:
            phrases[-1].append(word)
    return phrases


def _distance(left: WordBox, right: WordBox) -> float:
    return right.x0 - left.x1

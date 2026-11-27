"""Testy uniwersalnego odczytu legendy farb (stacja → nazwa)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_extractor_core.extraction.profiles.flexo_spec import parse_flexo_legend
from pdf_extractor_core.extraction.profiles.universal_legend import (
    score_legend_words,
    universal_station_separations,
)
from pdf_extractor_core.extraction.word_box import WordBox


def _w(text: str, x0: float, y0: float, *, w: float = 40, h: float = 10) -> WordBox:
    return WordBox(text=text, x0=x0, y0=y0, x1=x0 + w, y1=y0 + h, page_index=0, confidence=100)


def _nivea_legend_words() -> tuple[WordBox, ...]:
    """Układ jak w załączniku: dwie kolumny stacji + nazwy + screeny."""

    words: list[WordBox] = [
        _w("Job", 40, 20, w=20),
        _w("name:", 62, 20, w=30),
        _w("NHND", 100, 20, w=30),
        _w("84641", 135, 20, w=35),
        _w("PT", 175, 20, w=15),
        _w("RAPHAEL", 195, 20, w=50),
        _w("75ML", 250, 20, w=30),
        _w("1466078", 400, 20, w=50),
    ]
    # Kolumna 1
    rows_left = [
        (1, None, None, 80),
        (2, "P130", None, 110),
        (3, "Cyan", "150lpi/7,5°", 140),
        (4, "Magenta", "150lpi/67,5°", 170),
        (5, "Yellow", "150lpi/82,5°", 200),
        (6, "Black", "150lpi/37,5°", 230),
    ]
    for number, name, screen, y in rows_left:
        words.append(_w(str(number), 60, y, w=12))
        if name:
            words.append(_w(name, 90, y, w=55))
        if screen:
            words.append(_w(screen, 160, y, w=70))
    # Kolumna 2
    rows_right = [
        (7, "WarmGray2", "150lpi/67,5°", 80),
        (8, "Nivea", None, 110),
        (9, None, None, 140),
        (10, None, None, 170),
        (11, "Varnish", None, 200),
    ]
    for number, name, screen, y in rows_right:
        words.append(_w(str(number), 320, y, w=12))
        if name == "Nivea":
            words.append(_w("Nivea", 350, y, w=35))
            words.append(_w("Vivid", 390, y, w=30))
            words.append(_w("Blue", 425, y, w=28))
        elif name:
            words.append(_w(name, 350, y, w=70))
        if screen:
            words.append(_w(screen, 430, y, w=70))
    return tuple(words)


def test_universal_extracts_nivea_style_legend():
    words = _nivea_legend_words()
    rows = universal_station_separations(words)
    names = {row.color_name for row in rows}
    numbers = {row.separation_number for row in rows}
    assert "P130" in names
    assert "Cyan" in names
    assert "Magenta" in names
    assert "Yellow" in names
    assert "Black" in names
    assert "WarmGray2" in names
    assert any("Nivea" in name and "Blue" in name for name in names)
    assert "Varnish" in names
    # Puste stacje 1/9/10 bez nazwy nie wchodzą.
    assert "1" not in numbers or all(row.color_name for row in rows if row.separation_number == "1") is False
    assert "9" not in numbers
    assert "10" not in numbers
    assert len(rows) >= 7


def test_parse_flexo_legend_uses_universal():
    legend = parse_flexo_legend(_nivea_legend_words(), filename_hint="1466078")
    names = [row.color_name for row in legend.separations]
    assert "WarmGray2" in names
    assert "Cyan" in names
    assert legend.order_number == "1466078"


def test_score_prefers_legend_page():
    legend = _nivea_legend_words()
    other = (
        _w("Artwork", 40, 40),
        _w("preview", 100, 40),
        _w("only", 160, 40),
    )
    assert score_legend_words(legend) > score_legend_words(other)


def test_layout_shift_still_works():
    """Te same dane przesunięte w X/Y — bez sztywnych progów 330/360."""

    shifted = tuple(
        WordBox(
            text=word.text,
            x0=word.x0 + 220,
            y0=word.y0 + 180,
            x1=word.x1 + 220,
            y1=word.y1 + 180,
            page_index=0,
            confidence=100,
        )
        for word in _nivea_legend_words()
    )
    rows = universal_station_separations(shifted)
    names = {row.color_name for row in rows}
    assert "P130" in names
    assert "WarmGray2" in names
    assert "Varnish" in names

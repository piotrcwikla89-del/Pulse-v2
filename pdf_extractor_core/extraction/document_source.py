"""Czyta pierwszą stronę PDF do słów i znaczków numerów. Bez słownika farb."""

from dataclasses import dataclass
from pathlib import Path

import fitz

from pdf_extractor_core.extraction.badges import BadgeGlyph
from pdf_extractor_core.extraction.profiles.flexo_spec import screen_anchors
from pdf_extractor_core.extraction.word_box import WordBox


class PdfOpenError(Exception):
    """Plik nie daje się otworzyć jako PDF."""


@dataclass(frozen=True)
class DocumentText:
    page_count: int
    words: tuple[WordBox, ...]
    badges: tuple[BadgeGlyph, ...]


def read_document(path: Path) -> DocumentText:
    """Odczytuje stronę 1. Znaczki cyfr bierze tylko z pasa legendy separacji."""

    try:
        document = fitz.open(path)
    except Exception as exc:
        raise PdfOpenError(str(exc)) from exc
    try:
        if document.page_count < 1:
            raise PdfOpenError("PDF nie zawiera stron.")
        page = document[0]
        words = _words_from_page(page)
        badges = _badges_in_legend(page, words)
        return DocumentText(page_count=document.page_count, words=words, badges=badges)
    except PdfOpenError:
        raise
    except Exception as exc:
        raise PdfOpenError(str(exc)) from exc
    finally:
        document.close()


def _words_from_page(page: fitz.Page) -> tuple[WordBox, ...]:
    words: list[WordBox] = []
    for item in page.get_text("words"):
        x0, y0, x1, y1, text = item[:5]
        if str(text).strip() == "":
            continue
        if x1 < x0 or y1 < y0:
            continue
        words.append(
            WordBox(
                text=str(text),
                x0=float(x0),
                y0=float(y0),
                x1=float(x1),
                y1=float(y1),
                page_index=0,
                confidence=100,
            )
        )
    return tuple(words)


def _badges_in_legend(page: fitz.Page, words: tuple[WordBox, ...]) -> tuple[BadgeGlyph, ...]:
    zones = _legend_number_zones(words)
    if not zones:
        return ()
    glyphs: list[BadgeGlyph] = []
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing["rect"])
        # Cyfra „1” bywa wąska (~3 pt).
        if not (2.5 <= rect.width <= 12 and 5 <= rect.height <= 14):
            continue
        if len(drawing["items"]) < 3:
            continue
        if not any(_intersects(rect, zone) for zone in zones):
            continue
        glyph = _glyph_from_drawing(drawing)
        if glyph is not None:
            glyphs.append(glyph)
    return tuple(glyphs)


def _legend_number_zones(words: tuple[WordBox, ...]) -> tuple[tuple[float, float, float, float], ...]:
    """Pasy numerów stacji: Esko/LAC przy siatce oraz LAC przy markerze ``F``."""

    zones: list[tuple[float, float, float, float]] = []
    for anchor in screen_anchors(words):
        zones.append((anchor.x0 - 78, anchor.y0 - 6, anchor.x0 + 2, anchor.y1 + 6))
    for word in words:
        if word.text.strip().lower() != "f" or word.x0 < 400:
            continue
        # Numer w znaczku ~412; nie obejmuje litery F (~440), bo OCR myli F z „5”.
        zones.append((word.x0 - 45, word.y0 - 8, word.x0 - 8, word.y1 + 8))
    return tuple(zones)


def _intersects(rect: fitz.Rect, zone: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = zone
    return rect.x1 >= x0 and rect.x0 <= x1 and rect.y1 >= y0 and rect.y0 <= y1


def _glyph_from_drawing(drawing: dict) -> BadgeGlyph | None:
    rect = fitz.Rect(drawing["rect"])
    ink = _rasterize_drawing(drawing)
    if not ink or not ink[0]:
        return None
    height = len(ink)
    width = len(ink[0])
    ink_pixels = sum(sum(row) for row in ink)
    if ink_pixels == 0:
        return None
    hole_count, hole_y, hole_pixels = _holes(ink)
    bands = tuple(_band_ratio(ink, index) for index in range(5))
    return BadgeGlyph(
        x0=float(rect.x0),
        y0=float(rect.y0),
        x1=float(rect.x1),
        y1=float(rect.y1),
        aspect=float(rect.width / rect.height) if rect.height else 0.0,
        hole_count=hole_count,
        hole_y=hole_y,
        hole_ratio=hole_pixels / ink_pixels,
        bands=(bands[0], bands[1], bands[2], bands[3], bands[4]),
    )


def _rasterize_drawing(drawing: dict) -> list[list[bool]]:
    rect = fitz.Rect(drawing["rect"])
    canvas = fitz.open()
    try:
        pad = 1
        page = canvas.new_page(width=rect.width + 2 * pad, height=rect.height + 2 * pad)
        shape = page.new_shape()
        origin_x = rect.x0 - pad
        origin_y = rect.y0 - pad

        def shifted(point: fitz.Point) -> fitz.Point:
            return fitz.Point(point.x - origin_x, point.y - origin_y)

        for item in drawing["items"]:
            operation = item[0]
            if operation == "l":
                shape.draw_line(shifted(item[1]), shifted(item[2]))
            elif operation == "c":
                shape.draw_bezier(shifted(item[1]), shifted(item[2]), shifted(item[3]), shifted(item[4]))
        shape.finish(color=(0, 0, 0), fill=(0, 0, 0), closePath=True)
        shape.commit()
        pixmap = page.get_pixmap(matrix=fitz.Matrix(8, 8), alpha=False)
        ink: list[list[bool]] = []
        for y in range(pixmap.height):
            ink.append([pixmap.pixel(x, y)[0] < 128 for x in range(pixmap.width)])
        return ink
    finally:
        canvas.close()


def _band_ratio(ink: list[list[bool]], index: int) -> float:
    height = len(ink)
    start = height * index // 5
    end = height * (index + 1) // 5
    total = 0
    hits = 0
    for row in ink[start:end]:
        total += len(row)
        hits += sum(row)
    if total == 0:
        return 0.0
    return hits / total


def _holes(ink: list[list[bool]]) -> tuple[int, float | None, int]:
    height = len(ink)
    width = len(ink[0])
    seen = [[False for _ in range(width)] for _ in range(height)]
    stack: list[tuple[int, int]] = []
    for x in range(width):
        for y in (0, height - 1):
            if not ink[y][x] and not seen[y][x]:
                seen[y][x] = True
                stack.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            if not ink[y][x] and not seen[y][x]:
                seen[y][x] = True
                stack.append((x, y))
    while stack:
        x, y = stack.pop()
        for next_x, next_y in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= next_x < width and 0 <= next_y < height and not ink[next_y][next_x] and not seen[next_y][next_x]:
                seen[next_y][next_x] = True
                stack.append((next_x, next_y))
    hole_count = 0
    largest_pixels = 0
    largest_y: float | None = None
    for y in range(height):
        for x in range(width):
            if ink[y][x] or seen[y][x]:
                continue
            hole_count += 1
            pixels = 0
            sum_y = 0
            region = [(x, y)]
            seen[y][x] = True
            while region:
                current_x, current_y = region.pop()
                pixels += 1
                sum_y += current_y
                for next_x, next_y in (
                    (current_x + 1, current_y),
                    (current_x - 1, current_y),
                    (current_x, current_y + 1),
                    (current_x, current_y - 1),
                ):
                    if (
                        0 <= next_x < width
                        and 0 <= next_y < height
                        and not ink[next_y][next_x]
                        and not seen[next_y][next_x]
                    ):
                        seen[next_y][next_x] = True
                        region.append((next_x, next_y))
            if pixels > largest_pixels:
                largest_pixels = pixels
                largest_y = (sum_y / pixels) / height
    return hole_count, largest_y, largest_pixels

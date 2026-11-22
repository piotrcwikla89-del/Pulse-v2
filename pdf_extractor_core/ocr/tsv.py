"""Mapowanie TSV Tesseract na słowa ze współrzędnymi strony."""

from pdf_extractor_core.extraction.word_box import WordBox


def word_boxes_from_tsv(
    data: dict[str, list[object]],
    *,
    dpi: int,
    origin: tuple[float, float] = (0.0, 0.0),
    page_index: int = 0,
) -> tuple[WordBox, ...]:
    """Zamienia wiersze ``image_to_data`` na współrzędne w punktach PDF.

    Pusty tekst i pewność ujemna są pomijane. Poziom 5, gdy jest w danych, oznacza słowo.
    """

    if dpi <= 0:
        raise ValueError("Rozdzielczość OCR musi być dodatnia.")
    texts = data.get("text", [])
    confidences = data.get("conf", [])
    lefts = data.get("left", [])
    tops = data.get("top", [])
    widths = data.get("width", [])
    heights = data.get("height", [])
    levels = data.get("level")
    scale = 72.0 / dpi
    origin_x, origin_y = origin
    words: list[WordBox] = []
    for index, text in enumerate(texts):
        if levels is not None and int(levels[index]) != 5:
            continue
        token = str(text).strip()
        if token == "":
            continue
        confidence = _confidence(confidences[index] if index < len(confidences) else 0)
        if confidence is None:
            continue
        width = float(widths[index])
        height = float(heights[index])
        if width <= 0 or height <= 0:
            continue
        x0 = origin_x + float(lefts[index]) * scale
        y0 = origin_y + float(tops[index]) * scale
        words.append(
            WordBox(
                text=token,
                x0=x0,
                y0=y0,
                x1=x0 + width * scale,
                y1=y0 + height * scale,
                page_index=page_index,
                confidence=confidence,
            )
        )
    return tuple(words)


def _confidence(value: object) -> int | None:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    if number > 100:
        return 100
    return number

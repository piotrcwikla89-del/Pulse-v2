"""Adapter Tesseracta. Parser legendy dostaje te same WordBox co ścieżka tekstowa."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from pdf_extractor_core.extraction.patterns import DATE_PATTERN
from pdf_extractor_core.extraction.word_box import WordBox
from pdf_extractor_core.models.config import EngineConfig
from pdf_extractor_core.ocr.tsv import word_boxes_from_tsv


class OcrUnavailable(RuntimeError):
    """Program Tesseract albo biblioteka obrazu nie jest dostępna."""


class OcrFailed(RuntimeError):
    """Tesseract uruchomił się i zakończył błędem albo nie oddał obrazu."""


@dataclass(frozen=True)
class OcrPage:
    words: tuple[WordBox, ...]


def read_first_page(path: Path, config: EngineConfig, *, on_stage=None) -> OcrPage:
    """Renderuje pierwszą stronę i zwraca słowa OCR we współrzędnych PDF."""

    executable = resolve_tesseract(config)
    if executable is None:
        raise OcrUnavailable("Brak programu Tesseract. OCR skanu nie został wykonany.")
    try:
        import fitz
        import pytesseract
    except ImportError as exc:
        raise OcrUnavailable("Brak biblioteki potrzebnej do OCR strony.") from exc

    from pdf_extractor_core.ocr.preprocessor import color_grid_left, fade_color, grayscale, prepare_ocr_image

    if config.ocr_dpi <= 0:
        raise OcrFailed("Rozdzielczość OCR musi być dodatnia.")
    pytesseract.pytesseract.tesseract_cmd = executable
    _apply_tessdata_prefix(config, executable)
    _emit(on_stage, "OCR: wycinanie legendy…")
    document = fitz.open(path)
    try:
        page = document[0]
        clip, origin = _legend_clip(page)
        _emit(on_stage, f"OCR: render {config.ocr_dpi} DPI…")
        image = _render(page, clip, config.ocr_dpi)
    except Exception as exc:
        raise OcrFailed(f"Nie można wyrenderować strony do OCR: {exc}") from exc
    finally:
        document.close()

    left_px = color_grid_left(grayscale(image), config.ocr_dpi)
    # Nagłówek (data, job, plate) siedzi w górnej części legendy — mniejszy obraz = szybszy Tesseract.
    header_height = max(80, int(image.shape[0] * 0.42))
    _emit(on_stage, "OCR: odczyt nagłówka…")
    header_words = _recognize(fade_color(image[:header_height, :]), config, executable, origin)
    if left_px is None or left_px < 20 or left_px >= image.shape[1] - 20:
        return OcrPage(words=tuple(header_words))

    scale = 72.0 / config.ocr_dpi
    grid_x = origin[0] + left_px * scale
    _emit(on_stage, "OCR: odczyt tabeli farb…")
    grid = prepare_ocr_image(image[:, left_px:], dpi=config.ocr_dpi)
    grid_words = _recognize(grid.gray, config, executable, (grid_x, origin[1]))
    return OcrPage(words=tuple(_merge_legend_words(header_words, grid_words, grid_x)))


def _emit(on_stage, message: str) -> None:
    if on_stage is not None:
        on_stage(message)


def _render(page: object, clip: object, dpi: int):
    import fitz

    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False)
    return _pixmap_rgb(pixmap)


def _recognize(gray, config: EngineConfig, executable: str, origin: tuple[float, float]) -> list[WordBox]:
    import pytesseract

    try:
        data = pytesseract.image_to_data(
            gray,
            lang=config.ocr_languages,
            config=_tesseract_config(config, executable),
            output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrUnavailable("Brak programu Tesseract. OCR skanu nie został wykonany.") from exc
    except Exception as exc:
        raise OcrFailed(f"Tesseract nie odczytał strony: {exc}") from exc
    return list(word_boxes_from_tsv(data, dpi=config.ocr_dpi, origin=origin, page_index=0))


def _merge_legend_words(
    header_words: list[WordBox],
    grid_words: list[WordBox],
    grid_x: float,
) -> list[WordBox]:
    """Nagłówek zostaje z pełnej legendy, a wiersze farb z ciaśniejszego odczytu prawej siatki."""

    date_y = next(
        (word.y0 for word in header_words if DATE_PATTERN.fullmatch(word.text.strip().strip(".,:;|"))),
        None,
    )
    kept = [
        word
        for word in header_words
        if word.x1 < grid_x - 1 or (date_y is not None and abs(word.y0 - date_y) <= 8)
    ]
    if date_y is None:
        kept.extend(grid_words)
    else:
        kept.extend(word for word in grid_words if word.y0 >= date_y + 10)
    return kept


def resolve_tesseract(config: EngineConfig) -> str | None:
    """Ścieżka z konfiguracji ma pierwszeństwo. Pusta konfiguracja szuka instalacji lokalnej."""

    if config.tesseract_cmd:
        return config.tesseract_cmd if Path(config.tesseract_cmd).is_file() else None
    found = shutil.which("tesseract")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _legend_clip(page: object) -> tuple[object, tuple[float, float]]:
    """Wycina pasmo legendy po liniach tabeli. Probe w niskiej skali (tańszy niż pełny render)."""

    import fitz

    from pdf_extractor_core.ocr.preprocessor import legend_rows

    rect = page.rect
    probe_top = rect.height * 0.5
    # Skala 0.5 wystarcza do wykrycia linii; pełny decode i tak i tak nastąpi przy renderze DPI.
    probe = page.get_pixmap(
        matrix=fitz.Matrix(0.5, 0.5),
        clip=fitz.Rect(0, probe_top, rect.width, rect.height),
        alpha=False,
    )
    gray = _pixmap_gray(probe)
    bounds = legend_rows(gray)
    if bounds is None:
        top = max(0.0, float(rect.height) * 0.55)
        return fitz.Rect(0, top, rect.width, rect.height), (0.0, top)
    y0, y1 = bounds
    # legend_rows zwraca piksele probe (skala 0.5) → punkty PDF.
    scale = 2.0
    top = max(0.0, probe_top + y0 * scale - 40)
    bottom = min(rect.height, probe_top + y1 * scale + 16)
    if bottom - top < 80:
        top = max(0.0, float(rect.height) * 0.55)
        return fitz.Rect(0, top, rect.width, rect.height), (0.0, top)
    return fitz.Rect(0, top, rect.width, rect.height), (0.0, top)


def _pixmap_rgb(pixmap: object):
    import numpy as np

    channels = pixmap.n
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, channels).copy()
    if channels == 4:
        return image[:, :, :3]
    return image


def _pixmap_gray(pixmap: object):
    import cv2

    image = _pixmap_rgb(pixmap)
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def _resolve_tessdata_dir(config: EngineConfig, executable: str) -> str | None:
    if config.tessdata_dir:
        candidate = Path(config.tessdata_dir)
        if candidate.is_dir():
            return str(candidate)
    sibling = Path(executable).parent / "tessdata"
    return str(sibling) if sibling.is_dir() else None


def _apply_tessdata_prefix(config: EngineConfig, executable: str) -> None:
    """Ustawia TESSDATA_PREFIX zamiast ``--tessdata-dir``.

    Na Windows ścieżka ze spacją (np. ``C:\\PDF Extractor\\...``) psuje
    ``--tessdata-dir`` przez pytesseract: bez cudzysłowów Tesseract bierze
    tylko ``C:\\PDF``, z cudzysłowami wkleja je do nazwy pliku.
    """

    tessdata = _resolve_tessdata_dir(config, executable)
    if tessdata:
        os.environ["TESSDATA_PREFIX"] = tessdata


def _tesseract_config(config: EngineConfig, executable: str) -> str:
    del executable  # tessdata przez TESSDATA_PREFIX, nie przez --tessdata-dir
    # OEM 1 = LSTM; szybsze i wystarczające dla tej legendy.
    return f"--oem 1 --psm {config.ocr_page_segmentation_mode}"

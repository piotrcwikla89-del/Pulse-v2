"""Konfiguracja silnika ekstrakcji jak w PDF Color Extractor (TEXT + OCR)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pdf_extractor_core.models.config import EngineConfig, ProcessOptions
from pdf_extractor_core.services.engine import ExtractorEngine


def _discover_tesseract() -> str | None:
    env = (os.environ.get("TESSERACT_CMD") or "").strip()
    if env and Path(env).is_file():
        return env
    from pdf_extractor_core.ocr.tesseract_engine import resolve_tesseract

    return resolve_tesseract(EngineConfig())


@lru_cache(maxsize=1)
def build_engine() -> ExtractorEngine:
    tesseract = _discover_tesseract()
    tessdata = None
    if tesseract:
        sibling = Path(tesseract).parent / "tessdata"
        if sibling.is_dir():
            tessdata = str(sibling)
    config = EngineConfig(
        tesseract_cmd=tesseract,
        tessdata_dir=tessdata,
        ocr_languages="eng+pol",
        ocr_dpi=300,
        # Skanowane legendy Flexmar mają niski confidence — nie kasuj trafionych farb.
        ocr_confidence_threshold=35,
    )
    return ExtractorEngine(config=config)


def default_process_options(*, force: bool = False) -> ProcessOptions:
    return ProcessOptions(force=force, ocr_enabled=True)

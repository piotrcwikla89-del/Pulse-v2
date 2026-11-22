"""Konfiguracja silnika i opcje pojedynczego przebiegu."""

from collections.abc import Callable
from dataclasses import dataclass

from pdf_extractor_core.models.enums import ProcessingStatus
from pdf_extractor_core.models.extraction import ExtractionResult
from pdf_extractor_core.ports.cancel import CancelToken


@dataclass(frozen=True)
class EngineConfig:
    """Parametry OCR. 280 DPI: jakość skanu jak 400 DPI, render MuPDF ~2–3× szybszy."""

    tesseract_cmd: str | None = None
    tessdata_dir: str | None = None
    ocr_languages: str = "eng+pol"
    ocr_dpi: int = 280
    ocr_confidence_threshold: int = 60
    ocr_page_segmentation_mode: int = 6


@dataclass(frozen=True)
class ProgressEvent:
    completed: int
    total: int
    filename: str
    status: ProcessingStatus


ProgressCallback = Callable[[ProgressEvent], None]
StageCallback = Callable[[str], None]


@dataclass(frozen=True)
class ProcessOptions:
    """Opcje importu. ``force`` ponawia odczyt pliku o znanym skrócie SHA-256."""

    force: bool = False
    ocr_enabled: bool = True
    on_progress: ProgressCallback | None = None
    on_stage: StageCallback | None = None
    cancel: CancelToken | None = None


@dataclass(frozen=True)
class ProcessingOutcome:
    """Wynik ``process_pdf``. Przy DUPLICATE pole extraction zostaje puste."""

    status: ProcessingStatus
    extraction: ExtractionResult | None
    order_id: int | None = None
    document_version: int | None = None
    message: str = ""


@dataclass(frozen=True)
class FolderSummary:
    success_count: int
    review_count: int
    error_count: int
    duplicate_count: int
    cancelled: bool
    outcomes: tuple[ProcessingOutcome, ...] = ()

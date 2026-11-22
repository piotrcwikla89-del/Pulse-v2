"""Silnik ekstrakcji PDF Color Extractor.

Pakiet nie importuje PySide6 ani sqlite3. Okno, baza i Excel są osobnymi pakietami.
"""

from pathlib import Path

from pdf_extractor_core.errors import InvalidSource, RepositoryNotConfigured
from pdf_extractor_core.models import (
    EngineConfig,
    ExtractionResult,
    FolderSummary,
    ProcessOptions,
    ProcessingOutcome,
    ProcessingStatus,
)
from pdf_extractor_core.models.records import OrderDetails, SeparationRecord
from pdf_extractor_core.services.engine import ExtractorEngine
from pdf_extractor_core.validation.validator import validate_extraction as _validate_extraction
from pdf_extractor_core.version import CORE_VERSION, RESULT_CONTRACT_VERSION

__version__ = CORE_VERSION

__all__ = [
    "CORE_VERSION",
    "EngineConfig",
    "ExtractorEngine",
    "InvalidSource",
    "ProcessOptions",
    "ProcessingOutcome",
    "ProcessingStatus",
    "RepositoryNotConfigured",
    "RESULT_CONTRACT_VERSION",
    "extract_separations",
    "get_order",
    "get_separations",
    "process_folder",
    "process_pdf",
    "validate_extraction",
]


def extract_separations(
    path: str | Path,
    options: ProcessOptions | None = None,
    *,
    engine: ExtractorEngine,
) -> ExtractionResult:
    """Ekstrakcja bez zapisu i bez sprawdzania duplikatu."""

    return engine.extract_separations(path, options)


def validate_extraction(
    result: ExtractionResult,
    *,
    ocr_confidence: int | None = None,
    ocr_confidence_threshold: int = 60,
) -> ExtractionResult:
    """Czysta ocena kompletności wyniku."""

    return _validate_extraction(
        result,
        ocr_confidence=ocr_confidence,
        ocr_confidence_threshold=ocr_confidence_threshold,
    )


def process_pdf(
    path: str | Path,
    options: ProcessOptions | None = None,
    *,
    engine: ExtractorEngine,
) -> ProcessingOutcome:
    """Import jednego PDF przez przekazany silnik."""

    return engine.process_pdf(path, options)


def process_folder(
    path: str | Path,
    options: ProcessOptions | None = None,
    *,
    engine: ExtractorEngine,
) -> FolderSummary:
    """Import PDF-ów leżących bezpośrednio w folderze."""

    return engine.process_folder(path, options)


def get_order(order_number: str, *, engine: ExtractorEngine) -> OrderDetails | None:
    """Bieżące zlecenie z repozytorium silnika."""

    return engine.get_order(order_number)


def get_separations(
    order_number: str,
    *,
    engine: ExtractorEngine,
    document_id: int | None = None,
) -> list[SeparationRecord]:
    """Separacje bieżącej wersji albo wskazanego dokumentu."""

    return engine.get_separations(order_number, document_id=document_id)

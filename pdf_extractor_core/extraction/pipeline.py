"""Odczyt pierwszej strony. OCR startuje tylko wtedy, gdy tekst nie jest SUCCESS."""

from pathlib import Path

from pdf_extractor_core.extraction.document_source import PdfOpenError, read_document
from pdf_extractor_core.extraction.profiles.flexo_spec import PROFILE_ID, parse_flexo_legend, text_is_thin
from pdf_extractor_core.models.config import EngineConfig, ProcessOptions
from pdf_extractor_core.models.enums import ExtractionMethod, ValidationStatus
from pdf_extractor_core.models.extraction import ExtractionResult, failed_extraction
from pdf_extractor_core.models.issues import ErrorCode
from pdf_extractor_core.models.separation import SeparationDraft
from pdf_extractor_core.services.filenames import filename_order_hint
from pdf_extractor_core.validation.validator import validate_extraction
from pdf_extractor_core.version import CORE_VERSION, RESULT_CONTRACT_VERSION


def extract_document(
    path: Path,
    *,
    file_hash: str,
    file_size: int,
    options: ProcessOptions | None = None,
    config: EngineConfig | None = None,
) -> ExtractionResult:
    """Czyta PDF. Kompletna legenda tekstowa nie uruchamia OCR."""

    options = options if options is not None else ProcessOptions()
    config = config if config is not None else EngineConfig()
    try:
        document = read_document(path)
    except PdfOpenError as exc:
        return failed_extraction(
            path,
            code=ErrorCode.PDF_OPEN_FAILED,
            message=f"Nie można otworzyć PDF: {exc}",
            file_hash=file_hash,
            file_size=file_size,
        )

    hint, revision = filename_order_hint(path.name)
    legend = parse_flexo_legend(document.words, document.badges, filename_hint=hint)
    text_result = _text_result(path, document, file_hash, file_size, hint, revision, legend)
    if text_result.status is ValidationStatus.SUCCESS or not options.ocr_enabled:
        return text_result
    if options.on_stage is not None:
        options.on_stage("OCR: start (skan)…")
    ocr_result = _ocr_result(
        path,
        config,
        file_hash,
        file_size,
        document.page_count,
        hint,
        revision,
        on_stage=options.on_stage,
    )
    return _prefer_result(text_result, ocr_result)


def _text_result(path, document, file_hash, file_size, hint, revision, legend) -> ExtractionResult:
    if legend.separations:
        return validate_extraction(
            _result_from_legend(
                path,
                document.page_count,
                file_hash,
                file_size,
                hint,
                revision,
                legend,
                ExtractionMethod.TEXT,
            )
        )
    if text_is_thin(document.words):
        return _failed(
            path,
            code=ErrorCode.TEXT_INCOMPLETE,
            message="Warstwa tekstowa pierwszej strony nie zawiera tabeli separacji.",
            file_hash=file_hash,
            file_size=file_size,
            page_count=document.page_count,
            method=None,
            hint=hint,
            revision=revision,
        )
    return _failed(
        path,
        code=ErrorCode.TABLE_NOT_FOUND,
        message="Na pierwszej stronie nie rozpoznano tabeli separacji.",
        file_hash=file_hash,
        file_size=file_size,
        page_count=document.page_count,
        method=ExtractionMethod.TEXT,
        hint=hint,
        revision=revision,
    )


def _ocr_result(path, config, file_hash, file_size, page_count, hint, revision, on_stage=None) -> ExtractionResult:
    from pdf_extractor_core.ocr.tesseract_engine import OcrFailed, OcrUnavailable, read_first_page

    try:
        page = read_first_page(path, config, on_stage=on_stage)
    except OcrUnavailable as exc:
        return _failed(
            path,
            code=ErrorCode.OCR_UNAVAILABLE,
            message=str(exc),
            file_hash=file_hash,
            file_size=file_size,
            page_count=page_count,
            method=None,
            hint=hint,
            revision=revision,
        )
    except OcrFailed as exc:
        return _failed(
            path,
            code=ErrorCode.OCR_FAILED,
            message=str(exc),
            file_hash=file_hash,
            file_size=file_size,
            page_count=page_count,
            method=ExtractionMethod.OCR,
            hint=hint,
            revision=revision,
        )

    legend = parse_flexo_legend(page.words, filename_hint=hint)
    if legend.separations:
        confidence = min(item.confidence for item in legend.separations)
        return validate_extraction(
            _result_from_legend(
                path,
                page_count,
                file_hash,
                file_size,
                hint,
                revision,
                legend,
                ExtractionMethod.OCR,
            ),
            ocr_confidence=confidence,
            ocr_confidence_threshold=config.ocr_confidence_threshold,
        )
    if not any(character.isalnum() for word in page.words for character in word.text):
        return _failed(
            path,
            code=ErrorCode.TEXT_INCOMPLETE,
            message="OCR nie znalazł tekstu tabeli separacji na pierwszej stronie.",
            file_hash=file_hash,
            file_size=file_size,
            page_count=page_count,
            method=ExtractionMethod.OCR,
            hint=hint,
            revision=revision,
        )
    return _failed(
        path,
        code=ErrorCode.TABLE_NOT_FOUND,
        message="OCR nie rozpoznał tabeli separacji na pierwszej stronie.",
        file_hash=file_hash,
        file_size=file_size,
        page_count=page_count,
        method=ExtractionMethod.OCR,
        hint=hint,
        revision=revision,
    )


def _result_from_legend(path, page_count, file_hash, file_size, hint, revision, legend, method) -> ExtractionResult:
    rows = tuple(
        SeparationDraft(
            line_no=index,
            separation_number=item.separation_number,
            color_name=item.color_name,
            coverage=item.coverage,
            raw_coverage=item.raw_coverage,
            extraction_method=method,
            validation_status=ValidationStatus.SUCCESS,
        )
        for index, item in enumerate(legend.separations, start=1)
    )
    return ExtractionResult(
        contract_version=RESULT_CONTRACT_VERSION,
        core_version=CORE_VERSION,
        source_filename=path.name,
        source_path=str(path),
        file_hash=file_hash,
        file_size=file_size,
        page_count=page_count,
        page_index=0,
        extraction_method=method,
        profile_id=PROFILE_ID,
        order_number=legend.order_number,
        order_number_raw=legend.order_number_raw,
        filename_order_hint=hint,
        document_revision=revision,
        job_name=legend.job_name,
        production_date=legend.production_date,
        production_date_raw=legend.production_date_raw,
        separations=rows,
        status=ValidationStatus.SUCCESS,
        issues=(),
        error=None,
    )


def _prefer_result(text_result: ExtractionResult, ocr_result: ExtractionResult) -> ExtractionResult:
    """TEXT wygrywa, gdy OCR nie dokłada pokryć — chroni czyste nazwy Flexmar/LAC."""

    if not text_result.separations:
        return ocr_result
    if not ocr_result.separations:
        return text_result
    text_cov = sum(1 for row in text_result.separations if row.coverage is not None)
    ocr_cov = sum(1 for row in ocr_result.separations if row.coverage is not None)
    if ocr_cov > text_cov:
        return ocr_result
    if ocr_cov == text_cov and len(ocr_result.separations) > len(text_result.separations):
        return ocr_result
    if ocr_result.status is ValidationStatus.SUCCESS and text_result.status is not ValidationStatus.SUCCESS:
        if ocr_cov >= text_cov:
            return ocr_result
    return text_result


def _failed(path, *, code, message, file_hash, file_size, page_count, method, hint, revision) -> ExtractionResult:
    result = failed_extraction(path, code=code, message=message, file_hash=file_hash, file_size=file_size)
    return ExtractionResult(
        contract_version=result.contract_version,
        core_version=result.core_version,
        source_filename=result.source_filename,
        source_path=result.source_path,
        file_hash=result.file_hash,
        file_size=result.file_size,
        page_count=page_count,
        page_index=0,
        extraction_method=method,
        profile_id=PROFILE_ID if method is not None else "",
        order_number=None,
        order_number_raw=None,
        filename_order_hint=hint,
        document_revision=revision,
        job_name=None,
        production_date=None,
        production_date_raw=None,
        separations=(),
        status=result.status,
        issues=result.issues,
        error=result.error,
    )

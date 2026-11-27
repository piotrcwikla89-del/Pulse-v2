"""Odczyt strony z legendą farb. OCR startuje tylko gdy tekst nie jest SUCCESS."""

from pathlib import Path

from pdf_extractor_core.extraction.document_source import (
    PdfOpenError,
    iter_legend_page_candidates,
    read_document,
)
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
    """Czyta PDF. Próbuje stron z najsilniejszą legendą (nie tylko strona 0)."""

    options = options if options is not None else ProcessOptions()
    config = config if config is not None else EngineConfig()
    hint, revision = filename_order_hint(path.name)

    try:
        candidates = iter_legend_page_candidates(path, limit=4)
    except Exception:  # noqa: BLE001
        candidates = [0]

    best_text: ExtractionResult | None = None
    last_fail: ExtractionResult | None = None
    page_count = 0

    for page_index in candidates:
        try:
            document = read_document(path, preferred_page=page_index)
        except PdfOpenError as exc:
            return failed_extraction(
                path,
                code=ErrorCode.PDF_OPEN_FAILED,
                message=f"Nie można otworzyć PDF: {exc}",
                file_hash=file_hash,
                file_size=file_size,
            )
        page_count = document.page_count
        legend = parse_flexo_legend(document.words, document.badges, filename_hint=hint)
        text_result = _text_result(
            path,
            document,
            file_hash,
            file_size,
            hint,
            revision,
            legend,
            page_index=document.page_index,
        )
        if text_result.separations:
            if text_result.status is ValidationStatus.SUCCESS:
                best_text = text_result
                break
            if best_text is None or len(text_result.separations) > len(best_text.separations):
                best_text = text_result
        else:
            last_fail = text_result

    text_result = best_text or last_fail
    if text_result is None:
        return failed_extraction(
            path,
            code=ErrorCode.PDF_OPEN_FAILED,
            message="Nie można odczytać PDF.",
            file_hash=file_hash,
            file_size=file_size,
        )

    if text_result.status is ValidationStatus.SUCCESS or not options.ocr_enabled:
        return text_result
    if options.on_stage is not None:
        options.on_stage("OCR: start (skan)…")

    # Skan bez warstwy tekstowej: score stron po tekście nic nie mówi — OCR kilku stron.
    ocr_pages = list(candidates)
    total_pages = page_count or text_result.page_count or 1
    for index in range(min(total_pages, 5)):
        if index not in ocr_pages:
            ocr_pages.append(index)

    best_ocr: ExtractionResult | None = None
    last_ocr_fail: ExtractionResult | None = None
    for ocr_page in ocr_pages[:5]:
        if options.on_stage is not None:
            options.on_stage(f"OCR: strona {ocr_page + 1}…")
        ocr_result = _ocr_result(
            path,
            config,
            file_hash,
            file_size,
            total_pages,
            hint,
            revision,
            page_index=ocr_page,
            on_stage=options.on_stage,
        )
        if ocr_result.separations:
            if best_ocr is None or len(ocr_result.separations) > len(best_ocr.separations):
                best_ocr = ocr_result
            if ocr_result.status is ValidationStatus.SUCCESS and len(ocr_result.separations) >= 2:
                break
        else:
            last_ocr_fail = ocr_result

    return _prefer_result(text_result, best_ocr or last_ocr_fail or text_result)


def _text_result(path, document, file_hash, file_size, hint, revision, legend, *, page_index: int) -> ExtractionResult:
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
                page_index=page_index,
            )
        )
    if text_is_thin(document.words):
        return _failed(
            path,
            code=ErrorCode.TEXT_INCOMPLETE,
            message="Warstwa tekstowa strony nie zawiera tabeli separacji.",
            file_hash=file_hash,
            file_size=file_size,
            page_count=document.page_count,
            page_index=page_index,
            method=None,
            hint=hint,
            revision=revision,
        )
    return _failed(
        path,
        code=ErrorCode.TABLE_NOT_FOUND,
        message="Nie rozpoznano tabeli / legendy separacji na stronie PDF.",
        file_hash=file_hash,
        file_size=file_size,
        page_count=document.page_count,
        page_index=page_index,
        method=ExtractionMethod.TEXT,
        hint=hint,
        revision=revision,
    )


def _ocr_result(
    path,
    config,
    file_hash,
    file_size,
    page_count,
    hint,
    revision,
    *,
    page_index: int = 0,
    on_stage=None,
) -> ExtractionResult:
    from pdf_extractor_core.ocr.tesseract_engine import OcrFailed, OcrUnavailable, read_page

    try:
        page = read_page(path, config, page_index=page_index, on_stage=on_stage)
    except OcrUnavailable as exc:
        return _failed(
            path,
            code=ErrorCode.OCR_UNAVAILABLE,
            message=str(exc),
            file_hash=file_hash,
            file_size=file_size,
            page_count=page_count,
            page_index=page_index,
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
            page_index=page_index,
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
                page_index=page_index,
            ),
            ocr_confidence=confidence,
            ocr_confidence_threshold=config.ocr_confidence_threshold,
        )
    if not any(character.isalnum() for word in page.words for character in word.text):
        return _failed(
            path,
            code=ErrorCode.TEXT_INCOMPLETE,
            message="OCR nie znalazł tekstu tabeli separacji.",
            file_hash=file_hash,
            file_size=file_size,
            page_count=page_count,
            page_index=page_index,
            method=ExtractionMethod.OCR,
            hint=hint,
            revision=revision,
        )
    return _failed(
        path,
        code=ErrorCode.TABLE_NOT_FOUND,
        message="OCR nie rozpoznał tabeli separacji.",
        file_hash=file_hash,
        file_size=file_size,
        page_count=page_count,
        page_index=page_index,
        method=ExtractionMethod.OCR,
        hint=hint,
        revision=revision,
    )


def _result_from_legend(
    path,
    page_count,
    file_hash,
    file_size,
    hint,
    revision,
    legend,
    method,
    *,
    page_index: int = 0,
) -> ExtractionResult:
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
        page_index=page_index,
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


def _failed(
    path,
    *,
    code,
    message,
    file_hash,
    file_size,
    page_count,
    method,
    hint,
    revision,
    page_index: int = 0,
) -> ExtractionResult:
    result = failed_extraction(path, code=code, message=message, file_hash=file_hash, file_size=file_size)
    return ExtractionResult(
        contract_version=result.contract_version,
        core_version=result.core_version,
        source_filename=result.source_filename,
        source_path=result.source_path,
        file_hash=result.file_hash,
        file_size=result.file_size,
        page_count=page_count,
        page_index=page_index,
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

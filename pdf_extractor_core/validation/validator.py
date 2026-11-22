"""Czysta walidacja kontraktu. Nie czyta plików i nie zapisuje bazy."""

from dataclasses import replace

from pdf_extractor_core.models.enums import ExtractionMethod, ValidationStatus
from pdf_extractor_core.models.extraction import ExtractionResult
from pdf_extractor_core.models.issues import ERROR_LEVEL_CODES, ErrorCode, ExtractionError, ValidationIssue
from pdf_extractor_core.models.separation import SeparationDraft
from pdf_extractor_core.models.values import blank_to_none


def validate_extraction(
    result: ExtractionResult,
    *,
    ocr_confidence: int | None = None,
    ocr_confidence_threshold: int = 60,
) -> ExtractionResult:
    """Ocenia kompletność odczytu i zwraca nowy wynik.

    Nie usuwa wierszy i nie zmienia pisowni nazw farb. Pokrycie równe zero jest wartością.
    """

    issues: list[ValidationIssue] = []
    order_number = blank_to_none(result.order_number)
    if order_number is None:
        issues.append(ValidationIssue(ErrorCode.ORDER_NUMBER_MISSING, "Brak numeru zlecenia."))

    filename_hint = blank_to_none(result.filename_order_hint)
    if order_number is not None and filename_hint is not None and order_number != filename_hint:
        issues.append(
            ValidationIssue(
                ErrorCode.ORDER_NUMBER_CONFLICT,
                "Numer zlecenia z dokumentu różni się od numeru w nazwie pliku.",
            )
        )

    if blank_to_none(result.job_name) is None:
        issues.append(ValidationIssue(ErrorCode.JOB_NAME_MISSING, "Brak nazwy produktu."))

    if blank_to_none(result.production_date_raw) is not None and result.production_date is None:
        issues.append(ValidationIssue(ErrorCode.DATE_UNPARSED, "Nie rozpoznano daty produkcji."))

    if not result.separations:
        issues.append(ValidationIssue(ErrorCode.VALIDATION_FAILED, "Brak separacji."))

    rows = tuple(_validate_row(row) for row in result.separations)
    for row in rows:
        issues.extend(row.issues)

    if _low_ocr_confidence(result.extraction_method, ocr_confidence, ocr_confidence_threshold):
        issues.append(
            ValidationIssue(
                ErrorCode.LOW_OCR_CONFIDENCE,
                "Pewność OCR jest poniżej progu.",
            )
        )

    status, error = _status_for(issues)
    return replace(
        result,
        order_number=order_number,
        separations=rows,
        issues=tuple(issues),
        status=status,
        error=error,
    )


def _validate_row(row: SeparationDraft) -> SeparationDraft:
    row_issues: list[ValidationIssue] = []
    if row.color_name.strip() == "":
        row_issues.append(ValidationIssue(ErrorCode.COLOR_NAME_MISSING, "Brak nazwy farby.", row.line_no))
    if row.coverage is None:
        row_issues.append(ValidationIssue(ErrorCode.COVERAGE_MISSING, "Brak pokrycia.", row.line_no))
    elif row.coverage < 0 or row.coverage > 100:
        row_issues.append(
            ValidationIssue(
                ErrorCode.COVERAGE_OUT_OF_RANGE,
                "Pokrycie jest poza zakresem 0–100.",
                row.line_no,
            )
        )
    row_status = ValidationStatus.REVIEW if row_issues else ValidationStatus.SUCCESS
    return replace(row, issues=tuple(row_issues), validation_status=row_status)


def _low_ocr_confidence(
    method: ExtractionMethod | None,
    ocr_confidence: int | None,
    threshold: int,
) -> bool:
    if ocr_confidence is None or method not in {ExtractionMethod.OCR, ExtractionMethod.MIXED}:
        return False
    return ocr_confidence < threshold


def _status_for(issues: list[ValidationIssue]) -> tuple[ValidationStatus, ExtractionError | None]:
    primary = next((issue for issue in issues if issue.code in ERROR_LEVEL_CODES), None)
    if primary is not None:
        return ValidationStatus.ERROR, ExtractionError(primary.code, primary.message)
    if issues:
        return ValidationStatus.REVIEW, None
    return ValidationStatus.SUCCESS, None

"""Kody problemów walidacji i błędów odczytu."""

from dataclasses import dataclass


class ErrorCode:
    """Kody kontraktu 1. Wartości są częścią słownika wyniku."""

    PDF_OPEN_FAILED = "PDF_OPEN_FAILED"
    TEXT_INCOMPLETE = "TEXT_INCOMPLETE"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    OCR_FAILED = "OCR_FAILED"
    TABLE_NOT_FOUND = "TABLE_NOT_FOUND"
    ORDER_NUMBER_MISSING = "ORDER_NUMBER_MISSING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    IO_ERROR = "IO_ERROR"
    PARSER_NOT_READY = "PARSER_NOT_READY"

    JOB_NAME_MISSING = "JOB_NAME_MISSING"
    DATE_UNPARSED = "DATE_UNPARSED"
    COVERAGE_MISSING = "COVERAGE_MISSING"
    COVERAGE_OUT_OF_RANGE = "COVERAGE_OUT_OF_RANGE"
    LOW_OCR_CONFIDENCE = "LOW_OCR_CONFIDENCE"
    ORDER_NUMBER_CONFLICT = "ORDER_NUMBER_CONFLICT"
    COLOR_NAME_MISSING = "COLOR_NAME_MISSING"


ERROR_LEVEL_CODES = frozenset(
    {
        ErrorCode.PDF_OPEN_FAILED,
        ErrorCode.TEXT_INCOMPLETE,
        ErrorCode.OCR_UNAVAILABLE,
        ErrorCode.OCR_FAILED,
        ErrorCode.TABLE_NOT_FOUND,
        ErrorCode.ORDER_NUMBER_MISSING,
        ErrorCode.VALIDATION_FAILED,
        ErrorCode.IO_ERROR,
        ErrorCode.PARSER_NOT_READY,
    }
)


@dataclass(frozen=True)
class ValidationIssue:
    """Pojedynczy problem odczytu. line_no wskazuje wiersz separacji, jeśli dotyczy."""

    code: str
    message: str
    line_no: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"code": self.code, "message": self.message}
        if self.line_no is not None:
            payload["line_no"] = self.line_no
        return payload


@dataclass(frozen=True)
class ExtractionError:
    """Błąd, który blokuje status SUCCESS. Przy REVIEW pole wyniku zostaje puste."""

    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}

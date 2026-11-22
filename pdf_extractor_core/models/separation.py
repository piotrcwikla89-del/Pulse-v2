"""Wiersz separacji. Nazwa farby nie jest unikalna."""

from dataclasses import dataclass
from decimal import Decimal

from pdf_extractor_core.models.enums import ExtractionMethod, ValidationStatus
from pdf_extractor_core.models.issues import ValidationIssue
from pdf_extractor_core.models.values import coverage_to_text


@dataclass(frozen=True)
class SeparationDraft:
    """Jedna separacja w kolejności tabeli. Powtórzona nazwa farby zostaje osobnym wierszem."""

    line_no: int
    separation_number: str | None
    color_name: str
    coverage: Decimal | None
    raw_coverage: str | None
    extraction_method: ExtractionMethod
    validation_status: ValidationStatus
    issues: tuple[ValidationIssue, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "line_no": self.line_no,
            "separation_number": self.separation_number,
            "color_name": self.color_name,
            "coverage": coverage_to_text(self.coverage),
            "raw_coverage": self.raw_coverage,
            "extraction_method": self.extraction_method.value,
            "validation_status": self.validation_status.value,
            "issues": [issue.to_dict() for issue in self.issues],
        }

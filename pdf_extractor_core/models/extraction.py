"""Kontrakt wyniku ekstrakcji. Odbiorca nie zna parsera ani bazy."""

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from pdf_extractor_core.models.enums import ExtractionMethod, ValidationStatus
from pdf_extractor_core.models.issues import ExtractionError, ValidationIssue
from pdf_extractor_core.models.separation import SeparationDraft
from pdf_extractor_core.models.values import date_to_text
from pdf_extractor_core.services.filenames import filename_order_hint
from pdf_extractor_core.version import CORE_VERSION, RESULT_CONTRACT_VERSION


@dataclass(frozen=True)
class ExtractionResult:
    """Wynik odczytu jednego PDF. Status SUCCESS, REVIEW albo ERROR."""

    contract_version: int
    core_version: str
    source_filename: str
    source_path: str
    file_hash: str
    file_size: int
    page_count: int
    page_index: int
    extraction_method: ExtractionMethod | None
    profile_id: str
    order_number: str | None
    order_number_raw: str | None
    filename_order_hint: str | None
    document_revision: str | None
    job_name: str | None
    production_date: date | None
    production_date_raw: str | None
    separations: tuple[SeparationDraft, ...]
    status: ValidationStatus
    issues: tuple[ValidationIssue, ...]
    error: ExtractionError | None

    def to_dict(self) -> dict[str, object]:
        method = None if self.extraction_method is None else self.extraction_method.value
        return {
            "contract_version": self.contract_version,
            "core_version": self.core_version,
            "source_filename": self.source_filename,
            "source_path": self.source_path,
            "file_hash": self.file_hash,
            "file_size": self.file_size,
            "page_count": self.page_count,
            "page_index": self.page_index,
            "extraction_method": method,
            "profile_id": self.profile_id,
            "order_number": self.order_number,
            "order_number_raw": self.order_number_raw,
            "filename_order_hint": self.filename_order_hint,
            "document_revision": self.document_revision,
            "job_name": self.job_name,
            "production_date": date_to_text(self.production_date),
            "production_date_raw": self.production_date_raw,
            "status": self.status.value,
            "issues": [issue.to_dict() for issue in self.issues],
            "error": None if self.error is None else self.error.to_dict(),
            "separations": [row.to_dict() for row in self.separations],
        }


def failed_extraction(path: Path, *, code: str, message: str, file_hash: str = "", file_size: int = 0) -> ExtractionResult:
    """Wynik błędu, zanim parser zdąży odczytać tabelę."""

    hint, revision = filename_order_hint(path.name)
    issue = ValidationIssue(code, message)
    return ExtractionResult(
        contract_version=RESULT_CONTRACT_VERSION,
        core_version=CORE_VERSION,
        source_filename=path.name,
        source_path=str(path),
        file_hash=file_hash,
        file_size=file_size,
        page_count=0,
        page_index=0,
        extraction_method=None,
        profile_id="",
        order_number=None,
        order_number_raw=None,
        filename_order_hint=hint,
        document_revision=revision,
        job_name=None,
        production_date=None,
        production_date_raw=None,
        separations=(),
        status=ValidationStatus.ERROR,
        issues=(issue,),
        error=ExtractionError(code, message),
    )


def with_file_identity(result: ExtractionResult, *, file_hash: str, file_size: int) -> ExtractionResult:
    return replace(result, file_hash=file_hash, file_size=file_size)

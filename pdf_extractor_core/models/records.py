"""Rekordy odczytu katalogu. Nie są wierszami konkretnej bazy."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pdf_extractor_core.models.enums import ExtractionMethod, ValidationStatus
from pdf_extractor_core.models.separation import SeparationDraft


@dataclass(frozen=True)
class OrderDetails:
    """Bieżące pola zlecenia widoczne dla odbiorcy katalogu."""

    id: int
    order_number: str
    job_name: str | None
    production_date: date | None
    production_date_raw: str | None
    current_document_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SeparationRecord:
    """Zapisana separacja. Pokrycie zero pozostaje zerem."""

    id: int
    order_id: int
    document_id: int
    line_no: int
    separation_number: str | None
    color_name: str
    coverage: Decimal | None
    raw_coverage: str | None
    extraction_method: ExtractionMethod
    validation_status: ValidationStatus


@dataclass(frozen=True)
class ProcessedFileRecord:
    """Plik już zapisany w katalogu. Status nie przyjmuje wartości DUPLICATE."""

    id: int
    filename: str
    file_hash: str
    file_path: str
    file_size: int
    processing_status: ValidationStatus
    processed_at: str
    error_code: str | None
    error_message: str | None
    page_count: int | None
    core_version: str
    contract_version: int


@dataclass(frozen=True)
class DocumentVersionRecord:
    id: int
    order_id: int
    processed_file_id: int
    version_no: int
    is_current: bool
    document_revision: str | None
    extraction_method: ExtractionMethod
    validation_status: ValidationStatus
    profile_id: str


@dataclass(frozen=True)
class OrderDraft:
    order_number: str
    job_name: str | None
    production_date: date | None
    production_date_raw: str | None


@dataclass(frozen=True)
class DocumentVersionDraft:
    """Nowa wersja specyfikacji razem z pełnym zestawem separacji."""

    order_id: int
    processed_file_id: int
    job_name: str | None
    production_date: date | None
    production_date_raw: str | None
    document_revision: str | None
    extraction_method: ExtractionMethod
    validation_status: ValidationStatus
    profile_id: str
    page_index: int
    separations: tuple[SeparationDraft, ...]


@dataclass(frozen=True)
class ProcessedFileDraft:
    filename: str
    file_hash: str
    file_path: str
    file_size: int
    processing_status: ValidationStatus
    error_code: str | None
    error_message: str | None
    page_count: int | None
    core_version: str
    contract_version: int


@dataclass(frozen=True)
class ImportEventDraft:
    file_hash: str
    filename: str
    event_status: str
    message: str | None
    processed_file_id: int | None = None


@dataclass(frozen=True)
class DashboardCounts:
    orders: int
    separations: int
    documents: int
    errors: int
    review: int


@dataclass(frozen=True)
class OrderQuery:
    text: str | None = None
    status: ValidationStatus | None = None
    sort_by: str = "order_number"
    descending: bool = False
    limit: int | None = None
    offset: int = 0


@dataclass(frozen=True)
class OrderSummary:
    id: int
    order_number: str
    job_name: str | None
    production_date: date | None
    validation_status: ValidationStatus | None
    separation_count: int
    source_filename: str | None
    source_path: str | None = None

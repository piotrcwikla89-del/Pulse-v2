"""Zapis wyniku ekstrakcji przez porty. Bez SQL."""

from pdf_extractor_core.models.enums import ExtractionMethod, ProcessingStatus, ValidationStatus
from pdf_extractor_core.models.extraction import ExtractionResult
from pdf_extractor_core.models.records import (
    DocumentVersionDraft,
    ImportEventDraft,
    OrderDraft,
    ProcessedFileDraft,
)
from pdf_extractor_core.ports.repositories import UnitOfWork


def persist_extraction(
    unit_of_work: UnitOfWork,
    result: ExtractionResult,
    *,
    force: bool = False,
) -> tuple[int | None, int | None]:
    """Zapisuje jeden wynik. Zwraca ``(order_id, version_no)`` albo puste przy ERROR bez katalogu."""

    existing = unit_of_work.processed_files().find_by_hash(result.file_hash)
    status = result.status
    error_code = None if result.error is None else result.error.code
    error_message = None if result.error is None else result.error.message

    if existing is not None and force:
        file_id = existing.id
        unit_of_work.processed_files().update_status(
            file_id,
            processing_status=status.value,
            error_code=error_code,
            error_message=error_message,
        )
    elif existing is not None:
        unit_of_work.import_events().append(
            ImportEventDraft(
                file_hash=result.file_hash,
                filename=result.source_filename,
                event_status=ProcessingStatus.DUPLICATE.value,
                message="Ten plik PDF został już zaimportowany.",
                processed_file_id=existing.id,
            )
        )
        return None, None
    else:
        file_id = unit_of_work.processed_files().save(
            ProcessedFileDraft(
                filename=result.source_filename,
                file_hash=result.file_hash,
                file_path=result.source_path,
                file_size=result.file_size,
                processing_status=status,
                error_code=error_code,
                error_message=error_message,
                page_count=result.page_count,
                core_version=result.core_version,
                contract_version=result.contract_version,
            )
        )

    order_id: int | None = None
    version_no: int | None = None
    if status in {ValidationStatus.SUCCESS, ValidationStatus.REVIEW} and result.order_number:
        order_id, version_no = _save_catalog(unit_of_work, result, file_id=file_id, force=force)

    unit_of_work.import_events().append(
        ImportEventDraft(
            file_hash=result.file_hash,
            filename=result.source_filename,
            event_status=status.value,
            message=error_message,
            processed_file_id=file_id,
        )
    )
    return order_id, version_no


def record_duplicate(
    unit_of_work: UnitOfWork,
    *,
    file_hash: str,
    filename: str,
    processed_file_id: int,
) -> None:
    unit_of_work.import_events().append(
        ImportEventDraft(
            file_hash=file_hash,
            filename=filename,
            event_status=ProcessingStatus.DUPLICATE.value,
            message="Ten plik PDF został już zaimportowany.",
            processed_file_id=processed_file_id,
        )
    )


def _save_catalog(
    unit_of_work: UnitOfWork,
    result: ExtractionResult,
    *,
    file_id: int,
    force: bool,
) -> tuple[int, int]:
    method = result.extraction_method
    if method is None:
        method = ExtractionMethod.TEXT
    order_draft = OrderDraft(
        order_number=result.order_number or "",
        job_name=result.job_name,
        production_date=result.production_date,
        production_date_raw=result.production_date_raw,
    )
    order_id = unit_of_work.orders().upsert_current(order_draft, current_document_id=None)
    document_draft = DocumentVersionDraft(
        order_id=order_id,
        processed_file_id=file_id,
        job_name=result.job_name,
        production_date=result.production_date,
        production_date_raw=result.production_date_raw,
        document_revision=result.document_revision,
        extraction_method=method,
        validation_status=result.status,
        profile_id=result.profile_id or "",
        page_index=result.page_index,
        separations=result.separations,
    )
    if force:
        document_id, version_no = unit_of_work.documents().replace_current_from_same_file(
            document_draft,
            file_hash=result.file_hash,
        )
    else:
        document_id, version_no = unit_of_work.documents().add_version(document_draft)
    unit_of_work.orders().upsert_current(order_draft, current_document_id=document_id)
    return order_id, version_no

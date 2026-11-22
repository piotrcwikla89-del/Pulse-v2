"""Przypadki użycia CORE. Parser i SQL są za portami, nie w tym module."""

from pathlib import Path

from pdf_extractor_core.errors import InvalidSource, RepositoryNotConfigured
from pdf_extractor_core.extraction.pipeline import extract_document
from pdf_extractor_core.models.config import (
    EngineConfig,
    FolderSummary,
    ProcessOptions,
    ProcessingOutcome,
    ProgressEvent,
)
from pdf_extractor_core.models.enums import ProcessingStatus, ValidationStatus
from pdf_extractor_core.models.extraction import ExtractionResult, failed_extraction
from pdf_extractor_core.models.issues import ErrorCode
from pdf_extractor_core.models.records import (
    DashboardCounts,
    DocumentVersionRecord,
    OrderDetails,
    OrderQuery,
    OrderSummary,
    SeparationRecord,
)
from pdf_extractor_core.ports.repositories import UnitOfWork
from pdf_extractor_core.services.hashing import file_sha256
from pdf_extractor_core.services.persistence import persist_extraction, record_duplicate


class ExtractorEngine:
    """Silnik biznesowy. ``store`` jest fabryką jednostki pracy albo ``None``."""

    def __init__(self, config: EngineConfig | None = None, store: object | None = None) -> None:
        self._config = config if config is not None else EngineConfig()
        self._store_factory = store

    @property
    def config(self) -> EngineConfig:
        return self._config

    def extract_separations(self, path: Path, options: ProcessOptions | None = None) -> ExtractionResult:
        """Czyta jeden PDF i niczego nie zapisuje. Nie sprawdza duplikatu."""

        path = Path(path)
        if not path.is_file():
            return failed_extraction(path, code=ErrorCode.IO_ERROR, message="Nie znaleziono pliku PDF.")
        try:
            file_hash = file_sha256(path)
            file_size = path.stat().st_size
        except OSError as exc:
            return failed_extraction(
                path,
                code=ErrorCode.IO_ERROR,
                message=f"Nie można odczytać pliku PDF: {exc.strerror or exc}",
            )
        return extract_document(
            path,
            file_hash=file_hash,
            file_size=file_size,
            options=options,
            config=self._config,
        )

    def process_pdf(self, path: Path, options: ProcessOptions | None = None) -> ProcessingOutcome:
        """Importuje jeden PDF. Identyczny skrót bez ``force`` kończy się jako DUPLICATE."""

        options = options if options is not None else ProcessOptions()
        path = Path(path)
        if self._store_factory is not None and path.is_file() and not options.force:
            try:
                file_hash = file_sha256(path)
            except OSError:
                file_hash = ""
            if file_hash:
                duplicate = self._duplicate_outcome(path, file_hash)
                if duplicate is not None:
                    return duplicate
        result = self.extract_separations(path, options)
        order_id = None
        document_version = None
        if self._store_factory is not None and result.file_hash:
            with self._open_store() as unit_of_work:
                order_id, document_version = persist_extraction(
                    unit_of_work,
                    result,
                    force=options.force,
                )
                unit_of_work.commit()
        return ProcessingOutcome(
            status=_operation_status(result.status),
            extraction=result,
            order_id=order_id,
            document_version=document_version,
            message="" if result.error is None else result.error.message,
        )

    def process_folder(self, path: Path, options: ProcessOptions | None = None) -> FolderSummary:
        """Importuje pliki PDF leżące bezpośrednio w folderze. Podfoldery pomija."""

        options = options if options is not None else ProcessOptions()
        directory = Path(path)
        if not directory.is_dir():
            raise InvalidSource(f"Folder importu nie istnieje: {directory}")
        files = sorted(
            item for item in directory.iterdir() if item.is_file() and item.suffix.lower() == ".pdf"
        )
        outcomes: list[ProcessingOutcome] = []
        cancelled = False
        for index, pdf_path in enumerate(files, start=1):
            if options.cancel is not None and options.cancel.is_cancelled():
                cancelled = True
                break
            outcome = self.process_pdf(pdf_path, options)
            outcomes.append(outcome)
            if options.on_progress is not None:
                options.on_progress(
                    ProgressEvent(
                        completed=index,
                        total=len(files),
                        filename=pdf_path.name,
                        status=outcome.status,
                    )
                )
        return FolderSummary(
            success_count=_count(outcomes, ProcessingStatus.SUCCESS),
            review_count=_count(outcomes, ProcessingStatus.REVIEW),
            error_count=_count(outcomes, ProcessingStatus.ERROR),
            duplicate_count=_count(outcomes, ProcessingStatus.DUPLICATE),
            cancelled=cancelled,
            outcomes=tuple(outcomes),
        )

    def get_order(self, order_number: str) -> OrderDetails | None:
        """Zwraca bieżące zlecenie. Wymaga repozytorium."""

        with self._open_store() as unit_of_work:
            return unit_of_work.orders().get_by_number(order_number)

    def get_separations(
        self,
        order_number: str,
        *,
        document_id: int | None = None,
    ) -> list[SeparationRecord]:
        """Zwraca separacje. Domyślnie bieżąca wersja; ``document_id`` wybiera historię."""

        with self._open_store() as unit_of_work:
            if document_id is None:
                rows = unit_of_work.separations().list_for_order(order_number, current_only=True)
                return list(rows)
            rows = unit_of_work.separations().list_for_order(order_number, current_only=False)
        return [row for row in rows if row.document_id == document_id]

    def list_document_versions(self, order_number: str) -> list[DocumentVersionRecord]:
        """Historia wersji specyfikacji jednego numeru."""

        with self._open_store() as unit_of_work:
            return list(unit_of_work.documents().list_versions(order_number))

    def dashboard_counts(self) -> DashboardCounts:
        """Liczniki pulpitu z repozytorium."""

        with self._open_store() as unit_of_work:
            return unit_of_work.queries().dashboard_counts()

    def search_orders(self, query: OrderQuery | None = None) -> list[OrderSummary]:
        """Wyszukiwanie i filtr listy zleceń."""

        with self._open_store() as unit_of_work:
            return list(unit_of_work.queries().search_orders(query or OrderQuery()))

    def _duplicate_outcome(self, path: Path, file_hash: str) -> ProcessingOutcome | None:
        with self._open_store() as unit_of_work:
            existing = unit_of_work.processed_files().find_by_hash(file_hash)
            if existing is None:
                return None
            record_duplicate(
                unit_of_work,
                file_hash=file_hash,
                filename=path.name,
                processed_file_id=existing.id,
            )
            unit_of_work.commit()
        return ProcessingOutcome(
            status=ProcessingStatus.DUPLICATE,
            extraction=None,
            message="Ten plik PDF został już zaimportowany.",
        )

    def _known_hash(self, file_hash: str) -> bool:
        with self._open_store() as unit_of_work:
            return unit_of_work.processed_files().find_by_hash(file_hash) is not None

    def _open_store(self) -> UnitOfWork:
        if self._store_factory is None:
            raise RepositoryNotConfigured("Ta operacja wymaga repozytorium. Silnik został utworzony bez store.")
        opened = self._store_factory()
        return opened


def _operation_status(status: ValidationStatus) -> ProcessingStatus:
    return ProcessingStatus(status.value)


def _count(outcomes: list[ProcessingOutcome], status: ProcessingStatus) -> int:
    return sum(1 for outcome in outcomes if outcome.status is status)

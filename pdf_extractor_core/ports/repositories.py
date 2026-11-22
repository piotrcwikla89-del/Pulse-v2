"""Porty repozytorium. Implementacja SQLite żyje poza CORE."""

from typing import Protocol

from pdf_extractor_core.models.records import (
    DashboardCounts,
    DocumentVersionDraft,
    DocumentVersionRecord,
    ImportEventDraft,
    OrderDetails,
    OrderDraft,
    OrderQuery,
    OrderSummary,
    ProcessedFileDraft,
    ProcessedFileRecord,
    SeparationRecord,
)
from pdf_extractor_core.models.separation import SeparationDraft


class ProcessedFileRepository(Protocol):
    def find_by_hash(self, file_hash: str) -> ProcessedFileRecord | None:
        """Co najwyżej jeden rekord na skrót SHA-256."""

    def save(self, draft: ProcessedFileDraft) -> int:
        """Zapisuje plik i zwraca jego identyfikator."""

    def update_status(
        self,
        file_id: int,
        *,
        processing_status: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        """Aktualizuje status zapisu. Nie przyjmuje statusu DUPLICATE."""


class OrderRepository(Protocol):
    def get_by_number(self, order_number: str) -> OrderDetails | None:
        """Numer zlecenia jest unikalny."""

    def upsert_current(self, draft: OrderDraft, *, current_document_id: int | None) -> int:
        """Tworzy zlecenie albo odświeża jego bieżące pola."""


class DocumentRepository(Protocol):
    def add_version(self, draft: DocumentVersionDraft) -> tuple[int, int]:
        """Dodaje wersję, zdejmuje bieżącość poprzedniej i zwraca ``(document_id, version_no)``."""

    def replace_current_from_same_file(self, draft: DocumentVersionDraft, *, file_hash: str) -> tuple[int, int]:
        """Ponowny odczyt tego samego skrótu podmienia separacje tej wersji."""

    def list_versions(self, order_number: str) -> tuple[DocumentVersionRecord, ...]:
        """Historia specyfikacji jednego numeru zlecenia."""


class SeparationRepository(Protocol):
    def replace_for_document(self, document_id: int, rows: tuple[SeparationDraft, ...]) -> None:
        """Wstawia przekazaną listę. Nie łączy takich samych nazw farb."""

    def list_for_order(self, order_number: str, *, current_only: bool = True) -> tuple[SeparationRecord, ...]:
        """Separacje zlecenia. Domyślnie tylko bieżąca wersja dokumentu."""


class QueryRepository(Protocol):
    def dashboard_counts(self) -> DashboardCounts:
        """Liczniki pulpitu."""

    def search_orders(self, query: OrderQuery) -> tuple[OrderSummary, ...]:
        """Wyszukiwanie, filtr i sortowanie listy zleceń."""


class ImportEventRepository(Protocol):
    def append(self, draft: ImportEventDraft) -> None:
        """Dopisuje przebieg, w tym DUPLICATE, bez zmiany statusu ekstrakcji."""


class UnitOfWork(Protocol):
    """Jedna transakcja na jeden plik PDF."""

    def processed_files(self) -> ProcessedFileRepository:
        """Repozytorium plików."""

    def orders(self) -> OrderRepository:
        """Repozytorium zleceń."""

    def documents(self) -> DocumentRepository:
        """Repozytorium wersji specyfikacji."""

    def separations(self) -> SeparationRepository:
        """Repozytorium separacji."""

    def queries(self) -> QueryRepository:
        """Odczyty pulpitu i listy."""

    def import_events(self) -> ImportEventRepository:
        """Dziennik przebiegów importu."""

    def commit(self) -> None:
        """Zatwierdza transakcję pliku."""

    def rollback(self) -> None:
        """Wycofuje transakcję pliku."""

    def __enter__(self) -> "UnitOfWork":
        """Otwiera zakres transakcji."""

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Zamyka zakres. Wyjątek pozostawia decyzję commit/rollback wołającemu."""

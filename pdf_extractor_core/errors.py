"""Wyjątki silnika niezależne od interfejsu i od bazy."""


class CoreError(Exception):
    """Błąd użycia silnika, który nie jest wynikiem ekstrakcji dokumentu."""


class RepositoryNotConfigured(CoreError):
    """Odczyt katalogu wymaga fabryki repozytorium."""


class InvalidSource(CoreError):
    """Ścieżka źródłowa nie pasuje do operacji."""

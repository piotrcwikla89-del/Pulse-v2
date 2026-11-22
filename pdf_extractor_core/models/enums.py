"""Statusy i metody ekstrakcji widoczne w kontrakcie publicznym."""

from enum import Enum


class ProcessingStatus(str, Enum):
    """Wynik operacji importu. DUPLICATE nie nadpisuje zapisu ekstrakcji."""

    SUCCESS = "SUCCESS"
    REVIEW = "REVIEW"
    ERROR = "ERROR"
    DUPLICATE = "DUPLICATE"


class ValidationStatus(str, Enum):
    """Jakość odczytanych danych. Nie obejmuje duplikatu pliku."""

    SUCCESS = "SUCCESS"
    REVIEW = "REVIEW"
    ERROR = "ERROR"


class ExtractionMethod(str, Enum):
    """Sposób, którym powstały pola wyniku."""

    TEXT = "TEXT"
    OCR = "OCR"
    MIXED = "MIXED"

"""Pomocnicze formatowanie wartości kontraktu."""

from datetime import date
from decimal import Decimal


def blank_to_none(value: str | None) -> str | None:
    """Zamienia pusty tekst na brak wartości. Nie zmienia pisowni wewnątrz."""

    if value is None:
        return None
    stripped = value.strip()
    if stripped == "":
        return None
    return stripped


def coverage_to_text(value: Decimal | None) -> str | None:
    """Kanoniczny zapis procentu: zero to ``0``, bez ogona zmiennoprzecinkowego."""

    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def date_to_text(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()

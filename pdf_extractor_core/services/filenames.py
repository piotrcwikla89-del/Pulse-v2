"""Podpowiedź numeru zlecenia z nazwy pliku. Nie zastępuje numeru z treści."""

import re
from pathlib import Path

_FILENAME_HINT = re.compile(
    r"(?P<number>\d{5,})(?:\s+(?P<revision>v\d+))?",
    re.IGNORECASE,
)


def filename_order_hint(filename: str) -> tuple[str | None, str | None]:
    """Zwraca ``(numer, rewizja)`` odczytane z nazwy, np. ``1492818`` i ``v01``."""

    match = _FILENAME_HINT.search(Path(filename).stem)
    if match is None:
        return None, None
    revision = match.group("revision")
    return match.group("number"), (revision.lower() if revision else None)

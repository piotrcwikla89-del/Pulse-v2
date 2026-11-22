"""Identyfikacja pliku skrótem SHA-256. Deduplikacja dotyczy tylko treści."""

import hashlib
from pathlib import Path


def file_sha256(path: Path) -> str:
    """Zwraca skrót SHA-256 treści pliku, zapisany jako hex małymi literami."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

"""Wykrywanie środowiska (Render / lokal) i ścieżki danych."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_render() -> bool:
    return os.environ.get("RENDER", "").lower() in ("true", "1", "yes")


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def get_data_dir() -> Path:
    """Trwałe-ish dane aplikacji. Na Render bez dysku: /tmp (efemeryczne)."""

    override = (os.environ.get("PULSE_DATA_DIR") or "").strip()
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    if is_render():
        path = Path("/tmp/pulse_data")
        path.mkdir(parents=True, exist_ok=True)
        return path
    from helpers import get_base_path

    path = Path(get_base_path()) / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def planner_upload_dir() -> Path:
    path = get_data_dir() / "planner_uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def planner_thumbs_dir() -> Path:
    """Miniatury serwowane z /static/planner_thumbs — lokalnie w repo; na Render też w static jeśli writable, inaczej /tmp + osobny mount nie jest dostępny.
    Na Render static jest w image read-only poza buildem — zapisujemy do data i serwujemy osobno jeśli trzeba.
    """

    from helpers import get_base_path

    static_thumbs = Path(get_base_path()) / "static" / "planner_thumbs"
    try:
        static_thumbs.mkdir(parents=True, exist_ok=True)
        probe = static_thumbs / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return static_thumbs
    except OSError:
        path = get_data_dir() / "planner_thumbs"
        path.mkdir(parents=True, exist_ok=True)
        return path


def planner_browse_roots() -> list[tuple[str, str]]:
    """Dozwolone korzenie przeglądarki folderów (bezpieczeństwo na Render)."""

    roots: list[tuple[str, str]] = []
    pdf_root = (os.environ.get("PLANNER_PDF_ROOT") or "").strip()
    if pdf_root and os.path.isdir(pdf_root):
        roots.append(("PDF root (env)", pdf_root))
    upload = str(planner_upload_dir())
    roots.append(("Uploady planisty", upload))
    if not is_render():
        home = str(Path.home())
        roots.append(("Folder użytkownika", home))
        desktop = Path.home() / "Desktop"
        docs = Path.home() / "Documents"
        if desktop.is_dir():
            roots.append(("Pulpit", str(desktop)))
        if docs.is_dir():
            roots.append(("Dokumenty", str(docs)))
    return roots


def path_allowed_for_browse(path: str) -> bool:
    """Na Render tylko ścieżki pod whitelistą; lokalnie bez twardego limitu (jak dotychczas)."""

    if not is_render():
        return True
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    for _, root in planner_browse_roots():
        try:
            root_res = Path(root).resolve()
            if resolved == root_res or root_res in resolved.parents:
                return True
        except OSError:
            continue
    return False

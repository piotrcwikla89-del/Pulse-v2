"""Bezpieczne listowanie folderów po stronie serwera (Windows / UNC)."""

from __future__ import annotations

import os
import string
from pathlib import Path


def normalize_folder_path(raw: str | None) -> str:
    """Czyści wklejoną ścieżkę: cudzysłowy, slash'e, końcowe separatory."""

    if not raw:
        return ""
    path = str(raw).strip().strip('"').strip("'")
    if not path:
        return ""
    # Zachowaj UNC \\serwer\share
    if path.startswith("\\\\") or path.startswith("//"):
        path = path.replace("/", "\\")
        return path.rstrip("\\") if len(path) > 3 else path
    path = os.path.expandvars(os.path.expanduser(path))
    try:
        return str(Path(path))
    except Exception:  # noqa: BLE001
        return path


def list_windows_drives() -> list[dict]:
    drives: list[dict] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.isdir(root):
            drives.append(
                {
                    "name": f"{letter}:",
                    "path": root,
                    "kind": "drive",
                    "pdf_count": None,
                }
            )
    return drives


def parent_path(path: str) -> str | None:
    p = Path(path)
    parent = p.parent
    if str(parent) == str(p):
        return None
    # C:\ → None (wracamy do listy dysków)
    if len(str(p)) <= 3 and ":" in str(p):
        return None
    return str(parent)


def browse_directory(path: str | None = None) -> dict:
    """Zwraca listę podfolderów. ``path`` puste = dyski / skróty."""

    raw = normalize_folder_path(path)
    if not raw:
        items = list_windows_drives()
        home = str(Path.home())
        desktop = str(Path.home() / "Desktop")
        docs = str(Path.home() / "Documents")
        for label, candidate in (
            ("Folder użytkownika", home),
            ("Pulpit", desktop),
            ("Dokumenty", docs),
        ):
            if os.path.isdir(candidate):
                items.insert(
                    0,
                    {
                        "name": label,
                        "path": candidate,
                        "kind": "shortcut",
                        "pdf_count": _count_pdfs(candidate, shallow=True),
                    },
                )
        return {
            "ok": True,
            "path": "",
            "parent": None,
            "exists": True,
            "items": items,
            "pdf_count_here": 0,
            "message": "Wybierz dysk lub skrót",
        }

    if not os.path.isdir(raw):
        return {
            "ok": False,
            "path": raw,
            "parent": parent_path(raw),
            "exists": False,
            "items": [],
            "pdf_count_here": 0,
            "message": f"Folder nie istnieje lub jest niedostępny: {raw}",
        }

    items: list[dict] = []
    try:
        with os.scandir(raw) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if entry.name.startswith("."):
                        continue
                    items.append(
                        {
                            "name": entry.name,
                            "path": entry.path,
                            "kind": "dir",
                            "pdf_count": _count_pdfs(entry.path, shallow=True),
                        }
                    )
                except OSError:
                    continue
    except OSError as exc:
        return {
            "ok": False,
            "path": raw,
            "parent": parent_path(raw),
            "exists": False,
            "items": [],
            "pdf_count_here": 0,
            "message": f"Brak dostępu: {exc}",
        }

    return {
        "ok": True,
        "path": raw,
        "parent": parent_path(raw),
        "exists": True,
        "items": items,
        "pdf_count_here": _count_pdfs(raw, shallow=True),
        "message": "",
    }


def _count_pdfs(folder: str, *, shallow: bool = True) -> int:
    count = 0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if entry.is_file() and entry.name.lower().endswith(".pdf"):
                        count += 1
                    elif not shallow and entry.is_dir(follow_symlinks=False):
                        count += _count_pdfs(entry.path, shallow=True)
                except OSError:
                    continue
    except OSError:
        return 0
    return count

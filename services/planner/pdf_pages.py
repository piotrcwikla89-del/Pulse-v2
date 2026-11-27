"""Szybki podgląd stron PDF (cache PNG) — bez zmiany logiki importu farb."""

from __future__ import annotations

from pathlib import Path

_PAGE_SCALE = 1.15
_CACHE_SUBDIR = "pdf_pages"


def pdf_pages_cache_dir() -> Path:
    from services.runtime_env import planner_thumbs_dir

    path = planner_thumbs_dir() / _CACHE_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def pdf_page_count(pdf_path: Path) -> int:
    import fitz

    doc = fitz.open(pdf_path)
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def render_pdf_page_png(
    pdf_path: Path,
    page_index: int,
    *,
    cache_key: str,
    force: bool = False,
) -> Path | None:
    """Renderuje stronę do PNG (0-based). Wynik cache'owany na dysku."""

    import fitz

    if not pdf_path.is_file():
        return None
    safe_key = "".join(ch for ch in cache_key if ch.isalnum() or ch in "-_")[:40]
    out = pdf_pages_cache_dir() / f"{safe_key}_p{page_index}.png"
    if out.exists() and not force and out.stat().st_size > 500:
        return out

    doc = fitz.open(pdf_path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            return None
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(_PAGE_SCALE, _PAGE_SCALE), alpha=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out))
        return out
    finally:
        doc.close()

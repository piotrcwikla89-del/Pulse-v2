"""Testy scoringu miniaturek PDF (bez pełnego OCR)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.planner.thumbnails import (
    THUMB_VERSION,
    _best_run,
    _colorful_band_box,
    _layout_cue_score,
    _pixmap_is_usable,
    _rank_pages,
    render_artwork_thumbnail,
    thumbnail_path_for,
)


def test_best_run_picks_longest_dense():
    dens = [0.01, 0.05, 0.3, 0.4, 0.35, 0.02, 0.25, 0.28]
    run = _best_run(dens, 0.22)
    assert run == (2, 4)


def test_dense_band_box_on_synthetic():
    # 8x8 RGB: dark block in center
    w = h = 8
    samples = bytearray(w * h * 3)
    for i in range(len(samples)):
        samples[i] = 255
    for y in range(2, 6):
        for x in range(2, 6):
            o = (y * w + x) * 3
            samples[o : o + 3] = bytes([20, 40, 60])
    box = _colorful_band_box(w, h, samples)
    assert box is not None
    x0, y0, x1, y1 = box
    assert x0 <= 2 and y0 <= 2 and x1 >= 5 and y1 >= 5


def test_render_on_real_pdf_if_present():
    pdf_root = Path(r"C:\Users\DELL\Desktop\pdf")
    sample = pdf_root / "1518602.pdf"
    if not sample.is_file():
        return
    out = Path("static/planner_thumbs") / f"_test_probe_{THUMB_VERSION}.png"
    url = render_artwork_thumbnail(sample, out, force=True)
    assert url is not None
    assert out.exists()
    assert out.stat().st_size > 2000
    out.unlink(missing_ok=True)


def test_thumbnail_path_versioned():
    path = thumbnail_path_for("abcdef0123456789ffff", Path("static/planner_thumbs"))
    assert path.name.endswith(f"_{THUMB_VERSION}.png")
    assert "abcdef0123456789"[:16] in path.name


def _make_multipage_pdf(path: Path) -> None:
    import fitz

    doc = fitz.open()

    # Strona 0: czarna „separacja” — powinna przegrać ranking.
    black = doc.new_page(width=400, height=500)
    black.draw_rect(black.rect, color=(0, 0, 0), fill=(0, 0, 0))
    black.insert_text((40, 40), "K BLACK PLATE", fontsize=14, color=(1, 1, 1))

    # Strona 1: legenda / tekst techniczny bez wzoru.
    legend = doc.new_page(width=400, height=500)
    legend.insert_text((40, 60), "Separation legend\nC M Y K\nCoverage table", fontsize=16)

    # Strona 2: wzór z wymiarowaniem (jak sachet / eyemarki).
    art = doc.new_page(width=520, height=360)
    # Panele saszetki
    art.draw_rect(fitz.Rect(40, 60, 200, 300), color=(0.05, 0.25, 0.55), fill=(0.15, 0.45, 0.85))
    art.draw_rect(fitz.Rect(210, 60, 380, 300), color=(0.55, 0.35, 0.05), fill=(0.95, 0.75, 0.25))
    art.draw_rect(fitz.Rect(20, 40, 40, 70), color=(0, 0.2, 0.9), fill=(0, 0.35, 1))  # eyemark
    art.draw_rect(fitz.Rect(480, 40, 500, 70), color=(0, 0.2, 0.9), fill=(0, 0.35, 1))
    # Linie wymiarowe
    for x in (40, 200, 210, 380, 400):
        art.draw_line((x, 40), (x, 50), color=(0, 0, 0), width=0.6)
    art.draw_line((40, 45), (400, 45), color=(0, 0, 0), width=0.5)
    art.insert_text((50, 35), "6.5  27.5  27.5  9  18.5  eyemark 3.0  113 mm", fontsize=8)
    art.insert_text((420, 120), "0 cm\n1 cm\n2 cm", fontsize=7)
    art.insert_text((70, 180), "NIVEA Hand Cream", fontsize=14, color=(1, 1, 1))

    doc.save(path)
    doc.close()


def test_layout_cues_prefer_dimension_page():
    import fitz

    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "sample.pdf"
        _make_multipage_pdf(pdf)
        doc = fitz.open(pdf)
        try:
            scores = [_layout_cue_score(doc.load_page(i)) for i in range(doc.page_count)]
            assert scores[2] > scores[0]
            assert scores[2] > scores[1]
            ranked = _rank_pages(doc)
            assert ranked, "oczekiwano przynajmniej jednej strony w rankingu"
            assert ranked[0][0] == 2
            assert ranked[0][3] >= 12.0
        finally:
            doc.close()


def test_render_skips_black_plate_for_artwork_page():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "sample.pdf"
        out = Path(tmp) / f"thumb_{THUMB_VERSION}.png"
        _make_multipage_pdf(pdf)
        url = render_artwork_thumbnail(pdf, out, force=True)
        assert url is not None
        assert out.exists()
        assert out.stat().st_size > 2000

        # PNG nie powinno być prawie czarne.
        import fitz

        pix = fitz.Pixmap(str(out))
        assert _pixmap_is_usable(pix, min_color=8.0)


def test_black_pixmap_rejected():
    class _Pix:
        width = 200
        height = 200
        samples = bytes([4, 4, 4] * (200 * 200))

    assert _pixmap_is_usable(_Pix(), min_color=8.0) is False

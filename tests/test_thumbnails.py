"""Testy scoringu miniaturek PDF (bez pełnego OCR)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.planner.thumbnails import (
    THUMB_VERSION,
    _best_run,
    _colorful_band_box,
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

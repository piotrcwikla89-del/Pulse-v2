"""Miniatury PDF: wybór strony ze wzorem + crop gęstego, kolorowego obszaru.

Nie zmienia ekstrakcji farb — działa wyłącznie na warstwie podglądu.
Preferuje strony z różnorodnością kolorów (wzór), a nie czarne separacje.
"""

from __future__ import annotations

from pathlib import Path

THUMB_VERSION = "v5"
_MAX_PAGES = 12
_PROBE_SCALE = 0.28
_RENDER_SCALE = 1.4
_INK_THR = 248
_ROW_COL_THR = 0.18


def thumbnail_url(file_hash: str) -> str:
    return f"/media/planner_thumbs/{file_hash[:16]}_{THUMB_VERSION}.png"


def thumbnail_path_for(file_hash: str, out_dir: Path) -> Path:
    return out_dir / f"{file_hash[:16]}_{THUMB_VERSION}.png"


def render_artwork_thumbnail(
    pdf_path: Path,
    out_path: Path,
    *,
    force: bool = False,
) -> str | None:
    """Zapisuje PNG z wyciętym wzorem. Zwraca URL albo None."""

    try:
        import fitz
    except ImportError:
        return None
    if not pdf_path.is_file():
        return None
    if out_path.exists() and not force:
        return f"/media/planner_thumbs/{out_path.name}"

    try:
        doc = fitz.open(pdf_path)
    except Exception:  # noqa: BLE001
        return None
    try:
        if doc.page_count < 1:
            return None
        ranked = _rank_pages(doc)
        pix = None
        for page_index, clip, _score in ranked:
            page = doc.load_page(page_index)
            candidate = page.get_pixmap(
                matrix=fitz.Matrix(_RENDER_SCALE, _RENDER_SCALE),
                clip=clip,
                alpha=False,
            )
            if candidate.width < 120 or candidate.height < 120:
                continue
            colorfulness = _pixmap_colorfulness(candidate.samples)
            # Odrzuć prawie monochromatyczne / czarne separacje.
            if colorfulness < 12.0:
                continue
            pix = candidate
            break
        if pix is None:
            page_index = ranked[0][0] if ranked else _pick_colorful_page(doc)
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        return f"/media/planner_thumbs/{out_path.name}"
    except Exception:  # noqa: BLE001
        return None
    finally:
        doc.close()


def _rank_pages(doc) -> list[tuple[int, object, float]]:
    ranked: list[tuple[int, object, float]] = []
    limit = min(int(doc.page_count), _MAX_PAGES)
    for index in range(limit):
        page = doc.load_page(index)
        score, clip = _score_page(page)
        if clip is None or score <= 0:
            continue
        ranked.append((index, clip, score))
    ranked.sort(key=lambda item: item[2], reverse=True)
    return ranked


def _pick_colorful_page(doc) -> int:
    import fitz

    best_i = 0
    best_score = -1.0
    limit = min(int(doc.page_count), _MAX_PAGES)
    for index in range(limit):
        page = doc.load_page(index)
        probe = page.get_pixmap(matrix=fitz.Matrix(0.15, 0.15), alpha=False)
        score = _pixmap_colorfulness(probe.samples)
        if score > best_score:
            best_score = score
            best_i = index
    return best_i


def _score_page(page) -> tuple[float, object | None]:
    import fitz

    probe = page.get_pixmap(matrix=fitz.Matrix(_PROBE_SCALE, _PROBE_SCALE), alpha=False)
    box = _colorful_band_box(probe.width, probe.height, probe.samples)
    if box is None:
        return 0.0, None
    x0, y0, x1, y1 = box
    bw = x1 - x0 + 1
    bh = y1 - y0 + 1
    area = (bw * bh) / max(probe.width * probe.height, 1)
    color = _region_colorfulness(probe.samples, probe.width, x0, y0, x1, y1)
    dens = _ink_density(probe.samples, probe.width, x0, y0, x1, y1)
    # Kolorowość dominuje — czarne płyty mają wysoką gęstość, ale niski color.
    score = color * (0.45 + 0.55 * min(area, 0.85)) * (0.55 + 0.45 * dens)
    if color < 10:
        score *= 0.15
    if 0.12 <= area <= 0.75:
        score *= 1.25
    elif area > 0.92:
        score *= 0.75

    rect = page.rect
    clip = fitz.Rect(
        rect.x0 + (x0 / probe.width) * rect.width,
        rect.y0 + (y0 / probe.height) * rect.height,
        rect.x0 + ((x1 + 1) / probe.width) * rect.width,
        rect.y0 + ((y1 + 1) / probe.height) * rect.height,
    )
    pad_x = max(2.0, clip.width * 0.03)
    pad_y = max(2.0, clip.height * 0.03)
    clip = fitz.Rect(
        max(rect.x0, clip.x0 - pad_x),
        max(rect.y0, clip.y0 - pad_y),
        min(rect.x1, clip.x1 + pad_x),
        min(rect.y1, clip.y1 + pad_y),
    )
    if clip.width < 20 or clip.height < 20:
        return 0.0, None
    return score, clip


def _is_ink(samples, offset: int) -> bool:
    return (
        samples[offset] < _INK_THR
        or samples[offset + 1] < _INK_THR
        or samples[offset + 2] < _INK_THR
    )


def _is_colorful(samples, offset: int) -> bool:
    r = samples[offset]
    g = samples[offset + 1]
    b = samples[offset + 2]
    if r > 245 and g > 245 and b > 245:
        return False
    # Szarości / czystą czerń pomijamy jako „kolor”.
    mx = max(r, g, b)
    mn = min(r, g, b)
    return (mx - mn) >= 28 and mx >= 40


def _colorful_band_box(width: int, height: int, samples) -> tuple[int, int, int, int] | None:
    row_dens = []
    for y in range(height):
        hit = 0
        base = y * width * 3
        for x in range(width):
            o = base + x * 3
            if _is_colorful(samples, o) or (
                _is_ink(samples, o) and abs(samples[o] - samples[o + 1]) + abs(samples[o + 1] - samples[o + 2]) > 12
            ):
                hit += 1
        row_dens.append(hit / max(width, 1))
    col_dens = []
    for x in range(width):
        hit = 0
        for y in range(height):
            o = (y * width + x) * 3
            if _is_colorful(samples, o) or (
                _is_ink(samples, o) and abs(samples[o] - samples[o + 1]) + abs(samples[o + 1] - samples[o + 2]) > 12
            ):
                hit += 1
        col_dens.append(hit / max(height, 1))

    row_run = _best_run(row_dens, _ROW_COL_THR)
    col_run = _best_run(col_dens, _ROW_COL_THR)
    if row_run is None or col_run is None:
        return _fallback_ink_box(width, height, samples)
    y0, y1 = row_run
    x0, x1 = col_run
    return x0, y0, x1, y1


def _fallback_ink_box(width: int, height: int, samples) -> tuple[int, int, int, int] | None:
    minx, miny, maxx, maxy = width, height, -1, -1
    for y in range(height):
        base = y * width * 3
        for x in range(width):
            if _is_ink(samples, base + x * 3):
                if x < minx:
                    minx = x
                if y < miny:
                    miny = y
                if x > maxx:
                    maxx = x
                if y > maxy:
                    maxy = y
    if maxx < 0:
        return None
    return minx, miny, maxx, maxy


def _best_run(densities: list[float], thr: float) -> tuple[int, int] | None:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, dens in enumerate(densities):
        if dens >= thr:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(densities) - 1))
    if not runs:
        return None

    def run_score(run: tuple[int, int]) -> float:
        a, b = run
        length = b - a + 1
        mean = sum(densities[a : b + 1]) / length
        return length * mean

    return max(runs, key=run_score)


def _ink_density(samples, width: int, x0: int, y0: int, x1: int, y1: int) -> float:
    ink = 0
    total = max((x1 - x0 + 1) * (y1 - y0 + 1), 1)
    for y in range(y0, y1 + 1):
        base = y * width * 3
        for x in range(x0, x1 + 1):
            if _is_ink(samples, base + x * 3):
                ink += 1
    return ink / total


def _region_colorfulness(samples, width: int, x0: int, y0: int, x1: int, y1: int) -> float:
    # Średnia saturacja (max-min kanałów) + wariancja — wysoka dla wzoru, niska dla czerni.
    n = 0
    sat_sum = 0.0
    mean = [0.0, 0.0, 0.0]
    for y in range(y0, y1 + 1):
        base = y * width * 3
        for x in range(x0, x1 + 1):
            o = base + x * 3
            r, g, b = samples[o], samples[o + 1], samples[o + 2]
            if r > 250 and g > 250 and b > 250:
                continue
            sat_sum += max(r, g, b) - min(r, g, b)
            mean[0] += r
            mean[1] += g
            mean[2] += b
            n += 1
    if n < 8:
        return 0.0
    mean = [c / n for c in mean]
    var = 0.0
    for y in range(y0, y1 + 1):
        base = y * width * 3
        for x in range(x0, x1 + 1):
            o = base + x * 3
            r, g, b = samples[o], samples[o + 1], samples[o + 2]
            if r > 250 and g > 250 and b > 250:
                continue
            var += (r - mean[0]) ** 2 + (g - mean[1]) ** 2 + (b - mean[2]) ** 2
    var = (var / n) ** 0.5
    return (sat_sum / n) * 0.65 + var * 0.35


def _pixmap_colorfulness(samples) -> float:
    n = max(len(samples) // 3, 1)
    # Próbkowanie co N-ty piksel dla szybkości
    step = max(1, n // 4000)
    sat_sum = 0.0
    count = 0
    for i in range(0, n, step):
        o = i * 3
        r, g, b = samples[o], samples[o + 1], samples[o + 2]
        if r > 250 and g > 250 and b > 250:
            continue
        sat_sum += max(r, g, b) - min(r, g, b)
        count += 1
    if count < 8:
        return 0.0
    return sat_sum / count

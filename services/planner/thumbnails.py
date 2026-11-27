"""Miniatury PDF: wybór strony ze wzorem (najlepiej z wymiarowaniem) + crop.

Nie zmienia ekstrakcji farb — działa wyłącznie na warstwie podglądu.
Preferuje strony z pełnym wzorem i wymiarami (eyemarki, coty, linie wymiarowe),
a nie czarne / monochromatyczne separacje.
"""

from __future__ import annotations

import re
from pathlib import Path

THUMB_VERSION = "v6"
_MAX_PAGES = 28
_PROBE_SCALE = 0.28
_RENDER_SCALE = 1.35
_INK_THR = 248
_ROW_COL_THR = 0.18
_MIN_COLORFULNESS = 12.0
_MIN_FALLBACK_COLOR = 8.0

_LAYOUT_KEYWORDS = (
    "eyemark",
    "eye mark",
    "eye-mark",
    "wymiar",
    "die cut",
    "die-cut",
    "cutter",
    "scale",
    "skala",
    "trim",
    "bleed",
    "spad",
    "mm",
    " cm",
)


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
    if out_path.exists() and not force and out_path.stat().st_size > 1500:
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
        for page_index, clip, _score, layout in ranked:
            page = doc.load_page(page_index)
            candidate = _render_page(page, clip=clip)
            if candidate is None:
                continue
            if not _pixmap_is_usable(candidate, min_color=_MIN_COLORFULNESS):
                continue
            pix = candidate
            break

        if pix is None:
            # Drugi przebieg: pełna strona najbardziej kolorowa / layoutowa — bez czerni.
            fallback = _best_fullpage_candidate(doc, ranked)
            if fallback is None:
                return None
            pix = fallback

        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        return f"/media/planner_thumbs/{out_path.name}"
    except Exception:  # noqa: BLE001
        return None
    finally:
        doc.close()


def _render_page(page, *, clip=None):
    import fitz

    kwargs = {
        "matrix": fitz.Matrix(_RENDER_SCALE, _RENDER_SCALE),
        "alpha": False,
        "colorspace": fitz.csRGB,
    }
    if clip is not None:
        kwargs["clip"] = clip
    try:
        return page.get_pixmap(**kwargs)
    except Exception:  # noqa: BLE001
        try:
            return page.get_pixmap(
                matrix=fitz.Matrix(_RENDER_SCALE, _RENDER_SCALE),
                clip=clip,
                alpha=False,
            )
        except Exception:  # noqa: BLE001
            return None


def _rank_pages(doc) -> list[tuple[int, object, float, float]]:
    ranked: list[tuple[int, object, float, float]] = []
    limit = min(int(doc.page_count), _MAX_PAGES)
    for index in range(limit):
        page = doc.load_page(index)
        score, clip, layout = _score_page(page)
        if clip is None or score <= 0:
            continue
        ranked.append((index, clip, score, layout))
    ranked.sort(key=lambda item: item[2], reverse=True)
    return ranked


def _best_fullpage_candidate(doc, ranked: list[tuple[int, object, float, float]]):
    """Wybiera pełną stronę — najpierw wg rankingu, potem wg kolorowości; odrzuca czerń."""

    tried: set[int] = set()
    order: list[int] = [item[0] for item in ranked]
    limit = min(int(doc.page_count), _MAX_PAGES)
    for index in range(limit):
        if index not in order:
            order.append(index)

    best_pix = None
    best_score = -1.0
    for page_index in order:
        if page_index in tried:
            continue
        tried.add(page_index)
        page = doc.load_page(page_index)
        candidate = _render_page(page, clip=None)
        if candidate is None:
            continue
        color = _pixmap_colorfulness(candidate.samples)
        lum = _pixmap_mean_luminance(candidate.samples)
        if color < _MIN_FALLBACK_COLOR and lum < 40:
            continue
        if not _pixmap_is_usable(candidate, min_color=_MIN_FALLBACK_COLOR):
            continue
        layout = 0.0
        for item in ranked:
            if item[0] == page_index:
                layout = item[3]
                break
        score = color * 1.4 + layout * 2.5 + max(0.0, lum - 40) * 0.05
        if score > best_score:
            best_score = score
            best_pix = candidate
    return best_pix


def _pick_colorful_page(doc) -> int:
    import fitz

    best_i = 0
    best_score = -1.0
    limit = min(int(doc.page_count), _MAX_PAGES)
    for index in range(limit):
        page = doc.load_page(index)
        probe = page.get_pixmap(matrix=fitz.Matrix(0.15, 0.15), alpha=False)
        color = _pixmap_colorfulness(probe.samples)
        layout = _layout_cue_score(page)
        score = color + layout * 1.8
        if score > best_score:
            best_score = score
            best_i = index
    return best_i


def _score_page(page) -> tuple[float, object | None, float]:
    import fitz

    layout = _layout_cue_score(page)
    probe = page.get_pixmap(matrix=fitz.Matrix(_PROBE_SCALE, _PROBE_SCALE), alpha=False)
    box = _colorful_band_box(probe.width, probe.height, probe.samples)
    rect = page.rect

    # Strony z wymiarowaniem / eyemarkami: pokazuj prawie cały wzór, nie ciasny crop.
    if layout >= 12.0:
        inset_x = max(2.0, rect.width * 0.015)
        inset_y = max(2.0, rect.height * 0.015)
        clip = fitz.Rect(
            rect.x0 + inset_x,
            rect.y0 + inset_y,
            rect.x1 - inset_x,
            rect.y1 - inset_y,
        )
        color = _pixmap_colorfulness(probe.samples)
        dens = _ink_density(probe.samples, probe.width, 0, 0, probe.width - 1, probe.height - 1)
        score = (
            color * (0.55 + 0.45 * dens)
            + layout * 4.2
            + min(probe.width * probe.height, 180_000) / 40_000.0
        )
        if color < 8:
            score *= 0.25
        return score, clip, layout

    if box is None:
        if layout > 0:
            inset_x = max(2.0, rect.width * 0.02)
            inset_y = max(2.0, rect.height * 0.02)
            clip = fitz.Rect(
                rect.x0 + inset_x,
                rect.y0 + inset_y,
                rect.x1 - inset_x,
                rect.y1 - inset_y,
            )
            color = _pixmap_colorfulness(probe.samples)
            return color * 0.4 + layout * 3.0, clip, layout
        return 0.0, None, layout

    x0, y0, x1, y1 = box
    bw = x1 - x0 + 1
    bh = y1 - y0 + 1
    area = (bw * bh) / max(probe.width * probe.height, 1)
    color = _region_colorfulness(probe.samples, probe.width, x0, y0, x1, y1)
    dens = _ink_density(probe.samples, probe.width, x0, y0, x1, y1)
    # Kolorowość dominuje — czarne płyty mają wysoką gęstość, ale niski color.
    score = color * (0.45 + 0.55 * min(area, 0.85)) * (0.55 + 0.45 * dens)
    score += layout * 3.5
    if color < 10:
        score *= 0.15
    if 0.12 <= area <= 0.75:
        score *= 1.25
    elif area > 0.92:
        score *= 0.75

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
        return 0.0, None, layout
    return score, clip, layout


def _layout_cue_score(page) -> float:
    """Ocena „to wygląda jak strona wzoru z wymiarowaniem”, niezależna od numeru strony."""

    score = 0.0
    try:
        text = (page.get_text("text") or "").lower()
    except Exception:  # noqa: BLE001
        text = ""

    for keyword in _LAYOUT_KEYWORDS:
        if keyword in text:
            score += 7.0 if "eye" in keyword else 4.5

    nums = re.findall(r"\b\d{1,4}(?:[.,]\d{1,2})?\b", text)
    # Coty / wymiary to zwykle dużo krótkich liczb (6.5, 27.5, 113…).
    if len(nums) >= 6:
        score += min(22.0, 1.1 * len(nums))

    unit_hits = len(re.findall(r"\b\d+(?:[.,]\d+)?\s*(?:mm|cm)\b", text))
    score += min(12.0, unit_hits * 3.0)

    try:
        drawings = page.get_drawings() or []
    except Exception:  # noqa: BLE001
        drawings = []
    n_draw = len(drawings)
    if n_draw >= 25:
        score += min(18.0, n_draw * 0.08)
    elif n_draw >= 8:
        score += n_draw * 0.05

    # Wiele cienkich linii / prostokątów = typowa siatka wymiarowa / panele saszetki.
    thin_lines = 0
    for drawing in drawings[:400]:
        for item in drawing.get("items") or ():
            kind = item[0] if item else None
            if kind == "l":
                thin_lines += 1
            elif kind == "re":
                thin_lines += 0.5
    if thin_lines >= 20:
        score += min(14.0, thin_lines * 0.08)

    # Obrazy rastrowe na stronie (często podkład wzoru).
    try:
        images = page.get_images(full=True) or []
    except Exception:  # noqa: BLE001
        images = []
    if images:
        score += min(10.0, 3.0 * len(images))

    return score


def _pixmap_is_usable(pix, *, min_color: float) -> bool:
    if pix is None:
        return False
    if pix.width < 120 or pix.height < 120:
        return False
    samples = pix.samples
    color = _pixmap_colorfulness(samples)
    if color < min_color:
        return False
    lum = _pixmap_mean_luminance(samples)
    # Prawie czarna płyta (separacja K / czarny solid) — odrzuć.
    if lum < 32 and color < 28:
        return False
    # Prawie jednolita czerń / szarość bez saturacji.
    if lum < 22:
        return False
    return True


def _pixmap_mean_luminance(samples) -> float:
    n = max(len(samples) // 3, 1)
    step = max(1, n // 3500)
    total = 0.0
    count = 0
    for i in range(0, n, step):
        o = i * 3
        r, g, b = samples[o], samples[o + 1], samples[o + 2]
        total += 0.2126 * r + 0.7152 * g + 0.0722 * b
        count += 1
    return total / max(count, 1)


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

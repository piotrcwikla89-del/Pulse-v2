"""Przygotowanie obrazu strony do OCR. Moduł nie woła Tesseracta."""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Swatch:
    """Prostokąt próbki koloru na obrazie przygotowanym do OCR. Jednostka: piksel."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class PreparedImage:
    """Obraz skali szarości przekazany do Tesseracta oraz oryginał do odczytu numerów."""

    gray: np.ndarray
    color: np.ndarray
    swatches: tuple[Swatch, ...]


def grayscale(image: np.ndarray) -> np.ndarray:
    """RGB, BGR-nie: obraz jest w RGB. Szarość wraca bez zmian."""

    if image.ndim == 2:
        return image
    channels = image.shape[2]
    if channels == 4:
        image = image[:, :, :3]
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def denoise(gray: np.ndarray) -> np.ndarray:
    """Lekkie odszumienie, które nie skleja sąsiednich cyfr siatki."""

    if min(gray.shape[:2]) < 3:
        return gray
    return cv2.medianBlur(gray, 3)


def adaptive_threshold(gray: np.ndarray) -> np.ndarray:
    """Próg adaptacyjny. Wynik ma wyłącznie wartości 0 i 255."""

    block = 31 if min(gray.shape[:2]) >= 31 else 3
    if block % 2 == 0:
        block += 1
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block,
        11,
    )


def skew_angle_degrees(gray: np.ndarray) -> float:
    """Mały kąt przekosu w stopniach. Zero, gdy strona jest prosta albo pomiar jest niepewny."""

    if gray.ndim != 2 or min(gray.shape) < 20:
        return 0.0
    _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    points = cv2.findNonZero(binary)
    if points is None or len(points) < 80:
        return 0.0
    angle = float(cv2.minAreaRect(points)[-1])
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 1.5 or abs(angle) > 8:
        return 0.0
    return angle


def rotate_image(image: np.ndarray, angle: float) -> np.ndarray:
    """Obraca obraz wokół środka. Kąt zerowy zostawia tę samą treść i ten sam kształt."""

    if abs(angle) < 0.05:
        return image
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    border = (255, 255, 255) if image.ndim == 3 else 255
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border,
    )


def legend_rows(gray: np.ndarray) -> tuple[int, int] | None:
    """Pionowy zakres długich linii tabeli w dolnej części strony. ``None``, gdy linii nie ma."""

    if gray.ndim != 2 or gray.shape[0] < 40 or gray.shape[1] < 40:
        return None
    height, width = gray.shape
    start = int(height * 0.45)
    _threshold, inverted = cv2.threshold(gray[start:], 180, 255, cv2.THRESH_BINARY_INV)
    kernel_width = max(40, width // 20)
    lines = cv2.morphologyEx(
        inverted,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1)),
    )
    hits = np.where(lines.sum(axis=1) > kernel_width * 255 * 4)[0]
    clusters: list[list[int]] = []
    for raw in hits:
        absolute = int(raw) + start
        if not clusters or absolute - clusters[-1][-1] > 3:
            clusters.append([absolute])
        else:
            clusters[-1].append(absolute)
    if len(clusters) < 3:
        return None
    first = int(sum(clusters[0]) / len(clusters[0]))
    last = int(sum(clusters[-1]) / len(clusters[-1]))
    margin = max(8, int(height * 0.03))
    return max(0, first - margin), min(height - 1, last + margin)


def mask_swatches(image: np.ndarray, *, dpi: int) -> tuple[np.ndarray, tuple[Swatch, ...]]:
    """Zamalowuje próbki koloru na biało, żeby nie zlewały się z nazwą farby.

    Zwraca skalę szarości i prostokąty próbek odczytane z obrazu kolorowego.
    """

    color = image[:, :, :3] if image.ndim == 3 and image.shape[2] == 4 else image
    gray = grayscale(color)
    hsv = cv2.cvtColor(color, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    saturated = ((saturation > 25) & (value > 40)).astype(np.uint8) * 255
    saturated = cv2.morphologyEx(saturated, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    color_boxes = [_tighten_swatch(box, saturated) for box in _square_boxes(saturated, dpi)]
    dark = (gray < 60).astype(np.uint8) * 255
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    dark_boxes = [_tighten_swatch(box, dark) for box in _square_boxes(dark, dpi)]
    cleaned = gray.copy()
    cleaned[saturation > 40] = 255
    cleaned[dark > 0] = 255
    for swatch in (*color_boxes, *dark_boxes):
        y0 = max(0, swatch.y)
        x0 = max(0, swatch.x)
        y1 = min(cleaned.shape[0], swatch.y + swatch.height)
        x1 = min(cleaned.shape[1], swatch.x + swatch.width)
        cleaned[y0:y1, x0:x1] = 255
    return cleaned, tuple(_dedupe([*color_boxes, *dark_boxes]))


def color_grid_left(gray: np.ndarray, dpi: int) -> int | None:
    """Lewa krawędź prawej siatki farb, w pikselach obrazu legendy."""

    if gray.ndim != 2 or gray.shape[1] < 80:
        return None
    height, width = gray.shape
    _threshold, inverted = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
    kernel_height = max(24, int(36 * dpi / 72))
    if kernel_height >= height:
        return None
    vertical = cv2.morphologyEx(
        inverted,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_height)),
    )
    hits = np.where(vertical.sum(axis=0) > kernel_height * 255 * 3)[0]
    clusters: list[list[int]] = []
    for raw in hits:
        column = int(raw)
        if column < width * 0.48:
            continue
        if not clusters or column - clusters[-1][-1] > 4:
            clusters.append([column])
        else:
            clusters[-1].append(column)
    if not clusters:
        return None
    left = int(sum(clusters[0]) / len(clusters[0]))
    pad = max(4, int(6 * dpi / 72))
    return max(0, left - pad)


def fade_color(image: np.ndarray) -> np.ndarray:
    """Gasi kolorowe plamy, zostawia czarny tekst nagłówka."""

    color = image[:, :, :3] if image.ndim == 3 and image.shape[2] == 4 else image
    hsv = cv2.cvtColor(color, cv2.COLOR_RGB2HSV)
    gray = grayscale(color)
    gray = gray.copy()
    gray[(hsv[:, :, 1] > 40) & (hsv[:, :, 2] > 50)] = 255
    return gray


def prepare_ocr_image(image: np.ndarray, *, dpi: int) -> PreparedImage:
    """Skala szarości, korekta małego przekosu, usunięcie próbek i odszumienie.

    Do Tesseracta idzie odszumiona skala szarości. Twardy próg adaptacyjny jest
    dostępny osobno: na tej legendzie skleja zapis siatki w jeden nieczytelny token.
    """

    angle = skew_angle_degrees(grayscale(image))
    aligned = rotate_image(image, -angle) if angle else image
    gray, swatches = mask_swatches(aligned, dpi=dpi)
    # Mediana ściera cienkie ukośniki zapisu ``lpi / kąt / %``. Zostaje skala szarości.
    return PreparedImage(gray=gray, color=aligned, swatches=swatches)


def _square_boxes(mask: np.ndarray, dpi: int) -> list[Swatch]:
    min_side = max(8, int(7 * dpi / 72))
    max_side = max(min_side + 1, int(24 * dpi / 72))
    contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[Swatch] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width < min_side or height < min_side or width > max_side or height > max_side:
            continue
        ratio = width / height
        if ratio < 0.55 or ratio > 1.9:
            continue
        area = cv2.contourArea(contour)
        if area < 0.45 * width * height:
            continue
        boxes.append(Swatch(int(x), int(y), int(width), int(height)))
    return boxes


def _tighten_swatch(swatch: Swatch, mask: np.ndarray) -> Swatch:
    """Obcina prostokąt do próbki. Nadmiar szerokości sięga nazwy farby i nie może jej zasłonić."""

    region = mask[swatch.y : swatch.y + swatch.height, swatch.x : swatch.x + swatch.width]
    if region.size == 0:
        return swatch
    columns = np.where(region.mean(axis=0) > 0.25)[0]
    if len(columns) < 4:
        return swatch
    left = int(columns[0])
    right = int(columns[-1])
    if right - left + 1 > swatch.height * 1.25:
        right = left + swatch.height
    return Swatch(swatch.x + left, swatch.y, max(4, right - left + 1), swatch.height)


def _dedupe(swatches: list[Swatch]) -> list[Swatch]:
    kept: list[Swatch] = []
    for swatch in swatches:
        center_x = swatch.x + swatch.width / 2
        center_y = swatch.y + swatch.height / 2
        if any(
            abs(center_x - (other.x + other.width / 2)) < 8 and abs(center_y - (other.y + other.height / 2)) < 8
            for other in kept
        ):
            continue
        kept.append(swatch)
    return kept

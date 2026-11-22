"""Wspólna reprezentacja słowa ze współrzędnymi dla tekstu PDF i OCR."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WordBox:
    """Jedno słowo na stronie. Parser tabeli zna tylko tę strukturę.

    Pewność tekstu z warstwy PDF wynosi 100. OCR podaje własną wartość 0–100.
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page_index: int
    confidence: int = 100

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 100:
            raise ValueError("Pewność słowa musi być w zakresie 0–100.")
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("Prostokąt słowa ma odwrócone współrzędne.")

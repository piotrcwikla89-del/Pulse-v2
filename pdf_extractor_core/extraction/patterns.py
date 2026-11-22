"""Wzorce legendy separacji na pierwszej stronie specyfikacji."""

import re

# Esko: ``133.0 / 37.5 / 1.56%``
SCREEN_PATTERN = re.compile(
    r"^(?P<lpi>\d+(?:\.\d+)?)\s*/\s*(?P<angle>\d+(?:\.\d+)?)\s*/\s*(?P<coverage>\d+(?:[.,]\d+)?)\s*%$"
)

# LAC bromide: ``3.16% 126.0 lpi 7.5°``
LAC_SCREEN_PATTERN = re.compile(
    r"^(?P<coverage>\d+(?:[.,]\d+)?)\s*%\s+(?P<lpi>\d+(?:\.\d+)?)\s+lpi\s+"
    r"(?P<angle>\d+(?:[.,]\d+)?)\s*°?$",
    re.IGNORECASE,
)

# Flexmar: ``133lpi/37,5°`` (bez pokrycia w tym tokenie)
FLEXMAR_SCREEN_PATTERN = re.compile(
    r"^\d+(?:[.,]\d+)?\s*lpi\s*/\s*\d+(?:[.,]\d+)?\s*°?$",
    re.IGNORECASE,
)

# dd.mm.yyyy albo yyyy.mm.dd (LAC)
DATE_PATTERN = re.compile(
    r"^(?:\d{2}[./-]\d{2}[./-]\d{4}|\d{4}[./-]\d{2}[./-]\d{2})$"
)
ORDER_NUMBER_PATTERN = re.compile(r"^\d{5,10}$")
EMPTY_STATION_NAMES = frozenset({"-", "–", "—"})

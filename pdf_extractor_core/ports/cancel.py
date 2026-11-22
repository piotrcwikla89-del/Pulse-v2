"""Token anulowania sprawdzany między plikami."""

from threading import Event
from typing import Protocol


class CancelToken(Protocol):
    def is_cancelled(self) -> bool:
        """Zwraca prawdę, gdy odbiorca prosi o zatrzymanie kolejnych plików."""


class ThreadingCancelToken:
    """Token oparty na zdarzeniu wątku. Bezpieczny do ustawienia z interfejsu."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

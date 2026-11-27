"""Globalny lock OCR — tylko jedna partia naraz, żeby nie ubić całego serwisu na Renderze."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

_LOCK_NAME = "pulse_ocr_batch.lock"


def _lock_path() -> Path:
    from services.runtime_env import get_data_dir

    base = get_data_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path("/tmp")
    return base / _LOCK_NAME


@contextmanager
def try_ocr_lock(timeout: float = 0.05):
    """Non-blocking lock. ``acquired`` jest False, gdy inny worker już robi OCR."""

    path = _lock_path()
    fh = None
    acquired = False
    try:
        fh = open(path, "a+", encoding="utf-8")
        if os.name == "nt":
            import msvcrt

            start = time.monotonic()
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if (time.monotonic() - start) >= timeout:
                        break
                    time.sleep(0.02)
        else:
            import fcntl

            start = time.monotonic()
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if (time.monotonic() - start) >= timeout:
                        break
                    time.sleep(0.02)
        if acquired:
            fh.seek(0)
            fh.truncate()
            fh.write(f"pid={os.getpid()} at={time.time():.0f}\n")
            fh.flush()
        yield acquired
    finally:
        if fh is not None:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                fh.close()
            except OSError:
                pass

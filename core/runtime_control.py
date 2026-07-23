"""Thread-local cooperative cancellation for parser and OCR page loops."""

from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Callable, Iterator


_STATE = threading.local()


@contextmanager
def cancellation_scope(check: Callable[[], bool] | None) -> Iterator[None]:
    previous = getattr(_STATE, "cancel_check", None)
    _STATE.cancel_check = check
    try:
        yield
    finally:
        _STATE.cancel_check = previous


def raise_if_cancelled() -> None:
    check = getattr(_STATE, "cancel_check", None)
    if check and check():
        raise InterruptedError("任务已取消。")

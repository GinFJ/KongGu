"""Small Tkinter background task runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import threading
import traceback
from typing import TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class BackgroundTaskError:
    """Exception details captured from a background task."""

    exception: Exception
    detail: str


def run_background_task(
    *,
    owner,
    task: Callable[[], T],
    on_success: Callable[[T], None],
    on_error: Callable[[BackgroundTaskError], None],
    logger: logging.Logger | None = None,
    error_message: str = "后台任务失败",
) -> threading.Thread:
    """Run a callable in a daemon thread and marshal callbacks onto Tk's event loop."""

    def worker() -> None:
        try:
            result = task()
        except Exception as exc:
            detail = traceback.format_exc()
            if logger is not None:
                logger.exception(error_message)
            owner.after(0, lambda exc=exc, detail=detail: on_error(BackgroundTaskError(exc, detail)))
            return
        owner.after(0, lambda result=result: on_success(result))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread

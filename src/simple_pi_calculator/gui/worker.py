"""Compute worker running the engine in a ``QThread`` (DESIGN.md §5.4).

The worker holds a deep copy of the engine inputs taken on the GUI thread, never touches
widgets, throttles progress signals to ≥ 50 ms apart and reports cancellation separately from
failures.
"""

from __future__ import annotations

import copy
import threading
import time
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal, Slot

ComputeFn = Callable[[Any, Callable[[float, str], None], Callable[[], bool]],
                     tuple[list[Any], list[Any]]]

PROGRESS_INTERVAL_S = 0.050


class ComputeWorker(QObject):
    """QObject living in a worker thread (§5.4).

    ``compute_fn(inputs, progress, cancel)`` must return ``(results, issues)``; it is normally
    :meth:`EngineBridge.compute`. ``cancelled_exceptions`` lists the exception types that mean
    "cancelled" (``core.engine.CancelledError``).
    """

    progress = Signal(float, str)
    finished = Signal(object)          # tuple(results, issues)
    failed = Signal(str)               # traceback text
    cancelled = Signal()

    def __init__(self, inputs: Any, compute_fn: ComputeFn,
                 cancelled_exceptions: tuple[type[BaseException], ...] = ()):
        super().__init__()
        try:
            self._inputs = copy.deepcopy(inputs)
        except Exception:  # noqa: BLE001 - un-copyable inputs: use as-is (already a snapshot)
            self._inputs = inputs
        self._compute_fn = compute_fn
        self._cancelled_exceptions = tuple(cancelled_exceptions)
        self._cancel = threading.Event()
        self._last_emit = 0.0
        self.elapsed_s = 0.0

    def request_cancel(self) -> None:
        self._cancel.set()

    def is_cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def _on_progress(self, fraction: float, text: str = "") -> None:
        now = time.monotonic()
        if now - self._last_emit >= PROGRESS_INTERVAL_S or fraction >= 1.0:
            self._last_emit = now
            try:
                self.progress.emit(float(fraction), str(text))
            except RuntimeError:  # receiver deleted during shutdown
                pass

    @Slot()
    def run(self) -> None:
        start = time.monotonic()
        try:
            results, issues = self._compute_fn(self._inputs, self._on_progress,
                                               self._cancel.is_set)
        except BaseException as exc:  # noqa: BLE001 - everything is reported, never raised
            self.elapsed_s = time.monotonic() - start
            if self._cancelled_exceptions and isinstance(exc, self._cancelled_exceptions):
                self.cancelled.emit()
            elif self._cancel.is_set() and type(exc).__name__ == "CancelledError":
                self.cancelled.emit()
            else:
                self.failed.emit("".join(traceback.format_exception(exc)))
            return
        self.elapsed_s = time.monotonic() - start
        if self._cancel.is_set() and not results:
            self.cancelled.emit()
            return
        self.finished.emit((list(results), list(issues)))


__all__ = ["ComputeWorker"]

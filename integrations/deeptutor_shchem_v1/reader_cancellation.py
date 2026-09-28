"""Cooperative cancellation scoped to a desktop read, never process-global.

HTTP readers and file writes run unchanged outside an explicit read scope.
Nested reader pools copy the scope so file-validation checkpoints also stop
their running work; shutting down an executor alone only cancels queued work.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from threading import Event, RLock
from typing import Any, Iterator

_stop_events: ContextVar[tuple[Any, ...]] = ContextVar("shchem_read_stop", default=())


class TaskCancellationEvent(Event):
    """Linearize a user stop with a task's final durable publication."""
    def __init__(self):
        super().__init__()
        self._publication_lock = RLock()
        self._published = False

    def request_cancel(self):
        with self._publication_lock:
            if self._published:
                return False
            super().set()
            return True

    def set(self):
        self.request_cancel()


@contextmanager
def publication_guard():
    """Only final publications use this guard, never intermediate cache writes.

    If cancellation wins, no write starts. If publication wins, a late Stop
    cannot falsely label the committed task cancelled. Failure leaves it open.
    """
    signals = sorted({x for x in _stop_events.get() if isinstance(x, TaskCancellationEvent)}, key=id)
    for signal in signals:
        signal._publication_lock.acquire()
    try:
        check_read_cancelled()
        yield
        for signal in signals:
            signal._published = True
    finally:
        for signal in reversed(signals):
            signal._publication_lock.release()


class ReadCancelled(RuntimeError):
    code = "desktop_read_cancelled"
    message_zh = "本次任务已取消，未发布新的结果。"

    def __init__(self, message: str | None = None) -> None:
        if message:
            self.message_zh = message
        super().__init__(self.message_zh)


def check_read_cancelled() -> None:
    if cancellation_requested():
        raise ReadCancelled()


def cancellation_requested() -> bool:
    signals = _stop_events.get()
    # A durable final publication already won the race. A later facade-wide
    # shutdown must not relabel the committed task cancelled on scope exit.
    if any(isinstance(signal, TaskCancellationEvent) and signal._published for signal in signals):
        return False
    return any(signal.is_set() if hasattr(signal, "is_set") else signal()
               for signal in signals)


@contextmanager
def read_cancel_scope(event: Any, *, check_boundaries=True) -> Iterator[None]:
    # A facade shutdown scope must never mask its caller's per-task stop signal.
    signals = _stop_events.get()
    token = _stop_events.set(signals + ((event,) if event is not None and event not in signals else ()))
    try:
        if check_boundaries:
            check_read_cancelled()
        yield
        if check_boundaries:
            check_read_cancelled()
    finally:
        _stop_events.reset(token)


class ReaderThreadPoolExecutor(ThreadPoolExecutor):
    """Each submission gets its own context, including nested reader pools."""

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        check_read_cancelled()
        context = copy_context()

        def run() -> Any:
            check_read_cancelled()
            result = fn(*args, **kwargs)
            check_read_cancelled()
            return result

        return super().submit(context.run, run)

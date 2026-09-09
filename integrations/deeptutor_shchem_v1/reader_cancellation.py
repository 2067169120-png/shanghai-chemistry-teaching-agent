"""Cooperative cancellation scoped to a desktop read, never process-global.

HTTP readers and file writes run unchanged outside an explicit read scope.
Nested reader pools copy the scope so file-validation checkpoints also stop
their running work; shutting down an executor alone only cancels queued work.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from threading import Event
from typing import Any, Iterator

_stop_event: ContextVar[Event | None] = ContextVar("shchem_read_stop", default=None)


class ReadCancelled(RuntimeError):
    code = "desktop_read_cancelled"
    message_zh = "工作台正在关闭，已停止读取本地资料。"

    def __init__(self) -> None:
        super().__init__(self.message_zh)


def check_read_cancelled() -> None:
    event = _stop_event.get()
    if event is not None and event.is_set():
        raise ReadCancelled()


@contextmanager
def read_cancel_scope(event: Event) -> Iterator[None]:
    token = _stop_event.set(event)
    try:
        check_read_cancelled()
        yield
        check_read_cancelled()
    finally:
        _stop_event.reset(token)


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

from __future__ import annotations

"""Thread-safe Qt bridge for local reader and draft operations."""

import threading
import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


def _safe_failure_message(error: BaseException) -> str:
    message = getattr(error, "message_zh", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return "任务未完成，请检查本地资料或设置后重试。"


def _safe_emit(signal: Any, *args: Any) -> None:
    """Emit a worker signal without writing a Qt teardown error to stderr.

    A desktop window can be closed while a cooperative worker is finishing.
    In that small interval Qt may already have deleted the signal carrier or
    one of its queued receivers.  Treat that as normal shutdown rather than
    allowing ``Signal source has been deleted`` to escape from a QRunnable.
    """

    try:
        signal.emit(*args)
    except (RuntimeError, TypeError):
        # The GUI is already going away (or a receiver was deleted).  There is
        # no useful user-facing action left, and importantly no console noise
        # should leak from a windowed build.
        return


class _WorkerSignals(QObject):
    started = Signal(str, str)
    progress = Signal(str, str, object)
    succeeded = Signal(str, str, object)
    failed = Signal(str, str, str)
    cancelled = Signal(str, str)
    finished = Signal(str)


class _FunctionWorker(QRunnable):
    def __init__(
        self,
        task_id: str,
        label: str,
        operation: Callable[..., Any],
        cancel_event: threading.Event,
        *,
        progress_aware: bool = False,
    ) -> None:
        super().__init__()
        self.task_id = task_id
        self.label = label
        self.operation = operation
        self.cancel_event = cancel_event
        self.progress_aware = progress_aware
        # Keep the QObject signal carrier alive until the queued signals have
        # reached the GUI thread.  QThreadPool's eager auto-delete can destroy
        # a QRunnable immediately after run(), dropping those queued updates.
        # DesktopTaskBridge removes the runnable from its registry on the
        # final ``finished`` signal, so this does not accumulate workers.
        self.signals = _WorkerSignals()
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        _safe_emit(self.signals.started, self.task_id, self.label)
        if self.cancel_event.is_set():
            _safe_emit(self.signals.cancelled, self.task_id, self.label)
            _safe_emit(self.signals.finished, self.task_id)
            return
        try:
            if self.progress_aware:
                result = self.operation(
                    lambda payload: _safe_emit(
                        self.signals.progress,
                        self.task_id,
                        self.label,
                        payload,
                    ),
                    self.cancel_event.is_set,
                )
            else:
                result = self.operation()
        except Exception as exc:  # UI receives only a concise, safe message
            _safe_emit(
                self.signals.failed,
                self.task_id,
                self.label,
                _safe_failure_message(exc),
            )
        else:
            if self.cancel_event.is_set():
                _safe_emit(self.signals.cancelled, self.task_id, self.label)
            else:
                _safe_emit(self.signals.succeeded, self.task_id, self.label, result)
        finally:
            _safe_emit(self.signals.finished, self.task_id)


class DesktopTaskBridge(QObject):
    task_started = Signal(str, str)
    task_progress = Signal(str, str, object)
    task_succeeded = Signal(str, str, object)
    task_failed = Signal(str, str, str)
    task_cancelled = Signal(str, str)
    task_finished = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(4, self._pool.maxThreadCount())))
        self._workers: dict[str, _FunctionWorker] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._shutting_down = False

    def _ensure_open(self) -> None:
        if self._shutting_down:
            raise RuntimeError("任务桥正在关闭")

    def submit(
        self,
        label: str,
        operation: Callable[[], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
    ) -> str:
        self._ensure_open()
        task_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        worker = _FunctionWorker(task_id, label, operation, cancel_event)
        worker.signals.started.connect(self.task_started)
        worker.signals.progress.connect(self.task_progress)
        worker.signals.succeeded.connect(self.task_succeeded)
        worker.signals.failed.connect(self.task_failed)
        worker.signals.cancelled.connect(self.task_cancelled)
        worker.signals.finished.connect(self._forget)
        worker.signals.finished.connect(self.task_finished)
        if on_success is not None:
            worker.signals.succeeded.connect(
                lambda _task_id, _label, result: on_success(result)
            )
        if on_failure is not None:
            worker.signals.failed.connect(
                lambda _task_id, _label, message: on_failure(message)
            )
        self._workers[task_id] = worker
        self._cancel_events[task_id] = cancel_event
        self._pool.start(worker)
        return task_id

    def submit_progress(
        self,
        label: str,
        operation: Callable[[Callable[[Any], None], Callable[[], bool]], Any],
        *,
        on_progress: Callable[[Any], None] | None = None,
        on_success: Callable[[Any], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
    ) -> str:
        """Run a cancellable operation that reports thread-safe progress."""

        self._ensure_open()
        task_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        worker = _FunctionWorker(
            task_id,
            label,
            operation,
            cancel_event,
            progress_aware=True,
        )
        worker.signals.started.connect(self.task_started)
        worker.signals.progress.connect(self.task_progress)
        worker.signals.succeeded.connect(self.task_succeeded)
        worker.signals.failed.connect(self.task_failed)
        worker.signals.cancelled.connect(self.task_cancelled)
        worker.signals.finished.connect(self._forget)
        worker.signals.finished.connect(self.task_finished)
        if on_progress is not None:
            worker.signals.progress.connect(
                lambda _task_id, _label, value: on_progress(value)
            )
        if on_success is not None:
            worker.signals.succeeded.connect(
                lambda _task_id, _label, result: on_success(result)
            )
        if on_failure is not None:
            worker.signals.failed.connect(
                lambda _task_id, _label, message: on_failure(message)
            )
        self._workers[task_id] = worker
        self._cancel_events[task_id] = cancel_event
        self._pool.start(worker)
        return task_id

    def cancel(self, task_id: str) -> None:
        event = self._cancel_events.get(task_id)
        if event is not None:
            event.set()

    def cancel_all(self) -> None:
        for event in self._cancel_events.values():
            event.set()
        self._pool.clear()

    def shutdown(self, milliseconds: int = 5000) -> bool:
        """Cancel in-flight work and detach GUI callbacks before teardown.

        ``QMainWindow.closeEvent`` calls this instead of merely clearing the
        queue.  The bridge remains alive until the bounded wait completes;
        late worker emissions are harmless because ``_safe_emit`` absorbs Qt
        teardown races and every worker signal is disconnected on timeout.
        """

        self._shutting_down = True
        self.cancel_all()
        done = self._pool.waitForDone(max(0, int(milliseconds)))
        if not done:
            for worker in tuple(self._workers.values()):
                # QObject.disconnect() enters PySide's overloaded signature
                # resolver, which scans imported modules during cold shutdown.
                # Disconnect the actual signal instances instead.
                for name in ("started", "progress", "succeeded", "failed", "cancelled", "finished"):
                    try:
                        getattr(worker.signals, name).disconnect()
                    except (RuntimeError, TypeError):
                        pass
        else:
            self._workers.clear()
            self._cancel_events.clear()
        return done

    @Slot(str)
    def _forget(self, task_id: str) -> None:
        self._workers.pop(task_id, None)
        self._cancel_events.pop(task_id, None)

    def wait_for_done(self, milliseconds: int = 1500) -> bool:
        return self._pool.waitForDone(milliseconds)


__all__ = ["DesktopTaskBridge"]

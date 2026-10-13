from __future__ import annotations

from threading import Event, get_ident

import pytest
from PySide6.QtTest import QTest

from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge


@pytest.fixture
def app():
    value = create_application([])
    value.processEvents()
    return value


def drain(app):
    for _ in range(5):
        app.processEvents()
        QTest.qWait(10)


def submit(bridge, kind, operation, **callbacks):
    if kind == "plain":
        return bridge.submit("读取原题", operation, **callbacks)
    return bridge.submit_progress("读取原题", lambda _progress, _cancelled: operation(), **callbacks)


@pytest.mark.parametrize("kind", ["plain", "progress"])
def test_already_queued_success_cannot_start_another_reader_after_shutdown(app, monkeypatch, kind):
    bridge = DesktopTaskBridge()
    called, errors = [], []
    monkeypatch.setattr("sys.excepthook", lambda *error: errors.append(error))

    def completed(value):
        called.append(value)
        bridge.submit("读取题图", lambda: b"original image")

    submit(bridge, kind, lambda: "complete original text", on_success=completed)
    assert bridge.wait_for_done(2000)
    # No Qt events are dispatched until the real shutdown has finished.
    assert bridge.shutdown(2000)
    drain(app)
    assert called == [] and errors == []
    assert not bridge._workers and not bridge._cancel_events


@pytest.mark.parametrize("kind", ["plain", "progress"])
def test_queued_failure_does_not_edit_a_closed_window(app, kind):
    bridge = DesktopTaskBridge()
    delivered = []

    def fail():
        raise ValueError("private source text must not leak")

    submit(bridge, kind, fail, on_failure=delivered.append)
    assert bridge.wait_for_done(2000) and bridge.shutdown(2000)
    drain(app)
    assert delivered == []


def test_queued_progress_does_not_edit_a_closed_window(app):
    bridge = DesktopTaskBridge()
    delivered = []

    def operation(progress, _cancelled):
        progress({"read_blocks": 4})
        return "ready"

    bridge.submit_progress("读取原页", operation, on_progress=delivered.append, on_success=delivered.append)
    assert bridge.wait_for_done(2000) and bridge.shutdown(2000)
    drain(app)
    assert delivered == []


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_queued_public_gui_signals_are_silent_after_shutdown(app, outcome):
    bridge = DesktopTaskBridge()
    delivered = []
    for name in ("task_started", "task_progress", "task_succeeded", "task_failed", "task_cancelled", "task_cancelled_detail", "task_finished"):
        getattr(bridge, name).connect(lambda *args, name=name: delivered.append((name, args)))
    started, release = Event(), Event()

    def operation(progress, _cancelled):
        started.set()
        assert release.wait(2)
        progress({"read_blocks": 4})
        if outcome == "failure":
            raise ValueError("source unavailable")
        return "source read"

    identity = bridge.submit_progress("读取原页", operation)
    assert started.wait(2)
    if outcome == "cancelled":
        assert bridge.cancel(identity)
    release.set()
    assert bridge.wait_for_done(2000) and bridge.shutdown(2000)
    drain(app)
    assert delivered == []


@pytest.mark.parametrize("kind", ["plain", "progress"])
def test_open_window_still_gets_result_on_gui_thread_and_can_start_followup(app, kind):
    bridge = DesktopTaskBridge()
    gui = get_ident()
    delivered = []

    def completed(value):
        delivered.append((value, get_ident()))
        bridge.submit("读取题图", lambda: "image ready", on_success=lambda value: delivered.append((value, get_ident())))

    submit(bridge, kind, lambda: "text ready", on_success=completed)
    assert bridge.wait_for_done(2000)
    drain(app)
    assert bridge.wait_for_done(2000)
    drain(app)
    assert delivered == [("text ready", gui), ("image ready", gui)]
    assert bridge.shutdown(2000)


def test_work_already_committed_survives_without_late_ui_write(app, tmp_path):
    bridge = DesktopTaskBridge()
    saved, late = tmp_path / "saved.json", tmp_path / "late-ui.json"

    def save():
        saved.write_text('{"source_revision":"verified"}', encoding="utf-8")
        return str(saved)

    bridge.submit("保存草稿", save, on_success=lambda _value: late.write_text("closed UI", encoding="utf-8"))
    assert bridge.wait_for_done(2000) and bridge.shutdown(2000)
    drain(app)
    assert saved.read_text(encoding="utf-8") == '{"source_revision":"verified"}'
    assert not late.exists()


def test_timed_out_worker_cannot_publish_after_later_completion(app, monkeypatch):
    bridge = DesktopTaskBridge()
    started, release = Event(), Event()
    delivered, errors = [], []
    monkeypatch.setattr("sys.excepthook", lambda *error: errors.append(error))

    def operation():
        started.set()
        assert release.wait(2)
        return "done"

    bridge.submit("读取来源", operation, on_success=delivered.append, on_failure=delivered.append)
    assert started.wait(2)
    assert bridge.shutdown(0) is False
    release.set()
    assert bridge.wait_for_done(2000)
    drain(app)
    assert delivered == [] and errors == []

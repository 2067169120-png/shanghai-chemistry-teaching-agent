"""Selection protocol tests use an owned synthetic session, never Office."""
from copy import deepcopy
import threading

import pytest

from integrations.deeptutor_shchem_v1 import desktop_native_word_selection as selection
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_native_word_mapping as mapping

    path = tmp_path / "original.docx"
    data = b"synthetic bytes; pure XML mapper is separately tested"
    path.write_bytes(data)
    plan = {"objects": [{}, {}], "target_index": 1}
    monkeypatch.setattr(mapping, "build_source_plan", lambda *args: deepcopy(plan))
    monkeypatch.setattr(mapping, "verify_document", lambda p, xml: {"verified": xml == "document"})

    def verify_range(p, document, one, before, through, kind, native_type):
        assert (p, document, one, before, through, kind, native_type) == (plan, "document", "range", "before", "through", "inline", 3)
        return {"verified": True}

    monkeypatch.setattr(mapping, "verify_range", verify_range)
    event = threading.Event()
    instances = []
    native = {"kind": "inline", "index": 1, "type": 3, "story": 1, "start": 20, "end": 21}
    opened = {"read_only": True, "content_start": 0, "content_end": 100,
              # Deliberately shuffled collection order: actual ranges sort it.
              "records": [deepcopy(native), {**native, "index": 2, "start": 10, "end": 11}]}

    class Session:
        on_wait = None
        result_override = {}

        def __init__(self, directory, request, cancelled):
            self.request, self.cancelled = request, cancelled
            self.commands = []
            self.closed = False
            instances.append(self)

        def wait(self, name):
            if self.on_wait:
                self.on_wait(name)
            if name == "opened":
                return deepcopy(opened)
            if name == "inspected":
                return deepcopy(native)
            if name == "published":
                return {"status": "published", "visible": True}
            assert name == "selected"
            return {**native, "status": "selected", "selection_verified": True,
                    "source_unchanged": True, "read_only": True, **self.result_override}

        def send(self, name, payload):
            self.commands.append((name, payload))

        def xml(self, name):
            return name.removeprefix("selected-")

        def close(self, on_exit):
            self.closed = True
            return False

    def run(**kwargs):
        return selection.select_original(path, data, "synthetic locator", cancelled=event, session_factory=Session, **kwargs)

    return run, path, event, instances, Session, opened, mapping


def test_only_verified_actual_range_is_selected_in_original(protocol):
    run, path, _, instances, _, _, _ = protocol
    before = path.read_bytes()
    result = run()
    assert result["status"] == "selected"
    session = instances[0]
    assert session.request["path"] == str(path.resolve())
    assert session.request["visible"] is True
    assert session.commands == [("inspect", {"kind": "inline", "index": 1}), ("select", {"action": "select"}), ("publish", {"action": "publish"})]
    assert session.closed and path.read_bytes() == before


@pytest.mark.parametrize("moment", ["before_start", "opened", "inspected", "revalidate"])
def test_cancellation_never_commits_a_pending_request(protocol, moment):
    run, _, event, instances, session_type, _, _ = protocol
    if moment == "before_start":
        event.set()
    else:
        session_type.on_wait = staticmethod(lambda name: event.set() if name == moment else None)
    revalidate = event.set if moment == "revalidate" else None
    with pytest.raises(PreparationSourceError, match="取消"):
        run(revalidate=revalidate)
    assert all(instance.closed for instance in instances)
    assert not any(name == "select" for instance in instances for name, _ in instance.commands)


def test_source_and_question_revalidated_immediately_before_select(protocol):
    run, path, _, instances, _, _, _ = protocol
    with pytest.raises(PreparationSourceError, match="变化"):
        run(revalidate=lambda: path.write_bytes(b"changed externally"))
    assert instances[0].commands == [("inspect", {"kind": "inline", "index": 1})]
    assert instances[0].closed


def test_failed_range_evidence_closes_owned_session_without_select(protocol, monkeypatch):
    run, _, _, instances, _, _, mapping = protocol
    def reject(*args):
        raise PreparationSourceError("重复对象无法唯一匹配")
    monkeypatch.setattr(mapping, "verify_range", reject)
    with pytest.raises(PreparationSourceError, match="唯一"):
        run()
    assert instances[0].closed
    assert [name for name, _ in instances[0].commands] == ["inspect"]


@pytest.mark.parametrize("mutation", ["not_readonly", "overlap", "wrong_story", "ole", "missing", "bad_bound"])
def test_unproven_native_inventory_never_inspects_or_selects(protocol, mutation):
    run, _, _, instances, _, opened, _ = protocol
    if mutation == "not_readonly": opened["read_only"] = False
    elif mutation == "overlap": opened["records"][1].update(start=19, end=22)
    elif mutation == "wrong_story": opened["records"][0]["story"] = 6
    elif mutation == "ole": opened["records"][0]["type"] = 1
    elif mutation == "missing": opened["records"].pop()
    elif mutation == "bad_bound": opened["records"][0]["end"] = 101
    with pytest.raises(PreparationSourceError): run()
    assert instances[0].closed and instances[0].commands == []


@pytest.mark.parametrize("field,value", [("selection_verified", False), ("source_unchanged", False), ("read_only", False), ("start", 10)])
def test_selected_reply_must_prove_expected_range_and_unchanged_source(protocol, field, value):
    run, _, _, instances, session_type, _, _ = protocol
    session_type.result_override = {field: value}
    with pytest.raises(PreparationSourceError, match="回读"):
        run()
    assert instances[0].closed


def test_concurrent_selection_does_not_start_another_office_session(protocol):
    run, _, _, instances, _, _, _ = protocol
    assert selection._SESSION_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(PreparationSourceError, match="已有"):
            run()
        assert not instances
    finally:
        selection._SESSION_LOCK.release()


def test_cancel_after_hidden_selection_never_publishes(protocol):
    run, _, event, instances, session_type, _, _ = protocol
    session_type.on_wait = staticmethod(lambda name: event.set() if name == "selected" else None)
    with pytest.raises(PreparationSourceError, match="取消"):
        run()
    assert all(name != "publish" for name, _ in instances[0].commands)
    assert instances[0].closed


def test_question_change_after_selection_never_publishes(protocol):
    run, _, _, instances, _, _, _ = protocol
    checks = []
    def revalidate():
        checks.append(True)
        if len(checks) == 2:
            raise PreparationSourceError("题目范围已变化")
    with pytest.raises(PreparationSourceError, match="范围"):
        run(revalidate=revalidate)
    assert all(name != "publish" for name, _ in instances[0].commands)


def test_hidden_qa_does_not_publish(protocol):
    run, _, _, instances, _, _, _ = protocol
    run(visible=False)
    assert all(name != "publish" for name, _ in instances[0].commands)


def test_deferred_cleanup_keeps_directory_and_lock_until_helper_exits(protocol):
    run, _, _, instances, session_type, _, _ = protocol
    callbacks = []
    def deferred_close(self, on_exit):
        callbacks.append(on_exit)
        return True
    session_type.close = deferred_close
    run(visible=False)
    assert selection._SESSION_LOCK.locked()
    with pytest.raises(PreparationSourceError, match="已有"):
        run()
    assert len(instances) == 1
    callbacks.pop()()
    assert not selection._SESSION_LOCK.locked()


def test_timeout_keeps_cancel_marker_and_reaps_without_termination(tmp_path, monkeypatch):
    import subprocess
    callbacks, threads = [], []
    class Process:
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("synthetic helper", timeout)
            return 0
        def terminate(self):
            pytest.fail("must not abandon COM finally")
    class Thread:
        def __init__(self, *, target, **kwargs):
            self.target = target
            threads.append(self)
        def start(self):
            pass
    monkeypatch.setattr(selection.threading, "Thread", Thread)
    session = selection._WordSession.__new__(selection._WordSession)
    session.directory, session.process = tmp_path, Process()
    assert session.close(lambda: callbacks.append("exited")) is True
    assert (tmp_path / "cancel").exists() and not callbacks
    assert (tmp_path / "abandoned").exists()
    threads[0].target()
    assert callbacks == ["exited"]


def test_post_selection_content_change_is_rejected_before_window_handoff(protocol, monkeypatch):
    run, _, _, instances, _, _, mapping = protocol
    proofs = []
    def verify(*args):
        proofs.append(True)
        if len(proofs) == 2:
            raise PreparationSourceError("回读内容已变化")
        return {"verified": True}
    monkeypatch.setattr(mapping, "verify_range", verify)
    with pytest.raises(PreparationSourceError, match="回读内容"):
        run()
    assert all(name != "publish" for name, _ in instances[0].commands)
    assert instances[0].closed

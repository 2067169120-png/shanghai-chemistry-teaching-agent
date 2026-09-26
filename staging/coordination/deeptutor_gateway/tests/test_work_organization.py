"""A07: reversible organization of real saved identities, with no artifact writes."""
from copy import deepcopy
from pathlib import Path
import threading

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import PreparationDraftError
from integrations.deeptutor_shchem_v1.desktop_work_organization import WorkOrganizationError, revision
from test_desktop_preparation_drafts import _payload, _record
from test_phase_a_core import saved_state

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def facade(tmp_path):
    value = build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=tmp_path))
    yield value
    value.shutdown()


def save_draft(facade, identity="draft-test", topic="合成原课题"):
    facade.state_store.save_draft(identity, _record(_payload(topic=topic)))
    return identity


def row(facade, identity, kind="draft", shelf="current"):
    values = facade.search_preparation_work(kind=kind, shelf=shelf, limit=100)["items"]
    return next(value for value in values if value["id"] == identity)


def change(facade, record, action, **kwargs):
    return facade.organize_preparation_work(record["kind"], record["id"], action,
        expected_source=record["source_revision"], expected_organization=record["organization_revision"], **kwargs)


def terminal_task(facade, status="cancelled"):
    manager = facade._preparation_manager_instance()
    task = manager.prepare(_payload(topic="合成原任务"), "LOCAL", "REV")
    manager.cancel(task["task_id"])
    if status != "cancelled":
        record = manager._read_task(task["task_id"])
        record["status"] = status
        manager._write_task(record)
    return task["task_id"]


def files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_name_and_shelf_do_not_mutate_content_or_original_files(facade, tmp_path):
    identity = save_draft(facade)
    recovery = tmp_path / "recovery-test.json"
    recovery.write_text("未保存的表单不属于作品整理", encoding="utf-8")
    original = tmp_path / "source-test.png"
    original.write_bytes(b"synthetic-source-marker")
    facade.state_store.add_to_basket({"key": "original-question", "title": "原题"})
    before = facade.state_store.snapshot()
    change(facade, row(facade, identity), "rename", title="  周三第二课时  ")
    renamed = row(facade, identity)
    assert renamed["title"] == "周三第二课时" and renamed["original_title"] == "合成原课题"
    assert facade.search_preparation_work(query="第二课时")["total"] == 1
    assert facade.search_preparation_work(query="原课题")["total"] == 1
    assert facade.preparation_draft_option(identity)["title"] == "周三第二课时"
    loaded = facade.resolve_preparation_work(renamed)
    payload = facade.load_preparation_draft(identity, loaded["value"]["revision"])["payload"]
    assert payload["topic"] == "合成原课题"
    change(facade, renamed, "archive")
    assert facade.search_preparation_work()["total"] == 0
    assert facade.search_preparation_drafts()["total"] == 0
    archived = row(facade, identity, shelf="archived")
    assert facade.resolve_preparation_work(archived)["title"] == "周三第二课时"
    change(facade, archived, "trash")
    with pytest.raises(PreparationDraftError, match="回收站"):
        facade.preparation_draft_option(identity)
    trashed = row(facade, identity, shelf="trash")
    change(facade, trashed, "restore")
    assert row(facade, identity, shelf="archived")["title"] == "周三第二课时"
    change(facade, row(facade, identity, shelf="archived"), "unarchive")
    after = facade.state_store.snapshot()
    assert before["drafts"] == after["drafts"] and before["basket"] == after["basket"]
    assert recovery.read_text(encoding="utf-8") == "未保存的表单不属于作品整理"
    assert original.read_bytes() == b"synthetic-source-marker"


def test_pagination_filters_all_501_and_name_search_before_slicing(facade):
    saved_state(facade.paths.state_root, 501)
    old = facade.search_preparation_work(query="课题-0000")["items"][0]
    change(facade, old, "rename", title="下学期保留课")
    renamed = facade.search_preparation_work(query="保留")["items"][0]
    change(facade, renamed, "archive")
    found = facade.search_preparation_work(query="保留", shelf="archived")
    assert found["total"] == 1 and found["items"][0]["id"] == "prep-0000"
    assert found["shelf_counts"] == {"current": 0, "archived": 1, "trash": 0}
    assert facade.search_preparation_work(query="课题-0000", shelf="archived")["total"] == 1
    page = facade.search_preparation_work(offset=1000)
    assert page["total"] == 500 and page["offset"] == 475


@pytest.mark.parametrize("status", ["cancelled", "failed", "completed"])
def test_terminal_tasks_can_be_organized_without_rewriting_task_or_outputs(facade, status):
    identity = terminal_task(facade, status)
    output = facade.paths.task_root / "original-output.pptx"
    output.write_bytes(b"synthetic-original-output")
    before = files(facade.paths.task_root)
    change(facade, row(facade, identity, "task"), "rename", title="保留任务")
    for action, shelf in (("archive", "current"), ("trash", "archived"), ("restore", "trash"), ("unarchive", "archived")):
        change(facade, row(facade, identity, "task", shelf), action)
    assert row(facade, identity, "task")["title"] == "保留任务"
    assert facade.search_preparation_work(query="原任务", kind="task")["total"] == 1
    assert files(facade.paths.task_root) == before


@pytest.mark.parametrize("status", ["prepared", "running", "cancel_requested"])
@pytest.mark.parametrize("action", ["rename", "archive", "trash"])
def test_active_tasks_are_visible_and_actions_cannot_cancel_them(facade, status, action):
    identity = terminal_task(facade, status)
    selected = row(facade, identity, "task")
    before = files(facade.paths.task_root)
    assert not selected["manageable"]
    with pytest.raises(WorkOrganizationError, match="尚未结束"):
        change(facade, selected, action, title="不会写入")
    assert files(facade.paths.task_root) == before


def test_external_task_retry_is_visible_and_stale_action_rejected(facade):
    identity = terminal_task(facade)
    selected = row(facade, identity, "task")
    change(facade, selected, "trash")
    old = row(facade, identity, "task", "trash")
    facade._preparation_manager_instance().retry(identity)
    current = row(facade, identity, "task")
    assert not current["manageable"] and current["organization_note"]
    assert facade.search_preparation_work(shelf="trash")["total"] == 0
    with pytest.raises(WorkOrganizationError, match="已经变化"):
        change(facade, old, "restore")


def test_stale_source_and_stale_organization_leave_state_untouched(facade):
    identity = save_draft(facade)
    old = row(facade, identity)
    change(facade, old, "rename", title="第一次名称")
    before = facade.state_store.path.read_bytes()
    with pytest.raises(WorkOrganizationError, match="已经变化"):
        change(facade, old, "trash")
    assert facade.state_store.path.read_bytes() == before
    with pytest.raises(WorkOrganizationError, match="已变化"):
        facade.resolve_preparation_work(old)
    latest = row(facade, identity)
    facade.state_store.save_draft(identity, _record(_payload(topic="外部修改了正文")))
    before = facade.state_store.path.read_bytes()
    with pytest.raises(WorkOrganizationError, match="已经变化"):
        change(facade, latest, "archive")
    assert facade.state_store.path.read_bytes() == before


def test_missing_source_never_creates_or_changes_another_record(facade):
    identity = save_draft(facade)
    selected = row(facade, identity)
    save_draft(facade, "other")
    facade.state_store._update(lambda value: value["drafts"].pop(identity))
    before = facade.state_store.path.read_bytes()
    with pytest.raises(WorkOrganizationError, match="不存在"):
        change(facade, selected, "trash")
    assert facade.state_store.path.read_bytes() == before


@pytest.mark.parametrize("title", ["", "  ", "x" * 161, "bad\ntitle", "bad\x00title"])
def test_invalid_name_never_changes_state(facade, title):
    identity = save_draft(facade)
    before = facade.state_store.path.read_bytes()
    with pytest.raises(WorkOrganizationError):
        change(facade, row(facade, identity), "rename", title=title)
    assert facade.state_store.path.read_bytes() == before


def test_atomic_failure_preserves_previous_state(facade, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_state
    identity = save_draft(facade)
    selected = row(facade, identity)
    before = facade.state_store.path.read_bytes()
    def broken(*args):
        raise OSError("simulated write failure")
    monkeypatch.setattr(desktop_state.os, "replace", broken)
    with pytest.raises(WorkOrganizationError):
        change(facade, selected, "trash")
    assert facade.state_store.path.read_bytes() == before


def test_reload_metadata_type_qualified_ids_and_reads_do_not_write(facade):
    identity = terminal_task(facade)
    save_draft(facade, identity)
    change(facade, row(facade, identity, "draft"), "trash")
    assert row(facade, identity, "task")["shelf"] == "current"
    second = build_default_facade(facade.paths)
    try:
        before = facade.state_store.path.read_bytes()
        assert row(second, identity, "draft", "trash")["shelf"] == "trash"
        assert row(second, identity, "task")["shelf"] == "current"
        assert second.state_store.path.read_bytes() == before
    finally:
        second.shutdown()


def test_corrupt_metadata_never_turns_trashed_records_into_current(facade):
    save_draft(facade)
    facade.state_store._update(lambda value: value.update(work_organization="corrupt"))
    before = facade.state_store.path.read_bytes()
    result = facade.search_preparation_work(kind="draft")
    assert result["warnings"] and not result["items"]
    assert facade.state_store.path.read_bytes() == before


def test_retry_and_organization_share_task_lock(facade):
    identity = terminal_task(facade)
    manager = facade._preparation_manager_instance()
    selected = row(facade, identity, "task")
    finished = threading.Event()
    errors = []
    def do_change():
        try:
            change(facade, selected, "archive")
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()
    with manager._lock:
        thread = threading.Thread(target=do_change)
        thread.start()
        assert not finished.wait(.05)
        manager.retry(identity)
    thread.join(3)
    assert finished.is_set() and len(errors) == 1
    assert row(facade, identity, "task")["shelf"] == "current"

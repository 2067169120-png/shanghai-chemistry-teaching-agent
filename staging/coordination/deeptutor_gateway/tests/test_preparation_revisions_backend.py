from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from test_desktop_preparation_facade import (
    _facade,
    _payload,
    _ProviderStore,
    _Transport,
)

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationManager,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    NativePreparationRenderer,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_revision import (
    apply_preparation_text_edits,
    preparation_text_fields,
)


@pytest.fixture
def desktop_paths(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    return DesktopPaths.from_workspace(
        workspace,
        state_root=tmp_path / "personal-state",
    )


def _completed(facade, store, transport):
    task = facade.prepare_preparation(_payload(), "teacher-text", store.revision)
    completed = facade.generate_preparation(
        task.task_id,
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )
    assert completed.status == "completed"
    assert transport.calls == 1
    return completed


def _candidate_bytes(facade, task_id: str) -> bytes:
    return facade.preparation_artifact_path(task_id, "candidate_json").read_bytes()


def test_revision_exports_child_without_mutating_parent_or_calling_provider(
    desktop_paths,
):
    store, transport = _ProviderStore(), _Transport()
    facade = _facade(desktop_paths, store, transport)
    parent = _completed(facade, store, transport)
    assert parent.source_kind is None
    parent_candidate = _candidate_bytes(facade, parent.task_id)
    source = facade.preparation_revision_source(parent.task_id)

    field = next(
        field
        for field in preparation_text_fields(source["candidate"])
        if field["path"] == ["slides", 0, "purpose"]
    )
    child = facade.revise_preparation(
        parent.task_id,
        source["source_revision"],
        [{"path": field["path"], "text": "修订后的课堂作用"}],
        note="教师本地订正",
    )

    assert child.status == "completed"
    assert child.source_kind == "teacher_revision"
    assert child.task_id != parent.task_id
    assert transport.calls == 1
    assert store.borrow_calls == [("teacher-text", store.revision)]
    assert _candidate_bytes(facade, parent.task_id) == parent_candidate
    assert _candidate_bytes(facade, child.task_id) != parent_candidate
    assert facade.preparation_artifact_path(parent.task_id, "pptx").is_file()
    assert facade.preparation_artifact_path(child.task_id, "pptx").is_file()
    assert facade.preparation_artifact_path(
        parent.task_id, "pptx"
    ) != facade.preparation_artifact_path(child.task_id, "pptx")

    child_record = facade._preparation_manager.get_task(child.task_id)
    assert child_record["source_kind"] == "teacher_revision"
    assert child_record["model_invoked"] is False
    assert child_record["parent_task_id"] == parent.task_id
    assert child_record["parent_candidate_sha256"] == source["source_revision"]
    assert child_record["revision_note"] == "教师本地订正"


def test_revision_rejects_stale_and_noop_without_writes(desktop_paths):
    store, transport = _ProviderStore(), _Transport()
    facade = _facade(desktop_paths, store, transport)
    parent = _completed(facade, store, transport)
    source = facade.preparation_revision_source(parent.task_id)
    before = _candidate_bytes(facade, parent.task_id)

    field = next(
        field
        for field in preparation_text_fields(source["candidate"])
        if field["path"] == ["slides", 0, "purpose"]
    )
    with pytest.raises(DesktopFacadeError) as stale:
        facade.revise_preparation(
            parent.task_id,
            "0" * 64,
            [{"path": field["path"], "text": "新的文字"}],
        )
    assert stale.value.code == "preparation_revision_stale"

    with pytest.raises(DesktopFacadeError) as unchanged:
        facade.revise_preparation(
            parent.task_id,
            source["source_revision"],
            [{"path": field["path"], "text": field["text"]}],
        )
    assert unchanged.value.code == "preparation_revision_unchanged"
    assert _candidate_bytes(facade, parent.task_id) == before
    assert transport.calls == 1


def test_structure_only_revision_creates_worksheet_without_provider_access(
    desktop_paths,
):
    from test_preparation_structure import notes_operation

    store, transport = _ProviderStore(), _Transport()
    facade = _facade(desktop_paths, store, transport)
    parent = _completed(facade, store, transport)
    original = _candidate_bytes(facade, parent.task_id)
    source = facade.preparation_revision_source(parent.task_id)
    child = facade.revise_preparation(
        parent.task_id,
        source["source_revision"],
        [],
        structure_edits=[notes_operation()],
        note="本地补充学生留白",
    )
    assert child.status == "completed" and child.source_kind == "teacher_revision"
    assert transport.calls == 1
    assert store.borrow_calls == [("teacher-text", store.revision)]
    assert _candidate_bytes(facade, parent.task_id) == original
    revised = facade.preparation_revision_source(child.task_id)["candidate"]
    assert (
        revised["activities"][0]["worksheet"]["sections"][-1]["heading"] == "判断依据"
    )
    assert any("worksheet" in artifact for artifact in child.artifact_ids)


def test_revision_child_reopens_after_restart_and_retry_stays_local(
    desktop_paths,
):
    class _FailOnceRenderer:
        def __init__(self):
            self.delegate = NativePreparationRenderer()
            self.calls = 0
            self.fail_next = False

        def render(self, *args, **kwargs):
            self.calls += 1
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("local synthetic renderer failure")
            return self.delegate.render(*args, **kwargs)

    store, transport = _ProviderStore(), _Transport()
    manager = DesktopPreparationManager(
        desktop_paths.task_root / "preparation-v1", _FailOnceRenderer()
    )
    facade = _facade(desktop_paths, store, transport, manager=manager)
    parent = _completed(facade, store, transport)
    source = facade.preparation_revision_source(parent.task_id)
    field = next(
        field
        for field in preparation_text_fields(source["candidate"])
        if field["path"] == ["slides", 0, "purpose"]
    )
    manager.renderer.fail_next = True
    failed = facade.revise_preparation(
        parent.task_id,
        source["source_revision"],
        [{"path": field["path"], "text": "本地失败后仍可重试"}],
    )
    assert failed.status == "failed"
    child_id = failed.task_id
    assert transport.calls == 1

    retried = facade.retry_preparation(child_id)
    assert retried.status == "prepared"
    recovered = facade.generate_preparation(
        child_id,
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )
    assert recovered.status == "completed"
    assert transport.calls == 1
    assert store.borrow_calls == [("teacher-text", store.revision)]

    restarted = _facade(desktop_paths, store, _Transport())
    assert restarted.get_preparation(child_id).status == "completed"
    reopened = restarted.preparation_revision_source(child_id)
    assert reopened["task_id"] == child_id
    assert len(reopened["source_revision"]) == 64


def test_local_revision_with_missing_seed_fails_without_provider_call(desktop_paths):
    store, transport = _ProviderStore(), _Transport()
    facade = _facade(desktop_paths, store, transport)
    parent = _completed(facade, store, transport)
    source = facade.preparation_revision_source(parent.task_id)
    field = next(
        field
        for field in preparation_text_fields(source["candidate"])
        if field["path"] == ["slides", 0, "purpose"]
    )
    candidate = apply_preparation_text_edits(
        source["candidate"],
        [{"path": field["path"], "text": "种子丢失保护"}],
        facade._preparation_manager.revision_source(parent.task_id)["payload"],
    )
    child_record = facade._preparation_manager.create_revision(
        parent.task_id,
        source["source_revision"],
        candidate,
    )
    seed = (
        desktop_paths.task_root
        / "preparation-v1"
        / "seeds"
        / f"{child_record['task_id']}.candidate.json"
    )
    seed.unlink()
    failed = facade.generate_preparation(
        child_record["task_id"],
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )
    assert failed.status == "failed"
    assert transport.calls == 1
    assert store.borrow_calls == [("teacher-text", store.revision)]
    assert failed.retryable is True


def test_revision_source_hash_and_candidate_use_one_candidate_read(
    desktop_paths, monkeypatch
):
    store, transport = _ProviderStore(), _Transport()
    facade = _facade(desktop_paths, store, transport)
    parent = _completed(facade, store, transport)
    candidate_path = facade.preparation_artifact_path(parent.task_id, "candidate_json")
    original_read_bytes = Path.read_bytes
    candidate_reads: list[bytes] = []

    def read_bytes(path: Path) -> bytes:
        data = original_read_bytes(path)
        if path.resolve() == candidate_path.resolve():
            candidate_reads.append(data)
        return data

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    source = facade._preparation_manager.revision_source(parent.task_id)

    assert len(candidate_reads) == 1
    assert source["source_revision"] == hashlib.sha256(candidate_reads[0]).hexdigest()

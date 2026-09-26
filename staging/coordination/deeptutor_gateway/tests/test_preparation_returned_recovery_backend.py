from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from test_desktop_preparation import (
    FileRenderer,
    RecordingProvider,
    _payload,
    _raw_candidate,
)
from test_desktop_preparation_facade import (
    _facade,
    _ProviderStore,
    _Transport,
)

from integrations.deeptutor_shchem_v1 import desktop_preparation as preparation_module
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationError,
    DesktopPreparationManager,
    normalize_preparation_candidate,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_recovery import (
    apply_returned_comparison_edits,
)
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ProbeTransportResponse,
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


def _invalid_comparison_candidate() -> dict[str, Any]:
    candidate = deepcopy(_raw_candidate())
    candidate["slides"][1]["visual"] = {
        "kind": "comparison",
        "comparison": {
            "dimension_label": "比较维度",
            "columns": ["甲", "乙", "丙"],
            "rows": [
                {"label": "第一行", "values": ["甲1", "乙1"]},
                {"label": "第二行", "values": ["甲2", "乙2"]},
            ],
        },
        "steps": [],
    }
    return candidate


def _repaired_comparison() -> dict[str, Any]:
    return {
        "dimension_label": "比较维度",
        "columns": ["甲", "乙", "丙"],
        "rows": [
            {"label": "第一行", "values": ["甲1", "乙1", "丙1"]},
            {"label": "第二行", "values": ["甲2", "乙2", "丙2"]},
        ],
    }


def test_normalization_failure_persists_safe_raw_response_without_freezing_seed(
    tmp_path,
):
    candidate = _invalid_comparison_candidate()
    provider = RecordingProvider(candidate=candidate)
    manager = DesktopPreparationManager(tmp_path / "preparation", FileRenderer())
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-RAW-1")

    failed = manager.run(task["task_id"], provider, lambda _value: None, lambda: False)

    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "preparation_candidate_visual_invalid"
    assert failed["candidate_available"] is False
    assert failed["returned_candidate_available"] is True
    assert provider.calls and len(provider.calls) == 1

    source = manager.returned_source(task["task_id"])
    assert source["candidate"] == candidate
    assert len(source["source_revision"]) == 64
    assert "比较表" in source["error_message"]
    assert manager.get_task(task["task_id"])["returned_candidate_available"] is True

    returned_files = list(manager.returned_root.glob("*.candidate.json"))
    assert len(returned_files) == 1
    assert returned_files[0].read_bytes()


def test_returned_repair_creates_local_child_and_keeps_parent_response_immutable(
    tmp_path,
):
    candidate = _invalid_comparison_candidate()
    provider = RecordingProvider(candidate=candidate)
    manager = DesktopPreparationManager(tmp_path / "preparation", FileRenderer())
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-RAW-2")
    failed = manager.run(task["task_id"], provider, lambda _value: None, lambda: False)
    source = manager.returned_source(task["task_id"])
    original = deepcopy(source["candidate"])

    repaired_raw = apply_returned_comparison_edits(
        source["candidate"],
        [{"slide_number": 2, "comparison": _repaired_comparison()}],
    )
    canonical = normalize_preparation_candidate(repaired_raw, source["payload"])
    child = manager.create_returned_revision(
        task["task_id"],
        source["source_revision"],
        canonical,
        note="教师核对比较表列数",
    )

    assert child["status"] == "prepared"
    assert child["candidate_available"] is True
    assert child["returned_candidate_available"] is False
    child_record = manager._read_task(child["task_id"])
    assert child_record["source_kind"] == "teacher_revision"
    assert child_record["revision_kind"] == "returned_candidate_repair"
    assert child_record["model_invoked"] is False
    assert child_record["parent_task_id"] == task["task_id"]
    assert child_record["parent_candidate_sha256"] == source["source_revision"]

    def provider_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("returned-response repair must render locally")

    completed = manager.run(
        child["task_id"], provider_must_not_run, lambda _value: None, lambda: False
    )
    assert completed["status"] == "completed"
    assert len(provider.calls) == 1
    assert manager.returned_source(task["task_id"])["candidate"] == original
    assert failed["task_id"] == task["task_id"]


def test_returned_source_hash_drift_and_arbitrary_repair_paths_are_rejected(tmp_path):
    candidate = _invalid_comparison_candidate()
    manager = DesktopPreparationManager(tmp_path / "preparation", FileRenderer())
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-RAW-3")
    manager.run(
        task["task_id"],
        RecordingProvider(candidate=candidate),
        lambda _value: None,
        lambda: False,
    )
    source = manager.returned_source(task["task_id"])
    returned_file = next(manager.returned_root.glob("*.candidate.json"))
    returned_file.write_text(returned_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(Exception) as drift:
        manager.returned_source(task["task_id"])
    assert getattr(drift.value, "code", None) == "preparation_returned_candidate_drift"

    with pytest.raises(Exception) as invalid:
        apply_returned_comparison_edits(
            source["candidate"],
            [
                {
                    "path": ["slides", 1, "visual", "comparison"],
                    "text": "不能使用任意路径",
                }
            ],
        )
    assert getattr(invalid.value, "code", None) == "preparation_returned_repair_invalid"


def test_sensitive_returned_response_is_not_saved(tmp_path):
    candidate = _invalid_comparison_candidate()
    candidate["api_key"] = "must-not-persist"
    manager = DesktopPreparationManager(tmp_path / "preparation", FileRenderer())
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-RAW-SENSITIVE")

    failed = manager.run(
        task["task_id"],
        RecordingProvider(candidate=candidate),
        lambda _value: None,
        lambda: False,
    )

    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "preparation_sensitive_field_forbidden"
    assert failed["returned_candidate_available"] is False
    assert list(manager.returned_root.glob("*.candidate.json")) == []


def test_oversize_returned_response_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(preparation_module, "_MAX_RETURNED_CANDIDATE_BYTES", 128)
    candidate = _invalid_comparison_candidate()
    candidate["slides"][0]["teacher_notes"] = "x" * 2_000
    manager = DesktopPreparationManager(tmp_path / "preparation", FileRenderer())
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-RAW-LARGE")

    failed = manager.run(
        task["task_id"],
        RecordingProvider(candidate=candidate),
        lambda _value: None,
        lambda: False,
    )

    assert failed["status"] == "failed"
    assert failed["returned_candidate_available"] is False
    assert list(manager.returned_root.glob("*.candidate.json")) == []


def test_restart_reopens_returned_response_by_hash(tmp_path):
    candidate = _invalid_comparison_candidate()
    root = tmp_path / "preparation"
    first = DesktopPreparationManager(root, FileRenderer())
    task = first.prepare(_payload(), "PROFILE-TEXT", "REV-RAW-RESTART")
    first.run(
        task["task_id"],
        RecordingProvider(candidate=candidate),
        lambda _value: None,
        lambda: False,
    )
    source = first.returned_source(task["task_id"])

    restarted = DesktopPreparationManager(root, FileRenderer())
    assert restarted.get_task(task["task_id"])["returned_candidate_available"] is True
    reopened = restarted.returned_source(task["task_id"])
    assert reopened["source_revision"] == source["source_revision"]
    assert reopened["candidate"] == source["candidate"]


def test_stale_returned_source_does_not_create_child(tmp_path):
    candidate = _invalid_comparison_candidate()
    manager = DesktopPreparationManager(tmp_path / "preparation", FileRenderer())
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-RAW-STALE")
    manager.run(
        task["task_id"],
        RecordingProvider(candidate=candidate),
        lambda _value: None,
        lambda: False,
    )
    source = manager.returned_source(task["task_id"])
    repaired = apply_returned_comparison_edits(
        source["candidate"],
        [{"slide_number": 2, "comparison": _repaired_comparison()}],
    )
    canonical = normalize_preparation_candidate(repaired, source["payload"])

    with pytest.raises(DesktopPreparationError) as stale:
        manager.create_returned_revision(
            task["task_id"],
            "0" * 64,
            canonical,
        )
    assert stale.value.code == "preparation_revision_stale"
    assert [row["task_id"] for row in manager.list_tasks()] == [task["task_id"]]


def test_repaired_child_retry_stays_local_after_renderer_failure(tmp_path):
    candidate = _invalid_comparison_candidate()
    renderer = FileRenderer(fail_calls=1)
    manager = DesktopPreparationManager(tmp_path / "preparation", renderer)
    provider = RecordingProvider(candidate=candidate)
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-RAW-RETRY")
    manager.run(task["task_id"], provider, lambda _value: None, lambda: False)
    source = manager.returned_source(task["task_id"])
    repaired = apply_returned_comparison_edits(
        source["candidate"],
        [{"slide_number": 2, "comparison": _repaired_comparison()}],
    )
    canonical = normalize_preparation_candidate(repaired, source["payload"])
    child = manager.create_returned_revision(
        task["task_id"], source["source_revision"], canonical
    )

    def provider_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a repaired child retry must stay local")

    failed_child = manager.run(
        child["task_id"], provider_must_not_run, lambda _value: None, lambda: False
    )
    assert failed_child["status"] == "failed"
    assert failed_child["candidate_available"] is True
    manager.retry(child["task_id"])
    completed = manager.run(
        child["task_id"], provider_must_not_run, lambda _value: None, lambda: False
    )
    assert completed["status"] == "completed"
    assert len(renderer.calls) == 2  # two local child renders
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "edits",
    [
        [{"slide_number": 0, "comparison": _repaired_comparison()}],
        [{"slide_number": 4, "comparison": _repaired_comparison()}],
        [
            {"slide_number": 2, "comparison": _repaired_comparison()},
            {"slide_number": 2, "comparison": _repaired_comparison()},
        ],
        [{"slide_number": 1, "comparison": _repaired_comparison()}],
    ],
)
def test_returned_repair_rejects_invalid_page_or_non_comparison_edit(edits):
    candidate = _invalid_comparison_candidate()
    with pytest.raises(DesktopPreparationError) as invalid:
        apply_returned_comparison_edits(candidate, edits)
    assert invalid.value.code == "preparation_returned_repair_invalid"


class _InvalidComparisonTransport(_Transport):
    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        response = super().send(
            request,
            cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic,
        )
        body = json.loads(response.body.decode("utf-8"))
        candidate = json.loads(body["output_text"])
        candidate["slides"][0]["visual"] = {
            "kind": "comparison",
            "comparison": {
                "dimension_label": "比较维度",
                "columns": ["甲", "乙", "丙"],
                "rows": [
                    {"label": "第一行", "values": ["甲1", "乙1"]},
                    {"label": "第二行", "values": ["甲2", "乙2"]},
                ],
            },
            "steps": [],
        }
        body["output_text"] = json.dumps(candidate, ensure_ascii=False)
        return ProbeTransportResponse(
            http_status=response.http_status,
            content_type=response.content_type,
            content_encoding=response.content_encoding,
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            latency_ms=response.latency_ms,
            model_invoked=response.model_invoked,
        )


def test_facade_repair_returned_preparation_never_borrows_provider_again(
    desktop_paths,
):
    store, transport = _ProviderStore(), _InvalidComparisonTransport()
    facade = _facade(desktop_paths, store, transport)
    prepared = facade.prepare_preparation(_payload(), "teacher-text", store.revision)
    failed = facade.generate_preparation(
        prepared.task_id,
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )
    assert failed.status == "failed"
    assert failed.returned_candidate_available is True
    source = facade.preparation_returned_source(prepared.task_id)

    repaired = facade.repair_returned_preparation(
        prepared.task_id,
        source["source_revision"],
        [{"slide_number": 1, "comparison": _repaired_comparison()}],
        note="教师明确修订整张比较表",
    )

    assert repaired.status == "completed"
    assert repaired.source_kind == "teacher_revision"
    assert repaired.returned_candidate_available is False
    assert transport.calls == 1
    assert store.borrow_calls == [("teacher-text", store.revision)]
    parent = facade.get_preparation(prepared.task_id)
    assert parent.status == "failed"
    assert parent.returned_candidate_available is True
    assert facade.preparation_returned_source(prepared.task_id)["candidate"] == source[
        "candidate"
    ]


def test_facade_summary_defaults_returned_candidate_flag_to_false():
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade

    value = {
        "task_id": "PREP-" + "a" * 32,
        "status": "failed",
        "topic": "复习",
        "artifact_mode": "linked_bundle",
        "created_at": "2026-09-09T00:00:00Z",
        "updated_at": "2026-09-09T00:01:00Z",
        "progress": {"percent": 100, "message_zh": "失败"},
        "artifacts": [],
        "slide_count": 0,
        "error": {"retryable": True},
        "candidate_only": True,
        "teacher_review_required": True,
        "publication_allowed": False,
    }
    summary = DesktopWorkbenchFacade._preparation_summary(value)
    assert summary.returned_candidate_available is False

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationManager,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    NativePreparationRenderer,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ProbeTransportResponse,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
)


def _payload(*, output_kind: str = "joint") -> dict[str, Any]:
    return {
        "output_kind": output_kind,
        "topic": "化学平衡复习",
        "audience": "高二 3 班",
        "lesson_route": "复习",
        "lesson_timing": "1课时×40分钟",
        "objective": "能用证据判断条件改变后的平衡移动方向",
        "materials": "教材章节、教师提供的完整主题题和课堂说明",
        "advanced": {
            "learning_and_experiment": "先独立判断，再同伴说明证据",
            "template_and_delivery": "投影文字简洁，保留教师讲稿",
            "homework_and_strategy": "安排一道同结构迁移题",
        },
    }


def _candidate() -> dict[str, Any]:
    return {
        "title": "化学平衡：证据、判断与迁移",
        "objectives": [{"statement": "能依据浓度或压强变化判断平衡移动方向。"}],
        "activities": [
            {
                "title": "证据判断",
                "objective_numbers": [1],
                "minutes": 40,
                "teacher_action": "呈现教师核验后的情境并追问判断依据。",
                "student_action": "独立判断、说明证据并相互修正。",
                "materials": ["教师提供的完整主题题"],
            }
        ],
        "assessments": [
            {
                "title": "出口条",
                "objective_numbers": [1],
                "activity_numbers": [1],
                "evidence_of_learning": "学生写出方向判断和对应证据。",
                "success_criteria": ["方向正确", "证据与改变条件对应"],
            }
        ],
        "slides": [
            {
                "title": "化学平衡复习",
                "purpose": "完成证据判断和迁移",
                "objective_numbers": [1],
                "activity_numbers": [1],
                "assessment_numbers": [1],
                "minutes": 40,
                "content": ["先判断改变条件", "再用证据说明移动方向"],
                "teacher_notes": "具体题面、数据与结论在上课前由教师复核。",
            }
        ],
        "lesson_stages": [
            {
                "title": "判断与说明",
                "objective_numbers": [1],
                "activity_numbers": [1],
                "assessment_numbers": [1],
                "minutes": 40,
                "teacher_action": "组织独立作答和证据追问。",
                "student_action": "作答、交流并修正理由。",
                "materials": ["教师提供的完整主题题"],
                "assessment": "用出口条检查方向和依据。",
            }
        ],
        "homework": {
            "title": "课后迁移",
            "tasks": [
                {
                    "instruction": "完成一道同结构迁移题并写出判断依据。",
                    "objective_numbers": [1],
                }
            ],
            "estimated_minutes": 12,
        },
        "uncertainties": [
            {
                "field": "question_data",
                "description": "课堂例题的具体数据需由教师核对。",
                "teacher_action": "授课前核对题面、答案、单位和适用范围。",
            }
        ],
    }


class _ProviderStore:
    def __init__(
        self,
        *,
        revision: str = "REV-PREP-1",
        capabilities: tuple[str, ...] = ("text", "structured_output"),
        allowed_data_classes: tuple[str, ...] = (
            "synthetic_only",
            "question_text_redacted",
        ),
    ) -> None:
        self.revision = revision
        self.capabilities = capabilities
        self.allowed_data_classes = allowed_data_classes
        self.borrow_calls: list[tuple[str, str]] = []
        self.borrow_active = False

    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "profile_id": "teacher-text",
                "display_name": "教师模型服务",
                "base_url": "https://models.example/v1",
                "model_id": "teacher-model",
                "api_style": "responses",
                "revision": self.revision,
                "credential_state": "configured",
                "effective_capabilities": list(self.capabilities),
                "allowed_data_classes": list(self.allowed_data_classes),
            }
        ]

    @contextmanager
    def borrow_invocation_context(
        self, profile_id: str, *, expected_revision: str
    ) -> Iterator[ModelProviderProbeContext]:
        assert profile_id == "teacher-text"
        assert expected_revision == self.revision
        self.borrow_calls.append((profile_id, expected_revision))
        self.borrow_active = True
        try:
            yield ModelProviderProbeContext(
                profile_id=profile_id,
                provider_id="openai_compatible",
                model_id="teacher-model",
                base_url_policy="openai_compatible_public_https_v1",
                base_url="https://models.example/v1",
                revision=self.revision,
                api_key="fixture-secret-never-persisted",
                provider_kind="openai_compatible",
                api_style="responses",
                local_endpoint_policy="deny",
            )
        finally:
            self.borrow_active = False


class _Transport:
    def __init__(self) -> None:
        self.calls = 0
        self.request_bodies: list[bytes] = []

    def send(
        self,
        request: Any,
        *,
        cancel_event: Any,
        deadline_monotonic: float,
    ) -> ProbeTransportResponse:
        assert not cancel_event.is_set()
        assert deadline_monotonic > 0
        self.calls += 1
        self.request_bodies.append(request.body)
        body = {
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "output_text": json.dumps(_candidate(), ensure_ascii=False),
            "usage": {"input_tokens": 20, "output_tokens": 50, "total_tokens": 70},
        }
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            latency_ms=1,
            model_invoked=True,
        )


class _CancelledTransport(_Transport):
    def send(self, *_args: Any, **_kwargs: Any) -> ProbeTransportResponse:
        self.calls += 1
        error = RuntimeError("private transport cancellation")
        error.code = "cancelled"  # type: ignore[attr-defined]
        raise error


@pytest.fixture
def desktop_paths(tmp_path: Path) -> DesktopPaths:
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    return DesktopPaths.from_workspace(
        workspace,
        state_root=tmp_path / "personal-state",
    )


def _facade(
    paths: DesktopPaths,
    store: _ProviderStore,
    transport: _Transport,
    *,
    manager: DesktopPreparationManager | None = None,
) -> DesktopWorkbenchFacade:
    unused = object()
    manager = manager or DesktopPreparationManager(
        paths.task_root / "preparation-v1", NativePreparationRenderer()
    )
    return DesktopWorkbenchFacade(
        paths,
        theme_reader=unused,
        supplemental_reader=unused,
        curriculum_reader=unused,
        search_reader=unused,
        provider_store=store,
        state_store=DesktopStateStore(paths.state_root),
        paper_export_jobs=unused,
        preparation_manager=manager,
        preparation_transport=transport,
    )


def test_facade_image_import_survives_draft_roundtrip_without_provider(
    desktop_paths, tmp_path
):
    from PIL import Image

    store, transport = _ProviderStore(), _Transport()
    facade = _facade(desktop_paths, store, transport)
    source = tmp_path / "teacher-original.png"
    Image.new("RGB", (320, 200), "white").save(source)
    original = source.read_bytes()
    asset = facade.import_preparation_image(
        str(source), "测试图", "教师自备", "说明概念关系"
    )
    receipt = facade.create_preparation_draft({**_payload(), "image_assets": [asset]})
    option = next(
        row
        for row in facade.preparation_draft_options()
        if row["draft_id"] == receipt.draft_id
    )
    loaded = facade.load_preparation_draft(receipt.draft_id, option["revision"])
    assert loaded["payload"]["image_assets"] == [asset]
    assert facade._preparation_manager.image_store.load(asset) == original
    assert source.read_bytes() == original
    assert transport.calls == 0 and store.borrow_calls == []


def test_facade_runs_confirmed_joint_generation_and_reopens_artifacts(
    desktop_paths: DesktopPaths,
) -> None:
    store = _ProviderStore()
    transport = _Transport()
    facade = _facade(desktop_paths, store, transport)
    availability = facade.preparation_availability()
    assert availability.provider_ready is True
    assert availability.renderer_ready is True
    assert facade.preparation_profiles()[0].provider_name == "教师模型服务"

    prepared = facade.prepare_preparation(_payload(), "teacher-text", "REV-PREP-1")
    assert prepared.status == "prepared"
    assert prepared.output_kind == "joint"
    assert prepared.candidate_only is True
    assert prepared.publication_allowed is False

    progress: list[dict[str, Any]] = []
    completed = facade.generate_preparation(
        prepared.task_id,
        teacher_confirmed=True,
        progress_callback=progress.append,
        should_cancel=lambda: False,
    )
    assert completed.status == "completed"
    assert completed.progress_percent == 100
    assert completed.slide_count == 1
    assert {
        "pptx",
        "lesson_plan_docx",
        "preview_montage",
    }.issubset(completed.artifact_ids)
    assert completed.teacher_review_required is True
    assert completed.publication_allowed is False
    assert transport.calls == 1
    assert store.borrow_calls == [("teacher-text", "REV-PREP-1")]
    assert progress[-1]["percent"] == 100
    assert all(
        b"fixture-secret-never-persisted" not in body
        for body in transport.request_bodies
    )

    for artifact_id in ("pptx", "lesson_plan_docx", "preview_montage"):
        path = facade.preparation_artifact_path(completed.task_id, artifact_id)
        assert path.is_absolute()
        assert path.is_file()
        assert path.stat().st_size > 0

    all_state = b"".join(
        path.read_bytes()
        for path in desktop_paths.state_root.rglob("*")
        if path.is_file()
    )
    assert b"fixture-secret-never-persisted" not in all_state

    report = facade.preparation_classroom_review(completed.task_id)
    assert report["status"] == "teacher_review_required"
    assert len(report["pages"]) == 1
    qa = json.loads(
        facade.preparation_artifact_path(completed.task_id, "qa_report").read_text(
            "utf-8"
        )
    )
    assert qa["classroom_review"] == {
        key: value for key, value in report.items() if key != "source_reference"
    }
    assert report["source_reference"] == {
        "topic": _payload()["topic"],
        "materials": _payload()["materials"],
    }
    assert qa["scope"] == "machine_layout_check_only"
    assert transport.calls == 1
    assert store.borrow_calls == [("teacher-text", "REV-PREP-1")]

    # Reopening uses the manager's registered-artifact integrity check.
    candidate_path = facade.preparation_artifact_path(
        completed.task_id, "candidate_json"
    )
    candidate_path.write_text("{}", encoding="utf-8")
    with pytest.raises(DesktopFacadeError) as error:
        facade.preparation_classroom_review(completed.task_id)
    assert error.value.code == "preparation_artifact_drift"


def test_facade_restart_lists_completed_task_without_reinvoking_provider(
    desktop_paths: DesktopPaths,
) -> None:
    store = _ProviderStore()
    first_transport = _Transport()
    first = _facade(desktop_paths, store, first_transport)
    task = first.prepare_preparation(_payload(), "teacher-text", store.revision)
    completed = first.generate_preparation(
        task.task_id,
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )
    assert first_transport.calls == 1

    second_transport = _Transport()
    restarted = _facade(desktop_paths, store, second_transport)
    recent = restarted.list_preparations(limit=3)
    assert [row.task_id for row in recent] == [completed.task_id]
    assert recent[0].status == "completed"
    assert restarted.preparation_artifact_path(completed.task_id, "pptx").is_file()
    assert second_transport.calls == 0


def test_facade_requires_confirmation_and_current_structured_profile(
    desktop_paths: DesktopPaths,
) -> None:
    store = _ProviderStore()
    transport = _Transport()
    facade = _facade(desktop_paths, store, transport)

    with pytest.raises(DesktopFacadeError) as stale:
        facade.prepare_preparation(_payload(), "teacher-text", "STALE-REVISION")
    assert stale.value.code == "preparation_profile_stale"

    task = facade.prepare_preparation(_payload(), "teacher-text", store.revision)
    with pytest.raises(DesktopFacadeError) as unconfirmed:
        facade.generate_preparation(  # type: ignore[arg-type]
            task.task_id,
            teacher_confirmed=False,
        )
    assert unconfirmed.value.code == "teacher_confirmation_required"
    assert transport.calls == 0

    text_only_store = _ProviderStore(capabilities=("text",))
    text_only = _facade(desktop_paths, text_only_store, _Transport())
    assert text_only.preparation_profiles() == ()
    assert text_only.preparation_availability().provider_ready is False


def test_facade_maps_cancelled_task_to_explicit_retry(
    desktop_paths: DesktopPaths,
) -> None:
    store = _ProviderStore()
    facade = _facade(desktop_paths, store, _Transport())
    task = facade.prepare_preparation(_payload(), "teacher-text", store.revision)

    cancelled = facade.cancel_preparation(task.task_id)
    assert cancelled.status == "cancelled"
    assert cancelled.retryable is True
    retried = facade.retry_preparation(task.task_id)
    assert retried.status == "prepared"
    assert retried.progress_percent == 0


def test_facade_rejects_synthetic_only_profile_before_task_or_transport(
    desktop_paths: DesktopPaths,
) -> None:
    store = _ProviderStore(allowed_data_classes=("synthetic_only",))
    transport = _Transport()
    facade = _facade(desktop_paths, store, transport)

    assert facade.preparation_profiles() == ()
    assert facade.preparation_availability().provider_ready is False
    with pytest.raises(DesktopFacadeError) as denied:
        facade.prepare_preparation(_payload(), "teacher-text", store.revision)
    assert denied.value.code == "preparation_data_policy_not_allowed"
    assert facade.list_preparations(limit=3) == ()
    assert store.borrow_calls == []
    assert transport.calls == 0


def test_facade_summary_is_lean_and_rejects_unknown_or_escalated_state() -> None:
    value = {
        "task_id": "PREP-" + "a" * 32,
        "status": "cancelled",
        "topic": "平衡复习",
        "artifact_mode": "linked_bundle",
        "profile_id": "secret-internal-profile",
        "profile_revision": "secret-internal-revision",
        "candidate_id": "PREPCAND-INTERNAL",
        "created_at": "2026-09-05T12:00:00Z",
        "updated_at": "2026-09-05T12:01:00Z",
        "progress": {"percent": 37, "message_zh": "任务已停止。"},
        "artifacts": [
            {"artifact_id": "pptx", "relative_path": "internal/path"},
            {"artifact_id": "pptx", "sha256": "f" * 64},
            {"artifact_id": "qa_report", "filename": "qa_report.json"},
        ],
        "slide_count": 3,
        "quality": {"internal": "not projected"},
        "error": {"retryable": True},
        "candidate_only": True,
        "teacher_review_required": True,
        "publication_allowed": False,
    }
    summary = DesktopWorkbenchFacade._preparation_summary(value)
    assert summary.output_kind == "joint"
    assert summary.title_zh == "平衡复习"
    assert summary.progress_percent == 100
    assert summary.artifact_ids == ("pptx", "qa_report")
    assert summary.retryable is True
    projected = asdict(summary)
    for internal in (
        "profile_id",
        "profile_revision",
        "candidate_id",
        "relative_path",
        "sha256",
        "quality",
    ):
        assert internal not in projected

    with pytest.raises(DesktopFacadeError):
        DesktopWorkbenchFacade._preparation_summary({**value, "status": "invented"})
    with pytest.raises(DesktopFacadeError):
        DesktopWorkbenchFacade._preparation_summary(
            {**value, "publication_allowed": True}
        )


def test_seeded_renderer_retry_does_not_reborrow_or_reinvoke_model(
    desktop_paths: DesktopPaths,
) -> None:
    class _FailingRenderer:
        def render(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("synthetic renderer failure")

    manager = DesktopPreparationManager(
        desktop_paths.task_root / "preparation-v1", _FailingRenderer()
    )
    store = _ProviderStore()
    transport = _Transport()
    facade = _facade(desktop_paths, store, transport, manager=manager)
    task = facade.prepare_preparation(_payload(), "teacher-text", store.revision)
    failed = facade.generate_preparation(
        task.task_id,
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )
    assert failed.status == "failed"
    assert transport.calls == 1
    assert len(store.borrow_calls) == 1
    assert manager.get_task(task.task_id)["candidate_available"] is True

    facade.retry_preparation(task.task_id)
    manager.renderer = NativePreparationRenderer()
    # The original model profile can disappear after the validated candidate
    # is frozen; this retry is intentionally local-only.
    store.allowed_data_classes = ("synthetic_only",)
    store.revision = "ROTATED-AFTER-SEED"
    completed = facade.generate_preparation(
        task.task_id,
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )
    assert completed.status == "completed"
    assert transport.calls == 1
    assert len(store.borrow_calls) == 1


def test_profile_revision_mismatch_precedes_changed_data_policy(
    desktop_paths: DesktopPaths,
) -> None:
    store = _ProviderStore()
    transport = _Transport()
    facade = _facade(desktop_paths, store, transport)
    task = facade.prepare_preparation(_payload(), "teacher-text", store.revision)

    store.revision = "REV-PREP-2"
    store.allowed_data_classes = ("synthetic_only",)
    result = facade.generate_preparation(
        task.task_id,
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )

    assert result.status == "failed"
    assert result.retryable is False
    assert result.artifact_ids == ()
    assert store.borrow_calls == []
    assert store.borrow_active is False
    assert transport.calls == 0
    assert [row.task_id for row in facade.list_preparations(limit=3)] == [task.task_id]


def test_provider_credential_is_released_before_local_rendering(
    desktop_paths: DesktopPaths,
) -> None:
    store = _ProviderStore()

    class _BorrowReleasedRenderer:
        def __init__(self) -> None:
            self.delegate = NativePreparationRenderer()
            self.calls = 0

        def render(self, *args: Any, **kwargs: Any) -> Any:
            assert store.borrow_active is False
            self.calls += 1
            return self.delegate.render(*args, **kwargs)

    renderer = _BorrowReleasedRenderer()
    manager = DesktopPreparationManager(
        desktop_paths.task_root / "preparation-v1", renderer
    )
    transport = _Transport()
    facade = _facade(desktop_paths, store, transport, manager=manager)
    task = facade.prepare_preparation(_payload(), "teacher-text", store.revision)

    result = facade.generate_preparation(
        task.task_id,
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )

    assert result.status == "completed"
    assert renderer.calls == 1
    assert store.borrow_calls == [("teacher-text", "REV-PREP-1")]
    assert store.borrow_active is False
    assert transport.calls == 1


def test_transport_cancellation_finishes_as_cancelled_not_failed(
    desktop_paths: DesktopPaths,
) -> None:
    store = _ProviderStore()
    transport = _CancelledTransport()
    facade = _facade(desktop_paths, store, transport)
    task = facade.prepare_preparation(_payload(), "teacher-text", store.revision)

    result = facade.generate_preparation(
        task.task_id,
        teacher_confirmed=True,
        should_cancel=lambda: False,
    )

    assert result.status == "cancelled"
    assert result.retryable is True
    assert result.artifact_ids == ()
    assert transport.calls == 1

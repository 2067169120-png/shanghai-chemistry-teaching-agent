from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1 import intake_imports as intake_import_module
from integrations.deeptutor_shchem_v1.intake_imports import (
    IntakeImportError,
    IntakeImportJobManager,
    intake_visual_candidate_schema,
)
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ModelProviderProbeError,
    ProbeTransportResponse,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderSettingsStore,
)

SECRET = "sk-test-visual-intake-secret-1234567890"


class FakeCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def available(self) -> bool:
        return True

    def write(self, target_name: str, secret: str) -> None:
        self.values[target_name] = secret

    def read(self, target_name: str) -> str | None:
        return self.values.get(target_name)

    def exists(self, target_name: str) -> bool:
        return target_name in self.values

    def delete(self, target_name: str) -> bool:
        return self.values.pop(target_name, None) is not None


def candidate() -> dict[str, Any]:
    evidence = [{"page": 1, "bbox": {"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.2}}]
    return {
        "schema_version": "shchem.intake-visual-candidate.v1",
        "candidate_status": "candidate_only",
        "paper_identity_candidates": [
            {
                "field": "title",
                "value": "可见标题候选",
                "confidence": 0.92,
                "evidence": evidence,
            }
        ],
        "theme_boundaries": [],
        "printed_question_candidates": [],
        "atomic_part_candidates": [],
        "shared_material_candidates": [],
        "answer_page_mappings": [],
        "textbook_mapping_candidates": [],
        "cognitive_difficulty_candidates": [],
        "review_blockers": [],
        "requires_teacher_review": True,
        "central_registry_write": False,
    }


def reviewable_candidate(
    *, blocker_codes: list[str] | None = None
) -> dict[str, Any]:
    value = candidate()
    evidence = [
        {
            "page": 1,
            "bbox": {"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.2},
        }
    ]
    value["theme_boundaries"] = [
        {
            "theme_candidate_id": "theme-1",
            "title_zh": "电化学主题",
            "order": 1,
            "page_span": {"start_page": 1, "end_page": 1},
            "confidence": 0.9,
            "evidence": evidence,
        }
    ]
    value["shared_material_candidates"] = [
        {
            "shared_material_id": "shared-1",
            "theme_candidate_id": "theme-1",
            "page_span": {"start_page": 1, "end_page": 1},
            "summary_zh": "装置示意图",
            "confidence": 0.88,
            "evidence": evidence,
        }
    ]
    value["printed_question_candidates"] = [
        {
            "printed_candidate_id": "printed-1",
            "theme_candidate_id": "theme-1",
            "question_number": "1",
            "page_span": {"start_page": 1, "end_page": 1},
            "shared_material_refs": ["shared-1"],
            "confidence": 0.91,
            "evidence": evidence,
        }
    ]
    value["atomic_part_candidates"] = [
        {
            "atomic_candidate_id": "atomic-1",
            "printed_candidate_id": "printed-1",
            "part_label": "（1）",
            "item_type": "reasoned_explanation",
            "confidence": 0.89,
            "evidence": evidence,
        }
    ]
    value["answer_page_mappings"] = [
        {
            "answer_page": 1,
            "question_number": "1",
            "printed_candidate_id": "printed-1",
            "conflict": False,
            "confidence": 0.75,
            "evidence": evidence,
        }
    ]
    value["textbook_mapping_candidates"] = [
        {
            "atomic_candidate_id": "atomic-1",
            "volume_zh": "选择性必修1",
            "chapter_zh": "第4章 氧化还原反应与电化学",
            "section_zh": "4.3 原电池",
            "confidence": 0.82,
            "evidence": evidence,
        }
    ]
    value["cognitive_difficulty_candidates"] = [
        {
            "atomic_candidate_id": "atomic-1",
            "level": "intermediate",
            "reason_zh": "需要结合装置与电子转移解释。",
            "confidence": 0.78,
            "evidence": evidence,
        }
    ]
    value["review_blockers"] = [
        {
            "code": code,
            "details_zh": f"需确认：{code}",
            "evidence": evidence,
        }
        for code in (blocker_codes or [])
    ]
    return value


class FakeSuccessTransport:
    def __init__(self, candidate_value: dict[str, Any] | None = None) -> None:
        self.requests: list[Any] = []
        self.candidate_value = candidate_value or candidate()

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        del deadline_monotonic
        self.requests.append(request)
        if cancel_event.is_set():
            raise ModelProviderProbeError(
                "cancelled",
                "cancelled",
                409,
                connection_state="cancelled",
            )
        if request.api_style == "responses":
            payload = {
                "status": "completed",
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    self.candidate_value, ensure_ascii=False
                                ),
                            }
                        ]
                    }
                ],
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 20,
                    "total_tokens": 70,
                },
            }
        else:
            payload = {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                self.candidate_value, ensure_ascii=False
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70,
                },
            }
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            latency_ms=9,
            model_invoked=True,
        )


class FakeBlockingTransport(FakeSuccessTransport):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        self.started.set()
        while not self.release.wait(0.01):
            if cancel_event.is_set():
                raise ModelProviderProbeError(
                    "cancelled",
                    "cancelled",
                    409,
                    model_invoked=True,
                    connection_state="cancelled",
                )
        return super().send(
            request,
            cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic,
        )


class FakeFailureTransport:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        del request, cancel_event, deadline_monotonic
        self.calls += 1
        raise ModelProviderProbeError(
            "provider_unavailable",
            "provider unavailable",
            503,
            model_invoked=True,
        )


class FakePayloadTransport:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        del request, cancel_event, deadline_monotonic
        self.calls += 1
        body = json.dumps(self.payload, ensure_ascii=False).encode("utf-8")
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=body,
            latency_ms=4,
            model_invoked=True,
        )


class FakeAttemptGenerationTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.second_started = threading.Event()

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        del request, deadline_monotonic
        self.calls += 1
        if self.calls == 1:
            raise ModelProviderProbeError(
                "provider_unavailable",
                "first attempt failed",
                503,
                model_invoked=True,
            )
        self.second_started.set()
        while not cancel_event.wait(0.01):
            pass
        raise ModelProviderProbeError(
            "cancelled",
            "second attempt cancelled",
            409,
            model_invoked=True,
            connection_state="cancelled",
        )


def png_bytes(color: str = "white") -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (80, 120), color).save(stream, format="PNG")
    return stream.getvalue()


def pdf_bytes() -> bytes:
    return b"%PDF-1.7\n% visual intake fixture\n"


def docx_bytes(*, external: bool = False) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document xmlns:w='urn:test'/>")
        if external:
            archive.writestr(
                "word/_rels/document.xml.rels",
                '<Relationships><Relationship TargetMode = "External" Target="https://example.invalid"/></Relationships>',
            )
    return stream.getvalue()


class FixtureRenderer:
    def render(self, source_path: Path, *, mime_type: str, work_root: Path):
        del source_path, mime_type
        from integrations.deeptutor_shchem_v1.intake_imports import RenderedPage

        work_root.mkdir(parents=True)
        page = work_root / "page-0001.png"
        page.write_bytes(png_bytes())
        return [RenderedPage(page, "image/png")]


class BlockingRenderer(FixtureRenderer):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def render(self, source_path: Path, *, mime_type: str, work_root: Path):
        self.started.set()
        assert self.release.wait(3)
        return super().render(
            source_path, mime_type=mime_type, work_root=work_root
        )


def create_store(
    tmp_path: Path,
    *,
    configure_key: bool = True,
    custom_chat: bool = False,
    capabilities: list[str] | None = None,
    unified_visual_policy: bool = False,
) -> tuple[ModelProviderSettingsStore, dict[str, Any], FakeCredentialBackend]:
    backend = FakeCredentialBackend()
    store = ModelProviderSettingsStore(
        tmp_path / "provider",
        project_root=tmp_path / "project",
        credential_backend=backend,
    )
    declared = capabilities or ["text", "vision", "structured_output"]
    image_data_classes = ["synthetic_only", "source_page_image"]
    image_egress = "teacher_confirmed_source_pages"
    if unified_visual_policy:
        image_data_classes.append("student_answer_image")
        image_egress = "teacher_confirmed_visual_pages"
    if custom_chat:
        profile = {
            "profile_id": "vision-profile",
            "provider_kind": "openai_compatible",
            "display_name": "视觉服务",
            "base_url": "https://vision.example.com/v1",
            "api_style": "chat_completions",
            "local_endpoint_policy": "deny",
            "model_id": "teacher-vision-model",
            "capabilities": declared,
            "allowed_data_classes": image_data_classes,
            "image_egress": image_egress,
            "last_probe": None,
        }
    elif "vision" in declared:
        profile = {
            "profile_id": "vision-profile",
            "provider_id": "openai",
            "model_id": "gpt-5-mini",
            "base_url_policy": "openai_official_https_v1",
            "allowed_data_classes": image_data_classes,
            "image_egress": image_egress,
            "last_probe": None,
        }
    else:
        profile = {
            "profile_id": "vision-profile",
            "provider_kind": "openai_compatible",
            "display_name": "仅文本服务",
            "base_url": "https://text.example.com/v1",
            "api_style": "responses",
            "local_endpoint_policy": "deny",
            "model_id": "teacher-text-model",
            "capabilities": declared,
            "allowed_data_classes": ["synthetic_only"],
            "image_egress": "deny",
            "last_probe": None,
        }
    created = store.upsert_metadata(profile, expected_revision=None)
    if configure_key:
        created = store.put_credential(
            "vision-profile", SECRET, expected_revision=created["revision"]
        )
    return store, created, backend


def create_manager(
    tmp_path: Path,
    store: ModelProviderSettingsStore | None,
    transport: Any,
    *,
    renderer: Any | None = None,
    max_upload_bytes: int = 1024 * 1024,
) -> IntakeImportJobManager:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return IntakeImportJobManager(
        tmp_path / "intake",
        project_root=project,
        provider_store=store,
        renderer=renderer,
        transport=transport,
        max_upload_bytes=max_upload_bytes,
    )


def create_and_upload(
    manager: IntakeImportJobManager,
    data: bytes | None = None,
) -> dict[str, Any]:
    raw = data or png_bytes()
    created = manager.create(
        {
            "source_role": "question_paper",
            "year": "unknown",
            "region_or_school": "unknown",
            "paper_type": "unknown",
            "filename": "paper.png",
            "mime_type": "image/png",
            "size_bytes": len(raw),
        },
        actor_id="teacher-one",
    )
    uploaded = manager.upload(
        created["import_id"],
        raw,
        content_type="image/png",
        actor_id="teacher-one",
    )
    assert uploaded["status"] == "ready_for_analysis"
    return uploaded


def wait_terminal(manager: IntakeImportJobManager, import_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = manager.get(import_id, actor_id="teacher-one")
        if value["status"] not in {
            "queued_for_analysis",
            "analyzing",
            "cancel_requested",
        }:
            return value
        time.sleep(0.01)
    raise AssertionError("intake analysis did not terminate")


def complete_reviewable_import(
    tmp_path: Path, *, blocker_codes: list[str] | None = None
) -> tuple[IntakeImportJobManager, dict[str, Any]]:
    store, profile, _backend = create_store(tmp_path)
    manager = create_manager(
        tmp_path,
        store,
        FakeSuccessTransport(
            reviewable_candidate(blocker_codes=blocker_codes)
        ),
    )
    uploaded = create_and_upload(manager)
    manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    completed = wait_terminal(manager, uploaded["import_id"])
    assert completed["status"] == "completed"
    return manager, completed


def test_real_responses_multimodal_shape_and_candidate_stays_external(tmp_path: Path) -> None:
    store, profile, _backend = create_store(tmp_path)
    transport = FakeSuccessTransport()
    manager = create_manager(tmp_path, store, transport)
    before = sorted(path.relative_to(tmp_path / "project") for path in (tmp_path / "project").rglob("*"))
    uploaded = create_and_upload(manager)
    queued = manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    assert queued["status"] == "queued_for_analysis"
    completed = wait_terminal(manager, uploaded["import_id"])
    manager.shutdown()

    assert completed["status"] == "completed"
    assert completed["review"] == {
        "required": True,
        "status": "pending",
        "revision": 0,
        "decisions": [],
        "personal_library_visible": False,
    }
    assert len(completed["candidate_sha256"]) == 64
    assert completed["central_registry_write"] is False
    assert completed["candidate"]["candidate_status"] == "candidate_only"
    assert completed["invocation_boundary"] == {
        "one_time_production_model_invocation_enabled": False,
        "specialized_visual_api_path": True,
        "candidate_only": True,
    }
    attempt = completed["attempts"][-1]
    assert attempt["visual_api_invocation_allowed"] is True
    assert attempt["model_invoked"] is True
    assert attempt["provider_profile_id"] == "vision-profile"
    assert attempt["credential_revision"] == profile["revision"]
    assert attempt["vision_capability_evidence"]["sources"] == [
        "catalog",
        "declared",
    ]
    assert attempt["vision_capability_evidence"]["inferred_from_model_name"] is False
    assert attempt["request"]["direct_page_images"] is True
    assert attempt["request"]["recognized_text_input_present"] is False
    assert attempt["request"]["fallback_text_input_present"] is False

    assert len(transport.requests) == 1
    request = transport.requests[0]
    body = json.loads(request.body)
    content = body["input"][0]["content"]
    images = [item for item in content if item["type"] == "input_image"]
    assert len(images) == 1
    assert images[0]["image_url"].startswith("data:image/png;base64,")
    assert base64_payload(images[0]["image_url"]) == png_bytes()
    assert SECRET.encode() not in request.body
    assert "object_relative_path" not in json.dumps(completed)
    assert sorted(path.relative_to(tmp_path / "project") for path in (tmp_path / "project").rglob("*")) == before


def test_teacher_accept_is_hash_bound_append_only_and_projects_personal_library(
    tmp_path: Path,
) -> None:
    manager, completed = complete_reviewable_import(tmp_path)
    candidate_before = json.dumps(
        completed["candidate"], ensure_ascii=False, sort_keys=True
    )
    candidate_sha256 = completed["candidate_sha256"]

    accepted = manager.review_decision(
        completed["import_id"],
        expected_review_revision=0,
        candidate_sha256=candidate_sha256,
        decision="accept_personal_library",
        acknowledged_blocker_codes=[],
        teacher_note_zh="已逐页确认题号、上下文与答案对应。",
        actor_id="teacher-one",
    )
    assert accepted["candidate_sha256"] == candidate_sha256
    assert json.dumps(
        accepted["candidate"], ensure_ascii=False, sort_keys=True
    ) == candidate_before
    assert accepted["review"]["status"] == "accepted_personal_library"
    assert accepted["review"]["revision"] == 1
    assert accepted["review"]["personal_library_visible"] is True
    assert len(accepted["review"]["decisions"]) == 1
    decision = accepted["review"]["decisions"][0]
    assert decision["decision_id"].startswith("INTREV-")
    assert decision["candidate_sha256"] == candidate_sha256
    assert decision["revision_before"] == 0
    assert decision["revision_after"] == 1
    assert decision["acknowledged_blocker_codes"] == []
    assert accepted["events"][-1]["type"] == "review_decision_recorded"

    library = manager.personal_library(actor_id="teacher-one")
    manager.shutdown()
    assert set(library) == {"schema_version", "items", "count"}
    assert library["schema_version"] == "shchem.personal-import-library.v1"
    assert library["count"] == 1
    item = library["items"][0]
    assert set(item) == {
        "import_id",
        "candidate_sha256",
        "identity",
        "source",
        "theme_boundaries",
        "printed_question_candidates",
        "atomic_part_candidates",
        "shared_material_candidates",
        "answer_page_mappings",
        "textbook_mapping_candidates",
        "cognitive_difficulty_candidates",
        "review",
        "candidate_only",
        "central_registry_write",
    }
    assert item["candidate_sha256"] == candidate_sha256
    assert item["candidate_only"] is True
    assert item["central_registry_write"] is False
    assert len(item["printed_question_candidates"]) == 1
    assert len(item["atomic_part_candidates"]) == 1
    assert item["identity"]["declared"]["year"] == "unknown"
    assert item["identity"]["visual_candidates"][0]["value"] == "可见标题候选"
    assert "relative_path" not in json.dumps(item, ensure_ascii=False)
    assert "object_relative_path" not in json.dumps(item, ensure_ascii=False)


def test_review_cas_hash_blocker_ack_and_terminal_decision_fail_closed(
    tmp_path: Path,
) -> None:
    manager, completed = complete_reviewable_import(
        tmp_path, blocker_codes=["low_confidence", "other"]
    )
    import_id = completed["import_id"]
    candidate_sha256 = completed["candidate_sha256"]

    with pytest.raises(IntakeImportError) as stale:
        manager.review_decision(
            import_id,
            expected_review_revision=1,
            candidate_sha256=candidate_sha256,
            decision="accept_personal_library",
            acknowledged_blocker_codes=["low_confidence", "other"],
            teacher_note_zh="",
            actor_id="teacher-one",
        )
    assert stale.value.code == "review_revision_conflict"
    with pytest.raises(IntakeImportError) as wrong_hash:
        manager.review_decision(
            import_id,
            expected_review_revision=0,
            candidate_sha256="0" * 64,
            decision="accept_personal_library",
            acknowledged_blocker_codes=["low_confidence", "other"],
            teacher_note_zh="",
            actor_id="teacher-one",
        )
    assert wrong_hash.value.code == "candidate_sha256_mismatch"
    with pytest.raises(IntakeImportError) as incomplete_ack:
        manager.review_decision(
            import_id,
            expected_review_revision=0,
            candidate_sha256=candidate_sha256,
            decision="accept_personal_library",
            acknowledged_blocker_codes=["low_confidence"],
            teacher_note_zh="",
            actor_id="teacher-one",
        )
    assert incomplete_ack.value.code == "review_blocker_acknowledgement_required"
    with pytest.raises(IntakeImportError) as unknown_ack:
        manager.review_decision(
            import_id,
            expected_review_revision=0,
            candidate_sha256=candidate_sha256,
            decision="accept_personal_library",
            acknowledged_blocker_codes=["low_confidence", "other", "answer_conflict"],
            teacher_note_zh="",
            actor_id="teacher-one",
        )
    assert unknown_ack.value.code == "review_blocker_acknowledgement_invalid"

    accepted = manager.review_decision(
        import_id,
        expected_review_revision=0,
        candidate_sha256=candidate_sha256,
        decision="accept_personal_library",
        acknowledged_blocker_codes=["other", "low_confidence"],
        teacher_note_zh="已知悉两个视觉候选阻断项。",
        actor_id="teacher-one",
    )
    assert accepted["review"]["decisions"][0][
        "acknowledged_blocker_codes"
    ] == ["low_confidence", "other"]
    with pytest.raises(IntakeImportError) as repeated:
        manager.review_decision(
            import_id,
            expected_review_revision=1,
            candidate_sha256=candidate_sha256,
            decision="reject",
            acknowledged_blocker_codes=[],
            teacher_note_zh="",
            actor_id="teacher-one",
        )
    manager.shutdown()
    assert repeated.value.code == "review_already_final"


def test_reject_accepts_empty_ack_and_incomplete_candidate_cannot_be_accepted(
    tmp_path: Path,
) -> None:
    store, profile, _backend = create_store(tmp_path)
    manager = create_manager(tmp_path, store, FakeSuccessTransport())
    uploaded = create_and_upload(manager)
    manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    completed = wait_terminal(manager, uploaded["import_id"])
    with pytest.raises(IntakeImportError) as incomplete:
        manager.review_decision(
            completed["import_id"],
            expected_review_revision=0,
            candidate_sha256=completed["candidate_sha256"],
            decision="accept_personal_library",
            acknowledged_blocker_codes=[],
            teacher_note_zh="",
            actor_id="teacher-one",
        )
    assert incomplete.value.code == "candidate_structure_incomplete"
    rejected = manager.review_decision(
        completed["import_id"],
        expected_review_revision=0,
        candidate_sha256=completed["candidate_sha256"],
        decision="reject",
        acknowledged_blocker_codes=[],
        teacher_note_zh="题目边界不足，退回。",
        actor_id="teacher-one",
    )
    library = manager.personal_library(actor_id="teacher-one")
    manager.shutdown()
    assert rejected["review"]["status"] == "rejected"
    assert rejected["review"]["personal_library_visible"] is False
    assert library["items"] == []
    assert library["count"] == 0


def test_legacy_review_record_and_missing_candidate_hash_are_normalized(
    tmp_path: Path,
) -> None:
    manager, completed = complete_reviewable_import(tmp_path)
    with manager._lock:
        raw = manager._load_job(completed["import_id"])
        raw["review"] = {"required": True, "status": "pending"}
        raw.pop("candidate_sha256")
        manager._save_job(raw)

    legacy = manager.get(completed["import_id"], actor_id="teacher-one")
    assert legacy["candidate_sha256"] == completed["candidate_sha256"]
    assert legacy["review"] == {
        "required": True,
        "status": "pending",
        "revision": 0,
        "decisions": [],
        "personal_library_visible": False,
    }
    accepted = manager.review_decision(
        completed["import_id"],
        expected_review_revision=0,
        candidate_sha256=legacy["candidate_sha256"],
        decision="accept_personal_library",
        acknowledged_blocker_codes=[],
        teacher_note_zh="兼容旧复核记录。",
        actor_id="teacher-one",
    )
    with manager._lock:
        persisted = manager._load_job(completed["import_id"])
    manager.shutdown()
    assert accepted["review"]["revision"] == 1
    assert persisted["candidate_sha256"] == completed["candidate_sha256"]
    assert persisted["candidate"] == completed["candidate"]


def test_page_content_is_exact_hash_bound_owner_only_and_detects_tampering(
    tmp_path: Path,
) -> None:
    manager, completed = complete_reviewable_import(tmp_path)
    page = manager.page_content(
        completed["import_id"], 1, actor_id="teacher-one"
    )
    assert set(page) == {
        "import_id",
        "page",
        "mime_type",
        "sha256",
        "size_bytes",
        "width",
        "height",
        "content",
    }
    assert page["mime_type"] == "image/png"
    assert hashlib.sha256(page["content"]).hexdigest() == page["sha256"]
    assert len(page["content"]) == page["size_bytes"]
    with pytest.raises(IntakeImportError) as cross_owner:
        manager.page_content(
            completed["import_id"], 1, actor_id="teacher-two"
        )
    assert cross_owner.value.code == "import_not_found"
    with pytest.raises(IntakeImportError) as missing:
        manager.page_content(
            completed["import_id"], 2, actor_id="teacher-one"
        )
    assert missing.value.code == "page_not_found"
    with pytest.raises(IntakeImportError) as cross_owner_review:
        manager.review_decision(
            completed["import_id"],
            expected_review_revision=0,
            candidate_sha256=completed["candidate_sha256"],
            decision="reject",
            acknowledged_blocker_codes=[],
            teacher_note_zh="",
            actor_id="teacher-two",
        )
    assert cross_owner_review.value.code == "import_not_found"
    assert manager.personal_library(actor_id="teacher-two")["count"] == 0

    with manager._lock:
        raw = manager._load_job(completed["import_id"])
        relative_path = raw["source"]["pages"][0]["relative_path"]
    (manager.root / relative_path).write_bytes(png_bytes("black"))
    with pytest.raises(IntakeImportError) as tampered:
        manager.page_content(
            completed["import_id"], 1, actor_id="teacher-one"
        )
    manager.shutdown()
    assert tampered.value.code == "page_store_corrupt"


def test_personal_library_returns_fresh_deep_copies(tmp_path: Path) -> None:
    manager, completed = complete_reviewable_import(tmp_path)
    manager.review_decision(
        completed["import_id"],
        expected_review_revision=0,
        candidate_sha256=completed["candidate_sha256"],
        decision="accept_personal_library",
        acknowledged_blocker_codes=[],
        teacher_note_zh="",
        actor_id="teacher-one",
    )
    first = manager.personal_library(actor_id="teacher-one")
    first["items"][0]["atomic_part_candidates"][0]["item_type"] = "tampered"
    first["items"][0]["identity"]["declared"]["year"] = "2099"
    second = manager.personal_library(actor_id="teacher-one")
    current = manager.get(completed["import_id"], actor_id="teacher-one")
    manager.shutdown()
    assert second["items"][0]["atomic_part_candidates"][0]["item_type"] == (
        "reasoned_explanation"
    )
    assert second["items"][0]["identity"]["declared"]["year"] == "unknown"
    assert current["candidate_sha256"] == completed["candidate_sha256"]


def test_persisted_candidate_tamper_is_rejected_even_with_valid_job_record_hash(
    tmp_path: Path,
) -> None:
    manager, completed = complete_reviewable_import(tmp_path)
    with manager._lock:
        raw = manager._load_job(completed["import_id"])
        raw["candidate"]["atomic_part_candidates"][0]["item_type"] = "tampered"
        # _save_job refreshes the outer job-record hash, but deliberately does
        # not rewrite the independently bound candidate hash.
        manager._save_job(raw)
    with pytest.raises(IntakeImportError) as corrupt:
        manager.get(completed["import_id"], actor_id="teacher-one")
    manager.shutdown()
    assert corrupt.value.code == "import_record_corrupt"


def base64_payload(data_url: str) -> bytes:
    import base64

    return base64.b64decode(data_url.split(",", 1)[1], validate=True)


def test_custom_chat_multimodal_shape_uses_teacher_declared_vision(tmp_path: Path) -> None:
    store, profile, _backend = create_store(tmp_path, custom_chat=True)
    transport = FakeSuccessTransport()
    manager = create_manager(tmp_path, store, transport)
    uploaded = create_and_upload(manager)
    manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    completed = wait_terminal(manager, uploaded["import_id"])
    manager.shutdown()
    assert completed["status"] == "completed"
    assert completed["attempts"][-1]["vision_capability_evidence"]["sources"] == ["declared"]
    body = json.loads(transport.requests[0].body)
    content = body["messages"][1]["content"]
    images = [item for item in content if item["type"] == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_unified_visual_policy_allows_existing_source_page_intake(
    tmp_path: Path,
) -> None:
    store, profile, _backend = create_store(tmp_path, unified_visual_policy=True)
    transport = FakeSuccessTransport()
    manager = create_manager(tmp_path, store, transport)
    uploaded = create_and_upload(manager)
    manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    completed = wait_terminal(manager, uploaded["import_id"])
    manager.shutdown()

    assert completed["status"] == "completed"
    assert completed["attempts"][-1]["request"]["egress_policy"] == (
        "teacher_confirmed_visual_pages"
    )
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("configure_key", "capabilities", "expected_code"),
    [
        (False, ["text", "vision", "structured_output"], "awaiting_visual_provider"),
        (True, ["text", "structured_output"], "vision_capability_unconfirmed"),
        (True, ["text", "vision"], "structured_output_capability_required"),
    ],
)
def test_provider_gate_never_calls_transport(
    tmp_path: Path,
    configure_key: bool,
    capabilities: list[str],
    expected_code: str,
) -> None:
    store, profile, _backend = create_store(
        tmp_path,
        configure_key=configure_key,
        custom_chat="vision" in capabilities,
        capabilities=capabilities,
    )
    transport = FakeSuccessTransport()
    manager = create_manager(tmp_path, store, transport)
    uploaded = create_and_upload(manager)
    blocked = manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    manager.shutdown()
    assert blocked["status"] == "awaiting_visual_provider"
    assert blocked["candidate"] is None
    attempt = blocked["attempts"][-1]
    assert attempt["blocker"]["code"] == expected_code
    assert attempt["visual_api_invocation_allowed"] is False
    assert attempt["model_invoked"] is False
    assert transport.requests == []


def test_stale_revision_discards_successful_provider_response(tmp_path: Path) -> None:
    store, profile, _backend = create_store(tmp_path)
    transport = FakeBlockingTransport()
    manager = create_manager(tmp_path, store, transport)
    uploaded = create_and_upload(manager)
    manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    assert transport.started.wait(2)
    rotated = store.put_credential(
        "vision-profile", SECRET + "-rotated", expected_revision=profile["revision"]
    )
    assert rotated["revision"] != profile["revision"]
    transport.release.set()
    stale = wait_terminal(manager, uploaded["import_id"])
    manager.shutdown()
    assert stale["status"] == "awaiting_visual_provider"
    assert stale["candidate"] is None
    attempt = stale["attempts"][-1]
    assert attempt["status"] == "stale"
    assert attempt["blocker"]["code"] == "stale_provider_revision"
    assert attempt["model_invoked"] is True


def test_cancel_and_upstream_failure_never_fake_success(tmp_path: Path) -> None:
    store, profile, _backend = create_store(tmp_path)
    blocking = FakeBlockingTransport()
    manager = create_manager(tmp_path, store, blocking)
    uploaded = create_and_upload(manager)
    manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    assert blocking.started.wait(2)
    requested = manager.cancel(uploaded["import_id"], actor_id="teacher-one")
    assert requested["status"] == "cancel_requested"
    blocking.release.set()
    cancelled = wait_terminal(manager, uploaded["import_id"])
    manager.shutdown()
    assert cancelled["status"] == "cancelled"
    assert cancelled["candidate"] is None

    failure = FakeFailureTransport()
    manager = create_manager(tmp_path / "second", store, failure)
    uploaded = create_and_upload(manager)
    manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    failed = wait_terminal(manager, uploaded["import_id"])
    manager.shutdown()
    assert failed["status"] == "analysis_failed"
    assert failed["candidate"] is None
    assert failed["attempts"][-1]["model_invoked"] is True


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
            },
            "provider_response_incomplete",
        ),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "content": [
                            {
                                "type": "refusal",
                                "refusal": {"reason": "policy"},
                            }
                        ]
                    }
                ],
            },
            "provider_response_refused",
        ),
    ],
)
def test_incomplete_or_refused_provider_response_never_creates_candidate(
    tmp_path: Path,
    payload: dict[str, Any],
    expected_code: str,
) -> None:
    store, profile, _backend = create_store(tmp_path)
    transport = FakePayloadTransport(payload)
    manager = create_manager(tmp_path, store, transport)
    uploaded = create_and_upload(manager)
    manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    failed = wait_terminal(manager, uploaded["import_id"])
    manager.shutdown()
    assert transport.calls == 1
    assert failed["status"] == "analysis_failed"
    assert failed["candidate"] is None
    assert failed["attempts"][-1]["model_invoked"] is True
    assert failed["attempts"][-1]["blocker"]["code"] == expected_code


def test_old_attempt_cleanup_cannot_remove_new_attempt_cancel_handle(
    tmp_path: Path,
) -> None:
    store, profile, _backend = create_store(tmp_path)
    transport = FakeAttemptGenerationTransport()
    manager = create_manager(tmp_path, store, transport)
    uploaded = create_and_upload(manager)
    first_terminal_saved = threading.Event()
    release_first_worker = threading.Event()
    original_terminal_failure = manager._terminal_failure

    def delayed_terminal_failure(
        import_id: str,
        attempt_id: str,
        **kwargs: Any,
    ) -> None:
        original_terminal_failure(import_id, attempt_id, **kwargs)
        if kwargs.get("code") == "provider_unavailable":
            first_terminal_saved.set()
            assert release_first_worker.wait(3)

    manager._terminal_failure = delayed_terminal_failure  # type: ignore[method-assign]
    manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    assert first_terminal_saved.wait(2)
    retry = manager.analyze(
        uploaded["import_id"],
        profile_id="vision-profile",
        expected_revision=profile["revision"],
        teacher_confirmed_egress=True,
        actor_id="teacher-one",
    )
    assert retry["status"] == "queued_for_analysis"
    second_cancel_event = manager._cancel_events[uploaded["import_id"]]
    release_first_worker.set()
    assert transport.second_started.wait(2)
    assert manager._cancel_events[uploaded["import_id"]] is second_cancel_event

    requested = manager.cancel(uploaded["import_id"], actor_id="teacher-one")
    assert requested["status"] == "cancel_requested"
    terminal = wait_terminal(manager, uploaded["import_id"])
    manager.shutdown()
    assert terminal["status"] == "cancelled"
    assert terminal["candidate"] is None
    assert transport.calls == 2


@pytest.mark.parametrize(
    ("profile_id", "revision", "expected_code"),
    [
        ({"profile": "vision-profile"}, "rev_" + "a" * 32, "provider_profile_id_invalid"),
        (["vision-profile"], "rev_" + "a" * 32, "provider_profile_id_invalid"),
        ("vision-profile", {"revision": "rev"}, "provider_revision_invalid"),
        ("vision-profile", "rev_" + "a" * 1000, "provider_revision_invalid"),
    ],
)
def test_invalid_provider_identity_inputs_do_not_persist_attempts(
    tmp_path: Path,
    profile_id: Any,
    revision: Any,
    expected_code: str,
) -> None:
    store, _profile, _backend = create_store(tmp_path)
    manager = create_manager(tmp_path, store, FakeSuccessTransport())
    uploaded = create_and_upload(manager)
    with pytest.raises(IntakeImportError) as invalid:
        manager.analyze(
            uploaded["import_id"],
            profile_id=profile_id,
            expected_revision=revision,
            teacher_confirmed_egress=True,
            actor_id="teacher-one",
        )
    assert invalid.value.code == expected_code
    current = manager.get(uploaded["import_id"], actor_id="teacher-one")
    manager.shutdown()
    assert current["status"] == "ready_for_analysis"
    assert current["attempts"] == []


def test_hash_dedup_immutable_upload_and_recovery(tmp_path: Path) -> None:
    store, _profile, _backend = create_store(tmp_path)
    manager = create_manager(tmp_path, store, FakeSuccessTransport())
    first = create_and_upload(manager)
    second = create_and_upload(manager)
    assert first["source"]["sha256"] == second["source"]["sha256"]
    assert first["source"]["deduplicated"] is False
    assert second["source"]["deduplicated"] is True
    object_files = [path for path in (tmp_path / "intake" / "objects").rglob("*") if path.is_file()]
    assert len(object_files) == 1
    with pytest.raises(IntakeImportError) as replaced:
        manager.upload(
            first["import_id"],
            png_bytes("black"),
            content_type="image/png",
            actor_id="teacher-one",
        )
    assert replaced.value.code == "source_already_uploaded"

    with manager._lock:
        raw = manager._load_job(first["import_id"])
        raw["status"] = "analyzing"
        raw["attempts"].append(
            {
                "attempt_id": "INTATT-" + "a" * 32,
                "status": "running",
                "provider_profile_id": "vision-profile",
                "credential_revision": "rev_" + "b" * 32,
                "vision_capability_evidence": {"sources": ["catalog"]},
                "visual_api_invocation_allowed": True,
                "model_invoked": False,
            }
        )
        manager._save_job(raw)
    manager.shutdown()
    recovered_manager = create_manager(tmp_path, store, FakeSuccessTransport())
    recovered = recovered_manager.get(first["import_id"], actor_id="teacher-one")
    recovered_manager.shutdown()
    assert recovered["status"] == "ready_for_analysis"
    assert recovered["attempts"][-1]["status"] == "failed"
    assert recovered["attempts"][-1]["blocker"]["code"] == "interrupted_by_restart"


def test_store_lease_prevents_two_managers_from_recovering_same_jobs(
    tmp_path: Path,
) -> None:
    store, _profile, _backend = create_store(tmp_path)
    first = create_manager(tmp_path, store, FakeSuccessTransport())
    with pytest.raises(IntakeImportError) as duplicate:
        create_manager(tmp_path, store, FakeSuccessTransport())
    assert duplicate.value.code == "intake_state_in_use"
    assert first.root in intake_import_module._ROOT_LEASES
    with pytest.raises(IntakeImportError) as still_leased:
        create_manager(tmp_path, store, FakeSuccessTransport())
    assert still_leased.value.code == "intake_state_in_use"
    assert first.root in intake_import_module._ROOT_LEASES
    first.shutdown()
    reopened = create_manager(tmp_path, store, FakeSuccessTransport())
    reopened.shutdown()


def test_shutdown_closes_every_public_operation_before_releasing_lease(
    tmp_path: Path,
) -> None:
    store, profile, _backend = create_store(tmp_path)
    manager = create_manager(tmp_path, store, FakeSuccessTransport())
    raw = png_bytes()
    created = manager.create(
        {
            "source_role": "question_paper",
            "year": "unknown",
            "region_or_school": "unknown",
            "paper_type": "unknown",
            "filename": "paper.png",
            "mime_type": "image/png",
            "size_bytes": len(raw),
        },
        actor_id="teacher-one",
    )
    manager.shutdown()

    operations = (
        lambda: manager.get(created["import_id"], actor_id="teacher-one"),
        lambda: manager.upload(
            created["import_id"],
            raw,
            content_type="image/png",
            actor_id="teacher-one",
        ),
        lambda: manager.cancel(created["import_id"], actor_id="teacher-one"),
        lambda: manager.analyze(
            created["import_id"],
            profile_id="vision-profile",
            expected_revision=profile["revision"],
            teacher_confirmed_egress=True,
            actor_id="teacher-one",
        ),
        lambda: manager.create(
            {
                "source_role": "answer",
                "year": "unknown",
                "region_or_school": "unknown",
                "paper_type": "unknown",
                "filename": "answer.png",
                "mime_type": "image/png",
                "size_bytes": len(raw),
            },
            actor_id="teacher-one",
        ),
        lambda: manager.review_decision(
            created["import_id"],
            expected_review_revision=0,
            candidate_sha256="a" * 64,
            decision="reject",
            acknowledged_blocker_codes=[],
            teacher_note_zh="",
            actor_id="teacher-one",
        ),
        lambda: manager.personal_library(actor_id="teacher-one"),
        lambda: manager.page_content(
            created["import_id"], 1, actor_id="teacher-one"
        ),
    )
    for operation in operations:
        with pytest.raises(IntakeImportError) as closed:
            operation()
        assert closed.value.code == "intake_manager_closed"

    reopened = create_manager(tmp_path, store, FakeSuccessTransport())
    persisted = reopened.get(created["import_id"], actor_id="teacher-one")
    reopened.shutdown()
    assert persisted["status"] == "awaiting_upload"
    assert persisted["source"] is None


def test_shutdown_waits_for_admitted_upload_before_lease_release(
    tmp_path: Path,
) -> None:
    store, _profile, _backend = create_store(tmp_path)
    renderer = BlockingRenderer()
    manager = create_manager(
        tmp_path,
        store,
        FakeSuccessTransport(),
        renderer=renderer,
    )
    raw = pdf_bytes()
    created = manager.create(
        {
            "source_role": "question_paper",
            "year": "unknown",
            "region_or_school": "unknown",
            "paper_type": "unknown",
            "filename": "paper.pdf",
            "mime_type": "application/pdf",
            "size_bytes": len(raw),
        },
        actor_id="teacher-one",
    )

    upload_thread = threading.Thread(
        target=lambda: manager.upload(
            created["import_id"],
            raw,
            content_type="application/pdf",
            actor_id="teacher-one",
        )
    )
    upload_thread.start()
    assert renderer.started.wait(2)
    shutdown_thread = threading.Thread(target=manager.shutdown)
    shutdown_thread.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not manager._closed:
        time.sleep(0.01)
    assert manager._closed is True
    with pytest.raises(IntakeImportError) as closed:
        manager.cancel(created["import_id"], actor_id="teacher-one")
    assert closed.value.code == "intake_manager_closed"
    with pytest.raises(IntakeImportError) as leased:
        create_manager(tmp_path, store, FakeSuccessTransport())
    assert leased.value.code == "intake_state_in_use"

    renderer.release.set()
    upload_thread.join(timeout=3)
    shutdown_thread.join(timeout=3)
    assert not upload_thread.is_alive()
    assert not shutdown_thread.is_alive()
    reopened = create_manager(tmp_path, store, FakeSuccessTransport())
    persisted = reopened.get(created["import_id"], actor_id="teacher-one")
    reopened.shutdown()
    assert persisted["status"] == "ready_for_analysis"


def test_cancel_during_page_render_cannot_resurrect_job(tmp_path: Path) -> None:
    store, _profile, _backend = create_store(tmp_path)
    renderer = BlockingRenderer()
    manager = create_manager(
        tmp_path,
        store,
        FakeSuccessTransport(),
        renderer=renderer,
    )
    raw = pdf_bytes()
    created = manager.create(
        {
            "source_role": "question_paper",
            "year": "unknown",
            "region_or_school": "unknown",
            "paper_type": "unknown",
            "filename": "paper.pdf",
            "mime_type": "application/pdf",
            "size_bytes": len(raw),
        },
        actor_id="teacher-one",
    )
    result: dict[str, Any] = {}

    def upload() -> None:
        result.update(
            manager.upload(
                created["import_id"],
                raw,
                content_type="application/pdf",
                actor_id="teacher-one",
            )
        )

    thread = threading.Thread(target=upload)
    thread.start()
    assert renderer.started.wait(2)
    cancelled = manager.cancel(created["import_id"], actor_id="teacher-one")
    assert cancelled["status"] == "cancelled"
    renderer.release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result["status"] == "cancelled"
    assert (
        manager.get(created["import_id"], actor_id="teacher-one")["status"]
        == "cancelled"
    )
    manager.shutdown()


def test_pdf_docx_image_types_are_saved_before_page_render(tmp_path: Path) -> None:
    store, _profile, _backend = create_store(tmp_path)
    manager = create_manager(
        tmp_path, store, FakeSuccessTransport(), renderer=FixtureRenderer()
    )
    fixtures = [
        ("scan.pdf", "application/pdf", pdf_bytes()),
        (
            "handout.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            docx_bytes(),
        ),
        ("scan.webp", "image/webp", _webp_bytes()),
    ]
    for filename, mime_type, raw in fixtures:
        created = manager.create(
            {
                "source_role": "handout",
                "year": "unknown",
                "region_or_school": "unknown",
                "paper_type": "unknown",
                "filename": filename,
                "mime_type": mime_type,
                "size_bytes": len(raw),
            },
            actor_id="teacher-one",
        )
        uploaded = manager.upload(
            created["import_id"],
            raw,
            content_type=mime_type,
            actor_id="teacher-one",
        )
        assert uploaded["status"] == "ready_for_analysis"
        assert uploaded["source"]["page_count"] == 1
        source_saved = next(item for item in uploaded["events"] if item["type"] == "source_saved")
        pages_ready = next(item for item in uploaded["events"] if item["type"] == "pages_ready")
        assert source_saved["sequence"] < pages_ready["sequence"]
    manager.shutdown()


def _webp_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(stream, format="WEBP")
    return stream.getvalue()


def test_illegal_paths_mime_size_and_docx_relationships_fail_closed(tmp_path: Path) -> None:
    store, _profile, _backend = create_store(tmp_path)
    with pytest.raises(IntakeImportError) as in_project:
        IntakeImportJobManager(
            tmp_path / "project" / "intake",
            project_root=tmp_path / "project",
            provider_store=store,
        )
    assert in_project.value.code == "intake_state_not_external"

    manager = create_manager(tmp_path, store, FakeSuccessTransport(), max_upload_bytes=2000)
    base = {
        "source_role": "question_paper",
        "year": "unknown",
        "region_or_school": "unknown",
        "paper_type": "unknown",
        "mime_type": "image/png",
        "size_bytes": len(png_bytes()),
    }
    for filename in ("../paper.png", "folder/paper.png", "CON.png", "paper.pdf"):
        with pytest.raises(IntakeImportError):
            manager.create({**base, "filename": filename}, actor_id="teacher-one")
    with pytest.raises(IntakeImportError) as too_large:
        manager.create(
            {**base, "filename": "paper.png", "size_bytes": 2001},
            actor_id="teacher-one",
        )
    assert too_large.value.code == "upload_size_invalid"

    created = manager.create({**base, "filename": "paper.png"}, actor_id="teacher-one")
    with pytest.raises(IntakeImportError) as mime:
        manager.upload(
            created["import_id"],
            pdf_bytes(),
            content_type="image/png",
            actor_id="teacher-one",
        )
    assert mime.value.code == "upload_size_mismatch" or mime.value.code == "upload_signature_mismatch"

    hostile = docx_bytes(external=True)
    created = manager.create(
        {
            **base,
            "filename": "hostile.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(hostile),
        },
        actor_id="teacher-one",
    )
    with pytest.raises(IntakeImportError) as external:
        manager.upload(
            created["import_id"],
            hostile,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            actor_id="teacher-one",
        )
    assert external.value.code == "docx_external_relationship_forbidden"
    manager.shutdown()


def test_source_and_public_records_have_no_forbidden_recognition_dependencies_or_fields(
    tmp_path: Path,
) -> None:
    source = (
        Path(__file__).resolve().parents[4]
        / "integrations/deeptutor_shchem_v1/intake_imports.py"
    ).read_text(encoding="utf-8").casefold()
    forbidden_dependencies = (
        "pytesseract",
        "easyocr",
        "ocrmypdf",
        "paddleocr",
        "keras_ocr",
        "tesseract",
    )
    assert all(name not in source for name in forbidden_dependencies)

    manager = create_manager(tmp_path, None, FakeSuccessTransport())
    record = create_and_upload(manager)
    manager.shutdown()

    def keys(value: Any) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(key.casefold())
                found.extend(keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(keys(item))
        return found

    assert all("ocr" not in key for key in keys(record))
    assert record["recognition_mode"] == "direct_page_vision"


def test_candidate_array_limits_are_enforced_at_the_provider_boundary() -> None:
    from jsonschema import Draft202012Validator

    value = candidate()
    identity = value["paper_identity_candidates"][0]
    value["paper_identity_candidates"] = [identity] * 100
    validator = Draft202012Validator(intake_visual_candidate_schema())
    assert list(validator.iter_errors(value)) == []
    value["paper_identity_candidates"].append(identity)
    errors = list(validator.iter_errors(value))
    assert any(list(error.path) == ["paper_identity_candidates"] for error in errors)

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopVisualImportReceipt,
    DesktopWorkbenchFacade,
    _visual_failure_codes_from_blockers,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.intake_batches_v2 import (
    CandidateCAS,
    RenderedPixelPage,
)
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ProbeTransportResponse,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
)


def _png(color: str = "white") -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (24, 32), color)
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((4, 5, 19, 25), outline="black", width=1)
    drawing.line((7, 11, 16, 11), fill="black", width=1)
    drawing.line((7, 17, 14, 17), fill="black", width=1)
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def _docx(*, hybrid: bool = False) -> bytes:
    visual = (
        '<w:p><w:r><w:drawing><a:graphic xmlns:a="http://schemas.openxmlformats.org/'
        'drawingml/2006/main"><a:graphicData/></a:graphic></w:drawing></w:r></w:p>'
        if hybrid
        else ""
    )
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>1. 下列说法正确的是</w:t></w:r></w:p>
        <w:p><w:r><w:t>A. 测试候选</w:t></w:r></w:p>
        {visual}
      </w:body>
    </w:document>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        package.writestr("word/document.xml", document)
    return output.getvalue()


class StaticV2Renderer:
    def render(
        self, _source_file: Any, *, source_role: str
    ) -> tuple[RenderedPixelPage]:
        raw = _png(
            {"question": "white", "answer": "blue", "handout": "green"}[source_role]
        )
        return (
            RenderedPixelPage(
                pixels=raw,
                mime_type="image/png",
                width=24,
                height=32,
                render_recipe_sha256=hashlib.sha256(
                    f"fixture:{source_role}".encode()
                ).hexdigest(),
            ),
        )


class FakeProviderStore:
    def __init__(self, *, configured: bool = True, revision: str = "REV-1") -> None:
        self.configured = configured
        self.revision = revision
        self.borrow_calls = 0

    def list_metadata(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        return [
            {
                "profile_id": "vision",
                "revision": self.revision,
                "credential_state": "configured",
                "effective_capabilities": ["text", "vision", "structured_output"],
            }
        ]

    @contextmanager
    def borrow_invocation_context(
        self, profile_id: str, *, expected_revision: str
    ) -> Iterator[ModelProviderProbeContext]:
        assert profile_id == "vision"
        assert expected_revision == self.revision
        self.borrow_calls += 1
        yield ModelProviderProbeContext(
            profile_id=profile_id,
            provider_id="openai_compatible",
            model_id="vision-fixture",
            base_url_policy="openai_compatible_public_https_v1",
            base_url="https://example.com/v1",
            revision=self.revision,
            api_key="fixture-secret",
            provider_kind="openai_compatible",
            api_style="responses",
            local_endpoint_policy="deny",
        )


class FakeVisualTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.last_schema: dict[str, Any] | None = None
        self.review_schema: dict[str, Any] | None = None

    def send(
        self,
        request: Any,
        *,
        cancel_event: Any,
        deadline_monotonic: float,
    ) -> ProbeTransportResponse:
        del cancel_event, deadline_monotonic
        self.calls += 1
        body = json.loads(request.body)
        prompt = body["input"][0]["content"][0]["text"]
        metadata = json.loads(prompt.split("\n", 1)[1])
        if body["text"]["format"]["name"] == "shchem_visual_crop_review_v1":
            self.review_schema = body["text"]["format"]["schema"]
            return self._response({
                "request_digest": metadata["request_digest"],
                "checks": [{
                    "evidence_id": check["evidence_id"],
                    "page_sha256": check["page_sha256"],
                    "crop_sha256": check["crop_sha256"],
                    "status": "pass", "reason": "合成像素复核", "issue_codes": [],
                } for check in metadata["checks"]],
            })
        self.last_schema = body["text"]["format"]["schema"]
        page = metadata["page_manifests"][0]
        suffix = page["page_sha256"][:12]
        evidence_id = f"EV-DESKTOP-{suffix}"
        evidence = {
            "evidence_id": evidence_id,
            "source_file_id": page["source_file_id"],
            "source_role": metadata["source_role"],
            "page_number": page["page_number"],
            "page_sha256": page["page_sha256"],
            "bbox": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9},
        }
        themes = []
        paper_identity = None
        if metadata["source_role"] != "answer":
            paper_identity = {
                "title": "桌面视觉导入非空候选",
                "source_year": "unknown",
                "source_region_or_school": "页面未显示",
                "paper_type": "教师资料",
                "evidence_refs": [evidence_id],
            }
            themes = [
                {
                    "theme_big_question_id": f"THEME-DESKTOP-{suffix}",
                    "theme_number": "一",
                    "title": "页面可见主题",
                    "context": "仅用于桌面两阶段非空视觉合同测试。",
                    "sequence_in_paper": 1,
                    "fragment_position": "complete",
                    "shared_materials": [],
                    "visual_objects": [],
                    "dependency_edges": [],
                    "printed_questions": [
                        {
                            "printed_question_id": f"PRINTED-DESKTOP-{suffix}",
                            "question_number": "1",
                            "sequence_in_theme": 1,
                            "stem": "页面可见的合成测试题干。",
                            "options": [],
                            "response_requirements": "",
                            "chemical_expressions": [],
                            "shared_material_refs": [],
                            "visual_object_refs": [],
                            "atomic_parts": [
                                {
                                    "atomic_part_id": f"ATOMIC-DESKTOP-{suffix}",
                                    "part_label": None,
                                    "sequence_in_printed": 1,
                                    "stem": "页面可见的合成测试题干。",
                                    "options": [],
                                    "response_requirements": "填写页面要求的答案。",
                                    "chemical_expressions": [],
                                    "visual_object_refs": [],
                                    "evidence_refs": [evidence_id],
                                }
                            ],
                            "evidence_refs": [evidence_id],
                        }
                    ],
                    "evidence_refs": [evidence_id],
                }
            ]
        fragment = {
            "schema_version": "shchem.intake-batch-visual-fragment.v2",
            "shard_id": metadata["shard_id"],
            "source_role": metadata["source_role"],
            "input_mode": "direct_original_or_rendered_page_pixels",
            "source_text_layer_used": False,
            "fallback_used": False,
            "evidence": [evidence],
            "paper_identity": paper_identity,
            "theme_fragments": themes,
            "answer_candidates": [],
            "warnings": [],
        }
        return self._response(fragment)

    @staticmethod
    def _response(payload: dict[str, Any]) -> ProbeTransportResponse:
        response = {
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "output_text": json.dumps(payload, ensure_ascii=False),
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        }
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=json.dumps(response, ensure_ascii=False).encode(),
            latency_ms=1,
            model_invoked=True,
        )


class FailingVisualTransport(FakeVisualTransport):
    def send(
        self,
        request: Any,
        *,
        cancel_event: Any,
        deadline_monotonic: float,
    ) -> ProbeTransportResponse:
        del request, cancel_event, deadline_monotonic
        self.calls += 1
        return ProbeTransportResponse(
            http_status=503,
            content_type="application/json",
            content_encoding=None,
            body=b"{}",
            latency_ms=1,
            model_invoked=True,
        )


@pytest.fixture
def desktop_paths(tmp_path: Path) -> DesktopPaths:
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    return DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )


def _facade(
    paths: DesktopPaths,
    provider: FakeProviderStore,
    *,
    transport: FakeVisualTransport | None = None,
) -> DesktopWorkbenchFacade:
    unused = object()
    return DesktopWorkbenchFacade(
        paths,
        theme_reader=unused,
        supplemental_reader=unused,
        curriculum_reader=unused,
        search_reader=unused,
        provider_store=provider,
        state_store=DesktopStateStore(paths.state_root),
        paper_export_jobs=unused,
        visual_import_transport=transport,
        visual_import_renderer=StaticV2Renderer(),
    )


def test_save_orders_three_roles_and_rejects_answer_only(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    files = {}
    for name, color in (
        ("q1.png", "white"),
        ("q2.png", "black"),
        ("a1.png", "blue"),
        ("a2.png", "red"),
        ("h1.png", "green"),
    ):
        path = tmp_path / name
        path.write_bytes(_png(color))
        files[name] = path
    facade = _facade(desktop_paths, FakeProviderStore(configured=False))

    receipt = facade.save_visual_import_batch(
        question_files=(files["q1.png"], files["q2.png"]),
        answer_files=(files["a1.png"], files["a2.png"]),
        handout_files=(files["h1.png"],),
        source_type="上海教师自有资料",
    )

    assert [(item.role, item.order_index) for item in receipt.sources] == [
        ("question", 1),
        ("question", 2),
        ("answer", 1),
        ("answer", 2),
        ("handout", 1),
    ]
    assert receipt.status == "pending"
    assert receipt.candidate_only is True
    assert receipt.central_question_bank_write is False
    assert (
        len(list((desktop_paths.state_root / "visual-import-v2" / "sources").iterdir()))
        == 5
    )
    with pytest.raises(DesktopFacadeError, match="题目或讲义") as caught:
        facade.save_visual_import_batch(
            answer_files=(files["a1.png"],), source_type="答案"
        )
    assert caught.value.code == "question_source_required"


def test_offline_save_has_zero_provider_calls_three_states_and_deduped_native_lane(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    native = tmp_path / "native.docx"
    hybrid = tmp_path / "hybrid.docx"
    visual = tmp_path / "visual.png"
    native.write_bytes(_docx())
    hybrid.write_bytes(_docx(hybrid=True))
    visual.write_bytes(_png())
    provider = FakeProviderStore()
    transport = FakeVisualTransport()
    facade = _facade(desktop_paths, provider, transport=transport)

    first = facade.save_visual_import_batch(
        handout_files=(native, hybrid, visual), source_type="一轮复习讲义"
    )
    second = facade.save_visual_import_batch(
        handout_files=(native, hybrid, visual), source_type="一轮复习讲义"
    )

    assert [item.import_state for item in first.sources] == [
        "native_text_complete",
        "hybrid_visual_required",
        "visual_only_required",
    ]
    assert first.batch_id == second.batch_id
    assert first.visual_status == "awaiting_visual_provider"
    assert first.native_quick_count == 1
    assert transport.calls == 0
    assert provider.borrow_calls == 0
    inventory = desktop_paths.state_root / "word-handout-import" / "documents"
    assert len(list(inventory.glob("*.json"))) == 1
    assert (
        len(list((desktop_paths.state_root / "visual-import-v2" / "sources").iterdir()))
        == 3
    )


def test_cross_restart_resume_requires_current_profile_and_explicit_confirmation(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    source = tmp_path / "question.png"
    source.write_bytes(_png())
    first = _facade(desktop_paths, FakeProviderStore(configured=False))
    saved = first.save_visual_import_batch(
        question_files=(source,), source_type="上海试题候选"
    )
    provider = FakeProviderStore()
    transport = FakeVisualTransport()
    restarted = _facade(desktop_paths, provider, transport=transport)

    assert [
        item.batch_id for item in restarted.list_resumable_visual_import_batches()
    ] == [saved.batch_id]
    with pytest.raises(DesktopFacadeError, match="教师明确确认"):
        restarted.run_saved_visual_import_batch(
            batch_id=saved.batch_id,
            profile_id="vision",
            expected_profile_revision="REV-1",
            teacher_confirmed=False,  # type: ignore[arg-type]
        )
    preview = restarted.preview_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
    )
    preview_tokens = {
        "egress_preview_id": preview["preview_id"],
        "egress_revision": preview["revision"],
    }
    with pytest.raises(DesktopFacadeError, match="配置已变化"):
        restarted.run_saved_visual_import_batch(
            batch_id=saved.batch_id,
            profile_id="vision",
            expected_profile_revision="REV-OLD",
            teacher_confirmed=True,
            **preview_tokens,
        )
    assert transport.calls == 0
    assert provider.borrow_calls == 0

    completed = restarted.run_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
        teacher_confirmed=True,
        **preview_tokens,
    )

    assert isinstance(completed, DesktopVisualImportReceipt)
    assert completed.status == "candidate_ready_for_review"
    assert completed.visual_status == "completed"
    assert completed.candidate_only is True
    assert completed.central_question_bank_write is False
    assert transport.calls == 2
    assert provider.borrow_calls == 1
    assert transport.last_schema is not None
    assert transport.review_schema is not None
    assert "$defs" in transport.last_schema
    assert (
        "curriculum" not in transport.last_schema["$defs"]["atomic_part"]["properties"]
    )
    candidate = CandidateCAS.open(
        desktop_paths.state_root / "visual-import-v2" / "candidates" / saved.batch_id
    ).snapshot()["candidate"]
    theme = candidate["paper"]["theme_big_questions"][0]
    atomic = theme["printed_questions"][0]["atomic_parts"][0]
    assert atomic["classification"]["item_type"] == "unknown"
    assert atomic["curriculum"]["mapping_status"] == "unknown"
    assert atomic["cognitive_difficulty"]["status"] == "unknown"
    assert restarted.list_resumable_visual_import_batches() == ()
    assert not any(
        key in json.dumps(completed.as_dict(), ensure_ascii=False).casefold()
        for key in ("api_key", "source_sha256", "manifest_path", "relative_path")
    )
    with pytest.raises(DesktopFacadeError, match="不能重复覆盖"):
        restarted.run_saved_visual_import_batch(
            batch_id=saved.batch_id,
            profile_id="vision",
            expected_profile_revision="REV-1",
            teacher_confirmed=True,
            **preview_tokens,
        )
    assert transport.calls == 2


def test_missing_profile_and_cancel_are_chinese_and_do_not_call_transport(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    source = tmp_path / "question.png"
    source.write_bytes(_png())
    empty = FakeProviderStore(configured=False)
    transport = FakeVisualTransport()
    configured = _facade(desktop_paths, FakeProviderStore(), transport=transport)
    saved = configured.save_visual_import_batch(
        question_files=(source,), source_type="教师资料"
    )
    preview = configured.preview_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
    )
    facade = _facade(desktop_paths, empty, transport=transport)

    with pytest.raises(DesktopFacadeError) as missing:
        facade.run_saved_visual_import_batch(
            batch_id=saved.batch_id,
            profile_id="vision",
            expected_profile_revision="REV-1",
            teacher_confirmed=True,
            egress_preview_id=preview["preview_id"],
            egress_revision=preview["revision"],
        )
    assert re.search(r"[\u4e00-\u9fff]", missing.value.message_zh)
    assert transport.calls == 0

    with pytest.raises(DesktopFacadeError) as cancelled:
        configured.run_saved_visual_import_batch(
            batch_id=saved.batch_id,
            profile_id="vision",
            expected_profile_revision="REV-1",
            teacher_confirmed=True,
            egress_preview_id=preview["preview_id"],
            egress_revision=preview["revision"],
            should_cancel=lambda: True,
        )
    assert cancelled.value.code == "cancelled"
    assert re.search(r"[\u4e00-\u9fff]", cancelled.value.message_zh)
    assert transport.calls == 0


def test_saved_batch_rebuilds_only_from_archive_and_detects_changed_bytes(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    source = tmp_path / "question.png"
    raw = _png()
    source.write_bytes(raw)
    facade = _facade(desktop_paths, FakeProviderStore(), transport=FakeVisualTransport())
    saved = facade.save_visual_import_batch(
        question_files=(source,), source_type="教师资料"
    )
    preview = facade.preview_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
    )
    source.unlink()
    archive = desktop_paths.state_root / "visual-import-v2" / "sources"
    archived_source = next(archive.iterdir())
    archived_source.write_bytes(_png("black"))
    with pytest.raises(DesktopFacadeError, match="归档不完整或已变化") as caught:
        facade.run_saved_visual_import_batch(
            batch_id=saved.batch_id,
            profile_id="vision",
            expected_profile_revision="REV-1",
            teacher_confirmed=True,
            egress_preview_id=preview["preview_id"],
            egress_revision=preview["revision"],
        )
    assert caught.value.code == "visual_import_source_archive_invalid"


def test_failed_visual_run_remains_resumable(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    source = tmp_path / "question.png"
    source.write_bytes(_png())
    transport = FailingVisualTransport()
    facade = _facade(desktop_paths, FakeProviderStore(), transport=transport)
    saved = facade.save_visual_import_batch(
        question_files=(source,), source_type="教师资料"
    )
    preview = facade.preview_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
    )

    failed = facade.run_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
        teacher_confirmed=True,
        egress_preview_id=preview["preview_id"],
        egress_revision=preview["revision"],
    )

    assert failed.status == "failed"
    assert failed.visual_status == "failed"
    assert re.search(r"[\u4e00-\u9fff]", failed.message_zh)
    assert [
        item.batch_id for item in facade.list_resumable_visual_import_batches()
    ] == [saved.batch_id]
    assert transport.calls == 1


@pytest.mark.parametrize(
    "effective",
    [[], (), None, "vision structured_output", {}, ["vision"], ["structured_output"],
     ["vision", "structured_output", None]],
    ids=["empty-list", "empty-tuple", "null", "string", "mapping", "no-structured",
         "no-vision", "invalid-member"],
)
def test_effective_capabilities_block_preview_and_run_before_transport(
    desktop_paths: DesktopPaths, tmp_path: Path, monkeypatch, effective,
) -> None:
    provider = FakeProviderStore()
    transport = FakeVisualTransport()
    facade = _facade(desktop_paths, provider, transport=transport)
    source = tmp_path / "synthetic.png"
    source.write_bytes(_png())
    saved = facade.save_visual_import_batch(question_files=(source,), source_type="教师资料")
    metadata = provider.list_metadata()[0]
    metadata.update(effective_capabilities=effective, capabilities=["vision", "structured_output"])
    monkeypatch.setattr(provider, "list_metadata", lambda: [metadata])

    with pytest.raises(DesktopFacadeError) as preview_error:
        facade.preview_saved_visual_import_batch(
            batch_id=saved.batch_id, profile_id="vision", expected_profile_revision="REV-1",
        )
    assert preview_error.value.code == "visual_profile_missing"
    with pytest.raises(DesktopFacadeError) as run_error:
        facade.run_saved_visual_import_batch(
            batch_id=saved.batch_id, profile_id="vision", expected_profile_revision="REV-1",
            teacher_confirmed=True, egress_preview_id="synthetic-preview",
            egress_revision="synthetic-revision",
        )
    assert run_error.value.code == "visual_profile_missing"
    assert provider.borrow_calls == 0
    assert transport.calls == 0


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"capabilities": ["vision", "structured_output"]}, ("vision", "structured_output")),
        ({"capabilities": ("vision", "structured_output")}, ("vision", "structured_output")),
        ({"effective_capabilities": ("vision", "structured_output"), "capabilities": []}, ("vision", "structured_output")),
        ({"effective_capabilities": [], "capabilities": ["vision", "structured_output"]}, ()),
        ({"effective_capabilities": None, "capabilities": ["vision", "structured_output"]}, ()),
        ({"capabilities": None}, ()),
        ({}, ()),
    ],
)
def test_profile_summary_and_visual_gate_share_authoritative_capabilities(
    desktop_paths: DesktopPaths, monkeypatch, fields, expected,
) -> None:
    provider = FakeProviderStore()
    metadata = provider.list_metadata()[0]
    metadata.pop("effective_capabilities")
    metadata.update(fields)
    monkeypatch.setattr(provider, "list_metadata", lambda: [metadata])
    transport = FakeVisualTransport()
    facade = _facade(desktop_paths, provider, transport=transport)
    assert facade.list_provider_profiles()[0].capabilities == expected
    if {"vision", "structured_output"}.issubset(expected):
        assert facade._visual_import_profile("vision", "REV-1") == metadata
    else:
        with pytest.raises(DesktopFacadeError, match="视觉结构化能力"):
            facade._visual_import_profile("vision", "REV-1")
    assert provider.borrow_calls == 0
    assert transport.calls == 0


@pytest.mark.parametrize("code", [
    "visual_crop_review_failed", "visual_crop_review_invalid", "visual_crop_review_limit",
    "visual_crop_review_cancelled", "visual_crop_review_blank_crop", "visual_crop_review_schema_unsupported",
])
def test_crop_review_failure_codes_keep_safe_actionable_guidance(code):
    private_message = "synthetic-private-exception-and-key-do-not-display"
    codes = _visual_failure_codes_from_blockers([
        {"code": code, "message": private_message, "api_key": private_message},
    ], failed=True)
    assert codes == (code,)
    message = DesktopWorkbenchFacade._visual_import_message("failed", "failed", codes)
    assert "裁片复核未" in message
    assert "未作为完成候选入库" in message
    assert "回到原页" in message
    assert "不会自动重试" in message
    assert "检查模型设置和网络后再手动重试" not in message
    assert private_message not in message
    assert code not in message
    if code == "visual_crop_review_blank_crop":
        assert "空白裁片" in message

from __future__ import annotations

import hashlib
import io
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_visual_egress import (
    DesktopVisualEgressService,
    FrozenPageRenderer,
    VisualEgressError,
)
from integrations.deeptutor_shchem_v1.desktop_visual_import_v2 import (
    DesktopSourceFile,
)
from integrations.deeptutor_shchem_v1.intake_batches_v2 import (
    IntakeBatchFile,
    RenderedPixelPage,
)


def _png(color: tuple[int, int, int] = (20, 80, 220)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(stream, format="PNG", optimize=False)
    return stream.getvalue()


class _Renderer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.raw = _png()

    def render(self, source_file: IntakeBatchFile, *, source_role: str):
        self.calls.append((source_file.filename, source_role))
        return (
            RenderedPixelPage(
                pixels=self.raw,
                mime_type="image/png",
                width=32,
                height=24,
                render_recipe_sha256=hashlib.sha256(b"synthetic-render-v1").hexdigest(),
            ),
        )


class _PreviewFacade:
    def __init__(self) -> None:
        self.renderer = _Renderer()
        self.profile_calls: list[tuple[str, str]] = []
        self.provider_borrows = 0
        self.source = DesktopSourceFile(
            role="question",
            order_index=1,
            filename="合成题目.pdf",
            mime_type="application/pdf",
            content=b"synthetic-pdf-source",
            group_id="synthetic-group",
            source_file_id="QUESTION-1",
        )
        self.descriptor = {
            "batch_id": "DESKTOPBATCH-" + "a" * 32,
            "status": "pending",
            "visual_status": "awaiting_teacher_confirmation",
            "source_type": "合成视觉导入",
        }

    def _saved_visual_import_batch(self, batch_id: str):
        assert batch_id == self.descriptor["batch_id"]
        return dict(self.descriptor)

    def _visual_import_profile(self, profile_id: str, revision: str):
        self.profile_calls.append((profile_id, revision))
        return {
            "profile_id": profile_id,
            "revision": revision,
            "provider_name": "合成视觉服务",
            "model_id": "synthetic-vision",
            "base_url": "https://example.invalid/v1",
            "credential_state": "configured",
            "effective_capabilities": ["vision", "structured_output"],
        }

    def _restore_visual_import_sources(self, _descriptor):
        return (self.source,)

    @property
    def _visual_import_renderer(self):
        return self.renderer


def test_preview_freezes_rendered_pages_and_reader_returns_exact_bytes():
    facade = _PreviewFacade()
    service = DesktopVisualEgressService(facade)
    progress: list[dict[str, Any]] = []

    plan = service.preview(
        batch_id=facade.descriptor["batch_id"],
        profile_id="profile-synthetic",
        expected_profile_revision="revision-synthetic",
        progress_callback=progress.append,
    )

    assert facade.profile_calls == [("profile-synthetic", "revision-synthetic")]
    assert facade.renderer.calls == [("合成题目.pdf", "question")]
    assert [item["stage"] for item in progress] == [
        "planned",
        "rendered_source",
        "completed",
    ]
    assert plan["batch_id"] == facade.descriptor["batch_id"]
    assert plan["profile_id"] == "profile-synthetic"
    assert plan["profile_revision"] == "revision-synthetic"
    assert plan["model_label"].startswith("合成视觉服务 / synthetic-vision")
    assert "冻结" in plan["confirmation_text"]
    assert "学校授权" in plan["confirmation_text"]
    assert "个人信息" in plan["confirmation_text"]
    assert len(plan["pages"]) == 1
    page = plan["pages"][0]
    assert {
        "page_id",
        "source_name",
        "source_role",
        "page_number",
        "mime_type",
        "width",
        "height",
        "sha256",
    } <= set(page)
    assert page["source_name"] == "合成题目.pdf"
    assert page["source_role"] == "question"
    assert page["mime_type"] == "image/png"
    assert service.image(plan["preview_id"], plan["revision"], page["page_id"]) == facade.renderer.raw
    with pytest.raises(VisualEgressError, match="预览已失效"):
        service.image(plan["preview_id"], "wrong-revision", page["page_id"])
    assert service.discard(plan["preview_id"]) is True
    assert service.discard(plan["preview_id"]) is False
    with pytest.raises(VisualEgressError, match="预览已失效"):
        service.image(plan["preview_id"], plan["revision"], page["page_id"])
    assert facade.provider_borrows == 0


def test_frozen_renderer_rejects_source_change_and_replays_same_page():
    facade = _PreviewFacade()
    service = DesktopVisualEgressService(facade)
    plan = service.preview(
        batch_id=facade.descriptor["batch_id"],
        profile_id="profile-synthetic",
        expected_profile_revision="revision-synthetic",
    )
    renderer = service.frozen_renderer(
        batch_id=facade.descriptor["batch_id"],
        profile_id="profile-synthetic",
        expected_profile_revision="revision-synthetic",
        preview_id=plan["preview_id"],
        revision=plan["revision"],
        sources=(facade.source,),
    )
    assert isinstance(renderer, FrozenPageRenderer)
    pages = renderer.render(
        IntakeBatchFile(
            filename=facade.source.filename,
            mime_type=facade.source.mime_type,
            content=facade.source.content,
            source_file_id=facade.source.effective_source_file_id,
        ),
        source_role="question",
    )
    assert pages[0].pixels == facade.renderer.raw
    assert hashlib.sha256(pages[0].pixels).hexdigest() == plan["pages"][0]["sha256"]

    changed = DesktopSourceFile(
        role=facade.source.role,
        order_index=facade.source.order_index,
        filename=facade.source.filename,
        mime_type=facade.source.mime_type,
        content=b"different-source-bytes",
        group_id=facade.source.group_id,
        source_file_id=facade.source.source_file_id,
    )
    with pytest.raises(VisualEgressError, match="原文件.*变化"):
        renderer.validate((changed,))


def test_preview_checks_cancellation_between_sources():
    facade = _PreviewFacade()
    second = DesktopSourceFile(
        role="question",
        order_index=2,
        filename="第二页.pdf",
        mime_type="application/pdf",
        content=b"second-source",
        group_id="synthetic-group",
        source_file_id="QUESTION-2",
    )
    facade._restore_visual_import_sources = lambda _descriptor: (
        facade.source,
        second,
    )
    calls = 0

    def cancel_after_first_source():
        nonlocal calls
        calls += 1
        return calls >= 3

    with pytest.raises(VisualEgressError, match="预览已取消"):
        DesktopVisualEgressService(facade).preview(
            batch_id=facade.descriptor["batch_id"],
            profile_id="profile-synthetic",
            expected_profile_revision="revision-synthetic",
            should_cancel=cancel_after_first_source,
        )
    assert facade.renderer.calls == [("合成题目.pdf", "question")]


def test_preview_orders_interleaved_sources_like_coordinator_send_order():
    facade = _PreviewFacade()
    question_two = DesktopSourceFile(
        role="question",
        order_index=2,
        filename="第二道题.pdf",
        mime_type="application/pdf",
        content=b"second-question-source",
        group_id="synthetic-group",
        source_file_id="QUESTION-2",
    )
    answer = DesktopSourceFile(
        role="answer",
        order_index=1,
        filename="答案.pdf",
        mime_type="application/pdf",
        content=b"answer-source",
        group_id="synthetic-group",
        source_file_id="ANSWER-1",
    )
    handout = DesktopSourceFile(
        role="handout",
        order_index=1,
        filename="讲义.pdf",
        mime_type="application/pdf",
        content=b"handout-source",
        group_id="synthetic-group",
        source_file_id="HANDOUT-1",
    )
    facade._restore_visual_import_sources = lambda _descriptor: (
        facade.source,
        answer,
        question_two,
        handout,
    )

    plan = DesktopVisualEgressService(facade).preview(
        batch_id=facade.descriptor["batch_id"],
        profile_id="profile-synthetic",
        expected_profile_revision="revision-synthetic",
    )

    assert facade.renderer.calls == [
        ("合成题目.pdf", "question"),
        ("第二道题.pdf", "question"),
        ("答案.pdf", "answer"),
        ("讲义.pdf", "handout"),
    ]
    assert [
        (page["source_role"], page["source_order"])
        for page in plan["pages"]
    ] == [
        ("question", 1),
        ("question", 2),
        ("answer", 1),
        ("handout", 1),
    ]


def test_preview_rejects_cumulative_frozen_bytes_without_preparation_image_limit(
    monkeypatch,
):
    facade = _PreviewFacade()
    monkeypatch.setattr(
        "integrations.deeptutor_shchem_v1.desktop_visual_egress.MAX_EGRESS_BYTES",
        len(facade.renderer.raw) - 1,
    )
    with pytest.raises(VisualEgressError, match="512MiB"):
        DesktopVisualEgressService(facade).preview(
            batch_id=facade.descriptor["batch_id"],
            profile_id="profile-synthetic",
            expected_profile_revision="revision-synthetic",
        )


def test_facade_preview_and_image_calls_use_safe_chinese_wrapper(monkeypatch):
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    class Port:
        def preview(self, **kwargs):
            calls.append(("preview", (), kwargs))
            return {"preview_id": "p", "revision": "r"}

        def image(self, *args):
            calls.append(("image", args, {}))
            return b"pixels"

        def discard(self, *args):
            calls.append(("discard", args, {}))
            return True

    facade._visual_egress_service_instance = lambda: Port()
    assert facade.preview_saved_visual_import_batch(
        batch_id="B",
        profile_id="P",
        expected_profile_revision="R",
    ) == {"preview_id": "p", "revision": "r"}
    assert facade.visual_import_egress_image("p", "r", "page") == b"pixels"
    assert facade.discard_visual_import_egress("p") is True
    assert calls[0][0] == "preview"
    assert calls[1] == ("image", ("p", "r", "page"), {})
    assert calls[2] == ("discard", ("p",), {})


def test_run_saved_visual_import_batch_injects_frozen_renderer_before_provider():
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    source = DesktopSourceFile(
        role="question",
        order_index=1,
        filename="题目.pdf",
        mime_type="application/pdf",
        content=b"source",
        group_id="group",
        source_file_id="QUESTION-1",
    )
    descriptor = {
        "batch_id": "DESKTOPBATCH-" + "b" * 32,
        "status": "pending",
        "source_type": "合成批次",
    }
    frozen = object()
    renderer_args: list[object] = []
    process_calls: list[dict[str, Any]] = []

    class EgressPort:
        def frozen_renderer(self, **kwargs):
            assert kwargs["sources"] == (source,)
            renderer_args.append(kwargs["revision"])
            return frozen

    class Coordinator:
        def plan(self, request):
            return SimpleNamespace(batch_id=request.batch_id)

        def process(self, request, **kwargs):
            process_calls.append({"request": request, **kwargs})
            return object()

    class Providers:
        @contextmanager
        def borrow_invocation_context(self, profile_id, *, expected_revision):
            assert profile_id == "profile"
            assert expected_revision == "profile-revision"
            yield object()

    facade._saved_visual_import_batch = lambda _batch_id: descriptor
    facade._visual_import_profile = lambda _profile_id, _revision: {"profile_id": "profile"}
    facade._restore_visual_import_sources = lambda _descriptor: (source,)
    facade._visual_egress_service_instance = lambda: EgressPort()
    facade._providers = Providers()
    facade._visual_import_transport = object()
    facade._visual_import_coordinator = lambda *, visual_provider=None, renderer=None: (
        renderer_args.append(renderer) or Coordinator()
    )
    facade._save_visual_import_descriptor = lambda **_kwargs: "receipt"

    result = facade.run_saved_visual_import_batch(
        batch_id=descriptor["batch_id"],
        profile_id="profile",
        expected_profile_revision="profile-revision",
        teacher_confirmed=True,
        egress_preview_id="preview",
        egress_revision="preview-revision",
    )
    assert result == "receipt"
    assert renderer_args == ["preview-revision", frozen]
    assert process_calls[0]["visual_confirmation"] is True


def test_run_saved_visual_import_batch_requires_both_preview_tokens():
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    with pytest.raises(DesktopFacadeError, match="预览不完整"):
        facade.run_saved_visual_import_batch(
            batch_id="B",
            profile_id="P",
            expected_profile_revision="R",
            teacher_confirmed=True,
            egress_preview_id="preview-only",
        )

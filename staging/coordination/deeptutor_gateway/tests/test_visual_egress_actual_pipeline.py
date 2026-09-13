from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.intake_batches_v2 import RenderedPixelPage
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ProbeTransportResponse,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_visual_import_facade import (
    FakeProviderStore,
    FakeVisualTransport,
    _docx,
)


def _png(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (37, 29), color).save(output, format="PNG", optimize=False)
    return output.getvalue()


class CountingRenderer:
    """Synthetic renderer whose call count exposes accidental rerendering."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def render(self, source_file: Any, *, source_role: str):
        key = (source_role, str(source_file.filename))
        self.calls.append(key)
        seed = hashlib.sha256("/".join(key).encode("utf-8")).digest()
        raw = _png((seed[0], seed[1], seed[2]))
        return (
            RenderedPixelPage(
                pixels=raw,
                mime_type="image/png",
                width=37,
                height=29,
                render_recipe_sha256=hashlib.sha256(
                    f"fixture-render-v1:{key}".encode()
                ).hexdigest(),
            ),
        )


class CapturingVisualTransport(FakeVisualTransport):
    """Reuse the existing valid fake response while retaining outbound pixels."""

    def __init__(self) -> None:
        super().__init__()
        self.outbound: list[dict[str, Any]] = []

    def send(
        self,
        request: Any,
        *,
        cancel_event: Any,
        deadline_monotonic: float,
    ) -> ProbeTransportResponse:
        body = json.loads(request.body)
        content = body["input"][0]["content"]
        prompt = next(
            item["text"] for item in content if item.get("type") == "input_text"
        )
        metadata = json.loads(prompt.split("\n", 1)[1])
        image_parts = [
            item for item in content if item.get("type") == "input_image"
        ]
        manifests = metadata["page_manifests"]
        assert len(image_parts) == len(manifests)
        pages = []
        for manifest, image_part in zip(manifests, image_parts, strict=True):
            data_url = image_part["image_url"]
            _header, encoded = data_url.split(",", 1)
            pages.append(
                {
                    "source_role": metadata["source_role"],
                    "source_file_id": manifest["source_file_id"],
                    "page_number": manifest["page_number"],
                    "pixels": base64.b64decode(encoded, validate=True),
                }
            )
        self.outbound.append(
            {
                "source_role": metadata["source_role"],
                "pages": pages,
            }
        )
        return super().send(
            request,
            cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic,
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
    renderer: CountingRenderer,
    transport: CapturingVisualTransport,
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
        visual_import_renderer=renderer,
    )


def _run_with_egress(
    facade: DesktopWorkbenchFacade,
    batch_id: str,
    plan: dict[str, Any],
):
    return facade.run_saved_visual_import_batch(
        batch_id=batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
        teacher_confirmed=True,
        egress_preview_id=plan["preview_id"],
        egress_revision=plan["revision"],
    )


def _planned_pixels(
    facade: DesktopWorkbenchFacade, plan: dict[str, Any]
) -> dict[tuple[str, str, int], bytes]:
    return {
        (
            str(page["source_role"]),
            str(page["source_file_id"]),
            int(page["page_number"]),
        ): facade.visual_import_egress_image(
            plan["preview_id"], plan["revision"], page["page_id"]
        )
        for page in plan["pages"]
    }


def _outbound_pixels(
    transport: CapturingVisualTransport,
) -> dict[tuple[str, str, int], bytes]:
    return {
        (
            str(page["source_role"]),
            str(page["source_file_id"]),
            int(page["page_number"]),
        ): page["pixels"]
        for request in transport.outbound
        for page in request["pages"]
    }


@pytest.mark.parametrize("source_kind", ("pdf", "docx"))
def test_real_facade_freezes_pdf_and_docx_pixels_once_before_run(
    desktop_paths: DesktopPaths, tmp_path: Path, source_kind: str
) -> None:
    pdf = tmp_path / "题目.pdf"
    docx = tmp_path / "讲义.docx"
    pdf.write_bytes(b"%PDF-synthetic-question")
    docx.write_bytes(_docx(hybrid=True))
    renderer = CountingRenderer()
    transport = CapturingVisualTransport()
    provider = FakeProviderStore()
    facade = _facade(desktop_paths, provider, renderer, transport)

    if source_kind == "pdf":
        saved = facade.save_visual_import_batch(
            question_files=(pdf,), source_type="合成视觉资料"
        )
        expected_call = ("question", "题目.pdf")
    else:
        saved = facade.save_visual_import_batch(
            handout_files=(docx,), source_type="合成视觉资料"
        )
        expected_call = ("handout", "讲义.docx")
    assert renderer.calls == []
    assert transport.calls == 0
    assert provider.borrow_calls == 0

    plan = facade.preview_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
    )
    assert renderer.calls == [expected_call]
    assert transport.calls == 0
    assert provider.borrow_calls == 0
    expected = _planned_pixels(facade, plan)

    completed = _run_with_egress(facade, saved.batch_id, plan)

    assert completed.status == "candidate_ready_for_review"
    assert completed.visual_status == "completed"
    assert renderer.calls == [expected_call]
    assert provider.borrow_calls == 1
    assert transport.calls == len(plan["pages"])
    assert _outbound_pixels(transport) == expected


def test_real_facade_direct_image_identity_path_sends_original_bytes(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    original = _png((12, 170, 91))
    image = tmp_path / "原图.png"
    image.write_bytes(original)
    renderer = CountingRenderer()
    transport = CapturingVisualTransport()
    facade = _facade(desktop_paths, FakeProviderStore(), renderer, transport)

    saved = facade.save_visual_import_batch(
        question_files=(image,), source_type="合成原图资料"
    )
    plan = facade.preview_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
    )

    assert renderer.calls == []
    page = plan["pages"][0]
    assert page["source_role"] == "question"
    assert page["page_number"] == 1
    assert page["sha256"] == hashlib.sha256(original).hexdigest()
    assert facade.visual_import_egress_image(
        plan["preview_id"], plan["revision"], page["page_id"]
    ) == original

    completed = _run_with_egress(facade, saved.batch_id, plan)

    assert completed.status == "candidate_ready_for_review"
    assert renderer.calls == []
    assert transport.calls == 1
    assert _outbound_pixels(transport) == {
        ("question", page["source_file_id"], 1): original
    }


def test_source_tampering_after_preview_fails_closed_before_transport(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    source = tmp_path / "题目.pdf"
    source.write_bytes(b"%PDF-original")
    renderer = CountingRenderer()
    transport = CapturingVisualTransport()
    facade = _facade(desktop_paths, FakeProviderStore(), renderer, transport)
    saved = facade.save_visual_import_batch(
        question_files=(source,), source_type="合成资料"
    )
    plan = facade.preview_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
    )
    descriptor = facade._saved_visual_import_batch(saved.batch_id)
    metadata = descriptor["sources"][0]
    archive_path = (
        desktop_paths.state_root
        / "visual-import-v2"
        / str(metadata["archive_relative_path"])
    )
    archive_path.write_bytes(b"%PDF-tampered")

    with pytest.raises(DesktopFacadeError, match="原文件归档") as caught:
        _run_with_egress(facade, saved.batch_id, plan)

    assert caught.value.code == "visual_import_source_archive_invalid"
    assert transport.calls == 0
    assert renderer.calls == [("question", "题目.pdf")]


def test_profile_revision_change_after_preview_fails_closed_before_transport(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    source = tmp_path / "题目.pdf"
    source.write_bytes(b"%PDF-original")
    renderer = CountingRenderer()
    transport = CapturingVisualTransport()
    provider = FakeProviderStore()
    facade = _facade(desktop_paths, provider, renderer, transport)
    saved = facade.save_visual_import_batch(
        question_files=(source,), source_type="合成资料"
    )
    plan = facade.preview_saved_visual_import_batch(
        batch_id=saved.batch_id,
        profile_id="vision",
        expected_profile_revision="REV-1",
    )
    provider.revision = "REV-2"

    with pytest.raises(DesktopFacadeError, match="配置已变化") as caught:
        _run_with_egress(facade, saved.batch_id, plan)

    assert caught.value.code == "visual_profile_stale"
    assert transport.calls == 0
    assert renderer.calls == [("question", "题目.pdf")]

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
from copy import deepcopy
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
    image = Image.new("RGB", (37, 29))
    # Position-dependent pixels expose wrong offsets/axes that a flat fill
    # would hide even if the crop dimensions were otherwise correct.
    image.putdata([tuple((channel + 3 * x + 7 * y + index * x * y) % 240
                         for index, channel in enumerate(color))
                   for y in range(image.height) for x in range(image.width)])
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


class CountingRenderer:
    """Synthetic renderer whose call count exposes accidental rerendering."""

    def __init__(self, *, page_count: int = 1) -> None:
        self.calls: list[tuple[str, str]] = []
        self.page_count = page_count

    def render(self, source_file: Any, *, source_role: str):
        key = (source_role, str(source_file.filename))
        self.calls.append(key)
        result = []
        for index in range(self.page_count):
            seed = hashlib.sha256(f"{'/'.join(key)}/{index}".encode()).digest()
            raw = _png((seed[0], seed[1], seed[2]))
            result.append(RenderedPixelPage(
                pixels=raw,
                mime_type="image/png",
                width=37,
                height=29,
                render_recipe_sha256=hashlib.sha256(
                    f"fixture-render-v1:{key}:{index}".encode()
                ).hexdigest(),
            ))
        return tuple(result)


class CapturingVisualTransport(FakeVisualTransport):
    """Reuse the existing valid fake response while retaining outbound pixels."""

    def __init__(self, *, evidence_count: int = 1, review_outcome: str = "pass") -> None:
        super().__init__()
        self.outbound: list[dict[str, Any]] = []
        self.evidence_count = evidence_count
        self.review_outcome = review_outcome

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
        phase = ("review" if body["text"]["format"]["name"] == "shchem_visual_crop_review_v1"
                 else "extraction")
        manifests = metadata["source_pages"] if phase == "review" else metadata["page_manifests"]
        role = metadata["source_request"]["source_role"] if phase == "review" else metadata["source_role"]
        attachments = []
        for image_part in image_parts:
            header, encoded = image_part["image_url"].split(",", 1)
            assert header.startswith("data:") and header.endswith(";base64")
            attachments.append({"mime_type": header[5:-7], "pixels": base64.b64decode(encoded, validate=True)})
        assert len(attachments) == len(manifests) + (len(metadata["checks"]) if phase == "review" else 0)
        pages = []
        for index, manifest in enumerate(manifests):
            pages.append(
                {
                    "source_role": role,
                    "source_file_id": manifest["source_file_id"],
                    "page_number": manifest["page_number"],
                    "pixels": attachments[index]["pixels"],
                }
            )
        self.outbound.append(
            {
                "phase": phase,
                "source_role": role,
                "metadata": metadata,
                "attachments": attachments,
                "pages": pages,
            }
        )
        response = super().send(
            request,
            cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic,
        )
        payload = json.loads(json.loads(response.body)["output_text"])
        if phase == "extraction" and self.evidence_count > 1:
            original = payload["evidence"][0]
            payload["evidence"] = []
            for index in range(self.evidence_count):
                evidence = deepcopy(original)
                evidence["evidence_id"] = f"{original['evidence_id']}-CROP-{index + 1}"
                evidence["bbox"] = {"x": 0.03 + (index % 4) * 0.2,
                                    "y": 0.025 + (index // 4) * 0.15,
                                    "width": 0.16, "height": 0.12}
                payload["evidence"].append(evidence)
            refs = [item["evidence_id"] for item in payload["evidence"]]
            payload["paper_identity"]["evidence_refs"] = refs
            for theme in payload["theme_fragments"]:
                theme["evidence_refs"] = refs
                for printed in theme["printed_questions"]:
                    printed["evidence_refs"] = refs
                    for atomic in printed["atomic_parts"]:
                        atomic["evidence_refs"] = refs
            return self._response(payload)
        if phase == "review" and self.review_outcome != "pass":
            if self.review_outcome == "partial":
                payload["checks"].pop()
            else:
                payload["checks"][0].update(status=self.review_outcome, reason="合成复核未通过",
                    issue_codes=["incomplete_visual" if self.review_outcome == "reject" else "other_uncertainty"])
            return self._response(payload)
        return response


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
    result = {}
    for page in plan["pages"]:
        raw = facade.visual_import_egress_image(plan["preview_id"], plan["revision"], page["page_id"])
        assert hashlib.sha256(raw).hexdigest() == page["sha256"]
        with Image.open(io.BytesIO(raw)) as original:
            assert original.size == (page["width"], page["height"])
        result[(
            str(page["source_role"]),
            str(page["source_file_id"]),
            int(page["page_number"]),
        )] = raw
    return result


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
        if request["phase"] == "extraction"
        for page in request["pages"]
    }


def _assert_review_pixels(
    transport: CapturingVisualTransport, frozen_pages: dict[tuple[str, str, int], bytes],
    *, require_complete: bool = True,
) -> None:
    """Check each real outbound attachment against the user's frozen page pixels."""
    extracted: dict[str, list[dict[str, Any]]] = {}
    reviewed: dict[str, list[str]] = {}
    expected_evidence: dict[str, set[str]] = {}
    for outbound in transport.outbound:
        metadata = outbound["metadata"]
        if outbound["phase"] == "extraction":
            extracted[metadata["shard_id"]] = outbound["pages"]
            continue
        shard_id = metadata["source_request"]["shard_id"]
        assert shard_id in extracted, "review cannot precede its extraction"
        source_pages, checks = metadata["source_pages"], metadata["checks"]
        expected_evidence[shard_id] = {item["evidence_id"] for item in metadata["validated_fragment"]["evidence"]}
        fragment_bytes = json.dumps(metadata["validated_fragment"], ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()
        assert hashlib.sha256(fragment_bytes).hexdigest() == metadata["fragment_sha256"]
        attachments = outbound["attachments"]
        assert 1 <= len(checks) <= 8
        assert len(attachments) == len(source_pages) + len(checks)
        assert [item["attachment_index"] for item in source_pages + checks] == list(range(len(attachments)))
        assert outbound["pages"] == extracted[shard_id]
        subject = {key: value for key, value in metadata.items() if key not in {"request_digest", "validated_fragment"}}
        canonical = json.dumps(subject, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert hashlib.sha256(canonical).hexdigest() == metadata["request_digest"]
        source_by_identity = {}
        for index, page in enumerate(source_pages):
            identity = (page["source_role"], page["source_file_id"], page["page_number"])
            assert identity in frozen_pages, "no unpreviewed page may enter a review"
            raw = attachments[index]["pixels"]
            assert raw == frozen_pages[identity]
            assert page["attachment_kind"] == "source_page"
            assert attachments[index]["mime_type"] == page["mime_type"]
            assert hashlib.sha256(raw).hexdigest() == page["page_sha256"]
            with Image.open(io.BytesIO(raw)) as image:
                assert image.size == (page["width"], page["height"])
            source_by_identity[identity] = raw
        for index, check in enumerate(checks, len(source_pages)):
            evidence = next(item for item in metadata["validated_fragment"]["evidence"]
                            if item["evidence_id"] == check["evidence_id"])
            assert all(check[field] == evidence[field] for field in (
                "source_role", "source_file_id", "page_number", "page_sha256", "bbox",
            ))
            identity = (check["source_role"], check["source_file_id"], check["page_number"])
            assert identity in source_by_identity
            raw = source_by_identity[identity]
            assert hashlib.sha256(raw).hexdigest() == check["page_sha256"]
            assert check["attachment_kind"] == "actual_crop"
            assert attachments[index]["mime_type"] == "image/png"
            with Image.open(io.BytesIO(raw)) as original:
                box = check["bbox"]
                bounds = (math.floor(box["x"] * original.width), math.floor(box["y"] * original.height),
                          math.ceil((box["x"] + box["width"]) * original.width),
                          math.ceil((box["y"] + box["height"]) * original.height))
                assert list(bounds) == check["pixel_xyxy"]
                expected = original.crop(bounds)
                assert expected.size == (check["crop_width"], check["crop_height"])
                stream = io.BytesIO()
                expected.save(stream, "PNG")
            crop = attachments[index]["pixels"]
            assert crop == stream.getvalue()
            assert hashlib.sha256(crop).hexdigest() == check["crop_sha256"]
            reviewed.setdefault(shard_id, []).append(check["evidence_id"])
    assert reviewed and all(len(ids) == len(set(ids)) for ids in reviewed.values())
    if require_complete:
        assert set(reviewed) == set(extracted)
        assert {shard: set(ids) for shard, ids in reviewed.items()} == expected_evidence


def _file_hashes(path: Path) -> dict[str, str]:
    return {file.relative_to(path).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in path.rglob("*") if file.is_file()} if path.exists() else {}


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
    assert _planned_pixels(facade, plan) == expected
    assert transport.calls == 0 and renderer.calls == [expected_call]

    completed = _run_with_egress(facade, saved.batch_id, plan)

    assert completed.status == "candidate_ready_for_review"
    assert completed.visual_status == "completed"
    assert renderer.calls == [expected_call]
    assert provider.borrow_calls == 1
    assert transport.calls == 2
    assert [item["phase"] for item in transport.outbound] == ["extraction", "review"]
    assert _outbound_pixels(transport) == expected
    _assert_review_pixels(transport, expected)


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
    assert transport.calls == 0

    completed = _run_with_egress(facade, saved.batch_id, plan)

    assert completed.status == "candidate_ready_for_review"
    assert renderer.calls == []
    assert transport.calls == 2
    assert _outbound_pixels(transport) == {
        ("question", page["source_file_id"], 1): original
    }
    _assert_review_pixels(transport, _planned_pixels(facade, plan))


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
    assert provider.borrow_calls == 0
    assert renderer.calls == [("question", "题目.pdf")]


def test_review_groups_at_most_eight_evidence_and_covers_every_actual_crop(
    desktop_paths: DesktopPaths, tmp_path: Path,
) -> None:
    source = tmp_path / "多裁片题目.pdf"
    source.write_bytes(b"%PDF-synthetic-many-crops")
    renderer = CountingRenderer()
    transport = CapturingVisualTransport(evidence_count=17)
    provider = FakeProviderStore()
    facade = _facade(desktop_paths, provider, renderer, transport)
    saved = facade.save_visual_import_batch(question_files=(source,), source_type="合成多裁片资料")
    plan = facade.preview_saved_visual_import_batch(batch_id=saved.batch_id, profile_id="vision",
                                                  expected_profile_revision="REV-1")
    frozen = _planned_pixels(facade, plan)
    assert transport.calls == provider.borrow_calls == 0
    completed = _run_with_egress(facade, saved.batch_id, plan)
    assert completed.status == "candidate_ready_for_review"
    assert completed.visual_status == "completed"
    assert transport.calls == 4
    assert [item["phase"] for item in transport.outbound] == ["extraction", "review", "review", "review"]
    assert [len(item["metadata"]["checks"]) for item in transport.outbound if item["phase"] == "review"] == [8, 8, 1]
    assert renderer.calls == [("question", "多裁片题目.pdf")]
    assert provider.borrow_calls == 1
    _assert_review_pixels(transport, frozen)


def test_review_keeps_all_frozen_source_pages_before_actual_crop_attachments(
    desktop_paths: DesktopPaths, tmp_path: Path,
) -> None:
    source = tmp_path / "两页题目.pdf"
    source.write_bytes(b"%PDF-synthetic-two-pages")
    renderer = CountingRenderer(page_count=2)
    transport = CapturingVisualTransport()
    facade = _facade(desktop_paths, FakeProviderStore(), renderer, transport)
    saved = facade.save_visual_import_batch(question_files=(source,), source_type="合成两页资料")
    plan = facade.preview_saved_visual_import_batch(batch_id=saved.batch_id, profile_id="vision",
                                                  expected_profile_revision="REV-1")
    assert len(plan["pages"]) == 2
    frozen = _planned_pixels(facade, plan)
    assert transport.calls == 0 and renderer.calls == [("question", "两页题目.pdf")]
    completed = _run_with_egress(facade, saved.batch_id, plan)
    assert completed.status == "candidate_ready_for_review"
    assert transport.calls == 2
    review = transport.outbound[1]
    assert len(review["metadata"]["source_pages"]) == 2
    assert review["metadata"]["checks"][0]["attachment_index"] == 2
    assert renderer.calls == [("question", "两页题目.pdf")]
    _assert_review_pixels(transport, frozen)


@pytest.mark.parametrize("outcome", ["reject", "uncertain", "partial"])
def test_real_facade_nonpass_or_incomplete_review_fails_without_cas_or_retry(
    desktop_paths: DesktopPaths, tmp_path: Path, outcome: str,
) -> None:
    source = tmp_path / "复核失败题目.pdf"
    source.write_bytes(b"%PDF-synthetic-nonpass-review")
    renderer = CountingRenderer()
    # Nine evidence rows would require two review batches. Failing the first
    # group must neither retry it nor proceed to the remaining group.
    transport = CapturingVisualTransport(evidence_count=9, review_outcome=outcome)
    provider = FakeProviderStore()
    facade = _facade(desktop_paths, provider, renderer, transport)
    saved = facade.save_visual_import_batch(question_files=(source,), source_type="合成复核失败资料")
    plan = facade.preview_saved_visual_import_batch(batch_id=saved.batch_id, profile_id="vision",
                                                  expected_profile_revision="REV-1")
    frozen = _planned_pixels(facade, plan)
    assert transport.calls == provider.borrow_calls == 0
    cas_root = desktop_paths.state_root / "visual-import-v2" / "candidates"
    before = _file_hashes(cas_root)
    assert not (cas_root / saved.batch_id).exists()
    failed = _run_with_egress(facade, saved.batch_id, plan)
    assert failed.status == "failed" and failed.visual_status == "failed"
    assert facade._saved_visual_import_batch(saved.batch_id)["status"] == "failed"
    assert saved.batch_id in [item.batch_id for item in facade.list_resumable_visual_import_batches()]
    assert transport.calls == 2
    assert [item["phase"] for item in transport.outbound] == ["extraction", "review"]
    assert len(transport.outbound[1]["metadata"]["checks"]) == 8
    assert provider.borrow_calls == 1
    assert renderer.calls == [("question", "复核失败题目.pdf")]
    assert _file_hashes(cas_root) == before
    assert not (cas_root / saved.batch_id).exists()
    _assert_review_pixels(transport, frozen, require_complete=False)

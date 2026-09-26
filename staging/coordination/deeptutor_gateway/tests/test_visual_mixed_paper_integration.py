"""Actual synthetic CAS -> mixed basket -> frozen four-file export.

Pagination is an explicit test double, not evidence of Office rendering or
classroom/chemical approval. All source mutation cases target pytest tmp_path.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from threading import Event
from zipfile import ZipFile

from docx import Document
from mixed_pagination_test_support import synthetic_pagination
import pytest
from test_personal_visual_questions import BATCH_ID
from test_personal_visual_questions import (
    imported_visual_batch as imported_visual_batch,  # noqa: F401
)

from integrations.deeptutor_shchem_v1 import desktop_mixed_paper_pagination as pagination
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_pagination import (
    MixedPaperPaginationError,
)
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import (
    REQUEST_SCHEMA,
    MixedPaperError,
    MixedPaperService,
)
from integrations.deeptutor_shchem_v1.desktop_personal_visual_crops import (
    resolved_crop,
    source_binding,
)
from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import (
    PersonalVisualQuestionError,
)
from integrations.deeptutor_shchem_v1.intake_batches_v2 import CandidateCAS
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, read_cancel_scope

ERRORS = (MixedPaperError, MixedPaperPaginationError, PersonalVisualQuestionError)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _protected(context):
    """Exclude only owned assembly drafts/output, not source/CAS/crop/labels."""
    root = context["paths"].state_root
    return {path.relative_to(root).as_posix(): _sha(path.read_bytes()) for path in root.rglob("*")
            if path.is_file() and path.name != "desktop-state.v1.json"
            and "mixed-paper-previews" not in path.relative_to(root).parts}


def _service(context, **kwargs):
    rows = context["service"].catalog(BATCH_ID)["items"]
    service = MixedPaperService(context["facade"], pagination_builder=synthetic_pagination, **kwargs)
    return service, rows


def _request(service, **changes):
    projection = service.projection()
    return {
        "schema_version": REQUEST_SCHEMA, "title": "合成图片主题统一练习",
        "subtitle": "仅用于自动化测试", "mode": "daily_practice", "duration_minutes": 40,
        "show_question_scores": False, "basket_sha256": projection["basket_sha256"],
        "section_order": [item["key"] for item in projection["items"]], "settings_by_key": {},
        **changes,
    }


def _pages(preview):
    return [page for audience in ("student", "teacher")
            for page in preview.preview_model["pagination"]["documents"][audience]["pages"]]


def _prepare(service, rows):
    service.add_visual_questions([rows[0]])
    content = service.create_preview(_request(service))
    return content, service.prepare_pagination(content.preview_id, content.preview_hash)


def _read_all(service, preview):
    for page in _pages(preview):
        payload = service.image(preview.preview_id, page["image_id"])
        assert payload["content_type"] == "image/png"
        assert payload["sha256"] == _sha(payload["data"]) == page["sha256"]


def test_actual_synthetic_visual_theme_to_frozen_four_artifacts_without_source_writes(imported_visual_batch, monkeypatch):
    context = imported_visual_batch
    service, rows = _service(context)
    before = _protected(context)
    assert service.add_visual_questions([rows[0]]) == 1
    assert service.add_visual_questions([rows[1], rows[0]]) == 1
    basket = service.state.basket()
    assert len(basket) == 1 and basket[0]["item_kind"] == "personal_visual_theme"
    assert {row["key"] for row in basket[0]["visual_selections"]} == {row["key"] for row in rows}
    assert basket[0]["visual_selections"] == basket[0]["source_ref"]["selections"]
    projection = service.projection()
    assert len(projection["items"]) == 1
    item = projection["items"][0]
    assert len(item["content"]["theme"]["printed_questions"]) == 2
    assert item["content"]["theme"]["shared_materials"]
    assert item["settings"] == {"use_source_scores": True}
    assert [score["max_score"] for score in item["content"]["source_scores"]] == [2, 2]
    answer_hashes = {image["sha256"] for image in item["content"]["images"] if image["teacher_only"]}
    content = service.create_preview(_request(service))
    section = content.preview_model["sections"][0]
    student_images = [block for block in section["student_blocks"] if block["kind"] == "image"]
    teacher_images = [block for block in section["teacher_blocks"] if block["kind"] == "image"]
    assert student_images and teacher_images
    student_hashes = {_sha(service.image(content.preview_id, block["image_id"])["data"]) for block in student_images}
    teacher_hashes = {_sha(service.image(content.preview_id, block["image_id"])["data"]) for block in teacher_images}
    assert student_hashes.isdisjoint(answer_hashes)
    assert answer_hashes.issubset(teacher_hashes)
    paginated = service.prepare_pagination(content.preview_id, content.preview_hash)
    assert paginated.preview_hash != content.preview_hash
    assert not paginated.export_ready and not paginated.approved
    _read_all(service, paginated)
    assert service.approve(paginated.preview_id, paginated.preview_hash)["status"] == "approved"
    folder, record, snapshot = service._load(paginated.preview_id)
    root, manifest = service._pagination(folder, snapshot)
    assert snapshot["pagination_binding"]["manifest_sha256"] == manifest["manifest_sha256"]
    assert all(value is False for value in manifest["gates"].values())
    assert set(record["pages_read"]) == {page["image_id"] for page in _pages(paginated)}
    # Export must copy the approved artifacts; no new DOCX/render request.
    monkeypatch.setattr(service, "_build_docx", lambda *args: pytest.fail("must not rebuild on export"))
    monkeypatch.setattr(service, "_pagination_builder", lambda *args: pytest.fail("must not render on export"))
    result = service.export(paginated.preview_id, paginated.preview_hash)
    assert result["status"] == "completed" and result["pdf_status"] == "generated"
    assert {row["artifact_id"] for row in result["artifacts"]} == {
        "student_docx", "student_pdf", "teacher_docx", "teacher_pdf"}
    for artifact in result["artifacts"]:
        audience, fmt = artifact["artifact_id"].split("_")
        frozen = manifest["versions"][audience][fmt]
        raw = Path(artifact["path"]).read_bytes()
        assert artifact["sha256"] == _sha(raw) == frozen["sha256"]
        assert raw == (root / frozen["path"]).read_bytes()
        if fmt == "docx":
            with ZipFile(BytesIO(raw)) as archive:
                media = {_sha(archive.read(name)) for name in archive.namelist() if name.startswith("word/media/")}
            if audience == "student":
                assert not media.intersection(answer_hashes)
            else:
                assert answer_hashes.issubset(media)
    assert _protected(context) == before


def test_content_only_preview_cannot_approve_or_export(imported_visual_batch):
    service, rows = _service(imported_visual_batch)
    service.add_visual_questions([rows[0]])
    preview = service.create_preview(_request(service))
    for block in preview.preview_model["sections"][0]["teacher_blocks"]:
        if block["kind"] == "image":
            service.image(preview.preview_id, block["image_id"])
    with pytest.raises(MixedPaperError, match="尚未生成实际分页"):
        service.approve(preview.preview_id, preview.preview_hash)
    with pytest.raises(MixedPaperError):
        service.export(preview.preview_id, preview.preview_hash)


def test_unread_teacher_page_and_old_content_confirmation_are_blocked(imported_visual_batch):
    service, rows = _service(imported_visual_batch)
    content, paginated = _prepare(service, rows)
    first = paginated.preview_model["pagination"]["documents"]["student"]["pages"][0]
    service.image(paginated.preview_id, first["image_id"])
    with pytest.raises(MixedPaperError, match="逐页"):
        service.approve(paginated.preview_id, paginated.preview_hash)
    _read_all(service, paginated)
    with pytest.raises(MixedPaperError):
        service.approve(paginated.preview_id, content.preview_hash)
    assert service.approve(paginated.preview_id, paginated.preview_hash)["status"] == "approved"


@pytest.mark.parametrize("cancel_at", ["renderer", "after_renderer", "after_source_recheck"])
def test_cancelled_pagination_does_not_persist_approvable_result(imported_visual_batch, monkeypatch, cancel_at):
    context = imported_visual_batch
    service, rows = _service(context)
    service.add_visual_questions([rows[0]])
    preview = service.create_preview(_request(service))
    event = Event()

    def cancel(paths, output):
        if cancel_at == "after_renderer":
            result = synthetic_pagination(paths, output)
            event.set()
            return result
        if cancel_at == "after_source_recheck":
            return synthetic_pagination(paths, output)
        raise ReadCancelled()

    service._pagination_builder = cancel
    if cancel_at == "after_source_recheck":
        original_build = service._build_sections

        def cancel_after_recheck(*args, **kwargs):
            result = original_build(*args, **kwargs)
            if kwargs.get("check_images") is False:
                event.set()
            return result

        monkeypatch.setattr(service, "_build_sections", cancel_after_recheck)
    before = _protected(context)
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        service.prepare_pagination(preview.preview_id, preview.preview_hash)
    _, record, current = service._load(preview.preview_id)
    assert record["approval"] is None and not current.get("pagination_binding")
    with pytest.raises(MixedPaperError):
        service.approve(preview.preview_id, preview.preview_hash)
    with pytest.raises(MixedPaperError):
        service.export(preview.preview_id, preview.preview_hash)
    assert _protected(context) == before


def test_new_preview_model_invalidates_previous_page_confirmation(imported_visual_batch):
    service, rows = _service(imported_visual_batch)
    _, paginated = _prepare(service, rows)
    _read_all(service, paginated)
    service.approve(paginated.preview_id, paginated.preview_hash)
    changed = service.create_preview(_request(service, title="合成标题与排版设置已变化", duration_minutes=50))
    assert changed.preview_id != paginated.preview_id
    with pytest.raises(MixedPaperError):
        service.approve(paginated.preview_id, paginated.preview_hash)
    with pytest.raises(MixedPaperError):
        service.export(paginated.preview_id, paginated.preview_hash)


def _change_source(context, kind):
    personal = context["service"]
    snapshot, pages = personal._batch(BATCH_ID)
    if kind == "cas":
        cas = CandidateCAS.open(personal.root / "candidates" / BATCH_ID)
        cas.edit_atomic_part("ATOMIC-SYN-1", {"stem": "合成 CAS 新版本题干"},
                             expected_revision_token=snapshot["revision_token"],
                             expected_candidate_sha256=snapshot["candidate_sha256"],
                             actor_id="synthetic-test", idempotency_key="mixed-export-cas-change")
    elif kind == "crop":
        evidence = next(row for row in snapshot["candidate"]["evidence"] if row["source_role"] == "question")
        page = pages[(evidence["source_file_id"], evidence["page_number"], evidence["page_sha256"])]
        state = resolved_crop(source_binding(BATCH_ID, snapshot, evidence, page), evidence["bbox"])
        personal.crop_store.save(state, {"x": 0, "y": 0, "width": 0.5, "height": 0.5},
                                 expected_batch_revision=personal.crop_store.batch_revision({}),
                                 edit_origin="ai_source_review")
    elif kind == "page":
        target = personal.root / next(iter(pages.values()))["relative_path"]
        target.write_bytes(target.read_bytes() + b"synthetic page replacement")
    else:
        source = context["questions"][0]
        target = personal.root / "sources" / (source.source_sha256 + ".png")
        target.write_bytes(target.read_bytes() + b"synthetic source replacement")


@pytest.mark.parametrize("kind", ["cas", "crop", "page", "source"])
def test_source_change_after_pagination_invalidates_old_approval(imported_visual_batch, kind):
    context = imported_visual_batch
    service, rows = _service(context)
    _, paginated = _prepare(service, rows)
    _read_all(service, paginated)
    service.approve(paginated.preview_id, paginated.preview_hash)
    _change_source(context, kind)
    expected_protected = _protected(context)
    with pytest.raises(ERRORS):
        service.approve(paginated.preview_id, paginated.preview_hash)
    with pytest.raises(ERRORS):
        service.export(paginated.preview_id, paginated.preview_hash)
    folder, _, _ = service._load(paginated.preview_id)
    assert not list(folder.glob("export-*"))
    assert _protected(context) == expected_protected


def test_missing_frozen_page_blocks_confirmation_even_after_all_pages_read(imported_visual_batch):
    service, rows = _service(imported_visual_batch)
    _, paginated = _prepare(service, rows)
    _read_all(service, paginated)
    folder, _, snapshot = service._load(paginated.preview_id)
    root, manifest = service._pagination(folder, snapshot)
    # Only a synthetic tmp_path artifact is removed, never a user document.
    (root / manifest["versions"]["teacher"]["pages"][0]["path"]).unlink()
    with pytest.raises(MixedPaperPaginationError):
        service.approve(paginated.preview_id, paginated.preview_hash)
    with pytest.raises(MixedPaperError):
        service.export(paginated.preview_id, paginated.preview_hash)


def test_approve_rechecks_active_preview_after_final_source_check(imported_visual_batch, monkeypatch):
    service, rows = _service(imported_visual_batch)
    _, paginated = _prepare(service, rows)
    _read_all(service, paginated)
    original = service._build_sections
    replacement = []

    def replace_after_check(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("check_images") is False and not replacement:
            replacement.append(service.create_preview(_request(service, title="合成并发新预览")))
        return result

    monkeypatch.setattr(service, "_build_sections", replace_after_check)
    with pytest.raises(MixedPaperError):
        service.approve(paginated.preview_id, paginated.preview_hash)
    assert len(replacement) == 1
    _, record, _ = service._load(paginated.preview_id)
    assert record["approval"] is None


def test_export_rechecks_actual_cas_after_last_frozen_artifact_read(imported_visual_batch, monkeypatch):
    context = imported_visual_batch
    service, rows = _service(context)
    _, paginated = _prepare(service, rows)
    _read_all(service, paginated)
    service.approve(paginated.preview_id, paginated.preview_hash)
    original = pagination.read_frozen_artifact
    changed = []

    def change_after_last_read(root, manifest, audience, extension, **kwargs):
        result = original(root, manifest, audience, extension, **kwargs)
        if audience == "teacher" and extension == "pdf" and not changed:
            _change_source(context, "cas")
            changed.append(_protected(context))
        return result

    monkeypatch.setattr(pagination, "read_frozen_artifact", change_after_last_read)
    with pytest.raises(ERRORS):
        service.export(paginated.preview_id, paginated.preview_hash)
    assert len(changed) == 1
    assert _protected(context) == changed[0]


def test_paginated_preview_cannot_be_prepared_again_with_old_page_read_flags(imported_visual_batch):
    service, rows = _service(imported_visual_batch)
    _, paginated = _prepare(service, rows)
    _read_all(service, paginated)
    service.approve(paginated.preview_id, paginated.preview_hash)
    folder, record, snapshot = service._load(paginated.preview_id)
    before = {path.relative_to(folder): _sha(path.read_bytes()) for path in folder.rglob("*") if path.is_file()}
    with pytest.raises(MixedPaperError):
        service.prepare_pagination(paginated.preview_id, paginated.preview_hash)
    assert service._load(paginated.preview_id)[1:] == (record, snapshot)
    assert before == {path.relative_to(folder): _sha(path.read_bytes()) for path in folder.rglob("*") if path.is_file()}


@pytest.mark.parametrize("unknown_score", [False, True])
def test_visual_unknown_source_score_is_not_presented_as_complete_total(imported_visual_batch, unknown_score):
    context = imported_visual_batch
    if unknown_score:
        # Candidate schema uses zero for an unestablished source score. This
        # edit is confined to the existing synthetic tmp_path CAS fixture.
        cas = CandidateCAS.open(context["service"].root / "candidates" / BATCH_ID)
        snapshot = cas.snapshot()
        cas.edit_atomic_part(
            "ATOMIC-SYN-1", {"answer": {"max_score": 0, "scoring_points": []}},
            expected_revision_token=snapshot["revision_token"],
            expected_candidate_sha256=snapshot["candidate_sha256"],
            actor_id="synthetic-test", idempotency_key="mixed-unknown-source-score",
        )
    service, rows = _service(context)
    before = _protected(context)
    service.add_visual_questions([rows[0]])
    preview = service.create_preview(_request(service, show_question_scores=True))
    expected_known = 2 if unknown_score else 4
    assert preview.preview_model["stats"]["known_score"] == expected_known
    assert preview.preview_model["stats"]["total_score"] == (None if unknown_score else 4)
    folder, _, snapshot = service._load(preview.preview_id)
    payload = service._build_docx(preview.preview_id, snapshot, folder)
    for audience in ("student", "teacher"):
        document = Document(BytesIO(payload[audience + "_bytes"]))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert f"合计已知分值 {expected_known} 分" in text
        if unknown_score:
            assert "部分分值待核对，非总分" in text
        else:
            assert "部分分值待核对，非总分" not in text
    if unknown_score:
        current, _ = context["service"]._batch(BATCH_ID)
        raw_answer = current["candidate"]["paper"]["theme_big_questions"][0]["printed_questions"][0]["atomic_parts"][0]["answer"]
        assert raw_answer["max_score"] == 0 and raw_answer["scoring_points"] == []
    assert _protected(context) == before

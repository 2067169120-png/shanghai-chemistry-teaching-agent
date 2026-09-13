"""Complete-theme exports use synthetic temporary imports only; no live provider."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
from threading import Event
from zipfile import ZipFile

from docx import Document
import pytest
from test_personal_visual_questions import BATCH_ID
from test_personal_visual_questions import (
    imported_visual_batch as imported_visual_batch,  # noqa: F401
)

from integrations.deeptutor_shchem_v1.desktop_personal_visual_crops import (
    resolved_crop,
    source_binding,
)
from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import (
    PersonalVisualQuestionError,
)
from integrations.deeptutor_shchem_v1.desktop_personal_visual_theme_export import (
    ITEM_KIND,
    PersonalVisualThemeExportError,
    PersonalVisualThemeExportService,
)
from integrations.deeptutor_shchem_v1.desktop_personal_visual_theme_writer import (
    append_personal_visual_theme,
    build_theme_blocks,
)
from integrations.deeptutor_shchem_v1.intake_batches_v2 import candidate_sha256
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, read_cancel_scope


def _setup(context):
    service = context["service"]
    rows = service.catalog(BATCH_ID)["items"]
    return service, rows, PersonalVisualThemeExportService(service)


def _files(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def _mutated_snapshot(context, monkeypatch, change):
    service = context["service"]
    snapshot, pages = service._batch(BATCH_ID)
    snapshot = deepcopy(snapshot)
    change(snapshot["candidate"])
    snapshot["candidate_sha256"] = candidate_sha256(snapshot["candidate"])
    monkeypatch.setattr(service, "_batch", lambda batch: (deepcopy(snapshot), deepcopy(pages)))
    return snapshot


def test_complete_theme_preserves_typed_order_scores_assets_and_no_state_writes(imported_visual_batch, monkeypatch):
    context = imported_visual_batch
    service, rows, exporter = _setup(context)
    before = _files(context["paths"].state_root)
    monkeypatch.setattr(service, "_compile_reference", lambda *_: pytest.fail("Do not parse reference text"))
    items, assets = exporter.compile([dict(rows[1], points=99)])
    assert len(items) == 1 and items[0]["kind"] == ITEM_KIND
    item = items[0]
    snapshot, _ = service._batch(BATCH_ID)
    original = snapshot["candidate"]["paper"]["theme_big_questions"][0]
    theme = item["content"]["theme"]
    assert [row["printed_question_id"] for row in theme["printed_questions"]] == [
        row["printed_question_id"] for row in original["printed_questions"]]
    assert [row["shared_material_id"] for row in theme["shared_materials"]] == [
        row["shared_material_id"] for row in original["shared_materials"]]
    for expected, actual in zip(original["printed_questions"], theme["printed_questions"], strict=True):
        for old, new in zip(expected["atomic_parts"], actual["atomic_parts"], strict=True):
            for key, value in old["answer"].items():
                assert new["answer"][key] == value
            assert new["answer"]["teacher_only"] is True
    assert item["settings"] == {"use_source_scores": True}
    assert len(item["source_ref"]["selections"]) == 2
    assert item["source_ref"]["candidate_sha256"] == snapshot["candidate_sha256"]
    assert [score["max_score"] for score in item["content"]["source_scores"]] == [2, 2]
    assert all(score["authority"] == "source_reference_unverified" for score in item["content"]["source_scores"])
    assert item["content"]["candidate_only"] and not item["content"]["teacher_reviewed"]
    assert not item["content"]["publication_allowed"]
    for image in item["content"]["images"]:
        assert hashlib.sha256(assets[image["asset_id"]]).hexdigest() == image["sha256"]
        assert image["teacher_only"] == (image["role"] == "answer")
        assert image["binding"]["page_sha256"] and image["binding"]["crop_revision"]
        assert len(image["binding"]["pixel_xyxy"]) == 4
    assert _files(context["paths"].state_root) == before


def test_same_theme_from_different_clicked_questions_has_canonical_full_selection(imported_visual_batch):
    _, rows, exporter = _setup(imported_visual_batch)
    left, left_assets = exporter.compile([rows[0]])
    right, right_assets = exporter.compile([rows[1], rows[0]])
    assert left == right and left_assets == right_assets


def test_orphan_shared_material_and_answer_scoring_only_evidence_are_retained(imported_visual_batch, monkeypatch):
    def change(candidate):
        theme = candidate["paper"]["theme_big_questions"][0]
        orphan = deepcopy(theme["shared_materials"][0])
        orphan.update(shared_material_id="ORPHAN", content="完整主题孤立材料", visual_object_refs=[])
        theme["shared_materials"].append(orphan)
        answer = theme["printed_questions"][0]["atomic_parts"][0]["answer"]
        answer["evidence_refs"] = []
        answer["chemical_expressions"] = []
        answer["alignment"]["evidence_refs"] = []
    _mutated_snapshot(imported_visual_batch, monkeypatch, change)
    _, rows, exporter = _setup(imported_visual_batch)
    items, _ = exporter.compile([rows[0]])
    theme = items[0]["content"]["theme"]
    assert theme["shared_materials"][-1]["shared_material_id"] == "ORPHAN"
    assert theme["shared_materials"][-1]["image_refs"]
    assert theme["printed_questions"][0]["atomic_parts"][0]["answer"]["image_refs"]


def test_resolver_checks_current_snapshot_and_enforces_teacher_only(imported_visual_batch):
    _, rows, exporter = _setup(imported_visual_batch)
    items, assets = exporter.compile([rows[0]])
    item = items[0]
    answer = next(image for image in item["content"]["images"] if image["teacher_only"])
    assert exporter.resolve_asset(item, answer["asset_id"])["bytes"] == assets[answer["asset_id"]]
    with pytest.raises(PersonalVisualThemeExportError, match="学生"):
        exporter.resolve_asset(item, answer["asset_id"], audience="student")
    changed = deepcopy(item)
    changed["content"]["theme"]["title"] = "caller injected title"
    with pytest.raises(PersonalVisualThemeExportError, match="快照"):
        exporter.resolve_asset(changed, answer["asset_id"])
    with pytest.raises(PersonalVisualThemeExportError):
        exporter.resolve_asset(item, "file:///not-an-asset")


@pytest.mark.parametrize("change", [
    lambda theme: theme["printed_questions"][0].update(visual_object_refs=["MISSING"]),
    lambda theme: theme["printed_questions"][0].update(shared_material_refs=["MISSING"]),
    lambda theme: theme.update(merge_status="conflict"),
])
def test_incomplete_explicit_graph_is_not_silently_dropped(imported_visual_batch, monkeypatch, change):
    _mutated_snapshot(imported_visual_batch, monkeypatch,
                      lambda candidate: change(candidate["paper"]["theme_big_questions"][0]))
    _, rows, exporter = _setup(imported_visual_batch)
    with pytest.raises(PersonalVisualQuestionError):
        exporter.compile([rows[0]])


def test_changed_original_bytes_block_compile(imported_visual_batch):
    service, rows, exporter = _setup(imported_visual_batch)
    _, pages = service._batch(BATCH_ID)
    page = next(iter(pages.values()))
    (service.root / page["relative_path"]).write_bytes(b"changed synthetic image")
    with pytest.raises(PersonalVisualQuestionError):
        exporter.compile([rows[0]])


def test_candidate_digest_mismatch_and_crop_drift_block(imported_visual_batch, monkeypatch):
    service, rows, exporter = _setup(imported_visual_batch)
    original_batch = service._batch

    def corrupted(batch):
        snapshot, pages = original_batch(batch)
        snapshot["candidate_sha256"] = "0" * 64
        return snapshot, pages

    monkeypatch.setattr(service, "_batch", corrupted)
    with pytest.raises(PersonalVisualQuestionError):
        exporter.compile([rows[0]])
    monkeypatch.setattr(service, "_batch", original_batch)
    call = [0]

    def head(records):
        call[0] += 1
        return "a" * 64 if call[0] == 1 else "b" * 64

    monkeypatch.setattr(service.crop_store, "batch_revision", head)
    with pytest.raises(PersonalVisualThemeExportError, match="裁剪范围已变化"):
        exporter.compile([rows[0]])


def test_same_pixels_cannot_cross_student_answer_boundary(imported_visual_batch, monkeypatch):
    service, rows, exporter = _setup(imported_visual_batch)
    original = service._image_for_row
    first = []

    def same(*args, **kwargs):
        result = original(*args, **kwargs)
        if not first:
            first.append(result)
        return first[0]

    monkeypatch.setattr(service, "_image_for_row", same)
    with pytest.raises(PersonalVisualThemeExportError, match="相同图片"):
        exporter.compile([rows[0]])


def test_active_crop_bytes_and_binding_then_stale_crop_are_checked(imported_visual_batch, monkeypatch):
    context = imported_visual_batch
    service, _, exporter = _setup(context)
    snapshot, pages = service._batch(BATCH_ID)
    evidence = next(row for row in snapshot["candidate"]["evidence"] if row["source_role"] == "question")
    page = pages[(evidence["source_file_id"], evidence["page_number"], evidence["page_sha256"])]
    state = resolved_crop(source_binding(BATCH_ID, snapshot, evidence, page), evidence["bbox"])
    service.crop_store.save(state, {"x": 0, "y": 0, "width": 0.5, "height": 0.5},
                            expected_batch_revision=service.crop_store.batch_revision({}),
                            edit_origin="ai_source_review")
    rows = service.catalog(BATCH_ID)["items"]
    items, _ = exporter.compile([rows[0]])
    cropped = [image for image in items[0]["content"]["images"]
               if image["binding"]["evidence_id"] == evidence["evidence_id"]]
    assert cropped and all(image["binding"]["crop_active"] for image in cropped)
    assert cropped[0]["width"] == page["width"] // 2
    _mutated_snapshot(context, monkeypatch, lambda candidate: candidate["paper"].update(title="new synthetic title"))
    rows = service.catalog(BATCH_ID)["items"]
    with pytest.raises(PersonalVisualThemeExportError, match="旧个人裁剪"):
        exporter.compile([rows[0]])


def test_page_replaced_during_compile_is_reopened_and_rejected(imported_visual_batch, monkeypatch):
    service, rows, exporter = _setup(imported_visual_batch)
    read = service._file
    touched = []

    def replace_after_read(relative, *args, **kwargs):
        raw = read(relative, *args, **kwargs)
        if relative.startswith("pages/") and not touched:
            touched.append(relative)
            (service.root / relative).write_bytes(b"synthetic concurrent replacement")
        return raw

    monkeypatch.setattr(service, "_file", replace_after_read)
    with pytest.raises(PersonalVisualQuestionError):
        exporter.compile([rows[0]])


def test_cancelled_compile_never_returns_partial_assets(imported_visual_batch):
    _, rows, exporter = _setup(imported_visual_batch)
    event = Event()
    event.set()
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        exporter.compile([rows[0]])


def test_image_first_blocks_do_not_repeat_ocr_or_leak_answers(imported_visual_batch):
    _, rows, exporter = _setup(imported_visual_batch)
    items, assets = exporter.compile([rows[0]])
    item = items[0]
    student = build_theme_blocks(item, assets, "student")
    teacher = build_theme_blocks(item, assets, "teacher")
    student_text = "\n".join(block["text"] for block in student if block["kind"] == "text")
    teacher_text = "\n".join(block["text"] for block in teacher if block["kind"] == "text")
    theme = item["content"]["theme"]
    # The source shared-body image is the printed content, not a reason to
    # repeat its AI summary above the picture in the student paper.
    assert theme["context"] not in student_text
    for printed in theme["printed_questions"]:
        assert printed["stem"] not in student_text
        for atomic in printed["atomic_parts"]:
            assert atomic["answer"]["answer_body"] not in student_text
            assert atomic["answer"]["answer_body"] not in teacher_text
    assert not any(block.get("teacher_only") for block in student)
    assert any(block.get("teacher_only") for block in teacher)
    assert "来源参考分值：2分" in teacher_text


@pytest.mark.parametrize("visual_only", [False, True])
def test_shared_text_without_body_image_preserves_context_and_material(imported_visual_batch, monkeypatch, visual_only):
    def change(candidate):
        theme = candidate["paper"]["theme_big_questions"][0]
        for material in theme["shared_materials"]:
            material["evidence_refs"] = []
            for expression in material["chemical_expressions"]:
                expression["evidence_refs"] = []
            if not visual_only:
                material["visual_object_refs"] = []

    _mutated_snapshot(imported_visual_batch, monkeypatch, change)
    _, rows, exporter = _setup(imported_visual_batch)
    items, assets = exporter.compile([rows[0]])
    theme = items[0]["content"]["theme"]
    blocks = build_theme_blocks(items[0], assets, "student")
    text = "\n".join(block["text"] for block in blocks if block["kind"] == "text")
    assert theme["context"] in text
    for material in theme["shared_materials"]:
        assert material["content"] in text
        for expression in material["chemical_expressions"]:
            assert expression["raw"] in text
    if visual_only:
        shared_assets = {image["asset_id"] for image in items[0]["content"]["images"]
                         if image["role"] == "shared_material"}
        displayed = {block["asset_id"] for block in blocks if block["kind"] == "image"}
        assert shared_assets and shared_assets.issubset(displayed)


def test_figure_only_edge_keeps_typed_question_text_and_figure(imported_visual_batch, monkeypatch):
    def change(candidate):
        theme = candidate["paper"]["theme_big_questions"][0]
        printed = theme["printed_questions"][0]
        for node in [printed, *printed["atomic_parts"]]:
            node["evidence_refs"] = []
            node["chemical_expressions"] = []
            node["visual_object_refs"] = [theme["visual_objects"][0]["visual_object_id"]]
    _mutated_snapshot(imported_visual_batch, monkeypatch, change)
    _, rows, exporter = _setup(imported_visual_batch)
    items, assets = exporter.compile([rows[0]])
    blocks = build_theme_blocks(items[0], assets, "student")
    assert any(items[0]["content"]["theme"]["printed_questions"][0]["stem"] in block.get("text", "") for block in blocks)
    assert any(block["kind"] == "image" for block in blocks)


@pytest.mark.parametrize("mutation", ["missing", "changed", "score", "content"])
def test_writer_rejects_missing_tampered_images_and_custom_source_scores(imported_visual_batch, mutation):
    _, rows, exporter = _setup(imported_visual_batch)
    items, assets = exporter.compile([rows[0]])
    item = items[0]
    key = next(iter(assets))
    if mutation == "missing":
        del assets[key]
    elif mutation == "changed":
        assets[key] = b"not the frozen source"
    elif mutation == "score":
        item["settings"] = {"use_source_scores": False, "score_per_atomic": 10}
    else:
        item["content"]["theme"]["printed_questions"][0]["atomic_parts"][0]["answer"]["max_score"] = 99
    with pytest.raises(PersonalVisualThemeExportError):
        build_theme_blocks(item, assets, "student")


def test_docx_embeds_exact_images_and_teacher_only_answers_without_conversion(imported_visual_batch):
    _, rows, exporter = _setup(imported_visual_batch)
    items, assets = exporter.compile([rows[0]])
    item = items[0]
    for audience in ("student", "teacher"):
        document = Document()
        blocks = append_personal_visual_theme(document, item, assets, audience, ordinal=3)
        output = BytesIO()
        document.save(output)
        with ZipFile(BytesIO(output.getvalue())) as archive:
            media = {hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                     if name.startswith("word/media/")}
            xml = archive.read("word/document.xml").decode("utf-8")
        expected = {hashlib.sha256(assets[block["asset_id"]]).hexdigest()
                    for block in blocks if block["kind"] == "image"}
        assert media == expected
        assert "题组 3" in xml
        for printed in item["content"]["theme"]["printed_questions"]:
            assert printed["stem"] not in xml
        if audience == "student":
            assert "来源参考答案" not in xml
            answer_hashes = {image["sha256"] for image in item["content"]["images"] if image["teacher_only"]}
            assert not media.intersection(answer_hashes)

"""Synthetic local crop geometry, frozen confirmation, and source-bound history."""

from __future__ import annotations

import hashlib
import io
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image
from test_personal_visual_attributes import (
    _updates,
)
from test_personal_visual_attributes import (
    attribute_context as attribute_context,  # noqa: PLC0414 - pytest fixture re-export
)
from test_personal_visual_attributes import (
    imported_visual_batch as imported_visual_batch,  # noqa: PLC0414 - pytest fixture re-export
)

from integrations.deeptutor_shchem_v1.desktop_personal_visual_crops import (
    PersonalVisualCropError,
    PersonalVisualCropStore,
    crop_bytes,
    pixel_bounds,
    preview_registry,
    resolved_crop,
    validate_bbox,
    validate_record,
)
from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import (
    PersonalVisualQuestionError,
    PersonalVisualQuestionService,
)
from integrations.deeptutor_shchem_v1.intake_batches_v2 import CandidateCAS

BATCH_ID = "DESKTOPBATCH-" + "a" * 32
ORIGINAL_BBOX = {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}
NEW_BBOX = {"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3}


def binding(**changes):
    return {
        "batch_id": BATCH_ID,
        "candidate_revision": "rev_00000000_" + "a" * 64,
        "candidate_sha256": "b" * 64,
        "evidence_id": "EV-SYNTHETIC-1",
        "original_evidence_sha256": "c" * 64,
        "source_file_id": "SRC-SYNTHETIC-1",
        "source_role": "question",
        "page_number": 1,
        "page_sha256": "d" * 64,
        "source_sha256": "e" * 64,
    } | changes


@pytest.mark.parametrize(
    "bbox",
    [
        None,
        [],
        {},
        {**NEW_BBOX, "extra": 1},
        {**NEW_BBOX, "x": True},
        {**NEW_BBOX, "y": float("nan")},
        {**NEW_BBOX, "width": float("inf")},
        {**NEW_BBOX, "x": -0.1},
        {**NEW_BBOX, "y": 1},
        {**NEW_BBOX, "width": 0},
        {**NEW_BBOX, "height": -0.1},
        {**NEW_BBOX, "x": 0.9},
        {"x": 0, "y": 0, "width": 1.0000001, "height": 1},
    ],
)
def test_bbox_invalid_values_fail_without_clamping(bbox):
    with pytest.raises(PersonalVisualCropError):
        validate_bbox(bbox)


def test_crop_bytes_are_exact_pixel_window_and_integer_roundtrip_has_no_extra_border():
    source = Image.new("RGB", (100, 80))
    for y in range(80):
        for x in range(100):
            source.putpixel((x, y), (x, y, x + y))
    stream = io.BytesIO()
    source.save(stream, "PNG")
    bbox = validate_bbox(
        {"x": 29 / 100, "y": 13 / 80, "width": 41 / 100, "height": 39 / 80}
    )
    assert pixel_bounds(bbox, 100, 80) == {
        "left": 29,
        "top": 13,
        "right": 70,
        "bottom": 52,
    }
    raw = crop_bytes(stream.getvalue(), bbox, 100, 80)
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (41, 39)
        assert image.getpixel((0, 0)) == (29, 13, 42)
        assert image.getpixel((40, 38)) == (69, 51, 120)


def test_store_read_does_not_create_and_revisions_history_survive_new_instance(
    tmp_path,
):
    store = PersonalVisualCropStore(tmp_path / "local")
    assert store.get_batch(BATCH_ID) == {}
    assert store.history(BATCH_ID, "EV-SYNTHETIC-1") == []
    assert not store.path.exists()
    initial = resolved_crop(binding(), ORIGINAL_BBOX)
    first = store.save(
        initial, NEW_BBOX, expected_batch_revision=store.batch_revision({})
    )
    assert first["teacher_reviewed"] is False
    assert first["edit_origin"] == "teacher"
    second_store = PersonalVisualCropStore(store.root)
    records = second_store.get_batch(BATCH_ID)
    assert records["EV-SYNTHETIC-1"] == first
    current = resolved_crop(binding(), ORIGINAL_BBOX, first)
    assert current["active"] and not current["stale"]
    assert current["crop_revision"] != initial["crop_revision"]
    second = second_store.save(
        current,
        ORIGINAL_BBOX,
        expected_batch_revision=store.batch_revision(records),
        edit_origin="ai_source_review",
    )
    assert second["revision"] != initial["crop_revision"] != first["revision"]
    assert second["edit_origin"] == "ai_source_review"
    assert second["teacher_reviewed"] is False
    assert store.history(BATCH_ID, "EV-SYNTHETIC-1") == [first, second]


def test_two_store_instances_reject_stale_writer_without_history_append(tmp_path):
    left, right = PersonalVisualCropStore(tmp_path), PersonalVisualCropStore(tmp_path)
    initial = resolved_crop(binding(), ORIGINAL_BBOX)
    first = left.save(
        initial, NEW_BBOX, expected_batch_revision=left.batch_revision({})
    )
    with pytest.raises(PersonalVisualCropError, match="新版本"):
        right.save(initial, NEW_BBOX, expected_batch_revision=right.batch_revision({}))
    assert right.history(BATCH_ID, "EV-SYNTHETIC-1") == [first]


@pytest.mark.parametrize(
    "change",
    [
        {"candidate_revision": "rev_00000001_" + "f" * 64},
        {"candidate_sha256": "f" * 64},
        {"page_sha256": "f" * 64},
        {"source_file_id": "SRC-SYNTHETIC-2"},
        {"page_number": 2},
        {"original_evidence_sha256": "f" * 64},
    ],
)
def test_changed_source_binding_does_not_reuse_old_crop(tmp_path, change):
    store = PersonalVisualCropStore(tmp_path)
    initial = resolved_crop(binding(), ORIGINAL_BBOX)
    stored = store.save(
        initial, NEW_BBOX, expected_batch_revision=store.batch_revision({})
    )
    current = resolved_crop(binding(**change), ORIGINAL_BBOX, stored)
    assert not current["active"] and current["stale"]
    assert current["bbox"] == ORIGINAL_BBOX
    assert current["crop_revision"] != stored["revision"]
    assert current["stored_revision"] == stored["revision"]
    assert store.history(BATCH_ID, "EV-SYNTHETIC-1") == [stored]


def test_tampered_crop_record_fails_closed(tmp_path):
    store = PersonalVisualCropStore(tmp_path)
    stored = store.save(
        resolved_crop(binding(), ORIGINAL_BBOX),
        NEW_BBOX,
        expected_batch_revision=store.batch_revision({}),
    )
    changed = deepcopy(stored)
    changed["bbox"]["x"] = 0.3
    with pytest.raises(PersonalVisualCropError):
        validate_record(changed)


def test_frozen_registry_is_shared_by_facade_and_has_no_client_alias():
    facade = SimpleNamespace()
    registry = preview_registry(facade)
    receipt = {"binding": binding(), "bbox": deepcopy(NEW_BBOX)}
    frozen = registry.add(receipt)
    receipt["bbox"]["x"] = 0.7
    frozen["bbox"]["x"] = 0.8
    assert preview_registry(facade) is registry
    saved = registry.get(frozen["preview_id"], frozen["preview_revision"])
    assert saved["bbox"] == NEW_BBOX
    with pytest.raises(PersonalVisualCropError, match="失效"):
        registry.get(frozen["preview_id"], "0" * 64)
    assert registry.discard(frozen["preview_id"])
    assert not registry.discard(frozen["preview_id"])
    with pytest.raises(PersonalVisualCropError, match="失效"):
        registry.get(frozen["preview_id"], frozen["preview_revision"])


def _target(context, *, role="question"):
    rows = context["service"].catalog(BATCH_ID)["items"]
    candidates = [
        (
            sum(
                any(
                    other["evidence_id"] == image["evidence_id"]
                    for other in item["images"]
                )
                for item in rows
            ),
            row,
            image,
        )
        for row in rows
        for image in row["images"]
        if image["role"] == role
    ]
    _, row, image = min(candidates, key=lambda value: value[0])
    options = context["service"].crop_options(
        BATCH_ID, row["key"], row["revision"], image["image_id"]
    )
    return rows, row, image, options


def _preview(context, options, bbox=None):
    return context["service"].preview_crop(
        BATCH_ID,
        options["key"],
        options["revision"],
        options["image_id"],
        bbox or NEW_BBOX,
        expected_crop_revision=options["crop_revision"],
    )


def _files(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_crop_options_and_preview_show_real_pixels_but_do_not_persist(
    attribute_context,
):
    context = attribute_context
    _, row, image, options = _target(context)
    before = _files(context["paths"].state_root)
    assert options["role"] == "question" and options["role_label"] == "题面"
    assert options["evidence_id"] == image["evidence_id"]
    assert options["affected_questions"]
    assert row["key"] in options["affected_question_keys"]
    assert options["history"] == []
    assert options["source_binding"]["candidate_sha256"] == row["candidate_sha256"]
    preview = _preview(context, options)
    with Image.open(io.BytesIO(preview["original_image"]["bytes"])) as original:
        assert original.size == (options["width"], options["height"])
    with Image.open(io.BytesIO(preview["preview_image"]["bytes"])) as cropped:
        bounds = pixel_bounds(NEW_BBOX, options["width"], options["height"])
        assert cropped.size == (
            bounds["right"] - bounds["left"],
            bounds["bottom"] - bounds["top"],
        )
    assert preview["preview_image"]["bytes"] != preview["current_image"]["bytes"]
    assert "独立副本" in preview["warning"]
    assert _files(context["paths"].state_root) == before
    assert not context["service"].crop_store.path.exists()


def test_save_changes_only_actual_evidence_users_and_keeps_unaffected_labels_selection(
    attribute_context,
):
    context = attribute_context
    service = context["service"]
    for row in service.catalog(BATCH_ID)["items"]:
        options = service.attribute_options(BATCH_ID, row["key"], row["revision"])
        service.save_attributes(
            BATCH_ID,
            row["key"],
            row["revision"],
            _updates(options, teacher_note="合成教师标签"),
            expected_attribute_revision=options["attributes"]["revision"],
        )
    rows, row, image, options = _target(context)
    service.save_selection(rows)
    expected_keys = {
        item["key"]
        for item in rows
        if any(
            descriptor["evidence_id"] == image["evidence_id"]
            for descriptor in item["images"]
        )
    }
    unaffected = [item for item in rows if item["key"] not in expected_keys]
    assert unaffected, "synthetic fixture must contain an unrelated question"
    old_reference = service.reference([unaffected[0]])
    before = _files(service.root)
    attributes_before = service.attribute_store.path.read_bytes()
    before_rows = {item["key"]: item for item in rows}
    preview = _preview(context, options)
    # Facade methods create a fresh service each time; the receipt must survive.
    fresh_service = PersonalVisualQuestionService(context["facade"])
    saved = fresh_service.save_crop(
        preview["preview_id"], preview["preview_revision"], confirmed=True
    )
    assert set(saved) == {
        "batch_id",
        "detail",
        "affected_rows",
        "revision_changes",
        "crop_revision",
        "warnings",
    }
    assert {item["key"] for item in saved["affected_rows"]} == expected_keys
    assert {change["key"] for change in saved["revision_changes"]} == expected_keys
    current = fresh_service.catalog(BATCH_ID)
    assert {item["key"] for item in current["selection"]} == {
        item["key"] for item in unaffected
    }
    for item in current["items"]:
        old = before_rows[item["key"]]
        for field in (
            "candidate_revision",
            "candidate_sha256",
            "question_text",
            "shared_text",
            "answer_text",
        ):
            assert item[field] == old[field]
        assert item["teacher_reviewed"] is False
        if item["key"] in expected_keys:
            assert item["revision"] != old["revision"]
            assert item["attribute_warning"]
            assert item["attributes"]["teacher_note"] == ""
        else:
            assert item["revision"] == old["revision"]
            assert item["attribute_revision"] == old["attribute_revision"]
            assert item["attributes"]["teacher_note"] == "合成教师标签"
    detail = saved["detail"]
    new_image = next(
        item
        for item in detail["images"]
        if item["evidence_id"] == image["evidence_id"] and item["role"] == image["role"]
    )
    assert new_image["crop_revision"] == saved["crop_revision"]
    assert (
        fresh_service.image(
            BATCH_ID, row["key"], detail["revision"], new_image["image_id"]
        )["bytes"]
        == preview["preview_image"]["bytes"]
    )
    assert (
        fresh_service.image(
            BATCH_ID,
            row["key"],
            detail["revision"],
            new_image["image_id"],
            original=True,
        )["bytes"]
        == options["original_image"]["bytes"]
    )
    assert _files(service.root) == before
    assert service.attribute_store.path.read_bytes() == attributes_before
    with pytest.raises(PersonalVisualQuestionError, match="变化"):
        fresh_service.import_reference(old_reference, [])
    rebuilt = fresh_service.reference([unaffected[0]])
    new_digest = hashlib.sha256(preview["preview_image"]["bytes"]).hexdigest()
    assert new_digest in {asset["sha256"] for asset in rebuilt["image_assets"]}
    assert "独立副本" in " ".join(saved["warnings"])


def test_shared_crop_affects_every_real_reference_and_ai_save_never_claims_chemistry_review(
    attribute_context,
):
    context = attribute_context
    rows, _, image, options = _target(context, role="shared_material")
    expected = {
        row["key"]
        for row in rows
        if any(item["evidence_id"] == image["evidence_id"] for item in row["images"])
    }
    assert len(expected) > 1
    assert set(options["affected_question_keys"]) == expected
    preview = _preview(context, options)
    result = context["service"].save_crop(
        preview["preview_id"],
        preview["preview_revision"],
        confirmed=True,
        edit_origin="ai_source_review",
    )
    assert {row["key"] for row in result["affected_rows"]} == expected
    assert all(row["teacher_reviewed"] is False for row in result["affected_rows"])
    history = context["service"].crop_store.history(BATCH_ID, image["evidence_id"])
    assert (
        history[-1]["edit_origin"] == "ai_source_review"
        and history[-1]["teacher_reviewed"] is False
    )


@pytest.mark.parametrize("confirmed", [False, None, 1, "yes"])
def test_save_requires_explicit_boolean_confirmation(attribute_context, confirmed):
    context = attribute_context
    preview = _preview(context, _target(context)[3])
    with pytest.raises(PersonalVisualQuestionError, match="明确确认"):
        context["service"].save_crop(
            preview["preview_id"], preview["preview_revision"], confirmed=confirmed
        )
    assert not context["service"].crop_store.path.exists()


def test_discard_stale_revision_and_consumed_preview_cannot_write(attribute_context):
    context = attribute_context
    options = _target(context)[3]
    discarded = _preview(context, options)
    assert PersonalVisualQuestionService(context["facade"]).discard_crop(
        discarded["preview_id"]
    )
    with pytest.raises(PersonalVisualQuestionError, match="失效"):
        context["service"].save_crop(
            discarded["preview_id"], discarded["preview_revision"], confirmed=True
        )
    first, stale = _preview(context, options), _preview(context, options)
    with pytest.raises(PersonalVisualQuestionError, match="失效"):
        context["service"].save_crop(first["preview_id"], "0" * 64, confirmed=True)
    context["service"].save_crop(
        first["preview_id"], first["preview_revision"], confirmed=True
    )
    for preview in (first, stale):
        with pytest.raises(PersonalVisualQuestionError):
            context["service"].save_crop(
                preview["preview_id"], preview["preview_revision"], confirmed=True
            )
    assert (
        len(context["service"].crop_store.history(BATCH_ID, options["evidence_id"]))
        == 1
    )


def test_client_mutation_cannot_change_frozen_bbox_or_pixels(attribute_context):
    context = attribute_context
    options = _target(context)[3]
    preview = _preview(context, options)
    expected_pixels = preview["preview_image"]["bytes"]
    preview["new_bbox"]["x"] = 0.5
    preview["preview_image"]["bytes"] = b"untrusted client pixel substitution"
    result = context["service"].save_crop(
        preview["preview_id"], preview["preview_revision"], confirmed=True
    )
    image = next(
        item
        for item in result["detail"]["images"]
        if item["evidence_id"] == options["evidence_id"]
    )
    assert (
        context["service"].image(
            BATCH_ID,
            result["detail"]["key"],
            result["detail"]["revision"],
            image["image_id"],
        )["bytes"]
        == expected_pixels
    )


@pytest.mark.parametrize("change", ["page", "cas"])
def test_page_or_cas_change_after_preview_rejects_without_crop_write(
    attribute_context, change
):
    context = attribute_context
    options = _target(context)[3]
    preview = _preview(context, options)
    if change == "page":
        _, pages = context["service"]._batch(BATCH_ID)
        page = next(
            page
            for page in pages.values()
            if page["page_sha256"] == options["page_sha256"]
        )
        path = context["service"].root / page["relative_path"]
        path.write_bytes(path.read_bytes() + b"synthetic-tamper")
    else:
        cas = CandidateCAS.open(context["service"].root / "candidates" / BATCH_ID)
        snapshot = cas.snapshot()
        cas.edit_atomic_part(
            "ATOMIC-SYN-1",
            {"stem": "合成CAS变更"},
            expected_revision_token=snapshot["revision_token"],
            expected_candidate_sha256=snapshot["candidate_sha256"],
            actor_id="synthetic-crop-test",
            idempotency_key="crop-preview-cas-stale",
        )
    with pytest.raises(PersonalVisualQuestionError):
        context["service"].save_crop(
            preview["preview_id"], preview["preview_revision"], confirmed=True
        )
    assert not context["service"].crop_store.path.exists()


def test_no_change_bbox_and_wrong_crop_revision_are_rejected(attribute_context):
    context = attribute_context
    options = _target(context)[3]
    with pytest.raises(PersonalVisualQuestionError, match="没有变化"):
        _preview(context, options, options["current_bbox"])
    with pytest.raises(PersonalVisualQuestionError, match="版本已变化"):
        context["service"].preview_crop(
            BATCH_ID,
            options["key"],
            options["revision"],
            options["image_id"],
            NEW_BBOX,
            expected_crop_revision="0" * 64,
        )
    with pytest.raises(PersonalVisualQuestionError):
        _preview(context, options, {**NEW_BBOX, "width": True})
    assert not context["service"].crop_store.path.exists()


def test_answer_display_crop_preserves_answer_text_and_original_answer_page(
    attribute_context,
):
    context = attribute_context
    rows, _, image, options = _target(context, role="answer")
    assert options["source_role"] == "answer" and options["role"] == "answer"
    before = {row["key"]: row["answer_text"] for row in rows}
    original = options["original_image"]["bytes"]
    preview = _preview(context, options)
    saved = context["service"].save_crop(
        preview["preview_id"], preview["preview_revision"], confirmed=True
    )
    for row in context["service"].catalog(BATCH_ID)["items"]:
        assert row["answer_text"] == before[row["key"]]
    detail = saved["detail"]
    current_image = next(
        item for item in detail["images"] if item["evidence_id"] == image["evidence_id"]
    )
    assert (
        context["service"].image(
            BATCH_ID,
            detail["key"],
            detail["revision"],
            current_image["image_id"],
            original=True,
        )["bytes"]
        == original
    )


def test_crop_cannot_be_targeted_through_unrelated_question(attribute_context):
    context = attribute_context
    rows, _, image, _ = _target(context)
    unrelated = next(
        row
        for row in rows
        if not any(
            other["evidence_id"] == image["evidence_id"] for other in row["images"]
        )
    )
    with pytest.raises(PersonalVisualQuestionError, match="未找到"):
        context["service"].crop_options(
            BATCH_ID, unrelated["key"], unrelated["revision"], image["image_id"]
        )
    assert not context["service"].crop_store.path.exists()


def test_cas_change_suspends_saved_overlay_but_keeps_history(attribute_context):
    context = attribute_context
    _, _, image, options = _target(context)
    preview = _preview(context, options)
    context["service"].save_crop(
        preview["preview_id"], preview["preview_revision"], confirmed=True
    )
    before = context["service"].crop_store.history(BATCH_ID, image["evidence_id"])
    cas = CandidateCAS.open(context["service"].root / "candidates" / BATCH_ID)
    snapshot = cas.snapshot()
    cas.edit_atomic_part(
        "ATOMIC-SYN-1",
        {"stem": "合成CAS新版本"},
        expected_revision_token=snapshot["revision_token"],
        expected_candidate_sha256=snapshot["candidate_sha256"],
        actor_id="synthetic-crop-test",
        idempotency_key="crop-overlay-cas-stale",
    )
    rows = context["service"].catalog(BATCH_ID)["items"]
    affected = [
        row
        for row in rows
        if any(item["evidence_id"] == image["evidence_id"] for item in row["images"])
    ]
    assert affected and all(
        "旧个人裁剪未套用" in row["crop_warning"] for row in affected
    )
    row = affected[0]
    restored_image = next(
        item for item in row["images"] if item["evidence_id"] == image["evidence_id"]
    )
    assert "crop_revision" not in restored_image
    restored = context["service"].image(
        BATCH_ID, row["key"], row["revision"], restored_image["image_id"]
    )
    assert restored["bytes"] == options["current_image"]["bytes"]
    assert (
        context["service"].crop_store.history(BATCH_ID, image["evidence_id"]) == before
    )

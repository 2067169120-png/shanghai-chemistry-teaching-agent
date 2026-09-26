"""Temporary synthetic-only image attributes; no configured provider or files."""

from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from test_personal_visual_questions import BATCH_ID
from test_personal_visual_questions import (
    imported_visual_batch as imported_visual_batch,  # noqa: PLC0414 - pytest fixture re-export
)

from integrations.deeptutor_shchem_v1 import desktop_personal_visual_questions as visual
from integrations.deeptutor_shchem_v1 import desktop_visual_import_v2 as bridge
from integrations.deeptutor_shchem_v1.desktop_personal_visual_attributes import (
    ATTRIBUTE_SCHEMA,
    SCHEMA_VERSION,
    PersonalVisualAttributeError,
    PersonalVisualAttributeStore,
    apply_teacher_edits,
    build_teacher_updates,
    validate_attributes,
)
from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import (
    PersonalVisualQuestionError,
    PersonalVisualQuestionService,
    matches_personal_visual_filters,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    ATTRIBUTE_SCHEMA as WORD_SCHEMA,
)

SYNTHETIC_CATALOG = {
    "knowledge_points": [
        {"id": "K01", "name": "合成知识甲"},
        {"id": "K02", "name": "合成知识乙"},
    ],
    "nodes": [
        {
            "node_key": "SEC-A",
            "volume_id": "BOOK-A",
            "chapter_id": "CH-A",
            "section_title": "合成甲节",
            "chapter_title": "合成甲章",
            "volume_title": "合成甲册",
        },
        {
            "node_key": "SEC-B",
            "volume_id": "BOOK-B",
            "chapter_id": "CH-B",
            "section_title": "合成乙节",
            "chapter_title": "合成乙章",
            "volume_title": "合成乙册",
        },
    ],
}


@pytest.fixture
def attribute_context(imported_visual_batch, monkeypatch):
    monkeypatch.setattr(
        visual, "load_attribute_catalog", lambda _root: deepcopy(SYNTHETIC_CATALOG)
    )
    return imported_visual_batch


def selections(attributes, **updates):
    result = {
        "primary_knowledge_id": attributes["primary_knowledge"]["id"],
        "supporting_knowledge_ids": [
            row["id"] for row in attributes["supporting_knowledge"]
        ],
        "applicable_grades": attributes["applicable_grades"]["values"],
        **{
            "original_" + field: attributes["original_source"][field]["value"]
            for field in ("grade", "exam_type", "year", "region", "school")
        },
        "curriculum_section_keys": [
            row["section_key"] for row in attributes["curriculum_candidates"]
        ],
        "teaching_use_tags": attributes["teaching_use_tags"],
        "teacher_note": attributes["teacher_note"],
    }
    return deepcopy(result | updates)


def _options(context):
    service = context["service"]
    row = service.catalog(BATCH_ID)["items"][0]
    return row, service.attribute_options(BATCH_ID, row["key"], row["revision"])


def _updates(options, **changes):
    return build_teacher_updates(
        options["attributes"],
        selections(options["attributes"], **changes),
        options["catalog"],
    )


def _save(context, row, options, **changes):
    return context["service"].save_attributes(
        BATCH_ID,
        row["key"],
        row["revision"],
        _updates(options, **changes),
        expected_attribute_revision=options["attributes"]["revision"],
    )


def _hashes(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_read_is_source_bound_nonword_and_does_not_create_state(attribute_context):
    context = attribute_context
    before = _hashes(context["paths"].state_root)
    original_schema = deepcopy(WORD_SCHEMA)
    Draft202012Validator.check_schema(ATTRIBUTE_SCHEMA)
    row, options = _options(context)
    attrs = validate_attributes(options["attributes"])
    assert attrs["schema_version"] == SCHEMA_VERSION
    assert "word" not in attrs["schema_version"]
    assert attrs["question_revision"] == row["revision"]
    assert attrs["source"]["document_role"] == "personal_visual_question"
    assert "index_revision" not in attrs and "extraction_revision" not in attrs
    assert attrs["source_binding"]["candidate_sha256"] == row["candidate_sha256"]
    assert attrs["source_binding"]["pages"]
    assert attrs["source_observed"]["atomic_labels"]
    assert attrs["original_source"]["school"]["value"] == "unknown"
    assert attrs["original_source"]["region"]["value"] == "unknown"
    assert attrs["teacher_confirmed"] is False and row["teacher_reviewed"] is False
    assert options["history"] == [] and options["stored_revision"] is None
    assert set(options["catalog"]) == {"knowledge_points", "nodes", "teaching_use_tags"}
    assert _hashes(context["paths"].state_root) == before
    assert WORD_SCHEMA == original_schema


def test_saves_overlay_filters_history_restart_and_never_invalidate_content(
    attribute_context,
):
    context = attribute_context
    service = context["service"]
    before = _hashes(service.root)
    central_before = _hashes(context["paths"].workspace_root)
    calls = len(context["provider"].requests)
    row, options = _options(context)
    service.save_selection([row])
    image_before = {
        image["image_id"]: service.image(
            BATCH_ID, row["key"], row["revision"], image["image_id"], original=True
        )["bytes"]
        for image in row["images"]
    }
    saved = _save(
        context,
        row,
        options,
        primary_knowledge_id="K01",
        supporting_knowledge_ids=["K02"],
        applicable_grades=["grade_11"],
        original_grade="grade_12",
        original_exam_type="school_exam",
        original_year="2025",
        original_region="上海·合成区",
        original_school="合成测试学校",
        curriculum_section_keys=["SEC-A"],
        teaching_use_tags=["review", "practice"],
        teacher_note="只依据合成图测试个人教学标签。",
    )
    assert set(saved) == {"detail", "attributes", "attribute_revision"}
    assert saved["detail"]["revision"] == row["revision"]
    assert (
        saved["attribute_revision"]
        == saved["attributes"]["revision"]
        != options["attributes"]["revision"]
    )
    assert saved["attributes"]["primary_knowledge"]["status"] == "teacher_proposed"
    assert saved["attributes"]["teacher_confirmed"] is False
    assert (
        saved["attributes"]["source_binding"] == options["attributes"]["source_binding"]
    )
    assert (
        saved["attributes"]["source_observed"]
        == options["attributes"]["source_observed"]
    )
    restarted = PersonalVisualQuestionService(context["facade"])
    new_options = restarted.attribute_options(BATCH_ID, row["key"], row["revision"])
    assert len(new_options["history"]) == 2
    assert [entry["edit_version"] for entry in new_options["history"]] == [0, 1]
    second = restarted.save_attributes(
        BATCH_ID,
        row["key"],
        row["revision"],
        _updates(new_options, teacher_note="第二次合成修订"),
        expected_attribute_revision=saved["attribute_revision"],
        teacher_confirmed=True,
    )
    assert second["detail"]["revision"] == row["revision"]
    assert second["attributes"]["teacher_confirmed"] is True
    assert second["attributes"]["edit_version"] == 2
    updated = restarted.catalog(BATCH_ID)
    current = next(item for item in updated["items"] if item["key"] == row["key"])
    assert current["facets"]["knowledge"] == ["K01", "K02"]
    assert current["facets"]["grade"] == ["grade_11"]
    assert current["facets"]["exam"] == ["school_exam"]
    assert current["facets"]["teaching_use"] == ["review", "practice"]
    assert matches_personal_visual_filters(
        current, {"book": ["BOOK-A"], "knowledge": ["K01"]}
    )
    assert not matches_personal_visual_filters(current, {"book": ["BOOK-B"]})
    assert len(updated["selection"]) == 1
    assert all(
        item["attributes"]["primary_knowledge"]["id"] != "K01"
        for item in updated["items"]
        if item["key"] != row["key"]
    )
    assert current["teacher_reviewed"] is False
    for image in row["images"]:
        assert (
            service.image(
                BATCH_ID, row["key"], row["revision"], image["image_id"], original=True
            )["bytes"]
            == image_before[image["image_id"]]
        )
    assert _hashes(service.root) == before
    assert _hashes(context["paths"].workspace_root) == central_before
    assert len(context["provider"].requests) == calls


def test_stale_attribute_revision_rejected_and_history_not_changed(attribute_context):
    row, options = _options(attribute_context)
    _save(attribute_context, row, options, teacher_note="first")
    before = attribute_context["service"].attribute_store.history(row["key"])
    with pytest.raises(PersonalVisualQuestionError, match="新版本"):
        _save(attribute_context, row, options, teacher_note="stale")
    assert attribute_context["service"].attribute_store.history(row["key"]) == before


def test_grade_filter_options_use_chinese_labels_and_keep_unknown(attribute_context):
    row, options = _options(attribute_context)
    _save(attribute_context, row, options, applicable_grades=["grade_12"])
    choices = attribute_context["service"].catalog(BATCH_ID)["filter_options"]["grade"]
    labels = {choice["value"]: choice["label"] for choice in choices}
    assert labels["grade_12"] == "高三"
    assert labels["unknown"] == "待标注"


def test_exam_filter_options_use_chinese_labels_and_keep_unknown(
    attribute_context, monkeypatch,
):
    service = attribute_context["service"]
    snapshot, pages = service._batch(BATCH_ID)
    snapshot = deepcopy(snapshot)
    snapshot["candidate"]["paper"]["paper_type"] = "unknown"
    monkeypatch.setattr(service, "_batch", lambda _batch: (snapshot, pages))
    row, options = _options(attribute_context)
    _save(attribute_context, row, options, original_exam_type="second_mock")
    choices = attribute_context["service"].catalog(BATCH_ID)["filter_options"]["exam"]
    labels = {choice["value"]: choice["label"] for choice in choices}
    assert labels["second_mock"] == "二模"
    assert labels["unknown"] == "待标注"


def test_store_cas_rejects_second_writer_even_with_previously_resolved_options(
    attribute_context,
):
    row, options = _options(attribute_context)
    store = PersonalVisualAttributeStore(attribute_context["paths"].state_root)
    updates = _updates(options, teacher_note="concurrent")
    _save(attribute_context, row, options, teacher_note="winner")
    before = store.history(row["key"])
    with pytest.raises(PersonalVisualAttributeError, match="新版本"):
        store.save_teacher_edit(
            options["attributes"],
            updates,
            expected_stored_revision=None,
            curriculum_entries=options["catalog"],
        )
    assert store.history(row["key"]) == before


def test_content_cas_edit_requires_new_content_revision_and_preserves_old_label_history(
    attribute_context,
):
    context = attribute_context
    row, options = _options(context)
    _save(context, row, options, teacher_note="旧范围标签", primary_knowledge_id="K01")
    cas = bridge.CandidateCAS.open(context["service"].root / "candidates" / BATCH_ID)
    snap = cas.snapshot()
    cas.edit_atomic_part(
        "ATOMIC-SYN-1",
        {"stem": "新的合成题面范围"},
        expected_revision_token=snap["revision_token"],
        expected_candidate_sha256=snap["candidate_sha256"],
        actor_id="synthetic-test",
        idempotency_key="attributes-content-change",
    )
    with pytest.raises(PersonalVisualQuestionError, match="已经变化"):
        _save(context, row, options, teacher_note="old")
    current, new_options = _options(context)
    assert current["revision"] != row["revision"]
    assert new_options["warning"]
    assert new_options["attributes"]["teacher_note"] == ""
    assert new_options["attributes"]["primary_knowledge"]["id"] == "unknown"
    assert len(new_options["history"]) == 2
    saved = _save(context, current, new_options, teacher_note="新范围标签")
    history = context["service"].attribute_store.history(row["key"])
    assert [entry["edit_version"] for entry in history] == [0, 1, 2, 3]
    assert history[1]["teacher_note"] == "旧范围标签"
    assert saved["detail"]["revision"] == current["revision"]


@pytest.mark.parametrize(
    "changes",
    [
        {"primary_knowledge_id": "invented"},
        {"primary_knowledge_id": "K01", "supporting_knowledge_ids": ["K01"]},
        {"supporting_knowledge_ids": ["K02", "K02"]},
        {"applicable_grades": ["grade_9"]},
        {"original_grade": "grade_9"},
        {"original_exam_type": "official_assumed"},
        {"original_year": "last year"},
        {"original_region": "x" * 121},
        {"original_school": "x" * 121},
        {"curriculum_section_keys": ["not-a-section"]},
        {"teaching_use_tags": ["approved_for_publication"]},
        {"teaching_use_tags": ["review", "review"]},
        {"teacher_note": "x" * 2001},
    ],
)
def test_invalid_manual_selections_reject_without_write(attribute_context, changes):
    _, options = _options(attribute_context)
    with pytest.raises(PersonalVisualAttributeError):
        _updates(options, **changes)
    assert not attribute_context["service"].attribute_store.path.exists()


@pytest.mark.parametrize(
    "updates",
    [
        {"question_revision": "0" * 64},
        {"source_binding": {}},
        {"source_observed": {}},
        {"source": {}},
        {"answer_status": {}},
        {"teacher_reviewed": True},
    ],
)
def test_source_content_answer_and_review_gates_are_not_editable(
    attribute_context, updates
):
    row, options = _options(attribute_context)
    with pytest.raises(PersonalVisualQuestionError):
        attribute_context["service"].save_attributes(
            BATCH_ID,
            row["key"],
            row["revision"],
            updates,
            expected_attribute_revision=options["attributes"]["revision"],
        )
    assert not attribute_context["service"].attribute_store.path.exists()


def test_direct_updates_validate_catalog_and_cannot_rewrite_original_quotes(
    attribute_context,
):
    _, options = _options(attribute_context)
    updates = _updates(
        options, primary_knowledge_id="K01", curriculum_section_keys=["SEC-A"]
    )
    updates["primary_knowledge"]["label"] = "invented label"
    with pytest.raises(PersonalVisualAttributeError, match="编号和名称"):
        apply_teacher_edits(
            options["attributes"], updates, curriculum_entries=options["catalog"]
        )
    updates = _updates(options, curriculum_section_keys=["SEC-A"])
    updates["curriculum_candidates"][0]["volume_id"] = "BOOK-B"
    with pytest.raises(PersonalVisualAttributeError, match="映射"):
        apply_teacher_edits(
            options["attributes"], updates, curriculum_entries=options["catalog"]
        )
    original = deepcopy(options["attributes"]["original_source"])
    original["citation_quotes"] = [
        {"kind": "teacher_note", "quote": "fake source quote", "block_index": None}
    ]
    with pytest.raises(PersonalVisualAttributeError, match="引文"):
        apply_teacher_edits(options["attributes"], {"original_source": original})


def test_ai_source_review_is_distinct_and_cannot_claim_teacher_confirmation(
    attribute_context,
):
    context = attribute_context
    row, options = _options(context)
    updates = _updates(
        options,
        primary_knowledge_id="K01",
        curriculum_section_keys=["SEC-A"],
        original_school="合成学校",
    )
    with pytest.raises(PersonalVisualQuestionError):
        context["service"].save_attributes(
            BATCH_ID,
            row["key"],
            row["revision"],
            updates,
            expected_attribute_revision=options["attributes"]["revision"],
            teacher_confirmed=True,
            edit_origin="ai_source_review",
        )
    assert not context["service"].attribute_store.path.exists()
    saved = context["service"].save_attributes(
        BATCH_ID,
        row["key"],
        row["revision"],
        updates,
        expected_attribute_revision=options["attributes"]["revision"],
        edit_origin="ai_source_review",
    )
    attrs = saved["attributes"]
    assert attrs["annotation_source"] == "ai_source_review"
    assert attrs["primary_knowledge"]["status"] == "ai_source_review"
    assert attrs["curriculum_status"] == "ai_source_review"
    assert attrs["original_source"]["school"]["status"] == "ai_source_review"
    assert (
        attrs["teacher_confirmed"] is False
        and saved["detail"]["teacher_reviewed"] is False
    )
    assert context["service"].attribute_store.history(row["key"])[-1] == attrs


def test_ai_cannot_overwrite_teacher_fields_but_can_fill_other_source_field(
    attribute_context,
):
    context = attribute_context
    row, options = _options(context)
    _save(
        context,
        row,
        options,
        primary_knowledge_id="K01",
        original_school="教师记录合成学校",
        teacher_note="教师依据",
    )
    _, options = _options(context)
    before = context["service"].attribute_store.history(row["key"])
    for changes in (
        {"primary_knowledge_id": "K02"},
        {"original_school": "AI另猜学校"},
        {"teacher_note": "AI覆盖教师说明"},
    ):
        with pytest.raises(PersonalVisualQuestionError, match="教师修订"):
            context["service"].save_attributes(
                BATCH_ID,
                row["key"],
                row["revision"],
                _updates(options, **changes),
                expected_attribute_revision=options["attributes"]["revision"],
                edit_origin="ai_source_review",
            )
        assert context["service"].attribute_store.history(row["key"]) == before
    result = context["service"].save_attributes(
        BATCH_ID,
        row["key"],
        row["revision"],
        _updates(options, original_year="2025"),
        expected_attribute_revision=options["attributes"]["revision"],
        edit_origin="ai_source_review",
    )
    attrs = result["attributes"]
    assert (
        attrs["original_source"]["school"]
        == options["attributes"]["original_source"]["school"]
    )
    assert attrs["original_source"]["year"]["status"] == "ai_source_review"
    assert attrs["field_origins"]["original_source.school"] == "teacher"
    assert attrs["field_origins"]["original_source.year"] == "ai_source_review"
    assert attrs["primary_knowledge"]["status"] == "teacher_proposed"
    assert attrs["teacher_note"] == "教师依据"
    assert result["detail"]["revision"] == row["revision"]


def test_explicit_cas_seeds_are_preserved_without_filename_or_combined_source_guess(
    attribute_context, monkeypatch
):
    context = attribute_context
    snapshot, pages = context["service"]._batch(BATCH_ID)
    snapshot = deepcopy(snapshot)
    paper = snapshot["candidate"]["paper"]
    paper.update(
        source_year=2024,
        source_region_or_school="合成区或学校·不能拆猜",
        paper_type="school_exam",
    )
    atomic = paper["theme_big_questions"][0]["printed_questions"][0]["atomic_parts"][0]
    atomic["classification"].update(
        primary_knowledge_K=["K01"], supporting_knowledge_K=["K02"]
    )
    atomic["curriculum"].update(primary_chapter="SEC-A", secondary_chapters=[])
    pages = {
        key: {**value, "source_name": "2030-冒充地区-冒充学校-等级考.png"}
        for key, value in pages.items()
    }
    monkeypatch.setattr(context["service"], "_batch", lambda _batch: (snapshot, pages))
    row, options = _options(context)
    attrs = options["attributes"]
    assert attrs["primary_knowledge"]["id"] == "K01"
    assert attrs["primary_knowledge"]["status"] == "source_observed"
    assert attrs["curriculum_candidates"][0]["section_key"] == "SEC-A"
    assert attrs["original_source"]["year"]["value"] == "2024"
    assert attrs["original_source"]["exam_type"]["value"] == "school_exam"
    assert attrs["original_source"]["school"]["value"] == "unknown"
    assert attrs["original_source"]["region"]["value"] == "unknown"
    assert (
        attrs["source_observed"]["paper"]["source_region_or_school"]
        == "合成区或学校·不能拆猜"
    )
    assert row["teacher_reviewed"] is False


def test_unrelated_note_save_preserves_multiple_cas_primary_tags_and_chapter_only_path(
    attribute_context, monkeypatch
):
    context = attribute_context
    snapshot, pages = context["service"]._batch(BATCH_ID)
    snapshot = deepcopy(snapshot)
    atomic = snapshot["candidate"]["paper"]["theme_big_questions"][0][
        "printed_questions"
    ][0]["atomic_parts"][0]
    atomic["classification"].update(
        primary_knowledge_K=["K01", "K02"], supporting_knowledge_K=[]
    )
    atomic["curriculum"].update(primary_chapter="CH-A", secondary_chapters=[])
    monkeypatch.setattr(context["service"], "_batch", lambda _batch: (snapshot, pages))
    row, options = _options(context)
    assert options["attributes"]["primary_knowledge"]["id"] == "unknown"
    assert options["attributes"]["curriculum_candidates"] == []
    assert row["facets"]["knowledge"] == ["K01", "K02"]
    saved = _save(context, row, options, teacher_note="只修订备注，不改变CAS原有标签")
    assert saved["detail"]["facets"] == row["facets"]
    assert saved["detail"]["curriculum_paths"] == row["curriculum_paths"]
    assert saved["detail"]["revision"] == row["revision"]

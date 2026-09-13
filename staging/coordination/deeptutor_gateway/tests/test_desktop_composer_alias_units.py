from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.datong_answer_bindings import (
    EXISTING_ANSWER_AREA_NODE_IDS,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
    ComposerQuestion,
    PaperComposerModel,
    _theme_from_group,
)
from integrations.deeptutor_shchem_v1.paper_export_alias_projection import (
    PaperExportAliasProjectionError,
    project_explicit_alias_units,
)
from staging.coordination.deeptutor_gateway.tests.test_paper_export_alias_projection import (
    catalog,
    parent,
    selection,
    unit,
)


def test_composer_and_export_use_same_split_units_and_scores():
    parts = [unit("WAVE-1", 1), unit("WAVE-2", 2, "WAVE-1")]
    parts[0]["score"] = parts[1]["score"] = 2
    source = catalog([parent(aliases=parts)])
    original = deepcopy(source)
    group = source["papers"][0]["theme_groups"][0]
    theme = _theme_from_group(group, "fallback", scope="master")
    export = project_explicit_alias_units(source, [selection()], scope="master")
    rows = export.catalog["papers"][0]["theme_groups"][0]["atomic_chain"]
    assert [q.key for q in theme.questions] == [row["atomic_part_id"] for row in rows]
    assert len(theme.questions) == 2
    assert [q.source_question_number for q in theme.questions] == ["7", "7"]
    assert [q.source_ref["source_atomic_part_id"] for q in theme.questions] == [
        "WAVE-1", "WAVE-2",
    ]
    assert all(q.source_ref["master_parent_atomic_id"] == "MASTER-A" for q in theme.questions)
    assert "WAVE-1要求" in theme.questions[0].stem
    assert "WAVE-2要求" in theme.questions[1].stem
    model = PaperComposerModel(themes=[theme])
    assert model.total_questions == 2
    assert sum(q.score for q in theme.questions) == 4
    assert source == original


def test_composer_does_not_guess_unknown_alias_order():
    part = unit("WAVE-1", 1)
    part["atomic_sequence_in_printed"] = None
    group = catalog([parent(aliases=[part])])["papers"][0]["theme_groups"][0]
    with pytest.raises(PaperExportAliasProjectionError) as error:
        _theme_from_group(group, "fallback", scope="master")
    assert error.value.code == "paper_export_alias_order_unknown"


def test_plain_theme_keeps_existing_unknown_metadata_behavior():
    group = catalog([parent()])["papers"][0]["theme_groups"][0]
    group["atomic_chain"][0]["dependency"] = None
    theme = _theme_from_group(group, "fallback", scope="master")
    assert len(theme.questions) == 1
    assert theme.questions[0].key == "MASTER-A"


def test_question_score_visibility_defaults_hidden_invalidates_preview_and_restores():
    group = catalog([parent()])["papers"][0]["theme_groups"][0]
    theme = _theme_from_group(group, "fallback", scope="master")
    model = PaperComposerModel(themes=[theme])

    assert model.show_question_scores is False
    hidden = model.make_preview()
    assert hidden["show_question_scores"] is False
    assert hidden["themes"][0]["score"] == theme.score
    first_hash = model.preview_hash

    model.set_show_question_scores(True)
    assert model.show_question_scores is True
    assert model.preview is None
    assert model.preview_hash is None

    shown = model.make_preview()
    assert shown["show_question_scores"] is True
    assert model.preview_hash != first_hash

    payload = model.draft_payload()
    assert payload["show_question_scores"] is True
    restored = PaperComposerModel.from_draft_payload(
        payload,
        [
            {
                "key": theme.source_identity_sha256 or theme.key,
                "title_zh": theme.title,
                "atomic_total": theme.question_count,
            }
        ],
    )
    assert restored is not None
    assert restored.show_question_scores is True


def test_question_score_visibility_rejects_non_boolean_draft_values():
    group = catalog([parent()])["papers"][0]["theme_groups"][0]
    theme = _theme_from_group(group, "fallback", scope="master")
    model = PaperComposerModel(themes=[theme])
    payload = model.draft_payload()
    payload["show_question_scores"] = "false"

    assert PaperComposerModel.from_draft_payload(payload, []) is None
    with pytest.raises((TypeError, ValueError)):
        model.set_show_question_scores("false")  # type: ignore[arg-type]


def test_known_datong_answer_area_defaults_to_zero_without_overriding_explicit_space():
    known_id = "W1-DT2025-H1-MID-AP-DT2025-H1-Q01-P01"
    assert known_id in EXISTING_ANSWER_AREA_NODE_IDS

    base = {
        "atomic_part_id": known_id,
        "item_type": "fill_blank",
        "response_requirement_zh": "填写答案",
    }
    assert ComposerQuestion.from_atomic(base, 0).answer_space == 0

    unknown = dict(base, atomic_part_id="not-a-verified-answer-area")
    assert ComposerQuestion.from_atomic(unknown, 0).answer_space is None

    explicit_lines = dict(base, answer_space_lines=3)
    assert ComposerQuestion.from_atomic(explicit_lines, 0).answer_space == 3

    explicit_alias = dict(base, answer_space=4)
    assert ComposerQuestion.from_atomic(explicit_alias, 0).answer_space == 4

"""Narrow missing-label completion; all persisted sources and stores are synthetic."""

from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1 import (
    desktop_word_question_attributes as attributes,
)
from integrations.deeptutor_shchem_v1.desktop_word_questions import (
    WordQuestionError,
    WordQuestionService,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_word_auto_attributes import (
    _range,
    _rows,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_word_auto_attributes import (
    setup as setup,  # noqa: PLC0414 -- pytest fixture re-export
)
from staging.coordination.deeptutor_gateway.tests.test_word_attribute_completion import (
    directory,
)
from staging.coordination.deeptutor_gateway.tests.test_word_question_attributes import (
    metadata,
    question,
)


def _sealed(row, **changes):
    return attributes.validate_attributes(attributes._seal({**deepcopy(row), **changes}))


def _knowledge(identifier, label="已有合成知识点"):
    return {
        "id": identifier,
        "label": label,
        "status": "unknown" if identifier == "unknown" else "auto_suggested",
        "evidence": [{"kind": "question_text", "quote": "合成原文依据", "block_index": 2}],
    }


def _proposal():
    result = attributes.suggest_attributes(
        question("（2025·上海·高三二模）解释氧化还原反应并配平方程式。"),
        metadata(),
        directory("氧化还原反应"),
    )
    assert result["primary_knowledge"]["id"] == "K11"
    assert result["curriculum_candidates"]
    return result


def _missing(proposed):
    return _sealed(
        proposed,
        rule_revision="synthetic-legacy-v1",
        primary_knowledge=_knowledge("unknown", "原有待核对主题"),
        supporting_knowledge=[_knowledge("K08", "已保存辅助知识点")],
        curriculum_candidates=[],
        curriculum_status="pending_mapping",
    )


@pytest.mark.parametrize("missing_primary", [False, True])
@pytest.mark.parametrize("missing_mapping", [False, True])
def test_only_missing_target_fields_change_and_existing_nonempty_fields_survive(
    missing_primary, missing_mapping
):
    proposed = _proposal()
    old_mapping = {
        **deepcopy(proposed["curriculum_candidates"][0]),
        "section_key": "existing-section",
        "label": "已保存教材节",
    }
    existing = _sealed(
        _missing(proposed),
        primary_knowledge=_knowledge("unknown" if missing_primary else "K04"),
        curriculum_candidates=[] if missing_mapping else [old_mapping],
        curriculum_status="pending_mapping" if missing_mapping else "auto_suggested",
        teacher_note="保留已有导入备注；并非新增教师确认",
    )
    # Even empty fields outside the two permitted targets must not be filled.
    proposed = _sealed(
        proposed,
        source={**proposed["source"], "lecture_topic": "新的来源定位建议"},
        applicable_grades={**proposed["applicable_grades"], "values": ["grade_10"]},
        answer_status={**proposed["answer_status"], "value": "new-answer-suggestion"},
        teacher_note="新建议不得覆盖已有备注",
    )
    before = deepcopy((existing, proposed))
    result = attributes.complete_missing_attributes(existing, proposed)
    assert (existing, proposed) == before
    assert result["primary_knowledge"] == (
        proposed["primary_knowledge"] if missing_primary else existing["primary_knowledge"]
    )
    assert result["curriculum_candidates"] == (
        proposed["curriculum_candidates"] if missing_mapping else existing["curriculum_candidates"]
    )
    assert result["curriculum_status"] == (
        proposed["curriculum_status"] if missing_mapping else existing["curriculum_status"]
    )
    allowed = {"primary_knowledge", "curriculum_candidates", "curriculum_status", "rule_revision", "revision"}
    assert {k: v for k, v in result.items() if k not in allowed} == {
        k: v for k, v in existing.items() if k not in allowed
    }
    if missing_primary or missing_mapping:
        assert result["rule_revision"] == attributes.RULE_REVISION
        assert result["revision"] != existing["revision"]
    else:
        assert result == existing  # Do not rewrite a complete row just to stamp v2.
    assert attributes.validate_attributes(result) == result


def test_unknown_proposal_does_not_change_existing_fields_or_revision():
    proposed = _missing(_proposal())
    existing = _sealed(proposed, rule_revision="synthetic-older-rules")
    assert attributes.complete_missing_attributes(existing, proposed) == existing


@pytest.mark.parametrize("protection", ["teacher", "pinned-prefix", "pinned-suffix"])
def test_teacher_and_pinned_rows_remain_wholly_unchanged(protection):
    proposed = _proposal()
    existing = _missing(proposed)
    if protection == "teacher":
        existing = attributes.apply_teacher_edits(existing, {"teacher_note": "教师保留待确认"})
    else:
        existing = _sealed(
            existing,
            rule_revision="pinned-source-review" if protection == "pinned-prefix" else "synthetic-v1-pinned",
        )
    before = deepcopy((existing, proposed))
    result = attributes.complete_missing_attributes(existing, proposed)
    assert result == existing
    assert (existing, proposed) == before
    assert result is not existing
    result["source"]["lecture_topic"] = "外部修改返回值"
    assert (existing, proposed) == before


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("key", "different-question"),
        ("source_sha256", "b" * 64),
        ("source_revision", "source-v2"),
        ("question_revision", "question-v2"),
        ("index_revision", "index-v4"),
        ("extraction_revision", "extract-v2"),
    ],
)
@pytest.mark.parametrize("protected", [False, True])
def test_source_or_range_mismatch_is_rejected_even_for_protected_rows(
    field, changed, protected
):
    proposed = _proposal()
    existing = _missing(proposed)
    if protected:
        existing = _sealed(existing, rule_revision="synthetic-pinned-v1")
    proposed = _sealed(proposed, **{field: changed})
    before = deepcopy((existing, proposed))
    with pytest.raises(attributes.WordQuestionAttributeError, match="范围或来源已变化"):
        attributes.complete_missing_attributes(existing, proposed)
    assert (existing, proposed) == before


@pytest.mark.parametrize("target", ["existing", "proposed"])
def test_invalid_content_digest_is_rejected_before_merge(target):
    rows = {"proposed": _proposal()}
    rows["existing"] = _missing(rows["proposed"])
    rows[target]["teacher_note"] = "未重新封存的变更"
    before = deepcopy(rows)
    with pytest.raises(attributes.WordQuestionAttributeError, match="内容与版本不一致"):
        attributes.complete_missing_attributes(rows["existing"], rows["proposed"])
    assert rows == before


def test_primary_promotion_removes_its_supporting_duplicates_without_adding_new_tags():
    proposed = _proposal()
    existing = _sealed(
        _missing(proposed),
        supporting_knowledge=[
            _knowledge("K11", "先前辅助标签甲"),
            _knowledge("K08", "保留辅助标签甲"),
            _knowledge("K11", "先前辅助标签乙"),
            _knowledge("K04", "保留辅助标签乙"),
        ],
    )
    proposed = _sealed(proposed, supporting_knowledge=[_knowledge("K16", "不得额外合并")])
    before = deepcopy((existing, proposed))
    result = attributes.complete_missing_attributes(existing, proposed)
    assert result["primary_knowledge"]["id"] == "K11"
    assert result["supporting_knowledge"] == [
        existing["supporting_knowledge"][1], existing["supporting_knowledge"][3]
    ]
    assert result["primary_knowledge"]["id"] not in {row["id"] for row in result["supporting_knowledge"]}
    assert (existing, proposed) == before


def test_completion_is_idempotent_and_returned_nested_values_are_independent():
    proposed = _proposal()
    existing = _missing(proposed)
    before = deepcopy((existing, proposed))
    completed = attributes.complete_missing_attributes(existing, proposed)
    again = attributes.complete_missing_attributes(completed, proposed)
    assert again == completed and again is not completed
    assert again["revision"] == completed["revision"]
    again["primary_knowledge"]["evidence"][0]["quote"] = "外部修改"
    again["curriculum_candidates"][0]["label"] = "外部修改"
    assert (existing, proposed) == before
    assert completed["primary_knowledge"] == proposed["primary_knowledge"]
    assert completed["curriculum_candidates"] == proposed["curriculum_candidates"]


def _service_proposals(service):
    catalog = attributes.load_attribute_catalog(service.facade.paths.workspace_root)
    return [attributes.suggest_attributes(row, {"source_name": row["source_name"]}, catalog) for row in _rows(service)]


def test_only_missing_service_persists_exact_completion_and_retry_keeps_history(setup):
    service, facade, source, _ = setup
    source_bytes = source.read_bytes()
    preview = service.reader.word_preview_bytes(source_bytes, source.name)
    service.preview_cache.save(source_bytes, source.name, preview)
    cached = {path.name: path.read_bytes() for path in service.preview_cache.root.iterdir()}
    first, second = _service_proposals(service)
    saved = service.attribute_store.save_many([
        _missing(first),
        _sealed(second, primary_knowledge=_knowledge("K04", "已有非空主标签")),
    ])
    history = {row["key"]: service.attribute_store.history(row["key"]) for row in saved}
    result = service.annotate_imported_batch("B1", only_missing=True)
    assert result["model_invoked"] is False and result["method"] == "local_rules"
    assert result["question_count"] == result["saved_question_count"] == 2
    assert result["changed_question_count"] == 1
    completed = service.attribute_store.get(first["key"])
    assert completed["primary_knowledge"] == first["primary_knowledge"]
    assert completed["curriculum_candidates"] == first["curriculum_candidates"]
    assert completed["supporting_knowledge"] == saved[0]["supporting_knowledge"]
    assert completed["edit_version"] == saved[0]["edit_version"] + 1
    assert service.attribute_store.get(second["key"]) == saved[1]
    for key in ("source", "original_source", "applicable_grades", "answer_status", "material_status"):
        assert completed[key] == saved[0][key]
    assert service.attribute_store.history(first["key"]) == [*history[first["key"]], completed]
    assert service.attribute_store.history(second["key"]) == history[second["key"]]
    reopened = WordQuestionService(facade)
    assert reopened.attribute_store.get(first["key"]) == completed
    before_retry = reopened.attribute_store.history(first["key"])
    assert reopened.annotate_imported_batch("B1", only_missing=True)["changed_question_count"] == 0
    assert reopened.attribute_store.history(first["key"]) == before_retry
    assert source.read_bytes() == source_bytes
    assert {path.name: path.read_bytes() for path in service.preview_cache.root.iterdir()} == cached
    assert not facade.state_store.path.exists()


@pytest.mark.parametrize("protection", ["teacher", "pinned"])
def test_only_missing_service_preserves_protected_rows_and_history(setup, protection):
    service, _, source, _ = setup
    first, second = _service_proposals(service)
    row = _missing(first)
    if protection == "pinned":
        row = _sealed(row, rule_revision="synthetic-source-pinned-r1")
    saved = service.attribute_store.save_many([row, second])[0]
    if protection == "teacher":
        saved = service.attribute_store.save_teacher_edit(
            saved["key"], {"teacher_note": "教师暂不补齐此题"}, expected_revision=saved["revision"]
        )
    frozen_source = source.read_bytes()
    frozen_history = service.attribute_store.history(saved["key"])
    result = service.annotate_imported_batch("B1", only_missing=True)
    assert result["changed_question_count"] == 0
    assert result["preserved_teacher_count"] == int(protection == "teacher")
    assert service.attribute_store.get(saved["key"]) == saved
    assert service.attribute_store.history(saved["key"]) == frozen_history
    assert source.read_bytes() == frozen_source


@pytest.mark.parametrize("stale", ["source", "range"])
def test_only_missing_service_rejects_stale_binding_without_partial_commit(setup, stale):
    service, _, source, _ = setup
    rows = _rows(service)
    first, second = _service_proposals(service)
    stale_row = _missing(second)
    if stale == "source":
        stale_row = _sealed(stale_row, source_sha256="f" * 64)
    saved = service.attribute_store.save_many([_missing(first), stale_row])
    if stale == "range":
        _range(service, rows[1])
    history = {row["key"]: service.attribute_store.history(row["key"]) for row in saved}
    frozen_source = source.read_bytes()
    with pytest.raises(WordQuestionError, match="未提交"):
        service.annotate_imported_batch("B1", only_missing=True)
    assert service.attribute_store.get_many([row["key"] for row in saved]) == {row["key"]: row for row in saved}
    assert {row["key"]: service.attribute_store.history(row["key"]) for row in saved} == history
    assert source.read_bytes() == frozen_source


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", {}])
def test_only_missing_service_requires_an_explicit_boolean_before_writing(setup, invalid):
    service, facade, _, _ = setup
    with pytest.raises(WordQuestionError, match="布尔值"):
        service.annotate_imported_batch("B1", only_missing=invalid)
    assert not service.attribute_store.root.exists()
    assert not facade.reads

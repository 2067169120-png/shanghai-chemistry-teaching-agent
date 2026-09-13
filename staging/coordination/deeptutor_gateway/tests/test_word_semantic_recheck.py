"""Automatic-label recheck contracts; all providers and questions are synthetic."""

from __future__ import annotations

from copy import deepcopy

import pytest
from test_word_semantic_tags import (
    CATALOG,
    SemanticProvider,
    SemanticTransport,
    _attributes,
    _catalog,
    _choices,
    _make_facade,
    _response,
    _seal,
    _unit,
)
from test_word_semantic_tags import (
    desktop_paths as desktop_paths,  # noqa: PLC0414 - pytest fixture re-export
)

from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    WordQuestionAttributeError,
    automatic_tags_protected,
    recheck_automatic_attributes,
    suggest_attributes,
)
from integrations.deeptutor_shchem_v1.desktop_word_semantic_tags import (
    WordSemanticTagError,
    merge_response,
)


def _automatic_attributes(
    *, primary="K05", sections=("n-metal",), supporting=(), identity=None
):
    value = _attributes()
    point = next(row for row in CATALOG["knowledge_points"] if row["id"] == primary)
    value["primary_knowledge"] = {
        "id": primary,
        "label": point["name"],
        "status": "auto_suggested",
        "evidence": [
            {"kind": "question_text", "quote": "合成自动标签依据", "block_index": 1}
        ],
    }
    value["supporting_knowledge"] = [
        {
            "id": identifier,
            "label": next(
                row["name"]
                for row in CATALOG["knowledge_points"]
                if row["id"] == identifier
            ),
            "status": "auto_suggested",
            "evidence": [
                {
                    "kind": "question_text",
                    "quote": "合成辅助标签依据",
                    "block_index": 1,
                }
            ],
        }
        for identifier in supporting
    ]
    value["curriculum_candidates"] = []
    for section_key in sections:
        node = next(row for row in CATALOG["nodes"] if row["node_key"] == section_key)
        value["curriculum_candidates"].append(
            {
                "section_key": section_key,
                "chapter_id": node["chapter_id"],
                "volume_id": node["volume_id"],
                "label": node["section_title"],
                "status": "auto_suggested",
                "evidence": [
                    {
                        "kind": "question_text",
                        "quote": "合成教材映射依据",
                        "block_index": 1,
                    }
                ],
            }
        )
    value["curriculum_status"] = "auto_suggested" if sections else "pending_mapping"
    if identity:
        for key, replacement in identity.items():
            value[key] = deepcopy(replacement)
    return _seal(value)


def _stored_attributes(facade, choice, **kwargs):
    rows, _ = facade._word_questions()._resolve([choice])
    row = rows[0]
    value = suggest_attributes(row, {"source_name": row["source_name"]}, _catalog())
    old = _automatic_attributes(**kwargs)
    for key in (
        "key",
        "source_sha256",
        "source_revision",
        "index_revision",
        "extraction_revision",
        "question_revision",
        "source",
        "response_forms",
        "answer_status",
        "material_status",
        "applicable_grades",
        "original_source",
    ):
        old[key] = deepcopy(value[key])
    old["rule_revision"] = value["rule_revision"]
    return _seal(old)


def _save_attribute(facade, value):
    assert facade._word_questions().attribute_store.save_many([value])


def test_default_mode_is_missing_only_and_every_plan_unit_binds_mode(
    desktop_paths, tmp_path
):
    provider = SemanticProvider()
    transport = SemanticTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    choice = _choices(facade, 1)[0]

    plan = facade.word_semantic_tag_preview([choice], "semantic", provider.revision)

    assert plan["mode"] == "missing_only"
    assert [unit["mode"] for unit in plan["units"]] == ["missing_only"]
    assert plan["units"][0]["status"] == "ready"
    assert transport.calls == []


@pytest.mark.parametrize("mode", [None, "", "unknown", "recheck", 1, True, [], {}])
def test_facade_and_service_reject_invalid_recheck_modes_before_transport(
    desktop_paths, tmp_path, mode
):
    provider = SemanticProvider()
    transport = SemanticTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    choices = _choices(facade, 1)

    with pytest.raises(WordSemanticTagError, match="只补缺失|重新核对"):
        facade.word_semantic_tag_preview(
            choices, "semantic", provider.revision, mode=mode
        )
    with pytest.raises(WordSemanticTagError, match="只补缺失|重新核对"):
        facade._word_semantic_tags().preview(
            choices, "semantic", provider.revision, mode=mode
        )
    assert transport.calls == []


def test_recheck_sends_a_complete_automatic_question_and_can_replace_ids(
    desktop_paths, tmp_path
):
    provider = SemanticProvider()
    transport = SemanticTransport(primary_ids=["K10"], section_keys=["n-solution"])
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    choice = _choices(facade, 1)[0]
    old = _stored_attributes(
        facade, choice, primary="K05", sections=("n-metal",), supporting=("K10",)
    )
    _save_attribute(facade, old)

    plan = facade.word_semantic_tag_preview(
        [choice], "semantic", provider.revision, mode="recheck_automatic"
    )
    unit = plan["units"][0]
    assert plan["mode"] == "recheck_automatic"
    assert unit["mode"] == "recheck_automatic"
    assert unit["status"] == "ready"
    assert plan["request_count"] == 1

    result = facade.word_semantic_tag_run(
        plan["plan_id"], plan["revision"], confirmed=True
    )
    assert len(transport.calls) == 1
    assert result["items"][0]["status"] == "ready"
    proposed = result["items"][0]["proposed"]
    assert proposed["primary_knowledge"]["id"] == "K10"
    assert [row["section_key"] for row in proposed["curriculum_candidates"]] == [
        "n-solution"
    ]
    assert proposed["source"] == old["source"]
    assert proposed["original_source"] == old["original_source"]
    assert proposed["answer_status"] == old["answer_status"]
    assert proposed["teacher_note"] == old["teacher_note"]


@pytest.mark.parametrize(
    "protection",
    [
        "teacher_modified",
        "pinned",
        "primary_confirmed",
        "supporting_confirmed",
        "curriculum_confirmed",
    ],
)
def test_recheck_skips_each_protected_question_without_transport(
    desktop_paths, tmp_path, protection
):
    provider = SemanticProvider()
    transport = SemanticTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    choice = _choices(facade, 1)[0]
    old = _stored_attributes(
        facade,
        choice,
        primary="K05",
        sections=("n-metal",),
        supporting=("K10",),
    )
    if protection == "teacher_modified":
        old["annotation_source"] = "teacher_modified"
    elif protection == "pinned":
        old["rule_revision"] += "-pinned"
    elif protection == "primary_confirmed":
        old["primary_knowledge"]["status"] = "teacher_confirmed"
    elif protection == "supporting_confirmed":
        old["supporting_knowledge"][0]["status"] = "teacher_confirmed"
    else:
        old["curriculum_candidates"][0]["status"] = "teacher_confirmed"
        old["curriculum_status"] = "teacher_confirmed"
    old = _seal(old)
    assert automatic_tags_protected(old)
    _save_attribute(facade, old)

    plan = facade.word_semantic_tag_preview(
        [choice], "semantic", provider.revision, mode="recheck_automatic"
    )
    assert plan["units"][0]["status"] == "skipped"
    assert plan["request_count"] == 0
    result = facade.word_semantic_tag_run(
        plan["plan_id"], plan["revision"], confirmed=True
    )
    assert result["finished"] is True and result["items"] == []
    assert transport.calls == []


def test_recheck_mode_is_part_of_plan_hash_and_run_recompiles_that_mode(
    desktop_paths, tmp_path, monkeypatch
):
    provider = SemanticProvider()
    transport = SemanticTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    choice = _choices(facade, 1)[0]
    missing = facade.word_semantic_tag_preview([choice], "semantic", provider.revision)
    recheck = facade.word_semantic_tag_preview(
        [choice], "semantic", provider.revision, mode="recheck_automatic"
    )
    assert missing["revision"] != recheck["revision"]
    assert recheck["mode"] == "recheck_automatic"

    service = facade._word_semantic_tags()
    modes = []
    original = service._compile

    def counted(*args, **kwargs):
        modes.append(kwargs.get("mode"))
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_compile", counted)
    facade.word_semantic_tag_run(
        recheck["plan_id"], recheck["revision"], confirmed=True
    )
    assert modes == ["recheck_automatic"]
    assert len(transport.calls) == 1

    service._plans[recheck["plan_id"]]["mode"] = "missing_only"
    with pytest.raises(WordSemanticTagError, match="变|重新预览"):
        facade.word_semantic_tag_run(
            recheck["plan_id"], recheck["revision"], confirmed=True
        )


def test_merge_response_defaults_to_missing_only_but_recheck_changes_automatic_ids():
    old = _automatic_attributes(primary="K05", sections=("n-metal",))
    decoded = _response(primary="K10", section="n-solution")

    assert merge_response(decoded, _unit(old), _catalog()) == old
    unit = _unit(old)
    unit["mode"] = "recheck_automatic"
    changed = merge_response(decoded, unit, _catalog())
    assert changed["primary_knowledge"]["id"] == "K10"
    assert [row["section_key"] for row in changed["curriculum_candidates"]] == [
        "n-solution"
    ]


def test_recheck_unknown_or_empty_results_keep_old_automatic_labels():
    old = _automatic_attributes(primary="K05", sections=("n-metal",))
    unit = _unit(old)
    unit["mode"] = "recheck_automatic"
    unknown = merge_response(
        _response(primary="unknown", section=None), unit, _catalog()
    )
    assert unknown == old


def test_recheck_same_id_and_same_section_set_is_a_noop_even_if_evidence_is_reordered():
    old = _automatic_attributes(primary="K05", sections=("n-metal", "n-solution"))
    proposed = deepcopy(old)
    proposed["primary_knowledge"]["evidence"] = list(
        reversed(proposed["primary_knowledge"]["evidence"])
    )
    proposed["curriculum_candidates"] = list(
        reversed(proposed["curriculum_candidates"])
    )
    proposed["curriculum_candidates"][0]["evidence"] = [
        {
            "kind": "question_text",
            "quote": "模型改述依据",
            "block_index": 1,
        }
    ]
    proposed = _seal(proposed)
    assert recheck_automatic_attributes(old, proposed) == old


def test_recheck_primary_change_drops_duplicate_supporting_without_adding_old_primary():
    old = _automatic_attributes(
        primary="K05", sections=("n-metal",), supporting=("K10",)
    )
    proposed = _automatic_attributes(
        primary="K10", sections=("n-solution",), supporting=()
    )
    changed = recheck_automatic_attributes(old, proposed)
    assert changed["primary_knowledge"]["id"] == "K10"
    assert changed["supporting_knowledge"] == []
    assert "K05" not in [row["id"] for row in changed["supporting_knowledge"]]


def test_recheck_changes_only_automatic_primary_and_curriculum_fields():
    old = _automatic_attributes(primary="K05", sections=("n-metal",))
    proposed = _automatic_attributes(primary="K10", sections=("n-solution",))
    proposed["teacher_note"] = "模型不应覆盖教师备注"
    proposed["applicable_grades"] = {
        "values": ["grade_10"],
        "basis": "模型不应改变",
        "status": "usage_positioning",
        "evidence": [],
    }
    proposed["original_source"]["display_label"] = "模型不应改变原题出处"
    proposed["response_forms"] = []
    proposed = _seal(proposed)

    changed = recheck_automatic_attributes(old, proposed)
    assert changed["primary_knowledge"]["id"] == "K10"
    assert changed["curriculum_candidates"][0]["section_key"] == "n-solution"
    for key in (
        "teacher_note",
        "applicable_grades",
        "original_source",
        "response_forms",
    ):
        assert changed[key] == old[key]
    assert changed["key"] == old["key"]
    assert changed["question_revision"] == old["question_revision"]


def test_recheck_keeps_anti_fake_evidence_validation():
    unit = _unit(_automatic_attributes(primary="K05", sections=("n-metal",)))
    unit["mode"] = "recheck_automatic"
    value = _response(primary="K10", section="n-solution")
    value["primary"]["evidence"][0]["quote"] = "不是本题原文"
    with pytest.raises(WordSemanticTagError, match="不在本题原文"):
        merge_response(value, unit, _catalog())


def test_recheck_apply_preserves_compare_and_swap_on_stale_attributes(
    desktop_paths, tmp_path
):
    provider = SemanticProvider()
    transport = SemanticTransport(primary_ids=["K10"], section_keys=["n-solution"])
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    choice = _choices(facade, 1)[0]
    old = _stored_attributes(facade, choice, primary="K05", sections=("n-metal",))
    _save_attribute(facade, old)
    plan = facade.word_semantic_tag_preview(
        [choice], "semantic", provider.revision, mode="recheck_automatic"
    )
    facade.word_semantic_tag_run(plan["plan_id"], plan["revision"], confirmed=True)
    current = facade._word_questions().attribute_store.get(choice["key"])
    assert current is not None
    changed = deepcopy(current)
    changed["teacher_note"] = "并发教师修改"
    changed = _seal(changed)
    facade._word_questions().attribute_store.save_many(
        [changed], expected_revisions={choice["key"]: current["revision"]}
    )
    with pytest.raises(
        (WordSemanticTagError, WordQuestionAttributeError), match="标签已被修改|重新"
    ):
        facade.word_semantic_tag_apply(plan["plan_id"], [choice["key"]])
    assert (
        facade._word_questions().attribute_store.get(choice["key"])["teacher_note"]
        == "并发教师修改"
    )


def test_recheck_stale_source_is_rejected_without_transport(desktop_paths, tmp_path):
    provider = SemanticProvider()
    transport = SemanticTransport()
    facade = _make_facade(
        desktop_paths, tmp_path, provider, transport, question_count=1
    )
    choice = _choices(facade, 1)[0]
    plan = facade.word_semantic_tag_preview(
        [choice], "semantic", provider.revision, mode="recheck_automatic"
    )
    archive = (
        desktop_paths.state_root
        / "visual-import-v2"
        / "sources"
        / f"{facade.word_question_catalog()['items'][0]['source_sha256']}.docx"
    )
    archive.write_bytes(b"stale source fixture for recheck")
    with pytest.raises((WordSemanticTagError, ValueError), match="变|原文|范围"):
        facade.word_semantic_tag_run(plan["plan_id"], plan["revision"], confirmed=True)
    assert transport.calls == []


@pytest.mark.parametrize(
    ("code", "expected_code", "expected_note"),
    [
        (
            "provider_response_too_large",
            "provider_response_too_large",
            "模型响应超过安全容量，本题未写入",
        ),
        (
            "transport_private_failure",
            "word_semantic_tags_failed",
            "模型未返回可用的标签，本题未写入",
        ),
    ],
)
def test_transport_failure_uses_safe_whitelisted_message_without_private_exception_text(
    desktop_paths, tmp_path, code, expected_code, expected_note
):
    class LeakyTransport:
        def send(self, *_args, **_kwargs):
            error = RuntimeError("SECRET_API_KEY=do-not-leak")
            error.code = code
            error.message_zh = "private request details: SECRET_API_KEY=do-not-leak"
            raise error

    provider = SemanticProvider()
    facade = _make_facade(
        desktop_paths,
        tmp_path,
        provider,
        LeakyTransport(),
        question_count=1,
    )
    choice = _choices(facade, 1)[0]
    plan = facade.word_semantic_tag_preview(
        [choice], "semantic", provider.revision, mode="recheck_automatic"
    )
    result = facade.word_semantic_tag_run(
        plan["plan_id"], plan["revision"], confirmed=True
    )
    item = result["items"][0]
    assert item["failure_code"] == expected_code
    assert item["note"] == expected_note
    assert "SECRET_API_KEY" not in item["note"]

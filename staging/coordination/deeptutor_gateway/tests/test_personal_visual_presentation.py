"""Pure synthetic presentation tests; no facade, persistence, or provider."""

from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_personal_visual_presentation import (
    FORMAT_VERSION,
    build_personal_visual_presentation,
)


def expression(raw, *, kind="formula", status="observed", refs=("E-Q",)):
    return {"raw": raw, "kind": kind, "status": status, "evidence_refs": list(refs)}


def node(stem="", **updates):
    return {"stem": stem, "options": [], "response_requirements": "", "chemical_expressions": [],
            "visual_object_refs": [], "evidence_refs": ["E-Q"], **updates}


def atomic(stem="", *, label=None, identifier="AP1", **updates):
    values = {"atomic_part_id": identifier, "part_label": label,
              "answer": {"status": "source_answer_candidate", "answer_body": "来源答案", "analysis": "",
                         "chemical_expressions": [], "evidence_refs": ["E-A"], "max_score": 2,
                         "scoring_points": []}, **updates}
    return node(stem, **values)


@pytest.fixture
def inputs():
    printed = node("", printed_question_id="PQ1", shared_material_refs=[], atomic_parts=[])
    theme = {"theme_big_question_id": "T1", "context": "整个主题背景，不自动给每题",
             "shared_materials": [], "printed_questions": [printed], "dependency_edges": [], "visual_objects": []}
    row = {"revision": "frozen-existing-question-revision", "question_text": "  原始题目全文\n重复全文  ",
           "shared_text": "原始材料全文", "answer_text": "原始答案全文\n同一公式再次出现",
           "images": [{"image_id": "IMG-Q", "evidence_id": "E-Q", "role": "question"}],
           "attributes": {"revision": "saved-tag-revision", "teacher_confirmed": False},
           "warnings": ["原警告不能被展示函数修改"]}
    return theme, printed, row


def show(inputs):
    return build_personal_visual_presentation(*inputs)


def blocks(presentation, kind):
    return [block for block in presentation["source_blocks"] if block["kind"] == kind]


def test_exact_parent_child_stem_fold_keeps_new_requirement_and_unique_second_step(inputs):
    _, printed, _ = inputs
    printed["stem"] = "第一步已给出。\n第二步独有实验条件。"
    printed["response_requirements"] = "写出第三步方程式"
    printed["atomic_parts"] = [atomic("第一步已给出。", label="(1)", response_requirements="说明气体现象")]
    result = show(inputs)
    assert result["question_text"].count("第一步已给出。") == 1
    assert "第二步独有实验条件。" in result["question_text"]
    assert "写出第三步方程式" in result["question_text"] and "说明气体现象" in result["question_text"]
    assert "(1)" in result["question_text"]
    assert blocks(result, "stem")[1]["folded_into"] == blocks(result, "stem")[0]["block_id"]


def test_requirement_fold_is_literal_not_paraphrase_or_negation(inputs):
    _, printed, _ = inputs
    printed["stem"] = "不需要配平该反应"
    printed["response_requirements"] = "配平该反应"
    result = show(inputs)
    assert blocks(result, "response_requirements")[0]["folded_into"] is None
    printed["stem"] = "配平该反应"
    assert blocks(show(inputs), "response_requirements")[0]["folded_into"] is not None
    printed["stem"] = "配平上述反应"
    assert blocks(show(inputs), "response_requirements")[0]["folded_into"] is None


@pytest.mark.parametrize("body,raw,fold", [
    ("生成 H₂O。", "H₂O", True),
    ("生成H₂O。", "H₂O", True),
    ("生成 H2O。", "H₂O", False),
    ("生成 H₂O。", "H2O", False),
    ("含 Fe²⁺。", "Fe³⁺", False),
    ("含 Fe²⁺。", "Fe", False),
    ("含 Fe²⁺。", "Fe²⁺", True),
    ("含 CO₂。", "CO", False),
    ("含 CaCO₃。", "Ca", False),
    ("含 H₂O(g)。", "H₂O", False),
    ("含 H₂O↑。", "H₂O", False),
    ("含 C₂H₂。", "H₂", False),
    ("CO 与 O₂ 反应。", "CO₂", False),
    ("浓度为 10^-3。", "10", False),
    ("温度为 10℃。", "10", False),
    ("含 Fe^3+。", "Fe", False),
])
def test_chemistry_uses_exact_complete_tokens_never_normalization(inputs, body, raw, fold):
    _, printed, _ = inputs
    printed.update(stem=body, chemical_expressions=[expression(raw)])
    block = blocks(show(inputs), "chemical_expression")[0]
    assert (block["folded_into"] is not None) is fold


def test_complete_equation_folds_but_new_condition_keeps_equation_visible(inputs):
    _, printed, _ = inputs
    equation = "2H₂(g)+O₂(g)=2H₂O(g)"
    printed.update(stem="反应为 " + equation + "。", chemical_expressions=[expression(equation, kind="equation")])
    assert blocks(show(inputs), "chemical_expression")[0]["folded_into"] is not None
    printed["chemical_expressions"].append(expression("500 K", kind="condition"))
    result = show(inputs)
    assert all(block["folded_into"] is None for block in blocks(result, "chemical_expression"))
    assert "500 K" in result["question_text"]
    printed["stem"] += " 条件为 500 K。"
    assert all(block["folded_into"] is not None for block in blocks(show(inputs), "chemical_expression"))


def test_distinct_parent_child_conditions_do_not_fold_a_repeated_stem(inputs):
    _, printed, _ = inputs
    printed.update(stem="研究同一反应。", chemical_expressions=[expression("300 K", kind="condition")])
    printed["atomic_parts"] = [atomic("研究同一反应。", chemical_expressions=[expression("500 K", kind="condition")])]
    result = show(inputs)
    assert result["question_text"].count("研究同一反应。") == 2
    assert "300 K" in result["question_text"] and "500 K" in result["question_text"]


def test_uncertain_or_different_evidence_expression_is_not_hidden(inputs):
    _, printed, _ = inputs
    printed.update(stem="生成 H₂O。", chemical_expressions=[
        expression("H₂O", status="uncertain"), expression("H₂O", refs=("DIFFERENT-E",)),
        expression("H₂O", refs=()),
    ])
    result = show(inputs)
    assert all(block["folded_into"] is None for block in blocks(result, "chemical_expression"))
    assert blocks(result, "chemical_expression")[0]["status"] == "uncertain"


def test_options_with_same_content_keep_distinct_labels_and_unique_option_chemistry(inputs):
    _, printed, _ = inputs
    printed["options"] = [dict(node(), label=label, content="相同文字") for label in ("A", "B")]
    printed["atomic_parts"] = [atomic(options=[dict(node(), label="A", content="相同文字",
        chemical_expressions=[expression("唯一选项公式 NH₃")])])]
    result = show(inputs)
    assert "A  相同文字" in result["question_text"] and "B  相同文字" in result["question_text"]
    assert "A  唯一选项公式 NH₃" in result["question_text"]
    assert blocks(result, "option")[1]["folded_into"] is None
    assert blocks(result, "option")[2]["folded_into"] == blocks(result, "option")[0]["block_id"]


def test_same_string_in_sibling_atomic_questions_and_answers_is_preserved(inputs):
    _, printed, _ = inputs
    printed["atomic_parts"] = [atomic("解释实验现象。", identifier=f"AP{i}", label=f"({i})") for i in (1, 2)]
    for part in printed["atomic_parts"]:
        part["answer"].update(answer_body="H₂O", chemical_expressions=[expression("H₂O", refs=("E-A",))])
    result = show(inputs)
    assert result["question_text"].count("解释实验现象。") == 2
    assert result["answer_text"].count("H₂O") == 2
    assert result["answer_text"].count("来源参考分值：2分") == 2
    assert all(label in result["answer_text"] for label in ("(1)", "(2)"))
    bodies = blocks(result, "answer_body")
    assert all(block["folded_into"] is None for block in bodies)
    assert {block["folded_into"] for block in blocks(result, "chemical_expression")} == {block["block_id"] for block in bodies}


def test_question_and_answer_never_fold_across_roles(inputs):
    _, printed, _ = inputs
    printed["stem"] = "H₂O"
    printed["atomic_parts"] = [atomic(answer={"status": "source_answer_candidate", "answer_body": "H₂O",
        "analysis": "必须保留的来源解析", "chemical_expressions": [], "evidence_refs": ["E-Q"],
        "max_score": 3, "scoring_points": [{"score": 1, "description": "完整得分点", "evidence_refs": ["E-Q"]}]})]
    result = show(inputs)
    assert result["question_text"] == "H₂O"
    assert "H₂O" in result["answer_text"] and "必须保留的来源解析" in result["answer_text"]
    assert "1分：完整得分点" in result["answer_text"]
    assert blocks(result, "answer_body")[0]["folded_into"] is None


def test_shared_material_scope_is_explicit_and_never_guesses_not_required(inputs):
    theme, printed, _ = inputs
    theme["shared_materials"] = [dict(node(), shared_material_id="SM1", content="共有 H₂O。",
        chemical_expressions=[expression("H₂O")]), dict(node(), shared_material_id="SM2", content="未关联材料")]
    result = show(inputs)
    assert result["shared_text"] == "" and result["shared_scope"]["status"] == "unlinked"
    assert result["shared_scope"]["confirmed_not_required"] is False
    printed["shared_material_refs"] = ["SM1"]
    result = show(inputs)
    assert result["shared_text"] == "共有 H₂O。"
    assert result["shared_scope"]["status"] == "linked"
    assert "未关联材料" not in result["shared_text"]
    printed["shared_material_refs"] = ["SM-MISSING"]
    result = show(inputs)
    assert result["shared_scope"]["status"] == "invalid_reference"
    assert result["shared_scope"]["unresolved_material_ids"] == ["SM-MISSING"]


def test_same_text_in_two_explicit_shared_materials_keeps_both_scopes(inputs):
    theme, printed, _ = inputs
    theme["shared_materials"] = [dict(node(), shared_material_id=identifier, content="同一句独立来源材料")
                                 for identifier in ("SM1", "SM2")]
    printed["shared_material_refs"] = ["SM1", "SM2"]
    result = show(inputs)
    assert result["shared_text"].count("同一句独立来源材料") == 2
    assert all(block["folded_into"] is None for block in blocks(result, "shared_content"))


def test_missing_answer_retains_part_label_and_does_not_borrow_another_answer(inputs):
    _, printed, _ = inputs
    printed["atomic_parts"] = [atomic(label="(1)"), atomic(label="(2)", identifier="AP2", answer={"status": "missing"})]
    result = show(inputs)
    assert result["answer_text"].count("来源答案") == 1
    assert "(2)\n参考答案待补充" in result["answer_text"]


def test_input_full_text_revision_images_labels_and_warnings_are_unchanged(inputs):
    _, printed, row = inputs
    printed.update(stem="  生成 H₂O。  ", chemical_expressions=[expression("H₂O")])
    before = deepcopy(inputs)
    result = show(inputs)
    assert inputs == before
    assert result["binding_revision"] == row["revision"]
    assert result["format_version"] == FORMAT_VERSION
    assert result["full_text"] == {field: row[field] for field in ("question_text", "shared_text", "answer_text")}
    assert result == show(inputs)
    by_id = {block["block_id"]: block for block in result["source_blocks"]}
    for folded in result["folded_items"]:
        child, parent = by_id[folded["block_id"]], by_id[folded["into_block_id"]]
        start, end = folded["covered_span"]
        assert parent["text"][start:end] == child["text"].strip()
        assert parent["folded_into"] is None
    result["source_blocks"][0]["evidence_refs"].append("MUTATE-RESULT")
    result["full_text"]["question_text"] = "changed only returned data"
    assert inputs == before


def test_source_paths_resolve_every_original_text_and_all_folded_text_is_retained(inputs):
    _, printed, _ = inputs
    printed.update(stem="写出 H₂O。", chemical_expressions=[expression("H₂O")])
    printed["atomic_parts"] = [atomic("写出 H₂O。")]
    result = show(inputs)
    roots = {"theme": inputs[0], "printed": inputs[1]}
    for block in result["source_blocks"]:
        value = roots
        for token in block["source_path"].strip("/").split("/"):
            value = value[int(token)] if isinstance(value, list) else value[token]
        assert block["text"] == (str(value) if block["kind"] == "max_score" else value)
    assert {block["block_id"] for block in result["source_blocks"] if block["folded_into"]} == {
        item["block_id"] for item in result["folded_items"]}


def test_display_format_version_change_does_not_change_legacy_binding(inputs, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_personal_visual_presentation as presentation

    before = deepcopy(inputs)
    initial = show(inputs)
    monkeypatch.setattr(presentation, "FORMAT_VERSION", "synthetic-new-format-only")
    revised = show(inputs)
    assert revised["format_version"] != initial["format_version"]
    assert revised["binding_revision"] == initial["binding_revision"] == inputs[2]["revision"]
    assert revised["full_text"] == initial["full_text"]
    assert inputs == before


@pytest.mark.parametrize("change", ["revision", "question_text", "shared_text", "answer_text"])
def test_missing_legacy_binding_or_full_text_is_rejected(inputs, change):
    del inputs[2][change]
    with pytest.raises(ValueError):
        show(inputs)

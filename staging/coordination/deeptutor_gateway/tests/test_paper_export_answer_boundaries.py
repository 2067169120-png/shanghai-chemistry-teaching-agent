from integrations.deeptutor_shchem_v1.paper_export_renderer import (
    _teacher_answer_boundaries_present,
)


def _plan(labels):
    return {
        "visible": {
            "theme_sections": [
                {
                    "printed_questions": [
                        {
                            "atomic_parts": [
                                {"teacher_notes": {"answer_label_zh": label}}
                            ]
                        }
                        for label in labels
                    ]
                }
            ]
        }
    }


def test_all_missing_answers_require_each_absence_label():
    plan = _plan(["暂无参考答案"] * 3)
    assert _teacher_answer_boundaries_present("暂无参考答案：未对齐。\n" * 3, plan)
    assert not _teacher_answer_boundaries_present("暂无参考答案：未对齐。", plan)
    assert not _teacher_answer_boundaries_present(
        "参考答案（非官方，未独立核验）：", plan
    )


def test_mixed_answer_states_cannot_replace_nonofficial_with_absence():
    labels = [
        "暂无参考答案",
        "答案存在，尚未逐题对齐",
        "参考答案（非官方，未独立核验）",
    ]
    plan = _plan(labels)
    assert _teacher_answer_boundaries_present(
        "\n".join(x + "：内容" for x in labels), plan
    )
    assert not _teacher_answer_boundaries_present("暂无参考答案：" * 3, plan)


def test_pdf_whitespace_is_normalized_but_missing_labels_are_not_accepted():
    plan = _plan(["参考答案（非官方，未独立核验）"])
    assert _teacher_answer_boundaries_present(
        "参考答案（非官方，\n未独立核验）：A", plan
    )
    assert not _teacher_answer_boundaries_present("参考答案：A", plan)

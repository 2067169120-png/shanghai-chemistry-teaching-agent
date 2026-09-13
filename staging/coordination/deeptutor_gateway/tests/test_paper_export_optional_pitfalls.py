from integrations.deeptutor_shchem_v1.paper_export_renderer import (
    _teacher_source_and_pitfalls_present,
)


def plan(*pitfalls):
    return {
        "visible": {
            "theme_sections": [
                {
                    "printed_questions": [
                        {
                            "atomic_parts": [
                                {"teacher_notes": {"pitfalls_zh": items}}
                                for items in pitfalls
                            ]
                        }
                    ]
                }
            ]
        }
    }


def test_no_recorded_pitfalls_does_not_require_fabricated_content():
    assert _teacher_source_and_pitfalls_present("来源标签：非官方参考", plan([], []))
    assert not _teacher_source_and_pitfalls_present("参考答案正文", plan([], []))


def test_recorded_pitfalls_need_both_label_and_content():
    expected = plan(["保留反应条件"], [])
    assert not _teacher_source_and_pitfalls_present(
        "来源标签：来源；易错点：", expected
    )
    assert not _teacher_source_and_pitfalls_present(
        "来源标签：来源；保留反应条件", expected
    )
    assert _teacher_source_and_pitfalls_present(
        "来源标签：来源；易错点：保留反应条件", expected
    )


def test_duplicate_notes_are_checked_for_every_expected_occurrence():
    expected = plan(["检查单位"], ["检查单位"])
    assert not _teacher_source_and_pitfalls_present(
        "来源标签：来源；易错点：检查单位", expected
    )
    assert _teacher_source_and_pitfalls_present(
        "来源标签：来源；易错点：检查单位；易错点：检查单位", expected
    )

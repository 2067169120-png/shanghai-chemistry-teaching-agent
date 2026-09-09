from __future__ import annotations

from dataclasses import replace

import pytest

from integrations.deeptutor_shchem_v1.desktop_blueprint_drafts import (
    BlueprintDraftError,
)
from integrations.deeptutor_shchem_v1.desktop_library import (
    LibraryImage,
    LibraryPartDetail,
    LibraryThemeDetail,
)
from integrations.deeptutor_shchem_v1.desktop_library_preparation import (
    library_preparation_reference,
)


def _image(node_id: str, *, role: str = "question") -> LibraryImage:
    """Use opaque-looking values to prove image descriptors never enter text."""

    return LibraryImage(
        scope="master",
        node_id=f"opaque-node-{node_id}",
        crop_id=f"opaque-crop-{node_id}",
        sha256="a" * 64,
        role=role,
        caption_zh="共同材料原图" if role == "shared_material" else "原题题图",
        width=1200,
        height=800,
    )


def _part(
    key: str,
    label: str,
    summary: str,
    dependency: str,
    *,
    with_images: bool = True,
) -> LibraryPartDetail:
    return LibraryPartDetail(
        key=key,
        label_zh=label,
        summary_zh=summary,
        requirement_zh=f"作答要求-{key}",
        dependency_zh=dependency,
        question_images=(_image(key),) if with_images else (),
        classification_zh=(f"作答形式-{key}", f"主知识-{key}"),
        analysis_zh=(f"analysis-{key}",),
        reference_answer_zh=f"reference-answer-{key}",
        answer_boundary_zh=f"answer-boundary-{key}",
        quality_notes_zh=(f"quality-note-{key}",),
        availability_zh=f"availability-{key}",
    )


def _detail(
    *,
    parts: tuple[LibraryPartDetail, ...] | None = None,
    with_images: bool = True,
    context: str = "主题共同材料摘要",
) -> LibraryThemeDetail:
    return LibraryThemeDetail(
        key="opaque-theme-key",
        scope="master",
        title_zh="电解质主题",
        paper_title_zh="2025 上海二模",
        source_zh="核心题库 · 非官方参考来源",
        page_zh="第 3、4 页",
        context_zh=context,
        shared_images=(_image("shared", role="shared_material"),)
        if with_images
        else (),
        parts=parts
        or (
            _part("A1", "原卷第 7 题 · 作答单元 1", "摘要-A1：判断电解质类别。", "无已记录的前题结论依赖"),
            _part("A2", "原卷第 7 题 · 作答单元 2", "摘要-A2：承接前问完成计算。", "依赖前序作答单元：原卷第 7 题（1）"),
        ),
        source_fields=(("年份", "2025"), ("区域", "上海")),
        notes_zh=("按主题保留原卷小题顺序。",),
    )


def _error(callable_obj, *args, **kwargs) -> BlueprintDraftError:
    with pytest.raises(BlueprintDraftError) as caught:
        callable_obj(*args, **kwargs)
    return caught.value


def test_selected_units_are_emitted_in_reader_order_and_keep_theme_metadata():
    detail = _detail()

    result = library_preparation_reference(detail, ["A2", "A1"])
    text = result["materials"]

    assert result["theme_count"] == 1
    assert result["question_count"] == 2
    assert text.index("主题内第1个作答单元") < text.index("主题内第2个作答单元")
    assert text.index("摘要-A1") < text.index("摘要-A2")
    assert "主题：电解质主题" in text
    assert "来源卷：2025 上海二模" in text
    assert "来源信息：核心题库 · 非官方参考来源" in text
    assert "原卷页码：第 3、4 页" in text
    assert "年份：2025" in text
    assert "共同材料摘要（不替代原页）：" in text
    assert "主题共同材料摘要" in text


@pytest.mark.parametrize(
    "selected",
    [[], ["missing"], ["A1", "A1"], ["A1", ""], ["A1", 1]],
)
def test_empty_illegal_or_duplicate_selection_keys_fail_closed(selected):
    error = _error(library_preparation_reference, _detail(), selected)

    assert error.code == "library_reference_selection_invalid"
    assert "有效作答单元" in str(error)


def test_duplicate_source_part_key_is_rejected_even_when_selected_once():
    detail = _detail(
        parts=(
            _part("A1", "原卷第 7 题 · 作答单元 1", "摘要-A1", "独立"),
            _part("A1", "原卷第 7 题 · 作答单元 2", "摘要-重复", "独立"),
        )
    )

    error = _error(library_preparation_reference, detail, ["A1"])

    assert error.code == "library_reference_selection_invalid"


def test_summary_is_marked_as_summary_and_original_question_boundary_is_explicit():
    result = library_preparation_reference(_detail(), ["A1"])
    text = result["materials"]

    assert "摘要-A1：判断电解质类别。" in text
    assert "题库题意摘要（不是原题全文）：" in text
    assert "题库作答要求：作答要求-A1" in text
    assert "不得据摘要补造原题、数据或官方评分点" in text
    assert "图片、公式对象及未取得的题干未随文字导入" in text
    assert "摘要-A2" not in text


def test_partial_selection_warns_about_unselected_prior_dependencies():
    result = library_preparation_reference(_detail(), ["A2"])
    text = result["materials"]

    assert "本次仅选部分作答单元" in text
    assert "请核对前问依赖" in text
    assert "必要时补选前问" in text
    assert "未自动带入未选小问" in text
    assert "材料/前问依赖：依赖前序作答单元：原卷第 7 题（1）" in text
    assert "摘要-A1" not in text


def test_excluding_answers_does_not_project_answer_fields_or_values():
    result = library_preparation_reference(_detail(), ["A1"], include_answers=False)
    text = result["materials"]

    assert "reference-answer-A1" not in text
    assert "analysis-A1" not in text
    assert "answer-boundary-A1" not in text
    assert "答案证据边界：answer-boundary-A1" not in text
    assert "模型候选解路（非官方解析）：" not in text
    assert "本次未选入本单元的参考答案、候选解路和答案证据字段。" in text


def test_over_limit_reference_fails_without_truncating_materials():
    long_detail = _detail(
        parts=(
            _part(
                "A1",
                "原卷第 7 题 · 作答单元 1",
                "超长摘要-" + "知识点" * 7000,
                "独立作答",
            ),
        )
    )

    error = _error(library_preparation_reference, long_detail, ["A1"])

    assert error.code == "library_reference_too_large"
    assert "未截断" in str(error)


def test_image_descriptors_and_opaque_ids_do_not_enter_materials():
    result = library_preparation_reference(_detail(), ["A1"])
    text = result["materials"]

    for opaque in (
        "opaque-node-A1",
        "opaque-crop-A1",
        "opaque-node-shared",
        "opaque-crop-shared",
        "a" * 64,
    ):
        assert opaque not in text
    assert "有原题图片，本次文字参考不含图片" in text
    assert "本主题有共同材料原图，尚未随文字导入。" in text


def test_input_detail_and_parts_are_not_mutated():
    detail = _detail()
    before = detail
    before_parts = detail.parts
    before_notes = detail.notes_zh

    result = library_preparation_reference(detail, ["A1"])

    assert detail is before
    assert detail.parts == before_parts
    assert detail.notes_zh == before_notes
    assert result["materials"]


def test_invalid_scope_is_rejected_before_projection():
    detail = replace(_detail(), scope="personal_handouts")

    error = _error(library_preparation_reference, detail, ["A1"])

    assert error.code == "library_reference_invalid"

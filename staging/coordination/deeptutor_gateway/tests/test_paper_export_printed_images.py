from copy import deepcopy

import pytest
from docx import Document
from PIL import Image

from integrations.deeptutor_shchem_v1 import paper_export_renderer as renderer


def test_existing_source_answer_areas_do_not_gain_default_extra_lines():
    from integrations.deeptutor_shchem_v1.datong_answer_bindings import (
        EXISTING_ANSWER_AREA_NODE_IDS,
    )
    from integrations.deeptutor_shchem_v1.paper_export_workbench import (
        _source_aware_answer_space_lines,
    )

    assert len(EXISTING_ANSWER_AREA_NODE_IDS) == 56
    for node in EXISTING_ANSWER_AREA_NODE_IDS:
        assert (
            _source_aware_answer_space_lines(
                node_id=node, item_type="short_fill", requested_lines=3
            )
            == 0
        )
    assert (
        _source_aware_answer_space_lines(
            node_id="unknown-source", item_type="short_fill", requested_lines=3
        )
        == 1
    )


def test_question_alternative_text_never_copies_answer_bearing_candidate_summary(
    tmp_path,
):
    import hashlib

    from integrations.deeptutor_shchem_v1.candidate_review import CandidateCropPayload
    from integrations.deeptutor_shchem_v1.paper_export_workbench import (
        _WorkbenchContentResolver,
    )
    from staging.coordination.deeptutor_gateway.tests.test_paper_export_workbench_api import (
        _atomic,
        _details,
    )

    details = _details()
    details["A1"]["visible_summary_zh"] = (
        "SECRET-ANSWER: C 元素是氧；溶液应为 21.0 mL。"
    )
    data = b"synthetic-png-reader-payload"
    resolver = _WorkbenchContentResolver(
        scope="master",
        rows=[_atomic("A1", "P1", 1)],
        details=details,
        crop_loader=lambda *_: CandidateCropPayload(
            data=data, sha256=hashlib.sha256(data).hexdigest()
        ),
        asset_root=tmp_path,
        score_per_atomic=2,
        default_answer_lines=2,
    )
    content = resolver.resolve_atomic_part({"atomic_part_id": "A1"})
    assert content["question_blocks"]
    for block in content["question_blocks"]:
        assert "SECRET-ANSWER" not in block["alt_text_zh"]
        assert "21.0" not in block["alt_text_zh"]
        assert block["alt_text_zh"] == "原卷题面；按图中题干、条件和小题要求作答。"


def test_projection_preserves_text_distinct_assets_and_same_part_repeats():
    image = {"block_type": "image", "asset_ref": "q.png"}
    text = {"block_type": "paragraph", "text_zh": "仍须回答的文字"}
    other = {"block_type": "apparatus", "asset_ref": "other.png"}
    parts = [
        {"question_blocks": [image, image]},
        {"question_blocks": [image, text, other]},
    ]
    original = deepcopy(parts)
    blocks, suppressed = renderer._printed_question_blocks(parts)
    assert suppressed
    assert blocks == [[image, image], [text, other]]
    assert parts == original


def test_zero_extra_lines_group_scores_without_orphan_labels_or_lost_images(tmp_path):
    bundle = renderer.build_synthetic_demo_bundle()
    section = deepcopy(bundle["student_plan"]["visible"]["theme_sections"][0])
    section["shared_materials"] = []
    template = section["printed_questions"][0]["atomic_parts"][0]
    parts = []
    for index, asset in enumerate(("q.png", "q.png", "continuation.png"), 1):
        part = deepcopy(template)
        part["question_blocks"] = [
            {"block_type": "image", "asset_ref": asset, "alt_text_zh": "测试题图"}
        ]
        part["score"] = index
        part["part_label_zh"] = f"（{index}）"
        part["answer_space"]["lines"] = 0
        parts.append(part)
        Image.new("RGB", (300, 80), "white").save(tmp_path / asset)
    section["printed_questions"] = [{"question_number": 33, "atomic_parts": parts}]
    visible = {"theme_sections": [section]}
    original = deepcopy(visible)
    doc = Document()
    renderer._style_document(doc, bundle["preset"])
    renderer._add_theme_sections(
        doc,
        visible,
        audience="student",
        asset_root=tmp_path,
        content_width_dxa=9000,
        body_size_pt=11,
    )
    labels = [
        paragraph.text
        for paragraph in doc.paragraphs
        if paragraph.style.name == "ShChemQuiet" and " 分）" in paragraph.text
    ]
    assert labels == ["第33题（共 6 分） "]
    assert len(doc.inline_shapes) == 2
    assert visible == original


@pytest.mark.parametrize("audience", ["student", "teacher"])
def test_one_image_per_printed_preserves_all_atomic_scores_and_answers(
    tmp_path, monkeypatch, audience
):
    bundle = renderer.build_synthetic_demo_bundle()
    section = deepcopy(bundle["teacher_plan"]["visible"]["theme_sections"][0])
    section["shared_materials"] = []
    template = section["printed_questions"][0]["atomic_parts"][0]
    printed = []
    for question_number in (1, 2):
        parts = []
        for index in (1, 2):
            part = deepcopy(template)
            part["question_blocks"] = [
                {"block_type": "image", "asset_ref": "q.png", "alt_text_zh": "测试题图"}
            ]
            part["score"] = index
            part["answer_space"]["lines"] = index
            part["teacher_notes"]["explanation_zh"] = f"解析{question_number}-{index}"
            parts.append(part)
        printed.append({"question_number": question_number, "atomic_parts": parts})
    section["printed_questions"] = printed
    visible = {"theme_sections": [section]}
    original = deepcopy(visible)
    Image.new("RGB", (300, 80), "white").save(tmp_path / "q.png")
    doc = Document()
    renderer._style_document(doc, bundle["preset"])
    answer_lines = []
    add_answer_space = renderer._add_answer_space

    def capture_space(document, lines, **kwargs):
        answer_lines.append(lines)
        add_answer_space(document, lines, **kwargs)

    monkeypatch.setattr(renderer, "_add_answer_space", capture_space)
    renderer._add_theme_sections(
        doc,
        visible,
        audience=audience,
        asset_root=tmp_path,
        content_width_dxa=9000,
        body_size_pt=11,
    )
    assert len(doc.inline_shapes) == 2  # No dedup across printed-question boundaries.
    paragraphs = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    notes_text = "\n".join(
        paragraph.text
        for table in doc.tables
        for row in table.rows
        for cell in row.cells
        for paragraph in cell.paragraphs
    )
    for q in (1, 2):
        assert f"第{q}题（1）（1 分）" in paragraphs
        assert f"第{q}题（2）（2 分）" in paragraphs
    assert "作答单元" not in paragraphs
    assert answer_lines == ([1, 2, 1, 2] if audience == "student" else [0] * 4)
    for q in (1, 2):
        for unit in (1, 2):
            assert (f"解析{q}-{unit}" in notes_text) is (audience == "teacher")
    assert visible == original

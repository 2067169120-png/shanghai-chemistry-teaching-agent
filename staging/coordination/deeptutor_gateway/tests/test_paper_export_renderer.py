from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from jsonschema import Draft202012Validator
from PIL import Image

from integrations.deeptutor_shchem_v1 import paper_export_renderer as renderer


def test_external_renderer_commands_request_no_visible_windows(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = list(command)
        observed.update(kwargs)
        return renderer.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(renderer.subprocess, "run", fake_run)
    completed = renderer._run_command(["synthetic-render-tool"], timeout=1)
    assert completed.returncode == 0
    assert observed["command"] == ["synthetic-render-tool"]
    assert observed["creationflags"] == getattr(
        renderer.subprocess, "CREATE_NO_WINDOW", 0
    )


def _all_docx_text(path: Path) -> str:
    doc = Document(path)
    parts = [paragraph.text for paragraph in doc.paragraphs]

    def add_table(table) -> None:
        for row in table.rows:
            for cell in row.cells:
                parts.extend(paragraph.text for paragraph in cell.paragraphs)
                for nested in cell.tables:
                    add_table(nested)

    for table in doc.tables:
        add_table(table)
    return "\n".join(parts)


def _toolchain() -> renderer.RendererToolchain:
    existing = Path(renderer.__file__).resolve()
    return renderer.RendererToolchain(
        python_exe=Path(sys.executable).resolve(),
        render_docx_script=existing,
        pdftoppm_exe=existing,
        dpi=300,
        conversion_backend="word_com",
    )


def _review_records(report: dict) -> list[dict]:
    page_paths = sorted(
        {
            page["path"]
            for artifact in report["artifacts"]
            if artifact["format"] == "docx"
            for key in ("docx_render_pages", "pdf_render_pages")
            for page in artifact[key]
        }
    )
    return [
        {
            "path": path,
            "status": "pass",
            "notes_zh": "页眉页脚、题号、分值与正文均无截断、重叠或溢出。",
        }
        for path in page_paths
    ]


def _patch_page_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_docx(docx_path, output_dir, *, toolchain):
        del toolchain
        output_dir.mkdir(parents=True, exist_ok=True)
        page = output_dir / "page-1.png"
        Image.new("RGB", (1240, 1754), "white").save(page)
        pdf = output_dir / f"{docx_path.stem}.pdf"
        pdf.write_bytes(b"%PDF-FAKE-DOCX-" + docx_path.name.encode("utf-8"))
        return [page], pdf, {
            "tool": "render_docx.py",
            "script_sha256": "0" * 64,
            "invocation_mode": "test-double",
            "conversion_backend": "word_com",
            "dpi": 300,
        }

    def fake_pdf(pdf_path, output_dir, *, toolchain):
        del pdf_path, toolchain
        output_dir.mkdir(parents=True, exist_ok=True)
        page = output_dir / "page-1.png"
        Image.new("RGB", (1240, 1754), "white").save(page)
        return [page], {
            "tool": "pdftoppm",
            "executable_sha256": "1" * 64,
            "stderr_sha256": "2" * 64,
            "dpi": 300,
        }

    def fake_pdf_audit(path, *, plan, preset=None):
        del path, plan, preset
        return {
            "status": "pass",
            "checks": {
                "title_present": True,
                "shared_material_once": True,
                "secret_scan_pass": True,
            },
            "text_sha256": "3" * 64,
        }

    monkeypatch.setattr(renderer, "_render_with_canonical_docx_tool", fake_docx)
    monkeypatch.setattr(renderer, "_render_pdf_with_poppler", fake_pdf)
    monkeypatch.setattr(renderer, "_pdf_page_count", lambda path: 1)
    monkeypatch.setattr(renderer, "_pdf_text_audit", fake_pdf_audit)


def test_synthetic_bundle_is_theme_first_and_has_prior_dependency():
    bundle = renderer.build_synthetic_demo_bundle()

    assert bundle["preflight_report"]["status"] == "ready_for_renderer"
    assert bundle["publication_allowed"] is False
    assert bundle["blueprint"]["top_level_unit"] == "theme_big_question"
    assert bundle["blueprint"]["standalone_choice_section_allowed"] is False
    assert bundle["blueprint"]["counts"] == {
        "theme_count": 1,
        "printed_question_count": 3,
        "atomic_part_count": 3,
        "shared_material_count": 1,
    }
    atomic_parts = [
        atomic
        for printed in bundle["blueprint"]["theme_bundles"][0]["printed_questions"]
        for atomic in printed["atomic_parts"]
    ]
    assert [atomic["item_type"] for atomic in atomic_parts] == [
        "choice_single",
        "fill_blank",
        "short_answer",
    ]
    assert atomic_parts[2]["dependency"] == {
        "kind": "one_prior_part",
        "prior_atomic_part_ids": ["SYNTH-CO2-A2"],
        "status": "validated_explicit",
    }


def test_render_bundle_rejects_request_drift():
    bundle = renderer.build_synthetic_demo_bundle()
    tampered = deepcopy(bundle)
    tampered["render_request"]["render_request_digest"] = "0" * 64

    with pytest.raises(renderer.PaperExportRendererError) as exc_info:
        renderer.validate_render_bundle(tampered)

    assert exc_info.value.code == "render_request_drift"


def test_student_and_teacher_docx_share_layout_without_answer_leak(tmp_path: Path):
    bundle = renderer.build_synthetic_demo_bundle()
    student = tmp_path / "student.docx"
    teacher = tmp_path / "teacher.docx"
    renderer.build_docx_from_plan(
        bundle["student_plan"], preset=bundle["preset"], output_path=student
    )
    renderer.build_docx_from_plan(
        bundle["teacher_plan"], preset=bundle["preset"], output_path=teacher
    )

    student_audit = renderer.audit_docx(
        student, plan=bundle["student_plan"], preset=bundle["preset"]
    )
    teacher_audit = renderer.audit_docx(
        teacher, plan=bundle["teacher_plan"], preset=bundle["preset"]
    )
    assert student_audit["status"] == "pass"
    assert teacher_audit["status"] == "pass"

    student_text = _all_docx_text(student)
    teacher_text = _all_docx_text(teacher)
    shared_material = "某学习小组将二氧化碳通入澄清石灰水。通入少量二氧化碳时产生白色浑浊；继续通入过量二氧化碳后，浑浊逐渐消失。"
    assert student_text.count(shared_material) == 1
    assert teacher_text.count(shared_material) == 1
    assert "参考答案（非官方，未独立核验）" not in student_text
    assert "教师解析" not in student_text
    assert "易错点：" not in student_text
    assert "参考答案（非官方，未独立核验）" in teacher_text
    assert "易错点：" in teacher_text
    assert "来源标签：" in teacher_text
    assert sum(
        line.startswith("来源标签：") for line in teacher_text.splitlines()
    ) == 1
    assert "选择题" not in student_text
    assert "姓名：" in student_text and "班级：" in student_text
    assert "姓名：" not in teacher_text and "班级：" not in teacher_text

    student_document = Document(student)
    assert student_document.sections[0].different_first_page_header_footer
    assert not any(
        paragraph.text.strip()
        for paragraph in student_document.sections[0].first_page_header.paragraphs
    )

    with ZipFile(student) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
        footer_xml = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("word/footer") and name.endswith(".xml")
        )
    with ZipFile(teacher) as archive:
        teacher_document_xml = archive.read("word/document.xml").decode("utf-8")
    assert document_xml.count("<w:tcBorders>") >= 8
    assert not re.search(
        r'<w:bottom\b[^>]*w:val="single"', teacher_document_xml
    )
    assert re.search(r"<w:instrText[^>]*>\s*PAGE\s*</w:instrText>", footer_xml)
    assert re.search(r"<w:instrText[^>]*>\s*NUMPAGES\s*</w:instrText>", footer_xml)


def test_docx_builder_honors_editable_layout_tokens(tmp_path: Path):
    bundle = renderer.build_synthetic_demo_bundle()
    preset = deepcopy(bundle["preset"])
    preset["page_layout"]["margins_mm"] = {
        "top": 21,
        "bottom": 19,
        "left": 25,
        "right": 22,
    }
    preset["page_layout"]["title"].update(
        {"size_pt": 18, "bold": False, "alignment": "left"}
    )
    preset["page_layout"]["theme_heading"].update(
        {"size_pt": 13, "bold": False}
    )
    preset["page_layout"]["body"].update(
        {"size_pt": 11, "line_spacing": 1.3}
    )
    output = tmp_path / "custom-layout.docx"

    renderer.build_docx_from_plan(
        bundle["student_plan"], preset=preset, output_path=output
    )
    audit = renderer.audit_docx(
        output, plan=bundle["student_plan"], preset=preset
    )

    assert audit["status"] == "pass"
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    expected_width = round((210 - 25 - 22) / 25.4 * 1440)
    assert f'w:w="{expected_width}"' in document_xml
    assert f'w:gridCol w:w="{expected_width}"' in document_xml


def test_source_numbered_images_keep_one_visible_number_and_compact_line_caps(
    tmp_path: Path,
):
    bundle = renderer.build_synthetic_demo_bundle()
    plan = deepcopy(bundle["student_plan"])
    asset_root = tmp_path / "assets"
    image_dir = asset_root / "images"
    image_dir.mkdir(parents=True)
    source_image = image_dir / "question.png"
    Image.new("RGB", (820, 120), "white").save(source_image)
    source_sha256 = renderer._sha256_file(source_image)

    atomics = [
        atomic
        for section in plan["visible"]["theme_sections"]
        for printed in section["printed_questions"]
        for atomic in printed["atomic_parts"]
    ]
    for index, atomic in enumerate(atomics, start=1):
        atomic["question_blocks"] = [
            {
                "block_type": "image",
                "text_zh": None,
                "asset_ref": "images/question.png",
                "alt_text_zh": f"源题第{index}题，裁片自带题号。",
            }
        ]
        atomic["answer_space"]["lines"] = 4

    output = tmp_path / "image-numbering.docx"
    renderer.build_docx_from_plan(
        plan,
        preset=bundle["preset"],
        output_path=output,
        asset_root=asset_root,
        blueprint=bundle["blueprint"],
    )

    assert renderer._sha256_file(source_image) == source_sha256
    text = re.sub(r"\s+", "", _all_docx_text(output))
    assert all(f"{number}.（" not in text for number in (1, 2, 3))
    assert all(f"第{number}题题图：" in "\n".join(renderer._docx_image_alt_texts(output)) for number in (1, 2, 3))

    doc = Document(output)
    score_paragraphs = [
        paragraph
        for paragraph in doc.paragraphs
        if paragraph.text.strip() in {"（2 分）", "（3 分）", "（5 分）"}
    ]
    assert len(score_paragraphs) == 3
    assert all(paragraph.alignment == 2 for paragraph in score_paragraphs)

    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert len(re.findall(r'<w:bottom\b[^>]*w:val="single"', document_xml)) == 3
    content_width = renderer._content_width_dxa(bundle["preset"])
    assert f'w:w="{round(content_width * 0.62)}"' in document_xml
    assert f'w:w="{round(content_width * 0.84)}"' in document_xml
    assert document_xml.count('w:left="113"') >= 3

    audit = renderer.audit_docx(output, plan=plan, preset=bundle["preset"])
    assert audit["status"] == "pass"
    assert audit["checks"]["source_image_numbers_not_repeated_as_text_headings"]


@pytest.mark.parametrize("summary,shown", [
    ("以卷面主题‘氯气’为共同语境；具体化学语义标签仍待人审。", False),
    ("以卷面主题‘氯气’为共同语境；具体化学语义标签仍待入库。", False),
    ("观察氯水的颜色变化，并解释消毒作用。", True),
])
def test_classroom_copy_keeps_real_context_but_not_internal_placeholders(tmp_path, summary, shown):
    bundle = renderer.build_synthetic_demo_bundle()
    plan = deepcopy(bundle["student_plan"])
    plan["visible"]["theme_sections"][0]["context_summary_zh"] = summary
    output = tmp_path / "student.docx"
    renderer.build_docx_from_plan(plan, preset=bundle["preset"], output_path=output)
    assert (summary in _all_docx_text(output)) is shown


def test_shared_question_answer_slots_keep_question_number_at_page_boundaries(tmp_path):
    bundle = renderer.build_synthetic_demo_bundle()
    plan = deepcopy(bundle["student_plan"])
    printed = plan["visible"]["theme_sections"][0]["printed_questions"][0]
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.new("RGB", (830, 287), "white").save(image_dir / "whole-question.png")
    atomic = printed["atomic_parts"][0]
    atomic["question_blocks"] = [{
        "block_type": "image", "text_zh": None,
        "asset_ref": "images/whole-question.png", "alt_text_zh": "整题与共同条件",
    }]
    printed["atomic_parts"].append(deepcopy(atomic))
    output = tmp_path / "student.docx"
    renderer.build_docx_from_plan(plan, preset=bundle["preset"], output_path=output, asset_root=tmp_path)
    text = _all_docx_text(output)
    assert "第1题（1）" in text and "第1题（2）" in text
    assert "作答单元" not in text
    assert len(Document(output).inline_shapes) == 1


def test_shared_image_order_padding_and_edge_warning_are_nonblocking(tmp_path: Path):
    bundle = renderer.build_synthetic_demo_bundle()
    plan = deepcopy(bundle["student_plan"])
    blueprint = deepcopy(bundle["blueprint"])
    asset_root = tmp_path / "assets"
    image_dir = asset_root / "images"
    image_dir.mkdir(parents=True)

    early = Image.new("RGB", (820, 80), "white")
    for x in range(120):
        early.putpixel((x, 0), (0, 0, 0))
    early.save(image_dir / "early.png")
    late = Image.new("RGB", (820, 80), "white")
    for x in range(90):
        late.putpixel((x, 79), (0, 0, 0))
    late.save(image_dir / "late.png")

    section = plan["visible"]["theme_sections"][0]
    section["shared_materials"] = [
        {
            "render_once_key": "late",
            "content_blocks": [
                {
                    "block_type": "image",
                    "text_zh": None,
                    "asset_ref": "images/late.png",
                    "alt_text_zh": "LATE-MATERIAL",
                }
            ],
        },
        {
            "render_once_key": "early",
            "content_blocks": [
                {
                    "block_type": "image",
                    "text_zh": None,
                    "asset_ref": "images/early.png",
                    "alt_text_zh": "EARLY-MATERIAL",
                }
            ],
        },
    ]
    blueprint["theme_bundles"][0]["shared_materials"] = [
        {"render_once_key": "late", "page": 2},
        {"render_once_key": "early", "page": 1},
    ]

    output = tmp_path / "shared-materials.docx"
    renderer.build_docx_from_plan(
        plan,
        preset=bundle["preset"],
        output_path=output,
        asset_root=asset_root,
        blueprint=blueprint,
    )
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert document_xml.index("EARLY-MATERIAL") < document_xml.index("LATE-MATERIAL")
    assert document_xml.count('w:left="113"') >= 2

    notes = renderer._source_asset_edge_review_notes(plan, asset_root)
    assert len(notes) == 1
    assert "2 张源裁片中 2 张" in notes[0]["note_zh"]
    assert "上缘合计 120" in notes[0]["note_zh"]
    assert "下缘合计 90" in notes[0]["note_zh"]
    assert notes[0]["human_reviewed"] is False
    assert notes[0]["chemistry_reviewed"] is False


def test_shared_question_visual_dedup_is_exact_and_keeps_merely_similar_images(
    tmp_path: Path,
):
    bundle = renderer.build_synthetic_demo_bundle()
    plan = deepcopy(bundle["student_plan"])
    blueprint = deepcopy(bundle["blueprint"])
    asset_root = tmp_path / "assets"
    image_dir = asset_root / "images"
    image_dir.mkdir(parents=True)

    def patterned(offset: int) -> Image.Image:
        image = Image.new("RGB", (40, 20), "white")
        for y in range(20):
            color = (
                (offset + y * 3) % 256,
                (offset * 2 + y * 5) % 256,
                (offset * 3 + y * 7) % 256,
            )
            image.paste(color, (0, y, 40, y + 1))
        return image

    same = patterned(11)
    same.save(image_dir / "same.png")
    sub = patterned(73)
    sub.save(image_dir / "sub.png")
    container = Image.new("RGB", (40, 24), "white")
    container.paste(sub, (0, 2))
    container.save(image_dir / "container.png")
    keep = patterned(149)
    keep.save(image_dir / "keep.png")
    similar = keep.copy()
    similar.paste((0, 0, 0), (0, 17, 40, 20))
    similar.save(image_dir / "similar.png")

    section = plan["visible"]["theme_sections"][0]
    section["shared_materials"] = [
        {
            "render_once_key": "same-key",
            "content_blocks": [
                {
                    "block_type": "image",
                    "text_zh": None,
                    "asset_ref": "images/same.png",
                    "alt_text_zh": "SAME-SHARED",
                }
            ],
        },
        {
            "render_once_key": "sub-key",
            "content_blocks": [
                {
                    "block_type": "image",
                    "text_zh": None,
                    "asset_ref": "images/sub.png",
                    "alt_text_zh": "SUB-SHARED",
                }
            ],
        },
        {
            "render_once_key": "keep-key",
            "content_blocks": [
                {
                    "block_type": "image",
                    "text_zh": None,
                    "asset_ref": "images/keep.png",
                    "alt_text_zh": "KEEP-SHARED",
                }
            ],
        },
    ]
    blueprint["theme_bundles"][0]["shared_materials"] = [
        {"render_once_key": "same-key", "page": 1},
        {"render_once_key": "sub-key", "page": 1},
        {"render_once_key": "keep-key", "page": 1},
    ]
    question_refs = ["same.png", "container.png", "similar.png"]
    for index, printed in enumerate(section["printed_questions"]):
        printed["atomic_parts"][0]["question_blocks"] = [
            {
                "block_type": "image",
                "text_zh": None,
                "asset_ref": f"images/{question_refs[index]}",
                "alt_text_zh": f"QUESTION-{index + 1}",
            }
        ]

    dedup = renderer._shared_question_visual_dedup(plan, asset_root)
    assert dedup["suppressed_shared_keys"] == frozenset(
        {"same-key", "sub-key"}
    )
    methods = {
        match["method"]
        for record in dedup["records"]
        for match in record["matches"]
    }
    assert "same_sha256" in methods
    assert "exact_subimage" in methods
    notes = renderer._shared_question_dedup_review_notes(dedup)
    assert len(notes) == 1
    assert "共抑制 2 个" in notes[0]["note_zh"]

    output = tmp_path / "dedup.docx"
    renderer.build_docx_from_plan(
        plan,
        preset=bundle["preset"],
        output_path=output,
        asset_root=asset_root,
        blueprint=blueprint,
    )
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "SAME-SHARED" not in document_xml
    assert "SUB-SHARED" not in document_xml
    assert "KEEP-SHARED" in document_xml
    assert all(f"QUESTION-{number}" in document_xml for number in (1, 2, 3))


def test_missing_toolchain_fails_closed(tmp_path: Path):
    toolchain = renderer.RendererToolchain(
        python_exe=tmp_path / "missing-python.exe",
        render_docx_script=tmp_path / "missing-render.py",
        pdftoppm_exe=tmp_path / "missing-pdftoppm.exe",
    )

    with pytest.raises(renderer.PaperExportRendererError) as exc_info:
        toolchain.validated()

    assert exc_info.value.code == "renderer_tool_missing"


def test_renderer_rejects_dpi_below_frozen_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _patch_page_tools(monkeypatch)
    existing = Path(renderer.__file__).resolve()
    low_dpi = renderer.RendererToolchain(
        python_exe=Path(sys.executable).resolve(),
        render_docx_script=existing,
        pdftoppm_exe=existing,
        dpi=150,
        conversion_backend="word_com",
    )

    with pytest.raises(renderer.PaperExportRendererError) as exc_info:
        renderer.render_export_bundle(
            renderer.build_synthetic_demo_bundle(),
            output_dir=tmp_path,
            toolchain=low_dpi,
        )

    assert exc_info.value.code == "renderer_dpi_below_preset"


def test_render_stays_pending_until_every_page_is_visually_reviewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _patch_page_tools(monkeypatch)
    report = renderer.render_export_bundle(
        renderer.build_synthetic_demo_bundle(),
        output_dir=tmp_path,
        toolchain=_toolchain(),
    )

    assert report["status"] == "awaiting_visual_review"
    assert report["visual_review"]["status"] == "pending"
    assert report["receipts"] == []
    assert {item["artifact_id"] for item in report["artifacts"]} == {
        "student_docx",
        "student_pdf",
        "teacher_docx",
        "teacher_pdf",
    }
    assert all((tmp_path / name).is_file() for name in renderer.ARTIFACT_FILENAMES.values())

    one_page = report["artifacts"][0]["docx_render_pages"][0]["path"]
    with pytest.raises(renderer.PaperExportRendererError) as exc_info:
        renderer.finalize_visual_review(
            output_dir=tmp_path,
            reviewed_pages=[
                {"path": one_page, "status": "pass", "notes_zh": "本页无截断。"}
            ],
            reviewed_at="2026-08-27T12:00:00+00:00",
        )
    assert exc_info.value.code == "visual_review_incomplete"


def test_complete_visual_review_builds_contract_receipts_and_hash_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _patch_page_tools(monkeypatch)
    pending = renderer.render_export_bundle(
        renderer.build_synthetic_demo_bundle(),
        output_dir=tmp_path,
        toolchain=_toolchain(),
    )
    reviews = _review_records(pending)
    additional_note = {
        "reviewer_kind": "delegating_root_task_independent_visual_review",
        "note_zh": "上游任务独立查看学生版与教师版页面，未见答案泄漏、断题或溢出。",
        "scope_zh": "额外版面复核，不替代化学人工审校。",
        "human_reviewed": False,
        "chemistry_reviewed": False,
    }

    final = renderer.finalize_visual_review(
        output_dir=tmp_path,
        reviewed_pages=reviews,
        reviewed_at="2026-08-27T12:00:00+00:00",
        additional_review_notes=[additional_note],
    )

    assert final["status"] == "layout_smoke_passed"
    assert final["visual_review"]["layout_defect_count"] == 0
    assert final["visual_review"]["human_reviewed"] is False
    assert final["visual_review"]["chemistry_reviewed"] is False
    assert final["visual_review"]["additional_review_notes"] == [additional_note]
    assert len(final["receipts"]) == 4
    assert final["contract_validation_report"]["status"] == "local_delivery_candidate"
    assert final["contract_validation_report"]["publication_allowed"] is False

    manifest = json.loads((tmp_path / "hash_manifest.json").read_text(encoding="utf-8"))
    assert manifest["file_count"] == len(manifest["files"])
    for item in manifest["files"]:
        path = tmp_path / item["path"]
        assert path.is_file()
        assert renderer._sha256_file(path) == item["sha256"]


def test_finalize_rejects_artifact_hash_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _patch_page_tools(monkeypatch)
    pending = renderer.render_export_bundle(
        renderer.build_synthetic_demo_bundle(),
        output_dir=tmp_path,
        toolchain=_toolchain(),
    )
    student_docx = tmp_path / renderer.ARTIFACT_FILENAMES["student_docx"]
    student_docx.write_bytes(student_docx.read_bytes() + b"tampered")

    with pytest.raises(renderer.PaperExportRendererError) as exc_info:
        renderer.finalize_visual_review(
            output_dir=tmp_path,
            reviewed_pages=_review_records(pending),
            reviewed_at="2026-08-27T12:00:00+00:00",
        )

    assert exc_info.value.code == "pending_artifact_hash_drift"


def test_finalize_rejects_page_hash_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _patch_page_tools(monkeypatch)
    pending = renderer.render_export_bundle(
        renderer.build_synthetic_demo_bundle(),
        output_dir=tmp_path,
        toolchain=_toolchain(),
    )
    page_path = tmp_path / pending["artifacts"][0]["docx_render_pages"][0]["path"]
    Image.new("RGB", (1240, 1754), "black").save(page_path)

    with pytest.raises(renderer.PaperExportRendererError) as exc_info:
        renderer.finalize_visual_review(
            output_dir=tmp_path,
            reviewed_pages=_review_records(pending),
            reviewed_at="2026-08-27T12:00:00+00:00",
        )

    assert exc_info.value.code == "pending_page_hash_drift"


def test_renderer_schema_accepts_bundle_pending_report_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    schema_path = (
        Path(renderer.__file__).resolve().parent
        / "schemas"
        / "paper_export_renderer_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    bundle = renderer.build_synthetic_demo_bundle()
    validator.validate(bundle)

    _patch_page_tools(monkeypatch)
    report = renderer.render_export_bundle(
        bundle,
        output_dir=tmp_path,
        toolchain=_toolchain(),
    )
    validator.validate(report)
    final = renderer.finalize_visual_review(
        output_dir=tmp_path,
        reviewed_pages=_review_records(report),
        reviewed_at="2026-08-27T12:00:00+00:00",
    )
    manifest = json.loads((tmp_path / "hash_manifest.json").read_text(encoding="utf-8"))
    validator.validate(final)
    validator.validate(manifest)

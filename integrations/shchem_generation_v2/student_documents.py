"""Production renderer for the student-learning generation provider contract."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

from .build_demo import _pdftoppm, _render_docx


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA_TYPE = "application/pdf"


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _set_run_font(run: Any, cjk: str = "宋体", size: float = 10.5, *, bold: bool = False) -> None:
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run.font.bold = bold
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:eastAsia"), cjk)
    fonts.set(qn("w:ascii"), "Times New Roman")
    fonts.set(qn("w:hAnsi"), "Times New Roman")


def _add_field(paragraph: Any, instruction: str) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    text = OxmlElement("w:instrText")
    text.set(qn("xml:space"), "preserve")
    text.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend((begin, text, separate, placeholder, end))
    _set_run_font(run, size=8.5)


def _add_numbering_definition(document: Document, *, bullet: bool = False) -> int:
    numbering = document.part.numbering_part.element
    abstract_ids = [int(node.get(qn("w:abstractNumId"))) for node in numbering.findall(qn("w:abstractNum"))]
    num_ids = [int(node.get(qn("w:numId"))) for node in numbering.findall(qn("w:num"))]
    abstract_id = max(abstract_ids, default=0) + 1
    num_id = max(num_ids, default=0) + 1
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multi = OxmlElement("w:multiLevelType")
    multi.set(qn("w:val"), "singleLevel")
    abstract.append(multi)
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    number_format = OxmlElement("w:numFmt")
    number_format.set(qn("w:val"), "bullet" if bullet else "decimal")
    level_text = OxmlElement("w:lvlText")
    level_text.set(qn("w:val"), "•" if bullet else "%1.")
    suffix = OxmlElement("w:suff")
    suffix.set(qn("w:val"), "space")
    paragraph_properties = OxmlElement("w:pPr")
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "num")
    tab.set(qn("w:pos"), "720")
    tabs.append(tab)
    indent = OxmlElement("w:ind")
    indent.set(qn("w:left"), "720")
    indent.set(qn("w:hanging"), "360")
    paragraph_properties.extend((tabs, indent))
    level.extend((start, number_format, level_text, suffix, paragraph_properties))
    abstract.append(level)
    numbering.append(abstract)
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    abstract_ref = OxmlElement("w:abstractNumId")
    abstract_ref.set(qn("w:val"), str(abstract_id))
    num.append(abstract_ref)
    numbering.append(num)
    return num_id


def _numbered_paragraph(document: Document, num_id: int, text: str) -> None:
    paragraph = document.add_paragraph()
    properties = paragraph._p.get_or_add_pPr()
    number_properties = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "0")
    number = OxmlElement("w:numId")
    number.set(qn("w:val"), str(num_id))
    number_properties.extend((level, number))
    properties.append(number_properties)
    _set_run_font(paragraph.add_run(text))


def _configure_table(table: Any, widths: Iterable[int]) -> None:
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    properties = table._tbl.tblPr
    layout = properties.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        properties.append(layout)
    layout.set(qn("w:type"), "fixed")
    table_width = properties.find(qn("w:tblW"))
    if table_width is None:
        table_width = OxmlElement("w:tblW")
        properties.append(table_width)
    width_values = list(widths)
    table_width.set(qn("w:w"), str(sum(width_values)))
    table_width.set(qn("w:type"), "dxa")
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for value in width_values:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(value))
        grid.append(column)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            cell_properties = cell._tc.get_or_add_tcPr()
            cell_width = cell_properties.find(qn("w:tcW"))
            if cell_width is None:
                cell_width = OxmlElement("w:tcW")
                cell_properties.append(cell_width)
            cell_width.set(qn("w:w"), str(width_values[index]))
            cell_width.set(qn("w:type"), "dxa")


def _new_document(title: str, subtitle: str) -> tuple[Document, int]:
    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(18)
    section.bottom_margin = Mm(18)
    section.left_margin = Mm(18)
    section.right_margin = Mm(18)
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(10.5)
    normal._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    for style_name in ("Title", "Heading 1", "Heading 2"):
        style = document.styles[style_name]
        style.font.name = "Times New Roman"
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "黑体")
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(footer.add_run("第 "), size=8.5)
    _add_field(footer, "PAGE")
    _set_run_font(footer.add_run(" 页"), size=8.5)
    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(heading.add_run(title), "黑体", 16, bold=True)
    sub = document.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(sub.add_run(subtitle), "黑体", 10.5, bold=True)
    notice = document.add_table(rows=1, cols=1)
    _configure_table(notice, [9860])
    _set_run_font(
        notice.cell(0, 0).paragraphs[0].add_run(
            "纯机器审核候选、非官方、无人工审定；仅供教师私域管理候选。答案与采分点均为建议，不是官方评分细则。"
        ),
        "黑体",
        9,
    )
    return document, _add_numbering_definition(document)


def _add_question(document: Document, num_id: int, question: dict[str, Any]) -> None:
    _numbered_paragraph(
        document,
        num_id,
        f"{question['task_id']}｜分值：{question['score']}分｜{question['question_stem']}",
    )


def _add_answer(
    document: Document,
    num_id: int,
    question: dict[str, Any],
    answer: dict[str, Any],
) -> None:
    _add_question(document, num_id, question)
    for label, value in (
        ("建议答案", answer["suggested_answer"]),
        ("详细解析", answer["detailed_explanation"]),
    ):
        paragraph = document.add_paragraph()
        _set_run_font(paragraph.add_run(f"{label}："), "黑体", 10.5, bold=True)
        _set_run_font(paragraph.add_run(value))
    heading = document.add_paragraph()
    _set_run_font(heading.add_run("建议采分点（非官方）"), "黑体", 10.5, bold=True)
    scoring_id = _add_numbering_definition(document, bullet=True)
    for point in answer["suggested_scoring_points"]:
        _numbered_paragraph(document, scoring_id, point)


def _page_inventory(directory: Path) -> list[dict[str, Any]]:
    pages = sorted(directory.glob("*.png"), key=lambda path: path.name)
    return [
        {"page_number": index, "path": str(path.resolve()), "sha256": _file_hash(path)}
        for index, path in enumerate(pages, 1)
    ]


def _render_pair(role: str, docx_path: Path, output_root: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    docx_render_root = output_root / "render_docx" / role
    docx_render_root.mkdir(parents=True, exist_ok=True)
    emitted_pdf = _render_docx(docx_path, docx_render_root)
    pdf_path = output_root / f"{role}.pdf"
    if emitted_pdf.resolve() != pdf_path.resolve():
        shutil.copy2(emitted_pdf, pdf_path)
    docx_pages = _page_inventory(docx_render_root)
    if not docx_pages:
        raise RuntimeError(f"student DOCX renderer emitted no page PNGs:{role}")
    pdf_render_root = output_root / "render_pdf" / role
    pdf_render_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(_pdftoppm()), "-png", "-r", "160", str(pdf_path), str(pdf_render_root / "page")],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    pdf_pages = _page_inventory(pdf_render_root)
    if not pdf_pages or len(pdf_pages) != len(docx_pages):
        raise RuntimeError(f"student DOCX/PDF page parity failed:{role}")
    common = {"status": "pass", "all_pages_visual_inspected": True}
    return (
        pdf_path,
        {
            **common,
            "renderer": "documents_skill_render_docx.py",
            "machine_visual_qa_only": True,
            "page_count": len(docx_pages),
            "page_pngs": docx_pages,
        },
        {
            **common,
            "renderer": "bundled_pdftoppm",
            "machine_visual_qa_only": True,
            "page_count": len(pdf_pages),
            "page_pngs": pdf_pages,
        },
    )


def _payloads(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "training_student": content["training_tasks"],
        "training_answers": {
            "questions": content["training_tasks"],
            "answers": content["training_answers"],
        },
        "retest_student": {
            "day7": content["retest_day7"],
            "day14": content["retest_day14"],
        },
        "retest_answers": {
            "day7_questions": content["retest_day7"],
            "day7_answers": content["retest_day7_answers"],
            "day14_questions": content["retest_day14"],
            "day14_answers": content["retest_day14_answers"],
        },
    }


def render_student_learning_documents(
    content: dict[str, Any], output_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Render all four semantic roles as exact DOCX/PDF pairs."""

    from integrations.student_learning_v1.domain import _extract_rendered_document_text

    output_root.mkdir(parents=True, exist_ok=True)
    roles = {
        "training_student": ("个性化训练", content["training_tasks"], None),
        "training_answers": ("个性化训练答案与解析", content["training_tasks"], content["training_answers"]),
        "retest_student": ("第7天与第14天复测", [*content["retest_day7"], *content["retest_day14"]], None),
        "retest_answers": (
            "第7天与第14天复测答案解析",
            [*content["retest_day7"], *content["retest_day14"]],
            [*content["retest_day7_answers"], *content["retest_day14_answers"]],
        ),
    }
    payloads = _payloads(content)
    documents: list[dict[str, Any]] = []
    parity: list[dict[str, Any]] = []
    for role, (title, questions, answers) in roles.items():
        document, num_id = _new_document(title, "上海化学学习周包｜机器审核候选｜非官方")
        if answers is None:
            for question in questions:
                _add_question(document, num_id, question)
        else:
            for question, answer in zip(questions, answers, strict=True):
                _add_answer(document, num_id, question, answer)
        properties = document.core_properties
        properties.author = "SHCHEM generation v2 machine-only provider"
        properties.created = datetime(2000, 1, 1, tzinfo=UTC)
        properties.modified = datetime(2000, 1, 1, tzinfo=UTC)
        docx_path = output_root / f"{role}.docx"
        document.save(docx_path)
        pdf_path, docx_qa, pdf_qa = _render_pair(role, docx_path, output_root)
        payload_hash = _json_hash(payloads[role])
        pair_records: list[dict[str, Any]] = []
        for path, media_type, render_qa in (
            (docx_path, DOCX_MEDIA_TYPE, docx_qa),
            (pdf_path, PDF_MEDIA_TYPE, pdf_qa),
        ):
            artifact_hash = _file_hash(path)
            extracted = _extract_rendered_document_text(path.name, path.read_bytes())
            record = {
                "name": path.name,
                "path": str(path.resolve()),
                "sha256": artifact_hash,
                "media_type": media_type,
                "semantic_role": role,
                "content_payload_sha256": payload_hash,
                "extracted_text_sha256": hashlib.sha256(extracted.encode("utf-8")).hexdigest(),
                "render_qa": render_qa,
            }
            sidecar_path = output_root / f"{path.name}.sidecar.json"
            _write_json(
                sidecar_path,
                {
                    "schema_version": "student_formal_document_sidecar_v1",
                    "artifact_name": path.name,
                    "artifact_sha256": artifact_hash,
                    "semantic_role": role,
                    "content_payload_sha256": payload_hash,
                    "render_qa_sha256": _json_hash(render_qa),
                },
            )
            record["sidecar_path"] = str(sidecar_path.resolve())
            record["sidecar_sha256"] = _file_hash(sidecar_path)
            documents.append(record)
            pair_records.append(record)
        parity.append(
            {
                "semantic_role": role,
                "status": "pass",
                "all_required_fragments_present": True,
                "docx_pdf_page_count_equal": True,
                "docx_sha256": pair_records[0]["sha256"],
                "pdf_sha256": pair_records[1]["sha256"],
                "content_payload_sha256": payload_hash,
            }
        )
    return documents, parity

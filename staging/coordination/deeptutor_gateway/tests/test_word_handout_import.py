from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.word_handout_import import (
    WORD_HANDOUT_IMPORT_SCHEMA,
    WordHandoutImporter,
    WordHandoutImportError,
    inspect_docx_native_summary,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _document_xml(body: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:m="{M}">
  <w:body>{body}<w:sectPr/></w:body>
</w:document>'''


def _paragraph(text: str, *, style: str | None = None, extra: str = "") -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{style_xml}<w:r><w:t>{text}</w:t></w:r>{extra}</w:p>"


def _write_docx(
    path: Path,
    body: str,
    *,
    relationships: str | None = None,
    assets: dict[str, bytes] | None = None,
    styles: str | None = None,
    numbering: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("word/document.xml", _document_xml(body))
        if relationships is not None:
            package.writestr("word/_rels/document.xml.rels", relationships)
        if styles is not None:
            package.writestr("word/styles.xml", styles)
        if numbering is not None:
            package.writestr("word/numbering.xml", numbering)
        for name, content in (assets or {}).items():
            package.writestr(name, content)


def _native_pair(root: Path, package_id: str = "PKG-001") -> tuple[Path, Path]:
    package = root / package_id
    heading = _paragraph("01 核心突破练", style="Heading2")
    question = _paragraph("1．下列分离方法正确的是__________。")
    options = _paragraph("A．过滤  B．蒸馏  C．分液  D．升华")
    table = '''<w:tbl><w:tr>
      <w:tc><w:p><w:r><w:t>混合物</w:t></w:r></w:p></w:tc>
      <w:tc><w:p><w:r><w:t>方法</w:t></w:r></w:p></w:tc>
    </w:tr></w:tbl>'''
    original = package / "第01讲 测试（原卷版）.docx"
    solution = package / "第01讲 测试（解析版）.docx"
    _write_docx(original, heading + question + options + table)
    _write_docx(
        solution,
        heading
        + question
        + options
        + table
        + _paragraph("【答案】B")
        + _paragraph("【解析】沸点不同的互溶液体可用蒸馏分离。"),
    )
    return original, solution


def test_native_question_and_solution_pair_become_one_fast_import_candidate(
    tmp_path: Path,
) -> None:
    original, solution = _native_pair(tmp_path)
    result = WordHandoutImporter(tmp_path / "state").run_batch(
        [original, solution], persist=True
    )

    assert result.schema_version == WORD_HANDOUT_IMPORT_SCHEMA
    assert result.documents_total == 2
    assert result.documents_failed == 0
    assert result.primary_question_candidates == 1
    assert result.solution_reference_candidates == 1
    assert result.paired_question_candidates == 1
    assert result.quick_import_candidates == 1
    primary = next(
        item for item in result.candidate_index if item.record_role == "question_primary"
    )
    assert primary.import_state == "native_text_complete"
    assert primary.quick_import_eligible is True
    assert primary.reference_answer_text == "B"
    assert "沸点不同" in str(primary.reference_analysis_text)
    assert primary.answer_authority == "user_provided_teaching_material_reference"
    assert primary.answer_verified is False
    assert primary.source_document_sha256 == next(
        doc.source_sha256 for doc in result.documents if doc.document_role == "question_source"
    )
    table_blocks = [
        block for block in primary.native_blocks if block["block_kind"] == "table"
    ]
    assert table_blocks[0]["table"]["rows"][0]["cells"][1]["text"] == "方法"


def _image_relationships() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="{R}/image" Target="media/image1.png"/>
</Relationships>'''


def _drawing() -> str:
    return '<w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r>'


def test_visual_features_are_bound_per_question_not_per_document(tmp_path: Path) -> None:
    package = tmp_path / "PKG-002"
    original = package / "视觉混排（原卷版）.docx"
    solution = package / "视觉混排（解析版）.docx"
    decorative = _paragraph("装饰页眉", extra=_drawing())
    native = _paragraph("1．下列实验操作正确的是__________。") + _paragraph(
        "A．过滤  B．蒸馏  C．萃取  D．结晶"
    )
    visual = _paragraph("2．请根据下图选择正确的实验装置。", extra=_drawing())
    visual_only = _paragraph("3．", extra=_drawing())
    assets = {"word/media/image1.png": b"fixture-png"}
    _write_docx(
        original,
        decorative + _paragraph("01 核心突破练", style="Heading2") + native + visual + visual_only,
        relationships=_image_relationships(),
        assets=assets,
    )
    _write_docx(
        solution,
        decorative
        + _paragraph("01 核心突破练", style="Heading2")
        + native
        + _paragraph("【答案】A")
        + _paragraph("【解析】纯文字解析。")
        + visual
        + _paragraph("【答案】B")
        + _paragraph("【解析】需要查看装置图。")
        + visual_only
        + _paragraph("【答案】C")
        + _paragraph("【解析】需要查看图。"),
        relationships=_image_relationships(),
        assets=assets,
    )

    result = WordHandoutImporter(tmp_path / "state").run_batch(
        [original, solution], persist=True
    )
    primary = [
        item for item in result.candidate_index if item.record_role == "question_primary"
    ]
    assert [item.import_state for item in primary] == [
        "native_text_complete",
        "hybrid_visual_required",
        "visual_only_required",
    ]
    assert primary[0].media_references == ()
    assert primary[0].quick_import_eligible is True
    assert primary[1].media_references[0]["sha256"]
    assert "embedded_image_reference" in primary[1].blockers
    assert primary[2].quick_import_eligible is False
    assert result.quick_import_candidates == 1
    assert result.visual_completion_candidates == 2


def test_formula_ole_and_vertical_alignment_never_silently_become_plain_text(
    tmp_path: Path,
) -> None:
    package = tmp_path / "PKG-003"
    path = package / "公式对象（原卷版）.docx"
    relationships = f'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId2" Type="{R}/oleObject" Target="embeddings/oleObject1.bin"/>
</Relationships>'''
    body = (
        _paragraph("01 真题溯源练", style="Heading2")
        + '''<w:p><w:r><w:t>1．写出反应的方程式</w:t></w:r>
          <m:oMath><m:r><m:t>x+y</m:t></m:r></m:oMath>
          <w:r><w:object><w:OLEObject r:id="rId2"/></w:object></w:r>
          <w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r>
        </w:p>'''
    )
    _write_docx(
        path,
        body,
        relationships=relationships,
        assets={"word/embeddings/oleObject1.bin": b"ole-fixture"},
    )
    result = WordHandoutImporter().scan_document(path)
    candidate = result.candidates[0]
    assert candidate.import_state == "hybrid_visual_required"
    assert "ole_object_reference" in candidate.blockers
    assert "omml_equation_reference" not in candidate.blockers
    assert "run_vertical_alignment" not in candidate.blockers
    assert "x+y" in candidate.native_text
    assert "_{2}" in candidate.native_text
    assert "【待查看原文：嵌入对象或旧公式】" in candidate.native_text
    assert candidate.ole_references[0]["part_name"] == "word/embeddings/oleObject1.bin"
    assert candidate.ole_references[0]["sha256"]


def test_automatic_numbering_style_and_table_structure_are_preserved(tmp_path: Path) -> None:
    package = tmp_path / "PKG-004"
    path = package / "自动编号（原卷版）.docx"
    styles = f'''<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="{W}">
  <w:style w:type="paragraph" w:styleId="Question"><w:name w:val="题目"/></w:style>
</w:styles>'''
    numbering = f'''<?xml version="1.0" encoding="UTF-8"?>
<w:numbering xmlns:w="{W}">
  <w:abstractNum w:abstractNumId="9"><w:lvl w:ilvl="0"><w:start w:val="1"/>
    <w:numFmt w:val="decimal"/><w:lvlText w:val="%1．"/></w:lvl></w:abstractNum>
  <w:num w:numId="7"><w:abstractNumId w:val="9"/></w:num>
</w:numbering>'''
    body = '''<w:p><w:pPr><w:pStyle w:val="Question"/><w:numPr><w:ilvl w:val="0"/>
      <w:numId w:val="7"/></w:numPr></w:pPr><w:r><w:t>下列说法正确的是__________。</w:t></w:r></w:p>'''
    _write_docx(path, body, styles=styles, numbering=numbering)
    result = WordHandoutImporter().scan_document(path)
    candidate = result.candidates[0]
    assert candidate.source_question_label == "1"
    assert candidate.question_text.startswith("1．")
    block = candidate.native_blocks[0]
    assert block["style_id"] == "Question"
    assert block["style_name"] == "题目"
    assert block["numbering"]["num_id"] == "7"
    assert block["numbering"]["label"] == "1．"


def test_parenthesized_theme_subquestions_are_detected_without_treating_year_as_number(
    tmp_path: Path,
) -> None:
    path = tmp_path / "PKG-094" / "综合训练（原卷版）.docx"
    body = (
        _paragraph("一、胶体粒子与溶液配制(17分)")
        + _paragraph("(2025·上海模拟)阅读材料，回答问题。")
        + _paragraph("(1)配制该溶液需要量取________mL原液。")
        + _paragraph("(2)完成实验还需要的玻璃仪器是________。")
    )
    _write_docx(path, body)
    result = WordHandoutImporter().scan_document(path)
    assert [item.source_question_label for item in result.candidates] == ["1", "2"]
    assert all(item.section_title.startswith("一、") for item in result.candidates)
    assert all("2025" not in item.source_question_label for item in result.candidates)


def test_grouped_theme_answers_are_split_back_to_parenthesized_questions(
    tmp_path: Path,
) -> None:
    package = tmp_path / "PKG-095"
    original = package / "综合训练（原卷版）.docx"
    solution = package / "综合训练（解析版）.docx"
    questions = (
        _paragraph("一、物质检验(20分)")
        + _paragraph("(1)检验该离子所用试剂是________。")
        + _paragraph("(2)写出反应现象________。")
    )
    _write_docx(original, questions)
    _write_docx(
        solution,
        questions
        + _paragraph("【答案】(1)硝酸银溶液")
        + _paragraph("(2)产生白色沉淀")
        + _paragraph("【解析】(1)氯离子与银离子反应。")
        + _paragraph("(2)氯化银为白色沉淀。"),
    )
    result = WordHandoutImporter(tmp_path / "state").run_batch(
        [original, solution], persist=True
    )
    primary = [
        item for item in result.candidate_index if item.record_role == "question_primary"
    ]
    assert len(primary) == 2
    assert [item.reference_answer_text for item in primary] == [
        "硝酸银溶液",
        "产生白色沉淀",
    ]
    assert "氯离子" in str(primary[0].reference_analysis_text)
    assert "氯化银" in str(primary[1].reference_analysis_text)
    solution_records = [
        item
        for item in result.candidate_index
        if item.record_role == "answer_reference_duplicate"
    ]
    assert len(solution_records) == 2


def test_ambiguous_numbered_knowledge_is_not_promoted_and_uncertain_question_blocks(
    tmp_path: Path,
) -> None:
    path = tmp_path / "PKG-005" / "边界（原卷版）.docx"
    body = (
        _paragraph("知识点1 物质分离与提纯")
        + _paragraph("1．物质的分离")
        + _paragraph("补充材料", style="Heading2")
        + _paragraph("2．化合物甲乙丙丁依次转化的关系式和反应条件如下")
    )
    _write_docx(path, body)
    result = WordHandoutImporter().scan_document(path)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source_question_label == "2"
    assert candidate.import_state == "hybrid_visual_required"
    assert "question_boundary_uncertain" in candidate.blockers
    assert candidate.quick_import_eligible is False


def test_content_addressed_cache_is_idempotent_and_supports_resume(
    tmp_path: Path,
) -> None:
    original, solution = _native_pair(tmp_path, "PKG-006")
    progress: list[dict[str, Any]] = []
    importer = WordHandoutImporter(tmp_path / "state")
    first = importer.run_batch(
        [original, solution],
        persist=True,
        progress_callback=lambda item: progress.append(item.as_dict()),
    )
    second = importer.run_batch([original, solution], persist=True)

    assert first.batch_id == second.batch_id
    assert first.documents_cached == 0
    assert second.documents_cached == 2
    assert second.quick_import_candidates == 1
    assert progress[-1]["processed_documents"] == 2
    assert Path(str(second.manifest_path)).is_file()
    manifest = json.loads(Path(str(second.manifest_path)).read_text(encoding="utf-8"))
    assert manifest["batch_id"] == first.batch_id
    assert "api_key" not in json.dumps(manifest, ensure_ascii=False).casefold()
    assert len(list((tmp_path / "state" / "documents").glob("*.json"))) == 2
    inventory = importer.personal_handout_inventory(
        query="分离方法", state="quick_import_ready", limit=20
    )
    assert inventory["scope"] == "personal_handouts"
    assert inventory["central_catalog_mutated"] is False
    assert inventory["counts"]["personal_candidate_records"] == 1
    assert inventory["counts"]["native_text_submitted"] == 1
    assert inventory["counts"]["visual_completion_queue"] == 0
    assert inventory["page"]["filtered_total"] == 1
    assert inventory["items"][0]["inventory_lane"] == "native_text_submitted"


@pytest.mark.parametrize(
    "hidden_body",
    [
        '<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>OMITTED_DIRECT</w:t></w:r></w:p>',
        (
            '<w:p><w:r><w:rPr><w:rStyle w:val="HiddenChar"/></w:rPr>'
            '<w:t>OMITTED_CHAR_STYLE</w:t></w:r></w:p>'
        ),
        (
            '<w:p><w:pPr><w:pStyle w:val="HiddenParagraph"/></w:pPr>'
            '<w:r><w:t>OMITTED_PARAGRAPH_STYLE</w:t></w:r></w:p>'
        ),
        '<w:p><w:del><w:r><w:delText>OMITTED_DELETION</w:delText></w:r></w:del></w:p>',
        (
            '<w:p><w:moveFrom><w:r><w:t>OMITTED_MOVE_FROM</w:t></w:r></w:moveFrom>'
            '<w:moveTo><w:r><w:t>VISIBLE_MOVE_DESTINATION</w:t></w:r></w:moveTo></w:p>'
        ),
    ],
    ids=["direct-hidden", "character-style", "paragraph-style", "deleted", "moved"],
)
def test_persisted_candidates_never_reintroduce_omitted_run_content(
    tmp_path: Path, hidden_body: str
) -> None:
    path = tmp_path / "PKG-008" / "可见性（原卷版）.docx"
    styles = f'''<w:styles xmlns:w="{W}">
      <w:style w:type="character" w:styleId="HiddenBase"><w:rPr><w:vanish/></w:rPr></w:style>
      <w:style w:type="character" w:styleId="HiddenChar"><w:basedOn w:val="HiddenBase"/></w:style>
      <w:style w:type="paragraph" w:styleId="HiddenParagraphBase"><w:rPr><w:vanish/></w:rPr></w:style>
      <w:style w:type="paragraph" w:styleId="HiddenParagraph"><w:basedOn w:val="HiddenParagraphBase"/></w:style>
      <w:style w:type="character" w:styleId="SubBase"><w:rPr><w:vertAlign w:val="subscript"/></w:rPr></w:style>
      <w:style w:type="character" w:styleId="Sub"><w:basedOn w:val="SubBase"/></w:style>
    </w:styles>'''
    body = (
        _paragraph("01 核心突破练", style="Heading2")
        + _paragraph("1．请写出该物质的化学式__________。")
        + hidden_body
        + _paragraph(
            "可见物质H",
            extra='<w:r><w:rPr><w:rStyle w:val="Sub"/></w:rPr><w:t>2</w:t></w:r>'
            '<w:r><w:t>O</w:t></w:r>',
        )
    )
    _write_docx(path, body, styles=styles)
    state = tmp_path / "state"
    importer = WordHandoutImporter(state)
    first = importer.run_batch([path], persist=True)

    assert first.documents_failed == 0
    assert len(first.candidate_index) == 1
    candidate = first.candidate_index[0]
    assert "H_{2}O" in candidate.native_text
    assert "OMITTED_" not in json.dumps(candidate.as_dict())
    assert all(block["rich_runs"] == () for block in candidate.native_blocks)
    if "moveTo" in hidden_body:
        assert candidate.native_text.count("VISIBLE_MOVE_DESTINATION") == 1
        assert "tracked_changes" in candidate.features

    document_records = list((state / "documents").glob("*.json"))
    assert len(document_records) == 1
    stored = json.loads(document_records[0].read_text(encoding="utf-8"))
    assert "OMITTED_" not in json.dumps(stored)
    assert all(
        block["rich_runs"] == []
        for item in stored["document"]["candidates"]
        for block in item["native_blocks"]
    )
    assert "OMITTED_" not in Path(str(first.manifest_path)).read_text(encoding="utf-8")

    reopened = WordHandoutImporter(state).run_batch([path], persist=True)
    assert reopened.documents_cached == 1
    assert "OMITTED_" not in json.dumps(reopened.as_dict())
    assert "H_{2}O" in reopened.candidate_index[0].native_text


def test_previous_parser_cache_with_raw_runs_is_rebuilt(tmp_path: Path) -> None:
    original, _ = _native_pair(tmp_path, "PKG-009")
    state = tmp_path / "state"
    importer = WordHandoutImporter(state)
    importer.run_batch([original], persist=True)
    cache = next((state / "documents").glob("*.json"))
    stored = json.loads(cache.read_text(encoding="utf-8"))
    stored["parser_version"] = "1.2.0"
    stored["document"]["candidates"][0]["native_blocks"][0]["rich_runs"] = [
        {"text": "OMITTED_LEGACY_RAW_RUN"}
    ]
    cache.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

    rebuilt = WordHandoutImporter(state).run_batch([original], persist=True)

    assert rebuilt.documents_cached == 0
    assert "OMITTED_LEGACY_RAW_RUN" not in json.dumps(rebuilt.as_dict())
    assert "OMITTED_LEGACY_RAW_RUN" not in cache.read_text(encoding="utf-8")


def test_corpus_discovery_and_desktop_facade_direct_call_need_no_http(tmp_path: Path) -> None:
    expanded = tmp_path / "workspace" / "sh-chem-db" / ".intake" / "2026-07-30-user-teaching-pack" / "expanded"
    original, solution = _native_pair(expanded, "PKG-001")
    ignored = expanded / "misc" / "ignored.docx"
    _write_docx(ignored, _paragraph("1．下列说法正确的是__________。"))
    documents = WordHandoutImporter.discover_documents(expanded)
    assert documents == (solution.resolve(), original.resolve()) or documents == (
        original.resolve(),
        solution.resolve(),
    )

    workspace = tmp_path / "workspace"
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    paths = DesktopPaths.from_workspace(workspace, state_root=tmp_path / "personal")
    facade = object.__new__(DesktopWorkbenchFacade)
    facade.paths = paths
    result = facade.run_word_handout_import_batch(files=[original, solution])
    assert result["quick_import_candidates"] == 1
    assert Path(result["manifest_path"]).is_relative_to(paths.state_root)
    inventory = facade.personal_handout_inventory(limit=10)
    assert inventory["counts"]["personal_candidate_records"] == 1
    assert inventory["items"][0]["candidate_id"]


def test_picker_summary_is_native_ooxml_only_and_pdf_is_rejected(tmp_path: Path) -> None:
    original, _ = _native_pair(tmp_path, "PKG-007")
    summary = inspect_docx_native_summary(original)
    assert summary["xml_locator"] == "word/document.xml"
    assert summary["native_text_characters"] > 0
    assert summary["candidate_count"] == 1

    fake_pdf = tmp_path / "material.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7 hidden text must not be read")
    with pytest.raises(WordHandoutImportError, match="DOCX"):
        WordHandoutImporter(tmp_path / "state").run_batch([fake_pdf], persist=True)

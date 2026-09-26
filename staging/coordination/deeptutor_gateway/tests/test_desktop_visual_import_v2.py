from __future__ import annotations

import base64
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
FORMAL_FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "desktop_visual_import_v2"
SOURCE_TEST_SUPPORT_ROOT = WORKSPACE_ROOT / "parallel_outputs" / "intake_batches_v2"
FIXTURE_PATH = FORMAL_FIXTURE_ROOT / "synthetic_multifile_theme.json"
DESKTOP_FIXTURE_PATH = FORMAL_FIXTURE_ROOT / "desktop_import_contract.json"
MIXED_FIXTURE_PATH = FORMAL_FIXTURE_ROOT / "mixed_desktop_batch.json"

from integrations.deeptutor_shchem_v1 import desktop_visual_import_v2 as bridge
from integrations.deeptutor_shchem_v1 import intake_batches_v2 as core


def test_formal_modules_import_in_a_clean_python_process() -> None:
    code = (
        "from integrations.deeptutor_shchem_v1 import "
        "desktop_visual_import_v2 as bridge; "
        "from integrations.deeptutor_shchem_v1 import intake_batches_v2 as core; "
        "assert bridge.DesktopImportCoordinatorV2; assert core.CandidateCAS"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", code],
        cwd=WORKSPACE_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""


def _png_bytes(color: tuple[int, int, int] = (30, 90, 160)) -> bytes:
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (24, 32), color).save(output, format="PNG", optimize=False)
    return output.getvalue()


def _docx_bytes(*, with_visual_objects: bool = False) -> bytes:
    visual = ""
    if with_visual_objects:
        visual = """
        <w:p>
          <w:r><w:drawing><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
            <a:graphicData><a:t>DRAWING_TEXT_MUST_NOT_BE_NATIVE</a:t></a:graphicData>
          </a:graphic></w:drawing></w:r>
        </w:p>
        <w:p><w:r><m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
          <m:r><m:t>OMML_TEXT_MUST_NOT_BE_NATIVE</m:t></m:r>
        </m:oMath></w:r></w:p>
        <w:p><w:r><w:object><w:oleObject r:id="rId2" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></w:object></w:r></w:p>
        """
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>第1题 可编辑题干</w:t></w:r></w:p>
        <w:p><w:r><w:t>写出反应式并说明现象。</w:t></w:r></w:p>
        {visual}
      </w:body>
    </w:document>"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/oleObject1.bin"/>
    </Relationships>"""
    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        package.writestr("word/document.xml", document)
        if with_visual_objects:
            package.writestr("word/_rels/document.xml.rels", rels)
            package.writestr("word/embeddings/oleObject1.bin", b"not-text")
    return output.getvalue()


def _source(
    *,
    role: str,
    order: int,
    filename: str,
    content: bytes,
    group: str = "paper-a",
    source_id: str | None = None,
) -> bridge.DesktopSourceFile:
    mime = (
        "image/png"
        if filename.endswith(".png")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return bridge.DesktopSourceFile(
        role=role,
        order_index=order,
        filename=filename,
        mime_type=mime,
        content=content,
        group_id=group,
        source_file_id=source_id,
    )


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_table_markers_do_not_certify_actual_editable_content():
    output = io.BytesIO()
    document = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:object/></w:r></w:p></w:tc></w:tr></w:tbl></w:body>
    </w:document>'''
    with zipfile.ZipFile(output, "w") as package:
        package.writestr("word/document.xml", document)
    result = bridge.inspect_native_docx(_source(
        role="handout", order=1, filename="visual-table.docx", content=output.getvalue()
    ))
    assert not result.quick_import_eligible
    assert result.import_state == "visual_only_required"


def _mixed_fixture() -> dict[str, Any]:
    return json.loads(MIXED_FIXTURE_PATH.read_text(encoding="utf-8"))


def _mixed_sources() -> tuple[bridge.DesktopSourceFile, ...]:
    return tuple(
        bridge.DesktopSourceFile(
            role=row["role"],
            order_index=row["order_index"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            content=base64.b64decode(row["content_base64"], validate=True),
            group_id=row["group_id"],
            source_file_id=row["source_file_id"],
        )
        for row in _mixed_fixture()["sources"]
    )


def _lean_shard(source_role: str, *, shard_index: int = 1) -> core.VisualShardRequest:
    pixels = _png_bytes(
        {"question": (30, 90, 160), "answer": (40, 120, 70), "handout": (90, 40, 120)}[
            source_role
        ]
    )
    source_id = f"SRC-LEAN-{source_role.upper()}"
    page = core.VisualPixelPage(
        source_file_id=source_id,
        source_role=source_role,
        source_order=1,
        page_number=1,
        mime_type="image/png",
        width=24,
        height=32,
        page_sha256=core.sha256_bytes(pixels),
        render_recipe_sha256=core.sha256_bytes(f"lean:{source_role}".encode()),
        pixels=pixels,
    )
    return core.VisualShardRequest(
        batch_id="BATCH-LEAN-OBSERVATION",
        shard_id=f"BATCH-LEAN-OBSERVATION:{source_role}:{shard_index:04d}",
        shard_index=shard_index,
        source_role=source_role,
        pages=(page,),
    )


def _lean_evidence(
    request: core.VisualShardRequest, *, evidence_id: str
) -> dict[str, Any]:
    page = request.pages[0]
    return {
        "evidence_id": evidence_id,
        "source_file_id": page.source_file_id,
        "source_role": request.source_role,
        "page_number": page.page_number,
        "page_sha256": page.page_sha256,
        "bbox": {"x": 0.08, "y": 0.1, "width": 0.84, "height": 0.78},
    }


def _lean_question_fragment(request: core.VisualShardRequest) -> dict[str, Any]:
    evidence = _lean_evidence(request, evidence_id="EV-LEAN-QUESTION-1")
    refs = [evidence["evidence_id"]]
    expression = {
        "raw": "Cl₂ + 2OH⁻ → Cl⁻ + ClO⁻ + H₂O",
        "kind": "ionic_equation",
        "status": "uncertain",
        "evidence_refs": refs,
    }
    return {
        "schema_version": core.INTAKE_BATCH_VISUAL_FRAGMENT_V2_SCHEMA_VERSION,
        "shard_id": request.shard_id,
        "source_role": "question",
        "input_mode": core.DIRECT_PAGE_PIXEL_MODE,
        "source_text_layer_used": False,
        "fallback_used": False,
        "evidence": [evidence],
        "paper_identity": {
            "title": "2025 年上海某校化学练习",
            "source_year": 2025,
            "source_region_or_school": "上海某校（页面可见）",
            "paper_type": "练习",
            "evidence_refs": refs,
        },
        "theme_fragments": [
            {
                "theme_big_question_id": "THEME-LEAN-1",
                "theme_number": "一",
                "title": "氯及其化合物",
                "context": "含氯消毒剂的制备与使用。",
                "sequence_in_paper": 1,
                "fragment_position": "complete",
                "shared_materials": [
                    {
                        "shared_material_id": "SHARED-LEAN-1",
                        "material_type": "题干材料",
                        "content": "页面给出含氯消毒剂的制备信息。",
                        "chemical_expressions": [],
                        "visual_object_refs": ["VISUAL-LEAN-1"],
                        "evidence_refs": refs,
                    }
                ],
                "visual_objects": [
                    {
                        "visual_object_id": "VISUAL-LEAN-1",
                        "kind": "apparatus",
                        "description": "页面中的气体制备装置，部分导管连接较细。",
                        "evidence_refs": refs,
                    }
                ],
                "dependency_edges": [],
                "printed_questions": [
                    {
                        "printed_question_id": "PRINTED-LEAN-1",
                        "question_number": "1",
                        "sequence_in_theme": 1,
                        "stem": "写出页面所示反应的离子方程式。",
                        "options": [],
                        "response_requirements": "写出离子方程式。",
                        "chemical_expressions": [expression],
                        "shared_material_refs": ["SHARED-LEAN-1"],
                        "visual_object_refs": ["VISUAL-LEAN-1"],
                        "atomic_parts": [
                            {
                                "atomic_part_id": "ATOMIC-LEAN-1",
                                "part_label": "（1）",
                                "sequence_in_printed": 1,
                                "stem": "写出反应的离子方程式。",
                                "options": [],
                                "response_requirements": "填写一个离子方程式。",
                                "chemical_expressions": [expression],
                                "visual_object_refs": ["VISUAL-LEAN-1"],
                                "evidence_refs": refs,
                            }
                        ],
                        "evidence_refs": refs,
                    }
                ],
                "evidence_refs": refs,
            }
        ],
        "answer_candidates": [],
        "warnings": ["方程式的部分上标较小，等待教师核对。"],
    }


def _lean_answer_fragment(request: core.VisualShardRequest) -> dict[str, Any]:
    evidence = _lean_evidence(request, evidence_id="EV-LEAN-ANSWER-1")
    refs = [evidence["evidence_id"]]
    return {
        "schema_version": core.INTAKE_BATCH_VISUAL_FRAGMENT_V2_SCHEMA_VERSION,
        "shard_id": request.shard_id,
        "source_role": "answer",
        "input_mode": core.DIRECT_PAGE_PIXEL_MODE,
        "source_text_layer_used": False,
        "fallback_used": False,
        "evidence": [evidence],
        "paper_identity": None,
        "theme_fragments": [],
        "answer_candidates": [
            {
                "answer_candidate_id": "ANSWER-LEAN-1",
                "question_number": "unknown",
                "part_label": None,
                "atomic_part_id": None,
                "answer_body": "页面可见答案为氯气与碱反应的离子方程式。",
                "chemical_expressions": [
                    {
                        "raw": "Cl₂ + 2OH⁻ → Cl⁻ + ClO⁻ + H₂O",
                        "kind": "ionic_equation",
                        "status": "observed",
                        "evidence_refs": refs,
                    }
                ],
                "evidence_refs": refs,
            }
        ],
        "warnings": ["答案页未显示可唯一对应的题号。"],
    }


def _lean_source_records(
    requests: Sequence[core.VisualShardRequest],
) -> tuple[list[dict[str, Any]], str]:
    records = []
    for request in requests:
        page = request.pages[0]
        records.append(
            {
                "source_file_id": page.source_file_id,
                "source_role": page.source_role,
                "source_order": 1,
                "filename": f"{page.source_file_id}.png",
                "mime_type": page.mime_type,
                "size_bytes": len(page.pixels),
                "source_file_sha256": core.sha256_bytes(page.pixels),
                "pages": [page.public_manifest()],
            }
        )
    batch_sha = core.sha256_bytes(
        core.canonical_json_bytes(core._batch_subject(records))
    )
    return records, batch_sha


def test_lean_visual_schema_is_deeply_closed_and_provider_compatible() -> None:
    schema = core.intake_batch_visual_fragment_v2_schema()
    Draft202012Validator.check_schema(schema)

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(
                    value.get("properties", {})
                )
            assert not {
                "allOf",
                "not",
                "if",
                "then",
                "else",
                "pattern",
                "minLength",
                "maxLength",
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "minItems",
                "maxItems",
                "uniqueItems",
                "const",
            }.intersection(value)
            assert not any(key.startswith("dependent") for key in value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
    definitions = schema["$defs"]
    assert not {
        "curriculum",
        "classification",
        "cognitive_difficulty",
        "item_type",
    }.intersection(definitions["atomic_part"]["properties"])
    assert not {
        "analysis",
        "max_score",
        "scoring_points",
        "authority",
        "independently_verified",
    }.intersection(definitions["answer_candidate"]["properties"])
    assert "normalized" not in definitions["chemical_expression"]["properties"]
    assert not {"structured_representation", "requires_review"}.intersection(
        definitions["visual_object"]["properties"]
    )


def test_nonempty_lean_question_postfills_and_builds_valid_candidate() -> None:
    request = _lean_shard("question")
    raw = _lean_question_fragment(request)
    Draft202012Validator(core.intake_batch_visual_fragment_v2_schema()).validate(raw)

    fragment = core._validate_fragment(raw, request=request)
    atomic = fragment["theme_fragments"][0]["printed_questions"][0]["atomic_parts"][0]
    refs = atomic["evidence_refs"]
    assert atomic["curriculum"] == {
        "textbook_edition": None,
        "primary_chapter": None,
        "secondary_chapters": [],
        "mapping_status": "unknown",
        "rationale": "视觉导入阶段不作教材映射，等待教师或本地知识图谱补充。",
        "evidence_refs": refs,
    }
    assert atomic["classification"] == {
        "item_type": "unknown",
        "primary_knowledge_K": [],
        "supporting_knowledge_K": [],
        "ability_A": [],
        "context_C": [],
        "response_R": [],
        "representation_RP": [],
        "evidence_refs": refs,
    }
    assert atomic["cognitive_difficulty"]["status"] == "unknown"
    assert atomic["cognitive_difficulty"]["factors"] == []
    assert atomic["cognitive_difficulty"]["measured_difficulty"] is None
    assert atomic["cognitive_difficulty"]["human_verified"] is False
    assert atomic["cognitive_difficulty"]["student_data_used"] is False
    assert atomic["chemical_expressions"][0]["normalized"] is None
    assert atomic["chemical_expressions"][0]["status"] == "uncertain"
    visual = fragment["theme_fragments"][0]["visual_objects"][0]
    assert visual["structured_representation"] is None
    assert visual["requires_review"] is True
    assert fragment["warnings"] == raw["warnings"]
    assert fragment["evidence"][0]["page_sha256"] == request.pages[0].page_sha256
    assert fragment["evidence"][0]["bbox"] == raw["evidence"][0]["bbox"]

    records, batch_sha = _lean_source_records([request])
    candidate = core._build_candidate(
        batch_id=request.batch_id,
        batch_sha256=batch_sha,
        source_records=records,
        requests=[request],
        fragments=[fragment],
    )
    validated = core.validate_candidate_v2(candidate)
    assert len(validated["paper"]["theme_big_questions"]) == 1
    assert len(validated["paper"]["theme_big_questions"][0]["printed_questions"]) == 1
    assert validated["candidate_status"] == "candidate_only"
    assert validated["human_reviewed"] is False
    assert validated["retrieval_ready"] is False
    assert validated["publication_allowed"] is False
    assert validated["central_question_bank_write"] is False


def test_nonempty_lean_answer_is_unknown_and_not_silently_aligned() -> None:
    question_request = _lean_shard("question", shard_index=1)
    answer_request = _lean_shard("answer", shard_index=2)
    question_raw = _lean_question_fragment(question_request)
    answer_raw = _lean_answer_fragment(answer_request)
    schema = core.intake_batch_visual_fragment_v2_schema()
    Draft202012Validator(schema).validate(answer_raw)

    question = core._validate_fragment(question_raw, request=question_request)
    answer = core._validate_fragment(answer_raw, request=answer_request)
    normalized_answer = answer["answer_candidates"][0]
    assert normalized_answer["analysis"] == ""
    assert normalized_answer["max_score"] == 0
    assert normalized_answer["scoring_points"] == []
    assert normalized_answer["authority"] == "unknown"
    assert normalized_answer["independently_verified"] is False
    assert normalized_answer["chemical_expressions"][0]["normalized"] is None

    records, batch_sha = _lean_source_records([question_request, answer_request])
    candidate = core._build_candidate(
        batch_id=question_request.batch_id,
        batch_sha256=batch_sha,
        source_records=records,
        requests=[question_request, answer_request],
        fragments=[question, answer],
    )
    candidate = core.validate_candidate_v2(candidate)
    assert [
        row["answer_candidate_id"] for row in candidate["unaligned_answer_candidates"]
    ] == ["ANSWER-LEAN-1"]
    atomic = candidate["paper"]["theme_big_questions"][0]["printed_questions"][0][
        "atomic_parts"
    ][0]
    assert atomic["answer"]["status"] == "missing"
    assert any(
        blocker["code"] == "answer_alignment_pending"
        for blocker in candidate["review_blockers"]
    )


def test_postfill_preserves_full_fragment_and_candidate_hash_is_stable() -> None:
    request = _lean_shard("question")
    raw = _lean_question_fragment(request)
    first = core._validate_fragment(raw, request=request)
    legacy_bytes = core.canonical_json_bytes(first)
    assert (
        core.canonical_json_bytes(core._postfill_visual_observation_defaults(first))
        == legacy_bytes
    )

    second = core._validate_fragment(raw, request=request)
    assert core.canonical_json_bytes(first) == core.canonical_json_bytes(second)
    records, batch_sha = _lean_source_records([request])
    first_candidate = core._build_candidate(
        batch_id=request.batch_id,
        batch_sha256=batch_sha,
        source_records=records,
        requests=[request],
        fragments=[first],
    )
    second_candidate = core._build_candidate(
        batch_id=request.batch_id,
        batch_sha256=batch_sha,
        source_records=records,
        requests=[request],
        fragments=[second],
    )
    assert core.candidate_sha256(first_candidate) == core.candidate_sha256(
        second_candidate
    )


def test_desktop_fixture_declares_non_negotiable_lane_invariants() -> None:
    fixture = json.loads(DESKTOP_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["synthetic_only"] is True
    assert fixture["invariants"] == {
        "role_and_order_are_explicit": True,
        "filename_is_not_pairing_signal": True,
        "source_text_layer_supplied_to_visual_model": False,
        "ocr_or_text_fallback_allowed": False,
        "central_question_bank_write": False,
        "candidate_only": True,
    }
    for role in bridge.SOURCE_ROLES:
        rows = [item for item in fixture["sources"] if item["role"] == role]
        assert [item["order_index"] for item in rows] == list(range(1, len(rows) + 1))


def test_real_mixed_fixture_routes_three_states_and_queues_without_provider(
    tmp_path: Path,
) -> None:
    fixture = _mixed_fixture()
    sources = _mixed_sources()
    rows = {row["source_file_id"]: row for row in fixture["sources"]}
    assert fixture["synthetic_only"] is True
    assert {source.mime_type for source in sources} == {
        "image/png",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    for source in sources:
        assert (
            core.sha256_bytes(source.content)
            == rows[source.effective_source_file_id]["source_sha256"]
        )
    pdf = next(source for source in sources if source.mime_type == "application/pdf")
    assert pdf.content.startswith(b"%PDF-1.4") and pdf.content.rstrip().endswith(
        b"%%EOF"
    )
    for source in sources:
        if source.mime_type.endswith("wordprocessingml.document"):
            with zipfile.ZipFile(io.BytesIO(source.content)) as package:
                assert package.testzip() is None
                assert "word/document.xml" in package.namelist()
                assert "_rels/.rels" in package.namelist()
                root_relationships = package.read("_rels/.rels")
                assert b"/relationships/officeDocument" in root_relationships
                assert b'Target="word/document.xml"' in root_relationships

    request = bridge.DesktopImportRequest.from_sources(
        question_files=[source for source in sources if source.role == "question"],
        answer_files=[source for source in sources if source.role == "answer"],
        handout_files=[source for source in sources if source.role == "handout"],
        source_type="合成混合格式批次",
    )
    renderer = _MixedFixtureRenderer(fixture)
    coordinator = bridge.DesktopImportCoordinatorV2(
        archive_root=tmp_path / "personal-state",
        renderer=renderer,
    )
    plan = coordinator.plan(request)
    assert [(row["role"], row["order_index"]) for row in plan.sources] == [
        ("question", 1),
        ("answer", 1),
        ("handout", 1),
        ("handout", 2),
    ]
    states = {
        row["source_file_id"]: row["import_state"] for row in plan.source_import_states
    }
    assert states == {
        row["source_file_id"]: row["expected_import_state"]
        for row in fixture["sources"]
    }
    assert plan.native_quick_count == fixture["expected"]["native_quick_count"]
    assert plan.visual_queue_count == fixture["expected"]["visual_queue_count"]
    assert list(plan.visual_source_ids) == fixture["expected"]["visual_source_ids"]

    result = coordinator.process(request)
    receipt = result.as_dict()
    assert result.visual_status == fixture["expected"]["no_provider_status"]
    assert [row["source_file_id"] for row in result.visual_queue] == fixture[
        "expected"
    ]["visual_source_ids"]
    assert receipt["candidate_only"] is True
    assert receipt["central_question_bank_write"] is False
    assert result.visual_candidate is None
    assert result.blockers[0]["code"] == "visual_provider_required"
    assert len(list((tmp_path / "personal-state" / "sources").iterdir())) == 4
    assert renderer.calls == [("MIX-H-001", "handout")]
    assert {row["source_file_id"] for row in result.pixel_pages} == {
        "MIX-Q-001",
        "MIX-H-001",
    }
    native_record = next(
        row for row in result.native_records if row["source_file_id"] == "MIX-H-001"
    )
    assert (
        native_record["page_binding_status"]
        == "archived_rendered_page_evidence_pending_bbox"
    )


def _fixture_sources() -> tuple[
    list[bridge.DesktopSourceFile], list[bridge.DesktopSourceFile]
]:
    fixture = _fixture()
    questions = [
        bridge.DesktopSourceFile(
            role="question",
            order_index=index,
            filename=row["filename"],
            mime_type=row["mime_type"],
            content=base64.b64decode(row["content_base64"], validate=True),
            group_id="fixture-paper",
            source_file_id=row["source_file_id"],
        )
        for index, row in enumerate(fixture["question_files"], 1)
    ]
    answers = [
        bridge.DesktopSourceFile(
            role="answer",
            order_index=index,
            filename=row["filename"],
            mime_type=row["mime_type"],
            content=base64.b64decode(row["content_base64"], validate=True),
            group_id="fixture-paper",
            source_file_id=row["source_file_id"],
        )
        for index, row in enumerate(fixture["answer_files"], 1)
    ]
    return questions, answers


class _FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def render(self, source_file: Any, *, source_role: str) -> list[Any]:
        self.calls.append((source_file.source_file_id, source_role))
        pixels = _png_bytes((90, 40, 120))
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(pixels)) as image:
            width, height = image.size
        return [
            core.RenderedPixelPage(
                pixels=pixels,
                mime_type="image/png",
                width=width,
                height=height,
                render_recipe_sha256="a" * 64,
            )
        ]


class _MixedFixtureRenderer:
    """Deterministic page renderer backed by fixture pixels, never text."""

    def __init__(self, fixture: Mapping[str, Any]) -> None:
        self.rows = {row["source_file_id"]: row for row in fixture["sources"]}
        self.calls: list[tuple[str, str]] = []

    def render(self, source_file: Any, *, source_role: str) -> list[Any]:
        source_id = source_file.source_file_id
        assert isinstance(source_id, str)
        row = self.rows[source_id]
        assert core.sha256_bytes(source_file.content) == row["source_sha256"]
        self.calls.append((source_id, source_role))
        return [
            core.RenderedPixelPage(
                pixels=base64.b64decode(page["content_base64"], validate=True),
                mime_type=page["mime_type"],
                width=page["width"],
                height=page["height"],
                render_recipe_sha256=page["render_recipe_sha256"],
            )
            for page in row["rendered_pages"]
        ]


def _strict_candidate_from_shards(
    shards: Sequence[core.VisualShardRequest], *, batch_id: str
) -> dict[str, Any]:
    """Build one minimal but fully validated candidate for adapter tests."""

    sources_by_role: dict[str, dict[str, list[core.VisualPixelPage]]] = {
        role: {} for role in bridge.SOURCE_ROLES
    }
    for shard in shards:
        for page in shard.pages:
            sources_by_role[page.source_role].setdefault(
                page.source_file_id, []
            ).append(page)
    source_records: list[dict[str, Any]] = []
    for role in bridge.SOURCE_ROLES:
        ordered = sorted(
            sources_by_role[role].items(),
            key=lambda item: (item[1][0].source_order, item[0]),
        )
        for source_order, (source_id, pages) in enumerate(ordered, 1):
            page_rows = []
            for page_number, page in enumerate(
                sorted(pages, key=lambda value: value.page_number), 1
            ):
                row = page.public_manifest()
                row["source_order"] = source_order
                row["page_number"] = page_number
                page_rows.append(row)
            source_records.append(
                {
                    "source_file_id": source_id,
                    "source_role": role,
                    "source_order": source_order,
                    "filename": f"{source_id}.synthetic-page.png",
                    "mime_type": page_rows[0]["mime_type"],
                    "size_bytes": sum(row["size_bytes"] for row in page_rows),
                    "source_file_sha256": core.sha256_bytes(
                        core.canonical_json_bytes(page_rows)
                    ),
                    "pages": page_rows,
                }
            )
    batch_sha = core.sha256_bytes(
        core.canonical_json_bytes(core._batch_subject(source_records))
    )
    fragments: list[dict[str, Any]] = []
    hierarchy_emitted = False
    for shard in shards:
        evidence = [
            {
                "evidence_id": (
                    f"EV-{shard.shard_index:04d}-{page.page_number:04d}-"
                    f"{page.page_sha256[:16]}"
                ),
                "source_file_id": page.source_file_id,
                "source_role": shard.source_role,
                "page_number": page.page_number,
                "page_sha256": page.page_sha256,
                "bbox": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9},
            }
            for page in shard.pages
        ]
        refs = [row["evidence_id"] for row in evidence]
        paper_identity: Mapping[str, Any] | None = None
        themes: list[dict[str, Any]] = []
        if shard.source_role != "answer" and not hierarchy_emitted:
            hierarchy_emitted = True
            suffix = batch_sha[:16]
            paper_identity = {
                "title": "合成适配器候选",
                "source_year": "unknown",
                "source_region_or_school": "synthetic-only",
                "paper_type": "fixture",
                "evidence_refs": refs,
            }
            curriculum = {
                "textbook_edition": None,
                "primary_chapter": None,
                "secondary_chapters": [],
                "mapping_status": "unknown",
                "rationale": "合成适配器测试不作教材映射。",
                "evidence_refs": refs,
            }
            classification = {
                "item_type": "synthetic_adapter",
                "primary_knowledge_K": ["K-synthetic"],
                "supporting_knowledge_K": [],
                "ability_A": ["A-observe"],
                "context_C": ["C-synthetic"],
                "response_R": ["R-short-answer"],
                "representation_RP": ["RP-page"],
                "evidence_refs": refs,
            }
            difficulty = {
                "cognitive_prelabel": "unknown",
                "status": "unknown",
                "rationale": "合成适配器测试不作难度判断。",
                "factors": [],
                "measured_difficulty": None,
                "human_verified": False,
                "student_data_used": False,
                "evidence_refs": refs,
            }
            atomic = {
                "atomic_part_id": f"ATOMIC-TEST-{suffix}",
                "part_label": "（1）",
                "sequence_in_printed": 1,
                "stem": "仅用于协调层测试的合成最小作答单元。",
                "options": [],
                "response_requirements": "返回一个合成测试答案。",
                "chemical_expressions": [],
                "visual_object_refs": [],
                "curriculum": curriculum,
                "classification": classification,
                "cognitive_difficulty": difficulty,
                "evidence_refs": refs,
            }
            printed = {
                "printed_question_id": f"PRINTED-TEST-{suffix}",
                "question_number": "1",
                "sequence_in_theme": 1,
                "stem": atomic["stem"],
                "options": [],
                "response_requirements": atomic["response_requirements"],
                "chemical_expressions": [],
                "shared_material_refs": [],
                "visual_object_refs": [],
                "atomic_parts": [atomic],
                "evidence_refs": refs,
            }
            themes = [
                {
                    "theme_big_question_id": f"THEME-TEST-{suffix}",
                    "theme_number": "一",
                    "title": "合成协调层主题",
                    "context": "只验证页面分片与候选合同。",
                    "sequence_in_paper": 1,
                    "fragment_position": "complete",
                    "shared_materials": [],
                    "visual_objects": [],
                    "dependency_edges": [],
                    "printed_questions": [printed],
                    "evidence_refs": refs,
                }
            ]
        raw_fragment = {
            "schema_version": "shchem.intake-batch-visual-fragment.v2",
            "shard_id": shard.shard_id,
            "source_role": shard.source_role,
            "input_mode": "direct_original_or_rendered_page_pixels",
            "source_text_layer_used": False,
            "fallback_used": False,
            "evidence": evidence,
            "paper_identity": paper_identity,
            "theme_fragments": themes,
            "answer_candidates": [],
            "warnings": [],
        }
        fragments.append(core._validate_fragment(raw_fragment, request=shard))
    assert hierarchy_emitted
    return core._build_candidate(
        batch_id=batch_id,
        batch_sha256=batch_sha,
        source_records=source_records,
        requests=shards,
        fragments=fragments,
    )


def test_native_docx_inspection_reads_supported_math_but_excludes_drawing_text() -> None:
    source = _source(
        role="handout",
        order=1,
        filename="任意名称.docx",
        content=_docx_bytes(with_visual_objects=True),
    )
    inspection = bridge.inspect_native_docx(source)

    assert "可编辑题干" in inspection.native_text
    assert "DRAWING_TEXT_MUST_NOT_BE_NATIVE" not in inspection.native_text
    # This legacy-named sentinel is inside valid, editable m:r/m:t. The new
    # native reader preserves it; pixels and OLE contents remain unsupported.
    assert "OMML_TEXT_MUST_NOT_BE_NATIVE" in inspection.native_text
    assert inspection.import_state == "hybrid_visual_required"
    assert inspection.quick_import_eligible is False
    assert inspection.page_binding_status == "pending_rendered_page_evidence"
    assert inspection.visual_object_refs
    assert "omml_equation" not in inspection.features
    assert sum(block["native_math_count"] for block in inspection.native_blocks) == 1
    assert "ole_object_reference" in inspection.features


def test_native_docx_routes_visual_markers_in_header_and_rejects_entities() -> None:
    from io import BytesIO

    document = """<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>正文</w:t></w:r></w:p></w:body></w:document>""".encode()
    header = """<?xml version="1.0"?><w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><w:p><w:r><m:oMath><m:r><m:t>不要进入文字</m:t></m:r></m:oMath></w:r></w:p></w:hdr>""".encode()
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        package.writestr("word/document.xml", document)
        package.writestr("word/header1.xml", header)
    source = _source(
        role="handout",
        order=1,
        filename="header-equation.docx",
        content=output.getvalue(),
    )
    inspection = bridge.inspect_native_docx(source)
    assert inspection.import_state == "hybrid_visual_required"
    assert "不要进入文字" not in inspection.native_text
    assert "omml_equation" in inspection.features

    entity_output = BytesIO()
    entity_document = b"""<!DOCTYPE w:document [<!ENTITY x "expanded">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>"""
    with zipfile.ZipFile(entity_output, "w") as package:
        package.writestr("word/document.xml", entity_document)
    entity_source = _source(
        role="handout",
        order=1,
        filename="entity.docx",
        content=entity_output.getvalue(),
    )
    with pytest.raises(bridge.DesktopImportBridgeError, match="DOCX"):
        bridge.inspect_native_docx(entity_source)


def test_explicit_role_and_order_are_independent_of_filename() -> None:
    question = _source(
        role="question",
        order=1,
        filename="scan-A.png",
        content=_png_bytes(),
    )
    answer = _source(
        role="answer",
        order=1,
        filename="notes-without-role-marker.png",
        content=_png_bytes((80, 20, 20)),
    )
    plan = bridge.DesktopImportCoordinatorV2().plan(
        bridge.DesktopImportRequest.from_sources(
            question_files=[question], answer_files=[answer]
        )
    )
    assert [item["role"] for item in plan.sources] == ["question", "answer"]
    assert [item["order_index"] for item in plan.sources] == [1, 1]
    assert plan.visual_required is True
    assert plan.visual_source_ids == (
        question.effective_source_file_id,
        answer.effective_source_file_id,
    )


def test_mixed_batch_keeps_pure_native_docx_out_of_visual_lane() -> None:
    """A visual question page must not make an unrelated native page visual."""

    native_question = _source(
        role="question",
        order=1,
        filename="native-question.docx",
        content=_docx_bytes(),
        source_id="SRC-NATIVE-Q",
    )
    visual_question = _source(
        role="question",
        order=2,
        filename="visual-question.png",
        content=_png_bytes((12, 34, 56)),
        source_id="SRC-VISUAL-Q",
    )
    visual_answer = _source(
        role="answer",
        order=1,
        filename="visual-answer.png",
        content=_png_bytes((65, 43, 21)),
        source_id="SRC-VISUAL-A",
    )
    request = bridge.DesktopImportRequest.from_sources(
        question_files=[native_question, visual_question],
        answer_files=[visual_answer],
        source_type="混合批次",
    )

    calls: list[tuple[str, ...]] = []

    def visual_runner(
        shards: Sequence[core.VisualShardRequest], *, batch_id: str
    ) -> Mapping[str, Any]:
        calls.append(
            tuple(page.source_file_id for shard in shards for page in shard.pages)
        )
        assert batch_id
        return _strict_candidate_from_shards(shards, batch_id=batch_id)

    coordinator = bridge.DesktopImportCoordinatorV2(
        visual_runner=visual_runner,
    )
    plan = coordinator.plan(request)
    assert plan.native_quick_count == 1
    assert plan.visual_source_ids == ("SRC-VISUAL-Q", "SRC-VISUAL-A")
    assert plan.visual_context_source_ids == ()

    result = coordinator.process(request, visual_confirmation=True)

    assert result.visual_status == "completed"
    assert calls == [("SRC-VISUAL-Q", "SRC-VISUAL-A")]
    assert [item["source_file_id"] for item in result.visual_queue] == [
        "SRC-VISUAL-Q",
        "SRC-VISUAL-A",
    ]
    assert result.native_records[0]["source_file_id"] == "SRC-NATIVE-Q"
    assert result.native_records[0]["inventory_lane"] == "native_text_submitted"


def test_answer_only_visual_can_keep_native_question_as_context() -> None:
    """An answer-only visual page may retain the native question for context."""

    native_question = _source(
        role="question",
        order=1,
        filename="native-question.docx",
        content=_docx_bytes(),
        source_id="SRC-NATIVE-Q-CONTEXT",
    )
    visual_answer = _source(
        role="answer",
        order=1,
        filename="answer-only.png",
        content=_png_bytes((101, 102, 103)),
        source_id="SRC-ANSWER-ONLY",
    )
    request = bridge.DesktopImportRequest.from_sources(
        question_files=[native_question],
        answer_files=[visual_answer],
        source_type="答案补全批次",
    )

    calls: list[tuple[str, ...]] = []

    def visual_runner(
        shards: Sequence[core.VisualShardRequest], *, batch_id: str
    ) -> Mapping[str, Any]:
        calls.append(
            tuple(page.source_file_id for shard in shards for page in shard.pages)
        )
        assert batch_id
        return _strict_candidate_from_shards(shards, batch_id=batch_id)

    coordinator = bridge.DesktopImportCoordinatorV2(
        visual_runner=visual_runner,
        renderer=_FakeRenderer(),
    )
    plan = coordinator.plan(request)
    assert plan.native_quick_count == 1
    assert plan.visual_source_ids == ("SRC-ANSWER-ONLY",)
    assert plan.visual_context_source_ids == ("SRC-NATIVE-Q-CONTEXT",)

    result = coordinator.process(request, visual_confirmation=True)

    assert result.visual_status == "completed"
    # The native question is context only; the answer page is the sole visual
    # queue item and therefore the only page that needs visual completion.
    assert calls == [("SRC-NATIVE-Q-CONTEXT", "SRC-ANSWER-ONLY")]
    assert [item["source_file_id"] for item in result.visual_queue] == [
        "SRC-ANSWER-ONLY"
    ]
    assert result.native_records[0]["inventory_lane"] == "native_text_submitted"


def test_native_import_port_receives_only_pure_text_documents() -> None:
    pure_text = _source(
        role="question",
        order=1,
        filename="纯文字.docx",
        content=_docx_bytes(),
        source_id="SRC-NATIVE-PURE",
    )
    hybrid = _source(
        role="handout",
        order=1,
        filename="含公式.docx",
        content=_docx_bytes(with_visual_objects=True),
        source_id="SRC-HYBRID-VISUAL",
    )
    request = bridge.DesktopImportRequest.from_sources(
        question_files=[pure_text], handout_files=[hybrid]
    )
    native_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def native_importer(
        sources: Sequence[bridge.DesktopSourceFile],
        inspections: Sequence[bridge.NativeDocxInspection],
    ) -> Mapping[str, Any]:
        native_calls.append(
            (
                tuple(item.effective_source_file_id for item in sources),
                tuple(item.source_file_id for item in inspections),
            )
        )
        return {"inventory_lane": "personal_native_inventory"}

    def visual_runner(
        shards: Sequence[core.VisualShardRequest], *, batch_id: str
    ) -> Mapping[str, Any]:
        assert batch_id
        assert [page.source_file_id for shard in shards for page in shard.pages] == [
            "SRC-HYBRID-VISUAL"
        ]
        return _strict_candidate_from_shards(shards, batch_id=batch_id)

    result = bridge.DesktopImportCoordinatorV2(
        native_importer=native_importer,
        visual_runner=visual_runner,
    ).process(request, visual_confirmation=True)

    assert native_calls == [(("SRC-NATIVE-PURE",), ("SRC-NATIVE-PURE",))]
    assert result.native_records[1]["inventory_lane"] == "visual_completion_queue"


def test_native_only_batch_is_complete_without_visual_provider() -> None:
    source = _source(
        role="question",
        order=1,
        filename="personal-note.docx",
        content=_docx_bytes(),
        source_id="SRC-NATIVE-ONLY",
    )
    calls: list[str] = []

    def native_importer(
        sources: Sequence[bridge.DesktopSourceFile],
        inspections: Sequence[bridge.NativeDocxInspection],
    ) -> Mapping[str, Any]:
        calls.extend(item.effective_source_file_id for item in sources)
        assert all(item.quick_import_eligible for item in inspections)
        return {"stored": True}

    result = bridge.DesktopImportCoordinatorV2(native_importer=native_importer).process(
        bridge.DesktopImportRequest.from_sources(question_files=[source])
    )

    assert result.visual_status == "not_required"
    assert calls == ["SRC-NATIVE-ONLY"]
    assert result.native_import_receipt == {
        "stored": True,
        "candidate_only": True,
        "central_question_bank_write": False,
    }


def test_native_import_cannot_claim_central_write() -> None:
    source = _source(
        role="question",
        order=1,
        filename="native.docx",
        content=_docx_bytes(),
    )

    def native_importer(
        _sources: Sequence[bridge.DesktopSourceFile],
        _inspections: Sequence[bridge.NativeDocxInspection],
    ) -> Mapping[str, Any]:
        return {"central_question_bank_write": True}

    with pytest.raises(bridge.DesktopImportBridgeError, match="中央题库"):
        bridge.DesktopImportCoordinatorV2(native_importer=native_importer).process(
            bridge.DesktopImportRequest.from_sources(question_files=[source])
        )


def test_visual_runner_secret_fields_are_rejected() -> None:
    source = _source(
        role="question", order=1, filename="page.png", content=_png_bytes()
    )

    def visual_runner(
        shards: Sequence[core.VisualShardRequest], *, batch_id: str
    ) -> Mapping[str, Any]:
        assert batch_id
        candidate = _strict_candidate_from_shards(shards, batch_id=batch_id)
        candidate["api_key"] = "should-never-cross-boundary"
        return candidate

    result = bridge.DesktopImportCoordinatorV2(
        visual_runner=visual_runner,
    ).process(
        bridge.DesktopImportRequest.from_sources(question_files=[source]),
        visual_confirmation=True,
    )
    assert result.visual_status == "failed"
    assert result.visual_candidate is None
    assert result.blockers[0]["code"] == "secret_material_detected"


def test_process_can_pause_visual_egress_until_teacher_confirmation() -> None:
    source = _source(
        role="question", order=1, filename="page.png", content=_png_bytes()
    )
    calls: list[int] = []

    def visual_runner(
        shards: Sequence[core.VisualShardRequest], *, batch_id: str
    ) -> Mapping[str, Any]:
        calls.append(1)
        assert batch_id
        return _strict_candidate_from_shards(shards, batch_id=batch_id)

    coordinator = bridge.DesktopImportCoordinatorV2(visual_runner=visual_runner)
    paused = coordinator.process(
        bridge.DesktopImportRequest.from_sources(question_files=[source]),
        visual_confirmation=False,
    )
    assert paused.visual_status == "awaiting_teacher_confirmation"
    assert paused.visual_candidate is None
    assert calls == []
    with pytest.raises(bridge.DesktopImportBridgeError, match="确认格式"):
        coordinator.process(
            bridge.DesktopImportRequest.from_sources(question_files=[source]),
            visual_confirmation="yes",  # type: ignore[arg-type]
        )


def test_visual_confirmation_omission_is_fail_closed() -> None:
    source = _source(
        role="question", order=1, filename="page.png", content=_png_bytes()
    )
    calls: list[int] = []

    def visual_runner(
        shards: Sequence[core.VisualShardRequest], *, batch_id: str
    ) -> Mapping[str, Any]:
        calls.append(1)
        assert batch_id
        return _strict_candidate_from_shards(shards, batch_id=batch_id)

    result = bridge.DesktopImportCoordinatorV2(visual_runner=visual_runner).process(
        bridge.DesktopImportRequest.from_sources(question_files=[source])
    )
    assert result.visual_status == "awaiting_teacher_confirmation"
    assert result.blockers[0]["code"] == "teacher_confirmation_required"
    assert calls == []


def test_callable_visual_runner_receives_page_shards_not_raw_docx() -> None:
    source = _source(
        role="question",
        order=1,
        filename="formula.docx",
        content=_docx_bytes(with_visual_objects=True),
    )
    seen: list[Any] = []

    def visual_runner(
        shards: Sequence[core.VisualShardRequest], *, batch_id: str
    ) -> Mapping[str, Any]:
        seen.extend(shards)
        assert batch_id
        assert all(isinstance(shard, core.VisualShardRequest) for shard in shards)
        assert all(
            not hasattr(shard, "content")
            and all(not hasattr(page, "content") for page in shard.pages)
            for shard in shards
        )
        return _strict_candidate_from_shards(shards, batch_id=batch_id)

    result = bridge.DesktopImportCoordinatorV2(
        visual_runner=visual_runner,
        renderer=_FakeRenderer(),
    ).process(
        bridge.DesktopImportRequest.from_sources(question_files=[source]),
        visual_confirmation=True,
    )
    assert result.visual_status == "completed"
    assert seen and seen[0].pages[0].source_file_id == source.effective_source_file_id


def test_invalid_declared_image_is_rejected_during_planning() -> None:
    source = bridge.DesktopSourceFile(
        role="question",
        order_index=1,
        filename="not-an-image.png",
        mime_type="image/png",
        content=b"not-a-png",
    )
    with pytest.raises(bridge.DesktopImportBridgeError, match="页面像素"):
        bridge.DesktopImportCoordinatorV2().plan(
            bridge.DesktopImportRequest.from_sources(question_files=[source])
        )


def test_non_contiguous_explicit_order_fails_closed() -> None:
    first = _source(role="question", order=1, filename="a.png", content=_png_bytes())
    skipped = _source(role="question", order=3, filename="b.png", content=_png_bytes())
    with pytest.raises(bridge.DesktopImportBridgeError, match="连续编号"):
        bridge.DesktopImportCoordinatorV2().plan(
            bridge.DesktopImportRequest.from_sources(question_files=[first, skipped])
        )
    other_group = _source(
        role="answer",
        order=1,
        filename="answer.png",
        content=_png_bytes((2, 3, 4)),
        group="paper-b",
    )
    with pytest.raises(bridge.DesktopImportBridgeError, match="同一资料分组"):
        bridge.DesktopImportCoordinatorV2().plan(
            bridge.DesktopImportRequest.from_sources(
                question_files=[first], answer_files=[other_group]
            )
        )


def test_pixel_archive_keeps_exact_source_bytes_and_queues_without_provider(
    tmp_path: Path,
) -> None:
    raw = _png_bytes((1, 2, 3))
    source = _source(role="question", order=1, filename="page.png", content=raw)
    result = bridge.DesktopImportCoordinatorV2(
        archive_root=tmp_path / "personal-state"
    ).process(bridge.DesktopImportRequest.from_sources(question_files=[source]))

    assert result.visual_status == "awaiting_visual_provider"
    assert result.visual_candidate is None
    assert result.visual_queue[0]["status"] == "pending"
    assert result.pixel_pages[0]["pixel_bytes_preserved"] is True
    assert result.pixel_pages[0]["rendered_from_source"] is False
    page_path = tmp_path / "personal-state" / result.pixel_pages[0]["relative_path"]
    assert page_path.read_bytes() == raw
    assert Path(result.manifest_path or "").is_file()
    manifest_text = Path(result.manifest_path or "").read_text(encoding="utf-8")
    assert "central_question_bank_write" in manifest_text
    assert "path_hint" not in manifest_text


def test_pending_manifest_can_resume_after_provider_is_added(tmp_path: Path) -> None:
    """A queued batch can be retried without deleting its immutable receipt."""

    fixture = _fixture()
    questions, answers = _fixture_sources()
    native_question = _source(
        role="question",
        order=1,
        filename="native-context-free.docx",
        content=_docx_bytes(),
        group="fixture-paper",
        source_id="SRC-NATIVE-RESUME",
    )
    questions = [
        native_question,
        *(replace(source, order_index=source.order_index + 1) for source in questions),
    ]
    native_calls: list[tuple[str, ...]] = []

    def native_importer(
        sources: Sequence[bridge.DesktopSourceFile],
        _inspections: Sequence[bridge.NativeDocxInspection],
    ) -> Mapping[str, Any]:
        native_calls.append(
            tuple(source.effective_source_file_id for source in sources)
        )
        return {"personal_inventory_receipt": "fixture-native-once"}

    request = bridge.DesktopImportRequest.from_sources(
        question_files=questions,
        answer_files=answers,
        source_type="可恢复视觉批次",
        batch_id="DESKTOPBATCH-resume-fixture",
    )
    archive_root = tmp_path / "personal-state"
    first = bridge.DesktopImportCoordinatorV2(
        archive_root=archive_root,
        native_importer=native_importer,
    ).process(request)
    assert first.visual_status == "awaiting_visual_provider"
    assert native_calls == [("SRC-NATIVE-RESUME",)]

    path = SOURCE_TEST_SUPPORT_ROOT / "tests" / "test_intake_batches_v2.py"
    spec = importlib.util.spec_from_file_location("resume_fixture_provider_v2", path)
    assert spec is not None and spec.loader is not None
    provider_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = provider_module
    spec.loader.exec_module(provider_module)
    provider = provider_module.SyntheticStrictVisualProvider(fixture)
    resumed = bridge.DesktopImportCoordinatorV2(
        visual_provider=provider,
        renderer=_FakeRenderer(),
        native_importer=native_importer,
        archive_root=archive_root,
        max_pages_per_shard=2,
    ).process(request, visual_confirmation=True)
    assert resumed.visual_status == "completed"
    assert resumed.visual_candidate_sha256
    assert native_calls == [("SRC-NATIVE-RESUME",)]
    manifest = json.loads(Path(resumed.manifest_path or "").read_text(encoding="utf-8"))
    assert manifest["visual_status"] == "completed"


def test_existing_98x196_runner_summary_stays_candidate_only() -> None:
    class Receipt:
        def as_dict(self) -> dict[str, Any]:
            return {
                "inventory_complete_98x196": True,
                "documents_total": 196,
                "documents_completed": 196,
                "documents_failed": 0,
                "quick_import_candidates": 121,
                "visual_completion_candidates": 2810,
            }

    calls: list[Path] = []

    def runner(root: Path, **_: Any) -> Receipt:
        calls.append(root)
        return Receipt()

    result = bridge.DesktopImportCoordinatorV2.run_existing_native_corpus(
        runner, Path("expanded")
    )
    assert calls == [Path("expanded")]
    assert result["inventory_complete_98x196"] is True
    assert result["documents_completed"] == 196
    assert result["page_visual_completion_complete"] is False
    assert result["candidate_only"] is True
    assert result["central_question_bank_write"] is False


def test_existing_native_runner_accepts_positional_only_callbacks() -> None:
    seen: dict[str, Any] = {}

    def runner(root: Path, progress_callback: Any = None, should_cancel: Any = None, /):
        seen["root"] = root
        seen["progress"] = progress_callback
        seen["cancel"] = should_cancel
        return {"inventory_complete_98x196": False}

    progress = lambda _value: None
    cancel = lambda: False
    result = bridge.DesktopImportCoordinatorV2.run_existing_native_corpus(
        runner,
        "expanded",
        progress_callback=progress,
        should_cancel=cancel,
    )
    assert seen == {"root": Path("expanded"), "progress": progress, "cancel": cancel}
    assert result["inventory_complete_98x196"] is False


def test_full_visual_core_can_be_called_from_desktop_coordinator(
    tmp_path: Path,
) -> None:
    # Reuse the independent deterministic provider from the sibling v2 E2E
    # suite; it observes request pages only and never receives source text.
    path = SOURCE_TEST_SUPPORT_ROOT / "tests" / "test_intake_batches_v2.py"
    spec = importlib.util.spec_from_file_location("fixture_provider_v2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fixture = _fixture()
    questions, answers = _fixture_sources()
    provider = module.SyntheticStrictVisualProvider(fixture)
    renderer = _FakeRenderer()
    coordinator = bridge.DesktopImportCoordinatorV2(
        visual_provider=provider,
        renderer=renderer,
        archive_root=tmp_path / "personal-state",
        max_pages_per_shard=2,
    )
    result = coordinator.process(
        bridge.DesktopImportRequest.from_sources(
            question_files=questions,
            answer_files=answers,
            source_type="合成视觉批次",
        ),
        visual_confirmation=True,
    )

    assert result.visual_status == "completed"
    assert result.visual_candidate is not None
    assert result.visual_candidate["candidate_status"] == "candidate_only"
    assert result.visual_candidate["central_question_bank_write"] is False
    assert result.visual_candidate_sha256
    assert result.visual_revision_token
    assert result.pixel_pages
    assert renderer.calls == []  # all fixture inputs are already page images
    assert len(provider.requests) == 3  # two question shards plus one answer shard
    request_payload = provider.requests[0].transport_payload()
    assert request_payload["input_contract"]["source_text_layer_supplied"] is False
    assert request_payload["input_contract"]["fallback_allowed"] is False

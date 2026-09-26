from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
import yaml
from PIL import Image

from integrations.deeptutor_shchem_v1.answer_diagrams import (
    answer_diagram_dimensions,
    answer_diagram_png,
    answer_diagram_svg,
)
from integrations.deeptutor_shchem_v1.datong_answer_bindings import (
    QUESTION_BINDINGS,
    SHARED_CONTEXT_BINDINGS,
)
from integrations.deeptutor_shchem_v1.paper_format_presets import (
    PaperFormatContractError,
    _contains_forbidden_student_keys,
    _normalize_atomic_content,
    _normalize_supplement,
)
from integrations.deeptutor_shchem_v1.supplemental_answers import (
    all_supplemental_answers,
    answer_for_scan,
    validate_supplemental_answer,
)


def _scan(answer):
    question = answer["node_id"].split("-Q", 1)[1].split("-", 1)[0]
    bindings = QUESTION_BINDINGS.get(
        answer["node_id"],
        ((f"DT2025-H1-Q{question}-E1", answer["source_question_sha256"]),),
    )
    return {
        "node_id": answer["node_id"],
        "paper_id": "W1-DT2025-H1-MID",
        "reference_answer": {"availability": "absent"},
        "evidence_descriptors": [
            {
                "crop_id": crop_id,
                "sha256": sha,
                "evidence_role": "question",
            }
            for crop_id, sha in bindings
        ]
        + [
            {"crop_id": crop_id, "sha256": sha, "evidence_role": "shared_material"}
            for crop_id, sha in SHARED_CONTEXT_BINDINGS.get(answer["node_id"], ())
        ],
    }


@pytest.mark.parametrize(
    "answer", all_supplemental_answers(), ids=lambda x: x["answer_id"]
)
def test_solutions_are_bound_to_exact_question_and_do_not_mutate_source(answer):
    scan = _scan(answer)
    before = deepcopy(scan)
    assert answer_for_scan(scan) == answer
    assert scan == before
    assert answer["source_kind"] == "assistant_supplement"
    assert answer["independently_verified"] is False
    assert answer["explanation_zh"]


@pytest.mark.parametrize("change", ["hash", "id", "role", "paper", "extra"])
def test_mismatched_evidence_cannot_receive_answer(change):
    scan = _scan(all_supplemental_answers()[0])
    if change == "hash":
        scan["evidence_descriptors"][0]["sha256"] = "0" * 64
    elif change == "id":
        scan["evidence_descriptors"][0]["crop_id"] = "DIFFERENT"
    elif change == "role":
        scan["evidence_descriptors"][0]["evidence_role"] = "answer"
    elif change == "paper":
        scan["paper_id"] = "DIFFERENT"
    else:
        scan["evidence_descriptors"].append(
            {"evidence_role": "question", "crop_id": "EXTRA", "sha256": "0" * 64}
        )
    with pytest.raises(ValueError, match="题图版本"):
        answer_for_scan(scan)


@pytest.mark.parametrize(
    "state", ["present_part_aligned", "present_unaligned", "unknown"]
)
def test_source_answers_are_never_replaced(state):
    scan = _scan(all_supplemental_answers()[0])
    scan["reference_answer"]["availability"] = state
    assert answer_for_scan(scan) is None


def test_other_questions_have_no_fabricated_answer():
    assert answer_for_scan({"node_id": "other"}) is None
    scan = _scan(all_supplemental_answers()[0])
    scan["node_id"] = "W1-DT2025-H1-MID-AP-DT2025-H1-Q42-P01"
    assert answer_for_scan(scan) is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("text_zh", "D"),
        ("source_kind", "official"),
        ("diagram_key", "file:///secret.svg"),
        ("independently_verified", True),
    ],
)
def test_edited_or_mislabelled_supplements_fail_closed(key, value):
    answer = all_supplemental_answers()[0]
    answer[key] = value
    with pytest.raises(ValueError):
        validate_supplemental_answer(answer)


def test_first_answer_uses_room_temperature_and_dry_conditions():
    answer = all_supplemental_answers()[0]
    assert answer["text_zh"] == "C。"
    assert "常温、干燥" in answer["explanation_zh"]
    assert "不反应" in answer["explanation_zh"]


def test_atomic_contract_keeps_source_absence_and_teacher_only_supplement():
    supplement = all_supplemental_answers()[0]
    content = {
        "question_blocks": [
            {
                "block_type": "paragraph",
                "text_zh": "合成测试题面",
                "asset_ref": None,
                "alt_text_zh": None,
            }
        ],
        "score": 2,
        "answer_space_lines": 1,
        "reference_answer": {
            "status": "absent",
            "text_zh": None,
            "authority_label": "none",
            "independently_verified": False,
            "source_label_zh": None,
        },
        "explanation_zh": supplement["explanation_zh"],
        "explanation_label": "ai_candidate",
        "pitfalls_zh": [],
        "source_label_zh": "本地测试",
        "supplemental_answer": supplement,
    }
    normalized = _normalize_atomic_content(content, 1)
    assert normalized["reference_answer"]["status"] == "absent"
    assert normalized["supplemental_answer"] == supplement
    assert _contains_forbidden_student_keys({"supplemental_answer": supplement})
    content["reference_answer"]["status"] = "aligned"
    with pytest.raises(PaperFormatContractError, match="不能替代"):
        _normalize_supplement(content)


@pytest.mark.parametrize(
    "key,electrons",
    [("chlorine_atom_shells", 17), ("chloride_lewis", 8), ("oxygen_atom_shells", 8)],
)
def test_editable_diagrams_have_exact_electrons_and_raster_export(key, electrons):
    svg = ET.fromstring(answer_diagram_svg(key))
    circles = svg.findall("{http://www.w3.org/2000/svg}circle")
    texts = [node.text for node in svg.findall("{http://www.w3.org/2000/svg}text")]
    if key == "chloride_lewis":
        assert sum(float(circle.attrib["r"]) < 4 for circle in circles) == electrons
        assert "−" in texts and "Cl" in texts
        assert len(svg.findall("{http://www.w3.org/2000/svg}polyline")) == 2
    elif key == "chlorine_atom_shells":
        assert texts == ["+17", "2", "8", "7"]
        assert (
            len(circles) == 1
            and len(svg.findall("{http://www.w3.org/2000/svg}path")) == 6
        )
    else:
        assert texts == ["+8", "2", "6"]
        assert (
            len(circles) == 1
            and len(svg.findall("{http://www.w3.org/2000/svg}path")) == 4
        )
    with Image.open(io.BytesIO(answer_diagram_png(key))) as image:
        assert image.size == tuple(size * 3 for size in answer_diagram_dimensions(key))


def test_diagram_keys_are_not_paths_or_arbitrary_svg():
    with pytest.raises(ValueError):
        answer_diagram_svg("https://example.com/anything.svg")


def test_all_56_units_have_answers_with_stable_first_batch():
    answers = all_supplemental_answers()
    assert len(answers) == 56
    assert len({answer["node_id"] for answer in answers}) == 56
    assert sum(answer["diagram_key"] is not None for answer in answers) == 4
    assert all(answer["revision_id"].endswith("r1") for answer in answers[:15])
    assert {answer["node_id"] for answer in answers[15:]} == set(QUESTION_BINDINGS)


def test_redox_single_bridge_has_one_fe_to_cl_arrow_and_six_electrons():
    svg = ET.fromstring(answer_diagram_svg("ferrate_single_electron_bridge"))
    lines = svg.findall("{http://www.w3.org/2000/svg}polyline")
    assert len(lines) == 2  # bridge and its single arrowhead, not a double bridge
    points = [
        tuple(map(float, point.split(",")))
        for point in lines[0].attrib["points"].split()
    ]
    assert points[0][0] == 217 and points[-1][0] == 68
    texts = [node.text for node in svg.findall("{http://www.w3.org/2000/svg}text")]
    assert "6e⁻" in texts and "2Fe(OH)₃" in texts and "3KClO" in texts
    with Image.open(
        io.BytesIO(answer_diagram_png("ferrate_single_electron_bridge"))
    ) as image:
        assert image.size == (2880, 645)


@pytest.mark.parametrize("suffix", ["Q24-P01-S02", "Q26-P01", "Q33-P01", "Q38-P01-S02"])
def test_altered_shared_context_cannot_receive_answer(suffix):
    answer = next(
        a for a in all_supplemental_answers() if a["node_id"].endswith(suffix)
    )
    scan = _scan(answer)
    scan["evidence_descriptors"][-1]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="共享材料"):
        answer_for_scan(scan)


def test_source_aware_answers_keep_units_conditions_and_element_variables():
    answers = {
        a["node_id"].split("-AP-DT2025-H1-", 1)[1]: a
        for a in all_supplemental_answers()
    }
    assert answers["Q29-P01"]["text_zh"].startswith("B")
    assert answers["Q33-P01"]["diagram_key"] == "oxygen_atom_shells"
    assert "HClO₄" in answers["Q33-P03-S02"]["text_zh"]
    assert answers["Q38-P01-S02"]["text_zh"] == "21.0 mL。"
    assert "500 mL" in answers["Q38-P01-S02"]["explanation_zh"]
    assert "Al₂Cl₆" in answers["Q41-P01"]["text_zh"]


def test_openapi_accepts_supplements_and_rejects_false_authority_or_external_diagrams():
    contract = Path(__file__).resolve().parents[1] / "contracts/gateway_openapi_v1.yaml"
    document = yaml.safe_load(contract.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(
        document["components"]["schemas"]["SupplementalAnswer"]
    )
    for answer in all_supplemental_answers():
        assert list(validator.iter_errors(answer)) == []
    for field, value in (
        ("source_kind", "official"),
        ("label_zh", "官方答案"),
        ("independently_verified", True),
        ("diagram_key", "https://example.com/diagram.svg"),
        ("private_source_path", "private"),
    ):
        invalid = all_supplemental_answers()[0]
        invalid[field] = value
        assert list(validator.iter_errors(invalid))

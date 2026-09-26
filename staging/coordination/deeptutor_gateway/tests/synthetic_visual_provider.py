"""Deterministic synthetic intake provider; no network or private source fixture.

Extracted from the original local intake prototype support to make the public
personal-visual service regression suite self-contained.
"""
from __future__ import annotations
from copy import deepcopy
from typing import Any

DIFFICULTY_FACTOR_IDS = (
    "information_transformations",
    "reasoning_chain_steps",
    "knowledge_module_span",
    "representation_switches",
    "calculation_load",
    "experiment_load",
    "openness",
    "unfamiliarity",
    "language_density",
    "prior_dependency",
)

def _chem(
    raw: str,
    normalized: str | None,
    kind: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    return {
        "raw": raw,
        "normalized": normalized,
        "kind": kind,
        "status": "observed",
        "evidence_refs": evidence_refs,
    }

def _difficulty(evidence_refs: list[str]) -> dict[str, Any]:
    return {
        "cognitive_prelabel": "D2",
        "status": "candidate_pending_teacher",
        "rationale": "合成页面像素显示需要读取装置并完成一步因果推理。",
        "factors": [
            {
                "dimension_id": factor_id,
                "level": 1 if factor_id != "reasoning_chain_steps" else 2,
                "rationale": f"合成 fixture 对 {factor_id} 的页面可见候选证据。",
                "evidence_refs": evidence_refs,
            }
            for factor_id in DIFFICULTY_FACTOR_IDS
        ],
        "measured_difficulty": None,
        "human_verified": False,
        "student_data_used": False,
        "evidence_refs": evidence_refs,
    }

def _printed_question(
    fixture: dict[str, Any], index: int, evidence_refs: list[str]
) -> dict[str, Any]:
    source = fixture["printed_questions"][index]
    if index == 0:
        chemistry = [
            _chem(
                "Cu - 2e⁻ → Cu²⁺",
                r"\ce{Cu - 2e^- -> Cu^2+}",
                "equation",
                evidence_refs,
            )
        ]
        options: list[dict[str, Any]] = []
    else:
        chemistry = [_chem("Cu、Ag⁺", r"\ce{Cu, Ag+}", "formula", evidence_refs)]
        options = [
            {
                "label": "A",
                "content": "电子由铜电极流向银电极",
                "chemical_expressions": [],
                "visual_object_refs": [fixture["theme"]["visual_object_id"]],
                "evidence_refs": evidence_refs,
            },
            {
                "label": "B",
                "content": "电子由银电极流向铜电极",
                "chemical_expressions": [],
                "visual_object_refs": [fixture["theme"]["visual_object_id"]],
                "evidence_refs": evidence_refs,
            },
        ]
    atomic = {
        "atomic_part_id": source["atomic_part_id"],
        "part_label": source["part_label"],
        "sequence_in_printed": 1,
        "stem": source["stem"],
        "options": deepcopy(options),
        "response_requirements": source["response_requirements"],
        "chemical_expressions": deepcopy(chemistry),
        "visual_object_refs": [fixture["theme"]["visual_object_id"]],
        "curriculum": {
            "textbook_edition": "沪教版合成测试教材",
            "primary_chapter": "选择性必修1·电化学",
            "secondary_chapters": ["氧化还原反应"],
            "mapping_status": "candidate_pending_teacher",
            "rationale": "依据合成题面中的电极、电子与离子符号作候选映射。",
            "evidence_refs": evidence_refs,
        },
        "classification": {
            "item_type": source["item_type"],
            "primary_knowledge_K": source["primary_K"],
            "supporting_knowledge_K": ["K-redox"],
            "ability_A": source["A"],
            "context_C": source["C"],
            "response_R": source["R"],
            "representation_RP": source["RP"],
            "evidence_refs": evidence_refs,
        },
        "cognitive_difficulty": _difficulty(evidence_refs),
        "evidence_refs": evidence_refs,
    }
    return {
        "printed_question_id": source["printed_question_id"],
        "question_number": source["question_number"],
        "sequence_in_theme": source["sequence_in_theme"],
        "stem": source["stem"],
        "options": deepcopy(options),
        "response_requirements": source["response_requirements"],
        "chemical_expressions": deepcopy(chemistry),
        "shared_material_refs": [fixture["theme"]["shared_material_id"]],
        "visual_object_refs": [fixture["theme"]["visual_object_id"]],
        "atomic_parts": [atomic],
        "evidence_refs": evidence_refs,
    }

class SyntheticStrictVisualProvider:
    """Deterministic synthetic vision result; it receives pixels, never source text."""

    def __init__(
        self,
        fixture: dict[str, Any],
        *,
        misalign_second_answer: bool = False,
        contract_override: tuple[str, Any] | None = None,
        add_forbidden_output_key: bool = False,
    ) -> None:
        self.fixture = fixture
        self.misalign_second_answer = misalign_second_answer
        self.contract_override = contract_override
        self.add_forbidden_output_key = add_forbidden_output_key
        self.requests: list[Any] = []
        self.payloads: list[dict[str, Any]] = []

    @staticmethod
    def _evidence(request: Any) -> list[dict[str, Any]]:
        return [
            {
                "evidence_id": f"EV-{page.source_file_id}",
                "source_file_id": page.source_file_id,
                "source_role": request.source_role,
                "page_number": page.page_number,
                "page_sha256": page.page_sha256,
                "bbox": {"x": 0.05, "y": 0.06, "width": 0.9, "height": 0.88},
            }
            for page in request.pages
        ]

    def _paper_identity(self, evidence_ref: str) -> dict[str, Any]:
        identity = self.fixture["paper_identity"]
        return {
            "title": identity["title"],
            "source_year": identity["source_year"],
            "source_region_or_school": identity["source_region_or_school"],
            "paper_type": identity["paper_type"],
            "evidence_refs": [evidence_ref],
        }

    def _question_fragment(
        self, request: Any, evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        refs = [item["evidence_id"] for item in evidence]
        source_ids = {page.source_file_id for page in request.pages}
        theme = self.fixture["theme"]
        if "SRC-Q-A" in source_ids:
            position = "start"
            shared_materials = [
                {
                    "shared_material_id": theme["shared_material_id"],
                    "material_type": "synthetic_context",
                    "content": theme["shared_material_content"],
                    "chemical_expressions": [
                        _chem("Cu、Ag⁺", r"\ce{Cu, Ag+}", "formula", [refs[0]])
                    ],
                    "visual_object_refs": [theme["visual_object_id"]],
                    "evidence_refs": [refs[0]],
                }
            ]
            visual_objects = [
                {
                    "visual_object_id": theme["visual_object_id"],
                    "kind": "apparatus",
                    "description": "合成电化学装置图，含铜电极和银离子溶液。",
                    "structured_representation": {
                        "synthetic_only": True,
                        "electrodes": ["Cu", "Ag"],
                    },
                    "requires_review": True,
                    "evidence_refs": [refs[-1]],
                }
            ]
            dependencies: list[dict[str, Any]] = []
            printed = [_printed_question(self.fixture, 0, refs)]
            paper_identity = self._paper_identity(refs[0])
        elif "SRC-Q-C" in source_ids:
            position = "end"
            shared_materials = []
            visual_objects = []
            dependencies = [
                {
                    "dependency_edge_id": "DEP-SYN-1-TO-2",
                    "from_atomic_part_id": "ATOMIC-SYN-1",
                    "to_atomic_part_id": "ATOMIC-SYN-2",
                    "relation": "uses_previous_result",
                    "evidence_refs": refs,
                }
            ]
            printed = [_printed_question(self.fixture, 1, refs)]
            paper_identity = None
        else:  # pragma: no cover - protects the fixture/provider binding.
            raise AssertionError(f"unexpected synthetic question shard: {source_ids}")
        theme_fragment = {
            "theme_big_question_id": theme["theme_big_question_id"],
            "theme_number": theme["theme_number"],
            "title": theme["title"],
            "context": theme["context"],
            "sequence_in_paper": theme["sequence_in_paper"],
            "fragment_position": position,
            "shared_materials": shared_materials,
            "visual_objects": visual_objects,
            "dependency_edges": dependencies,
            "printed_questions": printed,
            "evidence_refs": refs,
        }
        return {
            "paper_identity": paper_identity,
            "theme_fragments": [theme_fragment],
            "answer_candidates": [],
        }

    def _answer_fragment(self, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        refs = [item["evidence_id"] for item in evidence]
        answers: list[dict[str, Any]] = []
        for index, source in enumerate(self.fixture["answers"]):
            atomic_id: str | None = source["atomic_part_id"]
            question_number = source["question_number"]
            if index == 1 and self.misalign_second_answer:
                atomic_id = None
                question_number = "99"
            chemistry = (
                [
                    _chem(
                        source["answer_body"],
                        r"\ce{Cu - 2e^- -> Cu^2+}",
                        "equation",
                        refs,
                    )
                ]
                if index == 0
                else [_chem("Cu → Ag", None, "other", refs)]
            )
            answers.append(
                {
                    "answer_candidate_id": source["answer_candidate_id"],
                    "question_number": question_number,
                    "part_label": source["part_label"],
                    "atomic_part_id": atomic_id,
                    "answer_body": source["answer_body"],
                    "analysis": source["analysis"],
                    "max_score": source["max_score"],
                    "scoring_points": [
                        {
                            "scoring_point_id": f"SCORE-SYN-{index + 1}",
                            "description": source["scoring_point"],
                            "score": source["score"],
                            "evidence_refs": refs,
                        }
                    ],
                    "chemical_expressions": chemistry,
                    "authority": "nonofficial_reference",
                    "independently_verified": False,
                    "evidence_refs": refs,
                }
            )
        return {
            "paper_identity": None,
            "theme_fragments": [],
            "answer_candidates": answers,
        }

    def analyze_shard(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        self.payloads.append(request.transport_payload())
        evidence = self._evidence(request)
        role_fragment = (
            self._answer_fragment(evidence)
            if request.source_role == "answer"
            else self._question_fragment(request, evidence)
        )
        fragment = {
            "schema_version": "shchem.intake-batch-visual-fragment.v2",
            "shard_id": request.shard_id,
            "source_role": request.source_role,
            "input_mode": "direct_original_or_rendered_page_pixels",
            "source_text_layer_used": False,
            "fallback_used": False,
            "evidence": evidence,
            **role_fragment,
            "warnings": [],
        }
        if self.contract_override is not None:
            field, value = self.contract_override
            fragment[field] = value
        if self.add_forbidden_output_key:
            fragment["recognized_text"] = "forbidden"
        return fragment

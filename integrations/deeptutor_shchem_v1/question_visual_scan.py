from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from .datong_crop_revision import project_datong_descriptor
from .public_kb import ReadOnlyDataError, _checked_exact_path
from .reference_answer import (
    project_reference_answer,
    reference_answer_catalog_metadata,
)
from .security import SecurityError, validate_identifier
from .source_crop_revision import SourceCropRevisionError
from .supplemental_answers import answer_for_scan

SCOPE = "candidate_only_read_only_question_visual_scan"
EXPECTED_OUTPUT_FILES = frozenset(
    {
        "README.md",
        "build_visual_scan.py",
        "coverage_report.json",
        "disagreement_report.json",
        "run_mutation_tests.py",
        "scan_record_schema.json",
        "scan_records.jsonl",
        "source_manifest.json",
        "test_visual_scan.py",
        "validate_visual_scan.py",
    }
)
EXPECTED_GATE_KEYS = frozenset(
    {
        "human_reviewed",
        "human_chemistry_reviewed",
        "verified",
        "official",
        "retrieval_ready",
        "retrieval_allowed",
        "teaching_use_allowed",
        "generation_allowed",
        "publication_allowed",
        "answer_verified",
        "rubric_verified",
        "measured_difficulty_verified",
        "pixel_reuse_allowed",
    }
)
COMPARISON_FIELDS = (
    "item_type",
    "selection_rule",
    "primary_K",
    "supporting_K",
    "A",
    "C",
    "R",
    "RP",
    "D",
)
FACTOR_IDS = (
    "information_transformations",
    "reasoning_chain_steps",
    "knowledge_module_span",
    "representation_switches",
    "calculation_load",
    "experiment_load",
    "openness",
    "unfamiliarity",
    "language_load",
    "dependency_on_prior_parts",
)
XH_RULE_PATHS = (
    "kb/knowledge_taxonomy.json",
    "kb/question_classification_v1/controlled_vocabulary.json",
    "kb/shanghai_observed_standard_v1/README.md",
    "kb/shanghai_observed_standard_v1/policy/allow_prohibit_rules.json",
    "kb/shanghai_observed_standard_v1/profiles/OSV1-2024-XUHUI-YIMO-4T.json",
    "kb/evaluation/wave1_shanghai_style_deep_read_2026-08-04/observed_facts/paper_profiles.json",
    "kb/paper_learning_v1/curated/district_profiles.json",
)
QP_RULE_PATHS = (
    "kb/knowledge_taxonomy.json",
    "kb/question_classification_v1/controlled_vocabulary.json",
    "kb/shanghai_observed_standard_v1/README.md",
    "kb/shanghai_observed_standard_v1/policy/allow_prohibit_rules.json",
    "kb/evaluation/wave1_shanghai_style_deep_read_2026-08-04/observed_facts/paper_profiles.json",
    "kb/paper_learning_v1/curated/district_profiles.json",
)
PT_RULE_PATHS = QP_RULE_PATHS
YP_RULE_PATHS = QP_RULE_PATHS
DT_RULE_PATHS = QP_RULE_PATHS
WAVE1_ROOT = "kb/formal/candidates/wave1_formalization_2026-08-04"
WAVE1_MANIFEST = f"{WAVE1_ROOT}/manifest.json"
WAVE1_FILES = {
    "paper": f"{WAVE1_ROOT}/paper_records.jsonl",
    "theme": f"{WAVE1_ROOT}/theme_big_question_records.jsonl",
    "printed": f"{WAVE1_ROOT}/printed_question_records.jsonl",
    "atomic": f"{WAVE1_ROOT}/atomic_part_records.jsonl",
    "visual": f"{WAVE1_ROOT}/visual_crop_manifest.jsonl",
}
WAVE1_CORE = frozenset({WAVE1_MANIFEST, *WAVE1_FILES.values()})
XH_ANSWER_PAGES = frozenset(
    {
        "staging/wechat/live_articles_20260802/2026-徐汇区-二模-申教在线/article_images/article-image-10.png",
        "staging/wechat/live_articles_20260802/2026-徐汇区-二模-申教在线/article_images/article-image-11.png",
        "staging/wechat/live_articles_20260802/2026-徐汇区-二模-申教在线/article_images/article-image-12.png",
    }
)
QP_ANSWER_PAGES = frozenset(
    {
        "staging/wechat/live_articles_20260802/2026-青浦区-二模-升学的信息/article_images/article-image-08.jpg",
        "staging/wechat/live_articles_20260802/2026-青浦区-二模-升学的信息/article_images/article-image-09.jpg",
    }
)
PT_ANSWER_PAGES = frozenset(
    {
        "staging/wechat/live_articles_20260802/2026-普陀区-二模-学教有方/article_images/article-image-11.png",
        "staging/wechat/live_articles_20260802/2026-普陀区-二模-学教有方/article_images/article-image-12.png",
    }
)
YP_ANSWER_PAGES = frozenset(
    {
        "staging/wechat/live_articles_20260802/2026-杨浦区-二模-申教在线/article_images/article-image-09.png",
        "staging/wechat/live_articles_20260802/2026-杨浦区-二模-申教在线/article_images/article-image-10.png",
        "staging/wechat/live_articles_20260802/2026-杨浦区-二模-申教在线/article_images/article-image-11.png",
    }
)
DT_ANSWER_PAGES: frozenset[str] = frozenset()
QP_OVERLAY_PREFIX = (
    "kb/classification/intake_complete_papers_overlay_v2_2026-08-03/"
    "qingpu_2026_second_mock_complete_paper"
)
QP_OVERLAY_PATHS = frozenset(
    {
        f"{QP_OVERLAY_PREFIX}/manifest.json",
        f"{QP_OVERLAY_PREFIX}/paper_record.json",
    }
)
PT_OVERLAY_PREFIX = (
    "kb/classification/intake_complete_papers_overlay_v2_2026-08-03/"
    "putuo_2026_second_mock_complete_paper"
)
PT_OVERLAY_PATHS = frozenset(
    {
        f"{PT_OVERLAY_PREFIX}/manifest.json",
        f"{PT_OVERLAY_PREFIX}/paper_record.json",
    }
)
YP_OVERLAY_PREFIX = (
    "kb/classification/intake_complete_papers_overlay_v2_2026-08-03/"
    "yangpu_2026_second_mock_complete_paper"
)
YP_OVERLAY_PATHS = frozenset(
    {
        f"{YP_OVERLAY_PREFIX}/manifest.json",
        f"{YP_OVERLAY_PREFIX}/paper_record.json",
    }
)
DT_INTAKE_PREFIX = (
    "kb/formal/candidates/intake_round_2026-08-02/"
    "datong_high1_2025_fall_midterm_complete_paper"
)
DT_OVERLAY_PREFIX = (
    "kb/classification/intake_complete_papers_overlay_v2_2026-08-03/"
    "datong_high1_2025_fall_midterm_complete_paper"
)
DT_OVERLAY_PATHS = frozenset(
    {
        f"{DT_OVERLAY_PREFIX}/manifest.json",
        f"{DT_OVERLAY_PREFIX}/paper_record.json",
    }
)
DT_SOURCE_CONTROL_PATHS = frozenset(
    {
        f"{DT_INTAKE_PREFIX}/manifest.json",
        f"{DT_INTAKE_PREFIX}/crop_manifest.jsonl",
        f"{DT_INTAKE_PREFIX}/section_records.jsonl",
        f"{DT_INTAKE_PREFIX}/school_question_records.jsonl",
        f"{DT_INTAKE_PREFIX}/school_part_records.jsonl",
        f"{DT_INTAKE_PREFIX}/page_reviews.jsonl",
        f"{DT_INTAKE_PREFIX}/validation_report.json",
        "staging/wechat/live_articles_20260802/2025-2026学年-大同中学-高一上期中-上海初高中化学/article_capture.json",
        "staging/wechat/live_articles_20260802/2025-2026学年-大同中学-高一上期中-上海初高中化学/article_body.txt",
        "staging/wechat/live_articles_20260802/2025-2026学年-大同中学-高一上期中-上海初高中化学/article_images/article-image-01.png",
    }
)
PT_PAPER_IDENTITY_EVIDENCE = (
    (
        "PT2026-SHARED-IDENTITY_HEADER",
        "kb/formal/candidates/intake_round_2026-08-02/putuo_2026_second_mock_complete_paper/evidence/shared/shared-identity_header.png",
        "1fa5058093c287a9fa9c5b39ce4f57b9f7108522fe13e4c96ea7c8f17a391d59",
        1,
    ),
    (
        "PT2026-SHARED-IDENTITY_MONTH_FOOTER",
        "kb/formal/candidates/intake_round_2026-08-02/putuo_2026_second_mock_complete_paper/evidence/shared/shared-identity_month_footer.png",
        "b5cf1f62b48611c229c56a0c13b00cb2f1d01d35fa96f84d8fcbf137e258ee55",
        1,
    ),
)
YP_PAPER_IDENTITY_EVIDENCE = (
    (
        "YP2026-SHARED-IDENTITY_HEADER",
        "kb/formal/candidates/intake_round_2026-08-02/yangpu_2026_second_mock_complete_paper/evidence/shared/shared-identity_header.png",
        "0652ef8795297d3e5c8022b1da9b42acc973a2a42ee269cadae21aeaea4b5951",
        1,
    ),
    (
        "YP2026-SHARED-IDENTITY_PAGE_FOOTER",
        "kb/formal/candidates/intake_round_2026-08-02/yangpu_2026_second_mock_complete_paper/evidence/shared/shared-identity_page_footer.png",
        "d31adee60202d959eded92feba7e90461f15cd02d51f7fb7c115447be084cef4",
        1,
    ),
)
DT_PAPER_IDENTITY_EVIDENCE = (
    (
        "DT2025-H1-SHARED-IDENTITY",
        f"{DT_INTAKE_PREFIX}/evidence/shared/shared-identity.png",
        "c6a6c333f8d379dd03dbb4887eaa38c02068cf239ebc73aaed46b71d9b768856",
        1,
    ),
)
DT_PARENT_QUESTION_EVIDENCE = (
    (
        "DT2025-H1-Q33-E1",
        f"{DT_INTAKE_PREFIX}/evidence/questions/section-4/dt2025-h1-q33-e1.png",
        "9ab6835999296ea701b3e1579ea687f2f45fcdcb7dac88f68b6a68a68dc107df",
        5,
    ),
)
DT_SOURCE_IDENTITY = {
    "source_id": "WX-DATONG-TITLE-ATTRIBUTED-2025-H1-FALL-MIDTERM",
    "title": "【高一化学】2025-2026年上海市大同中学高一期中化学试卷",
    "account": "上海初高中化学",
    "published_at": "2025-11-20",
    "url": "https://mp.weixin.qq.com/s?__biz=MzAwMDgxNzk1Mg==&mid=2648903917&idx=1&sn=384eb7abf6b787293e6a3845234d0a06&chksm=83b6d598cd5e2c1ef00e5b900adea332c09c7ddb4b2cf8fc3566328691f4193f95d6c934af40#rd",
    "source_type": "school_exam",
    "paper_family": "midterm",
    "structure_model": "other_observed",
    "evidence_level": "L2_PAGE_VERIFIED_SCHOOL_EXAM_WITH_TITLE_ONLY_SCHOOL_ATTRIBUTION",
    "official_status": "nonofficial",
    "temporal_role": "CURRENT_STYLE_PRIMARY_SCHOOL_EXAM_ONLY_NOT_LEVEL_EXAM_STYLE",
}
DT_PAPER_IDENTITY_BOUNDARY = {
    "visible_paper_face_title": "2025学年第一学期期中考试试卷（高一化学）",
    "paper_face_verified_claims": [
        "2025学年第一学期",
        "高一化学",
        "期中考试",
        "60分钟",
        "100分",
        "6页",
    ],
    "school_attribution": "上海市大同中学",
    "school_attribution_basis": "wechat_article_title_only_not_visible_on_paper_face",
    "district": "unknown",
    "exam_family": "school_midterm_not_level_exam_not_district_mock",
    "answer_material": "absent_in_captured_source",
}
PT_PREDECESSOR_OVERRIDES = (
    (
        "W1-PT2026-EM-AP-PT2026-T4-Q07-P01-S02",
        ("W1-PT2026-EM-AP-PT2026-T4-Q07-P01-S01",),
    ),
    (
        "W1-PT2026-EM-AP-PT2026-T4-Q08-P01-S02",
        ("W1-PT2026-EM-AP-PT2026-T4-Q08-P01-S01",),
    ),
    (
        "W1-PT2026-EM-AP-PT2026-T4-Q08-P01-S03",
        ("W1-PT2026-EM-AP-PT2026-T4-Q08-P01-S01",),
    ),
    (
        "W1-PT2026-EM-AP-PT2026-T5-Q06-P03",
        ("W1-PT2026-EM-AP-PT2026-T5-Q06-P01",),
    ),
)
YP_PREDECESSOR_OVERRIDES = (
    (
        "W1-YP2026-EM-AP-YP2026-T2-Q01-P01-S02",
        ("W1-YP2026-EM-AP-YP2026-T2-Q01-P01-S01",),
    ),
    (
        "W1-YP2026-EM-AP-YP2026-T4-Q05-P02-S02",
        ("W1-YP2026-EM-AP-YP2026-T4-Q05-P02-S01",),
    ),
)
DT_PREDECESSOR_OVERRIDES = (
    (
        "W1-DT2025-H1-MID-AP-DT2025-H1-Q06-P01-S03",
        ("W1-DT2025-H1-MID-AP-DT2025-H1-Q06-P01-S01",),
    ),
    (
        "W1-DT2025-H1-MID-AP-DT2025-H1-Q09-P01",
        ("W1-DT2025-H1-MID-AP-DT2025-H1-Q08-P01",),
    ),
    (
        "W1-DT2025-H1-MID-AP-DT2025-H1-Q13-P01-S02",
        ("W1-DT2025-H1-MID-AP-DT2025-H1-Q13-P01-S01",),
    ),
    (
        "W1-DT2025-H1-MID-AP-DT2025-H1-Q17-P01-S02",
        ("W1-DT2025-H1-MID-AP-DT2025-H1-Q17-P01-S01",),
    ),
    (
        "W1-DT2025-H1-MID-AP-DT2025-H1-Q24-P01-S02",
        ("W1-DT2025-H1-MID-AP-DT2025-H1-Q24-P01-S01",),
    ),
    (
        "W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S02",
        ("W1-DT2025-H1-MID-AP-DT2025-H1-Q33-P03-S01",),
    ),
    (
        "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S02",
        ("W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S01",),
    ),
    (
        "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S03",
        ("W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S02",),
    ),
)


@dataclass(frozen=True)
class BatchSpec:
    product_id: str
    paper_id: str
    product_relative: Path
    expected_manifest_self_sha256: str
    expected_fixed_counts: dict[str, int]
    expected_source_binding_count: int
    rule_paths: tuple[str, ...]
    answer_pages: frozenset[str]
    answer_page_format: str
    expected_atomic_count: int
    expected_theme_count: int
    expected_printed_count: int
    compare_agree: int
    compare_corrected: int
    compare_blocked: int
    corrected_nodes: int
    scan_id_prefix: str
    answer_alignment_status: str
    answer_availability_counts: tuple[tuple[str, int], ...]
    expected_output_files: frozenset[str] = EXPECTED_OUTPUT_FILES
    extra_source_paths: frozenset[str] = frozenset()
    paper_identity_evidence: tuple[tuple[str, str, str, int], ...] = ()
    predecessor_overrides: tuple[tuple[str, tuple[str, ...]], ...] = ()
    expected_question_descriptor_count: int | None = None
    expected_atomic_shared_descriptor_count: int | None = None
    expected_theme_atomic_counts: tuple[tuple[str, int], ...] = ()
    expected_theme_printed_counts: tuple[tuple[str, int], ...] = ()
    require_printed_parent_coverage: bool = False
    forbidden_rule_markers: tuple[str, ...] = ()
    filter_predecessor_placeholders: bool = False
    require_printed_atomic_one_to_one: bool = False
    source_namespace: str = "wave1_candidate_visual_scan"
    answer_authority: str = "nonofficial_reference"
    answer_evidence_required: bool = True
    expected_question_evidence_sha_count: int | None = None
    visual_inspection_declaration: dict[str, Any] | None = None
    parent_question_evidence: tuple[tuple[str, str, str, int], ...] = ()
    source_identity: dict[str, Any] | None = None
    paper_identity_boundary: dict[str, Any] | None = None
    require_answer_whole_pages_declaration: bool = False

    @property
    def status_counts(self) -> dict[str, int]:
        return {
            **self.expected_fixed_counts,
            "compare_agree": self.compare_agree,
            "compare_corrected": self.compare_corrected,
            "compare_blocked": self.compare_blocked,
            "corrected_nodes": self.corrected_nodes,
        }


XH_BATCH_SPEC = BatchSpec(
    product_id="XH2026-QUESTION-VISUAL-SCAN-V1-2026-08-24",
    paper_id="W1-XH2026-EM",
    product_relative=Path("kb/classification/question_visual_scan_v1_2026-08-24"),
    expected_manifest_self_sha256="8e45890206f48ad6e737dd99acd29fa0ce1a70f391bf960f8c645e6792cb2ee7",
    expected_fixed_counts={
        "expected_atomic_parts": 46,
        "scan_records": 46,
        "visual_scan_completed": 46,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 44,
        "unique_shared_crops_actually_viewed": 16,
        "answer_whole_pages_actually_viewed": 3,
    },
    expected_source_binding_count=122,
    rule_paths=XH_RULE_PATHS,
    answer_pages=XH_ANSWER_PAGES,
    answer_page_format="PNG",
    expected_atomic_count=46,
    expected_theme_count=5,
    expected_printed_count=44,
    compare_agree=369,
    compare_corrected=45,
    compare_blocked=0,
    corrected_nodes=25,
    scan_id_prefix="VS-XH2026-",
    answer_alignment_status="visually_number_aligned_on_answer_pages_10_to_12",
    answer_availability_counts=(("present_part_aligned", 46),),
)
QP_BATCH_SPEC = BatchSpec(
    product_id="QP2026-QUESTION-VISUAL-SCAN-V1-2026-08-25",
    paper_id="W1-QP2026-EM",
    product_relative=Path(
        "kb/classification/question_visual_scan_qp2026_v1_2026-08-25"
    ),
    expected_manifest_self_sha256="2e328518e3ceffd0d07613d52e2b8a0c82b0e4a05181ad3bd03da6e73251ac22",
    expected_fixed_counts={
        "expected_atomic_parts": 45,
        "scan_records": 45,
        "visual_scan_completed": 45,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 46,
        "unique_shared_crops_actually_viewed": 16,
        "answer_whole_pages_actually_viewed": 2,
    },
    expected_source_binding_count=123,
    rule_paths=QP_RULE_PATHS,
    answer_pages=QP_ANSWER_PAGES,
    answer_page_format="JPEG",
    expected_atomic_count=45,
    expected_theme_count=5,
    expected_printed_count=45,
    compare_agree=363,
    compare_corrected=42,
    compare_blocked=0,
    corrected_nodes=24,
    scan_id_prefix="VS-QP2026-",
    answer_alignment_status=(
        "visually_number_aligned_on_nonofficial_answer_whole_pages_08_to_09"
    ),
    answer_availability_counts=(("present_part_aligned", 45),),
    extra_source_paths=QP_OVERLAY_PATHS,
    filter_predecessor_placeholders=True,
    require_printed_atomic_one_to_one=True,
)
PT_BATCH_SPEC = BatchSpec(
    product_id="PT2026-QUESTION-VISUAL-SCAN-V1-2026-08-25",
    paper_id="W1-PT2026-EM",
    product_relative=Path(
        "kb/classification/question_visual_scan_pt2026_v1_2026-08-25"
    ),
    expected_manifest_self_sha256=(
        "f738a072ab08194e8513ca0237c0ab437a31662ca8fb7673c52587ab0feb25fd"
    ),
    expected_fixed_counts={
        "expected_atomic_parts": 52,
        "scan_records": 52,
        "visual_scan_completed": 52,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 52,
        "unique_shared_crops_actually_viewed": 26,
        "answer_whole_pages_actually_viewed": 2,
    },
    expected_source_binding_count=142,
    rule_paths=PT_RULE_PATHS,
    answer_pages=PT_ANSWER_PAGES,
    answer_page_format="PNG",
    expected_atomic_count=52,
    expected_theme_count=5,
    expected_printed_count=38,
    compare_agree=371,
    compare_corrected=97,
    compare_blocked=0,
    corrected_nodes=43,
    scan_id_prefix="VS-PT2026-",
    answer_alignment_status=(
        "visually_number_aligned_on_nonofficial_answer_whole_pages_11_to_12"
    ),
    answer_availability_counts=(
        ("present_part_aligned", 41),
        ("present_unaligned", 11),
    ),
    expected_output_files=EXPECTED_OUTPUT_FILES | {"pt_analyses.py"},
    extra_source_paths=PT_OVERLAY_PATHS,
    paper_identity_evidence=PT_PAPER_IDENTITY_EVIDENCE,
    predecessor_overrides=PT_PREDECESSOR_OVERRIDES,
    expected_question_descriptor_count=53,
    expected_atomic_shared_descriptor_count=24,
    expected_theme_atomic_counts=(
        ("W1-PT2026-EM-T01", 11),
        ("W1-PT2026-EM-T02", 10),
        ("W1-PT2026-EM-T03", 10),
        ("W1-PT2026-EM-T04", 12),
        ("W1-PT2026-EM-T05", 9),
    ),
    expected_theme_printed_counts=(
        ("W1-PT2026-EM-T01", 9),
        ("W1-PT2026-EM-T02", 6),
        ("W1-PT2026-EM-T03", 9),
        ("W1-PT2026-EM-T04", 8),
        ("W1-PT2026-EM-T05", 6),
    ),
    require_printed_parent_coverage=True,
    forbidden_rule_markers=(
        "/PROFILES/",
        "PUTUO-ERMO",
        "XUHUI-YIMO",
        "LEVEL-RECALL",
    ),
    filter_predecessor_placeholders=True,
)
YP_BATCH_SPEC = BatchSpec(
    product_id="YP2026-QUESTION-VISUAL-SCAN-V1-2026-08-25",
    paper_id="W1-YP2026-EM",
    product_relative=Path(
        "kb/classification/question_visual_scan_yp2026_v1_2026-08-25"
    ),
    expected_manifest_self_sha256=(
        "c6900402187dca1660d27036827c99b6abde0eb83cd85461ef3899fbbd552a26"
    ),
    expected_fixed_counts={
        "expected_themes": 5,
        "expected_printed_questions": 39,
        "expected_atomic_parts": 53,
        "scan_records": 53,
        "visual_scan_completed": 53,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 41,
        "unique_question_and_part_evidence_sha256_actually_viewed": 57,
        "unique_shared_crops_actually_viewed": 18,
        "answer_whole_pages_actually_viewed": 3,
    },
    expected_source_binding_count=149,
    rule_paths=YP_RULE_PATHS,
    answer_pages=YP_ANSWER_PAGES,
    answer_page_format="PNG",
    expected_atomic_count=53,
    expected_theme_count=5,
    expected_printed_count=39,
    compare_agree=390,
    compare_corrected=87,
    compare_blocked=0,
    corrected_nodes=44,
    scan_id_prefix="VS-YP2026-",
    answer_alignment_status=(
        "visually_checked_on_nonofficial_answer_whole_pages_09_to_11; "
        "four_root_split_units_remain_present_unaligned"
    ),
    answer_availability_counts=(
        ("present_part_aligned", 49),
        ("present_unaligned", 4),
    ),
    expected_output_files=EXPECTED_OUTPUT_FILES | {"yp_analyses.py"},
    extra_source_paths=YP_OVERLAY_PATHS,
    paper_identity_evidence=YP_PAPER_IDENTITY_EVIDENCE,
    predecessor_overrides=YP_PREDECESSOR_OVERRIDES,
    expected_question_descriptor_count=63,
    expected_atomic_shared_descriptor_count=16,
    expected_theme_atomic_counts=(
        ("W1-YP2026-EM-T01", 9),
        ("W1-YP2026-EM-T02", 11),
        ("W1-YP2026-EM-T03", 12),
        ("W1-YP2026-EM-T04", 12),
        ("W1-YP2026-EM-T05", 9),
    ),
    expected_theme_printed_counts=(
        ("W1-YP2026-EM-T01", 9),
        ("W1-YP2026-EM-T02", 7),
        ("W1-YP2026-EM-T03", 8),
        ("W1-YP2026-EM-T04", 7),
        ("W1-YP2026-EM-T05", 8),
    ),
    require_printed_parent_coverage=True,
    forbidden_rule_markers=(
        "/PROFILES/",
        "PUTUO-ERMO",
        "XUHUI-YIMO",
        "LEVEL-RECALL",
    ),
    filter_predecessor_placeholders=True,
    expected_question_evidence_sha_count=57,
    visual_inspection_declaration={
        "whole_question_crops_actually_viewed": 41,
        "question_and_part_evidence_sha256_actually_viewed": 57,
        "shared_crops_actually_viewed": 18,
        "answer_whole_pages_actually_viewed": 3,
        "metadata_only_scan_forbidden": True,
        "ocr_is_authority": False,
    },
)
DT_BATCH_SPEC = BatchSpec(
    product_id="DT2025-H1-MID-QUESTION-VISUAL-SCAN-V1-2026-08-25",
    paper_id="W1-DT2025-H1-MID",
    product_relative=Path(
        "kb/classification/question_visual_scan_dt2025_h1_mid_v1_2026-08-25"
    ),
    expected_manifest_self_sha256=(
        "ed7eed349e287098f8aa3be3cf97091f24daab6d5153e3c4f6a20d3fa9ce8d8d"
    ),
    expected_fixed_counts={
        "expected_atomic_parts": 56,
        "scan_records": 56,
        "visual_scan_completed": 56,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 45,
        "unique_shared_crops_actually_viewed": 13,
        "answer_whole_pages_actually_viewed": 0,
    },
    expected_source_binding_count=83,
    rule_paths=DT_RULE_PATHS,
    answer_pages=DT_ANSWER_PAGES,
    answer_page_format="PNG",
    expected_atomic_count=56,
    expected_theme_count=5,
    expected_printed_count=41,
    compare_agree=446,
    compare_corrected=58,
    compare_blocked=0,
    corrected_nodes=37,
    scan_id_prefix="VS-DT2025-H1-MID-",
    answer_alignment_status="no_answer_material_available_in_captured_source",
    answer_availability_counts=(("absent", 56),),
    expected_output_files=EXPECTED_OUTPUT_FILES | {"dt_analyses.py"},
    extra_source_paths=DT_OVERLAY_PATHS | DT_SOURCE_CONTROL_PATHS,
    paper_identity_evidence=DT_PAPER_IDENTITY_EVIDENCE,
    predecessor_overrides=DT_PREDECESSOR_OVERRIDES,
    expected_question_descriptor_count=45,
    expected_atomic_shared_descriptor_count=12,
    expected_theme_atomic_counts=(
        ("W1-DT2025-H1-MID-T01", 15),
        ("W1-DT2025-H1-MID-T02", 11),
        ("W1-DT2025-H1-MID-T03", 12),
        ("W1-DT2025-H1-MID-T04", 9),
        ("W1-DT2025-H1-MID-T05", 9),
    ),
    expected_theme_printed_counts=(
        ("W1-DT2025-H1-MID-T01", 9),
        ("W1-DT2025-H1-MID-T02", 9),
        ("W1-DT2025-H1-MID-T03", 10),
        ("W1-DT2025-H1-MID-T04", 6),
        ("W1-DT2025-H1-MID-T05", 7),
    ),
    require_printed_parent_coverage=True,
    forbidden_rule_markers=(
        "/PROFILES/",
        "LEVEL-RECALL",
        "XUHUI-YIMO",
        "PUTUO-ERMO",
        "SCHOOL-FOUR-SCHOOL-MIDTERM",
    ),
    filter_predecessor_placeholders=True,
    source_namespace="wave1_school_exam_candidate_visual_scan",
    answer_authority="none",
    answer_evidence_required=False,
    expected_question_evidence_sha_count=45,
    visual_inspection_declaration={
        "question_crops_actually_viewed": 46,
        "atomic_question_crops_actually_viewed": 45,
        "parent_context_question_crops_actually_viewed": 1,
        "shared_crops_actually_viewed": 13,
        "answer_whole_pages_actually_viewed": 0,
        "metadata_only_scan_forbidden": True,
        "ocr_is_authority": False,
    },
    parent_question_evidence=DT_PARENT_QUESTION_EVIDENCE,
    source_identity=DT_SOURCE_IDENTITY,
    paper_identity_boundary=DT_PAPER_IDENTITY_BOUNDARY,
    require_answer_whole_pages_declaration=True,
)
BATCH_SPECS = (
    XH_BATCH_SPEC,
    QP_BATCH_SPEC,
    PT_BATCH_SPEC,
    YP_BATCH_SPEC,
    DT_BATCH_SPEC,
)

# Legacy XH constants remain import-compatible for existing tests and callers.
PRODUCT_ID = XH_BATCH_SPEC.product_id
PAPER_ID = XH_BATCH_SPEC.paper_id
PRODUCT_RELATIVE = XH_BATCH_SPEC.product_relative
EXPECTED_MANIFEST_SELF_SHA256 = XH_BATCH_SPEC.expected_manifest_self_sha256
EXPECTED_FIXED_COUNTS = XH_BATCH_SPEC.expected_fixed_counts
STATUS_COUNTS = XH_BATCH_SPEC.status_counts
EXPECTED_SOURCE_BINDING_COUNT = XH_BATCH_SPEC.expected_source_binding_count
RULE_PATHS = XH_BATCH_SPEC.rule_paths
ANSWER_PAGES = XH_BATCH_SPEC.answer_pages

AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "human_chemistry_reviewed": False,
    "verified": False,
    "official": False,
    "retrieval_ready": False,
    "retrieval_allowed": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "answer_verified": False,
    "rubric_verified": False,
    "measured_difficulty_verified": False,
    "pixel_reuse_allowed": False,
}

_FORBIDDEN_PROJECTION_KEYS = {
    "answer",
    "answer_content",
    "answer_crop",
    "answer_crops",
    "answer_evidence",
    "answer_image_endpoint",
    "answer_page",
    "answer_page_path",
    "answer_pages",
    "answer_text",
    "crop_path",
    "crop_sha256",
    "evidence_binding_sha256",
    "exact_image_evidence",
    "image_endpoint",
    "original_source_path",
    "original_source_sha256",
    "output_binding",
    "output_bindings",
    "path",
    "pixel_endpoint",
    "product_relative",
    "reference_summary_zh",
    "rule_binding",
    "rule_bindings",
    "rule_path",
    "rule_paths",
    "source",
    "source_binding",
    "source_bindings",
    "source_id",
    "source_manifest",
    "source_manifest_binding",
    "source_package_path",
    "source_url",
    "viewed_evidence",
    "visual_alignment_evidence",
    "whole_page",
    "whole_page_path",
    "whole_page_sha256",
}
_LOCAL_PATH_PATTERN = re.compile(
    r"(?i:(?<![a-z0-9])[a-z]:[\\/]|file:/+|\\\\[^\\/\s]+[\\/]"
    r"|(?<![a-z0-9_.:/\\])/(?:home|users|tmp)/)"
    r"|(?<![A-Za-z0-9_./\\])(?:sh-chem-db/|kb/|staging/|runtime/|integrations/)"
)


class QuestionVisualScanError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _Snapshot:
    manifest_self_sha256: str
    output_binding_count: int
    source_binding_count: int
    records: tuple[dict[str, Any], ...]
    by_node_id: dict[str, dict[str, Any]]
    corrected_fields_by_node: tuple[dict[str, Any], ...]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _manifest_self_hash(manifest: dict[str, Any]) -> str:
    clone = deepcopy(manifest)
    clone["manifest_self_sha256"] = None
    return _sha256(_canonical_bytes(clone))


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuestionVisualScanError(
            "question_visual_scan_data_invalid", f"{label} is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise QuestionVisualScanError(
            "question_visual_scan_data_invalid", f"{label} must be a JSON object"
        )
    return value


def _jsonl_rows(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QuestionVisualScanError(
            "question_visual_scan_data_invalid", f"{label} is not valid UTF-8"
        ) from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise QuestionVisualScanError(
                "question_visual_scan_data_invalid",
                f"{label} contains invalid JSONL at line {line_number}",
            ) from exc
        if not isinstance(value, dict):
            raise QuestionVisualScanError(
                "question_visual_scan_data_invalid",
                f"{label} row {line_number} must be an object",
            )
        rows.append(value)
    return rows


def _false_gates(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != EXPECTED_GATE_KEYS:
        raise QuestionVisualScanError(
            "question_visual_scan_gate_invalid", f"{label} gate set drifted"
        )
    if any(item is not False for item in value.values()):
        raise QuestionVisualScanError(
            "question_visual_scan_gate_elevated", f"{label} authority gate elevated"
        )


def _all_false(value: Any, label: str) -> None:
    if not isinstance(value, dict) or not value or any(
        item is not False for item in value.values()
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_gate_elevated", f"{label} authority gate elevated"
        )


def _binding_index(
    value: Any, label: str, *, expected_count: int
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise QuestionVisualScanError(
            "question_visual_scan_binding_invalid", f"{label} binding count drifted"
        )
    indexed: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
            raise QuestionVisualScanError(
                "question_visual_scan_binding_invalid", f"{label} binding shape drifted"
            )
        relative = item.get("path")
        pure = PurePosixPath(relative) if isinstance(relative, str) else None
        if (
            pure is None
            or not relative
            or "\\" in relative
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or relative in indexed
            or type(item.get("bytes")) is not int
            or item["bytes"] < 0
            or not isinstance(item.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_binding_invalid", f"{label} binding is invalid"
            )
        indexed[relative] = item
    return indexed


def _verify_bound_bytes(
    root: Path, bindings: dict[str, dict[str, Any]], label: str
) -> dict[str, bytes]:
    verified: dict[str, bytes] = {}
    for relative, binding in bindings.items():
        try:
            path = _checked_exact_path(root, relative)
            raw = path.read_bytes()
        except (OSError, ReadOnlyDataError) as exc:
            raise QuestionVisualScanError(
                "question_visual_scan_binding_unavailable",
                f"{label} bound artifact is unavailable",
            ) from exc
        if len(raw) != binding["bytes"] or _sha256(raw) != binding["sha256"]:
            raise QuestionVisualScanError(
                "question_visual_scan_hash_mismatch",
                f"{label} bound artifact failed bytes or SHA-256 verification",
            )
        verified[relative] = raw
    return verified


def _png_dimensions(raw: bytes, label: str) -> tuple[int, int]:
    if (
        len(raw) < 24
        or raw[:8] != b"\x89PNG\r\n\x1a\n"
        or raw[12:16] != b"IHDR"
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_evidence_invalid", f"{label} is not a PNG"
        )
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    if width <= 0 or height <= 0:
        raise QuestionVisualScanError(
            "question_visual_scan_evidence_invalid", f"{label} dimensions are invalid"
        )
    return width, height


def _jpeg_dimensions(raw: bytes, label: str) -> tuple[int, int]:
    if len(raw) < 4 or raw[:2] != b"\xff\xd8" or raw[-2:] != b"\xff\xd9":
        raise QuestionVisualScanError(
            "question_visual_scan_evidence_invalid", f"{label} is not a JPEG"
        )
    cursor = 2
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while cursor < len(raw):
        if raw[cursor] != 0xFF:
            cursor += 1
            continue
        while cursor < len(raw) and raw[cursor] == 0xFF:
            cursor += 1
        if cursor >= len(raw):
            break
        marker = raw[cursor]
        cursor += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if cursor + 2 > len(raw):
            break
        segment_length = int.from_bytes(raw[cursor : cursor + 2], "big")
        if segment_length < 2 or cursor + segment_length > len(raw):
            break
        if marker in start_of_frame:
            if segment_length < 7:
                break
            height = int.from_bytes(raw[cursor + 3 : cursor + 5], "big")
            width = int.from_bytes(raw[cursor + 5 : cursor + 7], "big")
            if width > 0 and height > 0:
                return width, height
            break
        cursor += segment_length
    raise QuestionVisualScanError(
        "question_visual_scan_evidence_invalid", f"{label} dimensions are invalid"
    )


def _image_dimensions(
    raw: bytes, label: str, expected_format: str
) -> tuple[int, int]:
    if expected_format == "PNG":
        return _png_dimensions(raw, label)
    if expected_format == "JPEG":
        return _jpeg_dimensions(raw, label)
    raise QuestionVisualScanError(
        "question_visual_scan_evidence_invalid", f"{label} format contract is invalid"
    )


def _ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [
            str(item["id"])
            for item in value
            if isinstance(item, dict) and item.get("id")
        ]
    if isinstance(value, dict):
        if value.get("id"):
            return [str(value["id"])]
        for key in ("candidate_values", "values"):
            if isinstance(value.get(key), list):
                return [str(item) for item in value[key]]
    return []


def _normalized(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return sorted(value or [])


def _source_comparison_values(row: dict[str, Any]) -> dict[str, list[str]]:
    primary = [
        item for item in _ids(row.get("primary_knowledge_K")) if item != "unknown"
    ]
    return {
        "item_type": _normalized(row.get("item_type")),
        "selection_rule": _normalized(row.get("selection_rule")),
        "primary_K": _normalized(primary),
        "supporting_K": _normalized(_ids(row.get("supporting_knowledge_K"))),
        "A": _normalized(_ids(row.get("ability_A"))),
        "C": _normalized(_ids(row.get("context_C"))),
        "R": _normalized(_ids(row.get("response_R"))),
        "RP": _normalized(_ids(row.get("representation_RP"))),
        "D": _normalized(
            row.get("difficulty", {}).get("cognitive_prelabel") or "unknown"
        ),
    }


def _scan_comparison_values(record: dict[str, Any]) -> dict[str, list[str]]:
    classification = record["classification"]
    return {
        "item_type": _normalized(classification["item_type"]),
        "selection_rule": _normalized(classification["selection_rule"]),
        "primary_K": _normalized(classification["primary_K"]),
        "supporting_K": _normalized(classification["supporting_K"]),
        "A": _normalized(classification["A"]),
        "C": _normalized(classification["C"]),
        "R": _normalized(classification["R"]),
        "RP": _normalized(classification["RP"]),
        "D": _normalized(record["difficulty"]["cognitive_prelabel"]),
    }


def _expected_compare(old: list[str], scan: list[str]) -> tuple[str, float]:
    if old == scan:
        return "agree", 0.91
    if scan == ["unknown"]:
        return "blocked", 0.45
    return "corrected", 0.87


def _evidence_binding_hash(record: dict[str, Any]) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "atomic_part_id": record["hierarchy"]["atomic_part_id"],
                "viewed_evidence": record["viewed_evidence"],
                "answer_evidence": record["answer"]["visual_alignment_evidence"],
                "visible_summary_zh": record["visible_summary_zh"],
            }
        )
    )


def _expected_rule_contracts(
    spec: BatchSpec,
    source_bytes: dict[str, bytes],
    source_bindings: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for relative in spec.rule_paths:
        raw = source_bytes[relative]
        if relative == "kb/knowledge_taxonomy.json":
            value = _json_object(raw, relative)
            version = {
                "namespace_version": "knowledge_taxonomy",
                "declared_version": value.get("schema_version"),
                "version_basis": "JSON声明字段",
                "version_locator": "/schema_version",
            }
        elif relative == "kb/question_classification_v1/controlled_vocabulary.json":
            value = _json_object(raw, relative)
            version = {
                "namespace_version": "question_classification_v1",
                "declared_version": value.get("vocabulary_version"),
                "version_basis": "JSON声明字段",
                "version_locator": "/vocabulary_version",
            }
        elif relative == "kb/shanghai_observed_standard_v1/README.md":
            try:
                first_line = raw.decode("utf-8").splitlines()[0]
            except (UnicodeDecodeError, IndexError) as exc:
                raise QuestionVisualScanError(
                    "question_visual_scan_rule_invalid", "rule README is invalid"
                ) from exc
            if first_line != "# Shanghai Observed Standard v1":
                raise QuestionVisualScanError(
                    "question_visual_scan_rule_invalid", "rule README version drifted"
                )
            version = {
                "namespace_version": "shanghai_observed_standard_v1",
                "declared_version": "v1",
                "version_basis": "受控README首行",
                "version_locator": "line:1",
            }
        else:
            value = _json_object(raw, relative)
            if relative == "kb/shanghai_observed_standard_v1/policy/allow_prohibit_rules.json":
                namespace, locator = "shanghai_observed_standard_v1", "/standard_version"
                declared = value.get("standard_version")
            elif relative == "kb/shanghai_observed_standard_v1/profiles/OSV1-2024-XUHUI-YIMO-4T.json":
                namespace, locator = str(value.get("profile_id")), "/profile_version"
                declared = value.get("profile_version")
            elif relative == "kb/evaluation/wave1_shanghai_style_deep_read_2026-08-04/observed_facts/paper_profiles.json":
                namespace, locator = "wave1_shanghai_style_deep_read_2026-08-04", "/schema_version"
                declared = value.get("schema_version")
            elif relative == "kb/paper_learning_v1/curated/district_profiles.json":
                district_marker = {
                    QP_BATCH_SPEC.paper_id: "青浦",
                    PT_BATCH_SPEC.paper_id: "普陀",
                    YP_BATCH_SPEC.paper_id: "杨浦",
                }.get(spec.paper_id)
                if district_marker and district_marker not in raw.decode("utf-8"):
                    raise QuestionVisualScanError(
                        "question_visual_scan_rule_invalid",
                        "district profile registry no longer contains batch evidence",
                    )
                namespace, locator = "paper_learning_v1/curated", "/schema_version"
                declared = value.get("schema_version")
            else:
                raise QuestionVisualScanError(
                    "question_visual_scan_rule_invalid",
                    "unapproved visual scan rule path",
                )
            version = {
                "namespace_version": namespace,
                "declared_version": declared,
                "version_basis": "JSON声明字段",
                "version_locator": locator,
            }
        if any(not isinstance(item, str) or not item for item in version.values()):
            raise QuestionVisualScanError(
                "question_visual_scan_rule_invalid", "rule version binding drifted"
            )
        binding = source_bindings[relative]
        contracts.append(
            {
                "rule_id": relative,
                "version": version,
                "sha256": binding["sha256"],
                "bytes": binding["bytes"],
            }
        )
    return contracts


def _expected_paper_identity_descriptors(
    spec: BatchSpec,
    source_bytes: dict[str, bytes],
    source_bindings: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for crop_id, relative, expected_sha256, source_page in spec.paper_identity_evidence:
        if crop_id in seen_ids or relative in seen_paths:
            raise QuestionVisualScanError(
                "question_visual_scan_evidence_invalid",
                "paper identity evidence contains a duplicate id or path",
            )
        seen_ids.add(crop_id)
        seen_paths.add(relative)
        binding = source_bindings.get(relative)
        raw = source_bytes.get(relative)
        if (
            binding is None
            or raw is None
            or binding.get("sha256") != expected_sha256
            or _sha256(raw) != expected_sha256
            or not isinstance(source_page, int)
            or isinstance(source_page, bool)
            or source_page < 1
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_evidence_invalid",
                "paper identity evidence hash or page drifted",
            )
        width, height = _image_dimensions(raw, "paper identity crop", "PNG")
        descriptors.append(
            {
                "crop_id": crop_id,
                "evidence_role": "shared_material",
                "source_page": source_page,
                "sha256": expected_sha256,
                "bytes": len(raw),
                "width": width,
                "height": height,
                "visual_inspection_status": "actually_viewed_by_primary_model",
            }
        )
    return descriptors


def _expected_parent_question_descriptors(
    spec: BatchSpec,
    source_bytes: dict[str, bytes],
    source_bindings: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for crop_id, relative, expected_sha256, source_page in spec.parent_question_evidence:
        if crop_id in seen_ids or relative in seen_paths:
            raise QuestionVisualScanError(
                "question_visual_scan_evidence_invalid",
                "parent-question evidence contains a duplicate id or path",
            )
        seen_ids.add(crop_id)
        seen_paths.add(relative)
        binding = source_bindings.get(relative)
        raw = source_bytes.get(relative)
        if (
            binding is None
            or raw is None
            or binding.get("sha256") != expected_sha256
            or _sha256(raw) != expected_sha256
            or not isinstance(source_page, int)
            or isinstance(source_page, bool)
            or source_page < 1
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_evidence_invalid",
                "parent-question evidence hash or page drifted",
            )
        width, height = _image_dimensions(raw, "parent-question crop", "PNG")
        descriptors.append(
            {
                "crop_id": crop_id,
                "evidence_role": "question_parent_context",
                "source_page": source_page,
                "sha256": expected_sha256,
                "bytes": len(raw),
                "width": width,
                "height": height,
                "visual_inspection_status": "actually_viewed_by_primary_model",
            }
        )
    return descriptors


def _reject_unsafe_projection(value: Any) -> None:
    if isinstance(value, dict):
        if any(
            str(key).casefold() in _FORBIDDEN_PROJECTION_KEYS for key in value
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_projection_unsafe",
                "question visual scan projection contains a forbidden field",
            )
        for item in value.values():
            _reject_unsafe_projection(item)
        return
    if isinstance(value, list) or isinstance(value, tuple):
        for item in value:
            _reject_unsafe_projection(item)
        return
    if isinstance(value, str):
        probe = value
        for _ in range(8):
            if _LOCAL_PATH_PATTERN.search(probe):
                raise QuestionVisualScanError(
                    "question_visual_scan_projection_unsafe",
                    "question visual scan projection contains a local path",
                )
            decoded = unquote(probe)
            if decoded == probe:
                break
            probe = decoded
        else:
            if _LOCAL_PATH_PATTERN.search(probe) or unquote(probe) != probe:
                raise QuestionVisualScanError(
                    "question_visual_scan_projection_unsafe",
                    "question visual scan projection is excessively encoded",
                )


def _source_predecessors(
    spec: BatchSpec,
    source_row: dict[str, Any],
    atomic_index: dict[str, dict[str, Any]],
) -> list[str]:
    raw = source_row.get("dependency", {}).get(
        "exact_predecessor_atomic_part_ids", []
    )
    if raw in (None, "", "blocked_pending_review"):
        values: list[Any] = []
    elif isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        raise QuestionVisualScanError(
            "question_visual_scan_dependency_invalid",
            "Wave1 predecessor field has an unsupported shape",
        )
    valid: list[str] = []
    for value in values:
        if value == "blocked_pending_review" and spec.filter_predecessor_placeholders:
            continue
        if not isinstance(value, str):
            raise QuestionVisualScanError(
                "question_visual_scan_dependency_invalid",
                "Wave1 predecessor identifier drifted",
            )
        if spec.filter_predecessor_placeholders and (
            not value.startswith(f"{spec.paper_id}-AP-") or value not in atomic_index
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_dependency_invalid",
                "Wave1 predecessor contains a non-batch identifier",
            )
        valid.append(value)
    overrides = dict(spec.predecessor_overrides)
    if len(overrides) != len(spec.predecessor_overrides):
        raise QuestionVisualScanError(
            "question_visual_scan_dependency_invalid",
            "frozen predecessor override contains a duplicate node",
        )
    node_id = source_row.get("atomic_part_id")
    if node_id in overrides:
        expected = list(overrides[node_id])
        if any(value not in expected for value in valid):
            raise QuestionVisualScanError(
                "question_visual_scan_dependency_invalid",
                "Wave1 predecessor conflicts with the frozen visual dependency",
            )
        if (
            len(expected) != len(set(expected))
            or any(
                not value.startswith(f"{spec.paper_id}-AP-")
                or value not in atomic_index
                for value in expected
            )
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_dependency_invalid",
                "frozen visual predecessor is not an exact batch atomic id",
            )
        valid = expected
    return valid


def _verify_qp_overlay(
    spec: BatchSpec,
    source_bytes: dict[str, bytes],
    source_bindings: dict[str, dict[str, Any]],
) -> None:
    if spec.paper_id != QP_BATCH_SPEC.paper_id:
        return
    paper_relative = f"{QP_OVERLAY_PREFIX}/paper_record.json"
    manifest_relative = f"{QP_OVERLAY_PREFIX}/manifest.json"
    if set(spec.extra_source_paths) != {paper_relative, manifest_relative}:
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid", "Qingpu overlay source set drifted"
        )
    paper = _json_object(source_bytes[paper_relative], "Qingpu overlay paper")
    overlay = _json_object(source_bytes[manifest_relative], "Qingpu overlay manifest")
    expected_identity = {
        "calendar_month": None,
        "district": "青浦区",
        "grade": "高三",
        "identity_basis": "卷面首页直接可见；月份未在卷面给出。",
        "paper_type": "二模",
        "year": 2026,
    }
    source = paper.get("source")
    if (
        paper.get("schema_version") != "1.0.0-overlay-v2-paper-record"
        or paper.get("record_type") != "paper_record"
        or paper.get("overlay_id")
        != "intake-complete-papers-overlay-v2-2026-08-03"
        or paper.get("package_id") != "qingpu_2026_second_mock_complete_paper"
        or not isinstance(source, dict)
        or source.get("identity") != expected_identity
        or source.get("official_status") != "nonofficial"
        or source.get("source_id")
        != "WX-LIVE-2026-QINGPU-SECOND-MOCK-SHENGXUE-INFO"
        or paper.get("source_id") != source.get("source_id")
        or source.get("answer_authority")
        != "NONOFFICIAL_AGGREGATOR_PRINTED_REFERENCE_ANSWER"
        or source.get("official_rubric_status") != "none"
        or source.get("rubric_authority") != "none"
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid",
            "Qingpu overlay paper identity or authority drifted",
        )
    _all_false(paper.get("gates"), "Qingpu overlay paper")
    if (
        overlay.get("schema_version") != "1.0.0-overlay-v2-package-manifest"
        or overlay.get("overlay_id") != paper["overlay_id"]
        or overlay.get("package_id") != paper["package_id"]
        or overlay.get("status")
        != "PASS_MACHINE_ONLY_CLASSIFICATION_OVERLAY_ALL_GATES_CLOSED"
        or overlay.get("source_identity") != source
        or overlay.get("paper_record")
        != "qingpu_2026_second_mock_complete_paper/paper_record.json"
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid",
            "Qingpu overlay manifest identity drifted",
        )
    _all_false(overlay.get("gates"), "Qingpu overlay manifest")
    descriptor = next(
        (
            item
            for item in overlay.get("output_files", [])
            if isinstance(item, dict) and item.get("path") == "paper_record.json"
        ),
        None,
    )
    expected_descriptor = {
        "path": "paper_record.json",
        "bytes": source_bindings[paper_relative]["bytes"],
        "sha256": source_bindings[paper_relative]["sha256"],
    }
    if descriptor != expected_descriptor:
        raise QuestionVisualScanError(
            "question_visual_scan_source_binding_mismatch",
            "Qingpu overlay paper hash cross-binding drifted",
        )


def _verify_pt_overlay(
    spec: BatchSpec,
    source_bytes: dict[str, bytes],
    source_bindings: dict[str, dict[str, Any]],
) -> None:
    if spec.paper_id != PT_BATCH_SPEC.paper_id:
        return
    paper_relative = f"{PT_OVERLAY_PREFIX}/paper_record.json"
    manifest_relative = f"{PT_OVERLAY_PREFIX}/manifest.json"
    if set(spec.extra_source_paths) != {paper_relative, manifest_relative}:
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid", "Putuo overlay source set drifted"
        )
    paper = _json_object(source_bytes[paper_relative], "Putuo overlay paper")
    overlay = _json_object(source_bytes[manifest_relative], "Putuo overlay manifest")
    expected_identity = {
        "academic_year": "2025学年",
        "calendar_year": 2026,
        "district": "普陀区",
        "grade": "高三",
        "semester": "第二学期",
        "exam_month": "2026-03",
        "paper_type": "二模",
        "paper_family": "second_mock",
        "official_status": "nonofficial_repost",
        "answer_authority": "nonofficial_reference",
        "official_rubric_status": "none",
        "source_account": "学教有方",
        "source_id": "WX-PUTUO-2026-03-SECOND-MOCK-COMPLETE",
        "printed_question_page_count": 10,
        "printed_answer_page_count": 2,
        "duration_minutes": 60,
        "declared_score": 100,
    }
    source = paper.get("source")
    if (
        paper.get("schema_version") != "1.0.0-overlay-v2-paper-record"
        or paper.get("record_type") != "paper_record"
        or paper.get("overlay_id")
        != "intake-complete-papers-overlay-v2-2026-08-03"
        or paper.get("package_id") != "putuo_2026_second_mock_complete_paper"
        or not isinstance(source, dict)
        or any(source.get(key) != value for key, value in expected_identity.items())
        or paper.get("source_id") != source.get("source_id")
        or source.get("answer_authority") != "nonofficial_reference"
        or source.get("official_rubric_status") != "none"
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid",
            "Putuo overlay paper identity or authority drifted",
        )
    _all_false(paper.get("gates"), "Putuo overlay paper")
    if (
        overlay.get("schema_version")
        != "1.0.0-overlay-v2-package-manifest"
        or overlay.get("overlay_id") != paper["overlay_id"]
        or overlay.get("package_id") != paper["package_id"]
        or overlay.get("status")
        != "PASS_MACHINE_ONLY_CLASSIFICATION_OVERLAY_ALL_GATES_CLOSED"
        or overlay.get("source_identity") != source
        or overlay.get("paper_record")
        != "putuo_2026_second_mock_complete_paper/paper_record.json"
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid",
            "Putuo overlay manifest identity drifted",
        )
    _all_false(overlay.get("gates"), "Putuo overlay manifest")
    descriptor = next(
        (
            item
            for item in overlay.get("output_files", [])
            if isinstance(item, dict) and item.get("path") == "paper_record.json"
        ),
        None,
    )
    expected_descriptor = {
        "path": "paper_record.json",
        "bytes": source_bindings[paper_relative]["bytes"],
        "sha256": source_bindings[paper_relative]["sha256"],
    }
    if descriptor != expected_descriptor:
        raise QuestionVisualScanError(
            "question_visual_scan_source_binding_mismatch",
            "Putuo overlay paper hash cross-binding drifted",
        )


def _verify_yp_overlay(
    spec: BatchSpec,
    source_bytes: dict[str, bytes],
    source_bindings: dict[str, dict[str, Any]],
) -> None:
    if spec.paper_id != YP_BATCH_SPEC.paper_id:
        return
    paper_relative = f"{YP_OVERLAY_PREFIX}/paper_record.json"
    manifest_relative = f"{YP_OVERLAY_PREFIX}/manifest.json"
    if set(spec.extra_source_paths) != {paper_relative, manifest_relative}:
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid", "Yangpu overlay source set drifted"
        )
    paper = _json_object(source_bytes[paper_relative], "Yangpu overlay paper")
    overlay = _json_object(source_bytes[manifest_relative], "Yangpu overlay manifest")
    expected_identity = {
        "academic_year": "2025学年度",
        "calendar_year": 2026,
        "district": "杨浦区",
        "grade": "高三",
        "semester": "第二学期",
        "exam_month": "2026-04",
        "paper_type": "二模",
        "paper_family": "second_mock",
        "official_status": "nonofficial_repost",
        "answer_authority": "nonofficial_reference",
        "official_rubric_status": "none",
        "source_account": "申教在线",
        "source_id": "WX-YANGPU-2026-04-SECOND-MOCK-COMPLETE",
        "printed_question_page_count": 8,
        "printed_answer_page_count": 3,
        "duration_minutes": 60,
        "declared_score": 100,
    }
    source = paper.get("source")
    if (
        paper.get("schema_version") != "1.0.0-overlay-v2-paper-record"
        or paper.get("record_type") != "paper_record"
        or paper.get("overlay_id")
        != "intake-complete-papers-overlay-v2-2026-08-03"
        or paper.get("package_id") != "yangpu_2026_second_mock_complete_paper"
        or not isinstance(source, dict)
        or any(source.get(key) != value for key, value in expected_identity.items())
        or paper.get("source_id") != source.get("source_id")
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid",
            "Yangpu overlay paper identity or authority drifted",
        )
    _all_false(paper.get("gates"), "Yangpu overlay paper")
    if (
        overlay.get("schema_version")
        != "1.0.0-overlay-v2-package-manifest"
        or overlay.get("overlay_id") != paper["overlay_id"]
        or overlay.get("package_id") != paper["package_id"]
        or overlay.get("status")
        != "PASS_MACHINE_ONLY_CLASSIFICATION_OVERLAY_ALL_GATES_CLOSED"
        or overlay.get("source_identity") != source
        or overlay.get("paper_record")
        != "yangpu_2026_second_mock_complete_paper/paper_record.json"
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid",
            "Yangpu overlay manifest identity drifted",
        )
    _all_false(overlay.get("gates"), "Yangpu overlay manifest")
    descriptor = next(
        (
            item
            for item in overlay.get("output_files", [])
            if isinstance(item, dict) and item.get("path") == "paper_record.json"
        ),
        None,
    )
    expected_descriptor = {
        "path": "paper_record.json",
        "bytes": source_bindings[paper_relative]["bytes"],
        "sha256": source_bindings[paper_relative]["sha256"],
    }
    if descriptor != expected_descriptor:
        raise QuestionVisualScanError(
            "question_visual_scan_source_binding_mismatch",
            "Yangpu overlay paper hash cross-binding drifted",
        )


def _verify_dt_overlay(
    spec: BatchSpec,
    source_bytes: dict[str, bytes],
    source_bindings: dict[str, dict[str, Any]],
) -> None:
    if spec.paper_id != DT_BATCH_SPEC.paper_id:
        return
    paper_relative = f"{DT_OVERLAY_PREFIX}/paper_record.json"
    manifest_relative = f"{DT_OVERLAY_PREFIX}/manifest.json"
    if not {paper_relative, manifest_relative}.issubset(spec.extra_source_paths):
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid", "Datong overlay source set drifted"
        )
    paper = _json_object(source_bytes[paper_relative], "Datong overlay paper")
    overlay = _json_object(source_bytes[manifest_relative], "Datong overlay manifest")
    required = {
        "academic_year": "2025学年",
        "calendar_year": 2025,
        "grade": "高一",
        "semester": "第一学期",
        "paper_face_exam_type": "期中考试试卷",
        "paper_family": "midterm",
        "source_type": "school_exam",
        "official_status": "nonofficial",
        "answer_authority": "unknown",
        "official_rubric_status": "none",
        "printed_page_count": 6,
        "duration_minutes": 60,
        "declared_score": 100,
        "school_attribution": "上海市大同中学",
        "school_attribution_status": "title_attribution_only",
        "exam_month": None,
        "exam_month_status": "not_visible_on_paper_or_article_title",
    }
    source = paper.get("source")
    if (
        paper.get("schema_version") != "1.0.0-overlay-v2-paper-record"
        or paper.get("record_type") != "paper_record"
        or paper.get("overlay_id")
        != "intake-complete-papers-overlay-v2-2026-08-03"
        or paper.get("package_id")
        != "datong_high1_2025_fall_midterm_complete_paper"
        or not isinstance(source, dict)
        or any(source.get(key) != value for key, value in required.items())
        or source.get("evidence_level")
        != "L2_PAGE_VERIFIED_SCHOOL_EXAM_WITH_TITLE_ONLY_SCHOOL_ATTRIBUTION"
        or source.get("school_identity_basis")
        != "wechat_article_title_user_accepted_title_attribution_only_not_visible_on_paper_face"
        or source.get("publication_month_not_exam_month") != "2025-11"
        or source.get("source_account") != "上海初高中化学"
        or paper.get("source_id") != source.get("source_id")
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid",
            "Datong school-exam identity or title-only attribution drifted",
        )
    _all_false(paper.get("gates"), "Datong overlay paper")
    if (
        overlay.get("schema_version")
        != "1.0.0-overlay-v2-package-manifest"
        or overlay.get("overlay_id") != paper["overlay_id"]
        or overlay.get("package_id") != paper["package_id"]
        or overlay.get("status")
        != "PASS_MACHINE_ONLY_CLASSIFICATION_OVERLAY_ALL_GATES_CLOSED"
        or overlay.get("source_identity") != source
        or overlay.get("paper_record")
        != "datong_high1_2025_fall_midterm_complete_paper/paper_record.json"
    ):
        raise QuestionVisualScanError(
            "question_visual_scan_source_invalid",
            "Datong overlay manifest identity drifted",
        )
    _all_false(overlay.get("gates"), "Datong overlay manifest")
    descriptor = next(
        (
            item
            for item in overlay.get("output_files", [])
            if isinstance(item, dict) and item.get("path") == "paper_record.json"
        ),
        None,
    )
    expected_descriptor = {
        "path": "paper_record.json",
        "bytes": source_bindings[paper_relative]["bytes"],
        "sha256": source_bindings[paper_relative]["sha256"],
    }
    if descriptor != expected_descriptor:
        raise QuestionVisualScanError(
            "question_visual_scan_source_binding_mismatch",
            "Datong overlay paper hash cross-binding drifted",
        )


class _BatchQuestionVisualScanReader:
    """Runtime-verified projection for exactly one frozen visual-scan batch."""

    def __init__(self, shchem_root: Path, spec: BatchSpec):
        self.spec = spec
        self.shchem_root = shchem_root.absolute()
        self.product_root = (self.shchem_root / spec.product_relative).absolute()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise QuestionVisualScanError(
                "question_visual_scan_path_escape",
                "question visual scan product escaped the chemistry root",
            )

    @staticmethod
    def _verify_wave1_manifest(
        source_bytes: dict[str, bytes], source_bindings: dict[str, dict[str, Any]]
    ) -> None:
        manifest = _json_object(source_bytes[WAVE1_MANIFEST], "Wave1 manifest")
        if (
            manifest.get("status")
            != "ISOLATED_CANDIDATE_NOT_FORMAL_NOT_RETRIEVAL_READY"
            or manifest.get("counts", {}).get("atomic_parts") != 252
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_source_invalid", "Wave1 source status drifted"
            )
        _all_false(manifest.get("gates"), "Wave1 manifest")
        artifacts = manifest.get("generated_artifacts")
        if not isinstance(artifacts, list):
            raise QuestionVisualScanError(
                "question_visual_scan_source_invalid", "Wave1 artifacts are missing"
            )
        index = {
            item.get("path"): item
            for item in artifacts
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        for relative in WAVE1_FILES.values():
            name = relative.removeprefix(f"{WAVE1_ROOT}/")
            upstream = index.get(name)
            bound = source_bindings[relative]
            if not isinstance(upstream, dict) or any(
                upstream.get(key) != bound[key] for key in ("bytes", "sha256")
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_source_binding_mismatch",
                    "Wave1 output and visual-scan source binding disagree",
                )

    @staticmethod
    def _validate_classification(
        record: dict[str, Any], allowed: dict[str, set[str]]
    ) -> None:
        classification = record.get("classification")
        if not isinstance(classification, dict) or set(classification) != {
            "item_type",
            "selection_rule",
            "primary_K",
            "supporting_K",
            "A",
            "C",
            "R",
            "RP",
        }:
            raise QuestionVisualScanError(
                "question_visual_scan_classification_invalid",
                "scan classification shape drifted",
            )
        if (
            classification["item_type"] not in allowed["item"]
            or classification["selection_rule"] not in allowed["selection"]
            or classification["primary_K"] not in allowed["K"]
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_classification_invalid",
                "scan classification vocabulary drifted",
            )
        for field in ("supporting_K", "A", "C", "R", "RP"):
            axis = "K" if field == "supporting_K" else field
            values = classification[field]
            if not isinstance(values, list) or not set(values).issubset(allowed[axis]):
                raise QuestionVisualScanError(
                    "question_visual_scan_classification_invalid",
                    f"scan classification {field} drifted",
                )

    @staticmethod
    def _validate_difficulty(
        record: dict[str, Any], viewed_ids: list[str], prior_ids: list[str], allowed_d: set[str]
    ) -> None:
        difficulty = record.get("difficulty")
        if not isinstance(difficulty, dict) or set(difficulty) != {
            "cognitive_prelabel",
            "is_measured",
            "measured_difficulty",
            "factors",
            "basis_zh",
        }:
            raise QuestionVisualScanError(
                "question_visual_scan_difficulty_invalid", "difficulty shape drifted"
            )
        if (
            difficulty["cognitive_prelabel"] not in allowed_d
            or difficulty["is_measured"] is not False
            or difficulty["measured_difficulty"] is not None
            or not isinstance(difficulty["basis_zh"], str)
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_difficulty_invalid",
                "difficulty authority or prelabel drifted",
            )
        factors = difficulty["factors"]
        if not isinstance(factors, list) or [
            item.get("dimension_id") if isinstance(item, dict) else None
            for item in factors
        ] != list(FACTOR_IDS):
            raise QuestionVisualScanError(
                "question_visual_scan_difficulty_invalid",
                "ten-factor order or cardinality drifted",
            )
        enum_values = {
            "calculation_load": {"none", "single_step", "multi_step"},
            "experiment_load": {"none", "interpret", "design", "evaluate"},
            "openness": {"closed", "semi_open", "open"},
            "unfamiliarity": {"familiar", "partly_unfamiliar", "unfamiliar"},
            "language_load": {"low", "medium", "high"},
            "dependency_on_prior_parts": {
                "independent",
                "one_prior_part",
                "multiple_prior_parts",
            },
        }
        integer_dimensions = set(FACTOR_IDS[:4])
        bases: set[str] = set()
        for factor in factors:
            if set(factor) != {
                "dimension_id",
                "value",
                "evidence_crop_ids",
                "basis_zh",
                "inference_zh",
                "confidence",
                "evidence_status",
                "source_atomic_part_ids",
            }:
                raise QuestionVisualScanError(
                    "question_visual_scan_difficulty_invalid",
                    "difficulty factor shape drifted",
                )
            dimension = factor["dimension_id"]
            value = factor["value"]
            confidence = factor["confidence"]
            if (
                factor["evidence_crop_ids"] != viewed_ids
                or factor["evidence_status"]
                != "actual_visual_evidence_model_inference_not_measured"
                or not isinstance(factor["basis_zh"], str)
                or len(factor["basis_zh"]) < 12
                or factor["basis_zh"] in bases
                or not isinstance(factor["inference_zh"], str)
                or len(factor["inference_zh"]) < 8
                or isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_difficulty_invalid",
                    "difficulty factor evidence drifted",
                )
            bases.add(factor["basis_zh"])
            if dimension in integer_dimensions and (
                type(value) is not int or value < 0
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_difficulty_invalid",
                    "difficulty integer factor drifted",
                )
            if dimension in enum_values and value not in enum_values[dimension]:
                raise QuestionVisualScanError(
                    "question_visual_scan_difficulty_invalid",
                    "difficulty enum factor drifted",
                )
            expected_sources = prior_ids if dimension == FACTOR_IDS[-1] else []
            if factor["source_atomic_part_ids"] != expected_sources:
                raise QuestionVisualScanError(
                    "question_visual_scan_difficulty_invalid",
                    "difficulty prior-part binding drifted",
                )

    @staticmethod
    def _integrity(snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "manifest_self_sha256": snapshot.manifest_self_sha256,
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "output_binding_count": snapshot.output_binding_count,
            "source_binding_count": snapshot.source_binding_count,
            "record_count": len(snapshot.records),
            "fail_closed": True,
        }

    def _validated_snapshot(self) -> _Snapshot:
        try:
            manifest_raw = _checked_exact_path(
                self.product_root, "manifest.json"
            ).read_bytes()
        except (OSError, ReadOnlyDataError) as exc:
            raise QuestionVisualScanError(
                "question_visual_scan_product_unavailable",
                "question visual scan product is unavailable",
            ) from exc
        manifest = _json_object(manifest_raw, "visual scan manifest")
        computed_self_hash = _manifest_self_hash(manifest)
        if (
            set(manifest)
            != {
                "product_id",
                "schema_version",
                "status",
                "paper_id",
                "fixed_counts",
                "source_manifest_binding",
                "output_bindings",
                "authority_gates",
                "manifest_self_hash_contract",
                "manifest_self_sha256",
            }
            or manifest.get("product_id") != self.spec.product_id
            or manifest.get("paper_id") != self.spec.paper_id
            or manifest.get("schema_version")
            != "1.0.0-question-visual-scan-manifest"
            or manifest.get("status")
            != "PASS_MODEL_VISUAL_SCAN_CANDIDATE_GATES_CLOSED"
            or manifest.get("fixed_counts") != self.spec.expected_fixed_counts
            or manifest.get("manifest_self_hash_contract")
            != "sha256(canonical UTF-8 JSON with manifest_self_sha256 set to null)"
            or manifest.get("manifest_self_sha256") != computed_self_hash
            or computed_self_hash != self.spec.expected_manifest_self_sha256
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_manifest_invalid",
                "question visual scan manifest, fixed counts, or self hash drifted",
            )
        _false_gates(manifest.get("authority_gates"), "visual scan manifest")

        output_bindings = _binding_index(
            manifest.get("output_bindings"),
            "visual scan output",
            expected_count=len(self.spec.expected_output_files),
        )
        if set(output_bindings) != self.spec.expected_output_files:
            raise QuestionVisualScanError(
                "question_visual_scan_binding_invalid", "visual scan output set drifted"
            )
        output_bytes = _verify_bound_bytes(
            self.product_root, output_bindings, "visual scan output"
        )
        source_manifest_binding = manifest.get("source_manifest_binding")
        if (
            not isinstance(source_manifest_binding, dict)
            or source_manifest_binding != output_bindings["source_manifest.json"]
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_binding_mismatch",
                "source manifest output cross-binding drifted",
            )
        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "visual scan source manifest"
        )
        expected_source_manifest_fields = {
            "product_id",
            "paper_id",
            "source_bindings",
            "rule_contract_bindings",
            "visual_inspection_declaration",
            "authority_gates",
        }
        if self.spec.paper_identity_evidence:
            expected_source_manifest_fields.add("paper_identity_visual_evidence")
        if self.spec.parent_question_evidence:
            expected_source_manifest_fields.add("parent_question_visual_evidence")
        if self.spec.source_identity is not None:
            expected_source_manifest_fields.add("source_identity")
        if self.spec.paper_identity_boundary is not None:
            expected_source_manifest_fields.add("paper_identity_boundary")
        if self.spec.require_answer_whole_pages_declaration:
            expected_source_manifest_fields.add("answer_whole_pages")
        expected_visual_declaration = self.spec.visual_inspection_declaration or {
            "question_crops_actually_viewed": self.spec.expected_fixed_counts[
                "unique_question_crops_actually_viewed"
            ],
            "shared_crops_actually_viewed": self.spec.expected_fixed_counts[
                "unique_shared_crops_actually_viewed"
            ],
            "answer_whole_pages_actually_viewed": self.spec.expected_fixed_counts[
                "answer_whole_pages_actually_viewed"
            ],
            "metadata_only_scan_forbidden": True,
            "ocr_is_authority": False,
        }
        if (
            set(source_manifest)
            != expected_source_manifest_fields
            or source_manifest.get("product_id") != self.spec.product_id
            or source_manifest.get("paper_id") != self.spec.paper_id
            or source_manifest.get("visual_inspection_declaration")
            != expected_visual_declaration
            or (
                self.spec.source_identity is not None
                and source_manifest.get("source_identity") != self.spec.source_identity
            )
            or (
                self.spec.paper_identity_boundary is not None
                and source_manifest.get("paper_identity_boundary")
                != self.spec.paper_identity_boundary
            )
            or (
                self.spec.require_answer_whole_pages_declaration
                and source_manifest.get("answer_whole_pages") != []
            )
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_source_invalid", "source manifest drifted"
            )
        _false_gates(source_manifest.get("authority_gates"), "source manifest")
        source_bindings = _binding_index(
            source_manifest.get("source_bindings"),
            "visual scan source",
            expected_count=self.spec.expected_source_binding_count,
        )
        source_bytes = _verify_bound_bytes(
            self.shchem_root, source_bindings, "visual scan source"
        )
        rule_contracts = _expected_rule_contracts(
            self.spec, source_bytes, source_bindings
        )
        if (
            source_manifest.get("rule_contract_bindings") != rule_contracts
            or any(
                marker in rule_id.upper()
                for rule_id in self.spec.rule_paths
                for marker in self.spec.forbidden_rule_markers
            )
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_rule_invalid",
                "source manifest rule contracts drifted",
            )
        identity_descriptors = _expected_paper_identity_descriptors(
            self.spec, source_bytes, source_bindings
        )
        if self.spec.paper_identity_evidence and source_manifest.get(
            "paper_identity_visual_evidence"
        ) != identity_descriptors:
            raise QuestionVisualScanError(
                "question_visual_scan_evidence_invalid",
                "paper identity evidence descriptors drifted",
            )
        parent_question_descriptors = _expected_parent_question_descriptors(
            self.spec, source_bytes, source_bindings
        )
        if self.spec.parent_question_evidence and source_manifest.get(
            "parent_question_visual_evidence"
        ) != parent_question_descriptors:
            raise QuestionVisualScanError(
                "question_visual_scan_evidence_invalid",
                "parent-question evidence descriptors drifted",
            )
        self._verify_wave1_manifest(source_bytes, source_bindings)
        _verify_qp_overlay(self.spec, source_bytes, source_bindings)
        _verify_pt_overlay(self.spec, source_bytes, source_bindings)
        _verify_yp_overlay(self.spec, source_bytes, source_bindings)
        _verify_dt_overlay(self.spec, source_bytes, source_bindings)

        atomic_rows = [
            row
            for row in _jsonl_rows(source_bytes[WAVE1_FILES["atomic"]], "Wave1 atomic")
            if row.get("paper_id") == self.spec.paper_id
        ]
        theme_rows = [
            row
            for row in _jsonl_rows(source_bytes[WAVE1_FILES["theme"]], "Wave1 theme")
            if row.get("paper_id") == self.spec.paper_id
        ]
        printed_rows = [
            row
            for row in _jsonl_rows(
                source_bytes[WAVE1_FILES["printed"]], "Wave1 printed"
            )
            if row.get("paper_id") == self.spec.paper_id
        ]
        if (
            len(atomic_rows) != self.spec.expected_atomic_count
            or len(theme_rows) != self.spec.expected_theme_count
            or len(printed_rows) != self.spec.expected_printed_count
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_count_mismatch",
                "Wave1 batch hierarchy count drifted",
            )
        expected_sources = (
            set(self.spec.rule_paths)
            | set(WAVE1_CORE)
            | set(self.spec.answer_pages)
            | set(self.spec.extra_source_paths)
            | {
                relative
                for _, relative, _, _ in self.spec.paper_identity_evidence
            }
            | {
                relative
                for _, relative, _, _ in self.spec.parent_question_evidence
            }
        )
        question_descriptor_paths: set[str] = set()
        question_descriptor_ids: set[str] = set()
        shared_descriptor_paths: set[str] = set()
        shared_descriptor_ids: set[str] = set()
        for source_row in atomic_rows:
            for field, paths, crop_ids in (
                (
                    "question_evidence",
                    question_descriptor_paths,
                    question_descriptor_ids,
                ),
                (
                    "shared_material_evidence",
                    shared_descriptor_paths,
                    shared_descriptor_ids,
                ),
            ):
                for evidence in source_row.get(field, []):
                    if (
                        not isinstance(evidence, dict)
                        or not isinstance(evidence.get("crop_path"), str)
                        or not isinstance(evidence.get("crop_id"), str)
                    ):
                        raise QuestionVisualScanError(
                            "question_visual_scan_evidence_invalid",
                            "Wave1 evidence descriptor drifted",
                        )
                    expected_sources.add(evidence["crop_path"])
                    paths.add(evidence["crop_path"])
                    crop_ids.add(evidence["crop_id"])
            for evidence in source_row.get("answer_candidate", {}).get(
                "exact_image_evidence", []
            ):
                if not isinstance(evidence, dict) or not isinstance(
                    evidence.get("crop_path"), str
                ):
                    raise QuestionVisualScanError(
                        "question_visual_scan_evidence_invalid",
                        "Wave1 evidence descriptor drifted",
                    )
                expected_sources.add(evidence["crop_path"])
        if (
            self.spec.expected_question_descriptor_count is not None
            and (
                len(question_descriptor_paths)
                != self.spec.expected_question_descriptor_count
                or len(question_descriptor_ids)
                != self.spec.expected_question_descriptor_count
            )
        ) or (
            self.spec.expected_atomic_shared_descriptor_count is not None
            and (
                len(shared_descriptor_paths)
                != self.spec.expected_atomic_shared_descriptor_count
                or len(shared_descriptor_ids)
                != self.spec.expected_atomic_shared_descriptor_count
            )
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_count_mismatch",
                "batch question/shared evidence descriptor count drifted",
            )
        if set(source_bindings) != expected_sources:
            raise QuestionVisualScanError(
                "question_visual_scan_binding_invalid", "exact source set drifted"
            )

        vocab = _json_object(
            source_bytes[self.spec.rule_paths[1]], "controlled vocabulary"
        )
        contract = vocab.get("taxonomy_contract")
        if not isinstance(contract, dict):
            raise QuestionVisualScanError(
                "question_visual_scan_rule_invalid", "vocabulary contract drifted"
            )
        allowed = {
            "K": set(contract.get("knowledge_K", [])),
            "A": set(contract.get("ability_A", [])),
            "C": set(contract.get("context_C", [])),
            "R": set(contract.get("central_response_R", [])),
            "RP": set(vocab.get("representation_R", [])),
            "D": set(contract.get("difficulty_D", [])) | {"unknown"},
            "item": set(vocab.get("item_types", [])),
            "selection": set(vocab.get("selection_rules", [])),
        }
        if any(not values for values in allowed.values()):
            raise QuestionVisualScanError(
                "question_visual_scan_rule_invalid", "vocabulary values drifted"
            )

        theme_order = {
            row["theme_big_question_id"]: index
            for index, row in enumerate(theme_rows, 1)
        }
        printed_by_theme: dict[str, list[str]] = defaultdict(list)
        for row in printed_rows:
            printed_by_theme[row["theme_big_question_id"]].append(
                row["printed_question_id"]
            )
        printed_order = {
            node_id: index
            for values in printed_by_theme.values()
            for index, node_id in enumerate(values, 1)
        }
        atomic_by_printed: dict[str, list[str]] = defaultdict(list)
        for row in atomic_rows:
            atomic_by_printed[row["printed_question_id"]].append(
                row["atomic_part_id"]
            )
        expected_theme_atomic_counts = dict(
            self.spec.expected_theme_atomic_counts
        )
        expected_theme_printed_counts = dict(
            self.spec.expected_theme_printed_counts
        )
        if (
            len(expected_theme_atomic_counts)
            != len(self.spec.expected_theme_atomic_counts)
            or len(expected_theme_printed_counts)
            != len(self.spec.expected_theme_printed_counts)
            or (
                expected_theme_atomic_counts
                and Counter(
                    row["theme_big_question_id"] for row in atomic_rows
                )
                != Counter(expected_theme_atomic_counts)
            )
            or (
                expected_theme_printed_counts
                and Counter(
                    row["theme_big_question_id"] for row in printed_rows
                )
                != Counter(expected_theme_printed_counts)
            )
            or (
                self.spec.require_printed_parent_coverage
                and set(atomic_by_printed)
                != {row["printed_question_id"] for row in printed_rows}
            )
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_count_mismatch",
                "batch theme distribution or printed-parent coverage drifted",
            )
        if self.spec.require_printed_atomic_one_to_one and (
            set(atomic_by_printed)
            != {row["printed_question_id"] for row in printed_rows}
            or any(len(values) != 1 for values in atomic_by_printed.values())
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_count_mismatch",
                "batch printed/atomic one-to-one binding drifted",
            )
        atomic_order = {
            node_id: index
            for values in atomic_by_printed.values()
            for index, node_id in enumerate(values, 1)
        }
        atomic_index = {row["atomic_part_id"]: row for row in atomic_rows}
        if len(atomic_index) != len(atomic_rows):
            raise QuestionVisualScanError(
                "question_visual_scan_order_invalid",
                "Wave1 batch contains duplicate atomic identifiers",
            )
        predecessor_overrides = dict(self.spec.predecessor_overrides)
        if (
            len(predecessor_overrides) != len(self.spec.predecessor_overrides)
            or not set(predecessor_overrides).issubset(atomic_index)
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_dependency_invalid",
                "frozen predecessor override node set drifted",
            )

        records = _jsonl_rows(output_bytes["scan_records.jsonl"], "visual scan records")
        if len(records) != self.spec.expected_atomic_count:
            raise QuestionVisualScanError(
                "question_visual_scan_count_mismatch", "scan record count drifted"
            )
        by_node_id: dict[str, dict[str, Any]] = {}
        compare_counts: Counter[str] = Counter()
        field_counts: dict[str, Counter[str]] = defaultdict(Counter)
        expected_answer_availability = Counter(
            dict(self.spec.answer_availability_counts)
        )
        if (
            len(expected_answer_availability)
            != len(self.spec.answer_availability_counts)
            or sum(expected_answer_availability.values())
            != self.spec.expected_atomic_count
            or not set(expected_answer_availability).issubset(
                {"present_part_aligned", "present_unaligned", "absent"}
            )
            or (
                self.spec.answer_evidence_required
                and "absent" in expected_answer_availability
            )
            or (
                not self.spec.answer_evidence_required
                and set(expected_answer_availability) != {"absent"}
            )
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_answer_boundary_invalid",
                "frozen answer availability contract drifted",
            )
        answer_availability: Counter[str] = Counter()
        question_crops: set[str] = set()
        shared_crops: set[str] = {
            descriptor["sha256"] for descriptor in identity_descriptors
        }
        answer_pages: set[str] = set()
        summaries: set[str] = set()
        corrected_fields_by_node: list[dict[str, Any]] = []

        for index, (record, source_row) in enumerate(
            zip(records, atomic_rows, strict=True), 1
        ):
            hierarchy = record.get("hierarchy")
            node_id = hierarchy.get("atomic_part_id") if isinstance(hierarchy, dict) else None
            try:
                validate_identifier(node_id, "node_id")
            except (SecurityError, TypeError) as exc:
                raise QuestionVisualScanError(
                    "question_visual_scan_record_invalid", "scan node identifier drifted"
                ) from exc
            if node_id in by_node_id or node_id != source_row.get("atomic_part_id"):
                raise QuestionVisualScanError(
                    "question_visual_scan_order_invalid",
                    "scan record order or identity drifted",
                )
            by_node_id[node_id] = record
            expected_hierarchy = {
                "paper_id": self.spec.paper_id,
                "theme_id": source_row["theme_big_question_id"],
                "printed_question_id": source_row["printed_question_id"],
                "atomic_part_id": node_id,
                "paper_sequence": index,
                "theme_sequence": theme_order[source_row["theme_big_question_id"]],
                "printed_sequence": printed_order[source_row["printed_question_id"]],
                "atomic_sequence_in_printed": atomic_order[node_id],
                "printed_subpart_label": source_row.get("printed_subpart_label"),
                "sequence_basis": "exact Wave1 manifest-bound JSONL order; no identifier suffix inference",
            }
            if (
                record.get("schema_version")
                != "1.0.0-question-visual-scan-candidate"
                or record.get("scan_id")
                != f"{self.spec.scan_id_prefix}{index:02d}"
                or record.get("scan_status") != "visual_scan_completed"
                or record.get("visual_scan_completed") is not True
                or record.get("source")
                != {
                    "paper_id": self.spec.paper_id,
                    "source_namespace": self.spec.source_namespace,
                    "official_status": "nonofficial",
                }
                or hierarchy != expected_hierarchy
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_record_invalid",
                    "scan status, source, hierarchy, or sequence drifted",
                )
            _false_gates(record.get("authority_gates"), f"scan record {node_id}")
            _all_false(source_row.get("gates"), f"Wave1 source row {node_id}")

            dependency = record.get("dependency")
            shared_ids = [
                item["crop_id"]
                for item in source_row.get("shared_material_evidence", [])
            ]
            prior_ids = _source_predecessors(
                self.spec, source_row, atomic_index
            )
            preceding_ids = {
                row["atomic_part_id"] for row in atomic_rows[: index - 1]
            }
            if (
                not isinstance(dependency, dict)
                or set(dependency)
                != {
                    "shared_material_crop_ids",
                    "prior_atomic_part_ids",
                    "analysis_zh",
                }
                or dependency["shared_material_crop_ids"] != shared_ids
                or dependency["prior_atomic_part_ids"] != prior_ids
                or any(value not in preceding_ids for value in prior_ids)
                or not isinstance(dependency["analysis_zh"], str)
                or len(dependency["analysis_zh"]) < 8
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_dependency_invalid",
                    "scan dependency binding drifted",
                )

            expected_evidence: list[dict[str, Any]] = []
            for role, field in (
                ("question", "question_evidence"),
                ("shared_material", "shared_material_evidence"),
            ):
                for evidence in source_row.get(field, []):
                    relative = evidence["crop_path"]
                    raw = source_bytes[relative]
                    width, height = _png_dimensions(raw, f"{role} crop")
                    if _sha256(raw) != evidence.get("crop_sha256"):
                        raise QuestionVisualScanError(
                            "question_visual_scan_evidence_invalid",
                            "question/shared crop SHA drifted",
                        )
                    expected_evidence.append(
                        {
                            "crop_id": evidence["crop_id"],
                            "evidence_role": role,
                            "source_page": evidence["source_page_number"],
                            "sha256": evidence["crop_sha256"],
                            "bytes": len(raw),
                            "width": width,
                            "height": height,
                            "visual_inspection_status": "actually_viewed_by_primary_model",
                        }
                    )
            if record.get("viewed_evidence") != expected_evidence:
                raise QuestionVisualScanError(
                    "question_visual_scan_evidence_invalid",
                    "record-local question/shared evidence drifted",
                )
            for evidence in expected_evidence:
                target = question_crops if evidence["evidence_role"] == "question" else shared_crops
                target.add(evidence["sha256"])
            viewed_ids = [item["crop_id"] for item in expected_evidence]

            source_answer = source_row.get("answer_candidate")
            answer = record.get("answer")
            source_availability = (
                source_answer.get("availability")
                if isinstance(source_answer, dict)
                else None
            )
            if (
                not isinstance(source_answer, dict)
                or source_availability not in expected_answer_availability
                or source_answer.get("authority") != self.spec.answer_authority
                or source_answer.get("answer_verified") is not False
                or source_answer.get("official_answer_claim_allowed") is not False
                or not isinstance(source_answer.get("exact_image_evidence"), list)
                or not isinstance(answer, dict)
                or set(answer)
                != {
                    "availability",
                    "authority",
                    "alignment_status",
                    "answer_verified",
                    "official_answer_claim_allowed",
                    "reference_summary_zh",
                    "visual_alignment_evidence",
                }
                or answer.get("availability") != source_availability
                or answer.get("authority") != self.spec.answer_authority
                or answer.get("alignment_status")
                != self.spec.answer_alignment_status
                or answer.get("answer_verified") is not False
                or answer.get("official_answer_claim_allowed") is not False
                or not isinstance(answer.get("reference_summary_zh"), str)
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_answer_boundary_invalid",
                    "answer availability or authority boundary drifted",
                )
            answer_availability[source_availability] += 1
            expected_answer_evidence: list[dict[str, Any]] = []
            for evidence in source_answer.get("exact_image_evidence", []):
                relative = evidence["crop_path"]
                raw = source_bytes[relative]
                width, height = _png_dimensions(raw, "answer crop")
                whole_page = evidence.get("whole_page_path")
                if (
                    _sha256(raw) != evidence.get("crop_sha256")
                    or whole_page not in self.spec.answer_pages
                    or evidence.get("original_source_path") != whole_page
                    or whole_page not in source_bytes
                    or _sha256(source_bytes[whole_page])
                    != evidence.get("whole_page_sha256")
                    or _sha256(source_bytes[whole_page])
                    != evidence.get("original_source_sha256")
                ):
                    raise QuestionVisualScanError(
                        "question_visual_scan_answer_boundary_invalid",
                        "answer crop or whole-page binding drifted",
                    )
                _image_dimensions(
                    source_bytes[whole_page],
                    "answer whole page",
                    self.spec.answer_page_format,
                )
                answer_pages.add(whole_page)
                expected_answer_evidence.append(
                    {
                        "crop_id": evidence["crop_id"],
                        "source_page": evidence["source_page_number"],
                        "sha256": evidence["crop_sha256"],
                        "bytes": len(raw),
                        "width": width,
                        "height": height,
                        "visual_inspection_status": "aligned_via_actually_viewed_whole_answer_page",
                    }
                )
            if self.spec.answer_evidence_required and not expected_answer_evidence:
                raise QuestionVisualScanError(
                    "question_visual_scan_answer_boundary_invalid",
                    "answer alignment evidence is missing",
                )
            if (
                not self.spec.answer_evidence_required
                and (
                    source_answer["exact_image_evidence"] != []
                    or expected_answer_evidence
                    or answer["visual_alignment_evidence"] != []
                )
            ) or (
                self.spec.answer_evidence_required
                and answer["visual_alignment_evidence"] != expected_answer_evidence
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_answer_boundary_invalid",
                    "answer alignment evidence drifted",
                )

            self._validate_classification(record, allowed)
            self._validate_difficulty(record, viewed_ids, prior_ids, allowed["D"])
            candidate = record.get("candidate_analysis")
            if (
                not isinstance(candidate, dict)
                or set(candidate)
                != {"solution_path_zh", "candidate_only", "correctness_verified"}
                or not isinstance(candidate["solution_path_zh"], list)
                or not candidate["solution_path_zh"]
                or candidate["candidate_only"] is not True
                or candidate["correctness_verified"] is not False
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_candidate_analysis_invalid",
                    "candidate analysis boundary drifted",
                )
            if record.get("rule_bindings") != rule_contracts:
                raise QuestionVisualScanError(
                    "question_visual_scan_rule_invalid", "record rule binding drifted"
                )
            if record.get("evidence_binding_sha256") != _evidence_binding_hash(record):
                raise QuestionVisualScanError(
                    "question_visual_scan_evidence_invalid",
                    "record evidence binding hash drifted",
                )
            summary = record.get("visible_summary_zh")
            if (
                not isinstance(summary, str)
                or len(summary) < 12
                or summary in summaries
                or not isinstance(record.get("response_requirement_zh"), str)
                or len(record["response_requirement_zh"]) < 8
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_record_invalid",
                    "question-specific Chinese summary drifted",
                )
            summaries.add(summary)

            comparisons = record.get("comparison_with_wave1")
            if not isinstance(comparisons, dict) or set(comparisons) != set(
                COMPARISON_FIELDS
            ):
                raise QuestionVisualScanError(
                    "question_visual_scan_comparison_invalid",
                    "comparison field set drifted",
                )
            old_values = _source_comparison_values(source_row)
            scan_values = _scan_comparison_values(record)
            corrected_fields: list[str] = []
            for field in COMPARISON_FIELDS:
                comparison = comparisons[field]
                result, confidence = _expected_compare(
                    old_values[field], scan_values[field]
                )
                if (
                    not isinstance(comparison, dict)
                    or set(comparison)
                    != {"old", "scan", "compare", "reason_zh", "confidence"}
                    or comparison.get("old") != old_values[field]
                    or comparison.get("scan") != scan_values[field]
                    or comparison.get("compare") != result
                    or comparison.get("confidence") != confidence
                    or not isinstance(comparison.get("reason_zh"), str)
                    or len(comparison["reason_zh"]) < 12
                ):
                    raise QuestionVisualScanError(
                        "question_visual_scan_comparison_invalid",
                        "comparison source, scan, or derivation drifted",
                    )
                compare_counts[result] += 1
                field_counts[field][result] += 1
                if result == "corrected":
                    corrected_fields.append(field)
            corrected_fields_by_node.append(
                {"node_id": node_id, "corrected_fields": corrected_fields}
            )

        if (
            len(by_node_id) != self.spec.expected_atomic_count
            or len(question_crops)
            != (
                self.spec.expected_question_evidence_sha_count
                if self.spec.expected_question_evidence_sha_count is not None
                else self.spec.expected_fixed_counts[
                    "unique_question_crops_actually_viewed"
                ]
            )
            or len(shared_crops)
            != self.spec.expected_fixed_counts[
                "unique_shared_crops_actually_viewed"
            ]
            or answer_pages != set(self.spec.answer_pages)
            or answer_availability != expected_answer_availability
            or compare_counts["agree"] != self.spec.compare_agree
            or compare_counts["corrected"] != self.spec.compare_corrected
            or compare_counts["blocked"] != self.spec.compare_blocked
            or sum(bool(item["corrected_fields"]) for item in corrected_fields_by_node)
            != self.spec.corrected_nodes
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_count_mismatch",
                "record, evidence, comparison, or corrected-node count drifted",
            )

        coverage = _json_object(
            output_bytes["coverage_report.json"], "visual scan coverage report"
        )
        expected_field_coverage = {
            field: self.spec.expected_atomic_count
            for field in (
                "visible_summary_zh",
                "response_requirement_zh",
                "classification",
                "difficulty",
                "chemistry_observations",
                "candidate_analysis",
                "answer",
                "risks_and_limits",
                "comparison_with_wave1",
            )
        }
        if (
            coverage.get("product_id") != self.spec.product_id
            or coverage.get("paper_id") != self.spec.paper_id
            or coverage.get("counts") != self.spec.expected_fixed_counts
            or coverage.get("field_coverage") != expected_field_coverage
            or coverage.get("unscanned_atomic_part_ids") != []
            or coverage.get("all_authority_gates_false") is not True
            or coverage.get("claim_boundary_zh")
            != "模型逐题视觉扫描候选，不等于真人化学复核、官方答案、实测难度、检索、教学、生成或发布许可。"
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_report_invalid", "coverage report drifted"
            )
        disagreement = _json_object(
            output_bytes["disagreement_report.json"],
            "visual scan disagreement report",
        )
        _false_gates(disagreement.get("authority_gates"), "disagreement report")
        derived_overall = dict(sorted(compare_counts.items()))
        derived_by_field = {
            field: dict(sorted(counts.items()))
            for field, counts in sorted(field_counts.items())
        }
        expected_corrected = [
            {
                "atomic_part_id": item["node_id"],
                "fields": item["corrected_fields"],
            }
            for item in corrected_fields_by_node
            if item["corrected_fields"]
        ]
        if (
            disagreement.get("product_id") != self.spec.product_id
            or disagreement.get("paper_id") != self.spec.paper_id
            or disagreement.get("overall_compare_counts") != derived_overall
            or disagreement.get("by_field") != derived_by_field
            or disagreement.get("corrected_records") != expected_corrected
            or disagreement.get("blocked_records") != []
        ):
            raise QuestionVisualScanError(
                "question_visual_scan_report_invalid",
                "disagreement report drifted",
            )
        return _Snapshot(
            manifest_self_sha256=computed_self_hash,
            output_binding_count=len(output_bindings),
            source_binding_count=len(source_bindings),
            records=tuple(records),
            by_node_id=by_node_id,
            corrected_fields_by_node=tuple(corrected_fields_by_node),
        )

    def _snapshot(self) -> _Snapshot:
        try:
            return self._validated_snapshot()
        except QuestionVisualScanError:
            raise
        except (KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
            raise QuestionVisualScanError(
                "question_visual_scan_data_invalid",
                "question visual scan structural invariant failed closed",
            ) from exc

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        response = {
            "product_id": self.spec.product_id,
            "scope": SCOPE,
            "paper_id": self.spec.paper_id,
            "counts": self.spec.status_counts,
            "node_ids": [record["hierarchy"]["atomic_part_id"] for record in snapshot.records],
            "corrected_fields_by_node": deepcopy(
                list(snapshot.corrected_fields_by_node)
            ),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }
        _reject_unsafe_projection(response)
        return response

    def detail(
        self, node_id: str, *, snapshot: _Snapshot | None = None
    ) -> dict[str, Any]:
        try:
            validate_identifier(node_id, "node_id")
        except (SecurityError, TypeError) as exc:
            raise QuestionVisualScanError(
                "question_visual_scan_invalid_node_id",
                "question visual scan node_id is invalid",
                400,
            ) from exc
        snapshot = snapshot or self._snapshot()
        record = snapshot.by_node_id.get(node_id)
        if record is None:
            raise QuestionVisualScanError(
                "question_visual_scan_node_not_found",
                "question visual scan node was not found",
                404,
            )
        answer = record["answer"]
        candidate = record["candidate_analysis"]
        response = {
            "product_id": self.spec.product_id,
            "scope": SCOPE,
            "paper_id": self.spec.paper_id,
            "node_id": node_id,
            "scan_status": record["scan_status"],
            "hierarchy": deepcopy(record["hierarchy"]),
            "visible_summary_zh": record["visible_summary_zh"],
            "response_requirement_zh": record["response_requirement_zh"],
            "dependency": deepcopy(record["dependency"]),
            "scan_classification": deepcopy(record["classification"]),
            "cognitive_difficulty": deepcopy(record["difficulty"]),
            "chemistry_observations": deepcopy(record["chemistry_observations"]),
            "model_candidate_analysis": {
                "review_state_zh": "待复核候选分析",
                "solution_path_zh": deepcopy(candidate["solution_path_zh"]),
                "candidate_only": True,
                "correctness_verified": False,
            },
            "risks_and_limits": deepcopy(record["risks_and_limits"]),
            "comparison_with_wave1": deepcopy(record["comparison_with_wave1"]),
            "evidence_descriptors": deepcopy(record["viewed_evidence"]),
            "answer_boundary": {
                "availability": answer["availability"],
                "authority": answer["authority"],
                "verified": False,
            },
            "reference_answer": project_reference_answer(
                answer, record["risks_and_limits"]
            ),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }
        try:
            supplement = answer_for_scan(response)
        except ValueError as exc:
            raise QuestionVisualScanError("supplemental_answer_source_drift", str(exc)) from exc
        if supplement is not None:
            response["supplemental_answer"] = supplement
        try:
            response["evidence_descriptors"] = [
                project_datong_descriptor(self.shchem_root, item)
                for item in response["evidence_descriptors"]
            ]
        except SourceCropRevisionError as exc:
            raise QuestionVisualScanError("question_visual_scan_presentation_invalid", str(exc)) from exc
        _reject_unsafe_projection(response)
        return response


def _runtime_batch_specs() -> tuple[BatchSpec, ...]:
    specs: list[BatchSpec] = []
    for spec in BATCH_SPECS:
        if spec is XH_BATCH_SPEC:
            # Preserve legacy tests/callers that pin the historical module
            # constants while keeping the immutable BatchSpec as the default.
            spec = replace(
                spec,
                product_relative=PRODUCT_RELATIVE,
                expected_manifest_self_sha256=EXPECTED_MANIFEST_SELF_SHA256,
                expected_fixed_counts=EXPECTED_FIXED_COUNTS,
                expected_source_binding_count=EXPECTED_SOURCE_BINDING_COUNT,
                rule_paths=RULE_PATHS,
                answer_pages=ANSWER_PAGES,
            )
        specs.append(spec)
    return tuple(specs)


class QuestionVisualScanReader:
    """Fail-closed catalog and node dispatcher across frozen scan batches."""

    def __init__(self, shchem_root: Path):
        self.shchem_root = shchem_root.absolute()
        self._batch_readers = tuple(
            _BatchQuestionVisualScanReader(self.shchem_root, spec)
            for spec in _runtime_batch_specs()
        )
        if not self._batch_readers or self._batch_readers[0].spec.product_id != PRODUCT_ID:
            raise QuestionVisualScanError(
                "question_visual_scan_batch_spec_invalid",
                "legacy XH visual scan batch must remain first",
            )

    @staticmethod
    def _batch_status(
        reader: _BatchQuestionVisualScanReader, snapshot: _Snapshot
    ) -> dict[str, Any]:
        return {
            "product_id": reader.spec.product_id,
            "paper_id": reader.spec.paper_id,
            "counts": reader.spec.status_counts,
            "node_ids": [
                record["hierarchy"]["atomic_part_id"]
                for record in snapshot.records
            ],
            "corrected_fields_by_node": deepcopy(
                list(snapshot.corrected_fields_by_node)
            ),
            "reference_answer_by_node": [
                {
                    "node_id": record["hierarchy"]["atomic_part_id"],
                    **reference_answer_catalog_metadata(
                        record["answer"], record["risks_and_limits"]
                    ),
                }
                for record in snapshot.records
            ],
            "authority": dict(AUTHORITY),
            "integrity": reader._integrity(snapshot),
        }

    def _validated_catalog(
        self,
    ) -> tuple[tuple[_BatchQuestionVisualScanReader, _Snapshot], ...]:
        batches: list[tuple[_BatchQuestionVisualScanReader, _Snapshot]] = []
        owners: dict[str, str] = {}
        for reader in self._batch_readers:
            snapshot = reader._snapshot()
            for node_id in snapshot.by_node_id:
                previous = owners.get(node_id)
                if previous is not None:
                    raise QuestionVisualScanError(
                        "question_visual_scan_node_conflict",
                        "a visual scan node is owned by multiple frozen batches",
                    )
                owners[node_id] = reader.spec.product_id
            batches.append((reader, snapshot))
        if len(batches) != 5 or len(owners) != 252:
            raise QuestionVisualScanError(
                "question_visual_scan_catalog_count_mismatch",
                "visual scan catalog batch or unique-node count drifted",
            )
        return tuple(batches)

    def status(self) -> dict[str, Any]:
        # Backward-compatible endpoint: validate/project only the historical
        # XH batch and return its exact pre-catalog DTO.
        return self._batch_readers[0].status()

    def catalog(self) -> dict[str, Any]:
        validated = self._validated_catalog()
        batch_payloads = [
            self._batch_status(reader, snapshot)
            for reader, snapshot in validated
        ]
        node_ids = [
            node_id
            for batch in batch_payloads
            for node_id in batch["node_ids"]
        ]
        response = {
            "scope": SCOPE,
            "counts": {
                "batches": len(batch_payloads),
                "unique_nodes": len(node_ids),
                "visual_scan_completed": sum(
                    batch["counts"]["visual_scan_completed"]
                    for batch in batch_payloads
                ),
                "compare_agree": sum(
                    batch["counts"]["compare_agree"] for batch in batch_payloads
                ),
                "compare_corrected": sum(
                    batch["counts"]["compare_corrected"]
                    for batch in batch_payloads
                ),
                "compare_blocked": sum(
                    batch["counts"]["compare_blocked"]
                    for batch in batch_payloads
                ),
                "corrected_nodes": sum(
                    batch["counts"]["corrected_nodes"]
                    for batch in batch_payloads
                ),
            },
            "batches": batch_payloads,
            "node_ids": node_ids,
            "authority": dict(AUTHORITY),
            "integrity": {
                "batch_count": len(batch_payloads),
                "unique_node_count": len(node_ids),
                "all_batches_hash_verified_on_read": True,
                "all_batches_semantic_invariants_verified_on_read": True,
                "fail_closed": True,
            },
        }
        _reject_unsafe_projection(response)
        return response

    def detail(self, node_id: str) -> dict[str, Any]:
        try:
            validate_identifier(node_id, "node_id")
        except (SecurityError, TypeError) as exc:
            raise QuestionVisualScanError(
                "question_visual_scan_invalid_node_id",
                "question visual scan node_id is invalid",
                400,
            ) from exc
        validated = self._validated_catalog()
        matches = [
            (reader, snapshot)
            for reader, snapshot in validated
            if node_id in snapshot.by_node_id
        ]
        if len(matches) > 1:
            raise QuestionVisualScanError(
                "question_visual_scan_node_conflict",
                "a visual scan node is owned by multiple frozen batches",
            )
        if not matches:
            raise QuestionVisualScanError(
                "question_visual_scan_node_not_found",
                "question visual scan node was not found",
                404,
            )
        reader, snapshot = matches[0]
        # The catalog pass already completed full batch validation; pass its
        # frozen in-memory snapshot to avoid reopening any product file.
        return reader.detail(node_id, snapshot=snapshot)

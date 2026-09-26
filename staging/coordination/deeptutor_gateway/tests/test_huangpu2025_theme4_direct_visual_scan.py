from __future__ import annotations

from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.huangpu2025_theme4_direct_visual_scan import (
    EXPECTED_ATOMIC_IDS,
    PAPER_ID,
    PRODUCT_ID,
    THEME_ID,
    Huangpu2025Theme4DirectVisualScanError,
    Huangpu2025Theme4DirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value} | set().union(
            *(_keys(item) for item in value.values()), set()
        )
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value), set())
    return set()


def test_huangpu_reader_preserves_theme_identity_answers_textbooks_and_crop_roles():
    reader = Huangpu2025Theme4DirectVisualScanReader(SHCHEM_ROOT)
    status = reader.status()
    catalog = reader.catalog()

    assert status["product_id"] == PRODUCT_ID
    assert status["paper_id"] == PAPER_ID
    assert status["counts"] == {
        "expected_themes": 1,
        "expected_printed_questions": 9,
        "expected_atomic_parts": 11,
        "scan_records": 11,
        "visual_scan_completed": 11,
        "blocked_pending_broader_crop": 0,
        "question_or_shared_crop_bindings": 13,
        "shared_crop_bindings": 4,
        "nonofficial_answer_crop_bindings": 11,
        "total_exact_crop_bindings": 33,
    }
    assert catalog["master_node_ids"] == list(EXPECTED_ATOMIC_IDS)
    assert catalog["count"] == 11
    assert {
        (
            item["source_year"],
            item["source_region_or_school"],
            item["source_paper_type"],
        )
        for item in catalog["items"]
    } == {(2025, "黄浦区", "二模")}
    assert all(
        item["availability"] == "present_part_aligned"
        and item["source_authority"] == "nonofficial_reference"
        for item in catalog["items"]
    )
    assert sum(item["has_quality_note"] for item in catalog["items"]) == 4
    assert all(
        value is False
        for key, value in status["authority"].items()
        if key not in {"candidate_only", "read_only"}
    )

    first = reader.detail(EXPECTED_ATOMIC_IDS[0])
    dependent = reader.detail("HP2025-EM-S4-Q7-P2")
    assert first["scan_hierarchy"] == {
        "paper_id": PAPER_ID,
        "theme_id": THEME_ID,
        "theme_sequence": 4,
        "theme_title": "四、碘酸钙的制备",
        "printed_question_id": "HP2025-EM-S4-Q1",
        "printed_sequence": 1,
        "atomic_part_id": "HP2025-EM-S4-Q1-P1",
        "atomic_sequence_in_printed": 1,
        "independent_choice_section": False,
    }
    assert first["source_identity"]["year"] == 2025
    assert first["source_identity"]["region_or_school"] == "黄浦区"
    assert first["source_identity"]["paper_type"] == "二模"
    assert first["answer_boundary"] == {
        "availability": "present_part_aligned",
        "authority": "nonofficial_reference",
        "verified": False,
        "independently_verified": False,
        "official_answer_claim_allowed": False,
    }
    assert first["reference_answer"]["reference_answer_text"]
    assert first["reference_answer"]["independently_verified"] is False
    assert dependent["dependency"]["prior_atomic_part_ids"] == ["HP2025-EM-S4-Q7-P1"]

    textbook = first["textbook_directory_mapping"]
    assert textbook["mapping_status"] == "complete_directory_level_unit_unknown"
    assert [
        (item["volume_id"], item["chapter_id"], item["section_number"])
        for item in textbook["entries"]
    ] == [
        ("TB-E1", "TB-E1-C4", "4.1"),
        ("TB-M1", "TB-M1-C2", "2.2"),
    ]
    assert textbook["authority_boundary"] == {
        "directory_mapping_candidate_only": True,
        "edition_or_printing_verified": False,
        "unit_verified": False,
        "human_taxonomy_reviewed": False,
        "local_paths_or_hashes_exposed": False,
    }
    assert not {
        "source_path",
        "source_sha256",
        "mapping_registry_path",
        "mapping_registry_sha256",
        "visual_evidence",
    } & _keys(textbook)

    question = first["evidence_descriptors"][0]
    payload = reader.question_crop(first["master_node_id"], question["crop_id"])
    assert payload.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert payload.sha256 == question["sha256"]
    answer_crop_id = next(iter(reader._snapshot().answer_crop_ids))
    with pytest.raises(Huangpu2025Theme4DirectVisualScanError) as denied:
        reader.question_crop(first["master_node_id"], answer_crop_id)
    assert denied.value.status == 403


def test_huangpu_registration_updates_master_direct_and_theme_counts():
    direct = MasterDirectVisualScanReader(SHCHEM_ROOT)
    status = direct.status()
    assert status["counts"] == {
        "direct_scan_products": 10,
        "direct_scan_records": 224,
        "visual_scan_completed": 224,
        "blocked_pending_broader_crop": 0,
    }
    assert status["coverage"] == {
        "master_atomic_inventory": 470,
        "wave1_exact_visual_scanned": 169,
        "direct_master_visual_scanned": 224,
        "visual_scanned_master_atomic": 393,
        "remaining_unscanned": 77,
        "direct_exact_overlap": 0,
    }

    master = ThemeWorkbenchReader(SHCHEM_ROOT, direct_scans=direct).groups("master")
    assert master["counts"] == {
        "papers": 20,
        "theme_groups": 48,
        "atomic_parts": 470,
        "display_atomic_units": 477,
        "unassigned_atomic_parts": 43,
        "visual_scanned": 397,
        "unscanned": 73,
        "label_complete": 379,
        "label_pending": 91,
        "answer_aligned": 358,
        "answer_unaligned": 13,
        "answer_absent": 106,
        "quality_notes": 66,
    }
    paper = next(item for item in master["papers"] if item["paper"]["id"] == PAPER_ID)
    assert paper["paper"]["title"] == "2025年上海市黄浦区高三化学二模试卷"
    assert (
        paper["paper"]["status"] == "observed_theme_four_visual_scan_incomplete_paper"
    )
    theme = next(
        item for item in paper["theme_groups"] if item["theme"]["id"] == THEME_ID
    )
    assert theme["counts"]["printed"] == 9
    assert theme["counts"]["atomic"] == 11
    assert theme["counts"]["visual_scanned"] == 11
    assert theme["counts"]["answer_aligned"] == 11
    assert theme["counts"]["quality_notes"] == 4
    assert theme["shared_context"]["material_count"] == 4
    assert theme["dependencies"]["one_prior_part"] == 2
    assert theme["dependencies"]["explicit_prior_edge_count"] == 2

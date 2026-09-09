from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.master_parent_chain_repair_overlay import (
    AUTHORITY,
    EXPECTED_COUNTS,
    MasterParentChainRepairOverlayError,
    MasterParentChainRepairOverlayReader,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
PRODUCT = (
    SHCHEM_ROOT
    / "kb/classification/"
    "master_parent_chain_repair_datong_2025_h1_mid_v1_2026-08-27"
)
CENTRAL = (
    SHCHEM_ROOT
    / "kb/classification/theme_hierarchy_master_index_v1_2026-08-03"
)


@pytest.fixture(scope="module")
def overlay() -> dict:
    return MasterParentChainRepairOverlayReader(SHCHEM_ROOT).overlay()


def _themes(value: dict) -> list[dict]:
    return value["paper_groups"][0]["theme_groups"]


def _master_rows(value: dict) -> list[dict]:
    return [row for theme in _themes(value) for row in theme["atomic_chain"]]


def _effective_units(value: dict) -> list[dict]:
    return [
        unit
        for row in _master_rows(value)
        for unit in row["candidate_effective_units"]
    ]


def _recursive_keys(value) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value] + [
            key for nested in value.values() for key in _recursive_keys(nested)
        ]
    if isinstance(value, list):
        return [key for nested in value for key in _recursive_keys(nested)]
    return []


def _recursive_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _recursive_strings(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _recursive_strings(nested)]
    return []


def test_candidate_and_central_progress_are_independent(overlay: dict) -> None:
    assert overlay["scope"] == "candidate_only_read_only_master_parent_chain_repair"
    assert overlay["counts"] == EXPECTED_COUNTS
    assert overlay["completion"] == {
        "denominator_unit": "source_master_atomic",
        "candidate_completed": 43,
        "candidate_total": 43,
        "candidate_remaining": 0,
        "candidate_status": "machine_candidate_complete_pending_central_confirmation",
        "candidate_label_zh": "候选整理43/43完成",
        "central_label_zh": "中央原记录待确认",
        "human_reviewed": False,
    }
    assert overlay["central_master"] == {
        "atomic_total": 470,
        "complete_parent_chain_atomics": 427,
        "original_pending_atomics": 43,
        "candidate_overlay_atomics": 43,
        "applied": False,
        "human_confirmed": False,
        "denominator_unchanged": True,
    }
    assert overlay["authority"] == AUTHORITY


def test_one_paper_has_five_complete_theme_batches_in_source_order(
    overlay: dict,
) -> None:
    assert len(overlay["paper_groups"]) == 1
    paper = overlay["paper_groups"][0]
    assert paper["paper"]["id"] == "MASTER-PAPER-eca872096473cd6ec91a"
    assert paper["paper"]["page_span"] == [1, 6]
    assert paper["paper"]["source_metadata"]["school_attribution_status"] == (
        "title_attribution_only"
    )
    themes = paper["theme_groups"]
    assert [theme["theme"]["sequence"] for theme in themes] == [1, 2, 3, 4, 5]
    assert [theme["theme"]["page_span"] for theme in themes] == [
        [1, 2],
        [2, 3],
        [3, 4],
        [4, 5],
        [5, 6],
    ]
    assert [theme["counts"]["source_master_atomics"] for theme in themes] == [
        9,
        9,
        10,
        8,
        7,
    ]
    assert [theme["counts"]["effective_atomics"] for theme in themes] == [
        15,
        11,
        12,
        9,
        9,
    ]


def test_atomic_chain_keeps_43_master_rows_and_56_effective_units(
    overlay: dict,
) -> None:
    master_rows = _master_rows(overlay)
    effective = _effective_units(overlay)
    assert len(master_rows) == 43
    assert len({row["atomic_part_id"] for row in master_rows}) == 43
    assert len(effective) == 56
    assert len({unit["atomic_part_id"] for unit in effective}) == 56
    assert Counter(len(row["candidate_effective_units"]) for row in master_rows) == {
        1: 33,
        2: 7,
        3: 3,
    }
    assert sum(row["boundary_relation"] == "split_1_to_n" for row in master_rows) == 10
    assert "alias_units" not in _recursive_keys(overlay)


def test_atomic_chain_is_the_exact_central_unassigned_id_set(overlay: dict) -> None:
    projection = json.loads(
        (PRODUCT / "master_repair_projection.json").read_text(encoding="utf-8")
    )
    expected = set(projection["completed_source_master_atomic_ids"])
    actual = {row["atomic_part_id"] for row in _master_rows(overlay)}
    assert actual == expected
    assert len(actual) == 43
    assert overlay["integrity"]["central_unassigned_exact_set_verified"] is True


def test_dependency_edges_are_forward_only_and_closure_is_renderable(
    overlay: dict,
) -> None:
    effective = _effective_units(overlay)
    by_id = {unit["atomic_part_id"]: unit for unit in effective}
    theme_by_id = {
        unit["atomic_part_id"]: theme["theme"]["id"]
        for theme in _themes(overlay)
        for row in theme["atomic_chain"]
        for unit in row["candidate_effective_units"]
    }
    edge_count = 0
    for unit in effective:
        direct = unit["dependency"]["prior_effective_atomic_part_ids"]
        closure = unit["dependency"]["prior_effective_closure_ids"]
        edge_count += len(direct)
        assert set(direct).issubset(closure)
        assert len(closure) == len(set(closure))
        for prior_id in closure:
            assert prior_id in by_id
            assert theme_by_id[prior_id] == theme_by_id[unit["atomic_part_id"]]
            assert by_id[prior_id]["effective_sequence_in_theme"] < unit[
                "effective_sequence_in_theme"
            ]
    assert edge_count == 8
    assert sum(len(theme["dependency_edges"]) for theme in _themes(overlay)) == 8
    assert overlay["integrity"]["dependency_forward_closure_verified"] is True


def test_shared_materials_pages_and_answer_boundary_are_preserved(
    overlay: dict,
) -> None:
    assert all(theme["shared_context"]["shared_materials"] for theme in _themes(overlay))
    for theme in _themes(overlay):
        sequences = [
            material["material_sequence"]
            for material in theme["shared_context"]["shared_materials"]
        ]
        assert sequences == list(range(1, len(sequences) + 1))
        assert all(
            material["page_numbers"]
            and material["evidence_status"] == "actually_viewed_candidate"
            for material in theme["shared_context"]["shared_materials"]
        )
    for unit in _effective_units(overlay):
        assert unit["question_crop_ids"]
        assert unit["question_page_numbers"]
        assert [item["crop_id"] for item in unit["shared_materials"]] == unit[
            "shared_material_crop_ids"
        ]
        assert all(item["page_numbers"] for item in unit["shared_materials"])
        assert unit["answer"] == {
            "availability": "absent",
            "authority": "none",
            "alignment_status": "no_answer_material_available_in_captured_source",
            "official": False,
            "verified": False,
            "independently_verified": False,
        }
        assert unit["authority"] == AUTHORITY


def test_textbook_volume_chapter_section_mapping_is_safe_and_complete(
    overlay: dict,
) -> None:
    mappings = [
        entry
        for unit in _effective_units(overlay)
        for entry in unit["textbook_mapping_candidate"]["entries"]
    ]
    assert len(mappings) == 82
    assert all(
        unit["textbook_mapping_candidate"]["status"]
        == "complete_directory_level_unit_unknown"
        and unit["textbook_mapping_candidate"]["human_reviewed"] is False
        for unit in _effective_units(overlay)
    )
    assert all(
        entry["publisher"] == "上海科学技术出版社"
        and entry["volume_title"] == "必修第一册"
        and entry["chapter_title"].startswith("第")
        and entry["section_number"]
        and entry["section_title"]
        for entry in mappings
    )
    assert overlay["integrity"]["textbook_directory_mapping_verified"] is True


def test_sidecar_contains_no_locator_digest_or_answer_body(overlay: dict) -> None:
    forbidden_exact = {
        "answer_text",
        "reference_answer_text",
        "solution_text",
        "source_url",
        "url",
        "path",
        "local_path",
        "source_path",
        "crop_path",
        "sha256",
        "hash",
    }
    for key in _recursive_keys(overlay):
        lowered = key.casefold()
        assert lowered not in forbidden_exact
        assert not lowered.endswith(("_path", "_url", "_sha256", "_hash"))
        assert "answer_text" not in lowered
    for value in _recursive_strings(overlay):
        lowered = value.casefold()
        assert "http://" not in lowered
        assert "https://" not in lowered
        assert "file://" not in lowered
        assert "sh-chem-db/" not in lowered
        assert "kb/" not in lowered


def test_all_ten_difficulty_factors_remain_machine_candidates(overlay: dict) -> None:
    units = _effective_units(overlay)
    assert sum(
        len(unit["difficulty_candidate"]["ten_factors"]) for unit in units
    ) == 560
    assert all(
        unit["difficulty_candidate"]["is_measured"] is False
        and unit["difficulty_candidate"]["human_reviewed"] is False
        for unit in units
    )


def test_candidate_output_tamper_fails_closed(tmp_path: Path) -> None:
    copied = tmp_path / "candidate"
    shutil.copytree(PRODUCT, copied)
    coverage = copied / "coverage_report.json"
    coverage.write_bytes(coverage.read_bytes() + b" ")
    reader = MasterParentChainRepairOverlayReader(SHCHEM_ROOT)
    reader.product_root = copied
    with pytest.raises(MasterParentChainRepairOverlayError) as exc:
        reader.overlay()
    assert exc.value.code == "master_parent_repair_output_drift"


def test_central_output_tamper_fails_closed(tmp_path: Path) -> None:
    copied_root = tmp_path / "sh-chem-db"
    copied_central = copied_root / CENTRAL.relative_to(SHCHEM_ROOT)
    copied_central.parent.mkdir(parents=True)
    shutil.copytree(CENTRAL, copied_central)
    coverage = copied_central / "coverage_summary.json"
    coverage.write_bytes(coverage.read_bytes() + b" ")
    reader = MasterParentChainRepairOverlayReader(SHCHEM_ROOT)
    reader.central_root = copied_root
    with pytest.raises(MasterParentChainRepairOverlayError) as exc:
        reader.overlay()
    assert exc.value.code == "master_parent_repair_output_drift"


def test_overlay_is_a_fresh_projection_not_a_mutable_cache(overlay: dict) -> None:
    overlay["completion"]["candidate_completed"] = -1
    fresh = MasterParentChainRepairOverlayReader(SHCHEM_ROOT).overlay()
    assert fresh["completion"]["candidate_completed"] == 43

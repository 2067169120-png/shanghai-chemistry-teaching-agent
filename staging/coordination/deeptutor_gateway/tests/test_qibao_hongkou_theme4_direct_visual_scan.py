from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.huangpu2025_theme4_direct_visual_scan import (
    HONGKOU2026_SECOND_MOCK_THEME4_CONFIG,
    QIBAO2025_OPENING_THEME4_CONFIG,
    Hongkou2026SecondMockTheme4DirectVisualScanReader,
    Qibao2025OpeningTheme4DirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanError,
    MasterDirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.master_visual_scan_alias import (
    MasterVisualScanAliasReader,
)
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    MasterWave1WorkbenchReader,
)
from integrations.deeptutor_shchem_v1.question_search_workbench import (
    QuestionSearchWorkbench,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"


@pytest.mark.parametrize(
    ("reader_type", "config", "printed", "atomic", "safe_crops", "answer_crops"),
    (
        (
            Qibao2025OpeningTheme4DirectVisualScanReader,
            QIBAO2025_OPENING_THEME4_CONFIG,
            8,
            10,
            12,
            11,
        ),
        (
            Hongkou2026SecondMockTheme4DirectVisualScanReader,
            HONGKOU2026_SECOND_MOCK_THEME4_CONFIG,
            7,
            9,
            13,
            10,
        ),
    ),
)
def test_recent_theme_readers_verify_frozen_closure_and_keep_answer_pixels_private(
    reader_type, config, printed, atomic, safe_crops, answer_crops
):
    reader = reader_type(SHCHEM_ROOT)
    status = reader.status()
    catalog = reader.catalog()

    assert status["product_id"] == config.product_id
    assert status["counts"] == {
        "expected_themes": 1,
        "expected_printed_questions": printed,
        "expected_atomic_parts": atomic,
        "scan_records": atomic,
        "visual_scan_completed": atomic,
        "blocked_pending_broader_crop": 0,
        "question_or_shared_crop_bindings": safe_crops,
        "shared_crop_bindings": 2 if atomic == 10 else 3,
        "nonofficial_answer_crop_bindings": answer_crops,
        "total_exact_crop_bindings": config.crop_count,
    }
    assert catalog["master_node_ids"] == list(config.atomic_ids)
    assert sum(item["has_quality_note"] for item in catalog["items"]) == len(
        config.quality_ids
    )
    assert all(
        item["availability"] == "present_part_aligned"
        and item["source_authority"] == "nonofficial_reference"
        for item in catalog["items"]
    )
    assert status["integrity"]["hash_verified_on_read"] is True
    assert status["integrity"]["four_level_parent_chain_verified_on_read"] is True
    assert (
        status["integrity"]["answer_and_textbook_fields_verified_on_read"] is True
    )

    detail = reader.detail(config.atomic_ids[0])
    assert detail["scan_hierarchy"]["paper_id"] == config.paper_id
    assert detail["scan_hierarchy"]["theme_id"] == config.theme_id
    assert detail["answer_boundary"] == {
        "availability": "present_part_aligned",
        "authority": "nonofficial_reference",
        "source_authority": "nonofficial_wechat_reposted_reference",
        "verified": False,
        "independently_verified": False,
        "official_answer_claim_allowed": False,
        "official_scoring_claim_allowed": False,
    }
    assert detail["reference_answer"]["reference_answer_text"]
    assert detail["reference_answer"]["independently_verified"] is False
    assert detail["textbook_directory_mapping"]["entries"]

    question = next(
        item
        for item in detail["evidence_descriptors"]
        if item["evidence_role"] == "question"
    )
    payload = reader.question_crop(detail["master_node_id"], question["crop_id"])
    assert payload.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert payload.sha256 == question["sha256"]
    answer_crop_id = next(iter(reader._snapshot().answer_crop_ids))
    with pytest.raises(MasterDirectVisualScanError) as denied:
        reader.question_crop(detail["master_node_id"], answer_crop_id)
    assert denied.value.status == 403


def test_recent_theme_registration_is_disjoint_and_updates_dynamic_master_counts():
    aggregate = MasterDirectVisualScanReader(SHCHEM_ROOT)
    direct = aggregate.catalog()
    exact = set(
        MasterWave1WorkbenchReader(SHCHEM_ROOT).visual_scan_identity_index()[
            "exact_master_ids"
        ]
    )
    aliases = set(
        MasterVisualScanAliasReader(SHCHEM_ROOT).catalog()["master_node_ids"]
    )
    qibao = set(QIBAO2025_OPENING_THEME4_CONFIG.atomic_ids)
    hongkou = set(HONGKOU2026_SECOND_MOCK_THEME4_CONFIG.atomic_ids)
    all_direct = set(direct["master_node_ids"])

    assert qibao.isdisjoint(hongkou | exact | aliases)
    assert hongkou.isdisjoint(exact | aliases)
    assert len(all_direct) == 224
    assert direct["coverage"] == {
        "master_atomic_inventory": 470,
        "wave1_exact_visual_scanned": 169,
        "direct_master_visual_scanned": 224,
        "visual_scanned_master_atomic": 393,
        "remaining_unscanned": 77,
        "direct_exact_overlap": 0,
    }

    master = ThemeWorkbenchReader(SHCHEM_ROOT, direct_scans=aggregate).groups(
        "master"
    )
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
    groups = {
        group["theme"]["id"]: group
        for paper in master["papers"]
        for group in paper["theme_groups"]
    }
    assert groups[QIBAO2025_OPENING_THEME4_CONFIG.theme_id]["counts"] == {
        "printed": 8,
        "atomic": 10,
        "display_atomic_units": 10,
        "visual_scanned": 10,
        "unscanned": 0,
        "label_complete": 10,
        "label_pending": 0,
        "answer_aligned": 10,
        "answer_unaligned": 0,
        "answer_absent": 0,
        "quality_notes": 1,
    }
    assert groups[HONGKOU2026_SECOND_MOCK_THEME4_CONFIG.theme_id]["counts"] == {
        "printed": 7,
        "atomic": 9,
        "display_atomic_units": 9,
        "visual_scanned": 9,
        "unscanned": 0,
        "label_complete": 9,
        "label_pending": 0,
        "answer_aligned": 9,
        "answer_unaligned": 0,
        "answer_absent": 0,
        "quality_notes": 2,
    }
    assert groups[QIBAO2025_OPENING_THEME4_CONFIG.theme_id]["dependencies"][
        "explicit_prior_edge_count"
    ] == 2
    assert groups[HONGKOU2026_SECOND_MOCK_THEME4_CONFIG.theme_id]["dependencies"][
        "explicit_prior_edge_count"
    ] == 1


def test_recent_themes_are_searchable_as_complete_theme_cards():
    master = ThemeWorkbenchReader(SHCHEM_ROOT).groups("master")
    search = QuestionSearchWorkbench()

    for query, config, expected_atomic in (
        ("电解质溶液 废水处理", QIBAO2025_OPENING_THEME4_CONFIG, 10),
        ("电镀污泥 镍", HONGKOU2026_SECOND_MOCK_THEME4_CONFIG, 9),
    ):
        result = search.search(
            {"scope": "master", "q": query, "limit": 20},
            theme_loader=lambda _scope: master,
        )
        card = next(
            item for item in result["items"] if item["theme"]["id"] == config.theme_id
        )
        assert card["counts"]["atomic_total"] == expected_atomic
        assert len(card["atomic_chain"]) == expected_atomic
        assert card["dependencies"]["explicit_prior_edge_count"] == len(
            config.dependencies
        )
        assert "reference_answer_text" not in str(card)
        assert result["integrity"]["complete_theme_chain_returned"] is True


def test_recent_product_bound_output_drift_fails_closed(tmp_path: Path):
    reader = Qibao2025OpeningTheme4DirectVisualScanReader(SHCHEM_ROOT)
    copied = tmp_path / "qibao-product"
    shutil.copytree(reader.product_root, copied)
    reader.product_root = copied.resolve()
    records = copied / "scan_records.jsonl"
    records.write_bytes(records.read_bytes() + b"\n")

    with pytest.raises(MasterDirectVisualScanError) as captured:
        reader.catalog()
    assert captured.value.code == "master_direct_scan_binding_mismatch"

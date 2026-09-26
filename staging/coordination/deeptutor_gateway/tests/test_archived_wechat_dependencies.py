"""Bounded real-source dependency/assembly regression; no renderer or provider."""

from copy import deepcopy
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_library_session import (
    snapshot_reader_graph,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.paper_export_workbench import _prepare_catalog
from integrations.deeptutor_shchem_v1.paper_format_presets import (
    PaperFormatContractError,
    build_assembly_blueprint,
    default_shanghai_theme_preset,
)
from integrations.deeptutor_shchem_v1.shanghai_high_east2025_theme45_direct_visual_scan import (
    THEME_IDS as EAST_THEMES,
)
from integrations.deeptutor_shchem_v1.songjiang2025_theme2_direct_visual_scan import (
    EXPECTED_ATOMIC_IDS as SJ_ATOMS,
)
from integrations.deeptutor_shchem_v1.songjiang2025_theme2_direct_visual_scan import (
    THEME_ID as SJ_THEME,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader

DB = Path(__file__).resolve().parents[4] / "sh-chem-db"
SJ_MATERIALS = {
    "SJ25T2-C-783aedf98189899baf2c2909",  # Complete electrolysis context, A.
    "SJ25T2-C-377ff6ffad094904d6cf5ea4",  # Complete yeast context, B.
    "SJ25T2-C-bc033f159e7aba3c44d5af29",  # Complete green-rust method, D.
}
ARCHIVE_ONLY = {
    "SJ25T2-C-cad3a95bc7739857cfe7515e",  # Original page repeats Q1-4.
    "SJ25T2-C-c4702d34468809ed194d0065",  # Original page repeats Q5-9.
}
EAST_MATERIAL = "SHEAST2025-CROP-b39d0d33cdac04884a42c934"
pytestmark = pytest.mark.skipif(
    not (DB / "kb/formal/candidates/pending_v2/songjiang_2025_theme2_feoh2").is_dir(),
    reason="requires the two local archived candidate packs and frozen source pages",
)


@pytest.fixture(scope="module")
def catalog():
    # This is the native reader configuration, with a per-operation snapshot
    # graph only. No fabricated theme rows, personal state, or approval flags.
    direct = MasterDirectVisualScanReader(DB, include_archived_candidates=True)
    themes = ThemeWorkbenchReader(
        DB, master_workbench=direct.master_workbench, direct_scans=direct
    )
    (themes,) = snapshot_reader_graph((themes,))
    return DesktopWorkbenchFacade._with_presentation_snapshot(themes.groups("master"))


def _group(catalog, theme_id):
    return next(
        group
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
        if group["theme"]["id"] == theme_id
    )


def _selection(catalog, theme_id, unit="theme", atomic_id=None):
    return {
        "scope": "master",
        "selection_unit": unit,
        "theme_id": theme_id,
        "target_atomic_id": atomic_id,
        "expected_data_snapshot_id": catalog["data_snapshot_id"],
    }


def _blueprint(catalog, selections):
    def load(scope, snapshot_id):
        return _prepare_catalog(catalog, scope=scope, snapshot_id=snapshot_id)

    return build_assembly_blueprint(
        selections,
        theme_loader=load,
        expected_data_snapshot_id=catalog["data_snapshot_id"],
        preset=default_shanghai_theme_preset(),
    )


def test_real_fourteen_dependency_mapping_and_unreviewed_six_stay_distinct(catalog):
    sj = _group(catalog, SJ_THEME)
    assert [row["atomic_part_id"] for row in sj["atomic_chain"]] == list(SJ_ATOMS)
    expected_edges = {
        "SJ2025-EM-S2-Q7-P1": ["SJ2025-EM-S2-Q6-P1"],
        "SJ2025-EM-S2-Q9-P1": ["SJ2025-EM-S2-Q8-P1"],
    }
    for row in sj["atomic_chain"]:
        expected = expected_edges.get(row["atomic_part_id"], [])
        assert row["dependency"]["prior_atomic_part_ids"] == expected
        assert row["dependency"]["kind"] == (
            "one_prior_part" if expected else "shared_material_only"
        )
    for row in _group(catalog, EAST_THEMES[5])["atomic_chain"]:
        assert row["dependency"]["kind"] == "shared_material_only"
        assert row["dependency"]["prior_atomic_part_ids"] == []
    untouched = _group(catalog, EAST_THEMES[4])["atomic_chain"]
    assert len(untouched) == 6
    assert all(
        row["dependency"]["kind"] == "blocked_pending_review" for row in untouched
    )


def test_complete_two_themes_fourteen_units_pass_normal_dependency_gate(catalog):
    result = _blueprint(
        catalog,
        [_selection(catalog, SJ_THEME), _selection(catalog, EAST_THEMES[5])],
    )
    assert result["status"] == "ready_for_content_resolution"
    assert result["blockers"] == []
    assert result["counts"] == {
        "theme_count": 2,
        "printed_question_count": 14,
        "atomic_part_count": 14,
        "shared_material_count": 4,
    }
    assert all(
        theme["integrity"]["dependency_closure_complete"]
        for theme in result["theme_bundles"]
    )


@pytest.mark.parametrize("number", [7, 9])
def test_isolated_dependent_question_is_rejected(catalog, number):
    with pytest.raises(PaperFormatContractError) as caught:
        _blueprint(
            catalog,
            [_selection(catalog, SJ_THEME, "atomic", f"SJ2025-EM-S2-Q{number}-P1")],
        )
    assert caught.value.code == "single_atomic_breaks_dependency"


@pytest.mark.parametrize("number", [7, 9])
def test_dependency_selection_keeps_given_operation_or_calculation_and_materials(
    catalog, number
):
    target = f"SJ2025-EM-S2-Q{number}-P1"
    prior = f"SJ2025-EM-S2-Q{number - 1}-P1"
    result = _blueprint(catalog, [_selection(catalog, SJ_THEME, "dependency", target)])
    assert result["status"] == "ready_for_content_resolution"
    theme = result["theme_bundles"][0]
    assert theme["final_atomic_ids"] == [prior, target]
    assert theme["auto_added_dependency_ids"] == [prior]
    assert {row["material_id"] for row in theme["shared_materials"]} == SJ_MATERIALS
    assert not ARCHIVE_ONLY & {row["material_id"] for row in theme["shared_materials"]}


def test_complete_unknown_theme_still_blocks_without_global_gate_change(catalog):
    result = _blueprint(catalog, [_selection(catalog, EAST_THEMES[4])])
    assert result["status"] == "blocked"
    assert len(result["blockers"]) == 6
    assert {row["code"] for row in result["blockers"]} == {"dependency_pending_review"}


def test_material_union_has_complete_context_without_archived_question_repeats(catalog):
    sj = _group(catalog, SJ_THEME)
    east = _group(catalog, EAST_THEMES[5])
    assert {
        row["material_id"] for row in sj["shared_context"]["materials"]
    } == SJ_MATERIALS
    assert {row["material_id"] for row in east["shared_context"]["materials"]} == {
        EAST_MATERIAL
    }
    assert sj["shared_context"]["material_count"] == 3
    assert east["shared_context"]["material_count"] == 1


def test_missing_prior_in_source_theme_is_not_silently_accepted(catalog):
    changed = deepcopy(catalog)
    group = _group(changed, SJ_THEME)
    group["atomic_chain"] = [
        row for row in group["atomic_chain"] if row["atomic_part_id"] != SJ_ATOMS[5]
    ]
    with pytest.raises(PaperFormatContractError) as caught:
        _blueprint(changed, [_selection(changed, SJ_THEME)])
    assert caught.value.code == "dependency_cross_theme_or_missing"

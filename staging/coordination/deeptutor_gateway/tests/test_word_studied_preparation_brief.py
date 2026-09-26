"""Source-studied input retention; these checks do not grade teaching quality."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from prepare_word_studied_ionization_draft import (
    OUTPUT,
    ROOT,
    SOURCE_HASHES,
    NoProvider,
    build_brief,
    digest,
    validate_brief,
)

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
    PreparationImageStore,
)


def brief():
    return json.loads((OUTPUT / "teacher-brief.json").read_text("utf-8"))


def test_bundle_exactly_matches_readable_material_and_bound_image_bytes():
    value = brief()
    assert value == build_brief(
        (OUTPUT / "备课材料.md").read_text("utf-8"), value["image_assets"]
    )
    normalized = validate_brief(value)
    assert normalized["candidate_only"] is True
    images = PreparationImageStore(OUTPUT / "assets")
    for asset in value["image_assets"]:
        images.load(asset)
    for source, expected in SOURCE_HASHES.items():
        assert digest(ROOT / source) == expected


def test_source_notes_do_not_claim_teacher_confirmation_or_original_missing_arrows():
    text = brief()["materials"]
    assert "不是教师已确认的成品" in text
    assert "原页B/C箭头有缺字符" in text
    assert "不称原题原样复刻" in text
    assert "溶解平衡表达" in text
    assert "后者改为遮住例式后的复写" in text
    assert "Q9仅课后选做，不占80分钟必讲预算" in text


@pytest.mark.parametrize(
    "removed",
    [
        "NaHSO₄＝Na⁺＋HSO₄⁻",
        "HS⁻ ⇌ H⁺＋S²⁻",
        "6．H₂O ⇌ H⁺＋OH⁻。",
    ],
)
def test_missing_required_complete_formula_is_detected(removed):
    value = deepcopy(brief())
    value["materials"] = value["materials"].replace(removed, "缺口")
    with pytest.raises(AssertionError):
        validate_brief(value)


def test_exact_materials_survive_offline_save_restart_and_reload(tmp_path):
    paths = DesktopPaths.from_workspace(ROOT, state_root=tmp_path)
    facade = DesktopWorkbenchFacade(paths, provider_store=NoProvider())
    value = brief()
    receipt = facade.create_preparation_draft(value)
    restarted = DesktopWorkbenchFacade(paths, provider_store=NoProvider())
    option = restarted.preparation_draft_options()[0]
    loaded = restarted.load_preparation_draft(receipt.draft_id, option["revision"])
    assert loaded["payload"] == value
    assert len(restarted.preparation_draft_options()) == 1

"""Source-pixel display repair checks; not a teaching/chemical approval."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from itertools import pairwise
from pathlib import Path

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1 import (
    fosinopril_source_presentation as presentation,
)

ROOT = Path(__file__).resolve().parents[4] / "sh-chem-db"


def _inputs(root=ROOT):
    manifest = json.loads((root / presentation.MANIFEST_ASSET).read_bytes())
    outputs = {row["asset"]: (root / row["asset"]).read_bytes() for row in manifest["crops"]}
    sources = {row["asset"]: row for row in manifest["source_assets"]}
    recipes = {recipe.asset: recipe for recipe in presentation.RECIPES}
    crops = {}
    for row in manifest["crops"]:
        if row.get("source_asset") is None:
            continue
        asset = row["asset"]
        recipe = recipes.get(asset)
        crops[asset] = {
            **copy.deepcopy(row),
            "crop_id": "fixture-" + hashlib.sha256(asset.encode()).hexdigest(),
            "role": recipe.role if recipe else "unknown",
            "output_path": asset, "bytes": len(outputs[asset]),
            "width": row["dimensions"][0], "height": row["dimensions"][1],
            "source_sha256": sources[row["source_asset"]]["sha256"],
            "source_page_number": recipe.page if recipe else 0,
            "crop_box": list(row["crop_box_xywh"]),
            "safe_http_status_if_routed": 200 if recipe and recipe.role != "answer" else 403,
        }
    return outputs, crops


@pytest.fixture(scope="module")
def repaired():
    outputs, crops = _inputs()
    before_outputs, before_crops = outputs.copy(), copy.deepcopy(crops)
    presentation.apply_source_a_presentation(ROOT, outputs, crops)
    return outputs, crops, before_outputs, before_crops


@pytest.mark.parametrize("recipe", presentation.RECIPES, ids=lambda recipe: recipe.name)
def test_display_crop_is_exact_source_pixels_with_both_bindings(repaired, recipe):
    outputs, crops, before_outputs, before_crops = repaired
    crop, raw = crops[recipe.asset], outputs[recipe.asset]
    with Image.open(io.BytesIO(raw)) as actual, Image.open(ROOT / recipe.source_asset) as source:
        x, y, width, height = recipe.box
        expected = source.crop((x, y, x + width, y + height))
        assert actual.mode == expected.mode
        assert actual.size == expected.size
        assert actual.tobytes() == expected.tobytes()
    assert crop["sha256"] == hashlib.sha256(raw).hexdigest()
    assert crop["bytes"] == len(raw)
    assert crop["archived_sha256"] == hashlib.sha256(before_outputs[recipe.asset]).hexdigest()
    assert crop["archived_crop_box"] == list(recipe.archived_box)
    assert crop["crop_box"] == list(recipe.box)
    assert crop["presentation_revision"]["source_crop_box"] == list(recipe.box)
    assert crop["presentation_revision"]["human_review_complete"] is False
    for field in ("crop_id", "asset", "source_asset", "source_sha256", "role", "crop_box_xywh",
                  "safe_http_status_if_routed", "direct_pixel_reuse_allowed", "use"):
        assert crop[field] == before_crops[recipe.asset][field]
    # The archive on disk still contains the old bytes; this is a display view.
    assert (ROOT / recipe.asset).read_bytes() == before_outputs[recipe.asset]


def test_only_twenty_exact_source_a_images_change_and_answer_routes_stay_private(repaired):
    outputs, crops, before_outputs, before_crops = repaired
    expected_assets = {recipe.asset for recipe in presentation.RECIPES}
    assert len(expected_assets) == 20
    assert len(crops) == 49 and len(outputs) == 51
    assert {asset for asset in outputs if outputs[asset] != before_outputs[asset]} == expected_assets
    for asset in before_crops.keys() - expected_assets:
        assert crops[asset] == before_crops[asset]
    assert sum(crop["role"] == "answer" for crop in crops.values()) == 9
    assert all(crop["safe_http_status_if_routed"] == 403 for crop in crops.values() if crop["role"] == "answer")
    assert sum(crop["role"] == "question" for crop in crops.values()) == 10
    assert sum(crop["role"] == "shared_material" for crop in crops.values()) == 1


@pytest.mark.parametrize("field,value", [
    ("role", "question"),
    ("source_asset", "other-source.png"),
    ("source_sha256", "0" * 64),
    ("source_page_number", 5),
    ("crop_box", [45, 760, 985, 259]),
    ("crop_box_xywh", [45, 760, 985, 259]),
    ("sha256", "0" * 64),
    ("asset", "elsewhere.png"),
    ("output_path", "elsewhere.png"),
    ("width", 900),
])
def test_last_mapping_drift_does_not_partially_mutate_outputs(field, value):
    outputs, crops = _inputs()
    crops[presentation.RECIPES[-1].asset][field] = value
    before_outputs, before_crops = outputs.copy(), copy.deepcopy(crops)
    with pytest.raises(presentation.FosinoprilPresentationError, match="binding drifted"):
        presentation.apply_source_a_presentation(ROOT, outputs, crops)
    assert outputs == before_outputs
    assert crops == before_crops


def test_missing_mapping_and_changed_bytes_are_rejected():
    outputs, crops = _inputs()
    outputs[presentation.RECIPES[-1].asset] += b"corruption"
    with pytest.raises(presentation.FosinoprilPresentationError, match="binding drifted"):
        presentation.apply_source_a_presentation(ROOT, outputs, crops)
    outputs, crops = _inputs()
    del crops[presentation.RECIPES[-1].asset]
    with pytest.raises(presentation.FosinoprilPresentationError, match="frozen manifest"):
        presentation.apply_source_a_presentation(ROOT, outputs, crops)


def test_manifest_and_source_pin_fail_closed(tmp_path):
    raw = (ROOT / presentation.MANIFEST_ASSET).read_bytes()
    manifest_path = tmp_path / presentation.MANIFEST_ASSET
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(raw + b" ")
    outputs, crops = _inputs()
    before_outputs, before_crops = outputs.copy(), copy.deepcopy(crops)
    with pytest.raises(presentation.FosinoprilPresentationError, match="SHA-256 drifted"):
        presentation.apply_source_a_presentation(tmp_path, outputs, crops)
    manifest_path.write_bytes(raw)
    recipe = presentation.RECIPES[0]
    source_path = tmp_path / recipe.source_asset
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes((ROOT / recipe.source_asset).read_bytes() + b" ")
    with pytest.raises(presentation.FosinoprilPresentationError, match="SHA-256 drifted"):
        presentation.apply_source_a_presentation(tmp_path, outputs, crops)
    assert outputs == before_outputs
    assert crops == before_crops


def test_missing_source_is_not_replaced_by_archive_fallback(tmp_path):
    manifest_path = tmp_path / presentation.MANIFEST_ASSET
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes((ROOT / presentation.MANIFEST_ASSET).read_bytes())
    outputs, crops = _inputs()
    with pytest.raises(presentation.FosinoprilPresentationError, match="source is unavailable"):
        presentation.apply_source_a_presentation(tmp_path, outputs, crops)


def test_second_application_is_rejected_not_mislabelled_as_archive(repaired):
    outputs, crops, _, _ = repaired
    with pytest.raises(presentation.FosinoprilPresentationError, match="binding drifted"):
        presentation.apply_source_a_presentation(ROOT, outputs.copy(), copy.deepcopy(crops))


def test_rectangles_within_each_question_page_do_not_overlap():
    for source in presentation.SOURCE_SHA256:
        recipes = [recipe for recipe in presentation.RECIPES if recipe.source_asset == source]
        boxes = sorted(recipe.box for recipe in recipes)
        assert all(left[1] + left[3] <= right[1] for left, right in pairwise(boxes))

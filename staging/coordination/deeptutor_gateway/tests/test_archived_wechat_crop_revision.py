"""Source-pixel binding regressions, not human visual or teaching approval.

Synthetic checks are offline. Tests using ``local_sources`` require the two
existing local candidate packages and the separately generated repair manifest.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1 import archived_wechat_crop_revision as module
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanError,
)
from integrations.deeptutor_shchem_v1.shanghai_high_east2025_theme45_direct_visual_scan import (
    ShanghaiHighEast2025Theme45DirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.songjiang2025_theme2_direct_visual_scan import (
    Songjiang2025Theme2DirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.source_crop_revision import (
    SourceCropRevisionError,
)

WORKSPACE = Path(__file__).resolve().parents[4]
DB = WORKSPACE / "sh-chem-db"
REPAIRS = WORKSPACE / "runtime/deeptutor_shchem/crop_repairs_0.1.55_r1"
REPAIR_MANIFEST = REPAIRS / "crop_repair_manifest.json"
EXPECTED_VIEWS = {
    "SJ2025-EM-S2-Q8-P1": (
        "a9629baafc681405ba660ba4e0d6b68c7d82decce5f110255e5fc0690274e5cb",
        (160, 1275, 960, 50),
    ),
    "SHEAST2025-M05-B-T5-Q1-P1": (
        "d2cf264ac00788a9812ca5701f42ee2ee12addeaf984c66ce8f37ba6d89de040",
        (100, 588, 1080, 60),
    ),
    "SHEAST2025-M05-B-T5-Q2-P1": (
        "bdb9424b9bce142897da471a874fc507a07fc70c3f69e4358bb1043c833fd7ab",
        (100, 665, 1080, 100),
    ),
    "SHEAST2025-M05-B-T5-Q3-P1": (
        "10a8a27a642c5dc6abfeb3f6f290b31d7cd2ad2a76b1254f4b8a7dfd74569a3d",
        (100, 789, 1080, 55),
    ),
    "SHEAST2025-M05-B-T5-Q4-P1": (
        "565b5fbf038c255c69bad303acfd78ec88bc37fe5da3159f849b4d916d2c8aa1",
        (100, 856, 1080, 44),
    ),
}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _descriptor(recipe, original=b"archived fixture"):
    return {
        "crop_id": recipe.crop_id,
        "evidence_role": "question",
        "source_page": recipe.page,
        "sha256": recipe.archived_sha256,
        "bytes": len(original),
        "width": recipe.original_box[2],
        "height": recipe.original_box[3],
        "source_asset": recipe.source_asset,
        "source_sha256": recipe.source_sha256,
        "source_crop_box": list(recipe.original_box),
        "source_crop_box_convention": "xywh",
    }


def _png(seed=0):
    image = Image.new("RGB", (6, 5))
    image.putdata(
        [
            ((seed + x * 31) % 256, y * 43, (seed + x + y) % 256)
            for y in range(5)
            for x in range(6)
        ]
    )
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=0)
    return output.getvalue()


@pytest.fixture
def synthetic_source(tmp_path, monkeypatch):
    source = _png()
    original = b"verified archived fixture; not pixel evidence"
    recipe = module.CropRecipe(
        "FIXTURE-NODE",
        "FIXTURE-CROP",
        _sha(original),
        "source.png",
        _sha(source),
        1,
        (0, 0, 4, 4),
        (1, 1, 3, 2),
    )
    (tmp_path / recipe.source_asset).write_bytes(source)
    monkeypatch.setitem(module.RECIPES, recipe.crop_id, recipe)
    return tmp_path, recipe, source, original


@pytest.fixture(scope="module")
def local_sources():
    if not REPAIR_MANIFEST.is_file():
        pytest.skip("requires local archived source packages and repair manifest")
    manifest = json.loads(REPAIR_MANIFEST.read_bytes())
    readers = [
        Songjiang2025Theme2DirectVisualScanReader(DB),
        ShanghaiHighEast2025Theme45DirectVisualScanReader(DB),
    ]
    if not all(
        (reader.product_root / "candidate_manifest.json").is_file()
        for reader in readers
    ):
        pytest.skip("requires both local archived candidate packages")
    return manifest, [(reader, reader._snapshot()) for reader in readers]


def test_unregistered_crop_and_descriptor_pass_through_without_reading_files(
    monkeypatch,
):
    def forbidden_read(*_args, **_kwargs):
        pytest.fail("unregistered crops must not read source files or render")

    monkeypatch.setattr(module, "_source_bytes", forbidden_read)
    monkeypatch.setattr(module, "_render", forbidden_read)
    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    descriptor = {"crop_id": "UNKNOWN-CROP", "evidence_role": "unknown", "sha256": "x"}
    before = deepcopy(descriptor)
    raw = b"unchanged archived evidence"
    assert (
        module.recrop_archived_wechat_view(
            Path("missing"), "UNKNOWN", "UNKNOWN-CROP", raw
        )
        is raw
    )
    projected = module.project_archived_wechat_descriptor(
        Path("missing"), "UNKNOWN", descriptor
    )
    assert projected == before and projected is not descriptor
    assert descriptor == before


@pytest.mark.parametrize("entrypoint", ["descriptor", "pixels"])
def test_known_crop_rejects_wrong_node_before_source_read(
    synthetic_source, monkeypatch, entrypoint
):
    root, recipe, _source, original = synthetic_source
    monkeypatch.setattr(
        module, "_source_bytes", lambda *_: pytest.fail("binding must fail first")
    )
    with pytest.raises(SourceCropRevisionError, match="不属于当前题目"):
        if entrypoint == "descriptor":
            module.project_archived_wechat_descriptor(
                root, "OTHER-NODE", _descriptor(recipe)
            )
        else:
            module.recrop_archived_wechat_view(
                root, "OTHER-NODE", recipe.crop_id, original
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_role", "answer"),
        ("evidence_role", "shared_material"),
        ("evidence_role", "unknown"),
        ("sha256", "0" * 64),
        ("source_page", 2),
        ("width", 3),
        ("height", 3),
        ("source_sha256", "0" * 64),
        ("source_crop_box", [0, 1, 4, 4]),
        ("source_crop_box", None),
        ("source_crop_box", "0,0,4,4"),
        ("source_crop_box", {"x": 0}),
        ("source_asset", "wrong.png"),
        ("source_crop_box_convention", "ltrb"),
    ],
)
def test_descriptor_mismatches_fail_closed_before_pixel_read(
    synthetic_source, monkeypatch, field, value
):
    root, recipe, _source, original = synthetic_source
    descriptor = _descriptor(recipe, original)
    descriptor[field] = value
    before = deepcopy(descriptor)
    monkeypatch.setattr(
        module, "_source_bytes", lambda *_: pytest.fail("binding must fail first")
    )
    with pytest.raises(SourceCropRevisionError):
        module.project_archived_wechat_descriptor(root, recipe.node_id, descriptor)
    assert descriptor == before


def test_wrong_known_crop_id_cannot_select_another_nodes_recipe(synthetic_source):
    root, recipe, _source, original = synthetic_source
    descriptor = _descriptor(recipe, original)
    descriptor["crop_id"] = next(key for key in module.RECIPES if key != recipe.crop_id)
    with pytest.raises(SourceCropRevisionError, match="不属于当前题目"):
        module.project_archived_wechat_descriptor(root, recipe.node_id, descriptor)


def test_wrong_archived_pixels_are_not_repaired_or_rendered(
    synthetic_source, monkeypatch
):
    root, recipe, _source, _original = synthetic_source
    monkeypatch.setattr(
        module, "_source_bytes", lambda *_: pytest.fail("hash must fail first")
    )
    with pytest.raises(SourceCropRevisionError, match="原裁片"):
        module.recrop_archived_wechat_view(
            root, recipe.node_id, recipe.crop_id, b"wrong"
        )


@pytest.mark.parametrize("entrypoint", ["descriptor", "pixels"])
def test_source_changes_are_rechecked_even_after_a_successful_cached_render(
    synthetic_source, entrypoint
):
    root, recipe, _source, original = synthetic_source
    descriptor = _descriptor(recipe, original)
    module.recrop_archived_wechat_view(root, recipe.node_id, recipe.crop_id, original)
    source_path = root / recipe.source_asset
    old_stat = source_path.stat()
    changed = _png(17)
    assert len(changed) == old_stat.st_size
    source_path.write_bytes(changed)
    os.utime(source_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    with pytest.raises(SourceCropRevisionError, match="原页已经变化"):
        if entrypoint == "descriptor":
            module.project_archived_wechat_descriptor(root, recipe.node_id, descriptor)
        else:
            module.recrop_archived_wechat_view(
                root, recipe.node_id, recipe.crop_id, original
            )


def test_render_cache_uses_verified_bytes_and_box_not_path_or_timestamp(
    synthetic_source, monkeypatch
):
    root, recipe, source, original = synthetic_source
    module._render.cache_clear()
    first = module.recrop_archived_wechat_view(
        root, recipe.node_id, recipe.crop_id, original
    )
    assert (
        module.recrop_archived_wechat_view(
            root, recipe.node_id, recipe.crop_id, original
        )
        == first
    )
    assert module._render.cache_info().hits == 1
    source_path = root / recipe.source_asset
    old_stat = source_path.stat()
    changed = _png(91)
    assert len(source) == len(changed)
    source_path.write_bytes(changed)
    os.utime(source_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    new_recipe = replace(recipe, source_sha256=_sha(changed))
    monkeypatch.setitem(module.RECIPES, recipe.crop_id, new_recipe)
    second = module.recrop_archived_wechat_view(
        root, recipe.node_id, recipe.crop_id, original
    )
    assert second != first
    new_recipe = replace(new_recipe, box=(2, 1, 3, 2))
    monkeypatch.setitem(module.RECIPES, recipe.crop_id, new_recipe)
    third = module.recrop_archived_wechat_view(
        root, recipe.node_id, recipe.crop_id, original
    )
    assert third != second
    assert module._render.cache_info().misses == 3
    with (
        Image.open(io.BytesIO(changed)) as page,
        Image.open(io.BytesIO(third)) as output,
    ):
        assert output.mode == page.mode
        assert output.tobytes() == page.crop((2, 1, 5, 3)).tobytes()


@pytest.mark.parametrize(
    "box", [(-1, 0, 3, 2), (0, 0, 0, 2), (0, 0, 3, -1), (5, 0, 2, 2), (0, 4, 2, 2)]
)
def test_out_of_source_boxes_are_rejected_instead_of_padding(box):
    with pytest.raises(SourceCropRevisionError, match="越出原页"):
        module._render(_png(), box)


@pytest.mark.parametrize(
    "path_kind",
    ["missing", "outside_relative", "outside_absolute", "symlink", "junction"],
)
def test_source_paths_missing_outside_or_linked_are_rejected(
    synthetic_source, monkeypatch, path_kind
):
    root, recipe, source, original = synthetic_source
    if path_kind == "missing":
        changed_recipe = replace(recipe, source_asset="missing.png")
    elif path_kind.startswith("outside"):
        outside = root.parent / (root.name + "-outside.png")
        outside.write_bytes(source)
        asset = (
            "../" + outside.name if path_kind == "outside_relative" else str(outside)
        )
        changed_recipe = replace(recipe, source_asset=asset)
    else:
        # Exercise the platform's link/reparse rejection without requiring the
        # user to grant Windows symbolic-link creation privileges to tests.
        changed_recipe = recipe
        method = "is_symlink" if path_kind == "symlink" else "is_junction"
        original_check = getattr(Path, method)
        monkeypatch.setattr(
            Path,
            method,
            lambda path: path == root / recipe.source_asset or original_check(path),
        )
    monkeypatch.setitem(module.RECIPES, recipe.crop_id, changed_recipe)
    with pytest.raises(SourceCropRevisionError, match="原页不可用"):
        module.recrop_archived_wechat_view(
            root, recipe.node_id, recipe.crop_id, original
        )


def test_presentation_fingerprint_reads_no_files_and_tracks_only_relevant_recipes(
    monkeypatch,
):
    monkeypatch.setattr(
        Path, "read_bytes", lambda *_: pytest.fail("fingerprint is metadata-only")
    )
    recipe = next(iter(module.RECIPES.values()))
    catalog = {"papers": [{"atomic_chain": [{"atomic_part_id": recipe.node_id}]}]}
    before = deepcopy(catalog)
    first = module.presentation_fingerprint(catalog)
    assert first is not None and len(first) == 64
    assert module.presentation_fingerprint({"atomic_part_id": "UNKNOWN"}) is None
    assert module.presentation_fingerprint(catalog) == first
    assert catalog == before
    unrelated = next(
        row for row in module.RECIPES.values() if row.node_id != recipe.node_id
    )
    monkeypatch.setitem(module.RECIPES, unrelated.crop_id, replace(unrelated, page=99))
    assert module.presentation_fingerprint(catalog) == first
    monkeypatch.setitem(
        module.RECIPES, recipe.crop_id, replace(recipe, box=(1, 2, 3, 4))
    )
    assert module.presentation_fingerprint(catalog) != first


@pytest.mark.parametrize("node_id", EXPECTED_VIEWS)
def test_five_live_descriptors_routes_and_manifest_are_exact_source_pixels(
    local_sources, monkeypatch, node_id
):
    manifest, entries = local_sources
    reader, snapshot = next(
        (reader, snap) for reader, snap in entries if node_id in snap.by_master_id
    )
    record = snapshot.by_master_id[node_id]
    frozen_record = deepcopy(record)
    monkeypatch.setattr(reader, "_snapshot", lambda: snapshot)
    recipe = next(row for row in module.RECIPES.values() if row.node_id == node_id)
    crop = snapshot.crop_by_id[recipe.crop_id]
    archived = snapshot.output_bytes[crop["output_path"]]
    row = next(row for row in manifest["records"] if row["master_node_id"] == node_id)
    expected_sha, expected_box = EXPECTED_VIEWS[node_id]
    assert recipe.box == expected_box == tuple(row["crop_box"])
    assert row["role"] == row["evidence_role"] == crop["role"] == "question"
    assert recipe.source_asset == row["source_asset"] == crop["source_asset"]
    assert recipe.source_sha256 == row["source_sha256"] == crop["source_sha256"]
    assert (
        recipe.original_box
        == tuple(row["original_crop_box"])
        == tuple(crop["crop_box"])
    )
    assert recipe.archived_sha256 == _sha(archived) == row["supersedes_crop_sha256"]
    assert row["supersedes_crop_asset"] == crop["output_path"]
    assert recipe.page == row["source_page_number"] == crop["page_number"]
    paths = [
        DB / recipe.source_asset,
        DB / crop["output_path"],
        REPAIRS / row["crop_path"],
    ]
    before = {
        path: (path.stat().st_mtime_ns, _sha(path.read_bytes())) for path in paths
    }
    descriptor = next(
        row
        for row in reader.detail(node_id)["evidence_descriptors"]
        if row["crop_id"] == recipe.crop_id
    )
    payload = reader.question_crop(node_id, recipe.crop_id)
    assert (
        descriptor["crop_id"] == recipe.crop_id
    )  # Transport ID is not a newly invented identity.
    assert descriptor["archived_crop_sha256"] == recipe.archived_sha256
    assert descriptor["archived_source_crop_box"] == list(recipe.original_box)
    assert descriptor["source_asset"] == recipe.source_asset
    assert descriptor["source_sha256"] == recipe.source_sha256
    assert descriptor["source_crop_box"] == list(recipe.box)
    assert descriptor["source_crop_box_convention"] == "xywh"
    assert descriptor["source_page"] == recipe.page
    assert descriptor["evidence_role"] == "question"
    assert descriptor["presentation_revision_id"] == module.REVISION_ID
    assert (
        descriptor["sha256"]
        == payload.sha256
        == row["sha256"]
        == expected_sha
        == _sha(payload.data)
    )
    assert descriptor["bytes"] == row["bytes"] == len(payload.data)
    assert descriptor["width"] == row["width"] == recipe.box[2]
    assert descriptor["height"] == row["height"] == recipe.box[3]
    assert payload.data == (REPAIRS / row["crop_path"]).read_bytes()
    source = (DB / recipe.source_asset).read_bytes()
    assert _sha(source) == recipe.source_sha256
    with (
        Image.open(io.BytesIO(source)) as page,
        Image.open(io.BytesIO(payload.data)) as actual,
    ):
        x, y, width, height = recipe.box
        expected = page.crop((x, y, x + width, y + height))
        assert actual.mode == expected.mode == row["image_mode"]
        assert actual.size == expected.size == (width, height)
        assert actual.tobytes() == expected.tobytes()
    assert snapshot.by_master_id[node_id] == frozen_record
    assert snapshot.output_bytes[crop["output_path"]] == archived
    assert {
        path: (path.stat().st_mtime_ns, _sha(path.read_bytes())) for path in paths
    } == before
    assert row["human_visual_reviewed"] is False
    assert manifest["authority"]["teaching_use_approved"] is False


def test_all_52_archive_crops_unchanged_and_only_five_views_revised(
    local_sources, monkeypatch
):
    manifest, entries = local_sources
    assert manifest["count"] == len(manifest["records"]) == len(module.RECIPES) == 5
    assert set(EXPECTED_VIEWS) == {row.node_id for row in module.RECIPES.values()}
    paths = {DB / binding["path"] for binding in manifest["source_bindings"]}
    for _reader, snapshot in entries:
        paths.update(DB / crop["output_path"] for crop in snapshot.crop_by_id.values())
    before = {
        path: (path.stat().st_mtime_ns, _sha(path.read_bytes())) for path in paths
    }
    revised, unchanged, roles = set(), set(), Counter()
    for reader, snapshot in entries:
        monkeypatch.setattr(reader, "_snapshot", lambda snapshot=snapshot: snapshot)
        original_records = deepcopy(snapshot.records)
        for crop_id, crop in snapshot.crop_by_id.items():
            roles[crop["role"]] += 1
            original = snapshot.output_bytes[crop["output_path"]]
            assert _sha(original) == crop["sha256"]
            assert (DB / crop["output_path"]).read_bytes() == original
            if crop_id in module.RECIPES:
                revised.add(crop_id)
                continue  # Exact revised pixels are independently checked in the five cases above.
            descriptor = {
                "crop_id": crop_id,
                "evidence_role": crop["role"],
                "sha256": crop["sha256"],
                "bytes": len(original),
                "width": crop["width"],
                "height": crop["height"],
            }
            assert (
                module.recrop_archived_wechat_view(DB, "UNRELATED", crop_id, original)
                is original
            )
            assert (
                module.project_archived_wechat_descriptor(DB, "UNRELATED", descriptor)
                == descriptor
            )
            unchanged.add(crop_id)
        for record in snapshot.records:
            node_id = record["hierarchy"]["atomic_part_id"]
            detail = reader.detail(node_id)
            public = {row["crop_id"]: row for row in detail["evidence_descriptors"]}
            for archived in record["viewed_evidence"]:
                crop_id = archived["crop_id"]
                if crop_id not in module.RECIPES:
                    assert all(
                        public[crop_id][key] == value for key, value in archived.items()
                    )
                    assert "presentation_revision_id" not in public[crop_id]
                    crop = snapshot.crop_by_id[crop_id]
                    assert (
                        reader.question_crop(node_id, crop_id).data
                        == snapshot.output_bytes[crop["output_path"]]
                    )
            for answer in detail["answer_source_evidence"]:
                crop = snapshot.crop_by_id[answer["crop_id"]]
                assert answer["sha256"] == crop["sha256"]
                assert answer["evidence_role"] == "answer"
                assert "presentation_revision_id" not in answer
            for forbidden in snapshot.forbidden_crop_ids:
                with pytest.raises(MasterDirectVisualScanError) as caught:
                    reader.question_crop(node_id, forbidden)
                assert caught.value.status == 403
        assert snapshot.records == original_records
    assert revised == set(module.RECIPES)
    assert len(unchanged) == 47
    assert roles == {"question": 20, "shared_material": 7, "answer": 20, "unknown": 5}
    assert {
        path: (path.stat().st_mtime_ns, _sha(path.read_bytes())) for path in paths
    } == before

"""Lossless source-A display rectangles, independent of archived evidence.

These twenty explicit mappings repair inspected overlap/truncation.  They do
not redraw content, change source roles, merge versions, or grant review gates.
The caller must validate the frozen package and role relationships first.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

PRESENTATION_REVISION_ID = "fosinopril-source-a-source-pixel-recrop-20260912-r1"
PACKAGE_ASSET = "kb/formal/candidates/pending_v2/level_exam_2025_theme3_fosinopril_dual_source"
MANIFEST_ASSET = PACKAGE_ASSET + "/candidate_manifest.json"
MANIFEST_SHA256 = "7021ad2588be0d0aee82382e0f5ed7faa4fa7e16dbea284f88748f54e5117559"
_SOURCE_PREFIX = "02_等级考真题/2025-上海市-化学等级考-双源非官方回忆版/来源A-萌藤mountain/"
_PAPER4 = _SOURCE_PREFIX + "题卷-page-04.jpg"
_PAPER5 = _SOURCE_PREFIX + "题卷-page-05.jpg"
_ANSWER4 = _SOURCE_PREFIX + "机构解析-page-04.jpg"
SOURCE_SHA256 = {
    _PAPER4: "3529ccf5bb9b306920945ae39b90fefee6abf0ec0b2896a64ea551376547c6ac",
    _PAPER5: "4e35450c8382b603d2c07a6dea0018acb82ff3a5a318b6a83e3442ab46649b78",
    _ANSWER4: "6811b298d7b8452189724f16c32dbac0aed13d02ca3bb3e982855711009b127e",
}


class FosinoprilPresentationError(ValueError):
    """A frozen source/display binding changed; no partial repair is applied."""


@dataclass(frozen=True)
class PresentationRecipe:
    name: str
    source_asset: str
    page: int
    role: str
    archived_box: tuple[int, int, int, int]
    box: tuple[int, int, int, int]
    archived_sha256: str

    @property
    def asset(self) -> str:
        return PACKAGE_ASSET + "/evidence/" + self.name


# Every rectangle is (x, y, width, height) in the original JPEG.  A role is an
# assertion checked against the reader's source-bound role, never an assignment.
RECIPES = (
    PresentationRecipe("a-paper-p04-theme-route.png", _PAPER4, 4, "shared_material", (45, 410, 990, 445), (45, 450, 990, 425), "28fb9f0cf95eba8e7b7b47faa9ae4ef0d551af635502d1e9a8b8b92a60fb9d97"),
    PresentationRecipe("a-paper-p04-q01.png", _PAPER4, 4, "question", (45, 825, 990, 95), (45, 886, 990, 44), "d81a52acbf9fa0d4f9b2d55db0c49004fc859daeeb5981d72397ea7f1e648bac"),
    PresentationRecipe("a-paper-p04-q02.png", _PAPER4, 4, "question", (45, 900, 990, 75), (45, 932, 990, 40), "9f3bd35acf8b0925a684de8cf3a95388b6110e0a6d0fb11f2d742081f37d1480"),
    PresentationRecipe("a-paper-p04-q03.png", _PAPER4, 4, "question", (45, 950, 990, 115), (45, 975, 990, 80), "ec5148a4fe2850b09a46076b7973375958af0b24121306d1495ddca4844d7bb2"),
    PresentationRecipe("a-paper-p04-q04.png", _PAPER4, 4, "question", (45, 1030, 990, 120), (45, 1061, 990, 80), "30f959239b9a7c96c4d487adf288b8e87f2a5f85cdc466a7c897bba641959508"),
    PresentationRecipe("a-paper-p04-q05.png", _PAPER4, 4, "question", (45, 1120, 990, 90), (45, 1143, 990, 43), "2f9354886d23c26211a17274c1b87b5dc534a05bf55aae46f01539e9991cd0d9"),
    PresentationRecipe("a-paper-p04-q06.png", _PAPER4, 4, "question", (45, 1180, 990, 190), (45, 1190, 990, 143), "39b7b7bd86c2d59456225a3835268868e368113ae346fbc7993dfe50f26fd2c5"),
    PresentationRecipe("a-paper-p04-q07-start.png", _PAPER4, 4, "question", (45, 1330, 990, 96), (45, 1344, 1035, 82), "bc4c0c356503e369681f611afd47f5d15a90797e175d4429f493929891684819"),
    PresentationRecipe("a-paper-p05-q07-tail.png", _PAPER5, 5, "question", (45, 0, 990, 205), (45, 65, 990, 139), "6094d6250a7187ca44c3839d3b23c999ed06c218419ba1e4dda886c49c88e340"),
    PresentationRecipe("a-paper-p05-q08.png", _PAPER5, 5, "question", (45, 170, 990, 100), (45, 210, 990, 36), "d01f3e255cd02ec3e16fa58d5be164aa7d5de39fa7b6a9c8922994eb4e5ecd65"),
    PresentationRecipe("a-paper-p05-q09.png", _PAPER5, 5, "question", (45, 235, 990, 325), (45, 251, 990, 260), "8ca4791841cb437efb6a335afd97eca44e8fa6224c7b6cfbae3a5d1de521dca0"),
    PresentationRecipe("a-answer-p04-q01.png", _ANSWER4, 4, "answer", (45, 125, 985, 100), (45, 203, 985, 40), "a47d759e288313775f4620e974b7dbd1baee6768e37e7d7fe6ce322116a65309"),
    PresentationRecipe("a-answer-p04-q02.png", _ANSWER4, 4, "answer", (45, 185, 985, 90), (45, 247, 985, 35), "180e601f4b268ab7ff0394e98edee58aaf7d6cc552bc2b234e0773f19e0fe962"),
    PresentationRecipe("a-answer-p04-q03.png", _ANSWER4, 4, "answer", (45, 225, 985, 100), (45, 292, 985, 32), "d11efc1d961b68ba90dd3b0354119d518d28a7b18724fcafa505c0008bee41ab"),
    PresentationRecipe("a-answer-p04-q04.png", _ANSWER4, 4, "answer", (45, 270, 985, 120), (45, 337, 985, 30), "62accfcade7904673313a3f7596e6485237b36f66765ddbc498e5d56daaeeedb"),
    PresentationRecipe("a-answer-p04-q05-visual.png", _ANSWER4, 4, "answer", (45, 335, 985, 235), (45, 373, 985, 169), "5b1f61a287fda419d71827cd3d3add7f6b259d34724ee0a0caa098793c76df02"),
    PresentationRecipe("a-answer-p04-q06.png", _ANSWER4, 4, "answer", (45, 515, 985, 105), (45, 547, 985, 34), "b37e947d834bb1f059ed4651b97554e4de74af81c1fc3f3790a1f7ec3d0790fb"),
    PresentationRecipe("a-answer-p04-q07-visual.png", _ANSWER4, 4, "answer", (45, 565, 985, 220), (45, 592, 985, 128), "30ecd8619c7a686eea7063b09de8c5a2102d627ede32c43f920b329cf4c4a72e"),
    PresentationRecipe("a-answer-p04-q08.png", _ANSWER4, 4, "answer", (45, 720, 985, 85), (45, 732, 985, 31), "707857eb16d22b9517d08e9ff4b42632c5191c13fd369bb89c35fe8c506e1a69"),
    PresentationRecipe("a-answer-p04-q09-visual.png", _ANSWER4, 4, "answer", (45, 760, 985, 260), (45, 783, 985, 265), "d5d720cefffb0435f86d67c1e47d295db3234f033d03b6bb312cc61cf7624dac"),
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FosinoprilPresentationError(message)


def _read_pinned(root: Path, asset: str, expected_sha256: str) -> bytes:
    try:
        path = root / asset
        path.resolve(strict=True).relative_to(root)
        cursor = path
        while cursor != root:
            _require(not cursor.is_symlink() and not cursor.is_junction(), "linked source is not allowed")
            cursor = cursor.parent
        raw = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise FosinoprilPresentationError("required frozen source is unavailable") from exc
    _require(_sha256(raw) == expected_sha256, "frozen source SHA-256 drifted")
    return raw


def apply_source_a_presentation(
    root: Path, outputs: dict[str, bytes], crops: dict[str, dict[str, Any]]
) -> None:
    """Replace only twenty validated in-memory display crops, atomically.

    ``root`` is the ``sh-chem-db`` directory. No source file is written. Source
    SHA/asset, role, crop ID and archive crop_box_xywh remain unchanged; crop_box
    and dimensions describe the new display, with both bindings in metadata.
    """
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise FosinoprilPresentationError("source root is unavailable") from exc
    manifest = json.loads(_read_pinned(root, MANIFEST_ASSET, MANIFEST_SHA256))
    archived = {item["asset"]: item for item in manifest["crops"]}
    source_manifest = {item["asset"]: item for item in manifest["source_assets"]}
    source_bytes: dict[str, bytes] = {}
    updates: list[tuple[str, bytes, dict[str, Any]]] = []
    for recipe in RECIPES:
        asset = recipe.asset
        crop, original = crops.get(asset), outputs.get(asset)
        item = archived.get(asset, {})
        expected_source_sha = SOURCE_SHA256[recipe.source_asset]
        _require(
            isinstance(crop, dict) and isinstance(original, bytes)
            and item.get("source_asset") == recipe.source_asset
            and item.get("sha256") == recipe.archived_sha256
            and item.get("crop_box_xywh") == list(recipe.archived_box)
            and source_manifest.get(recipe.source_asset, {}).get("sha256") == expected_source_sha,
            "presentation mapping differs from the frozen manifest",
        )
        _require(
            crop.get("asset") == asset and crop.get("output_path") == asset
            and crop.get("sha256") == recipe.archived_sha256
            and _sha256(original) == recipe.archived_sha256
            and crop.get("crop_box") == list(recipe.archived_box)
            and crop.get("crop_box_xywh") == list(recipe.archived_box)
            and crop.get("source_asset") == recipe.source_asset
            and crop.get("source_sha256") == expected_source_sha
            and crop.get("source_page_number") == recipe.page
            and crop.get("role") == recipe.role
            and crop.get("bytes") == len(original)
            and (crop.get("width"), crop.get("height")) == recipe.archived_box[2:]
            and crop.get("dimensions") == list(recipe.archived_box[2:])
            and "presentation_revision" not in crop,
            "archived crop/role/source binding drifted",
        )
        if recipe.source_asset not in source_bytes:
            source_bytes[recipe.source_asset] = _read_pinned(root, recipe.source_asset, expected_source_sha)
        with Image.open(io.BytesIO(source_bytes[recipe.source_asset])) as source:
            x, y, width, height = recipe.box
            _require(0 <= x < x + width <= source.width and 0 <= y < y + height <= source.height,
                     "presentation rectangle is outside the source page")
            buffer = io.BytesIO()
            source.crop((x, y, x + width, y + height)).save(
                buffer, format="PNG", optimize=False, compress_level=9
            )
        raw = buffer.getvalue()
        display_sha = _sha256(raw)
        updates.append((asset, raw, {
            "archived_sha256": recipe.archived_sha256,
            "archived_crop_box": list(recipe.archived_box),
            "archived_bytes": len(original),
            "sha256": display_sha, "bytes": len(raw),
            "width": width, "height": height, "dimensions": [width, height],
            "crop_box": list(recipe.box),
            "presentation_revision": {
                "revision_id": PRESENTATION_REVISION_ID,
                "operation": "lossless_source_pixel_recrop",
                "source_asset": recipe.source_asset,
                "source_sha256": expected_source_sha,
                "source_page": recipe.page,
                "archived_crop_box": list(recipe.archived_box),
                "archived_sha256": recipe.archived_sha256,
                "source_crop_box": list(recipe.box),
                "display_sha256": display_sha,
                "source_pixels_altered": False,
                "source_role_changed": False,
                "original_files_modified": False,
                "human_review_complete": False,
            },
        }))
    # Finish all checks and rendering before the first mutation of caller state.
    for asset, raw, metadata in updates:
        outputs[asset] = raw
        crops[asset].update(metadata)

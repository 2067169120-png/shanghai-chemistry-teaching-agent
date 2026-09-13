"""Inspected source-pixel display repairs; archived evidence stays intact.

The recipes bind an existing node/crop pair to its original source page. They
restore clipped ink or remove adjacent-question fragments, never redraw text,
assign source roles, alter answers, or grant teaching approval.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from .source_crop_revision import SourceCropRevisionError

REVISION_ID = "archived-wechat-source-recrop-20260910-r3"


@dataclass(frozen=True)
class CropRecipe:
    node_id: str
    crop_id: str
    archived_sha256: str
    source_asset: str
    source_sha256: str
    page: int
    original_box: tuple[int, int, int, int]
    box: tuple[int, int, int, int]
    evidence_role: str = "question"
    related_node_ids: tuple[str, ...] = ()


_SONGJIANG_PAGE = (
    "03_各区一二模/松江区/2025-松江区-二模-化学试卷与参考答案/试卷-page-04.png"
)
_EAST_PAGE = (
    "04_市重点校考卷/上海中学东校/高二/"
    "2024学年度第二学期5月阶段性素质评估-B卷-化学试卷与非官方参考答案/"
    "试卷-page-05.jpg"
)
_EAST_SHA = "f3ced481f510cf22e9b3936efc1a1de13eb015c2b672fabb2fbabaa56ff996a6"

# Boxes are (x, y, width, height) in the verified source page, not LTRB.
RECIPES = {
    row.crop_id: row
    for row in (
        CropRecipe(
            "SJ2025-EM-S2-Q8-P1",
            "SJ25T2-C-6838f37eb5394b1dae52c57e",
            "a84b8b52900c0f78d6c5c81fa4ccde0c0fb63a1bb5333336ffcccbbd2168336b",
            _SONGJIANG_PAGE,
            "1c95e0dc77a285ed189f6d9622184a4ffaaf5ea6161d8a92d68f555eac63b22e",
            4,
            (160, 1275, 960, 70),
            (160, 1275, 960, 50),
        ),
        CropRecipe(
            "SHEAST2025-M05-B-T5-Q1-P1",
            "SHEAST2025-CROP-3b69753dc39c938942a9e3be",
            "096d2f82acce996b24c7a9f02cb621d86ff58bab5fe05c1a956608f41c76ed91",
            _EAST_PAGE,
            _EAST_SHA,
            5,
            (100, 605, 1080, 75),
            (100, 588, 1080, 60),
        ),
        CropRecipe(
            "SHEAST2025-M05-B-T5-Q2-P1",
            "SHEAST2025-CROP-d112caf4069295bdc8a12284",
            "58ac98ee7ced85401d511c4f0afe5f27bb0998ce4440b5554ece0f67b9e17547",
            _EAST_PAGE,
            _EAST_SHA,
            5,
            (100, 650, 1080, 155),
            (100, 665, 1080, 100),
        ),
        CropRecipe(
            "SHEAST2025-M05-B-T5-Q3-P1",
            "SHEAST2025-CROP-ea3efb8b2df043803cf53e38",
            "121554163d1f0e33505f38219dc7993c19ff5a01e7ec448d01e5576769112d20",
            _EAST_PAGE,
            _EAST_SHA,
            5,
            (100, 800, 1080, 70),
            (100, 789, 1080, 55),
        ),
        CropRecipe(
            "SHEAST2025-M05-B-T5-Q4-P1",
            "SHEAST2025-CROP-dd687f6d7113ceb5a2c19c66",
            "69e47d3595e60e34dab3c360faed22a64631fd84f0a4875d284210a1ec721c57",
            _EAST_PAGE,
            _EAST_SHA,
            5,
            (100, 865, 1080, 50),
            (100, 856, 1080, 44),
        ),
    )
}

# Existing apparatus references now include their actual source instructions.
# Full-page archival crops remain available in the source reader, but are not
# needed as classroom shared materials once these complete blocks are present.
SHARED_RECIPES = {
    row.crop_id: row
    for row in (
        CropRecipe(
            "SJ2025-EM-S2-Q1-P1",
            "SJ25T2-C-783aedf98189899baf2c2909",
            "e521b1f65aaeea270c07a5c9d9689101a532d3824a6f78d6945766dc7b11ac47",
            "03_各区一二模/松江区/2025-松江区-二模-化学试卷与参考答案/试卷-page-03.png",
            "c58a932b3387e809cc3cd9dc09f5f954a8a7b7256fa9aa9040ba21fbad0c4191",
            3,
            (510, 400, 300, 225),
            (185, 290, 920, 335),
            "shared_material",
            ("SJ2025-EM-S2-Q2-P1", "SJ2025-EM-S2-Q3-P1"),
        ),
        CropRecipe(
            "SJ2025-EM-S2-Q4-P1",
            "SJ25T2-C-377ff6ffad094904d6cf5ea4",
            "014dacbf3e1ec9d7583a33a9f09e53ce54943b3acbf0238a7839cfd7a66ff5fa",
            "03_各区一二模/松江区/2025-松江区-二模-化学试卷与参考答案/试卷-page-03.png",
            "c58a932b3387e809cc3cd9dc09f5f954a8a7b7256fa9aa9040ba21fbad0c4191",
            3,
            (650, 900, 380, 330),
            (185, 905, 920, 455),
            "shared_material",
            (
                "SJ2025-EM-S2-Q5-P1",
                "SJ2025-EM-S2-Q6-P1",
                "SJ2025-EM-S2-Q7-P1",
            ),
        ),
    )
}

# Source-page margins found during actual DOCX/PDF page inspection. Keep the
# original question ink and answer spaces; exclude only the unrelated margins.
MARGIN_RECIPES = {
    row.crop_id: row
    for row in (
        CropRecipe(
            "SJ2025-EM-S2-Q4-P1",
            "SJ25T2-C-ee95745bec37e2d5033436dd",
            "d66c6024f18978283042fd172a10a0eb18d4cfdad19a0e9a04a22bc9a5c4fcae",
            "03_各区一二模/松江区/2025-松江区-二模-化学试卷与参考答案/试卷-page-03.png",
            "c58a932b3387e809cc3cd9dc09f5f954a8a7b7256fa9aa9040ba21fbad0c4191",
            3,
            (160, 1360, 960, 295),
            (160, 1360, 960, 215),
        ),
        CropRecipe(
            "SJ2025-EM-S2-Q5-P1",
            "SJ25T2-C-f7d24d51145ebd9426681cea",
            "a002cb521dc4219ceadd632a5275df49775b9803e2ef80bb983d3d25be1b06bf",
            _SONGJIANG_PAGE,
            "1c95e0dc77a285ed189f6d9622184a4ffaaf5ea6161d8a92d68f555eac63b22e",
            4,
            (160, 215, 960, 95),
            (160, 225, 960, 85),
        ),
    )
}


def presentation_fingerprint(catalog: Mapping[str, Any]) -> str | None:
    """Bind native catalog snapshots to relevant recipes without file reads."""
    present: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            node_id = value.get("atomic_part_id")
            if isinstance(node_id, str):
                present.add(node_id)
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(catalog)
    selected = [
        asdict(row)
        for row in (
            *RECIPES.values(),
            *SHARED_RECIPES.values(),
            *MARGIN_RECIPES.values(),
        )
        if present.intersection((row.node_id, *row.related_node_ids))
    ]
    if not selected:
        return None
    payload = {
        "revision_id": REVISION_ID,
        "png_encoding": "source-mode-png-compress9-v1",
        "recipes": sorted(selected, key=lambda row: row["crop_id"]),
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _recipe(node_id: str, crop_id: str) -> CropRecipe | None:
    recipe = (
        RECIPES.get(crop_id)
        or SHARED_RECIPES.get(crop_id)
        or MARGIN_RECIPES.get(crop_id)
    )
    if recipe is not None and node_id not in (recipe.node_id, *recipe.related_node_ids):
        raise SourceCropRevisionError("返工裁图不属于当前题目。")
    return recipe


def _source_bytes(root: Path, recipe: CropRecipe) -> bytes:
    try:
        root = root.resolve(strict=True)
        source = root / recipe.source_asset
        source.resolve(strict=True).relative_to(root)
        cursor = source
        while cursor != root:
            if cursor.is_symlink() or cursor.is_junction():
                raise ValueError("linked source")
            cursor = cursor.parent
        raw = source.read_bytes()
    except (OSError, ValueError) as exc:
        raise SourceCropRevisionError("返工所需原页不可用，请核对来源。") from exc
    if hashlib.sha256(raw).hexdigest() != recipe.source_sha256:
        raise SourceCropRevisionError("返工原页已经变化，请重新核对裁剪边界。")
    return raw


@lru_cache(maxsize=8)
def _render(source: bytes, box: tuple[int, int, int, int]) -> bytes:
    # Cache exact verified bytes and the rectangle, not paths or timestamps.
    x, y, width, height = box
    with Image.open(io.BytesIO(source)) as image:
        if not (
            0 <= x < x + width <= image.width and 0 <= y < y + height <= image.height
        ):
            raise SourceCropRevisionError("返工裁图范围越出原页。")
        output = io.BytesIO()
        image.crop((x, y, x + width, y + height)).save(
            output, format="PNG", optimize=False, compress_level=9
        )
        return output.getvalue()


def recrop_archived_wechat_view(
    root: Path, node_id: str, crop_id: str, original: bytes
) -> bytes:
    recipe = _recipe(node_id, crop_id)
    if recipe is None:
        return original
    if hashlib.sha256(original).hexdigest() != recipe.archived_sha256:
        raise SourceCropRevisionError("原裁片与返工绑定不一致，请重新读取题目。")
    return _render(_source_bytes(root, recipe), recipe.box)


def project_archived_wechat_descriptor(
    root: Path, node_id: str, descriptor: Mapping[str, Any]
) -> dict[str, Any]:
    item = dict(descriptor)
    recipe = _recipe(node_id, item.get("crop_id", ""))
    if recipe is None:
        return item
    source_box = item.get("source_crop_box", recipe.original_box)
    if (
        item.get("evidence_role") != recipe.evidence_role
        or item.get("sha256") != recipe.archived_sha256
        or item.get("source_page") != recipe.page
        or (item.get("width"), item.get("height")) != recipe.original_box[2:]
        or item.get("source_sha256", recipe.source_sha256) != recipe.source_sha256
        or item.get("source_asset", recipe.source_asset) != recipe.source_asset
        or item.get("source_crop_box_convention", "xywh") != "xywh"
        or not isinstance(source_box, (list, tuple))
        or tuple(source_box) != recipe.original_box
    ):
        raise SourceCropRevisionError("返工题图描述与原始题面绑定不一致。")
    data = _render(_source_bytes(root, recipe), recipe.box)
    item.update(
        archived_crop_sha256=recipe.archived_sha256,
        archived_source_crop_box=list(recipe.original_box),
        source_asset=recipe.source_asset,
        source_sha256=recipe.source_sha256,
        source_crop_box=list(recipe.box),
        source_crop_box_convention="xywh",
        sha256=hashlib.sha256(data).hexdigest(),
        bytes=len(data),
        width=recipe.box[2],
        height=recipe.box[3],
        presentation_revision_id=REVISION_ID,
        presentation_note_zh=(
            "共同材料已按原页补全说明和图示，原裁片与来源记录保留。"
            if recipe.evidence_role == "shared_material"
            else "题面边界已按原页修订，原裁片与来源记录保留。"
        ),
    )
    return item

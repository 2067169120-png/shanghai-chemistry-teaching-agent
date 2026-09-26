"""Exact source-pixel presentation revisions; archived evidence stays untouched.

The reader first validates its original product and explicit crop relationships.
This layer then narrows those already-authorized views using inspected source
page coordinates. It never discovers roles from a filename or changes pixels.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image

REVISION_ID = "fengxian-theme2-source-recrop-20260909-r1"
_SOURCE = "03_各区一二模/奉贤区/2025-奉贤区-二模-化学试卷与参考答案"
_PAGE_HASHES = {
    3: "0c9ed12777d56b2d8ddcaca634de757b7f293eba3f00353b7d9625eba9566ecc",
    4: "f179440e5723313122c6aa900f68526a0117ea0616ba875569320dc4ec716de4",
}
# crop_id -> (original crop SHA-256, source page, left/top/right/bottom).
# All twelve rectangles were inspected against the original page. Shared
# apparatus and titration context remain separate, explicitly associated views.
FENGXIAN_RECROPS = {
    "paper-p03-q1": (
        "9b656540352ff4a418b7bcbd15143f3407085abd6f3643f551c64fbe3300b968",
        3,
        (145, 687, 1135, 726),
    ),
    "paper-p03-q2": (
        "4fc55b18bd6694e0abf4c08ae165eb2beea8e2d21a112072b1cf2e42ff0232be",
        3,
        (145, 731, 1135, 769),
    ),
    "paper-p03-q3": (
        "fb39d7b3435d1876d3c2428bbd3bc89a0db1c6dc4550fd4dd41bff0cdc5487b5",
        3,
        (145, 774, 1135, 850),
    ),
    "paper-p03-q4-graph": (
        "9a7a2a17a3f24e9d029c7e440bcab457a8515c4e114cbdc1e6c8203081bb18db",
        3,
        (145, 857, 1135, 1248),
    ),
    "paper-p03-q5": (
        "1ba5706be27909728199bf1fe764d4d209b7f6c79fa597957f7c684665b2d0fb",
        3,
        (145, 1266, 1135, 1350),
    ),
    "paper-p03-q6": (
        "63d34b389e3c7c116943af95aa8332ba120cab8321a1b1ecb33d34f61bb4634b",
        3,
        (145, 1355, 1135, 1394),
    ),
    "paper-p03-theme2-apparatus": (
        "4278b707250016d4397dbeeb628753935dab71708d7dd20dae3868e089fd2b16",
        3,
        (145, 208, 1135, 672),
    ),
    "paper-p03-titration-stimulus": (
        "4d231f62b427d3c16fd8cb8bae880000bc34e53c8491a5004a1a17760531b163",
        3,
        (145, 1396, 1135, 1655),
    ),
    "paper-p04-q7": (
        "0ca1eb9890986b7ca47c0624d0e88068f7e96bc625cb24ac91e12793a4605aef",
        4,
        (145, 197, 1135, 279),
    ),
    "paper-p04-q8-burette": (
        "007472278597c67c87397612c75f8984132c68710f87e4a06bfff1b8cc0ef60b",
        4,
        (145, 285, 1135, 551),
    ),
    "paper-p04-q9-table": (
        "cb8f96fcd7817d0d6797ea09032f9f2e3c8fb6d2c08d44c624e9d7962a084b7a",
        4,
        (145, 578, 1135, 879),
    ),
    "paper-p04-q10": (
        "1b3797269f0b583bfaaebd873a0a5ce904d136fc90e9980ebbc96cc111ce2570",
        4,
        (145, 881, 1135, 1090),
    ),
}
RECIPE_SHA256 = hashlib.sha256(
    json.dumps(
        [REVISION_ID, _SOURCE, _PAGE_HASHES, FENGXIAN_RECROPS],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()


class SourceCropRevisionError(ValueError):
    """A presentation revision no longer matches its exact archived source."""


def recrop_fengxian_view(root: Path, crop_id: str, original: bytes) -> bytes:
    recipe = FENGXIAN_RECROPS.get(crop_id)
    if recipe is None:
        return original
    expected, page, box = recipe
    if hashlib.sha256(original).hexdigest() != expected:
        raise SourceCropRevisionError("原裁图版本与返工记录不一致。")
    source = root / _SOURCE / f"试卷-page-0{page}.png"
    resolved_root = root.resolve(strict=True)
    try:
        source.resolve(strict=True).relative_to(resolved_root)
        cursor = source
        while cursor != root:
            if cursor.is_symlink() or cursor.is_junction():
                raise ValueError("linked source")
            cursor = cursor.parent
        raw = source.read_bytes()
    except (OSError, ValueError) as exc:
        raise SourceCropRevisionError("返工所需的原页不可用。") from exc
    if hashlib.sha256(raw).hexdigest() != _PAGE_HASHES[page]:
        raise SourceCropRevisionError("原页发生变化，请重新核对裁剪边界。")
    with Image.open(io.BytesIO(raw)) as image:
        left, top, right, bottom = box
        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
            raise SourceCropRevisionError("返工裁框越出原页。")
        output = io.BytesIO()
        image.crop(box).save(output, format="PNG")
    return output.getvalue()

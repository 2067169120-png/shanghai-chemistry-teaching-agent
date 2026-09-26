"""Inspected Datong source-pixel views; no archive writes or authority changes."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from .source_crop_revision import SourceCropRevisionError

REVISION_ID = "datong-source-recrop-20260909-r1"
SOURCE_PATH = (
    "staging/wechat/live_articles_20260802/"
    "2025-2026学年-大同中学-高一上期中-上海初高中化学/"
    "article_images/article-image-01.png"
)
SOURCE_SHA256 = "41a0a8904d923fe0677d3f0224719f96df247510905ffd80d47ee926740b469d"
# Exact archived crop IDs and SHA-256, followed by original long-page LTRB.
# These are presentation recipes, not inferred source-role assignments.
RECROPS = {
    "DT2025-H1-Q01-E1": (
        "42cac2ea9f6eee05c8be61fbed19f5f039be079e9fc8f2b192d4d394756dd529",
        (170, 565, 956, 777),
    ),
    "DT2025-H1-Q02-E1": (
        "337ba2cd048a982adc8e657e5e5dbf2e56e5e2e5730f557f7be0667f705d4349",
        (170, 781, 956, 853),
    ),
    "DT2025-H1-Q03-E1": (
        "fb61d2e0244fa7fb79b1f8785c35a47edacaa5d859938f205c7dbb70d09dc4ea",
        (170, 859, 956, 895),
    ),
    "DT2025-H1-Q04-E1": (
        "cad523a6c7f3ad0c04256808a95f3e2c1eddd3da828334d42a0c8a4c0c9b5953",
        (170, 902, 956, 981),
    ),
    "DT2025-H1-Q05-E1": (
        "0c9c7bc3b33d2f19f4d4b2c9ddfa8aa88ab09baca06c71904f53e67437e23ed2",
        (170, 989, 956, 1067),
    ),
    "DT2025-H1-Q06-E1": (
        "bea2b6844c9d44f33fb00fdf6837fb991973ba1e6e5f61d3429fa838b6553902",
        (170, 1075, 956, 1232),
    ),
    "DT2025-H1-Q07-E1": (
        "82b3ce9bd7a844a2ebca467d7ebcb1ed5040befc8aba736d5842ce9f9771ca58",
        (170, 1241, 956, 1352),
    ),
    "DT2025-H1-Q08-E1": (
        "4439be4a14dfa128431b06caf58c5ad7c0a8bf46cd88b25f4cab0cacfa090098",
        (155, 1638, 936, 1848),
    ),
    "DT2025-H1-Q09-E1": (
        "96937902407da606a1ce3961a720d9fd7b569a3429aacccc68ab0c0f06e7ec68",
        (155, 1850, 936, 1882),
    ),
    "DT2025-H1-SHARED-SECTION_1": (
        "85760549c35634a16e31cf4395a591f648752567d017354dda8e9545aa1ba07f",
        (164, 430, 956, 555),
    ),
    "DT2025-H1-SHARED-VIS_Q01_APPARATUS": (
        "f9d48936ef9b59abb699cd0f9a4c4547d982ef3bc212d36340be1d743388d604",
        (795, 644, 891, 773),
    ),
    "DT2025-H1-SHARED-VIS_Q08_APPARATUS_SET": (
        "1a0adb524dc36263554e40585a5f712cac418b7cef75b0797766e60409cbf748",
        (160, 1667, 777, 1847),
    ),
    # All three archived parts explicitly bind to the same parent Q33. The
    # old part strips omitted A-E's conditions. Serve the complete parent view;
    # identical PNG hashes let the document renderer print it once for Q33.
    "DT2025-H1-Q33-P01-E1": (
        "34f8309d3a846d97281ac50fcc4e19fa63e067183f647e4275a2b64109571304",
        (105, 6211, 939, 6498),
    ),
    "DT2025-H1-Q33-P02-E1": (
        "9863c0334b39588456f8bcac6fb7a93f873f95aed9e195c80f769ca3b3ceeee3",
        (105, 6211, 939, 6498),
    ),
    "DT2025-H1-Q33-P03-E1": (
        "2ed718a55136798eeaf2698c782006d95012a9909b4af423dfb1c61ed3a764e7",
        (105, 6211, 939, 6498),
    ),
    "DT2025-H1-Q14-E1": (
        "8bb87ff8cdd9c33d9af179fe82221f8992cca5595270870523a6b199751db1f6",
        (160, 2563, 935, 2636),
    ),
    "DT2025-H1-Q15-E1": (
        "9bc4345f76996b708c380ee32017cf85353e006b4f7dfc86d0785fe02be95dfc",
        (160, 2736, 935, 2768),
    ),
    "DT2025-H1-Q18-E1": (
        "33a6838ab60726e3d0e46254c4e4c1b5b989c91e9ff77eff0be118b340f66355",
        (125, 3261, 935, 3338),
    ),
    "DT2025-H1-Q21-E1": (
        "d823dba5faa73bed6e40cbd31b29e1a877cdeeba88d0d602bde438f810e0a4a0",
        (125, 3750, 935, 3830),
    ),
    "DT2025-H1-Q24-E1": (
        "b3702f83b998ab5f1d210d29b9aa30b17b35ef755734233d3f3b65af1db96493",
        (125, 4282, 935, 4470),
    ),
    "DT2025-H1-Q30-E1": (
        "88dcdc41f2cf2fbced0cdcaf51e1a75ea73603bb93a787ad480e7c4b6a72f641",
        (130, 5496, 935, 5605),
    ),
    "DT2025-H1-Q38-E1": (
        "d0077356b14d6a9851122b213a795b0e68fdd479ca9f7826e92cbf5c19b6a842",
        (105, 7347, 935, 7430),
    ),
    "DT2025-H1-SHARED-SECTION_2": (
        "e8c8bf7172b4c5132193150a0073b23583e621e6fb49d218ff1d3c767ffb676c",
        (160, 1970, 935, 2085),
    ),
    "DT2025-H1-SHARED-SECTION_3": (
        "d903450bc806de39b8774d226e4a2c0bf16e6b13bf1844e98485b00aeec5c90e",
        (125, 3450, 935, 3480),
    ),
    "DT2025-H1-SHARED-SECTION_4": (
        "95b55cb9675bd84902c1b8c5bd8e1c842591cd07246084e1262ec3abc4f6c974",
        (130, 5290, 940, 5356),
    ),
    "DT2025-H1-SHARED-SECTION_5": (
        "4616b2bff39977bb752ac6e2269c5ad949ba4335f781bffa504afcb51899ab83",
        (110, 6720, 935, 6756),
    ),
}
# Explicit upstream shared-figure bindings, checked against the two source boxes.
_COVERED_BY = {
    "DT2025-H1-SHARED-VIS_Q01_APPARATUS": "DT2025-H1-Q01-E1",
    "DT2025-H1-SHARED-VIS_Q08_APPARATUS_SET": "DT2025-H1-Q08-E1",
}


def _source_bytes(root: Path) -> bytes:
    root = root.resolve(strict=True)
    source = root / SOURCE_PATH
    try:
        source.resolve(strict=True).relative_to(root)
        cursor = source
        while cursor != root:
            if cursor.is_symlink() or cursor.is_junction():
                raise ValueError("linked source")
            cursor = cursor.parent
        data = source.read_bytes()
    except (OSError, ValueError) as exc:
        raise SourceCropRevisionError("大同返工所需的原页不可用。") from exc
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise SourceCropRevisionError("大同原页已变化，请重新核对裁图。")
    return data


@lru_cache(maxsize=24)
def _source_view(source: bytes, crop_id: str) -> bytes:
    # Cache by exact verified bytes, never only by a path or timestamp.
    box = RECROPS[crop_id][1]
    with Image.open(io.BytesIO(source)) as image:
        left, top, right, bottom = box
        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
            raise SourceCropRevisionError("大同裁图边界越出原页。")
        out = io.BytesIO()
        image.crop(box).save(out, format="PNG")
        return out.getvalue()


def recrop_datong_view(root: Path, crop_id: str, original: bytes) -> bytes:
    recipe = RECROPS.get(crop_id)
    if recipe is None:
        return original
    if hashlib.sha256(original).hexdigest() != recipe[0]:
        raise SourceCropRevisionError("大同原裁片与返工记录不一致。")
    return _source_view(_source_bytes(root), crop_id)


def project_datong_descriptor(
    root: Path, descriptor: Mapping[str, Any]
) -> dict[str, Any]:
    item = dict(descriptor)
    crop_id = item.get("crop_id")
    recipe = RECROPS.get(crop_id)
    if recipe is None:
        return item
    if item.get("sha256") != recipe[0] or item.get("evidence_role") not in {
        "question",
        "shared_material",
    }:
        raise SourceCropRevisionError("大同题图描述与原归档不一致。")
    data = _source_view(_source_bytes(root), crop_id)
    item["archived_crop_sha256"] = item["sha256"]
    item["sha256"] = hashlib.sha256(data).hexdigest()
    if "crop_sha256" in item:
        item["crop_sha256"] = item["sha256"]
    for field, value in (
        ("width", recipe[1][2] - recipe[1][0]),
        ("height", recipe[1][3] - recipe[1][1]),
        ("bytes", len(data)),
    ):
        if field in item:
            item[field] = value
    item["presentation_revision_id"] = REVISION_ID
    if "visual_inspection_status" in item:
        item["visual_inspection_status"] = (
            "source_page_and_repaired_crop_actually_viewed_by_primary_model_2026-09-09"
        )
    return item


def visible_evidence(descriptors: Any) -> list[Mapping[str, Any]]:
    """Suppress only a verified embedded figure when its complete question is here.

    A shared-only or different-question view keeps the figure. Source descriptors
    and their relationships remain available; only the presentation is reduced.
    """
    rows = [row for row in descriptors or [] if isinstance(row, Mapping)]
    questions = {
        row.get("crop_id")
        for row in rows
        if row.get("evidence_role") == "question"
        and row.get("presentation_revision_id") == REVISION_ID
    }
    return [
        row
        for row in rows
        if not (
            row.get("evidence_role") == "shared_material"
            and row.get("presentation_revision_id") == REVISION_ID
            and row.get("crop_id") in _COVERED_BY
            and _COVERED_BY.get(row.get("crop_id")) in questions
        )
    ]

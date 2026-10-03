"""Read-only candidate metadata for verified textbook pages, never model input.

The caller verifies the PDF separately. This adapter opens one fixed JSONL file
and never follows any path recorded in its contents or opens PDF/image bytes.
"""

from __future__ import annotations

import json
import math
import re
import stat
from pathlib import Path

CATALOG_RELATIVE_PATH = Path(
    "sh-chem-db/kb/textbook_knowledge_map_v1_2026-08-28/visual_assets.jsonl"
)
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_LINE_BYTES = 32 * 1024
MAX_ROWS = 2048
MAX_TEXT = 4000
MAX_PAGES = 3000

MISSING = "教材素材目录尚未接入；已核验的教材原页仍可查看。"
UNAVAILABLE = "部分教材素材元数据无法读取或核对，暂未显示；已核验的教材原页仍可查看。"
CONFLICT = "教材素材目录存在重复或冲突的 ID，相关记录全部暂不显示。"
STALE = "有教材素材对应其他 PDF 版本，暂未显示；已核验的教材原页仍可查看。"
_NOTICES = (MISSING, UNAVAILABLE, CONFLICT, STALE)
_CLOSED_FLAGS = (
    "human_reviewed", "retrieval_ready", "teaching_use_allowed",
    "generation_allowed", "publication_allowed",
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("non-finite JSON constant")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _text(value, limit=180, *, multiline=False):
    if (
        not isinstance(value, str) or not value.strip() or len(value) > limit
        or any((ord(char) < 32 and (not multiline or char not in "\n\t"))
               or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise ValueError("invalid asset text")
    return value


def _nullable_text(value):
    return None if value is None else _text(value)


def _sha256(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid PDF SHA-256")
    return value


def _page(value):
    if type(value) is not int or not 1 <= value <= MAX_PAGES:
        raise ValueError("invalid page number")
    return value


def _pages(value):
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= MAX_PAGES:
        raise ValueError("invalid selected pages")
    pages = [_page(page) for page in value]
    if len(set(pages)) != len(pages):
        raise ValueError("duplicate selected pages")
    return set(pages)


def _asset(value):
    """Whitelist display fields and require every source permission to be shut."""
    if not isinstance(value, dict):
        raise TypeError("invalid asset record")
    required = {
        "visual_asset_id", "label", "asset_type", "description", "volume_id",
        "chapter_id", "section_key", "supplement_node_key", "pdf_page",
        "printed_page", "source_sha256", "anchor_type", "bbox", "cropped",
        "asset_localization_status", "review_status", "candidate_only",
        *_CLOSED_FLAGS,
    }
    if not required.issubset(value):
        raise ValueError("incomplete asset record")
    if (
        value["candidate_only"] is not True
        or any(value[key] is not False for key in _CLOSED_FLAGS)
        or value["anchor_type"] != "whole_page" or value["bbox"] is not None
        or value["cropped"] is not False
        or value["asset_localization_status"] != "whole_page_only_pending_bbox_review"
        or value["review_status"] != "candidate-only"
    ):
        raise ValueError("unsupported asset review or localization state")
    result = {
        "visual_asset_id": _text(value["visual_asset_id"]),
        "label": _text(value["label"], 256),
        "asset_type": _text(value["asset_type"], 64),
        "description": _text(value["description"], MAX_TEXT, multiline=True),
        "volume_id": _text(value["volume_id"]),
        "chapter_id": _nullable_text(value["chapter_id"]),
        "section_key": _nullable_text(value["section_key"]),
        "supplement_node_key": _nullable_text(value["supplement_node_key"]),
        "pdf_page": _page(value["pdf_page"]),
        "printed_page": None if value["printed_page"] is None else _page(value["printed_page"]),
        "source_sha256": _sha256(value["source_sha256"]),
        "anchor_type": "whole_page", "bbox": None, "cropped": False,
        "asset_localization_status": "whole_page_only_pending_bbox_review",
        "review_status": "candidate-only", "candidate_only": True,
    }
    result.update({key: False for key in _CLOSED_FLAGS})
    return result


def _without_conflicts(values):
    """Withhold every repeated ID, even identical copies or invalid twins."""
    assets, seen, conflicts = [], set(), set()
    unavailable = False
    for value in values:
        asset_id = value.get("visual_asset_id") if isinstance(value, dict) else None
        if isinstance(asset_id, str):
            if asset_id in seen:
                conflicts.add(asset_id)
            seen.add(asset_id)
        try:
            assets.append(_asset(value))
        except (TypeError, ValueError):
            unavailable = True
    notices = [UNAVAILABLE] if unavailable else []
    if conflicts:
        notices.append(CONFLICT)
    return [asset for asset in assets if asset["visual_asset_id"] not in conflicts], notices


def _catalog_path(workspace):
    root = Path(workspace).absolute()
    path = root / CATALOG_RELATIVE_PATH
    # Check all components before resolving, including a linked workspace or a
    # Windows junction. Resolving first would erase evidence of those links.
    for component in (*reversed(path.parents), path):
        metadata = component.lstat()
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise ValueError("linked asset catalog path")
    if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        raise ValueError("asset catalog outside workspace")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("asset catalog is not a regular file")
    if metadata.st_size > MAX_FILE_BYTES:
        raise ValueError("oversized asset catalog")
    return path


def load_textbook_assets(workspace, *, volume_id, section_key, source_sha256, pdf_pages):
    """Return exact book/section/PDF/page matches with all use gates closed.

    ``section_key=None`` matches only an explicitly null section, never every
    section. Missing or malformed optional metadata returns a visible notice;
    the separately verified PDF remains the caller's source of page pixels.
    """
    result = {"assets": [], "notices": []}
    try:
        volume_id = _text(volume_id)
        section_key = _nullable_text(section_key)
        source_sha256 = _sha256(source_sha256)
        selected_pages = _pages(pdf_pages)
        path = _catalog_path(workspace)
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("oversized asset catalog")
        lines = data.splitlines()
        if len(lines) > MAX_ROWS or any(len(line) > MAX_LINE_BYTES for line in lines):
            raise ValueError("asset catalog line/row limit")
        values = [json.loads(line.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                             parse_constant=_invalid_constant, parse_float=_finite_float)
                  for line in lines if line.strip()]
        assets, result["notices"] = _without_conflicts(values)
        stale = False
        for asset in assets:
            if (asset["volume_id"] != volume_id or asset["section_key"] != section_key
                    or asset["pdf_page"] not in selected_pages):
                continue
            if asset["source_sha256"] != source_sha256:
                stale = True
                continue
            result["assets"].append(asset)
        if stale:
            result["notices"].append(STALE)
    except FileNotFoundError:
        result = {"assets": [], "notices": [MISSING]}
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        result = {"assets": [], "notices": [UNAVAILABLE]}
    return result


def textbook_assets_for_page(catalog, page, selected_pages):
    """Return fresh plain text for this page, suitable only for local display."""
    assets, notices = [], []
    try:
        selected = _pages(selected_pages)
        _page(page)
        if not isinstance(catalog, dict):
            raise TypeError("invalid display catalog")
        values = catalog.get("assets", [])
        raw_notices = catalog.get("notices", [])
        if (not isinstance(values, list) or len(values) > MAX_ROWS
                or not isinstance(raw_notices, list) or len(raw_notices) > len(_NOTICES)):
            raise ValueError("invalid display catalog")
        notices = [notice for notice in _NOTICES if notice in raw_notices]
        if any(not isinstance(notice, str) or notice not in _NOTICES for notice in raw_notices):
            notices.append(UNAVAILABLE)
        candidates, invalid = _without_conflicts(values)
        notices.extend(invalid)
        assets = [asset for asset in candidates
                  if page in selected and asset["pdf_page"] == page]
    except (TypeError, ValueError, RecursionError):
        notices.append(UNAVAILABLE)
    lines = [
        "教材素材（候选，待教师核对）",
        "整页锚点，尚未裁切。请在教材原页核对图形与候选说明。",
    ]
    for asset in assets:
        printed = asset["printed_page"] if asset["printed_page"] is not None else "待核对"
        lines.extend([
            "", asset["label"],
            f"PDF 文件页序：{asset['pdf_page']}；书上印刷页码：{printed}",
            "候选说明：" + asset["description"],
        ])
    if not assets:
        lines.extend(["", "当前页面暂无匹配的教材素材。"])
    if notices:
        lines.extend(["", *dict.fromkeys(notices)])
    return "\n".join(lines), len(assets)

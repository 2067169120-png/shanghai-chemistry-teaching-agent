"""Explicit section reading scope from the active, hash-verified directory."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from .curriculum_workbench import (
    CurriculumWorkbenchError,
    CurriculumWorkbenchReader,
    _strict_json_object,
)


class TextbookSectionError(ValueError):
    def __init__(self, message):
        self.code = "textbook_section_unavailable"
        self.message_zh = message
        super().__init__(message)


def _page_range(value):
    if (not isinstance(value, list) or len(value) != 2
            or any(type(page) is not int or page < 1 for page in value)
            or value[1] < value[0] or value[1] - value[0] >= 256):
        raise TextbookSectionError("本节目录页范围无效，未扩大阅读范围。")
    return list(range(value[0], value[1] + 1))


def _safe_source(workspace, value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or PurePosixPath(value).is_absolute()):
        raise TextbookSectionError("本节教材位置无法安全核对，请检查本地教材目录。")
    cursor = workspace
    for part in PurePosixPath(value).parts:
        cursor /= part
        if cursor.is_symlink():
            raise TextbookSectionError("本节教材位置包含链接，未打开其他文件。")
    if not cursor.resolve().is_relative_to(workspace) or cursor.suffix.casefold() != ".pdf":
        raise TextbookSectionError("本节教材位置越出工作区或不是PDF，未打开其他文件。")


def textbook_section_scope(workspace, concept):
    """Re-read the active directory every time; never derive pages from notes.

    This returns directory metadata only. The source service must separately
    verify the actual PDF bytes before exposing a readable section.
    """
    workspace = Path(workspace).resolve()
    try:
        database = workspace / "sh-chem-db"
        if database.is_symlink() or not database.resolve().is_relative_to(workspace):
            raise TextbookSectionError("教材目录位置无法安全核对。")
        reader = CurriculumWorkbenchReader(database)
        # Reuse the current registry's location, size and activated SHA checks.
        # No mapping snapshot or persistent directory cache is used here.
        raw = reader._directory_bytes()
        registry = _strict_json_object(raw)
        # Keep the existing registry protocol validation (identity, parent
        # links, page pairs and review boundary), without loading mappings.
        # Its second read must pass the same activated content hash.
        reader._load_directory()
        section_key, volume_id = concept.get("section_key"), concept.get("volume_id")
        if not isinstance(section_key, str) or not isinstance(volume_id, str):
            raise TextbookSectionError("所选知识点尚无明确的册与节，不能推测整节范围。")
        nodes = [row for row in registry["nodes"] if isinstance(row, dict) and row.get("node_key") == section_key]
        volumes = [row for row in registry["volumes"] if isinstance(row, dict) and row.get("volume_id") == volume_id]
        if len(nodes) != 1 or len(volumes) != 1:
            raise TextbookSectionError("所选知识点没有唯一对应的教材节，请先核对目录。")
        node, volume = nodes[0], volumes[0]
        if (node.get("volume_id") != volume_id or node.get("source_root") != "workspace_root"
                or volume.get("source_root") != "workspace_root"
                or node.get("status") != "toc_visual_verified_directory_node"
                or any(node.get(key) != volume.get(key) for key in ("source_path", "source_sha256", "volume_title"))
                or any(node.get(key) != concept.get(key) for key in ("source_path", "source_sha256", "chapter_id"))):
            raise TextbookSectionError("所选知识点与本节教材版本或所属章节不一致，未扩大阅读范围。")
        _safe_source(workspace, node.get("source_path"))
        pages, printed = _page_range(node.get("content_pdf_pages")), _page_range(node.get("printed_pages"))
        chosen = concept.get("pdf_pages")
        if (not isinstance(chosen, list) or not chosen
                or any(type(page) is not int or page < 1 for page in chosen)
                or not set(chosen).issubset(pages)):
            raise TextbookSectionError("知识点页范围与本节目录不一致，未扩大阅读范围。")
        if len(pages) != len(printed):
            raise TextbookSectionError("本节文件页与印刷页无法逐页对应，请先核对目录。")
        for key in ("volume_title", "section_number", "section_title"):
            if not isinstance(node.get(key), str) or not node[key].strip() or len(node[key]) > 200:
                raise TextbookSectionError("本节标题或编号缺失，请先核对目录。")
        return {"reading_mode": "section", "volume_id": volume_id, "section_key": section_key,
                "volume_title": node["volume_title"], "section_number": node["section_number"],
                "section_title": node["section_title"], "pdf_pages": pages, "printed_pages": printed,
                "concept_pdf_pages": list(chosen), "registry_sha256": hashlib.sha256(raw).hexdigest()}
    except CurriculumWorkbenchError as exc:
        raise TextbookSectionError("本节教材目录未能核对，请重新读取当前目录；知识点原页仍可单独打开。") from exc
    except (OSError, TypeError, KeyError) as exc:
        raise TextbookSectionError("本节教材目录暂不可读；知识点原页仍可单独打开。") from exc

"""Explicit, read-only access to registered candidate textbook anchor pages.

Catalog selection is independent of concept, excerpt and model-material scope.
Only an activated directory can supply a PDF location; asset image paths are
never used. Neither listing nor opening changes any review or permission gate.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
from pathlib import Path, PurePosixPath

from . import curriculum_workbench as curriculum
from . import desktop_textbook_assets as metadata

MAX_DIRECTORY_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 256 * 1024 * 1024
MAX_QUERY = 256


class TextbookAssetCatalogError(ValueError):
    def __init__(self, message):
        self.code = "textbook_asset_catalog_unavailable"
        self.message_zh = message
        super().__init__(message)


def _checked_file(workspace, relative, limit, label):
    """Check lexical components before resolution, including Windows junctions."""
    path = workspace / relative
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise TextbookAssetCatalogError(f"{label}位置包含链接，请使用工作区内的原文件。")
    if not path.resolve(strict=True).is_relative_to(workspace.resolve(strict=True)):
        raise TextbookAssetCatalogError(f"{label}位置越出工作区，请核对本地目录。")
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise TextbookAssetCatalogError(f"{label}不是有效文件或超过大小限制，请核对本地文件。")
    return path, info


def _source_relative(value):
    if (
        not isinstance(value, str) or not value or len(value) > 1024
        or "\\" in value or ":" in value or any(ord(c) < 32 for c in value)
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} or part.endswith((".", " "))
               for part in value.split("/"))
        or PurePosixPath(value).suffix.casefold() != ".pdf"
    ):
        raise TextbookAssetCatalogError("激活目录中的教材位置无效，请核对工作区内的 PDF 路径。")
    return Path(*PurePosixPath(value).parts)


def _read_catalog(workspace):
    try:
        path = metadata._catalog_path(workspace)
        with path.open("rb") as stream:
            raw = stream.read(metadata.MAX_FILE_BYTES + 1)
        if len(raw) > metadata.MAX_FILE_BYTES:
            raise ValueError("catalog size limit")
        lines = raw.splitlines()
        if len(lines) > metadata.MAX_ROWS or any(len(line) > metadata.MAX_LINE_BYTES for line in lines):
            raise ValueError("catalog line/count limit")
        values = [json.loads(line.decode("utf-8-sig"), object_pairs_hook=metadata._unique_object,
                             parse_constant=metadata._invalid_constant, parse_float=metadata._finite_float)
                  for line in lines if line.strip()]
        accepted, notices = metadata._without_conflicts(values)
        # Conflicting/invalid twins have already been removed before this map
        # can be used. Raw rows bind revisions without exposing unknown fields.
        raw_by_id = {value["visual_asset_id"]: value for value in values
                     if isinstance(value, dict) and isinstance(value.get("visual_asset_id"), str)}
        return accepted, raw_by_id, notices
    except FileNotFoundError as exc:
        raise TextbookAssetCatalogError(metadata.MISSING) from exc
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise TextbookAssetCatalogError("教材素材目录无法安全读取，请核对固定目录文件及记录格式。") from exc


def _directory(workspace):
    try:
        relative = Path("sh-chem-db") / curriculum.DIRECTORY_RELATIVE
        _checked_file(workspace, relative, MAX_DIRECTORY_BYTES, "教材目录")
        reader = curriculum.CurriculumWorkbenchReader(workspace / "sh-chem-db")
        # Real 5/19/60 protocol, parent, status and active-hash checks. Do not
        # load mapping snapshots or use the reader's cached public projection.
        _, _, _, chapter_to_volume = reader._load_directory()
        raw = reader._directory_bytes()
        registry = curriculum._strict_json_object(raw)
        return registry, hashlib.sha256(raw).hexdigest(), chapter_to_volume
    except TextbookAssetCatalogError:
        raise
    except (curriculum.CurriculumWorkbenchError, OSError, ValueError, TypeError, RecursionError) as exc:
        raise TextbookAssetCatalogError("当前教材目录未通过激活版本核对，请恢复或重新核对当前目录后刷新。") from exc


def _bind(asset, registry, chapter_to_volume):
    volumes = [row for row in registry["volumes"] if row["volume_id"] == asset["volume_id"]]
    if len(volumes) != 1 or volumes[0]["source_sha256"] != asset["source_sha256"]:
        raise TextbookAssetCatalogError("有素材的教材册或 PDF 摘要与激活目录不一致，请核对来源登记。")
    volume = volumes[0]
    relative = _source_relative(volume["source_path"])
    chapter_id, section_key = asset["chapter_id"], asset["section_key"]
    if chapter_id is not None and chapter_to_volume.get(chapter_id) != asset["volume_id"]:
        raise TextbookAssetCatalogError("有素材的所属章与教材册不一致，请核对分类登记。")
    chapter_nodes = [node for node in registry["nodes"] if node["chapter_id"] == chapter_id]
    node = None
    if section_key is not None:
        nodes = [row for row in registry["nodes"] if row["node_key"] == section_key]
        if (len(nodes) != 1 or asset["supplement_node_key"] is not None
                or any(nodes[0][key] != asset[key] for key in ("volume_id", "chapter_id", "source_sha256"))):
            raise TextbookAssetCatalogError("有素材的正式节及父章登记不一致，请核对分类登记。")
        node = nodes[0]
        first, last = node["content_pdf_pages"]
        printed_first, printed_last = node["printed_pages"]
        if (not first <= asset["pdf_page"] <= last or last - first != printed_last - printed_first
                or (asset["printed_page"] is not None
                    and asset["printed_page"] != printed_first + asset["pdf_page"] - first)):
            raise TextbookAssetCatalogError("有素材的原页锚点或已登记印刷页与所属节范围不一致，请核对页码。")
    elif (asset["supplement_node_key"] is None
          or any(row["node_key"] == asset["supplement_node_key"] for row in registry["nodes"])):
        raise TextbookAssetCatalogError("有素材缺少明确的补充栏目登记，或将正式节登记为补充栏目，请核对分类。")
    # Supplement keys remain literal declarations. Never parse IDs to invent
    # a parent or force an opener/review/appendix into a numbered section.
    display = {
        **asset,
        "volume_title": metadata._text(volume["volume_title"], 200),
        "chapter_title": metadata._text(chapter_nodes[0]["chapter_title"], 200) if chapter_nodes else None,
        "section_title": metadata._text(node["section_title"], 200) if node else None,
        "source_name": metadata._text(relative.name, 255),
    }
    return volume, relative, display


def _pdf_page_count(data):
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise TextbookAssetCatalogError("教材 PDF 受密码保护，无法核对原页；请使用可直接读取的原文件。")
        declared = reader.trailer["/Root"]["/Pages"]["/Count"]
        if isinstance(declared, bool) or not isinstance(declared, int) or not 1 <= declared <= metadata.MAX_PAGES:
            raise ValueError("invalid PDF page count")
        count = len(reader.pages)
        if count != declared or not 1 <= count <= metadata.MAX_PAGES:
            raise ValueError("PDF page tree/count mismatch")
        return count
    except TextbookAssetCatalogError:
        raise
    except ImportError as exc:
        raise TextbookAssetCatalogError("本地 PDF 页数核验组件不可用，请修复应用依赖后重试。") from exc
    except Exception as exc:
        # Parser failures are optional-source errors, never a reason to return
        # unverified bytes or to guess that an anchor exists in the document.
        raise TextbookAssetCatalogError("教材 PDF 页结构无法可靠读取，请核对原文件后重试。") from exc


def _read_pdf(workspace, relative, source_sha256, anchor):
    try:
        path, before = _checked_file(workspace, relative, MAX_PDF_BYTES, "教材 PDF")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                raise ValueError("source replaced while opening")
            data = stream.read(MAX_PDF_BYTES + 1)
        _, after = _checked_file(workspace, relative, MAX_PDF_BYTES, "教材 PDF")
        if len(data) > MAX_PDF_BYTES or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise TextbookAssetCatalogError("教材 PDF 读取期间变化或超过大小限制，请核对文件后重试。")
        if not data.startswith(b"%PDF-"):
            raise TextbookAssetCatalogError("教材原文件不是受支持的 PDF，请核对激活目录中的原书。")
        if hashlib.sha256(data).hexdigest() != source_sha256:
            raise TextbookAssetCatalogError("教材原文件内容与登记摘要不一致，请核对原书后刷新。")
        page_count = _pdf_page_count(data)
        if anchor > page_count:
            raise TextbookAssetCatalogError("素材原页锚点超出教材实际页数，请核对素材页码登记。")
        return data
    except TextbookAssetCatalogError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise TextbookAssetCatalogError("教材原文件缺失、不可读或位置已变化，请核对工作区内的原书。") from exc


class TextbookAssetCatalogService:
    def __init__(self, workspace):
        # Do not resolve away a symlink/junction before checking its components.
        self.workspace = Path(workspace).absolute()

    def _entries(self):
        accepted, raw_by_id, notices = _read_catalog(self.workspace)
        if not accepted:
            return {}, notices
        registry, registry_sha256, parents = _directory(self.workspace)
        entries = {}
        for asset in accepted:
            try:
                volume, relative, display = _bind(asset, registry, parents)
                revision = hashlib.sha256(json.dumps({
                    "asset": raw_by_id[asset["visual_asset_id"]],
                    "directory_sha256": registry_sha256,
                }, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()
                entries[asset["visual_asset_id"]] = {
                    "asset": asset, "volume": volume, "relative": relative,
                    "display": {**display, "revision": revision}, "revision": revision,
                }
            except TextbookAssetCatalogError as exc:
                notices.append(exc.message_zh)
            except (ValueError, TypeError, KeyError):
                notices.append("有素材的来源或分类信息无法核对，请修正目录登记后刷新。")
        return entries, list(dict.fromkeys(notices))

    def options(self, query=""):
        """Metadata-only listing; PDF bytes are verified on explicit selection."""
        try:
            if not isinstance(query, str) or len(query) > MAX_QUERY:
                raise TextbookAssetCatalogError("素材检索词无效或过长，请缩短检索词后重试。")
            entries, notices = self._entries()
            words = query.casefold().split()
            rows = []
            for entry in entries.values():
                display = entry["display"]
                searchable = " ".join(str(display[key] or "") for key in (
                    "visual_asset_id", "label", "description", "asset_type", "volume_id",
                    "volume_title", "chapter_id", "chapter_title", "section_key", "section_title",
                    "supplement_node_key", "pdf_page", "printed_page",
                )).casefold()
                if all(word in searchable for word in words):
                    rows.append(dict(display))
            return {"assets": rows, "notices": notices}
        except TextbookAssetCatalogError as exc:
            return {"assets": [], "notices": [exc.message_zh]}

    def source(self, visual_asset_id, revision):
        """Re-read the selected identity and return only its exact anchor scope."""
        try:
            metadata._text(visual_asset_id)
            metadata._sha256(revision)
        except (TypeError, ValueError) as exc:
            raise TextbookAssetCatalogError("素材选择记录无效，请刷新目录后重选。") from exc
        entries, notices = self._entries()
        entry = entries.get(visual_asset_id)
        if entry is None or entry["revision"] != revision:
            detail = notices[0] if entry is None and notices else ""
            raise TextbookAssetCatalogError("素材记录已变化、缺失或未通过核对，请刷新目录后重选。" + detail)
        asset, volume = entry["asset"], entry["volume"]
        data = _read_pdf(self.workspace, entry["relative"], volume["source_sha256"], asset["pdf_page"])
        # An optional catalog may change while a large PDF is being read. Do
        # not release a now-stale selection after that potentially long step.
        latest, _ = self._entries()
        if visual_asset_id not in latest or latest[visual_asset_id]["revision"] != revision:
            raise TextbookAssetCatalogError("素材或教材目录在读取期间已变化，请刷新目录后重选。")
        return {
            **entry["display"],
            "title": asset["label"], "statement": asset["description"],
            "source_title": volume["volume_title"], "source_sha256": volume["source_sha256"],
            "pdf_bytes": data, "pdf_pages": [asset["pdf_page"]], "printed_page": asset["printed_page"],
            "visual_assets": {"assets": [dict(asset)], "notices": []},
            "reading_hints": {"notes": [], "notices": []}, "reading_mode": "asset",
        }

"""Local chapter candidates from exact registered books, bookmarks or headings.

PDF file pages are used directly. Printed page offsets and chemistry content
are never inferred. Detection neither writes personal state nor executes PDF
actions, attachments or external links.
"""
from __future__ import annotations

import hashlib
import io
import re

from .desktop_preparation_sources import PreparationSourceError
from .desktop_textbook_workspace import _digest
from .reader_cancellation import ReadCancelled, check_read_cancelled

DETECTOR_REVISION = "local-book-directory-bookmarks-headings-v1"
EVIDENCE_SCHEMA = "shchem.personal-outline-evidence.v1"
MAX_OUTLINE_ROWS = 500
MAX_TEXT_PAGES = 600
MAX_PAGE_STREAM = 8 * 1024 * 1024
METHOD_LABELS = {"registered_directory": "同版本已登记目录", "pdf_bookmark": "PDF书签",
                 "pdf_text_heading": "正文标题文字（待核对）"}
_HEADING = re.compile(r"^(?:第\s*[零〇一二三四五六七八九十百0-9]+\s*[章节单元课篇]|"
                      r"[0-9]{1,2}[.．][0-9]{1,2}(?:[.．][0-9]{1,2})?)\s*[^0-9\s].{1,90}$")
EVIDENCE_FIELDS = {"schema", "detector_revision", "source_sha256", "candidate_id", "method",
                   "anchor", "metadata_sha256", "group_path", "pdf_start", "pdf_end", "end_basis"}


def _candidate(sha, title, start, end, method, *, anchor, metadata_sha256=None,
               group_path=(), end_basis="registered_range"):
    if (not isinstance(title, str) or not title.strip() or len(title) > 160
            or type(start) is not int or type(end) is not int or not 1 <= start <= end):
        raise ValueError("invalid chapter candidate")
    identity = {"source_sha256": sha, "method": method, "anchor": anchor, "title": title,
                "pdf_start": start, "pdf_end": end, "group_path": list(group_path)}
    candidate_id = "OUT-" + _digest(identity)[:32]
    return {"candidate_id": candidate_id, "title": title, "pdf_start": start, "pdf_end": end,
            "evidence": {"schema": EVIDENCE_SCHEMA, "detector_revision": DETECTOR_REVISION,
                         "source_sha256": sha, "candidate_id": candidate_id, "method": method,
                         "anchor": anchor, "metadata_sha256": metadata_sha256,
                         "group_path": list(group_path), "pdf_start": start, "pdf_end": end,
                         "end_basis": end_basis}}


def validate_evidence(value, source_sha256, page_count):
    """Validate durable origin data without promoting its review status."""
    if (not isinstance(value, dict) or set(value) != EVIDENCE_FIELDS
            or value["schema"] != EVIDENCE_SCHEMA or value["detector_revision"] != DETECTOR_REVISION
            or value["source_sha256"] != source_sha256
            or not re.fullmatch(r"OUT-[0-9a-f]{32}", str(value["candidate_id"]))
            or value["method"] not in METHOD_LABELS
            or not isinstance(value["anchor"], str) or not 0 < len(value["anchor"]) <= 500
            or value["metadata_sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(value["metadata_sha256"]))
            or not isinstance(value["group_path"], list) or len(value["group_path"]) > 12
            or any(not isinstance(title, str) or not 0 < len(title) <= 160 for title in value["group_path"])
            or type(value["pdf_start"]) is not int or type(value["pdf_end"]) is not int
            or not 1 <= value["pdf_start"] <= value["pdf_end"] <= page_count
            or value["end_basis"] not in {"registered_range", "next_heading", "book_end", "scan_limit", "same_page"}):
        raise PreparationSourceError("自动目录的来源依据或页范围不完整，请保留记录并核对备份。")


def _registered(root, sha, page_count):
    from .desktop_textbook_asset_catalog import _directory
    from .desktop_textbook_bookshelf import _section

    registry, revision, parents = _directory(root)
    volumes = [row for row in registry["volumes"] if row["source_sha256"] == sha]
    if len(volumes) != 1:
        return []
    volume_id = volumes[0]["volume_id"]
    rows = []
    for node in registry["nodes"]:
        if node["volume_id"] != volume_id or node["source_sha256"] != sha:
            continue
        check_read_cancelled()
        section = _section(node, revision)
        if parents.get(node["chapter_id"]) != volume_id or max(section["pdf_pages"]) > page_count:
            raise ValueError("registered chapter binding mismatch")
        rows.append(_candidate(sha, section["title"], min(section["pdf_pages"]), max(section["pdf_pages"]),
                    "registered_directory", anchor=node["node_key"], metadata_sha256=revision,
                    group_path=[section["chapter_title"]]))
    return rows


def _bookmarks(reader, sha, page_count):
    from pypdf.generic import Destination

    flat, notices = [], []

    def walk(items, parents=()):
        if len(parents) > 12:
            raise ValueError("outline too deep")
        previous = None
        for index, item in enumerate(items):
            check_read_cancelled()
            if isinstance(item, list):
                if previous is not None:
                    previous["children"] = True
                    walk(item, (*parents, previous["title"]))
                continue
            previous = None
            if not isinstance(item, Destination):
                notices.append("有书签不是本书页位置，未作为目录采用。")
                continue
            page = reader.get_destination_page_number(item)
            title = str(item.title).strip()
            if type(page) is not int or not 0 <= page < page_count or not 0 < len(title) <= 160:
                notices.append("有书签的页位置或标题无法读取，未作为目录采用。")
                continue
            previous = {"title": title, "start": page + 1, "group": parents, "children": False,
                        "anchor": str(len(flat)) + ":" + str(index)}
            flat.append(previous)
            if len(flat) > MAX_OUTLINE_ROWS:
                raise ValueError("too many bookmarks")

    walk(reader.outline)
    leaves = [row for row in flat if not row["children"]]
    rows = []
    for index, row in enumerate(leaves):
        next_page = leaves[index + 1]["start"] if index + 1 < len(leaves) else None
        # Same-page anchors do not prove which of two headings owns that page.
        basis = "book_end" if next_page is None else "next_heading"
        end = page_count if next_page is None else next_page - 1
        if end < row["start"]:
            end, basis = row["start"], "same_page"
            notices.append("有书签页序重复或倒置，相关候选暂只定位起始页，请核对范围。")
        rows.append(_candidate(sha, row["title"], row["start"], end, "pdf_bookmark",
                    anchor=row["anchor"], group_path=row["group"], end_basis=basis))
    return rows, notices


def _headings(reader, sha, page_count):
    headings, seen, notices = [], set(), []
    limit = min(page_count, MAX_TEXT_PAGES)
    scanned_pages, row_limit = 0, False
    for number in range(1, limit + 1):
        check_read_cancelled()
        scanned_pages = number
        page = reader.pages[number - 1]
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > MAX_PAGE_STREAM:
            notices.append("有正文页的文字流超过8MB，已跳过目录文字识别；原页仍可阅读。")
            continue
        text = page.extract_text() or ""
        if len(text) > 100000:
            notices.append("有正文页的文字量过大，已跳过目录文字识别。")
            continue
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
        # A contents keyword in body prose is insufficient. Require a short
        # stand-alone heading or several dot-leader entries on the same page.
        if (any(re.fullmatch(r"(?:目\s*录|CONTENTS|Contents)", line) for line in lines[:8])
                or sum(bool(re.search(r"[.．·…]{2,}\s*[0-9]+$", line)) for line in lines) >= 3):
            continue
        matches = [line for line in lines[:12] if _HEADING.fullmatch(line)
                   and not re.search(r"[.．·…]{2,}\s*[0-9]+$", line)]
        for title in matches:
            key = re.sub(r"\s", "", title)
            if key in seen:
                continue
            seen.add(key)
            headings.append((number, title))
            if len(headings) >= MAX_OUTLINE_ROWS:
                row_limit = True
                notices.append("正文标题已达500条，后续标题未纳入本次目录候选。")
                break
        if len(headings) >= MAX_OUTLINE_ROWS:
            break
    rows = []
    for index, (start, title) in enumerate(headings):
        next_page = headings[index + 1][0] if index + 1 < len(headings) else None
        end, basis = (next_page - 1, "next_heading") if next_page else (
            scanned_pages, "book_end" if scanned_pages == page_count and not row_limit else "scan_limit")
        if end < start:
            end, basis = start, "same_page"
        rows.append(_candidate(sha, title, start, end, "pdf_text_heading",
                    anchor=f"PDF {start}: {title}", end_basis=basis))
    if page_count > limit:
        notices.append(f"正文标题仅识别前{limit}个PDF页，后续仍可整书阅读。")
    if rows:
        notices.append("正文标题来自文字层；重复页眉、公式或目录续页可能影响识别，请对照原页核对。")
    return rows, notices


def detect_outline(source, workspace_root):
    sha, count = source["source_sha256"], source["page_count"]
    if hashlib.sha256(source["pdf_bytes"]).hexdigest() != sha:
        raise PreparationSourceError("目录识别的原书内容与保存版本不一致，请刷新后重试。")
    notices = []
    try:
        rows = _registered(workspace_root, sha, count)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ReadCancelled):
            raise
        rows = []
        notices.append("已有登记目录暂不可用，已转向PDF自身书签和正文标题。")
    if not rows:
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(source["pdf_bytes"]))
            rows, bookmark_notices = _bookmarks(reader, sha, count)
            notices.extend(bookmark_notices)
            if not rows:
                rows, text_notices = _headings(reader, sha, count)
                notices.extend(text_notices)
        except ReadCancelled:
            raise
        except Exception as exc:
            raise PreparationSourceError("PDF目录或正文标题暂无法可靠读取；原书和已有章节保留，可继续手工整理。") from exc
    if not rows:
        notices.append("未找到可定位的目录。扫描图像、无标题文字或不支持的布局可继续整书阅读并手工添加章节。")
    return {"rows": rows, "notices": list(dict.fromkeys(notices)), "detector_revision": DETECTOR_REVISION}

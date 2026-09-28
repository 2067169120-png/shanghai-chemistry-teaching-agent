"""Bounded, read-only display of textbook reading notes; never model material."""

from __future__ import annotations

import json
import re
from itertools import islice
from pathlib import Path

MAX_FILES = 64
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_NOTES = 512
MAX_TEXT = 4000
SCHEMAS = {
    "shchem.textbook-reading-notes.v1",
    "shchem.textbook-page-reading-notes.v1",
}
UNAVAILABLE = "部分研读提示暂时无法读取或核对；教材原页仍可查看。"
STALE = "有研读提示对应其他教材版本，暂未显示；教材原页仍可查看。"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("non-finite JSON constant")


def _pages(value):
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 64
        or any(type(page) is not int or page < 1 for page in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("invalid page list")
    return list(value)


def _note(value):
    if not isinstance(value, dict):
        raise ValueError("invalid reading note")
    result = {}
    for key in ("id", "title", "summary", "limitation", "classroom_check", "volume_id", "section_key"):
        text = value.get(key)
        limit = 180 if key in {"id", "title", "volume_id", "section_key"} else MAX_TEXT
        if not isinstance(text, str) or not text.strip() or len(text) > limit or "\x00" in text:
            raise ValueError("invalid note text")
        result[key] = text.strip()
    if value.get("candidate_only") is not True or value.get("human_reviewed") is not False:
        raise ValueError("invalid review flags")
    result["pdf_pages"] = _pages(value.get("pdf_pages"))
    result["printed_pages"] = _pages(value.get("printed_pages"))
    if len(result["pdf_pages"]) != len(result["printed_pages"]):
        raise ValueError("ambiguous printed page mapping")
    evidence = value.get("textbook_evidence")
    if (
        not isinstance(evidence, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("source_sha256", "")))
        or _pages(evidence.get("pdf_pages")) != result["pdf_pages"]
        or _pages(evidence.get("printed_pages")) != result["printed_pages"]
    ):
        raise ValueError("invalid textbook evidence")
    # The top-level source_sha256 may identify a lecture, not the textbook.
    result["textbook_sha256"] = evidence["source_sha256"]
    return result


def load_reading_hints(workspace, *, volume_id, section_key, source_sha256, pdf_pages):
    """Return only exact-version notes fully covered by the chosen page range.

    Only fixed local note files are opened. No path or instruction from their
    content is used. Optional evidence failures never block the verified PDF.
    """
    result = {"notes": [], "notices": []}
    unavailable = stale = False
    try:
        selected_pages = set(_pages(pdf_pages))
        root = Path(workspace).resolve()
        folder = root / "knowledge" / "textbook"
        if not folder.exists():
            return result
        if not folder.resolve().is_relative_to(root):
            raise ValueError("note directory outside workspace")
        paths = list(islice(folder.glob("reading-notes-*.json"), MAX_FILES + 1))
        if len(paths) > MAX_FILES:
            raise ValueError("too many note files")
        total_bytes = total_notes = 0
        candidates = []
        for path in sorted(paths, key=lambda p: p.name.casefold()):
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                    raise ValueError("note file outside directory")
                size = path.stat().st_size
                if size > MAX_FILE_BYTES:
                    raise ValueError("oversized reading note file")
                if total_bytes + size > MAX_TOTAL_BYTES:
                    unavailable = True
                    break
                with path.open("rb") as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError("oversized reading note file")
                total_bytes += len(data)
                if total_bytes > MAX_TOTAL_BYTES:
                    unavailable = True
                    break
                packet = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                                    parse_constant=_invalid_constant)
                if (
                    not isinstance(packet, dict)
                    or not isinstance(packet.get("notes"), list)
                ):
                    raise ValueError("invalid reading note packet")
                total_notes += len(packet["notes"])
                if total_notes > MAX_NOTES:
                    unavailable = True
                    break
                related = [value for value in packet["notes"] if not isinstance(value, dict)
                           or (value.get("volume_id") == volume_id and value.get("section_key") == section_key)]
                if not related:
                    continue
                if (packet.get("schema_version") not in SCHEMAS
                        or packet.get("candidate_only") is not True
                        or packet.get("human_reviewed") is not False):
                    raise ValueError("unsupported reading note packet")
                for value in related:
                    try:
                        note = _note(value)
                    except ValueError:
                        unavailable = True
                        continue
                    if note["textbook_sha256"] != source_sha256:
                        stale = True
                        continue
                    if set(note["pdf_pages"]).issubset(selected_pages):
                        candidates.append(note)
            except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
                unavailable = True
        # Identical duplicated notes display once. Conflicting reused IDs are
        # all withheld rather than choosing a file by incidental sort order.
        by_id, conflicts = {}, set()
        for note in candidates:
            prior = by_id.get(note["id"])
            if prior is not None and prior != note:
                conflicts.add(note["id"])
                unavailable = True
            by_id[note["id"]] = note
        seen = set()
        for key, note in by_id.items():
            fingerprint = json.dumps({k: v for k, v in note.items() if k != "id"}, sort_keys=True)
            if key not in conflicts and fingerprint not in seen:
                result["notes"].append(note)
                seen.add(fingerprint)
    except (OSError, ValueError, TypeError, RecursionError):
        unavailable = True
    if unavailable:
        result["notices"].append(UNAVAILABLE)
    if stale:
        result["notices"].append(STALE)
    return result


def reading_hints_for_page(hints, page, selected_pages):
    """Plain display text only. Moving away cannot retain unrelated hints."""
    notes = [note for note in hints.get("notes", [])
             if page in selected_pages and page in note["pdf_pages"]]
    lines = ["研读提示（AI整理的候选内容，待教师核对）"]
    if notes:
        for position, note in enumerate(notes):
            if position:
                lines.append("")
            lines.extend([
                note["title"],
                "PDF文件页序：" + "、".join(map(str, note["pdf_pages"]))
                + "；书上印刷页码：" + "、".join(map(str, note["printed_pages"])),
                "适用范围与待核对处：" + note["limitation"],
                "研读摘要：" + note["summary"],
                "课堂核对建议：" + note["classroom_check"],
            ])
    else:
        lines.extend(["", "当前页面暂无匹配的研读提示。"])
    if hints.get("notices"):
        lines.extend(["", *hints["notices"]])
    return "\n".join(lines), len(notes)

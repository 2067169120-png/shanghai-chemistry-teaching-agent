"""Profile-local navigation, bound to freshly verified PDF bytes and scope.

These records locate pages. They do not certify sources, select materials or
change any teacher review state. Readers must bind a newly loaded source before
applying a saved page.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

DRAFT_KEY = "textbook-reading-positions-v1"
SCHEMA = "shchem.textbook-reading-positions.v1"
MAX_POSITIONS = 64


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 200 and not any(ord(c) < 32 for c in value)


def _valid_binding(binding):
    if not isinstance(binding, dict) or set(binding) != {"selector", "source_sha256", "pdf_pages"}:
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", str(binding["source_sha256"])):
        return False
    pages = binding["pdf_pages"]
    if (not isinstance(pages, list) or not 1 <= len(pages) <= 3000
            or any(type(page) is not int or not 1 <= page <= 3000 for page in pages)
            or pages != sorted(set(pages))):
        return False
    selector = binding["selector"]
    if not isinstance(selector, dict):
        return False
    mode = selector.get("mode")
    if mode in {"section", "supplement"}:
        key = "section_key" if mode == "section" else "supplement_node_key"
        return (set(selector) == {"mode", key, "revision", "volume_id"}
                and all(_text(selector[field]) for field in (key, "revision", "volume_id")))
    if mode == "book":
        if set(selector) != {"mode", "source_id", "revision", "section_id", "study_revision"}:
            return False
        return (all(_text(selector[field]) for field in ("source_id", "revision"))
                and selector["revision"] == binding["source_sha256"]
                and (selector["section_id"] is None and selector["study_revision"] is None
                    or _text(selector["section_id"]) and _text(selector["study_revision"])))
    return False


def bind_source(source):
    """Create a navigation binding only from the service's current PDF bytes."""
    mode = source.get("reading_mode")
    selector = {"mode": mode, "revision": source.get("revision")}
    if mode in {"section", "supplement"} and source.get("entry_origin") == "textbook_bookshelf":
        key = "section_key" if mode == "section" else "supplement_node_key"
        selector.update({key: source.get(key), "volume_id": source.get("volume_id")})
    elif mode == "book" and source.get("entry_origin") == "personal_textbook":
        section_id = source.get("section_id")
        selector.update(source_id=source.get("source_id"), section_id=section_id,
                        study_revision=source.get("study_revision") if section_id is not None else None)
    else:
        raise ValueError("该阅读窗口不保存连续阅读位置。")
    binding = {"selector": selector, "source_sha256": source.get("source_sha256"),
               "pdf_pages": deepcopy(source.get("pdf_pages"))}
    raw = source.get("pdf_bytes")
    if (not _valid_binding(binding) or not isinstance(raw, bytes)
            or hashlib.sha256(raw).hexdigest() != binding["source_sha256"]):
        raise ValueError("阅读位置的原文件与范围无法核对。")
    return binding


def validated_record(value):
    """Discard malformed navigation metadata without modifying the profile."""
    if not isinstance(value, dict) or set(value) != {"schema", "entries"} or value.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "entries": []}
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_POSITIONS:
        return {"schema": SCHEMA, "entries": []}
    valid = []
    seen = set()
    for row in reversed(entries):
        if (not isinstance(row, dict) or set(row) != {"binding", "pdf_page", "label"}
                or not _valid_binding(row["binding"]) or type(row["pdf_page"]) is not int
                or row["pdf_page"] not in row["binding"]["pdf_pages"]
                or not isinstance(row["label"], str) or not 0 < len(row["label"]) <= 240
                or any(ord(c) < 32 for c in row["label"])):
            continue
        key = _digest(row["binding"])
        if key not in seen:
            valid.append(deepcopy(row));seen.add(key)
    return {"schema": SCHEMA, "entries": list(reversed(valid))}


class TextbookReadingPositions:
    def __init__(self, state):
        self.state = state

    def snapshot(self):
        return validated_record(self.state.snapshot().get("drafts", {}).get(DRAFT_KEY))

    def latest(self):
        entries = self.snapshot()["entries"]
        return entries[-1] if entries else None

    def lookup(self, binding):
        if not _valid_binding(binding):
            return None
        return next((row for row in reversed(self.snapshot()["entries"]) if row["binding"] == binding), None)

    def remember(self, binding, page, label):
        if (not _valid_binding(binding) or type(page) is not int or page not in binding["pdf_pages"]
                or not isinstance(label, str) or not label.strip()):
            raise ValueError("阅读位置不在已核对的原书范围内。")
        label = " ".join(label.split())[:240]
        row = {"binding": deepcopy(binding), "pdf_page": page, "label": label}
        def operation(value):
            current = validated_record(value["drafts"].get(DRAFT_KEY))
            entries = [old for old in current["entries"] if old["binding"] != binding]
            entries.append(row)
            updated = {"schema": SCHEMA, "entries": entries[-MAX_POSITIONS:]}
            if value["drafts"].get(DRAFT_KEY) == updated:
                return False
            value["drafts"][DRAFT_KEY] = updated
        self.state._update(operation)
        return deepcopy(row)

    def personal_backup_record(self, books):
        versions = {(book["source_id"], book["source_sha256"]) for book in books}
        entries = [row for row in self.snapshot()["entries"]
                   if row["binding"]["selector"]["mode"] == "book"
                   and (row["binding"]["selector"]["source_id"], row["binding"]["source_sha256"]) in versions]
        return {"schema": SCHEMA, "entries": entries}

"""Build metadata-only review tasks from an existing local Word import.

Sources, cached previews, saved ranges and attributes are read without repair.
This module does not configure a provider, infer labels, or promote candidates.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
from collections import Counter
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from . import desktop_word_question_index as question_index
from .desktop_source_quality import (
    QUALITY_PATH,
    apply_source_quality,
    source_quality_notes,
)
from .desktop_word_preview_cache import (
    CACHE_REVISION,
    MAX_PREVIEW_BYTES,
    WordPreviewCache,
)
from .desktop_word_question_attributes import (
    automatic_tags_protected,
    validate_attributes,
)

SCHEMA = "shchem.local-import-continuation.v1"
INVENTORY = "sh-chem-db/.intake/2026-07-30-user-teaching-pack/archive_inventory.csv"
RANGES_DRAFT = "native-word-question-ranges-v1"
CATEGORIES = (
    "readable_pure_text",
    "requires_image",
    "scope_or_material_incomplete",
    "teacher_or_pinned_protected",
)
_PLACEHOLDER = re.compile(r"【(?:待查看原文|未提取到文字)")


class ContinuationError(ValueError):
    """A machine-readable failure; messages never contain source text."""


def json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContinuationError("duplicate_json_key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _inside(root, path):
    root, path = Path(root).resolve(), Path(path)
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ContinuationError("input_path_outside_declared_root")
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ContinuationError("linked_input_not_supported")
        if part == root:
            break
    return resolved


class InputAudit:
    def __init__(self, workspace, state):
        self.roots = {
            "workspace": Path(workspace).resolve(),
            "state": Path(state).resolve(),
        }
        self.entries = {}

    @staticmethod
    def fingerprint(path):
        if not path.exists():
            return {"exists": False, "bytes": None, "sha256": None}
        if not path.is_file():
            raise ContinuationError("input_is_not_file")
        digest = hashlib.sha256()
        count = 0
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                count += len(block)
        return {"exists": True, "bytes": count, "sha256": digest.hexdigest()}

    def watch(self, scope, path, role, *, optional=False):
        path = _inside(self.roots[scope], path)
        current = self.fingerprint(path)
        if not optional and not current["exists"]:
            raise ContinuationError("required_input_missing:" + role)
        key = (scope, path.relative_to(self.roots[scope]).as_posix())
        prior = self.entries.get(key)
        if prior and prior["before"] != current:
            raise ContinuationError("input_changed_during_read:" + role)
        self.entries[key] = {
            "scope": scope,
            "path": key[1],
            "role": role,
            "before": current,
        }
        return path

    def read(self, scope, path, role):
        path = self.watch(scope, path, role)
        raw = path.read_bytes()
        key = (scope, path.relative_to(self.roots[scope]).as_posix())
        if sha(raw) != self.entries[key]["before"]["sha256"]:
            raise ContinuationError("input_changed_during_read:" + role)
        return raw

    def verify(self):
        rows = []
        for key in sorted(self.entries):
            entry = self.entries[key]
            path = self.roots[entry["scope"]] / entry["path"]
            after = self.fingerprint(_inside(self.roots[entry["scope"]], path))
            if after != entry["before"]:
                raise ContinuationError("input_changed_during_run:" + entry["role"])
            rows.append({**entry, "after": after, "unchanged": True})
        return {
            "schema_version": SCHEMA,
            "checked_inputs": len(rows),
            "changed_inputs": 0,
            "all_unchanged": True,
            "inputs": rows,
        }


def _inventory(workspace, audit, expected_sources):
    path = workspace / INVENTORY
    rows = list(
        csv.DictReader(
            io.StringIO(
                audit.read("workspace", path, "archive_inventory").decode("utf-8-sig")
            )
        )
    )
    selected = [row for row in rows if row["document_role"] == "解析版"]
    if len(selected) != expected_sources or expected_sources < 1:
        raise ContinuationError("analysis_source_denominator_mismatch")
    if len({row["package_id"] for row in selected}) != len(selected):
        raise ContinuationError("duplicate_inventory_package")
    if len({row["sha256"] for row in selected}) != len(selected):
        raise ContinuationError("duplicate_inventory_source_sha256")
    return path.parent, sorted(selected, key=lambda row: row["package_id"]), len(rows)


def _saved_batch(state, rows, audit):
    expected = {row["sha256"] for row in rows}
    matches = []
    for path in sorted((state / "visual-import-v2/batches").glob("*.json")):
        value = _json(audit.read("state", path, "saved_import_descriptor"))
        sources = value.get("plan", {}).get("sources", [])
        word_sources = [
            source
            for source in sources
            if str(source.get("filename", "")).lower().endswith(".docx")
        ]
        if {source.get("source_sha256") for source in word_sources} == expected:
            if (
                len(word_sources) != len(rows)
                or value.get("central_question_bank_write") is not False
            ):
                raise ContinuationError("saved_batch_scope_invalid")
            matches.append((value["batch_id"], word_sources))
    if len(matches) != 1:
        raise ContinuationError("unique_matching_saved_word_batch_required")
    return matches[0]


def _attributes(state, keys, audit):
    path = audit.watch(
        "state", state / "word-question-attributes.sqlite3", "word_attribute_database"
    )
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = audit.watch(
            "state", Path(str(path) + suffix), "sqlite_sidecar" + suffix, optional=True
        )
        if sidecar.exists():
            raise ContinuationError("sqlite_sidecars_require_offline_snapshot")
    raw = audit.read("state", path, "word_attribute_database")
    if raw[:16] != b"SQLite format 3\x00" or len(raw) < 100:
        raise ContinuationError("invalid_sqlite_database_header")
    if raw[18:20] != b"\x01\x01":
        raise ContinuationError("sqlite_wal_mode_requires_offline_snapshot")
    found = {}
    # Even mode=ro can write a live WAL shared-memory file. SQLite sees only
    # audited bytes in memory here, never the user's database or sidecar paths.
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        total = connection.execute("SELECT COUNT(*) FROM attributes").fetchone()[0]
        for key, payload in connection.execute("SELECT key,payload FROM attributes"):
            if key not in keys:
                continue
            row = validate_attributes(_json(payload))
            if row["key"] != key or key in found:
                raise ContinuationError("attribute_key_mismatch_or_duplicate")
            found[key] = row
    finally:
        connection.close()
    return found, total


def _binding(item, stored):
    if stored is None:
        return "missing"
    pairs = {
        "key": "key",
        "source_sha256": "source_sha256",
        "source_revision": "source_revision",
        "question_revision": "revision",
        "index_revision": "index_revision",
        "extraction_revision": "extraction_revision",
    }
    return (
        "current"
        if all(stored[left] == item[right] for left, right in pairs.items())
        else "stale"
    )


def _classify(item, stored, binding):
    if stored is not None and automatic_tags_protected(stored):
        return "teacher_or_pinned_protected"
    blocks = item["context_blocks"] + item["question_blocks"]
    assets = [asset for block in blocks for asset in block.get("assets", [])]
    material = stored.get("material_status", {}) if stored else {}
    incomplete = (
        binding == "stale"
        or not item.get("selection_ready")
        or not item.get("export_ready")
        or any(material.get(key) for key in ("missing_context", "missing_visual"))
        or item.get("content_quality", {}).get("status")
        not in (None, "not_fully_reviewed")
    )
    if incomplete:
        return "scope_or_material_incomplete"
    if assets:
        return "requires_image"
    if not any(block.get("text", "").strip() for block in blocks) or any(
        _PLACEHOLDER.search(block.get("text", "")) or block.get("warnings")
        for block in blocks
    ):
        return "scope_or_material_incomplete"
    return "readable_pure_text"


def _task(item, stored, source):
    binding = _binding(item, stored)
    missing = []
    if binding != "current" or stored["primary_knowledge"]["id"] == "unknown":
        missing.append("primary_knowledge")
    if binding != "current" or not stored["curriculum_candidates"]:
        missing.append("curriculum_mapping")
    if not missing:
        return None
    category = _classify(item, stored, binding)
    blocks = item["context_blocks"] + item["question_blocks"]
    assets = [asset for block in blocks for asset in block.get("assets", [])]
    evidence = {
        "package_id": source["package_id"],
        "source_name": source["source_name"],
        "source_path": source["source_path"],
        "source_sha256": item["source_sha256"],
        "preview_sha256": source["preview_sha256"],
        "source_revision": item["source_revision"],
        "extraction_revision": item["extraction_revision"],
        "index_revision": item["index_revision"],
        "question_key": item["key"],
        "question_revision": item["revision"],
        "attribute_revision": stored["revision"] if stored else None,
        "attribute_edit_version": stored["edit_version"] if stored else None,
        "attribute_binding_status": binding,
        "ranges": {
            key: item.get(key)
            for key in (
                "origin_block_start",
                "block_start",
                "question_end",
                "answer_start",
                "block_end",
                "context_start",
                "context_end",
            )
        },
        "missing_fields": missing,
        "category": category,
        "has_answer_range": bool(item["answer_blocks"]),
        "has_shared_context": bool(item["context_blocks"]),
        "question_context_asset_count": len(assets),
        "unsupported_asset_count": sum(
            asset.get("preview_supported") is not True for asset in assets
        ),
        "warning_count": len(item.get("warnings", [])),
        "source_issue_count": len(item.get("content_quality", {}).get("issues", [])),
        "status": "blocked_pending_review"
        if category in CATEGORIES[2:]
        else "pending_source_review",
        "next_action": {
            "readable_pure_text": "read_complete_source_ranges_then_propose_labels",
            "requires_image": "inspect_original_objects_and_complete_source_ranges",
            "scope_or_material_incomplete": "resolve_binding_or_material_scope_before_label_review",
            "teacher_or_pinned_protected": "preserve_existing_teacher_or_pinned_fields",
        }[category],
        "candidate_only": True,
        "human_reviewed": False,
    }
    return {"task_id": "IMPORT-REVIEW-" + sha(json_bytes(evidence))[:24], **evidence}


def build_continuation(workspace, state, *, expected_sources=98):
    workspace, state = Path(workspace).resolve(), Path(state).resolve()
    audit = InputAudit(workspace, state)
    inventory_root, inventory, inventory_count = _inventory(
        workspace, audit, expected_sources
    )
    batch_id, saved_sources = _saved_batch(state, inventory, audit)
    saved = {row["source_sha256"]: row for row in saved_sources}
    state_payload = _json(
        audit.read("state", state / "desktop-state.v1.json", "desktop_range_state")
    )
    if state_payload.get("schema_version") != "shchem.desktop-state.v1":
        raise ContinuationError("desktop_state_schema_mismatch")
    overrides = state_payload.get("drafts", {}).get(RANGES_DRAFT, {}).get("ranges", {})
    if not isinstance(overrides, dict):
        raise ContinuationError("range_override_mapping_required")
    quality_path = workspace / QUALITY_PATH
    audit.watch("workspace", quality_path, "source_quality_notes", optional=True)
    quality = source_quality_notes(workspace)
    cache = WordPreviewCache(state / "word-question-previews")
    items, sources, keys = [], [], set()
    for row in inventory:
        path = _inside(workspace, inventory_root / row["output_relative_path"])
        raw = audit.read("workspace", path, "original_analysis_docx")
        if sha(raw) != row["sha256"] or len(raw) != int(row["bytes"]):
            raise ContinuationError("original_inventory_hash_or_size_mismatch")
        descriptor = saved[row["sha256"]]
        if descriptor["filename"] != path.name or descriptor["size_bytes"] != len(raw):
            raise ContinuationError("saved_import_source_identity_mismatch")
        cache_path = cache._path(row["sha256"], path.name)
        compressed = audit.read("state", cache_path, "native_preview_cache")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            decoded = stream.read(MAX_PREVIEW_BYTES + 1)
        if len(decoded) > MAX_PREVIEW_BYTES:
            raise ContinuationError("preview_size_limit_exceeded")
        record = _json(decoded)
        preview = record.get("preview")
        if record.get("cache_revision") != CACHE_REVISION or not cache._valid(
            preview, row["sha256"], path.name
        ):
            raise ContinuationError("native_preview_cache_stale_or_invalid")
        source = {
            "package_id": row["package_id"],
            "source_name": path.name,
            "source_path": path.relative_to(workspace).as_posix(),
            "source_sha256": row["sha256"],
            "source_bytes": len(raw),
            "source_file_id": descriptor["source_file_id"],
            "preview_sha256": sha(compressed),
            "source_revision": preview["revision"],
        }
        source_items = []
        for item in question_index.index_word_questions(preview):
            override = overrides.get(item["key"])
            if override:
                if override.get("source_revision") != preview["revision"]:
                    raise ContinuationError("saved_range_override_stale")
                item = question_index.apply_question_range(
                    preview, item, **override["range"]
                )
            item = apply_source_quality(item, quality)
            if item["key"] in keys:
                raise ContinuationError("duplicate_active_question_key")
            keys.add(item["key"])
            source_items.append(item)
            items.append((item, source))
        sources.append(
            {
                **source,
                "question_count": len(source_items),
                "selectable_count": sum(
                    bool(item.get("selection_ready")) for item in source_items
                ),
            }
        )
    if not keys:
        raise ContinuationError("empty_current_question_index")
    stored, database_count = _attributes(state, keys, audit)
    queue, categories, missing, bindings = [], Counter(), Counter(), Counter()
    current_primary = current_mapping = original_exam_known = 0
    for item, source in items:
        attributes = stored.get(item["key"])
        binding = _binding(item, attributes)
        bindings[binding] += 1
        if binding == "current":
            current_primary += attributes["primary_knowledge"]["id"] != "unknown"
            current_mapping += bool(attributes["curriculum_candidates"])
            original_exam_known += (
                attributes["original_source"]["exam_type"]["value"] != "unknown"
            )
        task = _task(item, attributes, source)
        if task:
            queue.append(task)
            categories[task["category"]] += 1
            missing.update(task["missing_fields"])
    queue.sort(
        key=lambda row: (
            CATEGORIES.index(row["category"]),
            row["package_id"],
            row["ranges"]["origin_block_start"],
            row["task_id"],
        )
    )
    for source in sources:
        source_tasks = [
            task for task in queue if task["source_sha256"] == source["source_sha256"]
        ]
        source["pending_task_count"] = len(source_tasks)
        source["pending_categories"] = dict(
            sorted(Counter(task["category"] for task in source_tasks).items())
        )
    summary = {
        "schema_version": SCHEMA,
        "status": "candidate_review_queue_built",
        "batch_id": batch_id,
        "inventory_rows": inventory_count,
        "analysis_sources": len(sources),
        "current_questions": len(items),
        "selectable_questions": sum(
            bool(item.get("selection_ready")) for item, _ in items
        ),
        "database_attribute_rows": database_count,
        "active_attribute_rows": len(stored),
        "surplus_attribute_rows_not_counted_as_questions": database_count - len(stored),
        "attribute_binding_counts": dict(sorted(bindings.items())),
        "current_primary_known": current_primary,
        "current_curriculum_mapped": current_mapping,
        "original_exam_known": original_exam_known,
        "original_exam_unknown": len(items) - original_exam_known,
        "missing_field_counts": dict(sorted(missing.items())),
        "pending_tasks": len(queue),
        "category_counts": {name: categories[name] for name in CATEGORIES},
        "duplicate_source_sha256": 0,
        "duplicate_current_question_keys": 0,
        "source_hashes_verified": True,
        "candidate_only": True,
        "human_reviewed": False,
        "provider_calls": 0,
        "labels_written": 0,
        "source_or_personal_state_written": False,
        "central_question_bank_write": False,
        "retrieval_ready": False,
        "publication_allowed": False,
        "scope": "Saved analysis-version Word import only; current source ranges and metadata, without question text, answers, images or student data. Review tasks are not chemistry judgments or completed imports.",
    }
    return {"summary": summary, "queue": queue, "sources": sources, "audit": audit}


def write_continuation(workspace, state, output, *, expected_sources=98):
    workspace, state, output = (
        Path(workspace).resolve(),
        Path(state).resolve(),
        Path(output).resolve(),
    )
    if output.exists():
        raise ContinuationError("output_already_exists")
    if not output.is_relative_to(workspace / "outputs") or output.is_relative_to(state):
        raise ContinuationError("output_must_be_under_workspace_outputs")
    built = build_continuation(workspace, state, expected_sources=expected_sources)
    audit = built["audit"].verify()
    files = {
        "summary.json": json_bytes(built["summary"]),
        "review_queue.jsonl": b"".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
            for row in built["queue"]
        ),
        "source_coverage.json": json_bytes(built["sources"]),
        "input_hash_audit.json": json_bytes(audit),
    }
    manifest = {
        "schema_version": SCHEMA,
        "status": "complete_metadata_only",
        "input_hashes_unchanged": True,
        "files": [
            {"path": name, "bytes": len(files[name]), "sha256": sha(files[name])}
            for name in sorted(files)
        ],
    }
    temporary = output.with_name("." + output.name + "." + uuid4().hex + ".partial")
    _inside(workspace / "outputs", temporary)
    temporary.mkdir(parents=True, exist_ok=False)
    written = []
    try:
        for name, raw in files.items():
            target = temporary / name
            with target.open("xb") as stream:
                written.append(target)
                stream.write(raw)
        # A failed audit never publishes a success-shaped report directory.
        if built["audit"].verify() != audit:
            raise ContinuationError("input_audit_changed")
        target = temporary / "manifest.json"
        with target.open("xb") as stream:
            written.append(target)
            stream.write(json_bytes(manifest))
        if output.exists():
            raise ContinuationError("output_created_concurrently")
        temporary.rename(output)
    finally:
        # Only this invocation's exact files are removable, never source inputs.
        if temporary.is_dir():
            for target in written:
                with suppress(OSError):
                    target.unlink()
            with suppress(OSError):
                temporary.rmdir()
    return built["summary"]

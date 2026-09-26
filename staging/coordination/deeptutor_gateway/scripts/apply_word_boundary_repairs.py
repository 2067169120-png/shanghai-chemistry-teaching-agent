"""One reviewed, source-pinned local repair; dry-run unless --apply is explicit.

Reports contain identifiers, concise attribute metadata and hashes; no question
or answer text. No source text is embedded here.
The JSON range store and SQLite attribute store are separate transactions: an
interrupted apply is reported as partial, never silently rolled back or retried.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_source_quality import (
    apply_source_quality,
    source_quality_notes,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    _seal,
    load_attribute_catalog,
    suggest_attributes,
    validate_attributes,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
    _key,
    apply_question_range,
    index_word_questions,
)
from integrations.deeptutor_shchem_v1.desktop_word_questions import RANGES_DRAFT

BATCH_ID = "DESKTOPBATCH-b2f0b01ab11633066da08744a68db874"
SOURCES = (
    (
        "PKG-022",
        "6dc0a132c5a1716c195fba215943d3e917e5d3aea3b559db5dfcfee43f1f9dc9",
        (316, 340),
    ),
    (
        "PKG-040",
        "fbd71747d2b620c02d8a112a8f370f6f7459248a97e2861c69302bda3249b01c",
        (265,),
    ),
    (
        "PKG-069",
        "8f8da61b8146b45bc39bc6162dae3a8895fb00ce1b646df60f168c00b916a325",
        (293,),
    ),
    (
        "PKG-092",
        "b0c0276c5d9874864cecf8a5853556eba7f1c21d44e23bdc40620a1b5f983c4a",
        (79,),
    ),
)
MANUAL_RANGES = {
    "PKG-040": (265, 266, 267, 268, "unmarked_answer"),
    "PKG-069": (293, 295, 296, 297, "self_contained_reference"),
    "PKG-092": (79, 84, 85, 87, "nonstandard_label"),
}
BOUNDARY_FIELDS = (
    "block_start",
    "question_end",
    "answer_start",
    "block_end",
    "context_start",
    "context_end",
)
PINNED_ATTRIBUTE_REVISION = "word-boundary-pinned-attributes-20260912-v1"
PINNED_KNOWLEDGE_NAMES = {
    "K09": "反应方向、限度和速率进阶",
    "K11": "氧化还原与电化学",
    "K12": "原子结构与元素性质进阶",
}


class RepairStop(RuntimeError):
    """A failed evidence or access gate; do not work around it."""


class NoProviders:
    def __init__(self):
        self.calls = 0

    def list_metadata(self):
        return ()

    def __getattr__(self, _name):
        self.calls += 1
        raise RepairStop("provider_access_forbidden")


def _require(condition, code):
    if not condition:
        raise RepairStop(code)


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _file_hash(path):
    path = Path(path)
    _require(
        path.is_file() and not path.is_symlink(), "required_file_missing_or_linked"
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_id(package, digest):
    return f"SRC-HANDOUT-{package[-3:]}-{digest[:16]}"


def _boundaries(package):
    start, qend, answer, end, issue = MANUAL_RANGES[package]
    return {
        "block_start": start,
        "question_end": qend,
        "answer_start": answer,
        "block_end": end,
        "context_start": None,
        "context_end": None,
        "reviewed_issues": [issue],
    }


def source_pinned_suggestion(question, suggestion, taxonomy):
    """Apply only the three approved source-specific automatic label corrections.

    The block anchors verify source identity, not chemical truth. The mapping
    comes from this repair's prior question-and-figure review, remains an
    automatic suggestion, and cannot confirm a teacher judgement or exam type.
    """
    row = validate_attributes(suggestion)
    package = next(
        (
            package
            for package, sha, _ in SOURCES
            if sha == question.get("source_sha256")
        ),
        None,
    )
    target = (package, question.get("origin_block_start"))
    if target not in {("PKG-022", 340), ("PKG-040", 265), ("PKG-069", 293)}:
        return row
    _require(
        row["annotation_source"] == "auto_suggested",
        "pinned_override_requires_automatic_proposal",
    )
    _require(
        question["key"]
        == _key(question["source_sha256"], question["origin_block_start"]),
        "pinned_override_question_identity_mismatch",
    )
    _require(
        all(
            row[field] == question[field]
            for field in ("key", "source_sha256", "source_revision")
        )
        and row["question_revision"] == question["revision"],
        "pinned_override_source_binding_mismatch",
    )
    _require(row["source"]["package_id"] == package, "pinned_override_package_mismatch")
    blocks = {block["index"]: block for block in question["question_blocks"]}
    _require(
        len(blocks) == len(question["question_blocks"]),
        "pinned_override_ambiguous_blocks",
    )
    knowledge = (
        taxonomy.get("knowledge_points", []) if isinstance(taxonomy, dict) else []
    )

    def evidence(index, needles):
        block = blocks.get(index)
        _require(
            block is not None and isinstance(block.get("text"), str),
            "pinned_override_source_block_missing",
        )
        text = block["text"]
        _require(
            all(needle in text for needle in needles),
            "pinned_override_source_anchor_missing",
        )
        # Runtime excerpts only; no question or answer wording is in this script.
        excerpts = []
        for needle in needles:
            start = max(0, text.index(needle) - 100)
            quote = text if len(text) <= 500 else text[start : start + 500]
            entry = {"kind": "question_text", "quote": quote, "block_index": index}
            if entry not in excerpts:
                excerpts.append(entry)
        return excerpts

    def label(identifier, bound_evidence):
        matches = [entry for entry in knowledge if entry.get("id") == identifier]
        _require(
            len(matches) == 1
            and matches[0].get("name") == PINNED_KNOWLEDGE_NAMES[identifier],
            "pinned_override_taxonomy_mismatch",
        )
        return {
            "id": identifier,
            "label": matches[0]["name"],
            "status": "auto_suggested",
            "evidence": bound_evidence,
        }

    if target == ("PKG-022", 340):
        row["primary_knowledge"] = label(
            "K09", evidence(344, ("决速步骤",)) + evidence(345, ("活化能",))
        )
        row["supporting_knowledge"] = [
            label(
                "K11",
                evidence(
                    346,
                    (
                        "反应1的机理",
                        "Fe^{2+",
                        "Fe^{3+",
                        "H_{2}O_{2",
                    ),
                ),
            )
        ]
    elif target == ("PKG-040", 265):
        row["primary_knowledge"] = label(
            "K12",
            evidence(265, ("电负性", "2025·上海卷")) + evidence(266, ("C", "H", "O")),
        )
        row["supporting_knowledge"] = []
        citations = list(
            re.finditer(
                r"[（(](20\d{2})·(上海)卷[^（）()\n]{1,32}[）)]", blocks[265]["text"]
            )
        )
        _require(
            len(citations) == 1 and citations[0].group(1) == "2025",
            "pinned_override_attribution_mismatch",
        )
        citation = {
            "kind": "question_text",
            "quote": citations[0].group(0),
            "block_index": 265,
        }
        original = row["original_source"]
        for field in ("exam_type", "grade"):
            _require(
                original[field]["value"] == "unknown"
                and original[field]["status"] == "unknown",
                "pinned_override_unexpected_exam_or_grade",
            )
        for field, value in (("year", "2025"), ("region", "上海")):
            _require(
                original[field]["value"] in {"unknown", value},
                "pinned_override_attribution_conflict",
            )
            original[field] = {
                "value": value,
                "status": "source_observed",
                "evidence": [citation],
            }
        if citation not in original["citation_quotes"]:
            original["citation_quotes"].append(citation)
    else:
        row["primary_knowledge"] = {
            "id": "unknown",
            "label": "广义水解观点下的产物判断",
            "status": "auto_suggested",
            "evidence": evidence(293, ("广义的水解观认为", "根据上述观点")),
        }
        row["supporting_knowledge"] = []

    # Keep the source chapter verbatim, but do not inherit its unrelated
    # organic-textbook mapping. Do not invent a replacement textbook section.
    row["curriculum_candidates"] = []
    row["curriculum_status"] = "pending_mapping"
    row["rule_revision"] = PINNED_ATTRIBUTE_REVISION
    labels = [row["primary_knowledge"], *row["supporting_knowledge"]]
    _require(
        len({item["id"] for item in labels}) == len(labels),
        "pinned_override_duplicate_knowledge",
    )
    for item in labels:
        for entry in item["evidence"]:
            _require(
                entry["kind"] == "question_text"
                and entry["quote"] in blocks[entry["block_index"]]["text"],
                "pinned_override_evidence_not_in_question",
            )
    return validate_attributes(_seal(row))


def _normalized_range(value):
    _require(isinstance(value, dict), "invalid_existing_range")
    _require(
        set(value) <= {*BOUNDARY_FIELDS, "reviewed_issues"},
        "unknown_existing_range_fields",
    )
    return {
        **{field: value.get(field) for field in BOUNDARY_FIELDS},
        "reviewed_issues": sorted(value.get("reviewed_issues", [])),
    }


def _outside_state(state, range_keys):
    value = deepcopy(state)
    value.pop("updated_at", None)  # The existing state writer owns this timestamp.
    drafts = value.get("drafts", {})
    payload = drafts.get(RANGES_DRAFT)
    if payload is not None:
        ranges = payload.get("ranges", {})
        for key in range_keys:
            ranges.pop(key, None)
        if not ranges:
            payload.pop("ranges", None)
        if not payload:
            drafts.pop(RANGES_DRAFT, None)
    return _digest(value)


def _database_snapshot(store, keys):
    """Hash all non-target rows without logging or saving their payloads."""
    _require(store.path.is_file(), "existing_attribute_database_required")
    outside = {"attributes": [], "history": []}
    targets = {"attributes": {}, "history": {key: [] for key in keys}}
    counts = {}
    with store._connect(readonly=True) as connection:
        for table, columns, order in (
            ("attributes", "key,payload", "key"),
            ("history", "key,version,payload", "key,version"),
        ):
            rows = connection.execute(
                f"SELECT {columns} FROM {table} ORDER BY {order}"
            ).fetchall()
            counts[table] = len(rows)
            for row in rows:
                if row[0] not in keys:
                    outside[table].append(row)
                elif table == "attributes":
                    targets[table][row[0]] = row[1]
                else:
                    targets[table][row[0]].append(row[1:])
    return {
        "outside": {name: _digest(rows) for name, rows in outside.items()},
        "counts": counts,
        "targets": targets,
    }


def _blocks_digest(row):
    # Changing the role of complete blocks must not change their text or assets.
    blocks = [
        block
        for group in ("question_blocks", "answer_blocks", "context_blocks")
        for block in row[group]
    ]
    return _digest(sorted(blocks, key=lambda block: block["index"]))


def build_plan(facade):
    """Read four verified warm previews; all proposed writes validate in memory."""
    service = facade._word_questions()
    expected_ids = {_source_id(package, digest) for package, digest, _ in SOURCES}
    locations = {BATCH_ID: expected_ids}
    descriptor = facade._saved_visual_import_batch(BATCH_ID)
    sources = facade._restore_visual_import_sources(descriptor, source_ids=expected_ids)
    expected_shas = {digest for _, digest, _ in SOURCES}
    _require(
        {source.source_sha256 for source in sources} == expected_shas
        and len(sources) == 4,
        "four_pinned_sources_required",
    )
    files, previews = {}, {}
    for source in sources:
        digest = source.source_sha256
        package = next(package for package, sha, _ in SOURCES if sha == digest)
        _require(
            source.effective_source_file_id == _source_id(package, digest),
            "source_id_mismatch",
        )
        archive = facade._visual_import_root / "sources" / f"{digest}.docx"
        _require(_file_hash(archive) == digest, "archive_hash_mismatch")
        cache_path = service.preview_cache._path(digest, source.filename)
        _require(cache_path.is_file(), "verified_warm_preview_required_no_reparse")
        cache_hash = _file_hash(cache_path)
        preview = service.preview_cache.load(source.content, source.filename)
        _require(preview is not None, "verified_warm_preview_required_no_reparse")
        _require(preview["source_sha256"] == digest, "preview_source_mismatch")
        _require(
            _file_hash(cache_path) == cache_hash, "preview_changed_during_validation"
        )
        files[str(archive)] = digest
        files[str(cache_path)] = cache_hash
        # Prevent the catalogue's cache-miss fallback from creating any files.
        indexed = index_word_questions(preview)
        service._cache[(digest, source.filename)] = (
            deepcopy(preview),
            deepcopy(indexed),
        )
        previews[digest] = preview

    catalog, inventory = service._catalog(locations)
    _require(set(inventory) == expected_shas, "catalogue_source_set_mismatch")
    state = facade.state_store.snapshot()
    overrides = state.get("drafts", {}).get(RANGES_DRAFT, {}).get("ranges", {})
    _require(isinstance(overrides, dict), "invalid_range_store")
    quality = source_quality_notes(facade.paths.workspace_root)
    targets, proposals, manual = [], [], []
    for package, digest, origins in SOURCES:
        for origin in origins:
            matches = [
                row
                for row in catalog["items"]
                if row["source_sha256"] == digest
                and row["origin_block_start"] == origin
            ]
            _require(len(matches) == 1, "exact_target_missing_or_duplicated")
            current = matches[0]
            raw = next(
                row for row in inventory[digest][3] if row["key"] == current["key"]
            )
            planned = current
            existing = overrides.get(current["key"])
            if package in MANUAL_RANGES:
                boundaries = _boundaries(package)
                if existing is not None:
                    _require(
                        existing.get("source_revision") == previews[digest]["revision"]
                        and _normalized_range(existing.get("range")) == boundaries,
                        "different_teacher_range_exists_stop",
                    )
                planned = apply_question_range(previews[digest], raw, **boundaries)
                _require(
                    _blocks_digest(raw) == _blocks_digest(planned),
                    "manual_repair_changed_source_blocks",
                )
                planned.update(
                    {
                        field: current[field]
                        for field in (
                            "batch_id",
                            "source_id",
                            "source_name",
                            "archive_source_id",
                            "source_block_count",
                        )
                    }
                )
                planned = apply_source_quality(planned, quality)
                manual.append(
                    {
                        "key": current["key"],
                        "revision": current["revision"],
                        "source_revision": previews[digest]["revision"],
                        "boundaries": boundaries,
                        "already_applied": existing is not None,
                    }
                )
            elif existing is not None:
                _require(
                    existing.get("source_revision") == previews[digest]["revision"],
                    "stale_prefix_question_teacher_range",
                )
                restored = apply_question_range(
                    previews[digest], raw, **_normalized_range(existing.get("range"))
                )
                _require(
                    restored["revision"] == current["revision"],
                    "invalid_prefix_question_teacher_range",
                )
            _require(
                planned["export_ready"]
                and planned["selection_ready"]
                and planned["question_blocks"]
                and planned["answer_blocks"],
                "planned_target_not_complete_or_exportable",
            )
            targets.append(
                {
                    "package": package,
                    "origin": origin,
                    "current": current,
                    "planned": planned,
                }
            )
    _require(
        len(targets) == len({target["planned"]["key"] for target in targets}) == 5,
        "exactly_five_unique_targets_required",
    )
    first, added = (target["planned"] for target in targets[:2])
    _require(
        first["block_start"] == 316
        and first["block_end"] < 340
        and added["block_start"] == 340
        and all(
            block["index"] < 340
            for group in ("question_blocks", "answer_blocks")
            for block in first[group]
        ),
        "pkg022_not_split_into_two_complete_questions",
    )
    keys = {target["planned"]["key"] for target in targets}
    before = service.attribute_store.get_many(keys)
    taxonomy = load_attribute_catalog(facade.paths.workspace_root)
    for target in targets:
        row = target["planned"]
        old = before.get(row["key"])
        if old is None:
            _require(
                target["package"] == "PKG-022" and target["origin"] == 340,
                "previous_target_attribute_missing",
            )
            origin = before.get(first["key"])
            _require(origin is not None, "pkg022_verified_sibling_metadata_required")
        else:
            origin = old
        _require(
            origin["source_sha256"] == row["source_sha256"]
            and origin["source"]["package_id"] == target["package"]
            and origin["source"]["source_name"] == row["source_name"],
            "existing_attribute_source_binding_mismatch",
        )
        proposals.append(
            source_pinned_suggestion(
                row, suggest_attributes(row, origin["source"], taxonomy), taxonomy
            )
        )
    database = _database_snapshot(service.attribute_store, keys)
    for key in keys:
        history = database["targets"]["history"][key]
        for version, payload in history:
            entry = validate_attributes(json.loads(payload))
            _require(
                entry["key"] == key and entry["edit_version"] == version,
                "invalid_target_history_binding",
            )
        old = before.get(key)
        _require(
            (not history and old is None)
            or (
                old is not None
                and history
                and history[-1][0] == old["edit_version"]
                and json.loads(history[-1][1]) == old
            ),
            "target_current_history_mismatch",
        )
    return {
        "service": service,
        "locations": locations,
        "targets": targets,
        "manual": manual,
        "proposals": proposals,
        "keys": keys,
        "before_attributes": before,
        "state": state,
        "files": files,
        "database": database,
        "preview_revisions": {
            sha: preview["revision"] for sha, preview in previews.items()
        },
    }


def _check_unchanged(facade, plan, *, applied):
    now_state = facade.state_store.snapshot()
    range_keys = {item["key"] for item in plan["manual"]}
    outside_before = _outside_state(plan["state"], range_keys)
    outside_after = _outside_state(now_state, range_keys)
    _require(outside_before == outside_after, "non_target_state_changed")
    now_db = _database_snapshot(plan["service"].attribute_store, plan["keys"])
    _require(
        plan["database"]["outside"] == now_db["outside"],
        "non_target_attributes_or_history_changed",
    )
    now_files = {path: _file_hash(path) for path in plan["files"]}
    _require(plan["files"] == now_files, "source_or_preview_file_changed")
    if not applied:
        _require(
            _digest(plan["state"]) == _digest(now_state) and plan["database"] == now_db,
            "dry_run_or_preflight_wrote_state",
        )
    for key, history in plan["database"]["targets"]["history"].items():
        _require(
            now_db["targets"]["history"][key][: len(history)] == history,
            "target_history_prefix_changed",
        )
        old = plan["before_attributes"].get(key)
        if old and old["annotation_source"] == "teacher_modified":
            _require(
                plan["database"]["targets"]["attributes"][key]
                == now_db["targets"]["attributes"].get(key)
                and now_db["targets"]["history"][key] == history,
                "teacher_attribute_or_history_changed",
            )
    return {
        "non_target_state_before": outside_before,
        "non_target_state_after": outside_after,
        "non_target_database_before": plan["database"]["outside"],
        "non_target_database_after": now_db["outside"],
        "database_counts_before": plan["database"]["counts"],
        "database_counts_after": now_db["counts"],
        "source_and_cache_hashes_before": plan["files"],
        "source_and_cache_hashes_after": now_files,
        "target_history_prefixes_preserved": True,
        "teacher_attributes_preserved": True,
    }


def apply_plan(facade, plan, report, checkpoint):
    """Caller must hold the desktop lock; never call this on an unverified plan."""
    _check_unchanged(facade, plan, applied=False)
    for item in plan["manual"]:
        if item["already_applied"]:
            continue
        report["write_started"] = True
        checkpoint()
        updated = facade.word_question_update_range(
            item["key"], item["revision"], **item["boundaries"]
        )
        expected = next(
            target["planned"]
            for target in plan["targets"]
            if target["planned"]["key"] == item["key"]
        )
        _require(
            updated["revision"] == expected["revision"],
            "saved_range_differs_from_validated_plan",
        )
        report["range_writes"].append(item["key"])
        checkpoint()
    # Revalidate actual persisted ranges and content before any attribute write.
    catalog, _ = plan["service"]._catalog(plan["locations"])
    current = {row["key"]: row for row in catalog["items"]}
    for target in plan["targets"]:
        row = target["planned"]
        _require(
            current[row["key"]]["revision"] == row["revision"],
            "persisted_target_revision_mismatch",
        )
        _require(
            current[row["key"]]["export_ready"]
            and current[row["key"]]["selection_ready"]
            and current[row["key"]]["content_quality"] == row["content_quality"],
            "persisted_source_quality_changed",
        )
    actual_ranges = facade.state_store.snapshot()["drafts"][RANGES_DRAFT]["ranges"]
    for item in plan["manual"]:
        actual = actual_ranges.get(item["key"], {})
        _require(
            actual.get("source_revision") == item["source_revision"]
            and _normalized_range(actual.get("range")) == item["boundaries"],
            "persisted_range_fields_mismatch",
        )
    report["persisted_range_fields_verified"] = True
    _check_unchanged(facade, plan, applied=True)
    _require(
        _database_snapshot(plan["service"].attribute_store, plan["keys"])
        == plan["database"],
        "attributes_changed_since_preflight",
    )
    report["write_started"] = True
    checkpoint()
    saved = plan["service"].attribute_store.save_many(plan["proposals"])
    _require(
        len(saved) == 5 and {row["key"] for row in saved} == plan["keys"],
        "attribute_target_set_mismatch",
    )
    report["attribute_results"] = []
    for row in saved:
        old = plan["before_attributes"].get(row["key"])
        action = (
            "added"
            if old is None
            else "teacher_modified_preserved"
            if old["annotation_source"] == "teacher_modified"
            else "unchanged"
            if old == row
            else "updated"
        )
        expected = next(
            value for value in plan["proposals"] if value["key"] == row["key"]
        )
        _require(
            action == "teacher_modified_preserved"
            or row["question_revision"] == expected["question_revision"],
            "saved_attribute_revision_mismatch",
        )
        report["attribute_results"].append(
            {
                "key": row["key"],
                "action": action,
                "current_question_binding": row["question_revision"]
                == expected["question_revision"],
                "edit_version": row["edit_version"],
            }
        )
    _require(
        plan["service"].attribute_store.get_many(plan["keys"])
        == {row["key"]: row for row in saved},
        "persisted_attributes_differ_from_receipt",
    )
    checkpoint()


def _public_targets(plan):
    suggestions = {row["key"]: row for row in plan["proposals"]}
    result = []
    for target in plan["targets"]:
        row = target["planned"]
        suggestion = suggestions[row["key"]]
        original = suggestion["original_source"]
        old = plan["before_attributes"].get(row["key"])
        result.append(
            {
                "package": target["package"],
                "origin_block_start": target["origin"],
                "key": row["key"],
                "before_revision": target["current"]["revision"],
                "planned_revision": row["revision"],
                "planned_range": {field: row[field] for field in BOUNDARY_FIELDS},
                "reviewed_issues": row.get("boundary_review", {}).get("issues", []),
                "source_quality": {
                    "status": row["content_quality"]["status"],
                    "human_reviewed": row["content_quality"]["human_reviewed"],
                    "issue_ids": [
                        issue["id"] for issue in row["content_quality"]["issues"]
                    ],
                },
                "existing_annotation_source": old["annotation_source"] if old else None,
                "automatic_proposal": {
                    "primary_knowledge": {
                        field: suggestion["primary_knowledge"][field]
                        for field in ("id", "label", "status")
                    },
                    "supporting_knowledge": [
                        {field: item[field] for field in ("id", "label", "status")}
                        for item in suggestion["supporting_knowledge"]
                    ],
                    "rule_revision": suggestion["rule_revision"],
                    "original_source": {
                        field: {
                            part: original[field][part] for part in ("value", "status")
                        }
                        for field in ("exam_type", "year", "region", "grade")
                    },
                    "source_chapter": suggestion["source"]["source_chapter"],
                    "curriculum_section_keys": [
                        node["section_key"]
                        for node in suggestion["curriculum_candidates"]
                    ],
                },
            }
        )
    return result


@contextmanager
def desktop_lock(state_root, *, apply):
    if not apply:
        yield
        return
    from PySide6.QtCore import QLockFile

    path = state_root / ".desktop-workbench.lock"
    # Do not remove even an apparently stale existing lock.
    _require(not path.exists() and not path.is_symlink(), "desktop_lock_exists_stop")
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)
    _require(lock.tryLock(0), "desktop_lock_unavailable_stop")
    try:
        yield
    finally:
        lock.unlock()  # Only this invocation's successfully acquired lock.


def _make_facade(state_root):
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths

    class ExistingPaths(DesktopPaths):
        def ensure_mutable_roots(self):
            # Constructor must not create settings or other folders in dry-run.
            _require(self.state_root.is_dir(), "existing_state_root_required")

    unused = object()
    return DesktopWorkbenchFacade(
        ExistingPaths.from_workspace(ROOT, state_root=state_root),
        provider_store=NoProviders(),
        theme_reader=unused,
        supplemental_reader=unused,
        curriculum_reader=unused,
        search_reader=unused,
        paper_export_jobs=unused,
        visual_import_renderer=unused,
        visual_import_native_importer=unused,
    )


def run_repair(*, state_root, report_path, apply=False, facade_factory=None):
    """Run with an exclusive new local report, optionally holding the app lock."""
    state_root = Path(state_root)
    _require(
        state_root.is_dir()
        and not state_root.is_symlink()
        and not state_root.is_junction(),
        "existing_unlinked_state_root_required",
    )
    state_root = state_root.resolve(strict=True)
    report_path = Path(report_path).resolve()
    _require(
        not report_path.is_relative_to(ROOT) and report_path.suffix == ".json",
        "report_must_be_local_json_outside_checkout",
    )
    _require(not report_path.exists(), "fresh_report_path_required")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "word-boundary-repairs-20260912-v1",
        "apply": apply,
        "status": "preflight",
        "write_started": False,
        "range_writes": [],
        "attribute_results": [],
        "provider_calls": None,
        "student_manager_initialized": None,
        "created_at": datetime.now(UTC).isoformat(),
        "batch_id": BATCH_ID,
    }
    facade = plan = None
    with report_path.open("x", encoding="utf-8") as handle:

        def checkpoint():
            handle.seek(0)
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())

        checkpoint()
        try:
            with desktop_lock(state_root, apply=apply):
                try:
                    facade = (facade_factory or _make_facade)(state_root)
                    plan = build_plan(facade)
                    report["preview_revisions"] = plan["preview_revisions"]
                    report["targets"] = _public_targets(plan)
                    if apply:
                        apply_plan(facade, plan, report, checkpoint)
                    report["status"] = "applied" if apply else "dry_run_validated"
                finally:
                    if facade is not None:
                        report["provider_calls"] = facade._providers.calls
                        report["student_manager_initialized"] = (
                            facade._student_analysis_manager is not None
                        )
                        _require(
                            report["provider_calls"] == 0, "unexpected_provider_access"
                        )
                        _require(
                            not report["student_manager_initialized"],
                            "unexpected_student_manager",
                        )
                    if plan is not None:
                        report["integrity"] = _check_unchanged(
                            facade, plan, applied=report["write_started"]
                        )
        except Exception as exc:  # noqa: BLE001 - retain a partial-apply audit for every ordinary failure.
            report["status"] = (
                "failed_partial_apply"
                if report["write_started"]
                else "blocked_no_apply"
            )
            report["error"] = (
                str(exc) if isinstance(exc, RepairStop) else type(exc).__name__
            )
        finally:
            checkpoint()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="New private JSON path outside the checkout",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_repair(
            state_root=args.state_root, report_path=args.report, apply=args.apply
        )
    except (OSError, RepairStop) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked_no_apply",
                    "error": str(exc)
                    if isinstance(exc, RepairStop)
                    else type(exc).__name__,
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(args.report.resolve()),
                "range_write_count": len(report["range_writes"]),
                "attribute_result_count": len(report["attribute_results"]),
            }
        )
    )
    return 0 if report["status"] in {"applied", "dry_run_validated"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

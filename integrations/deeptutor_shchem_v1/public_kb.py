from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, ClassVar

from .reader_cancellation import check_read_cancelled
from .security import SecurityError, validate_identifier


class ReadOnlyDataError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code = code
        self.status = status


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReadOnlyDataError("public_data_unavailable", "public data is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & 0x400)


def _checked_exact_path(root: Path, relative: str) -> Path:
    """Resolve a compile-time allowlisted path without accepting reparse points."""

    check_read_cancelled()
    if not relative or "\\" in relative or Path(relative).is_absolute():
        raise ReadOnlyDataError("public_path_not_allowed", "public path is not allowlisted", 403)
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReadOnlyDataError("public_path_not_allowed", "public path is not allowlisted", 403)
    raw_root = root.absolute()
    if not raw_root.exists():
        raise ReadOnlyDataError("public_data_unavailable", "public data root is unavailable")
    if _is_reparse(raw_root):
        raise ReadOnlyDataError("public_reparse_rejected", "public data root is a reparse point", 403)
    resolved_root = raw_root.resolve(strict=True)
    candidate = raw_root.joinpath(*parts)
    current = raw_root
    for part in parts:
        current = current / part
        if not current.exists():
            raise ReadOnlyDataError("public_data_unavailable", "public data is unavailable")
        if _is_reparse(current):
            raise ReadOnlyDataError("public_reparse_rejected", "public data reparse points are rejected", 403)
    resolved = candidate.resolve(strict=True)
    try:
        common = Path(os.path.commonpath([str(resolved_root), str(resolved)]))
    except ValueError as exc:
        raise ReadOnlyDataError("public_path_escape", "public path escaped its root", 403) from exc
    if common != resolved_root or resolved != candidate.absolute():
        raise ReadOnlyDataError("public_path_escape", "public path escaped its root", 403)
    if not resolved.is_file():
        raise ReadOnlyDataError("public_data_unavailable", "public data is unavailable")
    return resolved


def _json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadOnlyDataError("public_data_invalid", f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReadOnlyDataError("public_data_invalid", f"{label} must be a JSON object")
    return value


def _evidence_value(value: Any) -> Any:
    if isinstance(value, dict):
        explicit = value.get("value")
        if explicit is not None:
            return explicit
        for key in ("candidate_values", "values", "candidate_labels"):
            candidates = value.get(key)
            if isinstance(candidates, list) and candidates:
                return candidates
        if "value" in value:
            return None
    return value


def _as_string_list(value: Any) -> list[str]:
    value = _evidence_value(value)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    if isinstance(value, dict):
        candidates = value.get("candidate_values") or value.get("values") or value.get("candidate_labels")
        if candidates is None:
            candidates = value.get("declared_prelabel") or value.get("candidate_label") or value.get("measured")
        return _as_string_list(candidates)
    return []


def _tag_evidence(*sources: Any) -> dict[str, Any]:
    values: set[str] = set()
    states: list[str] = []
    relevant_dicts: list[dict[str, Any]] = []
    for source in sources:
        values.update(_as_string_list(source))
        if not isinstance(source, dict):
            continue
        if _as_string_list(source):
            relevant_dicts.append(source)
        state = source.get("evidence_state")
        if isinstance(state, str) and state and state not in states:
            states.append(state)
    human_verified = bool(relevant_dicts) and all(
        source.get("human_verified") is True
        and source.get("value") is not None
        and source.get("evidence_state")
        in {"human_verified", "verified", "reviewed"}
        for source in relevant_dicts
    )
    if human_verified:
        evidence_state = "human_verified"
    elif "machine_candidate" in states:
        evidence_state = "machine_candidate"
    elif states:
        evidence_state = states[0]
    else:
        evidence_state = "unknown_candidate_provenance"
    return {
        "candidate_values": sorted(values),
        "evidence_state": evidence_state,
        "source_evidence_states": states,
        "human_verified": human_verified,
        "candidate_only": not human_verified,
    }


class PublicKBReader:
    """Read-only, hash-verified projection of the public candidate KB only."""

    INDEX_ROOT = "kb/classification/theme_hierarchy_master_index_v1_2026-08-03"
    TAXONOMY = "kb/knowledge_taxonomy.json"
    MANIFEST = f"{INDEX_ROOT}/manifest.json"
    LAYER_FILES: ClassVar[dict[str, str]] = {
        "paper": f"{INDEX_ROOT}/paper_records.jsonl",
        "theme_big_question": f"{INDEX_ROOT}/theme_big_question_records.jsonl",
        "printed_question": f"{INDEX_ROOT}/printed_question_records.jsonl",
        "atomic_part": f"{INDEX_ROOT}/atomic_part_records.jsonl",
    }
    REVIEW_QUEUE = f"{INDEX_ROOT}/human_review_queue.jsonl"
    SOURCE_VERSION_CROSSWALK_ROOT = (
        "kb/workbench/source_version_crosswalk_v1_2026-08-26"
    )
    SOURCE_VERSION_CROSSWALK_MANIFEST = (
        f"{SOURCE_VERSION_CROSSWALK_ROOT}/manifest.json"
    )
    SOURCE_VERSION_CROSSWALK_DATA = (
        f"{SOURCE_VERSION_CROSSWALK_ROOT}/source_version_crosswalk.json"
    )
    SOURCE_VERSION_CROSSWALK_SCHEMA = (
        f"{SOURCE_VERSION_CROSSWALK_ROOT}/source_version_crosswalk.schema.json"
    )
    SOURCE_VERSION_CROSSWALK_FILES: ClassVar[tuple[str, ...]] = (
        f"{SOURCE_VERSION_CROSSWALK_ROOT}/README.md",
        f"{SOURCE_VERSION_CROSSWALK_ROOT}/mutation_tests.py",
        SOURCE_VERSION_CROSSWALK_DATA,
        SOURCE_VERSION_CROSSWALK_SCHEMA,
        f"{SOURCE_VERSION_CROSSWALK_ROOT}/tests/test_source_version_crosswalk.py",
        f"{SOURCE_VERSION_CROSSWALK_ROOT}/validate_crosswalk.py",
    )
    SOURCE_VERSION_UPSTREAM_FILES: ClassVar[tuple[str, ...]] = (
        "catalog.csv",
        "kb/corpus_manifest.jsonl",
        (
            "04_市重点校考卷/华东师范大学第二附属中学_文章标注/高二/"
            "2024学年第一学期期中-化学试卷与非官方参考答案/manifest.json"
        ),
        (
            "kb/formal/candidates/intake_round_09_2026-08-03/"
            "huaer_affiliated_high2_2024_fall_midterm/manifest.json"
        ),
        (
            "kb/formal/candidates/intake_round_09_2026-08-03/"
            "huaer_affiliated_high2_2024_fall_midterm/page_reviews.jsonl"
        ),
        (
            "kb/formal/candidates/intake_round_09_2026-08-03/"
            "fudan_affiliated_high1_2025_fall_midterm/manifest.json"
        ),
        (
            "kb/formal/candidates/intake_round_09_2026-08-03/"
            "fudan_affiliated_high1_2025_fall_midterm/page_reviews.jsonl"
        ),
        (
            "kb/formal/candidates/intake_round_2026-08-02/"
            "datong_high1_2025_fall_midterm_complete_paper/manifest.json"
        ),
        (
            "kb/formal/candidates/intake_round_2026-08-02/"
            "datong_high1_2025_fall_midterm_complete_paper/page_reviews.jsonl"
        ),
    )
    NODE_ID_FIELDS: ClassVar[dict[str, str]] = {
        "paper": "paper_id",
        "theme_big_question": "theme_big_question_id",
        "printed_question": "printed_question_id",
        "atomic_part": "atomic_part_id",
    }
    CHILD_TYPE: ClassVar[dict[str, str]] = {
        "paper": "theme_big_question",
        "theme_big_question": "printed_question",
        "printed_question": "atomic_part",
    }
    PURPOSES = frozenset(
        {
            "teacher_review",
            "candidate_inspection",
            "taxonomy_review",
            "lesson_planning",
            "student_support",
            "generation_review",
            "review",
        }
    )
    FILTERS = frozenset(
        {
            "node_type",
            "year",
            "region",
            "region_or_school",
            "paper_type",
            "grade",
            "tag",
            "tags",
            "knowledge_K",
            "ability_A",
            "context_C",
            "response_R",
            "representation_RP",
            "difficulty_D",
            "review_state",
        }
    )
    PRIVATE_MARKERS = (
        "06_学生错题档案",
        "private_profiles",
        "private_state",
        "student_data",
        "raw_student",
        "95-source",
        "profile_id",
    )

    def __init__(self, root: Path):
        self.root = root.absolute()
        self._manifest: dict[str, Any] | None = None
        self._manifest_hash: str | None = None
        self._bindings: dict[str, dict[str, Any]] = {}
        self._layers: dict[str, list[dict[str, Any]]] | None = None
        self._nodes: dict[tuple[str, str], dict[str, Any]] = {}

    def _read_exact(self, relative: str, *, verify_manifest: bool = False) -> tuple[bytes, str]:
        allowed = {
            self.TAXONOMY,
            self.MANIFEST,
            self.REVIEW_QUEUE,
            self.SOURCE_VERSION_CROSSWALK_MANIFEST,
            *self.LAYER_FILES.values(),
            *self.SOURCE_VERSION_CROSSWALK_FILES,
            *self.SOURCE_VERSION_UPSTREAM_FILES,
        }
        if relative not in allowed:
            raise ReadOnlyDataError("public_path_not_allowed", "public path is not allowlisted", 403)
        path = _checked_exact_path(self.root, relative)
        data = path.read_bytes()
        digest = _sha256(data)
        if verify_manifest:
            self._load_manifest()
            binding = self._bindings.get(relative)
            if not binding:
                raise ReadOnlyDataError("public_manifest_binding_missing", "public file has no manifest binding")
            if int(binding.get("bytes", -1)) != len(data) or binding.get("sha256") != digest:
                raise ReadOnlyDataError("public_manifest_hash_mismatch", "public file failed manifest verification")
        return data, digest

    def _load_manifest(self) -> dict[str, Any]:
        if self._manifest is not None:
            return self._manifest
        data, digest = self._read_exact(self.MANIFEST)
        manifest = _json_bytes(data, "public hierarchy manifest")
        bindings: dict[str, dict[str, Any]] = {}
        for item in manifest.get("output_bindings", []):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                relative = str(item["path"])
                if relative in {*self.LAYER_FILES.values(), self.REVIEW_QUEUE}:
                    bindings[relative] = item
        required = {*self.LAYER_FILES.values(), self.REVIEW_QUEUE}
        if not required.issubset(bindings):
            raise ReadOnlyDataError("public_manifest_binding_missing", "public hierarchy manifest is incomplete")
        self._manifest = manifest
        self._manifest_hash = digest
        self._bindings = bindings
        return manifest

    def taxonomy(self) -> dict[str, Any]:
        data, digest = self._read_exact(self.TAXONOMY)
        taxonomy = _json_bytes(data, "knowledge taxonomy")
        dimensions = taxonomy.get("dimensions")
        if not isinstance(dimensions, dict):
            raise ReadOnlyDataError("public_data_invalid", "taxonomy dimensions are missing")
        mapped = {
            "K": dimensions.get("knowledge_points", []),
            "A": dimensions.get("abilities", []),
            "C": dimensions.get("contexts", []),
            "R": dimensions.get("response_types", []),
            "D": dimensions.get("difficulty", []),
        }
        if any(not isinstance(mapped[key], list) for key in mapped):
            raise ReadOnlyDataError("public_data_invalid", "taxonomy dimensions are invalid")
        return {
            "schema_version": taxonomy.get("schema_version"),
            "title": taxonomy.get("title"),
            "scope": taxonomy.get("scope"),
            "evidence_note": taxonomy.get("evidence_note"),
            "dimensions": mapped,
            "dimension_codes": ["K", "A", "C", "R", "D"],
            "sha256": digest,
            "read_only": True,
            "edits_allowed": False,
            "claim_scope": "taxonomy_reference_not_official_exam_scope",
        }

    def _load_jsonl(self, relative: str) -> list[dict[str, Any]]:
        data, _ = self._read_exact(relative, verify_manifest=True)
        rows: list[dict[str, Any]] = []
        for line_number, raw in enumerate(data.decode("utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ReadOnlyDataError(
                    "public_data_invalid", f"public JSONL is invalid at line {line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise ReadOnlyDataError("public_data_invalid", "public JSONL row must be an object")
            rows.append(value)
        return rows

    def _load_layers(self) -> dict[str, list[dict[str, Any]]]:
        if self._layers is not None:
            return self._layers
        layers = {kind: self._load_jsonl(relative) for kind, relative in self.LAYER_FILES.items()}
        nodes: dict[tuple[str, str], dict[str, Any]] = {}
        for kind, rows in layers.items():
            id_field = self.NODE_ID_FIELDS[kind]
            for row in rows:
                node_id = row.get(id_field)
                if isinstance(node_id, str) and node_id:
                    nodes[(kind, node_id)] = row
        self._layers = layers
        self._nodes = nodes
        return layers

    @staticmethod
    def _identity(row: dict[str, Any]) -> dict[str, Any]:
        identity = row.get("source_identity")
        return identity if isinstance(identity, dict) else {}

    def _projection(self, kind: str, row: dict[str, Any]) -> dict[str, Any]:
        identity = self._identity(row)
        node_id = str(row.get(self.NODE_ID_FIELDS[kind], ""))
        parent_id: str | None = None
        parent_type: str | None = None
        if kind == "theme_big_question":
            parent_type, parent_id = "paper", row.get("parent_paper_id")
        elif kind == "printed_question":
            parent_type, parent_id = "theme_big_question", row.get("parent_theme_big_question_id")
        elif kind == "atomic_part":
            parent_type, parent_id = "printed_question", row.get("parent_printed_question_id")

        title = row.get("paper_title")
        if kind == "theme_big_question":
            title = _evidence_value(row.get("theme_title"))
        if kind == "printed_question":
            title = row.get("printed_question_number_literal")
        if kind == "atomic_part":
            title = row.get("printed_number_literal")
        tag_evidence = {
            "K": _tag_evidence(
                row.get("primary_knowledge_K"),
                row.get("supporting_knowledge_K"),
                row.get("knowledge_K_evidence"),
            ),
            "A": _tag_evidence(row.get("ability_A")),
            "C": _tag_evidence(row.get("context_C")),
            "R": _tag_evidence(
                row.get("response_R_evidence"), row.get("response_R")
            ),
            "RP": _tag_evidence(
                row.get("representation_RP_evidence"),
                row.get("representation_RP"),
            ),
            "D": _tag_evidence(row.get("difficulty")),
        }
        tags = {
            axis: list(evidence["candidate_values"])
            for axis, evidence in tag_evidence.items()
        }
        gates = row.get("gates") if isinstance(row.get("gates"), dict) else {}
        human_reviewed = gates.get("human_reviewed") is True
        return {
            "node_type": kind,
            "node_id": node_id,
            "parent_type": parent_type,
            "parent_id": parent_id if isinstance(parent_id, str) else None,
            "title_or_literal": title if isinstance(title, (str, int, float)) else None,
            "order": row.get(
                {
                    "paper": "paper_order",
                    "theme_big_question": "theme_order",
                    "printed_question": "printed_question_order",
                    "atomic_part": "atomic_part_order",
                }[kind]
            ),
            "metadata": {
                "year": row.get("year") or identity.get("year") or identity.get("academic_year"),
                "region_or_school": row.get("region_or_school") or identity.get("region_or_school") or identity.get("school"),
                "paper_type": row.get("paper_type") or identity.get("exam_type"),
                "grade": row.get("grade") or identity.get("grade"),
                "source_id": row.get("source_id"),
                "source_layer": row.get("source_layer"),
                "parent_binding_status": row.get("parent_binding_status") or row.get("parent_theme_assignment_status"),
            },
            "classification": {
                "item_type": _evidence_value(row.get("item_type")) or row.get("core_item_type"),
                "response_type": _evidence_value(row.get("response_type_summary")),
                "tags": tags,
                "tag_evidence": tag_evidence,
                "tag_scope": "candidate_values_only_unless_human_verified",
                "classification_status": row.get("classification_status") or "unknown",
                "measured_difficulty": False,
            },
            "lifecycle": {
                "candidate": True,
                "machine_pass": False,
                "human_review_pending": not human_reviewed,
                "human_reviewed": human_reviewed,
                "release": False,
            },
            "rights": {
                "content_exposed": False,
                "source_pixels_exposed": False,
                "publication_allowed": False,
            },
        }

    def _node(self, kind: str, node_id: str) -> dict[str, Any]:
        if kind not in self.LAYER_FILES:
            raise ReadOnlyDataError("invalid_node_type", "unsupported hierarchy node type", 400)
        validate_identifier(node_id, "node_id")
        self._load_layers()
        row = self._nodes.get((kind, node_id))
        if row is None:
            raise ReadOnlyDataError("hierarchy_node_not_found", "hierarchy node not found", 404)
        return row

    def node(self, kind: str, node_id: str) -> dict[str, Any]:
        row = self._node(kind, node_id)
        result = self._projection(kind, row)
        parents: list[dict[str, Any]] = []
        cursor = result
        missing_parent: dict[str, Any] | None = None
        while cursor.get("parent_type"):
            parent_kind = str(cursor["parent_type"])
            parent_value = cursor.get("parent_id")
            if not isinstance(parent_value, str) or not parent_value:
                missing_parent = {
                    "expected_parent_type": parent_kind,
                    "parent_id": None,
                    "status": "unknown",
                    "human_review_pending": True,
                }
                break
            parent_id = parent_value
            parent_row = self._nodes.get((parent_kind, parent_id))
            if parent_row is None:
                missing_parent = {
                    "expected_parent_type": parent_kind,
                    "parent_id": parent_id,
                    "status": "unknown",
                    "human_review_pending": True,
                }
                break
            cursor = self._projection(parent_kind, parent_row)
            parents.append(cursor)
        result["parents"] = list(reversed(parents))
        result["hierarchy_path"] = [item["node_type"] for item in result["parents"]] + [kind]
        result["parent_binding_status"] = result["metadata"].get(
            "parent_binding_status"
        )
        if kind == "paper":
            result["parent_chain_status"] = "paper_root"
            result["parent_display"] = "整卷根节点"
            result["hierarchy_path_complete"] = True
            result["missing_parent"] = None
        elif missing_parent is None and result["parents"] and result["parents"][0]["node_type"] == "paper":
            result["parent_chain_status"] = "complete"
            result["parent_display"] = "父链完整"
            result["hierarchy_path_complete"] = True
            result["missing_parent"] = None
        else:
            result["parent_chain_status"] = "missing_parent_pending_review"
            result["parent_display"] = "父节点缺失 / unknown / 待复核"
            result["hierarchy_path_complete"] = False
            result["missing_parent"] = missing_parent
        result["index_integrity"] = self.integrity()
        return result

    def children(self, kind: str, node_id: str, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        self._node(kind, node_id)
        child_kind = self.CHILD_TYPE.get(kind)
        if child_kind is None:
            return {"parent": {"node_type": kind, "node_id": node_id}, "items": [], "count": 0, "total": 0}
        parent_field = {
            "theme_big_question": "parent_paper_id",
            "printed_question": "parent_theme_big_question_id",
            "atomic_part": "parent_printed_question_id",
        }[child_kind]
        rows = [row for row in self._load_layers()[child_kind] if row.get(parent_field) == node_id]
        items = [self._projection(child_kind, row) for row in rows[offset : offset + limit]]
        return {
            "parent": {"node_type": kind, "node_id": node_id},
            "child_type": child_kind,
            "items": items,
            "count": len(items),
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "read_only": True,
        }

    @staticmethod
    def _evidence_summary(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        allowed = {"evidence_id", "binding_id", "role", "kind", "page_number", "sha256", "bytes", "coordinates", "hash_origin", "explicit_role"}
        summaries: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                summaries.append({"evidence_id": item})
            elif isinstance(item, dict):
                summaries.append({key: item[key] for key in allowed if key in item})
        return summaries

    def evidence(self, kind: str, node_id: str) -> dict[str, Any]:
        row = self._node(kind, node_id)
        provenance = []
        for item in row.get("physical_provenance_records", []):
            if isinstance(item, dict):
                provenance.append(
                    {
                        key: item.get(key)
                        for key in ("layer", "record_type", "record_id", "source_id", "source_record_index")
                        if item.get(key) is not None
                    }
                )
        return {
            "node": self._projection(kind, row),
            "evidence_missing": row.get("evidence_missing") is True,
            "evidence_refs": self._evidence_summary(row.get("evidence_refs")),
            "crop_refs": self._evidence_summary(row.get("crop_refs")),
            "physical_provenance": provenance,
            "source_files_opened": False,
            "content_exposed": False,
            "source_pixels_exposed": False,
            "read_only": True,
        }

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        unknown = set(payload) - {"query", "purpose", "filters", "limit", "offset"}
        if unknown:
            raise ReadOnlyDataError("unsafe_search_parameter", "search contains unsupported parameters", 400)
        purpose = payload.get("purpose")
        if purpose not in self.PURPOSES:
            raise ReadOnlyDataError("purpose_required", "a safe read-only search purpose is required", 400)
        query = str(payload.get("query", "")).strip()
        if len(query) > 200:
            raise ReadOnlyDataError("query_too_long", "query is too long", 400)
        lowered = query.casefold()
        if any(marker.casefold() in lowered for marker in self.PRIVATE_MARKERS):
            raise ReadOnlyDataError("private_scope_rejected", "private or student paths are not searchable", 403)
        filters = payload.get("filters", {})
        if not isinstance(filters, dict):
            raise ReadOnlyDataError("invalid_filters", "filters must be an object", 400)
        if set(filters) - self.FILTERS:
            raise ReadOnlyDataError("unsafe_filter", "search filter is not allowlisted", 400)
        serialized_filters = json.dumps(filters, ensure_ascii=False).casefold()
        if any(marker.casefold() in serialized_filters for marker in self.PRIVATE_MARKERS):
            raise ReadOnlyDataError("private_scope_rejected", "private or student paths are not searchable", 403)
        try:
            limit = min(max(int(payload.get("limit", 20)), 1), 100)
            offset = min(max(int(payload.get("offset", 0)), 0), 10000)
        except (TypeError, ValueError) as exc:
            raise ReadOnlyDataError("invalid_pagination", "limit and offset must be integers", 400) from exc
        kinds = [str(filters["node_type"])] if filters.get("node_type") else list(self.LAYER_FILES)
        if any(kind not in self.LAYER_FILES for kind in kinds):
            raise ReadOnlyDataError("invalid_node_type", "unsupported hierarchy node type", 400)
        matches: list[dict[str, Any]] = []
        for kind in kinds:
            for row in self._load_layers()[kind]:
                item = self._projection(kind, row)
                if query and lowered not in json.dumps(item, ensure_ascii=False, sort_keys=True).casefold():
                    continue
                if not self._matches_filters(item, filters):
                    continue
                matches.append(item)
        page = matches[offset : offset + limit]
        return {
            "purpose": purpose,
            "filters": filters,
            "items": page,
            "count": len(page),
            "total": len(matches),
            "offset": offset,
            "limit": limit,
            "read_only": True,
            "claim_scope": "public_candidate_index_only_not_retrieval_ready",
            "index_integrity": self.integrity(),
        }

    @staticmethod
    def _matches_filters(item: dict[str, Any], filters: dict[str, Any]) -> bool:
        metadata = item["metadata"]
        classification = item["classification"]
        tags = classification["tags"]
        scalar_map = {
            "year": metadata.get("year"),
            "region": metadata.get("region_or_school"),
            "region_or_school": metadata.get("region_or_school"),
            "paper_type": metadata.get("paper_type"),
            "grade": metadata.get("grade"),
            "review_state": "human_review_pending" if item["lifecycle"]["human_review_pending"] else "human_reviewed",
        }
        for key, actual in scalar_map.items():
            if key in filters and str(filters[key]).casefold() not in str(actual or "").casefold():
                return False
        requested_tags: list[str] = []
        for key in ("tag", "tags"):
            if key in filters:
                requested_tags.extend(_as_string_list(filters[key]))
        axis_map = {"knowledge_K": "K", "ability_A": "A", "context_C": "C", "response_R": "R", "representation_RP": "RP", "difficulty_D": "D"}
        for key, axis in axis_map.items():
            if key in filters:
                requested = set(_as_string_list(filters[key]))
                if requested and not requested.intersection(tags.get(axis, [])):
                    return False
        return not requested_tags or bool(
            set(requested_tags).intersection(
                {tag for values in tags.values() for tag in values}
            )
        )

    def review_queue(self, *, limit: int, offset: int, level: str | None, state: str | None) -> dict[str, Any]:
        rows = self._load_jsonl(self.REVIEW_QUEUE)
        if level:
            rows = [row for row in rows if row.get("level") == level]
        if state:
            rows = [row for row in rows if row.get("state") == state]
        items = []
        for row in rows[offset : offset + limit]:
            items.append(
                {
                    key: row.get(key)
                    for key in ("review_key", "level", "record_id", "field", "state", "blocking_gate", "note", "independent_choice_section")
                }
            )
        return {
            "items": items,
            "count": len(items),
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "read_only": True,
            "human_review_pending": True,
            "release": False,
            "index_integrity": self.integrity(),
        }

    def integrity(self) -> dict[str, Any]:
        manifest = self._load_manifest()
        return {
            "manifest_sha256": self._manifest_hash,
            "manifest_status": manifest.get("status"),
            "structural_index_validation": "machine_pass",
            "hash_verified_on_read": True,
            "claim_boundary": manifest.get("claim_boundary"),
            "human_reviewed": False,
            "release": False,
        }


class GenerationRunReader:
    """Exact-allowlist, metadata-only reader for the current R18 candidate run."""

    RUN_ID = "r18"
    VERIFICATION_PATH = (
        "staging/coordination/generation_publication/"
        "R18_DOUBLE_CLEAN_VERIFICATION.json"
    )
    CONTROLLED_PATHS = (
        ("sh-chem-db/kb/figures/machine_v2/assets/"
        "FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.png"),
        ("sh-chem-db/kb/figures/machine_v2/assets/"
        "FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.svg"),
        "sh-chem-db/kb/figures/machine_v2/component_registry_r18.json",
        ("sh-chem-db/kb/figures/machine_v2/"
        "FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.spec.json"),
        ("sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/"
        "deterministic_check_report.json"),
        ("sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/"
        "deterministic_check_request.json"),
        ("sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/"
        "subject_answer.json"),
        ("sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/"
        "subject_question.json"),
        "staging/coordination/generation_publication/prefreeze_receipt_r18.json",
        "staging/coordination/generation_publication/generator_execution_metadata_r18.json",
        "staging/v1_generation/candidates/r18/delivery_status.json",
        "staging/v1_generation/candidates/r18/frozen_paper.json",
        "staging/v1_generation/candidates/r18/generator_provenance_receipt.json",
        "staging/v1_generation/candidates/r18/r18_producer_receipt.json",
        "staging/v1_generation/candidates/r18/sol_generator_receipt.json",
        "staging/v1_generation/candidates/r18/task_card.json",
        "staging/v1_generation/candidates/r18/week_plan.json",
        "staging/v1_generation/reports/r18/components.json",
        "staging/v1_generation/reports/r18/conservation.json",
        "staging/v1_generation/reports/r18/controller_machine_pass.json",
        "staging/v1_generation/reports/r18/coverage_matrix.json",
        "staging/v1_generation/reports/r18/coverage_matrix_validation.json",
        "staging/v1_generation/reports/r18/cross_question_leakage.json",
        "staging/v1_generation/reports/r18/dedup.json",
        "staging/v1_generation/reports/r18/deterministic_atomic_scope.json",
        "staging/v1_generation/reports/r18/figure.json",
        "staging/v1_generation/reports/r18/figure_topology_mutations.json",
        ("staging/v1_generation/reports/r18/figure_visual_qa/"
        "FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.onebit-print.png"),
        ("staging/v1_generation/reports/r18/figure_visual_qa/"
        "FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.quarter-grayscale.png"),
        "staging/v1_generation/reports/r18/figure_visual_qa.json",
        "staging/v1_generation/reports/r18/inverse.json",
        ("staging/v1_generation/reports/r18/mutations/"
        "FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.disconnected-gas-path.png"),
        ("staging/v1_generation/reports/r18/mutations/"
        "FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.disconnected-gas-path.svg"),
        "staging/v1_generation/reports/r18/p32_display_parser.json",
        "staging/v1_generation/reports/r18/question_subject_isolation.json",
        ("staging/v1_generation/reports/r18/"
        "question_subject_semantic_mutations.json"),
        "staging/v1_generation/reports/r18/r13_invalidation_correction.json",
        ("staging/v1_generation/reports/r18/"
        "rubric_name_requirement_mutations.json"),
        "staging/v1_generation/reports/r18/schema.json",
        ("staging/v1_generation/reports/r18/"
        "student_visible_prompt_safety_mutations.json"),
        "staging/v1_generation/reports/r18/versioned_schema.json",
    )
    ALLOWED = (VERIFICATION_PATH, *CONTROLLED_PATHS)
    REQUIRED_QA_PATHS = (
        "staging/v1_generation/reports/r18/controller_machine_pass.json",
        "staging/v1_generation/reports/r18/schema.json",
        "staging/v1_generation/reports/r18/dedup.json",
        "staging/v1_generation/reports/r18/coverage_matrix_validation.json",
        "staging/v1_generation/reports/r18/figure_visual_qa.json",
    )
    PREFREEZE_PATH = (
        "staging/coordination/generation_publication/prefreeze_receipt_r18.json"
    )
    TASK_PATH = "staging/v1_generation/candidates/r18/task_card.json"
    PAPER_PATH = "staging/v1_generation/candidates/r18/frozen_paper.json"
    DELIVERY_PATH = "staging/v1_generation/candidates/r18/delivery_status.json"

    def __init__(self, shchem_root: Path):
        self.workspace = shchem_root.absolute().parent

    def _read_bytes(self, logical_path: str) -> tuple[bytes, str]:
        if logical_path not in self.ALLOWED:
            raise ReadOnlyDataError("generation_path_not_allowed", "generation path is not allowlisted", 403)
        path = _checked_exact_path(self.workspace, logical_path)
        data = path.read_bytes()
        return data, _sha256(data)

    def _read_json(self, logical_path: str) -> tuple[dict[str, Any], bytes, str]:
        data, digest = self._read_bytes(logical_path)
        return _json_bytes(data, logical_path), data, digest

    @staticmethod
    def _valid_binding(binding: dict[str, Any]) -> bool:
        digest = binding.get("sha256")
        size = binding.get("bytes")
        return (
            isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
            and isinstance(size, int)
            and size >= 0
        )

    def _verification(
        self,
    ) -> tuple[
        dict[str, Any],
        dict[str, dict[str, Any]],
        dict[str, Any],
        dict[str, dict[str, Any]],
    ]:
        report, _, _ = self._read_json(self.VERIFICATION_PATH)
        raw_items = report.get("controlled_files", [])
        if not isinstance(raw_items, list):
            raw_items = []
        logical_paths = [
            item.get("logical_path")
            for item in raw_items
            if isinstance(item, dict) and isinstance(item.get("logical_path"), str)
        ]
        duplicate_paths = sorted(
            {
                path
                for path in logical_paths
                if logical_paths.count(path) > 1
            }
        )
        actual_set = set(logical_paths)
        allowlist_set = set(self.CONTROLLED_PATHS)
        missing_paths = sorted(allowlist_set - actual_set)
        unexpected_paths = sorted(actual_set - allowlist_set)
        malformed_entry_count = len(raw_items) - len(logical_paths)
        path_set_exact = (
            not duplicate_paths
            and not missing_paths
            and not unexpected_paths
            and malformed_entry_count == 0
            and len(logical_paths) == len(self.CONTROLLED_PATHS)
        )
        expected: dict[str, dict[str, Any]] = {}
        for item in raw_items:
            if (
                isinstance(item, dict)
                and item.get("logical_path") in allowlist_set
                and logical_paths.count(str(item.get("logical_path"))) == 1
            ):
                expected[str(item["logical_path"])] = item
        claimed = report.get("self_hash")
        canonical = dict(report)
        canonical.pop("self_hash", None)
        computed = hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self_hash_verified = isinstance(claimed, str) and claimed == computed
        artifacts: dict[str, dict[str, Any]] = {}
        for logical_path in self.CONTROLLED_PATHS:
            binding = expected.get(logical_path)
            try:
                data, digest = self._read_bytes(logical_path)
                available = True
            except ReadOnlyDataError:
                data, digest, available = b"", None, False
            binding_valid = isinstance(binding, dict) and self._valid_binding(binding)
            verified = bool(
                available
                and binding_valid
                and binding.get("sha256") == digest
                and binding.get("bytes") == len(data)
            )
            artifacts[logical_path] = {
                "artifact_id": Path(logical_path).name,
                "logical_path": logical_path,
                "sha256": digest,
                "bytes": len(data) if available else None,
                "manifest_binding_present": binding is not None,
                "manifest_hash_verified": verified,
                "available": available,
                "download_available": False,
            }
        all_live_verified = path_set_exact and all(
            item["manifest_hash_verified"] for item in artifacts.values()
        )
        blockers: list[str] = []
        if not self_hash_verified:
            blockers.append("double_clean_self_hash_invalid")
        if not path_set_exact:
            blockers.append("controlled_file_allowlist_mismatch")
        if not all_live_verified:
            blockers.append("controlled_file_live_hash_verification_failed")
        summary = {
            "self_hash_verified": self_hash_verified,
            "controlled_path_set_exact": path_set_exact,
            "controlled_file_count_expected": len(self.CONTROLLED_PATHS),
            "controlled_file_count_observed": len(logical_paths),
            "missing_paths": missing_paths,
            "duplicate_paths": duplicate_paths,
            "unexpected_paths": unexpected_paths,
            "malformed_entry_count": malformed_entry_count,
            "all_controlled_files_live_verified": all_live_verified,
            "live_verified_count": sum(
                item["manifest_hash_verified"] for item in artifacts.values()
            ),
            "blockers": blockers,
        }
        return report, expected, summary, artifacts

    def list_runs(self) -> dict[str, Any]:
        current = self.current(include_artifacts=False)
        summary = {key: current[key] for key in ("run_id", "version_id", "paper_id", "stage", "human_review_pending", "release")}
        return {"items": [summary], "count": 1, "current_run_id": self.RUN_ID, "read_only": True}

    def current(self, *, include_artifacts: bool = True) -> dict[str, Any]:
        verification, _, integrity, artifact_map = self._verification()
        required_json_errors: list[str] = []

        def required_json(logical_path: str) -> tuple[dict[str, Any], str | None]:
            try:
                value, _, digest = self._read_json(logical_path)
                return value, digest
            except ReadOnlyDataError:
                required_json_errors.append(
                    f"required_generation_json_unavailable:{logical_path}"
                )
                return {}, None

        prefreeze, prefreeze_hash = required_json(self.PREFREEZE_PATH)
        task, task_hash = required_json(self.TASK_PATH)
        paper, paper_hash = required_json(self.PAPER_PATH)
        delivery, _ = required_json(self.DELIVERY_PATH)
        qa = []
        qa_pass = True
        for logical_path in self.REQUIRED_QA_PATHS:
            report, digest = required_json(logical_path)
            artifact = artifact_map[logical_path]
            verified = artifact["manifest_hash_verified"] and digest == artifact["sha256"]
            status = report.get("status", "unknown")
            errors = (
                list(report.get("errors", []))
                if isinstance(report.get("errors", []), list)
                else ["qa_errors_field_invalid"]
            )
            passed = str(status).casefold() == "pass" and verified and not errors
            qa_pass = qa_pass and passed
            qa.append(
                {
                    "check": report.get("check") or Path(logical_path).stem,
                    "status": status,
                    "errors": errors,
                    "sha256": digest,
                    "manifest_hash_verified": verified,
                    "required_pass": passed,
                }
            )
        verification_status_pass = str(verification.get("status", "")).casefold().startswith("pass")
        machine_pass = bool(
            integrity["self_hash_verified"]
            and integrity["controlled_path_set_exact"]
            and integrity["all_controlled_files_live_verified"]
            and verification_status_pass
            and qa_pass
            and not required_json_errors
        )
        integrity_blockers = list(integrity["blockers"])
        if not verification_status_pass:
            integrity_blockers.append("double_clean_status_not_pass")
        if not qa_pass:
            integrity_blockers.append("required_qa_not_all_pass")
        integrity_blockers.extend(required_json_errors)
        integrity["required_qa_all_pass"] = qa_pass
        integrity["verification_status_pass"] = verification_status_pass
        integrity["machine_pass_gate"] = machine_pass
        integrity["blockers"] = list(dict.fromkeys(integrity_blockers))
        themes = paper.get("themes", []) if isinstance(paper.get("themes"), list) else []
        task_summary = {
            key: task.get(key)
            for key in (
                "version_id",
                "paper_id",
                "target",
                "duration_minutes",
                "total_score",
                "theme_count",
                "theme_scores",
                "numbering_mode",
                "selection_rule",
                "standalone_choice_section",
                "grade",
                "teaching_stage",
                "purpose",
            )
        }
        paper_summary = {
            "schema_version": paper.get("schema_version"),
            "version_id": paper.get("version_id"),
            "paper_id": paper.get("paper_id"),
            "content_status": paper.get("content_status"),
            "claim_boundary": paper.get("claim_boundary"),
            "answer_authority": paper.get("answer_authority"),
            "rubric_authority": paper.get("rubric_authority"),
            "theme_count": len(themes),
            "theme_ids": [item.get("theme_id") for item in themes if isinstance(item, dict)],
            "human_reviewed": paper.get("human_reviewed") is True,
            "publication_allowed": False,
        }
        artifacts = (
            [artifact_map[path] for path in self.CONTROLLED_PATHS]
            if include_artifacts
            else []
        )
        timeline = [
            {"stage": "candidate", "label": "候选已生成", "status": "complete", "authority_change": False},
            {"stage": "machine_pass", "label": "机器结构与 QA 通过", "status": "complete" if machine_pass else "blocked", "authority_change": False},
            {"stage": "human_review_pending", "label": "人工复核待完成", "status": "pending", "authority_change": False},
            {"stage": "release", "label": "外部发布", "status": "blocked", "authority_change": False},
        ]
        return {
            "run_id": self.RUN_ID,
            "version_id": prefreeze.get("version_id"),
            "paper_id": paper.get("paper_id"),
            "stage": "machine_pass" if machine_pass else "candidate",
            "task_card": task_summary,
            "paper": paper_summary,
            "qa": qa,
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "gate_timeline": timeline,
            "integrity": {
                "double_clean_self_hash_verified": integrity["self_hash_verified"],
                "controlled_path_set_exact": integrity[
                    "controlled_path_set_exact"
                ],
                "all_controlled_files_live_verified": integrity[
                    "all_controlled_files_live_verified"
                ],
                "artifact_manifest_hashes_verified": integrity[
                    "all_controlled_files_live_verified"
                ],
                "required_qa_all_pass": integrity["required_qa_all_pass"],
                "machine_pass_gate": machine_pass,
                "missing_paths": integrity["missing_paths"],
                "duplicate_paths": integrity["duplicate_paths"],
                "unexpected_paths": integrity["unexpected_paths"],
                "controlled_file_count_expected": integrity[
                    "controlled_file_count_expected"
                ],
                "controlled_file_count_observed": integrity[
                    "controlled_file_count_observed"
                ],
                "live_verified_count": integrity["live_verified_count"],
                "blockers": integrity["blockers"],
                "prefreeze_receipt_sha256": prefreeze_hash,
                "task_card_sha256": task_hash,
                "paper_sha256": paper_hash,
                "double_clean_status": verification.get("status"),
            },
            "delivery": {
                "content_status": delivery.get("content_status"),
                "teacher_managed_delivery_candidate": delivery.get("teacher_managed_delivery_candidate") is True,
                "external_publication_allowed": False,
                "official_claim_allowed": False,
                "blockers": list(
                    dict.fromkeys(
                        [
                            *integrity["blockers"],
                            *(
                                list(delivery.get("next_required", []))
                                if isinstance(delivery.get("next_required", []), list)
                                else ["external_release_interface_closed"]
                            ),
                        ]
                    )
                ),
            },
            "machine_pass": machine_pass,
            "human_review_pending": True,
            "human_reviewed": False,
            "release": False,
            "publication_allowed": False,
            "read_only": True,
            "authority_changing_actions": [],
        }

    def artifacts(self, run_id: str) -> dict[str, Any]:
        validate_identifier(run_id, "run_id")
        if run_id != self.RUN_ID:
            raise ReadOnlyDataError("generation_run_not_found", "generation run not found", 404)
        current = self.current(include_artifacts=True)
        return {
            "run_id": run_id,
            "items": current["artifacts"],
            "count": current["artifact_count"],
            "integrity": current["integrity"],
            "download_available": False,
            "read_only": True,
            "release": False,
        }

    def get(self, run_id: str) -> dict[str, Any]:
        validate_identifier(run_id, "run_id")
        if run_id != self.RUN_ID:
            raise ReadOnlyDataError("generation_run_not_found", "generation run not found", 404)
        return self.current()


def pagination(
    query: dict[str, list[str]], *, max_limit: int = 100
) -> tuple[int, int]:
    if type(max_limit) is not int or not 1 <= max_limit <= 1000:
        raise SecurityError("invalid_pagination", "pagination maximum is invalid")
    try:
        limit = min(max(int((query.get("limit") or ["50"])[0]), 1), max_limit)
        offset = min(max(int((query.get("offset") or ["0"])[0]), 0), 10000)
    except (TypeError, ValueError) as exc:
        raise SecurityError("invalid_pagination", "limit and offset must be integers") from exc
    return limit, offset

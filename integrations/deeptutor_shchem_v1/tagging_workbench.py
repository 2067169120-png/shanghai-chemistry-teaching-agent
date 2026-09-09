"""Teacher-controlled gateway for candidate-only atomic-part tag patches.

The browser never selects a filesystem path, a store root, a base manifest, or
an authority flag.  This adapter reads the existing :class:`PublicKBReader`
exact allowlist, derives immutable control snapshots, and then delegates the
append-only transaction to ``shchem_tagging_workbench_v1``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from integrations.shchem_tagging_workbench_v1 import (
    AppendOnlyTagPatchStore,
    ContractError,
    IntegrityError,
    StoreConflictError,
    canonical_json_bytes,
    validate_patch_request,
)
from integrations.shchem_tagging_workbench_v1.contracts import (
    parse_json_object,
    validate_identifier,
)

from .public_kb import PublicKBReader, ReadOnlyDataError

TAG_PATCH_WRITE_CAPABILITY = "tag_patch_candidate_write"
PRODUCTION_STORE_RELATIVE = Path(
    "staging/coordination/tagging_workbench/candidate_store_v1"
)

_AUTHORITY_FLAGS = {
    "candidate_only": True,
    "human_reviewed": False,
    "retrieval_ready": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "official": False,
    "teaching_use_allowed": False,
}
_BROWSER_REQUEST_FIELDS = frozenset(
    {
        "node_type",
        "node_id",
        "factor_updates",
        "reason",
        "expected_current_public_node_identity",
    }
)
_PUBLIC_IDENTITY_FIELDS = frozenset(
    {
        "node_type",
        "node_id",
        "paper_id",
        "public_manifest_sha256",
        "public_atomic_layer_sha256",
        "public_record_sha256",
        "public_record_size_bytes",
    }
)
_RP_AXIS = tuple(f"RP{index:02d}" for index in range(1, 13))
_PRIVATE_MARKERS = (
    "private_state",
    "private_profiles",
    "06_学生错题档案",
    "95-source",
    "state_root",
    "workspace_root",
)
_HOST_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|(?:file|vscode)://|\\\\[^\\]+\\[^\\]+)",
    re.IGNORECASE,
)


class TagPatchGatewayError(RuntimeError):
    """Stable API-facing failure for the candidate tagging slice."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise TagPatchGatewayError(
            "invalid_tag_patch_contract", f"{label} must be an object", 400
        )
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise TagPatchGatewayError(
            "invalid_tag_patch_contract",
            f"{label} exact-key violation; missing={missing}, extra={extra}",
            400,
        )
    return value


def _sensitive_string(value: str) -> bool:
    lowered = value.casefold()
    return bool(_HOST_PATH_RE.search(value)) or any(
        marker.casefold() in lowered for marker in _PRIVATE_MARKERS
    )


def _safe_public_value(value: Any) -> Any:
    """Recursively redact host/private strings in already allowlisted output."""

    if isinstance(value, dict):
        return {str(key): _safe_public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_public_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_public_value(item) for item in value]
    if isinstance(value, str) and _sensitive_string(value):
        return "[redacted_host_or_private_text]"
    return value


def _candidate_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, dict):
        explicit = value.get("value")
        if isinstance(explicit, str):
            return [explicit]
        for key in ("values", "candidate_values", "candidate_labels"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, str)]
        declared = value.get("declared_prelabel")
        if isinstance(declared, str):
            return [declared]
    return []


def _taxonomy_ids(dimensions: Mapping[str, Any], key: str) -> list[str]:
    entries = dimensions.get(key)
    if not isinstance(entries, list):
        raise TagPatchGatewayError(
            "public_taxonomy_incomplete",
            f"public taxonomy dimension {key} is unavailable",
            503,
        )
    output: list[str] = []
    for entry in entries:
        item_id = entry.get("id") if isinstance(entry, dict) else entry
        if isinstance(item_id, str):
            output.append(item_id)
    if not output:
        raise TagPatchGatewayError(
            "public_taxonomy_incomplete",
            f"public taxonomy dimension {key} has no controlled IDs",
            503,
        )
    return output


class TagPatchGateway:
    """Derive public bindings and expose only create/list/get candidate actions."""

    def __init__(
        self,
        public_kb: PublicKBReader,
        workspace_root: Path,
        *,
        store: AppendOnlyTagPatchStore | None = None,
        store_factory: Callable[[], AppendOnlyTagPatchStore] | None = None,
    ):
        self.public_kb = public_kb
        self.workspace_root = workspace_root.absolute()
        self._store = store
        self._store_factory = store_factory or (
            lambda: AppendOnlyTagPatchStore(self.workspace_root)
        )

    @property
    def production_store_path(self) -> Path:
        return self.workspace_root / PRODUCTION_STORE_RELATIVE

    def _store_if_present(self) -> AppendOnlyTagPatchStore | None:
        if self._store is not None:
            return self._store
        if not self.production_store_path.exists():
            return None
        self._store = self._store_factory()
        return self._store

    def _store_for_write(self) -> AppendOnlyTagPatchStore:
        if self._store is None:
            self._store = self._store_factory()
        return self._store

    def _public_bytes(
        self, relative: str, *, verify_manifest: bool = False
    ) -> tuple[bytes, str]:
        # The relative path always comes from PublicKBReader constants; no
        # caller-controlled path crosses this boundary.
        return self.public_kb._read_exact(relative, verify_manifest=verify_manifest)

    @staticmethod
    def _atomic_rows(raw: bytes) -> list[tuple[dict[str, Any], bytes]]:
        rows: list[tuple[dict[str, Any], bytes]] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = parse_json_object(line)
            except ContractError as exc:
                raise TagPatchGatewayError(
                    "public_atomic_index_invalid",
                    f"public atomic index row {line_number} is invalid",
                    503,
                ) from exc
            rows.append((row, line))
        return rows

    @staticmethod
    def _target_raw(
        row: dict[str, Any],
        raw_record: bytes,
        *,
        manifest_sha256: str,
        atomic_layer_sha256: str,
    ) -> bytes:
        node_id = row.get("atomic_part_id")
        paper_id = row.get("parent_paper_id")
        return canonical_json_bytes(
            {
                "node_type": "atomic_part",
                "node_id": node_id,
                "paper_id": paper_id,
                "public_manifest_sha256": manifest_sha256,
                "public_atomic_layer_sha256": atomic_layer_sha256,
                "public_record_sha256": _sha256(raw_record),
                "public_record_size_bytes": len(raw_record),
                "record": row,
            }
        )

    def _taxonomy_raw(self) -> tuple[bytes, str]:
        source_raw, source_sha256 = self._public_bytes(self.public_kb.TAXONOMY)
        try:
            taxonomy = json.loads(source_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TagPatchGatewayError(
                "public_taxonomy_invalid",
                "public taxonomy is not valid UTF-8 JSON",
                503,
            ) from exc
        dimensions = taxonomy.get("dimensions") if isinstance(taxonomy, dict) else None
        if not isinstance(dimensions, dict):
            raise TagPatchGatewayError(
                "public_taxonomy_incomplete",
                "public taxonomy dimensions are missing",
                503,
            )
        snapshot = {
            "schema_version": "shchem_gateway_tag_taxonomy_snapshot_v1",
            "source_taxonomy_schema_version": taxonomy.get("schema_version"),
            "source_taxonomy_sha256": source_sha256,
            "source_taxonomy_size_bytes": len(source_raw),
            "representation_axis_source": "gateway_closed_candidate_vocabulary_RP01_RP12_v1",
            "axes": {
                "K": _taxonomy_ids(dimensions, "knowledge_points"),
                "A": _taxonomy_ids(dimensions, "abilities"),
                "C": _taxonomy_ids(dimensions, "contexts"),
                "R": _taxonomy_ids(dimensions, "response_types"),
                "RP": list(_RP_AXIS),
                "D": _taxonomy_ids(dimensions, "difficulty"),
            },
        }
        return canonical_json_bytes(snapshot), source_sha256

    @staticmethod
    def _current_values(row: dict[str, Any]) -> dict[str, Any]:
        difficulty = row.get("difficulty")
        difficulty_labels = _candidate_values(difficulty)
        item_types = _candidate_values(row.get("item_type"))
        if not item_types and isinstance(row.get("core_item_type"), str):
            item_types = [row["core_item_type"]]
        primary = _candidate_values(row.get("primary_knowledge_K"))
        supporting = _candidate_values(row.get("supporting_knowledge_K"))
        response = _candidate_values(row.get("response_R_evidence"))
        if not response:
            response = _candidate_values(row.get("response_R"))
        representation = _candidate_values(row.get("representation_RP_evidence"))
        if not representation:
            representation = _candidate_values(row.get("representation_RP"))
        return {
            "item_type": item_types[0] if item_types else None,
            "selection_rule": row.get("selection_rule")
            if isinstance(row.get("selection_rule"), str)
            else None,
            "primary_knowledge_K": primary[0] if primary else None,
            "supporting_knowledge_K": supporting,
            "ability_A": _candidate_values(row.get("ability_A")),
            "context_C": _candidate_values(row.get("context_C")),
            "response_R": response[0] if len(response) == 1 else response,
            "representation_RP": representation,
            "cognitive_prelabel": difficulty_labels[0]
            if difficulty_labels and difficulty_labels[0].startswith("D")
            else None,
            "difficulty_factors": None,
        }

    def _context(self, node_type: Any, node_id: Any) -> dict[str, Any]:
        if node_type != "atomic_part":
            raise TagPatchGatewayError(
                "invalid_tag_patch_contract",
                "only node_type=atomic_part is patchable",
                400,
            )
        try:
            safe_node_id = validate_identifier(node_id, "node_id")
            manifest_raw, manifest_sha256 = self._public_bytes(self.public_kb.MANIFEST)
            atomic_raw, atomic_layer_sha256 = self._public_bytes(
                self.public_kb.LAYER_FILES["atomic_part"], verify_manifest=True
            )
            rows = self._atomic_rows(atomic_raw)
        except ReadOnlyDataError as exc:
            raise TagPatchGatewayError(exc.code, str(exc), exc.status) from exc
        except ContractError as exc:
            raise TagPatchGatewayError(
                "invalid_tag_patch_contract", str(exc), 400
            ) from exc

        target: tuple[dict[str, Any], bytes] | None = None
        seen_ids: set[str] = set()
        for row, raw_record in rows:
            row_id = row.get("atomic_part_id")
            if not isinstance(row_id, str):
                continue
            if row_id in seen_ids:
                raise TagPatchGatewayError(
                    "public_atomic_index_invalid",
                    "public atomic index contains duplicate node IDs",
                    503,
                )
            seen_ids.add(row_id)
            if row_id == safe_node_id:
                target = (row, raw_record)
        if target is None:
            raise TagPatchGatewayError(
                "tag_patch_node_not_found", "public atomic-part node was not found", 404
            )

        target_row, target_record_raw = target
        paper_id = target_row.get("parent_paper_id")
        try:
            safe_paper_id = validate_identifier(paper_id, "paper_id")
        except ContractError as exc:
            raise TagPatchGatewayError(
                "tag_patch_parent_binding_unavailable",
                "atomic-part paper binding is unavailable or invalid",
                409,
            ) from exc

        target_raw = self._target_raw(
            target_row,
            target_record_raw,
            manifest_sha256=manifest_sha256,
            atomic_layer_sha256=atomic_layer_sha256,
        )
        members: list[dict[str, Any]] = []
        for row, raw_record in rows:
            if row.get("parent_paper_id") != safe_paper_id:
                continue
            record_id = row.get("atomic_part_id")
            try:
                safe_record_id = validate_identifier(record_id, "atomic record ID")
            except ContractError as exc:
                raise TagPatchGatewayError(
                    "public_atomic_index_invalid",
                    "public atomic index contains an invalid record identity",
                    503,
                ) from exc
            derived = self._target_raw(
                row,
                raw_record,
                manifest_sha256=manifest_sha256,
                atomic_layer_sha256=atomic_layer_sha256,
            )
            members.append(
                {
                    "paper_id": safe_paper_id,
                    "node_kind": "atomic_part",
                    "record_id": safe_record_id,
                    "record_sha256": _sha256(derived),
                    "record_size_bytes": len(derived),
                }
            )
        members.sort(key=lambda item: item["record_id"])
        manifest_id_seed = canonical_json_bytes(
            {
                "public_manifest_sha256": manifest_sha256,
                "public_manifest_size_bytes": len(manifest_raw),
                "public_atomic_layer_sha256": atomic_layer_sha256,
                "public_atomic_layer_size_bytes": len(atomic_raw),
                "paper_id": safe_paper_id,
            }
        )
        base_raw = canonical_json_bytes(
            {
                "schema_version": "shchem_atomic_part_index_manifest_v1",
                "manifest_id": f"ATOMIC-{_sha256(manifest_id_seed)}",
                "paper_id": safe_paper_id,
                "node_kind": "atomic_part",
                "records": members,
            }
        )
        taxonomy_raw, source_taxonomy_sha256 = self._taxonomy_raw()
        identity = {
            "node_type": "atomic_part",
            "node_id": safe_node_id,
            "paper_id": safe_paper_id,
            "public_manifest_sha256": manifest_sha256,
            "public_atomic_layer_sha256": atomic_layer_sha256,
            "public_record_sha256": _sha256(target_record_raw),
            "public_record_size_bytes": len(target_record_raw),
        }
        return {
            "identity": identity,
            "base_raw": base_raw,
            "target_raw": target_raw,
            "taxonomy_raw": taxonomy_raw,
            "manifest_evidence_sha256": manifest_sha256,
            "source_taxonomy_sha256": source_taxonomy_sha256,
            "current_values": self._current_values(target_row),
        }

    def current_node_context(self, node_type: str, node_id: str) -> dict[str, Any]:
        context = self._context(node_type, node_id)
        return _safe_public_value(
            {
                "patchable": True,
                "expected_current_public_node_identity": context["identity"],
                "current_factor_values": context["current_values"],
                "candidate_status": "pending_human_review_not_applied",
                "write_capability_required": TAG_PATCH_WRITE_CAPABILITY,
                **_AUTHORITY_FLAGS,
            }
        )

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _exact_keys(payload, _BROWSER_REQUEST_FIELDS, "tag_patch_request")
        identity = _exact_keys(
            request["expected_current_public_node_identity"],
            _PUBLIC_IDENTITY_FIELDS,
            "expected_current_public_node_identity",
        )
        context = self._context(request["node_type"], request["node_id"])
        if identity != context["identity"]:
            raise TagPatchGatewayError(
                "stale_public_node_identity",
                "current public node identity changed; reload before creating a candidate",
                409,
            )
        factor_updates = request["factor_updates"]
        if type(factor_updates) is not dict:
            raise TagPatchGatewayError(
                "invalid_tag_patch_contract", "factor_updates must be an object", 400
            )
        core_request = {
            "node_type": "atomic_part",
            "node_id": context["identity"]["node_id"],
            "paper_id": context["identity"]["paper_id"],
            "base_index_manifest_sha256": _sha256(context["base_raw"]),
            "target_record_sha256": _sha256(context["target_raw"]),
            "taxonomy_sha256": _sha256(context["taxonomy_raw"]),
            "changes": copy.deepcopy(factor_updates),
            # Browser reasons are free text, so strip host/private markers
            # before the append-only snapshot is written, not only when the
            # API projection is returned.
            "reason": _safe_public_value(request["reason"]),
            "evidence_binding_ids": ["PUBLIC-KB-MANIFEST"],
        }
        try:
            # Validate before lazily creating the production store.  Rejected
            # browser contracts therefore leave no production-state directory.
            validate_patch_request(
                core_request,
                base_index_manifest_raw=context["base_raw"],
                target_record_raw=context["target_raw"],
                taxonomy_raw=context["taxonomy_raw"],
            )
            created = self._store_for_write().create_candidate_patch(
                core_request,
                base_index_manifest_raw=context["base_raw"],
                target_record_raw=context["target_raw"],
                taxonomy_raw=context["taxonomy_raw"],
                evidence_hash_bindings={
                    "PUBLIC-KB-MANIFEST": context["manifest_evidence_sha256"]
                },
            )
        except StoreConflictError as exc:
            raise TagPatchGatewayError("tag_patch_conflict", str(exc), 409) from exc
        except ContractError as exc:
            raise TagPatchGatewayError(
                "invalid_tag_patch_contract", str(exc), 400
            ) from exc
        except IntegrityError as exc:
            raise TagPatchGatewayError(
                "tag_patch_integrity_failure",
                "candidate store integrity check failed",
                409,
            ) from exc
        return self._detail(created, context=context)

    @staticmethod
    def _diff(current: dict[str, Any], updates: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "field": field,
                "before": copy.deepcopy(current.get(field)),
                "after": copy.deepcopy(updates[field]),
            }
            for field in sorted(updates)
        ]

    def _detail(
        self, verified: dict[str, Any], *, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        record = verified["record"]
        if context is None:
            try:
                context = self._context(record["node_type"], record["node_id"])
            except TagPatchGatewayError:
                context = None
        current_identity = context["identity"] if context else None
        binding_current = bool(
            current_identity
            and record.get("target_record_sha256") == _sha256(context["target_raw"])
        )
        current_values = context["current_values"] if binding_current else {}
        output = {
            "schema_version": "shchem_gateway_tag_patch_candidate_v1",
            "patch_id": verified["patch_id"],
            "node_type": record["node_type"],
            "node_id": record["node_id"],
            "paper_id": record["paper_id"],
            "created_at_utc": record["created_at_utc"],
            "candidate_status": "pending_human_review_not_applied",
            "changed_fields": sorted(record["changes"]),
            "factor_updates": copy.deepcopy(record["changes"]),
            "reason": record["reason"],
            "diff": self._diff(current_values, record["changes"]),
            "binding": {
                "current_public_identity_matches": binding_current,
                "base_index_manifest_sha256": record["base_index_manifest_sha256"],
                "target_record_sha256": record["target_record_sha256"],
                "taxonomy_sha256": record["taxonomy_sha256"],
                "candidate_file_sha256": verified["file_sha256"],
            },
            "actions": {
                "apply_available": False,
                "human_approve_available": False,
                "retrieval_activation_available": False,
                "generation_activation_available": False,
                "publication_available": False,
                "official_claim_available": False,
            },
            **_AUTHORITY_FLAGS,
        }
        return _safe_public_value(output)

    def list(
        self,
        *,
        node_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 1 <= limit <= 200
            or offset < 0
        ):
            raise TagPatchGatewayError(
                "invalid_tag_patch_pagination", "invalid limit or offset", 400
            )
        if node_id is not None:
            try:
                node_id = validate_identifier(node_id, "node_id")
            except ContractError as exc:
                raise TagPatchGatewayError(
                    "invalid_tag_patch_contract", str(exc), 400
                ) from exc
        store = self._store_if_present()
        try:
            rows = [] if store is None else store.list_patches()
        except (ContractError, IntegrityError) as exc:
            raise TagPatchGatewayError(
                "tag_patch_integrity_failure",
                "candidate store integrity check failed",
                409,
            ) from exc
        if node_id is not None:
            rows = [row for row in rows if row.get("node_id") == node_id]
        total = len(rows)
        items = [
            {
                "patch_id": row["patch_id"],
                "node_type": row["node_type"],
                "node_id": row["node_id"],
                "created_at_utc": row["created_at_utc"],
                "changed_fields": list(row["changed_fields"]),
                "candidate_status": "pending_human_review_not_applied",
                **_AUTHORITY_FLAGS,
            }
            for row in rows[offset : offset + limit]
        ]
        return _safe_public_value(
            {
                "schema_version": "shchem_gateway_tag_patch_list_v1",
                "items": items,
                "count": len(items),
                "total": total,
                "limit": limit,
                "offset": offset,
                "filters": {"node_type": "atomic_part", "node_id": node_id},
                "candidate_status": "pending_human_review_not_applied",
                **_AUTHORITY_FLAGS,
            }
        )

    def get(self, patch_id: str) -> dict[str, Any]:
        try:
            safe_patch_id = validate_identifier(patch_id, "patch_id")
        except ContractError as exc:
            raise TagPatchGatewayError(
                "invalid_tag_patch_contract", str(exc), 400
            ) from exc
        store = self._store_if_present()
        if store is None:
            raise TagPatchGatewayError(
                "tag_patch_not_found", "candidate tag patch was not found", 404
            )
        try:
            known = {row["patch_id"] for row in store.list_patches()}
            if safe_patch_id not in known:
                raise TagPatchGatewayError(
                    "tag_patch_not_found", "candidate tag patch was not found", 404
                )
            return self._detail(store.get_patch(safe_patch_id))
        except TagPatchGatewayError:
            raise
        except ContractError as exc:
            raise TagPatchGatewayError(
                "invalid_tag_patch_contract", str(exc), 400
            ) from exc
        except IntegrityError as exc:
            raise TagPatchGatewayError(
                "tag_patch_integrity_failure",
                "candidate store integrity check failed",
                409,
            ) from exc


__all__ = [
    "PRODUCTION_STORE_RELATIVE",
    "TAG_PATCH_WRITE_CAPABILITY",
    "TagPatchGateway",
    "TagPatchGatewayError",
]

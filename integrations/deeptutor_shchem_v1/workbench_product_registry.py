from __future__ import annotations

"""Atomic release snapshot for the three teacher-facing question browsers.

The registry is intentionally non-additive: ``wave1`` refines identities from
``master`` and ``supplemental`` is an isolated candidate scope.  A snapshot is
built once per service lifetime.  Either every registered scope, both overlay
manifests, and all three static files validate, or nothing is activated.
"""

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .config import CONTRACT_VERSION
from .supplemental_visual_scan import SupplementalVisualScanReader
from .theme_workbench import ThemeWorkbenchReader


REGISTRY_SCHEMA_VERSION = "1.0.0-workbench-product-registry"
READINESS_SCHEMA_VERSION = "1.0.0-workbench-readiness"
REGISTRY_ID = "SHCHEM-WORKBENCH-PRODUCT-REGISTRY-V1"
DATA_SNAPSHOT_ALGORITHM = "canonical-sha256-of-workbench-product-projections-v1"
PRODUCT_ORDER = ("wave1", "master", "supplemental")
THEME_SCHEMA_VERSION = "1.0.0-theme-workbench-candidate"

REGISTRY_RELATIVE = Path(
    "kb/workbench/workbench_product_registry_v1/registry.json"
)
REGISTRY_SCHEMA_RELATIVE = Path(
    "kb/workbench/workbench_product_registry_v1/registry.schema.json"
)
REGISTRY_FILE_SHA256 = (
    "87ab4d675bc9709717eb3fd9507bb86902cdc90fe1be53b13854aba27e2f9b69"
)
REGISTRY_SCHEMA_SHA256 = (
    "22d2d05409d99e077baf4b964a660ebcefc11a3ee8b391e92b2d97aea627d773"
)
REGISTRY_SELF_SHA256 = (
    "8d0b96efd50fac4c882a2dbf09c9a822b4788338e7cf59432a0e8cc4e9087667"
)

_STATIC_FILES = ("app.js", "index.html", "styles.css")
_AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "official": False,
    "retrieval_ready": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
}
_INTEGRITY = {
    "overlay_manifest_hash_verified": True,
    "ui_file_hashes_verified": True,
    "product_manifests_verified": True,
    "product_counts_derived": True,
    "stable_product_order": True,
    "unknown_products_rejected": True,
    "duplicate_products_rejected": True,
    "incompatible_products_rejected": True,
    "question_content_exposed": False,
    "source_paths_exposed": False,
    "answer_text_exposed": False,
    "fail_closed": True,
}
_EXPECTED_SEMANTICS = {
    "wave1": "candidate_refinement_view",
    "master": "canonical_master_inventory",
    "supplemental": "isolated_supplemental_candidate_scope",
}


class WorkbenchProductRegistryError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return _sha256(_canonical_bytes(value))


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise WorkbenchProductRegistryError(
                    "workbench_registry_json_invalid",
                    f"{label} contains a duplicate key",
                )
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique_object)
    except WorkbenchProductRegistryError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkbenchProductRegistryError(
            "workbench_registry_json_invalid", f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise WorkbenchProductRegistryError(
            "workbench_registry_json_invalid", f"{label} must be a JSON object"
        )
    return value


def _read_regular_file(path: Path, label: str) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        stat = path.stat()
    except OSError as exc:
        raise WorkbenchProductRegistryError(
            "workbench_registry_file_unavailable", f"{label} is unavailable"
        ) from exc
    if resolved != path.absolute() or path.is_symlink() or not path.is_file():
        raise WorkbenchProductRegistryError(
            "workbench_registry_file_unsafe", f"{label} is not a regular pinned file"
        )
    # Frozen release inputs must not be mutable through a second hard-link name.
    if getattr(stat, "st_nlink", 1) != 1:
        raise WorkbenchProductRegistryError(
            "workbench_registry_file_unsafe", f"{label} has multiple hard links"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise WorkbenchProductRegistryError(
            "workbench_registry_file_unavailable", f"{label} could not be read"
        ) from exc


class WorkbenchProductRegistryReader:
    """Build and hold one all-or-nothing workbench release snapshot."""

    def __init__(
        self,
        shchem_root: Path,
        overlay_root: Path,
        *,
        theme_workbench: ThemeWorkbenchReader | None = None,
        supplemental_visual_scans: SupplementalVisualScanReader | None = None,
    ):
        self.shchem_root = shchem_root.absolute()
        self.overlay_root = overlay_root.absolute()
        self.theme_workbench = theme_workbench or ThemeWorkbenchReader(
            self.shchem_root
        )
        self.supplemental_visual_scans = (
            supplemental_visual_scans
            or SupplementalVisualScanReader(self.shchem_root)
        )
        self._activation_lock = threading.Lock()
        self._registry_bytes: bytes | None = None
        self._readiness_bytes: bytes | None = None
        self._theme_bytes: dict[str, bytes] = {}
        self._terminal_error: WorkbenchProductRegistryError | None = None

    def _registry_document(self) -> dict[str, Any]:
        schema_path = self.shchem_root / REGISTRY_SCHEMA_RELATIVE
        registry_path = self.shchem_root / REGISTRY_RELATIVE
        schema_raw = _read_regular_file(schema_path, "workbench registry schema")
        registry_raw = _read_regular_file(registry_path, "workbench registry")
        if _sha256(schema_raw) != REGISTRY_SCHEMA_SHA256:
            raise WorkbenchProductRegistryError(
                "workbench_registry_schema_hash_mismatch",
                "workbench registry schema hash drifted",
            )
        if _sha256(registry_raw) != REGISTRY_FILE_SHA256:
            raise WorkbenchProductRegistryError(
                "workbench_registry_hash_mismatch",
                "workbench registry file hash drifted",
            )
        schema = _strict_json(schema_raw, "workbench registry schema")
        registry = _strict_json(registry_raw, "workbench registry")
        try:
            Draft202012Validator.check_schema(schema)
            errors = sorted(
                Draft202012Validator(schema).iter_errors(registry),
                key=lambda error: tuple(str(value) for value in error.absolute_path),
            )
        except Exception as exc:
            raise WorkbenchProductRegistryError(
                "workbench_registry_schema_invalid",
                "workbench registry schema could not be evaluated",
            ) from exc
        if errors:
            raise WorkbenchProductRegistryError(
                "workbench_registry_document_invalid",
                "workbench registry does not satisfy its pinned schema",
            )
        self_projection = deepcopy(registry)
        observed_self = self_projection.pop("self_sha256", None)
        if (
            observed_self != REGISTRY_SELF_SHA256
            or _canonical_sha256(self_projection) != REGISTRY_SELF_SHA256
        ):
            raise WorkbenchProductRegistryError(
                "workbench_registry_self_hash_mismatch",
                "workbench registry self hash drifted",
            )
        return registry

    def _product_descriptors(self) -> tuple[dict[str, Any], ...]:
        registry = self._registry_document()
        products = registry.get("products")
        if not isinstance(products, list):
            raise WorkbenchProductRegistryError(
                "workbench_registry_document_invalid",
                "workbench registry products are unavailable",
            )
        return tuple(deepcopy(products))

    @staticmethod
    def _validate_descriptors(
        descriptors: tuple[dict[str, Any], ...]
    ) -> tuple[dict[str, Any], ...]:
        if any(not isinstance(value, dict) for value in descriptors):
            raise WorkbenchProductRegistryError(
                "workbench_registry_unknown_product",
                "workbench registry contains an invalid product",
            )
        ids = [value.get("product_id") for value in descriptors]
        scopes = [value.get("scope") for value in descriptors]
        if len(ids) != len(set(ids)) or len(scopes) != len(set(scopes)):
            raise WorkbenchProductRegistryError(
                "workbench_registry_duplicate_product",
                "workbench registry contains a duplicate product or scope",
            )
        if set(ids) != set(PRODUCT_ORDER) or set(scopes) != set(PRODUCT_ORDER):
            raise WorkbenchProductRegistryError(
                "workbench_registry_unknown_product",
                "workbench registry contains an unknown or missing product",
            )
        by_scope = {str(value["scope"]): value for value in descriptors}
        ordered = tuple(by_scope[scope] for scope in PRODUCT_ORDER)
        for descriptor, scope in zip(ordered, PRODUCT_ORDER, strict=True):
            if descriptor.get("product_id") != scope:
                raise WorkbenchProductRegistryError(
                    "workbench_registry_unknown_product",
                    "workbench product identity and scope differ",
                )
            if descriptor.get("api_contract_version") != CONTRACT_VERSION:
                raise WorkbenchProductRegistryError(
                    "workbench_registry_contract_incompatible",
                    "workbench product API contract is incompatible",
                )
            if descriptor.get("schema_version") != THEME_SCHEMA_VERSION:
                raise WorkbenchProductRegistryError(
                    "workbench_registry_schema_incompatible",
                    "workbench product theme schema is incompatible",
                )
            if (
                descriptor.get("status") != "active_candidate_browse"
                or descriptor.get("inventory_semantics")
                != _EXPECTED_SEMANTICS[scope]
                or descriptor.get("non_additive_to")
                != [value for value in PRODUCT_ORDER if value != scope]
            ):
                raise WorkbenchProductRegistryError(
                    "workbench_registry_product_invalid",
                    "workbench product activation boundary is invalid",
                )
        return ordered

    def _ui_identity(self) -> tuple[str, str]:
        inner_path = self.overlay_root / "overlay.manifest.json"
        outer_path = self.overlay_root.parent / "overlay.manifest.json"
        manifests: list[tuple[dict[str, Any], bytes]] = []
        for path, label in (
            (inner_path, "runtime overlay manifest"),
            (outer_path, "distribution overlay manifest"),
        ):
            raw = _read_regular_file(path, label)
            manifest = _strict_json(raw, label)
            if manifest.get("gateway_contract") != CONTRACT_VERSION:
                raise WorkbenchProductRegistryError(
                    "workbench_registry_contract_incompatible",
                    "overlay API contract is incompatible",
                )
            files = manifest.get("files")
            if not isinstance(files, dict) or set(files) != set(_STATIC_FILES):
                raise WorkbenchProductRegistryError(
                    "workbench_registry_ui_manifest_invalid",
                    "overlay static-file inventory is invalid",
                )
            binding = manifest.get("workbench_product_registry")
            if not isinstance(binding, dict) or binding != {
                "registry_id": REGISTRY_ID,
                "registry_schema_version": REGISTRY_SCHEMA_VERSION,
                "registry_file_sha256": REGISTRY_FILE_SHA256,
                "registry_schema_sha256": REGISTRY_SCHEMA_SHA256,
                "dynamic_counts": True,
                "cross_scope_sum_allowed": False,
            }:
                raise WorkbenchProductRegistryError(
                    "workbench_registry_ui_manifest_invalid",
                    "overlay does not bind the activated workbench registry",
                )
            manifests.append((manifest, raw))
        inner, inner_raw = manifests[0]
        outer, _ = manifests[1]
        if inner["files"] != outer["files"]:
            raise WorkbenchProductRegistryError(
                "workbench_registry_ui_manifest_invalid",
                "overlay static-file bindings differ",
            )
        for name in _STATIC_FILES:
            descriptor = inner["files"][name]
            if not isinstance(descriptor, dict) or set(descriptor) != {
                "sha256",
                "bytes",
            }:
                raise WorkbenchProductRegistryError(
                    "workbench_registry_ui_manifest_invalid",
                    "overlay static-file descriptor is invalid",
                )
            raw = _read_regular_file(self.overlay_root / name, f"overlay {name}")
            if descriptor != {"sha256": _sha256(raw), "bytes": len(raw)}:
                raise WorkbenchProductRegistryError(
                    "workbench_registry_ui_file_mismatch",
                    "overlay static file does not match its manifest",
                )
        return _canonical_sha256(inner["files"]), _sha256(inner_raw)

    def _theme_operations(self) -> dict[str, Callable[[], dict[str, Any]]]:
        return {
            "wave1": lambda: self.theme_workbench.groups("wave1"),
            "master": lambda: self.theme_workbench.groups("master"),
            "supplemental": self.supplemental_visual_scans.theme_groups,
        }

    @staticmethod
    def _printed_ids(theme: dict[str, Any]) -> set[str]:
        values: set[str] = set()
        chains: list[Any] = []
        for paper in theme.get("papers", []):
            if isinstance(paper, dict):
                for group in paper.get("theme_groups", []):
                    if isinstance(group, dict):
                        chains.extend(group.get("atomic_chain", []))
        unassigned = theme.get("unassigned_pending_review")
        if isinstance(unassigned, dict):
            chains.extend(unassigned.get("atomic_chain", []))
        for item in chains:
            if isinstance(item, dict):
                printed_id = item.get("printed_question_id")
                if isinstance(printed_id, str) and printed_id:
                    values.add(printed_id)
        return values

    @staticmethod
    def _product(
        descriptor: dict[str, Any], theme: dict[str, Any]
    ) -> dict[str, Any]:
        scope = descriptor["scope"]
        if (
            theme.get("schema_version") != THEME_SCHEMA_VERSION
            or theme.get("scope") != scope
        ):
            raise WorkbenchProductRegistryError(
                "workbench_registry_schema_incompatible",
                "a theme product returned an incompatible projection",
            )
        authority = theme.get("authority")
        if not isinstance(authority, dict) or any(
            authority.get(key) is not expected
            for key, expected in _AUTHORITY.items()
        ):
            raise WorkbenchProductRegistryError(
                "workbench_registry_authority_invalid",
                "a workbench product widened its authority",
            )
        counts = theme.get("counts")
        if not isinstance(counts, dict):
            raise WorkbenchProductRegistryError(
                "workbench_registry_counts_invalid",
                "a workbench product has no verified counts",
            )
        projected_counts: dict[str, int | None] = {
            "papers": counts.get("papers"),
            "theme_big_questions": counts.get("theme_groups"),
            "printed_questions": None,
            "atomic_parts": counts.get("atomic_parts"),
            "display_atomic_units": counts.get("display_atomic_units"),
            "unassigned_atomic_parts": counts.get("unassigned_atomic_parts"),
        }
        if any(
            type(value) is not int or value < 0
            for key, value in projected_counts.items()
            if key != "printed_questions"
        ):
            raise WorkbenchProductRegistryError(
                "workbench_registry_counts_invalid",
                "a workbench product count is invalid",
            )
        printed_ids = WorkbenchProductRegistryReader._printed_ids(theme)
        blockers: list[dict[str, Any]] = []
        if scope == "master":
            blockers.append(
                {
                    "code": "printed_questions_not_complete_from_theme_projection",
                    "severity": "warning",
                    "message_zh": "主索引仍有无最小作答单元或缺主题父链的印刷小题，当前主题投影不能给出完整印刷题总数。",
                }
            )
        else:
            projected_counts["printed_questions"] = len(printed_ids)
        manifest_sha256 = _canonical_sha256(theme)
        data_snapshot_id = theme.get("data_snapshot_id")
        if not isinstance(data_snapshot_id, str) or len(data_snapshot_id) != 64:
            data_snapshot_id = manifest_sha256
        return {
            "product_id": scope,
            "display_name_zh": descriptor["display_name_zh"],
            "scope": scope,
            "schema_version": descriptor["schema_version"],
            "status": "ready_candidate_browse",
            "compatible": True,
            "data_snapshot_id": data_snapshot_id,
            "manifest_sha256": manifest_sha256,
            "counts": projected_counts,
            "blockers": blockers,
            "non_additive_to": [value for value in PRODUCT_ORDER if value != scope],
        }

    def _build(self) -> tuple[bytes, bytes, dict[str, bytes]]:
        descriptors = self._validate_descriptors(self._product_descriptors())
        ui_build_id, overlay_manifest_sha256 = self._ui_identity()
        operations = self._theme_operations()
        with ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="shchem-release-snapshot"
        ) as executor:
            futures = {scope: executor.submit(operations[scope]) for scope in PRODUCT_ORDER}
            themes = {scope: futures[scope].result() for scope in PRODUCT_ORDER}
        products = [
            self._product(descriptor, themes[descriptor["scope"]])
            for descriptor in descriptors
        ]
        snapshot_basis = {
            "algorithm": DATA_SNAPSHOT_ALGORITHM,
            "products": products,
        }
        data_snapshot_id = _canonical_sha256(snapshot_basis)
        registry = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "registry_id": REGISTRY_ID,
            "api_contract_version": CONTRACT_VERSION,
            "ui_build_id": ui_build_id,
            "manifest_sha256": overlay_manifest_sha256,
            "data_snapshot_id": data_snapshot_id,
            "data_snapshot_algorithm": DATA_SNAPSHOT_ALGORITHM,
            "product_count": len(products),
            "product_order": list(PRODUCT_ORDER),
            "products": products,
            "combined_atomic_total": None,
            "cross_scope_sum_allowed": False,
            "scope_boundary_zh": "Master、Wave1 与补充资料分别是主索引、候选细化视图和隔离候选范围，题量不能相加。",
            "authority": dict(_AUTHORITY),
            "integrity": {
                **_INTEGRITY,
                "registry_file_sha256": REGISTRY_FILE_SHA256,
            },
        }
        readiness = {
            "schema_version": READINESS_SCHEMA_VERSION,
            "status": "ready_candidate_browse",
            "api_contract_version": CONTRACT_VERSION,
            "ui_build_id": ui_build_id,
            "manifest_sha256": overlay_manifest_sha256,
            "data_snapshot_id": data_snapshot_id,
            "product_registry_id": REGISTRY_ID,
            "product_count": len(products),
            "product_ids": list(PRODUCT_ORDER),
            "ui_contract_compatible": True,
            "ready_for_candidate_browse": True,
            "combined_atomic_total": None,
            "cross_scope_sum_allowed": False,
            "subsystem_checks": {
                "product_registry": {"status": "pass", "blockers": []},
                "overlay_static_binding": {"status": "pass", "blockers": []},
                "offline_browse": {"status": "pass", "blockers": []},
                "full_bank_readiness": {
                    "status": "separate_governance_not_browse_product",
                    "blockers": [],
                },
            },
            "blockers": [
                {
                    "code": "model_provider_not_configured",
                    "severity": "info",
                    "blocks": ["model_analysis", "generation"],
                    "message_zh": "尚未配置模型服务；本地题库与主题浏览不受影响。",
                }
            ],
            "authority": dict(_AUTHORITY),
        }
        theme_bytes = {
            scope: _canonical_bytes(themes[scope]) for scope in PRODUCT_ORDER
        }
        return _canonical_bytes(registry), _canonical_bytes(readiness), theme_bytes

    def _activate(self) -> None:
        if self._registry_bytes is not None:
            return
        if self._terminal_error is not None:
            raise self._terminal_error
        with self._activation_lock:
            if self._registry_bytes is not None:
                return
            if self._terminal_error is not None:
                raise self._terminal_error
            try:
                registry_bytes, readiness_bytes, theme_bytes = self._build()
            except WorkbenchProductRegistryError as exc:
                self._terminal_error = exc
                raise
            except Exception as exc:
                failure = WorkbenchProductRegistryError(
                    "workbench_registry_activation_failed",
                    "workbench release snapshot could not be activated",
                )
                self._terminal_error = failure
                raise failure from exc
            self._theme_bytes = theme_bytes
            self._readiness_bytes = readiness_bytes
            self._registry_bytes = registry_bytes

    def registry(self) -> dict[str, Any]:
        self._activate()
        assert self._registry_bytes is not None
        return json.loads(self._registry_bytes.decode("utf-8"))

    def readiness(self) -> dict[str, Any]:
        self._activate()
        assert self._readiness_bytes is not None
        return json.loads(self._readiness_bytes.decode("utf-8"))

    def theme_groups(self, scope: str) -> dict[str, Any]:
        if scope not in PRODUCT_ORDER:
            raise WorkbenchProductRegistryError(
                "workbench_registry_unknown_product",
                "workbench product scope is unknown",
                400,
            )
        self._activate()
        return json.loads(self._theme_bytes[scope].decode("utf-8"))

    @property
    def activated(self) -> bool:
        return self._registry_bytes is not None


__all__ = [
    "DATA_SNAPSHOT_ALGORITHM",
    "PRODUCT_ORDER",
    "READINESS_SCHEMA_VERSION",
    "REGISTRY_ID",
    "REGISTRY_SCHEMA_VERSION",
    "WorkbenchProductRegistryError",
    "WorkbenchProductRegistryReader",
]

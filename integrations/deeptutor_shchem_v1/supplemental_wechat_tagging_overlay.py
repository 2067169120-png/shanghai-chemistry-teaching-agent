from __future__ import annotations

import hashlib
import inspect
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any

from jsonschema import Draft202012Validator

from .security import SecurityError, validate_identifier
from .supplemental_visual_scan import SupplementalVisualScanReader

SCOPE = "candidate_only_read_only_supplemental_wechat_tagging_overlay"
SCHEMA_VERSION = "1.0.0-supplemental-wechat-tagging-overlay-workbench"
REGISTRY_SCHEMA_VERSION = (
    "1.0.0-supplemental-wechat-tagging-overlay-registry"
)
REGISTRY_ID = (
    "SHCHEM-SUPPLEMENTAL-WECHAT-TAGGING-OVERLAY-REGISTRY-2026-08-27-V1"
)
REGISTRY_RELATIVE = Path(
    "kb/classification/"
    "supplemental_wechat_textbook_tagging_overlay_registry_v1_2026-08-27/"
    "registry.json"
)
REGISTRY_SCHEMA_RELATIVE = Path(
    "kb/classification/"
    "supplemental_wechat_textbook_tagging_overlay_registry_v1_2026-08-27/"
    "registry.schema.json"
)
REGISTRY_FILE_SHA256 = (
    "025f64b687cd74d5b8517e823d9905d60014909b33f72bab0fa96c5931f0abc1"
)
REGISTRY_SCHEMA_SHA256 = (
    "58d7c3358dcfef917a7b1d57354cfdf2e55c5d019ed3000ea9b0bd9582abe38f"
)
BASE_REGISTRY_ID = (
    "SHCHEM-SUPPLEMENTAL-VISUAL-SCAN-REGISTRY-2026-08-26-V1"
)
BASE_REGISTRY_FILE_SHA256 = (
    "37dcd5b08e26fd2b3c08145b8bbaa737a604d2080ee8fd21c320829a0d9b31e0"
)
PRODUCT_ID = "SHCHEM-SUPPLEMENTAL-WECHAT-TEXTBOOK-TAGGING-2026-08-27-V1"
PRODUCT_MANIFEST_FILE_SHA256 = (
    "52ee4abc72322004e228122377c32867b3fdef92910bf91e6d4e7e93b50a3fb9"
)
PRODUCT_MANIFEST_SELF_SHA256 = (
    "42cc782e660f9893080d0db1801ecfac026e0da94a9f0d9edf4c5b154274d7ab"
)
TAGGING_RECORDS_FILE_SHA256 = (
    "9983571a7fb75d6139c53e9f2815c51977afbcceba0674be8457ebd2731e6197"
)
TAGGING_RECORD_SCHEMA_FILE_SHA256 = (
    "2d83c47b4f6e527bca0983c85c444274096a7e583656a979ebe5c4024be611ef"
)
DATA_SNAPSHOT_ID = "SUPPLEMENTAL-WECHAT-TAGGING-OVERLAY-2026-08-27-V1"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_DTO_TEXT = re.compile(
    r"(?i:https?://|file:/+|(?<![a-z0-9])[a-z]:[\\/]"
    r"|\\\\[^\\/\s]+[\\/]"
    r"|(?<![A-Za-z0-9_./\\])(?:sh-chem-db/|kb/|staging/|runtime/|"
    r"integrations/|\.intake/|课本/))"
)
_FORBIDDEN_DTO_KEYS = {
    "path",
    "source_path",
    "source_root",
    "sha256",
    "self_sha256",
    "manifest_file_sha256",
    "manifest_self_sha256",
    "tagging_records_file_sha256",
    "tagging_record_schema_file_sha256",
    "reference_summary_zh",
    "reference_answer_text",
    "visual_evidence",
}
_FACTOR_IDS = (
    "information_transformations",
    "reasoning_chain_steps",
    "knowledge_module_span",
    "representation_switches",
    "calculation_load",
    "experiment_load",
    "openness",
    "unfamiliarity",
    "language_load",
    "dependency_on_prior_parts",
)
_IGNORED_PRODUCT_FILES = {
    "manifest.json",
    "validation_report.json",
    "mutation_report.json",
    "mutation_test_report.json",
}
_EXPECTED_BLOCKED = {
    ("SJ2026-EM-S2-Q5-P1", "K10", "TB-E1-C3"),
    ("SJ2026-EM-S3-Q9-P1", "K17", "TB-E3-C3"),
    ("MVPPLUS-A-PL-0bb39aa129f46783-T1-Q9-P1", "K14", "TB-E2-C3"),
    ("HK2026-EM-S2-Q9-P1", "K09", "TB-E1-C2"),
}
_AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "human_chemistry_reviewed": False,
    "verified": False,
    "official": False,
    "retrieval_ready": False,
    "retrieval_allowed": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "answer_verified": False,
    "rubric_verified": False,
    "measured_difficulty_verified": False,
    "pixel_reuse_allowed": False,
}


class SupplementalWechatTaggingOverlayError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _OverlaySnapshot:
    status: dict[str, Any]
    details_by_node_id: dict[str, dict[str, Any]]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: dict[str, Any], *, null_field: str) -> str:
    copied = deepcopy(value)
    copied[null_field] = None
    raw = json.dumps(
        copied,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(raw)


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_value(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicate_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SupplementalWechatTaggingOverlayError(
            "supplemental_tagging_json_invalid",
            f"{label} is not strict UTF-8 JSON",
        ) from exc


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    value = _json_value(raw, label)
    if not isinstance(value, dict):
        raise SupplementalWechatTaggingOverlayError(
            "supplemental_tagging_json_invalid",
            f"{label} is not a JSON object",
        )
    return value


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SupplementalWechatTaggingOverlayError(
            "supplemental_tagging_path_invalid", f"{label} is malformed"
        )
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise SupplementalWechatTaggingOverlayError(
            "supplemental_tagging_path_invalid", f"{label} leaves its root"
        )
    return relative


def _safe_node_id(value: Any) -> str:
    if not isinstance(value, str):
        raise SupplementalWechatTaggingOverlayError(
            "supplemental_tagging_node_id_invalid",
            "supplemental tagging node ID is invalid",
            400,
        )
    try:
        return validate_identifier(value, "node_id")
    except SecurityError as exc:
        raise SupplementalWechatTaggingOverlayError(
            "supplemental_tagging_node_id_invalid",
            "supplemental tagging node ID is not routable",
            400,
        ) from exc


def _assert_safe_dto(value: Any, *, key: str | None = None) -> None:
    if key in _FORBIDDEN_DTO_KEYS or (
        isinstance(key, str) and key.casefold().endswith("_sha256")
    ):
        raise SupplementalWechatTaggingOverlayError(
            "supplemental_tagging_projection_leak",
            "supplemental tagging DTO contains a forbidden field",
        )
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            _assert_safe_dto(nested_value, key=str(nested_key))
        return
    if isinstance(value, list):
        for nested in value:
            _assert_safe_dto(nested, key=key)
        return
    if isinstance(value, str) and (
        _FORBIDDEN_DTO_TEXT.search(value) or _SHA256.fullmatch(value)
    ):
        raise SupplementalWechatTaggingOverlayError(
            "supplemental_tagging_projection_leak",
            "supplemental tagging DTO contains a path, URL, or hash",
        )


def _same(value: Any, expected: Any, code: str, message: str) -> None:
    if value != expected:
        raise SupplementalWechatTaggingOverlayError(code, message)


class SupplementalWechatTaggingOverlayReader:
    """Hash-bound, fail-closed overlay for the 57 Shanghai WeChat atomics."""

    def __init__(
        self,
        shchem_root: Path,
        base_reader: SupplementalVisualScanReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.base_reader = base_reader or self._new_unwrapped_base_reader()
        self._snapshot_lock = Lock()
        self._snapshot_cache: _OverlaySnapshot | None = None

    def _new_unwrapped_base_reader(self) -> SupplementalVisualScanReader:
        parameters = inspect.signature(SupplementalVisualScanReader).parameters
        if "tagging_overlay" in parameters:
            return SupplementalVisualScanReader(
                self.shchem_root, tagging_overlay=None
            )
        return SupplementalVisualScanReader(self.shchem_root)

    def _read_bound_file(
        self,
        relative: Path | PurePosixPath,
        expected_sha256: str,
        label: str,
        *,
        root: Path | None = None,
    ) -> bytes:
        if not _SHA256.fullmatch(expected_sha256):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_hash_invalid",
                f"{label} has an invalid activated hash",
            )
        confinement_root = (root or self.shchem_root).resolve()
        path = confinement_root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_path_invalid",
                    f"{label} path contains a symbolic link",
                )
        resolved = path.resolve()
        if (
            not resolved.is_relative_to(confinement_root)
            or not resolved.is_file()
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_unavailable", f"{label} is unavailable"
            )
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_unavailable", f"{label} is unreadable"
            ) from exc
        if _sha256(raw) != expected_sha256:
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_drift",
                f"{label} does not match its activated SHA-256",
            )
        return raw

    def _load_registry(self) -> dict[str, Any]:
        schema_raw = self._read_bound_file(
            REGISTRY_SCHEMA_RELATIVE,
            REGISTRY_SCHEMA_SHA256,
            "supplemental tagging overlay registry schema",
        )
        registry_raw = self._read_bound_file(
            REGISTRY_RELATIVE,
            REGISTRY_FILE_SHA256,
            "supplemental tagging overlay registry",
        )
        schema = _json_object(schema_raw, "overlay registry schema")
        registry = _json_object(registry_raw, "overlay registry")
        try:
            validator = Draft202012Validator(schema)
            validator.check_schema(schema)
            errors = list(validator.iter_errors(registry))
        except Exception as exc:
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_registry_schema_invalid",
                "supplemental tagging overlay registry schema is invalid",
            ) from exc
        if errors:
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_registry_invalid",
                "supplemental tagging overlay registry failed schema validation",
            )
        _same(
            registry.get("schema_version"),
            REGISTRY_SCHEMA_VERSION,
            "supplemental_tagging_registry_identity_invalid",
            "supplemental tagging overlay registry schema version drifted",
        )
        _same(
            registry.get("registry_id"),
            REGISTRY_ID,
            "supplemental_tagging_registry_identity_invalid",
            "supplemental tagging overlay registry ID drifted",
        )
        self_sha = registry.get("self_sha256")
        if (
            not isinstance(self_sha, str)
            or not _SHA256.fullmatch(self_sha)
            or _canonical_sha256(registry, null_field="self_sha256")
            != self_sha
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_registry_drift",
                "supplemental tagging overlay registry self hash mismatches",
            )
        return registry

    def _product_root(self, product_relative: str) -> Path:
        relative = _safe_relative(product_relative, "overlay product path")
        path = self.shchem_root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_path_invalid",
                    "overlay product path contains a symbolic link",
                )
        resolved = path.resolve()
        if (
            not resolved.is_relative_to(self.shchem_root)
            or not resolved.is_dir()
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_unavailable",
                "overlay product directory is unavailable",
            )
        return resolved

    def _actual_output_inventory(
        self, product_root: Path
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(product_root.rglob("*"), key=lambda p: p.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(product_root)
            if (
                path.name in _IGNORED_PRODUCT_FILES
                or path.suffix in {".pyc", ".pyo"}
                or "__pycache__" in relative.parts
                or ".pytest_cache" in relative.parts
            ):
                continue
            if path.is_symlink():
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_path_invalid",
                    "overlay product output contains a symbolic link",
                )
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_unavailable",
                    "overlay product output is unreadable",
                ) from exc
            rows.append(
                {
                    "bytes": len(raw),
                    "path": relative.as_posix(),
                    "sha256": _sha256(raw),
                }
            )
        return rows

    def _load_product_records(
        self, registry: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        product = registry["overlay_product"]
        _same(
            product["product_id"],
            PRODUCT_ID,
            "supplemental_tagging_product_identity_invalid",
            "overlay product identity drifted",
        )
        for value, expected, label in (
            (
                product["manifest_file_sha256"],
                PRODUCT_MANIFEST_FILE_SHA256,
                "manifest file",
            ),
            (
                product["manifest_self_sha256"],
                PRODUCT_MANIFEST_SELF_SHA256,
                "manifest self",
            ),
            (
                product["tagging_records_file_sha256"],
                TAGGING_RECORDS_FILE_SHA256,
                "tagging records",
            ),
            (
                product["tagging_record_schema_file_sha256"],
                TAGGING_RECORD_SCHEMA_FILE_SHA256,
                "tagging record schema",
            ),
        ):
            _same(
                value,
                expected,
                "supplemental_tagging_product_hash_binding_invalid",
                f"overlay product {label} binding drifted",
            )

        product_root = self._product_root(product["product_relative"])
        manifest_raw = self._read_bound_file(
            PurePosixPath("manifest.json"),
            PRODUCT_MANIFEST_FILE_SHA256,
            "overlay product manifest",
            root=product_root,
        )
        manifest = _json_object(manifest_raw, "overlay product manifest")
        _same(
            manifest.get("product_id"),
            PRODUCT_ID,
            "supplemental_tagging_product_identity_invalid",
            "overlay manifest product identity drifted",
        )
        _same(
            manifest.get("self_sha256"),
            PRODUCT_MANIFEST_SELF_SHA256,
            "supplemental_tagging_manifest_drift",
            "overlay manifest activated self hash drifted",
        )
        if (
            manifest.get("self_hash_algorithm")
            != "canonical-json-sha256-with-self_sha256-null-v1"
            or _canonical_sha256(manifest, null_field="self_sha256")
            != PRODUCT_MANIFEST_SELF_SHA256
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_manifest_drift",
                "overlay manifest canonical self hash mismatches",
            )

        supplied_outputs = manifest.get("outputs")
        if not isinstance(supplied_outputs, list) or any(
            not isinstance(row, dict) for row in supplied_outputs
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_manifest_closure_invalid",
                "overlay manifest output closure is malformed",
            )
        actual_outputs = self._actual_output_inventory(product_root)
        if (
            sorted(
                supplied_outputs, key=lambda row: row.get("path", "").casefold()
            )
            != sorted(actual_outputs, key=lambda row: row["path"].casefold())
            or manifest.get("output_file_count") != len(actual_outputs)
            or manifest.get("total_output_bytes_excluding_manifest_and_reports")
            != sum(row["bytes"] for row in actual_outputs)
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_manifest_closure_invalid",
                "overlay manifest output closure mismatches the product",
            )
        for row in supplied_outputs:
            relative = _safe_relative(row.get("path"), "manifest output path")
            expected = row.get("sha256")
            if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_manifest_closure_invalid",
                    "overlay manifest output hash is malformed",
                )
            raw = self._read_bound_file(
                relative,
                expected,
                f"overlay product output {relative.as_posix()}",
                root=product_root,
            )
            if len(raw) != row.get("bytes"):
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_manifest_closure_invalid",
                    "overlay manifest output byte count drifted",
                )

        output_by_path = {row["path"]: row for row in supplied_outputs}
        required_hashes = {
            "tagging_records.jsonl": TAGGING_RECORDS_FILE_SHA256,
            "tagging_record_schema.json": TAGGING_RECORD_SCHEMA_FILE_SHA256,
        }
        for filename, expected in required_hashes.items():
            if output_by_path.get(filename, {}).get("sha256") != expected:
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_manifest_closure_invalid",
                    f"overlay manifest does not bind {filename}",
                )

        schema_raw = self._read_bound_file(
            PurePosixPath("tagging_record_schema.json"),
            TAGGING_RECORD_SCHEMA_FILE_SHA256,
            "overlay tagging record schema",
            root=product_root,
        )
        records_raw = self._read_bound_file(
            PurePosixPath("tagging_records.jsonl"),
            TAGGING_RECORDS_FILE_SHA256,
            "overlay tagging records",
            root=product_root,
        )
        schema = _json_object(schema_raw, "overlay tagging record schema")
        try:
            validator = Draft202012Validator(schema)
            validator.check_schema(schema)
        except Exception as exc:
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_record_schema_invalid",
                "overlay tagging record schema is invalid",
            ) from exc

        try:
            decoded = records_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_records_invalid",
                "overlay tagging records are not UTF-8",
            ) from exc
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(decoded.splitlines(), 1):
            if not line.strip():
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_records_invalid",
                    "overlay tagging records contain a blank line",
                )
            value = _json_object(
                line.encode("utf-8"), f"overlay tagging record {line_number}"
            )
            errors = list(validator.iter_errors(value))
            if errors:
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_record_invalid",
                    f"overlay tagging record {line_number} failed schema validation",
                )
            records.append(value)
        _same(
            len(records),
            product["atomic_part_count"],
            "supplemental_tagging_record_count_invalid",
            "overlay tagging record count drifted",
        )
        return records, manifest

    def _base_public_snapshot(
        self,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        dict[str, dict[str, Any]],
    ]:
        status = self.base_reader.status()
        if not isinstance(status, dict):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_base_invalid",
                "base Supplemental status is malformed",
            )
        registry = status.get("registry")
        counts = status.get("counts")
        if not isinstance(registry, dict) or not isinstance(counts, dict):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_base_invalid",
                "base Supplemental status lacks registry or counts",
            )
        expected_counts = {
            "atomic_parts": 86,
            "shanghai_exam_atomic_parts": 57,
            "external_handout_atomic_parts": 29,
        }
        _same(
            registry.get("registry_id"),
            BASE_REGISTRY_ID,
            "supplemental_tagging_base_registry_invalid",
            "base Supplemental registry ID drifted",
        )
        _same(
            registry.get("file_sha256"),
            BASE_REGISTRY_FILE_SHA256,
            "supplemental_tagging_base_registry_invalid",
            "base Supplemental registry file binding drifted",
        )
        for key, expected in expected_counts.items():
            _same(
                counts.get(key),
                expected,
                "supplemental_tagging_base_count_invalid",
                f"base Supplemental {key} count drifted",
            )
        authority = status.get("authority")
        if not isinstance(authority, dict) or any(
            authority.get(key) != expected
            for key, expected in _AUTHORITY.items()
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_base_authority_invalid",
                "base Supplemental authority boundary drifted",
            )
        page = self.base_reader.list_atomic(limit=200, offset=0)
        items = page.get("items") if isinstance(page, dict) else None
        if (
            not isinstance(items, list)
            or page.get("total") != 86
            or page.get("count") != 86
            or len(items) != 86
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_base_list_invalid",
                "base Supplemental public list does not close at 86 atomics",
            )
        by_id: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_base_list_invalid",
                    "base Supplemental list contains a malformed item",
                )
            node_id = item.get("node_id")
            if not isinstance(node_id, str) or node_id in by_id:
                raise SupplementalWechatTaggingOverlayError(
                    "supplemental_tagging_base_list_invalid",
                    "base Supplemental list IDs are invalid or duplicated",
                )
            by_id[node_id] = item
        source_counts = {
            kind: sum(item.get("source_kind") == kind for item in items)
            for kind in (
                "shanghai_exam_wechat_archive",
                "external_teaching_handout",
            )
        }
        _same(
            source_counts,
            {
                "shanghai_exam_wechat_archive": 57,
                "external_teaching_handout": 29,
            },
            "supplemental_tagging_base_partition_invalid",
            "base Supplemental exam/handout partition drifted",
        )
        return status, items, by_id

    @staticmethod
    def _expected_classification(record: dict[str, Any]) -> dict[str, Any]:
        classification = record["classification"]
        return {
            "A": deepcopy(classification["A"]),
            "C": deepcopy(classification["C"]),
            "R": deepcopy(classification["R"]),
            "RP": deepcopy(classification["RP"]),
            "item_type": classification["item_type"],
            "primary_K": classification["primary_K"],
            "selection_rule": classification["selection_rule"],
            "supporting_K": deepcopy(classification["supporting_K"]),
        }

    @staticmethod
    def _evidence_descriptors(record: dict[str, Any]) -> list[tuple[Any, ...]]:
        structure = record["structure_review"]
        rows = [
            *structure["question_evidence"],
            *structure["shared_material_evidence"],
        ]
        return sorted(
            (row["crop_id"], row["role"], row.get("source_page"))
            for row in rows
        )

    def _cross_check_record(
        self,
        record: dict[str, Any],
        base_item: dict[str, Any],
        base_detail: dict[str, Any],
    ) -> dict[str, Any]:
        hierarchy = record["hierarchy"]
        node_id = hierarchy["atomic_part_id"]
        node = base_detail.get("node")
        visual = base_detail.get("visual_scan")
        if not isinstance(node, dict) or not isinstance(visual, dict):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_base_detail_invalid",
                "base Supplemental detail is malformed",
            )
        _same(
            node.get("node_id"),
            node_id,
            "supplemental_tagging_base_detail_invalid",
            "base Supplemental detail node ID drifted",
        )
        for source in (base_item, visual):
            _same(
                source.get("source_kind"),
                "shanghai_exam_wechat_archive",
                "supplemental_tagging_base_partition_invalid",
                "overlay record is not bound to a Shanghai WeChat exam atomic",
            )
        _same(
            record["source_layer"]["source_kind"],
            "shanghai_exam_wechat_archive",
            "supplemental_tagging_source_layer_invalid",
            "overlay source layer is not the Shanghai WeChat exam archive",
        )

        expected_classification = self._expected_classification(record)
        _same(
            visual.get("scan_classification"),
            expected_classification,
            "supplemental_tagging_classification_mismatch",
            "overlay classification differs from the base public detail",
        )
        expected_list_classification = {
            "item_type": expected_classification["item_type"],
            "selection_rule": expected_classification["selection_rule"],
            "K": [
                expected_classification["primary_K"],
                *expected_classification["supporting_K"],
            ],
            "A": expected_classification["A"],
            "C": expected_classification["C"],
            "R": expected_classification["R"],
            "RP": expected_classification["RP"],
        }
        _same(
            base_item.get("classification"),
            expected_list_classification,
            "supplemental_tagging_classification_mismatch",
            "overlay classification differs from the base public list",
        )

        difficulty = record["difficulty"]
        base_difficulty = visual.get("cognitive_difficulty")
        if not isinstance(base_difficulty, dict):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_difficulty_mismatch",
                "base Supplemental difficulty detail is malformed",
            )
        _same(
            difficulty["cognitive_candidate"],
            base_difficulty.get("cognitive_prelabel"),
            "supplemental_tagging_difficulty_mismatch",
            "overlay cognitive candidate differs from base",
        )
        _same(
            difficulty["factors"],
            base_difficulty.get("factors"),
            "supplemental_tagging_difficulty_mismatch",
            "overlay ten-factor difficulty differs from base",
        )
        _same(
            difficulty["is_measured"],
            base_difficulty.get("is_measured"),
            "supplemental_tagging_difficulty_mismatch",
            "overlay measured-difficulty boundary differs from base",
        )
        _same(
            difficulty["measured_difficulty"],
            base_difficulty.get("measured_difficulty"),
            "supplemental_tagging_difficulty_mismatch",
            "overlay measured-difficulty value differs from base",
        )
        factors = difficulty["factors"]
        _same(
            tuple(row.get("dimension_id") for row in factors),
            _FACTOR_IDS,
            "supplemental_tagging_difficulty_mismatch",
            "overlay difficulty does not contain the exact ten factors",
        )
        base_list_difficulty = base_item.get("difficulty")
        if not isinstance(base_list_difficulty, dict):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_difficulty_mismatch",
                "base Supplemental list difficulty is malformed",
            )
        _same(
            base_list_difficulty.get("factors"),
            factors,
            "supplemental_tagging_difficulty_mismatch",
            "overlay ten-factor difficulty differs from base list",
        )

        scan_hierarchy = visual.get("scan_hierarchy")
        if not isinstance(scan_hierarchy, dict):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_hierarchy_mismatch",
                "base Supplemental hierarchy is malformed",
            )
        hierarchy_pairs = (
            ("paper_id", "paper_id"),
            ("theme_big_question_id", "theme_id"),
            ("printed_question_id", "printed_question_id"),
            ("atomic_part_id", "atomic_part_id"),
            ("theme_sequence", "theme_sequence"),
            ("printed_sequence", "printed_sequence"),
            ("atomic_sequence_in_printed", "atomic_sequence_in_printed"),
            ("theme_title", "theme_title"),
        )
        for overlay_key, base_key in hierarchy_pairs:
            _same(
                hierarchy[overlay_key],
                scan_hierarchy.get(base_key),
                "supplemental_tagging_hierarchy_mismatch",
                f"overlay hierarchy {overlay_key} differs from base",
            )
        parent_chain = base_item.get("parent_chain")
        expected_parent_ids = [
            hierarchy["paper_id"],
            hierarchy["theme_big_question_id"],
            hierarchy["printed_question_id"],
            hierarchy["atomic_part_id"],
        ]
        if not isinstance(parent_chain, list):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_hierarchy_mismatch",
                "base Supplemental parent chain is malformed",
            )
        _same(
            [row.get("node_id") for row in parent_chain],
            expected_parent_ids,
            "supplemental_tagging_hierarchy_mismatch",
            "overlay four-level parent chain differs from base",
        )
        _same(
            [row.get("order") for row in parent_chain],
            [
                None,
                hierarchy["theme_sequence"],
                hierarchy["printed_sequence"],
                hierarchy["atomic_sequence_in_printed"],
            ],
            "supplemental_tagging_hierarchy_mismatch",
            "overlay hierarchy sequence differs from base",
        )

        dependency = record["structure_review"]["dependency"]
        base_dependency = visual.get("dependency")
        if not isinstance(base_dependency, dict):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_dependency_mismatch",
                "base Supplemental dependency is malformed",
            )
        for key in (
            "dependency_kind",
            "prior_atomic_part_ids",
            "shared_material_crop_ids",
            "analysis_zh",
        ):
            _same(
                dependency[key],
                base_dependency.get(key),
                "supplemental_tagging_dependency_mismatch",
                f"overlay dependency {key} differs from base",
            )
        base_descriptors = visual.get("evidence_descriptors")
        if not isinstance(base_descriptors, list):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_shared_material_mismatch",
                "base Supplemental evidence descriptors are malformed",
            )
        _same(
            self._evidence_descriptors(record),
            sorted(
                (
                    row.get("crop_id"),
                    row.get("evidence_role"),
                    row.get("source_page"),
                )
                for row in base_descriptors
            ),
            "supplemental_tagging_shared_material_mismatch",
            "overlay question/shared evidence differs from base detail",
        )

        answer = record["answer_alignment"]
        answer_boundary = visual.get("answer_boundary")
        reference_answer = visual.get("reference_answer")
        if not isinstance(answer_boundary, dict) or not isinstance(
            reference_answer, dict
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_answer_alignment_mismatch",
                "base Supplemental answer boundary is malformed",
            )
        expected_answer = {
            "alignment_status": (
                "present_part_aligned_to_nonofficial_reference_pixels"
            ),
            "availability": "present_part_aligned",
            "authority": "nonofficial_reference",
            "source_authority": "nonofficial_reference",
            "direct_source_adoption": True,
            "independently_verified": False,
            "answer_verified": False,
            "official_answer_claim_allowed": False,
        }
        for key, expected in expected_answer.items():
            _same(
                answer.get(key),
                expected,
                "supplemental_tagging_answer_alignment_mismatch",
                f"overlay answer boundary {key} drifted",
            )
        _same(
            answer_boundary,
            {
                "availability": answer["availability"],
                "authority": answer["authority"],
                "verified": False,
                "independently_verified": False,
            },
            "supplemental_tagging_answer_alignment_mismatch",
            "overlay answer boundary differs from base detail",
        )
        for key, expected in (
            ("availability", answer["availability"]),
            ("source_authority", answer["source_authority"]),
            ("independently_verified", False),
        ):
            _same(
                reference_answer.get(key),
                expected,
                "supplemental_tagging_answer_alignment_mismatch",
                f"overlay answer {key} differs from base detail",
            )
        _same(
            base_item.get("answer_meta", {}).get("availability"),
            answer["availability"],
            "supplemental_tagging_answer_alignment_mismatch",
            "overlay answer availability differs from base list",
        )
        _same(
            base_item.get("answer_meta", {}).get("source_authority"),
            answer["source_authority"],
            "supplemental_tagging_answer_alignment_mismatch",
            "overlay answer authority differs from base list",
        )

        source_identity = record["source_identity"]
        if (
            not isinstance(source_identity.get("paper_face"), dict)
            or not isinstance(source_identity.get("wechat_article"), dict)
            or source_identity["paper_face"] == source_identity["wechat_article"]
            or not source_identity.get("identity_separation_note")
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_source_identity_invalid",
                "paper-face and WeChat identities are not strictly separated",
            )

        manifest_gates = record.get("authority_gates")
        if not isinstance(manifest_gates, dict) or any(
            value is not False for value in manifest_gates.values()
        ):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_authority_invalid",
                "overlay record attempts to unlock an authority gate",
            )

        mapping = record["textbook_mapping"]
        safe_entries = []
        safe_mapping_keys = (
            "knowledge_tag",
            "knowledge_role",
            "evidence_level",
            "relation",
            "mapping_status",
            "volume_id",
            "volume_title",
            "chapter_id",
            "chapter_title",
            "section_id",
            "section_number",
            "section_title",
            "unit_id",
            "unit_title",
            "unit_status",
            "edition_or_printing",
            "edition_status",
            "blocker_or_note",
        )
        for entry in mapping["entries"]:
            safe_entries.append(
                {key: deepcopy(entry.get(key)) for key in safe_mapping_keys}
            )
        answer_projection = {
            "alignment_status": answer["alignment_status"],
            "availability": answer["availability"],
            "authority": answer["authority"],
            "source_authority": answer["source_authority"],
            "source_authority_zh": answer["source_authority_zh"],
            "direct_source_adoption": answer["direct_source_adoption"],
            "independently_verified": answer["independently_verified"],
            "answer_verified": answer["answer_verified"],
            "official_answer_claim_allowed": answer[
                "official_answer_claim_allowed"
            ],
            "quality_note": deepcopy(answer["quality_note"]),
            "reference_answer_exposed": False,
            "answer_pixels_exposed": False,
        }
        detail = {
            "schema_version": SCHEMA_VERSION,
            "scope": SCOPE,
            "data_snapshot_id": DATA_SNAPSHOT_ID,
            "node_id": node_id,
            "hierarchy": deepcopy(hierarchy),
            "classification": expected_classification,
            "difficulty": deepcopy(base_difficulty),
            "source_identity": deepcopy(source_identity),
            "source_layer": deepcopy(record["source_layer"]),
            "textbook_directory_mapping": {
                "mapping_status": mapping["mapping_status"],
                "entries": safe_entries,
            },
            "answer_alignment_boundary": answer_projection,
            "authority": deepcopy(_AUTHORITY),
        }
        _assert_safe_dto(detail)
        return detail

    def _build_snapshot(self) -> _OverlaySnapshot:
        registry = self._load_registry()
        records, manifest = self._load_product_records(registry)
        base_status, base_items, base_by_id = self._base_public_snapshot()

        record_ids = [record["hierarchy"]["atomic_part_id"] for record in records]
        if len(record_ids) != len(set(record_ids)):
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_record_id_invalid",
                "overlay tagging record IDs are duplicated",
            )
        exam_ids = [
            item["node_id"]
            for item in base_items
            if item["source_kind"] == "shanghai_exam_wechat_archive"
        ]
        handout_ids = {
            item["node_id"]
            for item in base_items
            if item["source_kind"] == "external_teaching_handout"
        }
        _same(
            record_ids,
            exam_ids,
            "supplemental_tagging_id_closure_invalid",
            "overlay IDs/order do not exactly equal the 57 base exam atomics",
        )
        if set(record_ids) & handout_ids:
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_handout_overlap",
                "overlay tagging records overlap external handouts",
            )

        details: dict[str, dict[str, Any]] = {}
        blocked: set[tuple[str, str, str]] = set()
        mapping_entries = 0
        factor_entries = 0
        mapping_status_counts: dict[str, int] = {}
        paper_ids: set[str] = set()
        theme_ids: set[str] = set()
        printed_ids: set[str] = set()
        for record in records:
            node_id = record["hierarchy"]["atomic_part_id"]
            base_detail = self.base_reader.detail(node_id)
            details[node_id] = self._cross_check_record(
                record, base_by_id[node_id], base_detail
            )
            hierarchy = record["hierarchy"]
            paper_ids.add(hierarchy["paper_id"])
            theme_ids.add(hierarchy["theme_big_question_id"])
            printed_ids.add(hierarchy["printed_question_id"])
            mapping = record["textbook_mapping"]
            mapping_status = mapping["mapping_status"]
            mapping_status_counts[mapping_status] = (
                mapping_status_counts.get(mapping_status, 0) + 1
            )
            mapping_entries += len(mapping["entries"])
            factor_entries += len(record["difficulty"]["factors"])
            for entry in mapping["entries"]:
                if entry["mapping_status"] != "blocked_pending_review":
                    continue
                if any(
                    entry.get(key) is not None
                    for key in (
                        "node_key",
                        "section_id",
                        "section_number",
                        "section_title",
                    )
                ):
                    raise SupplementalWechatTaggingOverlayError(
                        "supplemental_tagging_blocked_precision_invalid",
                        "a chapter-level blocked mapping fabricates section precision",
                    )
                blocked.add(
                    (node_id, entry["knowledge_tag"], entry["chapter_id"])
                )

        expected_product = registry["overlay_product"]
        count_checks = {
            "paper_count": len(paper_ids),
            "theme_big_question_count": len(theme_ids),
            "printed_question_count": len(printed_ids),
            "atomic_part_count": len(records),
            "textbook_mapping_entry_count": mapping_entries,
            "difficulty_factor_entry_count": factor_entries,
            "complete_mapping_count": mapping_status_counts.get(
                "complete_directory_level_unit_unknown", 0
            ),
            "partial_blocked_mapping_count": mapping_status_counts.get(
                "partial_blocked", 0
            ),
            "answer_aligned_nonofficial_unverified_count": len(records),
            "external_handout_atomic_count": len(set(record_ids) & handout_ids),
        }
        for key, value in count_checks.items():
            _same(
                value,
                expected_product[key],
                "supplemental_tagging_count_invalid",
                f"overlay {key} count drifted",
            )
        _same(
            blocked,
            _EXPECTED_BLOCKED,
            "supplemental_tagging_blocked_closure_invalid",
            "overlay chapter-level blocked mapping closure drifted",
        )
        _same(
            manifest.get("completed_counts"),
            {
                "paper": 3,
                "theme_big_question": 6,
                "printed_question": 51,
                "atomic_part": 57,
            },
            "supplemental_tagging_manifest_count_invalid",
            "overlay manifest hierarchy counts drifted",
        )

        status = {
            "schema_version": SCHEMA_VERSION,
            "scope": SCOPE,
            "data_snapshot_id": DATA_SNAPSHOT_ID,
            "registry": {
                "registry_id": REGISTRY_ID,
                "schema_version": REGISTRY_SCHEMA_VERSION,
                "base_registry_id": BASE_REGISTRY_ID,
                "product_id": PRODUCT_ID,
            },
            "counts": {
                "papers": 3,
                "theme_big_questions": 6,
                "printed_questions": 51,
                "atomic_parts": 57,
                "base_supplemental_atomic_parts": 86,
                "base_shanghai_exam_atomic_parts": 57,
                "base_external_handout_atomic_parts": 29,
                "overlay_external_handout_atomic_parts": 0,
                "textbook_mapping_entries": 117,
                "complete_textbook_mappings": 53,
                "partial_blocked_textbook_mappings": 4,
                "difficulty_factor_entries": 570,
                "answer_aligned_nonofficial_unverified": 57,
            },
            "authority": deepcopy(_AUTHORITY),
            "integrity": {
                "registry_file_hash_pinned": True,
                "registry_schema_hash_pinned": True,
                "registry_schema_verified": True,
                "registry_self_hash_verified": True,
                "base_registry_binding_verified": True,
                "base_public_list_detail_cross_checked": True,
                "product_manifest_file_hash_pinned": True,
                "product_manifest_self_hash_verified": True,
                "product_manifest_output_closure_verified": True,
                "tagging_record_schema_verified": True,
                "tagging_record_ids_bidirectionally_closed": True,
                "external_handout_excluded": True,
                "answer_text_excluded": True,
                "answer_pixels_excluded": True,
                "local_paths_excluded": True,
                "hashes_excluded": True,
                "fail_closed": True,
            },
        }
        _same(
            base_status["counts"]["atomic_parts"],
            status["counts"]["base_supplemental_atomic_parts"],
            "supplemental_tagging_base_count_invalid",
            "overlay status base total differs from base Supplemental status",
        )
        _assert_safe_dto(status)
        return _OverlaySnapshot(status=status, details_by_node_id=details)

    def _snapshot(self) -> _OverlaySnapshot:
        cached = self._snapshot_cache
        if cached is not None:
            return cached
        with self._snapshot_lock:
            cached = self._snapshot_cache
            if cached is None:
                cached = self._build_snapshot()
                self._snapshot_cache = cached
            return cached

    def status(self) -> dict[str, Any]:
        return deepcopy(self._snapshot().status)

    def detail(self, node_id: str) -> dict[str, Any]:
        safe_id = _safe_node_id(node_id)
        detail = self._snapshot().details_by_node_id.get(safe_id)
        if detail is None:
            raise SupplementalWechatTaggingOverlayError(
                "supplemental_tagging_not_found",
                "supplemental WeChat tagging overlay is unavailable for this node",
                404,
            )
        return deepcopy(detail)

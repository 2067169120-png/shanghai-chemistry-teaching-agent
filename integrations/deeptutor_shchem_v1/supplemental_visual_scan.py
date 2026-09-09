from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any
from urllib.parse import quote

from jsonschema import Draft202012Validator

from .candidate_review import CandidateCropPayload
from .reader_cancellation import check_read_cancelled
from .security import SecurityError, validate_identifier

SCOPE = "candidate_only_read_only_supplemental_visual_scan"
THEME_SCOPE = "supplemental"
SCHEMA_VERSION = "1.0.0-supplemental-visual-scan-workbench"
THEME_SCHEMA_VERSION = "1.0.0-theme-workbench-candidate"
MAX_CROP_BYTES = 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_TEXT = re.compile(
    r"(?i:https?://|file:/+|(?<![a-z0-9])[a-z]:[\\/]"
    r"|\\\\[^\\/\s]+[\\/]"
    r"|(?<![A-Za-z0-9_./\\])(?:sh-chem-db/|kb/|staging/|runtime/|integrations/))"
)
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
_ANSWER_AUTHORITIES = {
    "nonofficial_reference",
    "nonofficial_teaching_handout_reference",
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


class SupplementalVisualScanError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class SupplementalProductSpec:
    key: str
    product_relative: Path
    manifest_file_sha256: str
    product_id: str
    paper_id: str
    theme_id: str
    theme_sequence: int
    record_count: int
    printed_count: int
    crop_count: int
    source_count: int
    source_kind: str
    source_kind_zh: str
    paper_title_zh: str
    theme_title_zh: str
    source_boundary_zh: str


REGISTRY_SCHEMA_VERSION = "1.0.0-supplemental-product-registry"
REGISTRY_ID = "SHCHEM-SUPPLEMENTAL-VISUAL-SCAN-REGISTRY-2026-08-26-V1"
REGISTRY_RELATIVE = Path(
    "kb/classification/"
    "supplemental_visual_scan_registry_v1_2026-08-26/registry.json"
)
REGISTRY_SCHEMA_RELATIVE = Path(
    "kb/classification/"
    "supplemental_visual_scan_registry_v1_2026-08-26/registry.schema.json"
)
# These two trust anchors are updated only when a new frozen registry snapshot is activated.
REGISTRY_FILE_SHA256 = "37dcd5b08e26fd2b3c08145b8bbaa737a604d2080ee8fd21c320829a0d9b31e0"
REGISTRY_SCHEMA_SHA256 = "2aebe013162f13e4ff3ea9390317bb44074946d584ce2e2ad8fc5f5d4a8ea339"


@dataclass(frozen=True)
class _ProductSnapshot:
    spec: SupplementalProductSpec
    records: tuple[dict[str, Any], ...]
    by_node_id: dict[str, dict[str, Any]]
    crop_by_id: dict[str, dict[str, Any]]
    crop_bytes_by_id: dict[str, bytes]
    source_manifest: dict[str, Any]
    theme_summary: dict[str, Any]
    manifest_self_sha256: str
    manifest_file_sha256: str
    output_file_count: int
    source_file_count: int


@dataclass(frozen=True)
class _RegistrySnapshot:
    registry_id: str
    schema_version: str
    registry_file_sha256: str
    registry_self_sha256: str
    products: tuple[_ProductSnapshot, ...]
    by_node_id: dict[str, tuple[_ProductSnapshot, dict[str, Any]]]


def _source_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.casefold() in {
        "unknown",
        "not_applicable",
        "blocked_pending_review",
        "pending_review",
    }:
        return None
    return value


def _source_year(value: Any) -> str | None:
    if type(value) is int and 1900 <= value <= 2100:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"(?:19|20|21)[0-9]{2}", value):
        return value
    return None


def _year_from_explicit_date(value: Any) -> str | None:
    """Read only a dedicated date literal; paper titles and IDs are never parsed."""

    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"\s*((?:19|20|21)[0-9]{2})(?:[.\-/年][0-9]{1,2}(?:月)?|年)?\s*",
        value,
    )
    return match.group(1) if match else None


def _unique_known(values: list[str | None], label: str) -> str | None:
    known = {value for value in values if value is not None}
    if len(known) > 1:
        raise SupplementalVisualScanError(
            "supplemental_scan_source_metadata_conflict",
            f"supplemental {label} metadata conflicts within one product or paper",
        )
    return next(iter(known), None)


def _snapshot_source_metadata(snapshot: _ProductSnapshot) -> dict[str, str]:
    """Project only verified structured source fields from this frozen product."""

    manifest = snapshot.source_manifest
    source_boundary = manifest.get("source_boundary")
    source_boundary = source_boundary if isinstance(source_boundary, dict) else {}
    manifest_identity = manifest.get("identity_boundary")
    manifest_identity = manifest_identity if isinstance(manifest_identity, dict) else {}
    record_boundaries = [
        boundary
        for record in snapshot.records
        if isinstance((boundary := record.get("identity_boundary")), dict)
    ]
    boundaries = [source_boundary, manifest_identity, *record_boundaries]

    years: list[str | None] = []
    regions: list[str | None] = []
    paper_types: list[str | None] = []
    visibility: list[bool] = []
    second_mock_declared = False
    for boundary in boundaries:
        years.extend(
            (
                _source_year(boundary.get("year")),
                _source_year(boundary.get("calendar_year")),
                _year_from_explicit_date(boundary.get("paper_face_date_literal")),
            )
        )
        regions.extend(
            _source_text(boundary.get(key))
            for key in ("region", "district", "school")
        )
        paper_types.extend(
            _source_text(boundary.get(key))
            for key in ("paper_type", "exam_type")
        )
        for key in (
            "district_visible_on_paper_face",
            "region_face_verified",
            "paper_face_region_and_simulation_visible",
        ):
            value = boundary.get(key)
            if type(value) is bool:
                visibility.append(value)
        second_mock_declared = second_mock_declared or any(
            key.startswith("second_mock_") for key in boundary
        )

    region = _unique_known(regions, "region")
    if region is None:
        attribution = None
    elif False in visibility:
        attribution = "article_title_attribution_only"
    elif True in visibility:
        attribution = "direct_paper_pixel_evidence"
    else:
        attribution = None

    if snapshot.spec.source_kind == "external_teaching_handout":
        source_tier = "external_handout"
        paper_type = "external_handout"
        attribution = "not_exam_identity"
    else:
        source_tier = snapshot.spec.source_kind
        paper_type = _unique_known(paper_types, "paper type")
        if paper_type is None and second_mock_declared:
            paper_type = "second_mock_nonofficial_attribution"

    return {
        "source_tier": source_tier,
        "year": _unique_known(years, "year") or "unknown",
        "region": region or "unknown",
        "paper_type": paper_type or "unknown",
        "attribution_status": attribution or "unknown",
    }


def _merge_source_metadata(values: list[dict[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in (
        "source_tier",
        "year",
        "region",
        "paper_type",
        "attribution_status",
    ):
        result[key] = (
            _unique_known(
                [value.get(key) if value.get(key) != "unknown" else None for value in values],
                key,
            )
            or "unknown"
        )
    return result


class _FrozenDict(dict):
    """A dict-compatible recursively frozen value for the in-process snapshot."""

    @staticmethod
    def _blocked(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("supplemental registry snapshot is immutable")

    __setitem__ = _blocked
    __delitem__ = _blocked
    clear = _blocked
    pop = _blocked
    popitem = _blocked
    setdefault = _blocked
    update = _blocked
    __ior__ = _blocked

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[Any, Any]:
        return {
            deepcopy(key, memo): deepcopy(value, memo)
            for key, value in self.items()
        }


class _FrozenList(list):
    """A list-compatible recursively frozen value for validated record arrays."""

    @staticmethod
    def _blocked(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("supplemental registry snapshot is immutable")

    __setitem__ = _blocked
    __delitem__ = _blocked
    __iadd__ = _blocked
    __imul__ = _blocked
    append = _blocked
    clear = _blocked
    extend = _blocked
    insert = _blocked
    pop = _blocked
    remove = _blocked
    reverse = _blocked
    sort = _blocked

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        return [deepcopy(value, memo) for value in self]


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return _FrozenDict(
            (key, _deep_freeze(nested)) for key, nested in value.items()
        )
    if isinstance(value, list):
        return _FrozenList(_deep_freeze(nested) for nested in value)
    if isinstance(value, tuple):
        return tuple(_deep_freeze(nested) for nested in value)
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: Any, *, null_field: str | None = None) -> str:
    copied = deepcopy(value)
    if null_field is not None:
        copied[null_field] = None
    raw = json.dumps(
        copied, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256(raw)


def _safe_relative(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SupplementalVisualScanError(
            "supplemental_scan_path_invalid", "a relative path is malformed"
        )
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise SupplementalVisualScanError(
            "supplemental_scan_path_invalid", "a relative path leaves its root"
        )
    return relative


def _safe_routable_id(value: Any, label: str, code: str) -> str:
    if not isinstance(value, str):
        raise SupplementalVisualScanError(code, f"{label} is invalid")
    try:
        return validate_identifier(value, label)
    except SecurityError as exc:
        raise SupplementalVisualScanError(
            code, f"{label} is not HTTP-routable"
        ) from exc


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupplementalVisualScanError(
            "supplemental_scan_json_invalid", f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise SupplementalVisualScanError(
            "supplemental_scan_json_invalid", f"{label} is not a JSON object"
        )
    return value


def _quality_note(record: dict[str, Any]) -> str | None:
    answer_note = record.get("answer", {}).get("quality_note")
    if isinstance(answer_note, str) and answer_note.strip():
        return answer_note.strip()
    known = record.get("candidate_analysis", {}).get("known_quality_note_code")
    if isinstance(known, str) and known:
        return f"扫描记录含显式非阻断质量备注（{known}）；来源答案保持原样。"
    ambiguity = record.get("risks_and_limits", {}).get(
        "ambiguity_or_multiple_solutions_zh"
    )
    if isinstance(ambiguity, list) and any(
        isinstance(item, str) and item.strip() for item in ambiguity
    ):
        return "扫描记录含显式歧义或多解提示；来源答案保持原样。"
    return None


def _answer_projection(record: dict[str, Any]) -> dict[str, Any]:
    answer = record["answer"]
    availability = answer.get("availability")
    authority = answer.get("authority")
    if answer.get("answer_verified") is not False or answer.get(
        "independently_verified"
    ) is not False:
        raise SupplementalVisualScanError(
            "supplemental_scan_authority_escalation",
            "a source answer attempts to claim independent verification",
        )
    if availability == "present_part_aligned":
        text = answer.get("reference_summary_zh")
        if authority not in _ANSWER_AUTHORITIES or not isinstance(text, str) or not text:
            raise SupplementalVisualScanError(
                "supplemental_scan_answer_invalid",
                "an aligned source answer lacks exact nonofficial text",
            )
        if _FORBIDDEN_TEXT.search(text):
            raise SupplementalVisualScanError(
                "supplemental_scan_projection_leak",
                "source answer text contains a path or URL",
            )
        projected_text: str | None = text
        source_authority = authority
    elif availability == "present_unaligned":
        if authority not in _ANSWER_AUTHORITIES:
            raise SupplementalVisualScanError(
                "supplemental_scan_answer_invalid",
                "an unaligned source answer has unsupported authority",
            )
        projected_text = None
        source_authority = authority
    elif availability == "absent":
        if authority != "none":
            raise SupplementalVisualScanError(
                "supplemental_scan_answer_invalid",
                "an absent source answer must use none authority",
            )
        projected_text = None
        source_authority = "none"
    else:
        raise SupplementalVisualScanError(
            "supplemental_scan_answer_invalid", "answer availability is unsupported"
        )
    return {
        "availability": availability,
        "reference_answer_text": projected_text,
        "source_authority": source_authority,
        "independently_verified": False,
        "quality_note": _quality_note(record),
    }


def _answer_meta(record: dict[str, Any]) -> dict[str, Any]:
    projected = _answer_projection(record)
    return {
        "availability": projected["availability"],
        "source_authority": projected["source_authority"],
        "has_quality_note": projected["quality_note"] is not None,
    }


def _dependency(record: dict[str, Any]) -> dict[str, Any]:
    raw = record["dependency"]
    raw_kind = raw.get("dependency_kind")
    kind = {
        "shared_theme_context": "shared_material_only",
        "independent": "independent",
        "one_prior_part": "one_prior_part",
        "multiple_prior_parts": "multiple_prior_parts",
    }.get(raw_kind)
    if kind is None:
        raise SupplementalVisualScanError(
            "supplemental_scan_dependency_invalid",
            "a dependency kind is not controlled",
        )
    prior = raw.get("prior_atomic_part_ids")
    if not isinstance(prior, list) or len(prior) != len(set(prior)):
        raise SupplementalVisualScanError(
            "supplemental_scan_dependency_invalid", "prior dependency IDs are invalid"
        )
    if (
        raw_kind in {"independent", "shared_theme_context"}
        and prior
    ) or (raw_kind == "one_prior_part" and len(prior) != 1) or (
        raw_kind == "multiple_prior_parts" and len(prior) < 2
    ):
        raise SupplementalVisualScanError(
            "supplemental_scan_dependency_invalid",
            "dependency kind and prior-edge count are inconsistent",
        )
    return {
        "kind": kind,
        "prior_atomic_part_ids": list(prior),
        "explicit_prior_edge_count": len(prior),
        "status": "model_visual_scan_pending_human",
    }


class SupplementalVisualScanReader:
    def __init__(self, shchem_root: Path):
        self.shchem_root = shchem_root.resolve()
        self._snapshot_lock = Lock()
        self._registry_snapshot_cache: _RegistrySnapshot | None = None

    def _read_bound_registry_file(
        self, relative: Path, expected_sha256: str, label: str
    ) -> bytes:
        check_read_cancelled()
        path = self.shchem_root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise SupplementalVisualScanError(
                    "supplemental_registry_path_invalid",
                    f"{label} path contains a symbolic link",
                )
        resolved = path.resolve()
        if not resolved.is_relative_to(self.shchem_root) or not resolved.is_file():
            raise SupplementalVisualScanError(
                "supplemental_registry_unavailable", f"{label} is unavailable"
            )
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            raise SupplementalVisualScanError(
                "supplemental_registry_unavailable", f"{label} is unreadable"
            ) from exc
        if _sha256(raw) != expected_sha256:
            raise SupplementalVisualScanError(
                "supplemental_registry_drift",
                f"{label} does not match its activated SHA-256",
            )
        return raw

    def _load_registry_specs(
        self,
    ) -> tuple[dict[str, Any], tuple[SupplementalProductSpec, ...]]:
        schema_raw = self._read_bound_registry_file(
            REGISTRY_SCHEMA_RELATIVE,
            REGISTRY_SCHEMA_SHA256,
            "supplemental registry schema",
        )
        registry_raw = self._read_bound_registry_file(
            REGISTRY_RELATIVE,
            REGISTRY_FILE_SHA256,
            "supplemental registry",
        )
        schema = _json_object(schema_raw, "supplemental registry schema")
        registry = _json_object(registry_raw, "supplemental registry")
        try:
            validator = Draft202012Validator(schema)
            validator.check_schema(schema)
            errors = list(validator.iter_errors(registry))
        except Exception as exc:
            raise SupplementalVisualScanError(
                "supplemental_registry_schema_invalid",
                "supplemental registry schema is invalid",
            ) from exc
        if errors:
            raise SupplementalVisualScanError(
                "supplemental_registry_invalid",
                f"supplemental registry failed schema validation: {errors[0].message}",
            )
        if (
            registry.get("schema_version") != REGISTRY_SCHEMA_VERSION
            or registry.get("registry_id") != REGISTRY_ID
        ):
            raise SupplementalVisualScanError(
                "supplemental_registry_identity_invalid",
                "supplemental registry identity or schema version drifted",
            )
        self_sha = registry.get("self_sha256")
        if (
            not isinstance(self_sha, str)
            or not _SHA256.fullmatch(self_sha)
            or _canonical_sha256(registry, null_field="self_sha256") != self_sha
        ):
            raise SupplementalVisualScanError(
                "supplemental_registry_drift",
                "supplemental registry canonical self hash mismatches",
            )
        claims = registry.get("claims")
        expected_claims = {
            "candidate_only": True,
            "read_only": True,
            "human_reviewed": False,
            "official": False,
            "retrieval_ready": False,
            "teaching_use_allowed": False,
            "generation_allowed": False,
            "publication_allowed": False,
            "product_manifests_pinned": True,
            "dynamic_counts": True,
            "non_additive_to_master_and_wave1": True,
        }
        if claims != expected_claims:
            raise SupplementalVisualScanError(
                "supplemental_registry_authority_invalid",
                "supplemental registry authority or activation claims drifted",
            )
        products = registry.get("products")
        if (
            not isinstance(products, list)
            or registry.get("product_count") != len(products)
            or not products
        ):
            raise SupplementalVisualScanError(
                "supplemental_registry_count_invalid",
                "supplemental registry product count is inconsistent",
            )
        specs: list[SupplementalProductSpec] = []
        seen_keys: set[str] = set()
        seen_paths: set[str] = set()
        seen_product_ids: set[str] = set()
        seen_theme_ids: set[str] = set()
        seen_theme_slots: set[tuple[str, int]] = set()
        paper_metadata: dict[str, tuple[str, str, str, str]] = {}
        for product in products:
            relative = _safe_relative(product["product_relative"])
            path_key = relative.as_posix()
            slot = (product["paper_id"], product["theme_sequence"])
            if (
                product["key"] in seen_keys
                or path_key in seen_paths
                or product["product_id"] in seen_product_ids
                or product["theme_id"] in seen_theme_ids
                or slot in seen_theme_slots
            ):
                raise SupplementalVisualScanError(
                    "supplemental_registry_identity_invalid",
                    "supplemental registry contains a duplicate key, path, product, theme, or theme slot",
                )
            if product["record_count"] < product["printed_count"]:
                raise SupplementalVisualScanError(
                    "supplemental_registry_count_invalid",
                    "an atomic record count is smaller than its printed-question count",
                )
            public_text = (
                product["source_kind_zh"],
                product["paper_title_zh"],
                product["theme_title_zh"],
                product["source_boundary_zh"],
            )
            if any(_FORBIDDEN_TEXT.search(value) for value in public_text):
                raise SupplementalVisualScanError(
                    "supplemental_registry_projection_leak",
                    "supplemental registry public text contains a path or URL",
                )
            metadata = (
                product["source_kind"],
                product["source_kind_zh"],
                product["paper_title_zh"],
                product["source_boundary_zh"],
            )
            prior_metadata = paper_metadata.setdefault(product["paper_id"], metadata)
            if prior_metadata != metadata:
                raise SupplementalVisualScanError(
                    "supplemental_registry_identity_invalid",
                    "themes of one paper disagree on source or paper metadata",
                )
            seen_keys.add(product["key"])
            seen_paths.add(path_key)
            seen_product_ids.add(product["product_id"])
            seen_theme_ids.add(product["theme_id"])
            seen_theme_slots.add(slot)
            specs.append(
                SupplementalProductSpec(
                    key=product["key"],
                    product_relative=Path(*relative.parts),
                    manifest_file_sha256=product["manifest_file_sha256"],
                    product_id=product["product_id"],
                    paper_id=product["paper_id"],
                    theme_id=product["theme_id"],
                    theme_sequence=product["theme_sequence"],
                    record_count=product["record_count"],
                    printed_count=product["printed_count"],
                    crop_count=product["crop_count"],
                    source_count=product["source_count"],
                    source_kind=product["source_kind"],
                    source_kind_zh=product["source_kind_zh"],
                    paper_title_zh=product["paper_title_zh"],
                    theme_title_zh=product["theme_title_zh"],
                    source_boundary_zh=product["source_boundary_zh"],
                )
            )
        return registry, tuple(specs)

    def _build_registry_snapshot(self) -> _RegistrySnapshot:
        registry, specs = self._load_registry_specs()
        products = tuple(self._load_product(spec) for spec in specs)
        by_node_id: dict[str, tuple[_ProductSnapshot, dict[str, Any]]] = {}
        printed_owner: dict[str, str] = {}
        scan_ids: set[str] = set()
        for product in products:
            for node_id, record in product.by_node_id.items():
                if node_id in by_node_id:
                    raise SupplementalVisualScanError(
                        "supplemental_scan_identity_invalid",
                        "supplemental products overlap on an atomic-part identity",
                    )
                by_node_id[node_id] = (product, record)
                printed_id = record["hierarchy"]["printed_question_id"]
                owner = printed_owner.setdefault(printed_id, product.spec.product_id)
                scan_id = record["scan_id"]
                if owner != product.spec.product_id or scan_id in scan_ids:
                    raise SupplementalVisualScanError(
                        "supplemental_scan_identity_invalid",
                        "supplemental products overlap on a printed or scan identity",
                    )
                scan_ids.add(scan_id)
        expected_atomic = sum(spec.record_count for spec in specs)
        if len(by_node_id) != expected_atomic:
            raise SupplementalVisualScanError(
                "supplemental_scan_count_mismatch",
                "verified product records do not match the frozen registry",
            )
        return _RegistrySnapshot(
            registry_id=registry["registry_id"],
            schema_version=registry["schema_version"],
            registry_file_sha256=REGISTRY_FILE_SHA256,
            registry_self_sha256=registry["self_sha256"],
            products=products,
            by_node_id=_deep_freeze(by_node_id),
        )

    def _registry_snapshot(self) -> _RegistrySnapshot:
        check_read_cancelled()
        snapshot = self._registry_snapshot_cache
        if snapshot is not None:
            return snapshot
        with self._snapshot_lock:
            check_read_cancelled()
            snapshot = self._registry_snapshot_cache
            if snapshot is None:
                snapshot = self._build_registry_snapshot()
                check_read_cancelled()
                self._registry_snapshot_cache = snapshot
        return snapshot

    @property
    def product_specs(self) -> tuple[SupplementalProductSpec, ...]:
        return tuple(product.spec for product in self._registry_snapshot().products)

    def _product_root(self, spec: SupplementalProductSpec) -> Path:
        root = (self.shchem_root / spec.product_relative).resolve()
        if not root.is_relative_to(self.shchem_root):
            raise SupplementalVisualScanError(
                "supplemental_scan_path_invalid", "product root leaves sh-chem-db"
            )
        return root

    def _load_product(self, spec: SupplementalProductSpec) -> _ProductSnapshot:
        check_read_cancelled()
        root = self._product_root(spec)
        manifest_path = root / "manifest.json"
        try:
            manifest_raw = manifest_path.read_bytes()
        except OSError as exc:
            raise SupplementalVisualScanError(
                "supplemental_scan_unavailable", f"{spec.key} manifest is unavailable"
            ) from exc
        if _sha256(manifest_raw) != spec.manifest_file_sha256:
            raise SupplementalVisualScanError(
                "supplemental_scan_manifest_drift",
                f"{spec.key} manifest does not match its pinned file hash",
            )
        manifest = _json_object(manifest_raw, f"{spec.key} manifest")
        if (
            manifest.get("product_id") != spec.product_id
            or manifest.get("paper_id") != spec.paper_id
            or manifest.get("theme_id") != spec.theme_id
        ):
            raise SupplementalVisualScanError(
                "supplemental_scan_identity_invalid",
                f"{spec.key} manifest identity drifted",
            )
        self_sha = manifest.get("self_sha256")
        if not isinstance(self_sha, str) or not _SHA256.fullmatch(self_sha):
            raise SupplementalVisualScanError(
                "supplemental_scan_manifest_invalid", "manifest self hash is absent"
            )
        if _canonical_sha256(manifest, null_field="self_sha256") != self_sha:
            raise SupplementalVisualScanError(
                "supplemental_scan_manifest_drift", "manifest self hash mismatches"
            )
        outputs = manifest.get("outputs")
        if not isinstance(outputs, list) or len(outputs) != manifest.get(
            "output_file_count"
        ):
            raise SupplementalVisualScanError(
                "supplemental_scan_manifest_invalid", "output bindings are incomplete"
            )
        output_bytes: dict[str, bytes] = {}
        for binding in outputs:
            if not isinstance(binding, dict):
                raise SupplementalVisualScanError(
                    "supplemental_scan_manifest_invalid", "an output binding is malformed"
                )
            relative = _safe_relative(binding.get("path"))
            key = relative.as_posix()
            if key in output_bytes or key == "manifest.json":
                raise SupplementalVisualScanError(
                    "supplemental_scan_manifest_invalid", "output paths are duplicated"
                )
            path = root.joinpath(*relative.parts)
            if path.is_symlink() or not path.is_file():
                raise SupplementalVisualScanError(
                    "supplemental_scan_output_invalid", "a bound output is missing or linked"
                )
            raw = path.read_bytes()
            if (
                binding.get("bytes") != len(raw)
                or binding.get("sha256") != _sha256(raw)
            ):
                raise SupplementalVisualScanError(
                    "supplemental_scan_output_drift", f"output bytes drifted: {key}"
                )
            output_bytes[key] = raw
        actual_files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.relative_to(root).parts
            and ".pytest_cache" not in path.relative_to(root).parts
        }
        if actual_files != set(output_bytes) | {"manifest.json"}:
            raise SupplementalVisualScanError(
                "supplemental_scan_output_drift",
                "product recursive output closure contains unbound files",
            )
        required = {
            "scan_record_schema.json",
            "scan_records.jsonl",
            "source_manifest.json",
            "crop_manifest.json",
            "theme_summary.json",
        }
        if not required <= set(output_bytes):
            raise SupplementalVisualScanError(
                "supplemental_scan_manifest_invalid", "required product files are absent"
            )

        schema = _json_object(output_bytes["scan_record_schema.json"], "record schema")
        try:
            validator = Draft202012Validator(schema)
            validator.check_schema(schema)
        except Exception as exc:
            raise SupplementalVisualScanError(
                "supplemental_scan_schema_invalid", "record schema is invalid"
            ) from exc
        records: list[dict[str, Any]] = []
        try:
            text = output_bytes["scan_records.jsonl"].decode("utf-8")
            for line_number, line in enumerate(text.splitlines(), 1):
                if not line:
                    raise ValueError(f"blank line {line_number}")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError(f"non-object line {line_number}")
                errors = list(validator.iter_errors(value))
                if errors:
                    raise ValueError(
                        f"schema error line {line_number}: {errors[0].message}"
                    )
                records.append(value)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SupplementalVisualScanError(
                "supplemental_scan_records_invalid", "scan records failed schema validation"
            ) from exc
        if len(records) != spec.record_count:
            raise SupplementalVisualScanError(
                "supplemental_scan_count_mismatch", "record count drifted"
            )

        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source manifest"
        )
        sources = source_manifest.get("sources")
        if not isinstance(sources, list) or len(sources) != spec.source_count:
            raise SupplementalVisualScanError(
                "supplemental_scan_source_invalid", "source closure count drifted"
            )
        source_paths: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                raise SupplementalVisualScanError(
                    "supplemental_scan_source_invalid", "source binding is malformed"
                )
            relative = _safe_relative(source.get("path"))
            key = relative.as_posix()
            if key in source_paths:
                raise SupplementalVisualScanError(
                    "supplemental_scan_source_invalid", "source path is duplicated"
                )
            source_paths.add(key)
            path = self.shchem_root.joinpath(*relative.parts)
            if path.is_symlink() or not path.is_file():
                raise SupplementalVisualScanError(
                    "supplemental_scan_source_invalid", "a source is missing or linked"
                )
            raw = path.read_bytes()
            if source.get("bytes") != len(raw) or source.get("sha256") != _sha256(raw):
                raise SupplementalVisualScanError(
                    "supplemental_scan_source_drift", f"source bytes drifted: {key}"
                )

        crop_manifest = _json_object(
            output_bytes["crop_manifest.json"], "crop manifest"
        )
        crops = crop_manifest.get("crops")
        if not isinstance(crops, list) or len(crops) != spec.crop_count:
            raise SupplementalVisualScanError(
                "supplemental_scan_crop_invalid", "crop count drifted"
            )
        crop_by_id: dict[str, dict[str, Any]] = {}
        crop_bytes_by_id: dict[str, bytes] = {}
        product_prefix = spec.product_relative.as_posix() + "/"
        for crop in crops:
            if not isinstance(crop, dict):
                raise SupplementalVisualScanError(
                    "supplemental_scan_crop_invalid", "a crop binding is malformed"
                )
            crop_id = _safe_routable_id(
                crop.get("crop_id"), "crop_id", "supplemental_scan_crop_invalid"
            )
            if crop_id in crop_by_id:
                raise SupplementalVisualScanError(
                    "supplemental_scan_crop_invalid", "crop identity is invalid"
                )
            declared = crop.get("output_path", crop.get("path"))
            if isinstance(declared, str) and declared.startswith(product_prefix):
                declared = declared[len(product_prefix) :]
            relative = _safe_relative(declared)
            raw = output_bytes.get(relative.as_posix())
            if raw is None or crop.get("bytes") != len(raw) or crop.get(
                "sha256"
            ) != _sha256(raw):
                raise SupplementalVisualScanError(
                    "supplemental_scan_crop_drift", f"crop bytes drifted: {crop_id}"
                )
            if (
                len(raw) > MAX_CROP_BYTES
                or not raw.startswith(PNG_SIGNATURE)
                or not raw.endswith(b"IEND\xaeB`\x82")
            ):
                raise SupplementalVisualScanError(
                    "supplemental_scan_crop_invalid", "crop PNG is unsafe"
                )
            role = crop.get("evidence_role", crop.get("role"))
            exposable = crop.get("http_exposable") is True or (
                crop.get("safe_http_status_if_routed") == 200
                and crop.get("access_policy")
                == "internal_read_only_200_if_authorized"
            )
            if exposable and role not in {"question", "shared_material"}:
                raise SupplementalVisualScanError(
                    "supplemental_scan_crop_authority_invalid",
                    "a non-question crop attempts HTTP exposure",
                )
            normalized = deepcopy(crop)
            normalized["_role"] = role
            normalized["_http_exposable"] = exposable
            crop_by_id[crop_id] = normalized
            crop_bytes_by_id[crop_id] = raw

        by_node_id: dict[str, dict[str, Any]] = {}
        seen_printed: set[str] = set()
        position: dict[str, int] = {}
        printed_sequence_by_id: dict[str, int] = {}
        printed_id_by_sequence: dict[int, str] = {}
        atomic_sequences_by_printed: dict[str, set[int]] = defaultdict(set)
        record_order: list[tuple[int, int]] = []
        for index, record in enumerate(records):
            hierarchy = record.get("hierarchy")
            node_id = hierarchy.get("atomic_part_id") if isinstance(hierarchy, dict) else None
            node_id = _safe_routable_id(
                node_id, "node_id", "supplemental_scan_identity_invalid"
            )
            if (
                node_id in by_node_id
                or hierarchy.get("paper_id") != spec.paper_id
                or hierarchy.get("theme_id") != spec.theme_id
                or hierarchy.get("theme_sequence") != spec.theme_sequence
                or hierarchy.get("theme_title") != spec.theme_title_zh
            ):
                raise SupplementalVisualScanError(
                    "supplemental_scan_identity_invalid", "record parent identity drifted"
                )
            by_node_id[node_id] = record
            position[node_id] = index
            printed_id = hierarchy["printed_question_id"]
            printed_sequence = hierarchy["printed_sequence"]
            atomic_sequence = hierarchy["atomic_sequence_in_printed"]
            if (
                printed_sequence_by_id.get(printed_id, printed_sequence)
                != printed_sequence
                or printed_id_by_sequence.get(printed_sequence, printed_id)
                != printed_id
                or atomic_sequence in atomic_sequences_by_printed[printed_id]
            ):
                raise SupplementalVisualScanError(
                    "supplemental_scan_order_invalid",
                    "printed or atomic source order is ambiguous",
                )
            printed_sequence_by_id[printed_id] = printed_sequence
            printed_id_by_sequence[printed_sequence] = printed_id
            atomic_sequences_by_printed[printed_id].add(atomic_sequence)
            record_order.append((printed_sequence, atomic_sequence))
            seen_printed.add(printed_id)
            gates = record.get("authority_gates")
            if not isinstance(gates, dict) or any(
                gates.get(key) is not False
                for key in (
                    "human_reviewed",
                    "human_chemistry_reviewed",
                    "official",
                    "retrieval_ready",
                    "teaching_use_allowed",
                    "generation_allowed",
                    "publication_allowed",
                    "answer_verified",
                    "pixel_reuse_allowed",
                )
                if key in gates
            ):
                raise SupplementalVisualScanError(
                    "supplemental_scan_authority_escalation",
                    "a record authority gate is not false",
                )
            if spec.source_kind == "external_teaching_handout" and any(
                gates.get(key) is not False
                for key in (
                    "exam_candidate",
                    "input_eligible",
                    "master_direct_attachment_allowed",
                )
            ):
                raise SupplementalVisualScanError(
                    "supplemental_scan_authority_escalation",
                    "a handout attempts exam or Master eligibility",
                )
            factors = record.get("difficulty", {}).get("factors")
            if not isinstance(factors, list) or tuple(
                factor.get("dimension_id") for factor in factors if isinstance(factor, dict)
            ) != _FACTOR_IDS:
                raise SupplementalVisualScanError(
                    "supplemental_scan_difficulty_invalid",
                    "ten-factor difficulty evidence is incomplete or reordered",
                )
            for evidence in record.get("viewed_evidence", []):
                if not isinstance(evidence, dict):
                    raise SupplementalVisualScanError(
                        "supplemental_scan_evidence_invalid", "viewed evidence is malformed"
                    )
                crop = crop_by_id.get(evidence.get("crop_id"))
                if (
                    crop is None
                    or evidence.get("sha256") != crop.get("sha256")
                    or evidence.get("bytes") != crop.get("bytes")
                ):
                    raise SupplementalVisualScanError(
                        "supplemental_scan_evidence_invalid",
                        "viewed evidence is not bound to a crop",
                    )
            _answer_projection(record)
        if len(seen_printed) != spec.printed_count:
            raise SupplementalVisualScanError(
                "supplemental_scan_count_mismatch", "printed-question count drifted"
            )
        if record_order != sorted(record_order) or set(printed_id_by_sequence) != set(
            range(1, spec.printed_count + 1)
        ) or any(
            sequences != set(range(1, len(sequences) + 1))
            for sequences in atomic_sequences_by_printed.values()
        ):
            raise SupplementalVisualScanError(
                "supplemental_scan_order_invalid",
                "printed or atomic source order is not contiguous",
            )
        for node_id, record in by_node_id.items():
            prior = record["dependency"].get("prior_atomic_part_ids")
            if not isinstance(prior, list) or any(
                prior_id not in position or position[prior_id] >= position[node_id]
                for prior_id in prior
            ):
                raise SupplementalVisualScanError(
                    "supplemental_scan_dependency_invalid",
                    "a dependency is missing or points forward",
                )
            _dependency(record)

        theme_summary = _json_object(
            output_bytes["theme_summary.json"], "theme summary"
        )
        frozen_records = tuple(_deep_freeze(record) for record in records)
        frozen_by_node_id = {
            record["hierarchy"]["atomic_part_id"]: record
            for record in frozen_records
        }
        return _ProductSnapshot(
            spec=spec,
            records=frozen_records,
            by_node_id=_deep_freeze(frozen_by_node_id),
            crop_by_id=_deep_freeze(crop_by_id),
            crop_bytes_by_id=_deep_freeze(crop_bytes_by_id),
            source_manifest=_deep_freeze(source_manifest),
            theme_summary=_deep_freeze(theme_summary),
            manifest_self_sha256=self_sha,
            manifest_file_sha256=spec.manifest_file_sha256,
            output_file_count=len(outputs),
            source_file_count=len(sources),
        )

    def _snapshots(self) -> tuple[_ProductSnapshot, ...]:
        return self._registry_snapshot().products

    @staticmethod
    def _crop_page(crop: dict[str, Any]) -> int | None:
        value = crop.get("source_page", crop.get("source_page_number"))
        return value if isinstance(value, int) and value > 0 else None

    def _crop_refs(
        self, snapshot: _ProductSnapshot, record: dict[str, Any]
    ) -> dict[str, Any]:
        node_id = record["hierarchy"]["atomic_part_id"]
        items: list[dict[str, Any]] = []
        for evidence in record.get("viewed_evidence", []):
            crop_id = evidence["crop_id"]
            crop = snapshot.crop_by_id[crop_id]
            if not crop["_http_exposable"] or crop["_role"] not in {
                "question",
                "shared_material",
            }:
                continue
            items.append(
                {
                    "crop_id": crop_id,
                    "evidence_role": crop["_role"],
                    "source_page": self._crop_page(crop),
                    "sha256": crop["sha256"],
                    "crop_sha256": crop["sha256"],
                    "content_type": "image/png",
                    "access": "teacher_loopback_read_only",
                    "image_endpoint": (
                        "/api/v1/kb/workbench/supplemental-scans/"
                        f"{quote(node_id, safe='')}/question-crops/"
                        f"{quote(crop_id, safe='')}"
                    ),
                }
            )
        items.sort(
            key=lambda item: (
                0 if item["evidence_role"] == "question" else 1,
                str(item["source_page"]),
                item["crop_id"],
            )
        )
        return {
            "status": (
                "candidate_teacher_only_visual_evidence" if items else "blocked_by_product_policy"
            ),
            "count": len(items),
            "items": items,
            "paths_exposed": False,
            "human_reviewed": False,
        }

    def _item(
        self, snapshot: _ProductSnapshot, record: dict[str, Any]
    ) -> dict[str, Any]:
        spec = snapshot.spec
        hierarchy = record["hierarchy"]
        classification = record["classification"]
        node_id = hierarchy["atomic_part_id"]
        printed_count = Counter(
            row["hierarchy"]["printed_question_id"] for row in snapshot.records
        )[hierarchy["printed_question_id"]]
        atomic_label = (
            "整题"
            if printed_count == 1
            else f"作答单元 {hierarchy['atomic_sequence_in_printed']}"
        )
        answer_meta = _answer_meta(record)
        return {
            "source_namespace": "supplemental_visual_scan_candidate",
            "batch_id": spec.product_id,
            "product_id": spec.product_id,
            "source_kind": spec.source_kind,
            "source_kind_zh": spec.source_kind_zh,
            "node_type": "atomic_part",
            "node_id": node_id,
            "title_or_literal": record.get("visible_summary_zh"),
            "task_summary": record.get("visible_summary_zh"),
            "item_type": classification["item_type"],
            "classification": {
                "item_type": classification["item_type"],
                "selection_rule": classification["selection_rule"],
                "K": [classification["primary_K"], *classification["supporting_K"]],
                "A": list(classification["A"]),
                "C": list(classification["C"]),
                "R": list(classification["R"]),
                "RP": list(classification["RP"]),
            },
            "difficulty": {
                "cognitive_prelabel": record["difficulty"]["cognitive_prelabel"],
                "cognitive_evidence_status": "model_candidate_pending_human",
                "measured_difficulty": "blocked_pending_student_data",
                "calibration_status": "not_measured",
                "factors": deepcopy(record["difficulty"]["factors"]),
            },
            "classification_status": "model_visual_scan_pending_human",
            "review_status": "pending_human_review",
            "parent_chain": [
                {
                    "node_type": "paper",
                    "node_id": spec.paper_id,
                    "label": spec.paper_title_zh,
                    "order": None,
                },
                {
                    "node_type": "theme_big_question",
                    "node_id": spec.theme_id,
                    "label": spec.theme_title_zh,
                    "order": hierarchy["theme_sequence"],
                },
                {
                    "node_type": "printed_question",
                    "node_id": hierarchy["printed_question_id"],
                    "label": f"第 {hierarchy['printed_sequence']} 题",
                    "order": hierarchy["printed_sequence"],
                },
                {
                    "node_type": "atomic_part",
                    "node_id": node_id,
                    "label": atomic_label,
                    "order": hierarchy["atomic_sequence_in_printed"],
                },
            ],
            "source_refs": {
                "status": "local_paths_not_exposed",
                "count": snapshot.source_file_count,
                "items": [],
            },
            "crop_refs": self._crop_refs(snapshot, record),
            "answer_meta": answer_meta,
            "authority": dict(_AUTHORITY),
            "source_boundary_zh": spec.source_boundary_zh,
        }

    def _all_items(
        self, snapshots: tuple[_ProductSnapshot, ...]
    ) -> list[dict[str, Any]]:
        return [
            self._item(snapshot, record)
            for snapshot in snapshots
            for record in snapshot.records
        ]

    def status(self) -> dict[str, Any]:
        registry_snapshot = self._registry_snapshot()
        snapshots = registry_snapshot.products
        items = self._all_items(snapshots)
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": SCOPE,
            "data_snapshot_id": registry_snapshot.registry_file_sha256,
            "registry": {
                "registry_id": registry_snapshot.registry_id,
                "schema_version": registry_snapshot.schema_version,
                "file_sha256": registry_snapshot.registry_file_sha256,
                "self_sha256": registry_snapshot.registry_self_sha256,
                "product_count": len(registry_snapshot.products),
                "dynamic_counts": True,
            },
            "counts": {
                "papers": len({snapshot.spec.paper_id for snapshot in snapshots}),
                "theme_big_questions": len(snapshots),
                "printed_questions": sum(
                    snapshot.spec.printed_count for snapshot in snapshots
                ),
                "atomic_parts": len(items),
                "shanghai_exam_atomic_parts": sum(
                    len(snapshot.records)
                    for snapshot in snapshots
                    if snapshot.spec.source_kind == "shanghai_exam_wechat_archive"
                ),
                "external_handout_atomic_parts": sum(
                    len(snapshot.records)
                    for snapshot in snapshots
                    if snapshot.spec.source_kind == "external_teaching_handout"
                ),
                "question_pixels_available": sum(
                    1 for item in items if item["crop_refs"]["count"] > 0
                ),
                "answer_aligned": sum(
                    1
                    for item in items
                    if item["answer_meta"]["availability"] == "present_part_aligned"
                ),
                "answer_unaligned": sum(
                    1
                    for item in items
                    if item["answer_meta"]["availability"] == "present_unaligned"
                ),
                "answer_absent": sum(
                    1
                    for item in items
                    if item["answer_meta"]["availability"] == "absent"
                ),
                "quality_notes": sum(
                    1 for item in items if item["answer_meta"]["has_quality_note"]
                ),
            },
            "products": [
                {
                    "product_id": snapshot.spec.product_id,
                    "paper_id": snapshot.spec.paper_id,
                    "theme_id": snapshot.spec.theme_id,
                    "source_kind": snapshot.spec.source_kind,
                    "source_kind_zh": snapshot.spec.source_kind_zh,
                    "paper_title_zh": snapshot.spec.paper_title_zh,
                    "theme_title_zh": snapshot.spec.theme_title_zh,
                    "count": len(snapshot.records),
                    "source_boundary_zh": snapshot.spec.source_boundary_zh,
                }
                for snapshot in snapshots
            ],
            "authority": dict(_AUTHORITY),
            "integrity": {
                "registry_file_hash_pinned": True,
                "registry_schema_verified": True,
                "registry_self_hash_verified": True,
                "dynamic_counts_derived_from_verified_products": True,
                "manifest_file_hashes_pinned": True,
                "manifest_self_hashes_verified": True,
                "recursive_output_closure_verified": True,
                "source_hashes_verified_on_read": True,
                "record_schemas_verified_on_read": True,
                "crop_hashes_verified_on_read": True,
                "answer_images_excluded": True,
                "fail_closed": True,
            },
            "non_additivity": {
                "must_not_be_added_to_master470": True,
                "must_not_be_added_to_wave252": True,
                "handout_is_not_shanghai_exam": True,
            },
        }

    def list_atomic(self, *, limit: int, offset: int) -> dict[str, Any]:
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 1 <= limit <= 200
            or not 0 <= offset <= 100000
        ):
            raise SupplementalVisualScanError(
                "supplemental_scan_pagination_invalid", "pagination is invalid", 400
            )
        registry_snapshot = self._registry_snapshot()
        snapshots = registry_snapshot.products
        items = self._all_items(snapshots)
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": SCOPE,
            "data_snapshot_id": registry_snapshot.registry_file_sha256,
            "items": items[offset : offset + limit],
            "count": len(items[offset : offset + limit]),
            "total": len(items),
            "limit": limit,
            "offset": offset,
        }

    def _find(
        self, node_id: str
    ) -> tuple[_ProductSnapshot, dict[str, Any]]:
        try:
            validate_identifier(node_id, "node_id")
        except SecurityError as exc:
            raise SupplementalVisualScanError(exc.code, str(exc), exc.status) from exc
        found = self._registry_snapshot().by_node_id.get(node_id)
        if found is not None:
            return found
        raise SupplementalVisualScanError(
            "supplemental_scan_not_found", "supplemental atomic part was not found", 404
        )

    def detail(self, node_id: str) -> dict[str, Any]:
        registry_snapshot = self._registry_snapshot()
        snapshot, record = self._find(node_id)
        spec = snapshot.spec
        item = self._item(snapshot, record)
        evidence = []
        for descriptor in item["crop_refs"]["items"]:
            crop = snapshot.crop_by_id[descriptor["crop_id"]]
            evidence.append(
                {
                    "crop_id": descriptor["crop_id"],
                    "evidence_role": descriptor["evidence_role"],
                    "source_page": descriptor["source_page"],
                    "sha256": descriptor["sha256"],
                    "bytes": crop["bytes"],
                    "width": crop["width"],
                    "height": crop["height"],
                    "content_type": "image/png",
                    "access": "teacher_loopback_read_only",
                    "image_endpoint": descriptor["image_endpoint"],
                }
            )
        answer = _answer_projection(record)
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": SCOPE,
            "data_snapshot_id": registry_snapshot.registry_file_sha256,
            "node": item,
            "visual_scan": {
                "node_id": node_id,
                "product_id": spec.product_id,
                "scope": SCOPE,
                "paper_id": spec.paper_id,
                "source_kind": spec.source_kind,
                "source_kind_zh": spec.source_kind_zh,
                "source_boundary_zh": spec.source_boundary_zh,
                "scan_id": record["scan_id"],
                "scan_status": record["scan_status"],
                "visible_summary_zh": record.get("visible_summary_zh"),
                "response_requirement_zh": record.get("response_requirement_zh"),
                "scan_hierarchy": deepcopy(record["hierarchy"]),
                "scan_classification": deepcopy(record["classification"]),
                "cognitive_difficulty": deepcopy(record["difficulty"]),
                "dependency": deepcopy(record["dependency"]),
                "theme_chain_role": deepcopy(record["theme_chain_role"]),
                "chemistry_observations": deepcopy(record["chemistry_observations"]),
                "candidate_analysis": deepcopy(record["candidate_analysis"]),
                "risks_and_limits": deepcopy(record["risks_and_limits"]),
                "comparison_with_prior_candidate": deepcopy(
                    record["comparison_with_prior_candidate"]
                ),
                "reference_answer": answer,
                "answer_boundary": {
                    "availability": answer["availability"],
                    "authority": answer["source_authority"],
                    "verified": False,
                    "independently_verified": False,
                },
                "evidence_descriptors": evidence,
                "authority": dict(_AUTHORITY),
            },
        }

    def question_crop(self, node_id: str, crop_id: str) -> CandidateCropPayload:
        try:
            validate_identifier(crop_id, "crop_id")
        except SecurityError as exc:
            raise SupplementalVisualScanError(exc.code, str(exc), exc.status) from exc
        snapshot, record = self._find(node_id)
        allowed = {
            item["crop_id"] for item in self._crop_refs(snapshot, record)["items"]
        }
        crop = snapshot.crop_by_id.get(crop_id)
        if crop_id not in allowed or crop is None or crop["_role"] not in {
            "question",
            "shared_material",
        }:
            raise SupplementalVisualScanError(
                "supplemental_scan_crop_forbidden",
                "only this atomic part's safe question/shared crop may be read",
                403,
            )
        raw = snapshot.crop_bytes_by_id[crop_id]
        return CandidateCropPayload(data=raw, sha256=_sha256(raw))

    @staticmethod
    def _theme_context(snapshot: _ProductSnapshot) -> str:
        summary = snapshot.theme_summary
        direct = summary.get("theme_context_zh")
        if isinstance(direct, str) and direct:
            context = direct
        else:
            theme = summary.get("theme")
            title = theme.get("theme_title") if isinstance(theme, dict) else None
            context = (
                f"围绕“{title}”组织主题材料、实验或概念问题链。"
                if isinstance(title, str) and title
                else f"围绕“{snapshot.spec.theme_title_zh}”组织主题问题链。"
            )
        if _FORBIDDEN_TEXT.search(context):
            raise SupplementalVisualScanError(
                "supplemental_scan_projection_leak",
                "theme summary context contains a path or URL",
            )
        return context

    def _theme_group(self, snapshot: _ProductSnapshot) -> dict[str, Any]:
        spec = snapshot.spec
        material_users: dict[str, set[str]] = defaultdict(set)
        material_pages: dict[str, int | None] = {}
        atomic_chain: list[dict[str, Any]] = []
        pages: set[int] = set()
        for record in snapshot.records:
            hierarchy = record["hierarchy"]
            node_id = hierarchy["atomic_part_id"]
            for evidence in record.get("viewed_evidence", []):
                crop = snapshot.crop_by_id[evidence["crop_id"]]
                page = self._crop_page(crop)
                if isinstance(page, int):
                    pages.add(page)
                if crop["_role"] == "shared_material":
                    material_users[evidence["crop_id"]].add(node_id)
                    material_pages[evidence["crop_id"]] = page
            classification = record["classification"]
            dependency = _dependency(record)
            answer = _answer_meta(record)
            atomic_chain.append(
                {
                    "atomic_part_id": node_id,
                    "printed_question_id": hierarchy["printed_question_id"],
                    "printed_question_number": str(hierarchy["printed_sequence"]),
                    "printed_sequence": hierarchy["printed_sequence"],
                    "printed_sequence_status": "known_explicit",
                    "atomic_sequence_in_printed": hierarchy[
                        "atomic_sequence_in_printed"
                    ],
                    "atomic_sequence_status": "known_explicit",
                    "item_type": classification["item_type"],
                    "label_summary": {
                        "primary_K": classification["primary_K"],
                        "supporting_K": list(classification["supporting_K"]),
                        "A": list(classification["A"]),
                        "C": list(classification["C"]),
                        "R": list(classification["R"]),
                        "RP": list(classification["RP"]),
                        "cognitive_prelabel": record["difficulty"][
                            "cognitive_prelabel"
                        ],
                        "source": "supplemental_visual_scan",
                        "status": "model_candidate_pending_human",
                    },
                    "answer": answer,
                    "visual_coverage_kind": "supplemental_visual_scan",
                    "dependency": dependency,
                    "visible_summary_zh": record.get("visible_summary_zh"),
                    "response_requirement_zh": record.get(
                        "response_requirement_zh"
                    ),
                    "theme_chain_role": {
                        "value": record.get("theme_chain_role", {}).get("role_zh"),
                        "status": "model_candidate_pending_human",
                    },
                    "detail_endpoint": (
                        "/api/v1/kb/workbench/supplemental-scans/"
                        f"{quote(node_id, safe='')}"
                    ),
                    "alias_units": [],
                }
            )
        answer_counts = Counter(row["answer"]["availability"] for row in atomic_chain)
        dependency_counts = Counter(row["dependency"]["kind"] for row in atomic_chain)
        counts = {
            "printed": spec.printed_count,
            "atomic": spec.record_count,
            "display_atomic_units": spec.record_count,
            "visual_scanned": spec.record_count,
            "unscanned": 0,
            "label_complete": spec.record_count,
            "label_pending": 0,
            "answer_aligned": answer_counts["present_part_aligned"],
            "answer_unaligned": answer_counts["present_unaligned"],
            "answer_absent": answer_counts["absent"],
            "quality_notes": sum(
                1 for row in atomic_chain if row["answer"]["has_quality_note"]
            ),
        }
        paper = {
            "id": spec.paper_id,
            "title": spec.paper_title_zh,
            "source_metadata": _snapshot_source_metadata(snapshot),
            "order": None,
            "order_status": "independent_supplemental_source_order",
            "status": (
                "external_teaching_handout_theme_only_unreviewed"
                if spec.source_kind == "external_teaching_handout"
                else "observed_theme_one_article_classified_incomplete_paper"
            ),
            "observed_theme_count": 1,
            "missing_theme_note_zh": (
                "当前仅逐图整理该讲义的一个主题；讲义不是上海原题。"
                if spec.source_kind == "external_teaching_handout"
                else "当前只完成主题一逐图整理；不能称为整卷已扫描完成。"
            ),
        }
        return {
            "paper": paper,
            "theme": {
                "id": spec.theme_id,
                "title": spec.theme_title_zh,
                "sequence": spec.theme_sequence,
                "sequence_status": "known_explicit",
                "page_span": {
                    "start_page": min(pages) if pages else None,
                    "end_page": max(pages) if pages else None,
                    "page_numbers": sorted(pages),
                    "status": "explicit_visual_evidence_union",
                },
                "parent_chain_status": "complete_supplemental_parent_chain",
            },
            "counts": counts,
            "shared_context": {
                "context_summary_zh": self._theme_context(snapshot),
                "context_status": "visual_scan_candidate_context",
                "material_count": len(material_users),
                "materials": [
                    {
                        "material_id": crop_id,
                        "type": "shared_material",
                        "page": material_pages[crop_id],
                        "used_by_atomic_count": len(users),
                        "candidate_description_zh": (
                            "主题共享材料；已逐图查看，文字摘要待人工复核。"
                        ),
                        "preview_allowed": snapshot.crop_by_id[crop_id][
                            "_http_exposable"
                        ],
                    }
                    for crop_id, users in sorted(material_users.items())
                ],
            },
            "dependencies": {
                "independent": dependency_counts["independent"],
                "shared_material_only": dependency_counts["shared_material_only"],
                "one_prior_part": dependency_counts["one_prior_part"],
                "multiple_prior_parts": dependency_counts["multiple_prior_parts"],
                "per_alias_unit": 0,
                "blocked": 0,
                "explicit_prior_edge_count": sum(
                    row["dependency"]["explicit_prior_edge_count"]
                    for row in atomic_chain
                ),
            },
            "atomic_chain": atomic_chain,
        }

    def theme_groups(self) -> dict[str, Any]:
        registry_snapshot = self._registry_snapshot()
        snapshots = registry_snapshot.products
        groups = [self._theme_group(snapshot) for snapshot in snapshots]
        counts = Counter()
        for group in groups:
            counts.update(group["counts"])
        grouped: dict[str, list[tuple[_ProductSnapshot, dict[str, Any]]]] = (
            defaultdict(list)
        )
        for snapshot, group in zip(snapshots, groups, strict=True):
            grouped[snapshot.spec.paper_id].append((snapshot, group))
        paper_entries: list[dict[str, Any]] = []
        for entries in grouped.values():
            entries.sort(key=lambda entry: entry[0].spec.theme_sequence)
            first_spec = entries[0][0].spec
            source_metadata = _merge_source_metadata(
                [
                    _snapshot_source_metadata(snapshot)
                    for snapshot, _ in entries
                ]
            )
            observed_count = len(entries)
            if first_spec.source_kind == "external_teaching_handout":
                status = (
                    "external_teaching_handout_theme_only_unreviewed"
                    if observed_count == 1
                    else "external_teaching_handout_partial_themes_unreviewed"
                )
                missing = (
                    f"当前已逐图整理该讲义的 {observed_count} 个主题；"
                    "讲义不是上海原题，且仍未审定。"
                )
            else:
                status = (
                    "observed_theme_one_article_classified_incomplete_paper"
                    if observed_count == 1
                    else "observed_multiple_supplemental_themes_incomplete_paper"
                )
                missing = (
                    f"当前已逐图整理该卷的 {observed_count} 个主题；"
                    "其余主题尚未扫描，不能称为整卷完成。"
                )
            paper = {
                "id": first_spec.paper_id,
                "title": first_spec.paper_title_zh,
                "source_metadata": source_metadata,
                "order": None,
                "order_status": "independent_supplemental_source_order",
                "status": status,
                "observed_theme_count": observed_count,
                "missing_theme_note_zh": missing,
            }
            theme_groups = []
            for _, group in entries:
                projected = deepcopy(group)
                projected["paper"] = deepcopy(paper)
                theme_groups.append(projected)
            paper_entries.append(
                {"paper": deepcopy(paper), "theme_groups": theme_groups}
            )
        return {
            "schema_version": THEME_SCHEMA_VERSION,
            "scope": THEME_SCOPE,
            "data_snapshot_id": registry_snapshot.registry_file_sha256,
            "authority": dict(_AUTHORITY),
            "integrity": {
                "hash_verified_on_read": True,
                "semantic_invariants_verified_on_read": True,
                "complete_scope_coverage": True,
                "no_duplicate_atomic_parts": True,
                "explicit_order_only": True,
                "dependency_edges_validated": True,
                "answer_text_excluded": True,
                "pixel_reuse_allowed": False,
                "fail_closed": True,
            },
            "counts": {
                "papers": len(paper_entries),
                "theme_groups": len(groups),
                "atomic_parts": sum(group["counts"]["atomic"] for group in groups),
                "display_atomic_units": sum(
                    group["counts"]["display_atomic_units"] for group in groups
                ),
                "unassigned_atomic_parts": 0,
                "visual_scanned": counts["visual_scanned"],
                "unscanned": counts["unscanned"],
                "label_complete": counts["label_complete"],
                "label_pending": counts["label_pending"],
                "answer_aligned": counts["answer_aligned"],
                "answer_unaligned": counts["answer_unaligned"],
                "answer_absent": counts["answer_absent"],
                "quality_notes": counts["quality_notes"],
            },
            "papers": paper_entries,
            "unassigned_pending_review": {
                "status": "none",
                "reason_zh": None,
                "count": 0,
                "atomic_chain": [],
            },
        }


__all__ = [
    "REGISTRY_FILE_SHA256",
    "REGISTRY_ID",
    "REGISTRY_SCHEMA_SHA256",
    "REGISTRY_SCHEMA_VERSION",
    "SCOPE",
    "THEME_SCOPE",
    "SupplementalVisualScanError",
    "SupplementalVisualScanReader",
]

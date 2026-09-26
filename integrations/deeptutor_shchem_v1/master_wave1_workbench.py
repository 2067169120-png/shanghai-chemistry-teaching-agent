from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .public_kb import (
    ReadOnlyDataError,
    _checked_exact_path,
    _evidence_value,
    _tag_evidence,
)
from .reference_answer import ABSENT, NONE, project_reference_answer
from .security import SecurityError, validate_identifier

PRODUCT_ID = "MASTER-WAVE1-CROSSWALK-V1-2026-08-24"
PRODUCT_RELATIVE = Path("kb/classification/master_wave1_crosswalk_v1_2026-08-24")
EXPECTED_MANIFEST_SELF_SHA256 = (
    "2d3b5ef0710e0e8883213f130c4a8853462ec3f5268e824b7fcfc005594f70da"
)

MASTER_ROOT = "kb/classification/theme_hierarchy_master_index_v1_2026-08-03"
MASTER_FILES = {
    "paper": f"{MASTER_ROOT}/paper_records.jsonl",
    "theme_big_question": f"{MASTER_ROOT}/theme_big_question_records.jsonl",
    "printed_question": f"{MASTER_ROOT}/printed_question_records.jsonl",
    "atomic_part": f"{MASTER_ROOT}/atomic_part_records.jsonl",
    "question_anchor": f"{MASTER_ROOT}/question_anchor_records.jsonl",
}
MASTER_MANIFEST = f"{MASTER_ROOT}/manifest.json"
WAVE1_ROOT = "kb/formal/candidates/wave1_formalization_2026-08-04"
WAVE1_ATOMIC = f"{WAVE1_ROOT}/atomic_part_records.jsonl"
WAVE1_MANIFEST = f"{WAVE1_ROOT}/manifest.json"

EXPECTED_COUNTS = {
    "candidate_namespace_overlay_allowed": 165,
    "crosswalk_records": 252,
    "exact_1_to_1": 169,
    "exact_identity_mapped_label_overlay_blocked": 4,
    "master_atomic_inventory": 470,
    "matched_master_atomic_endpoints": 186,
    "unmatched_master_atomic_endpoints": 284,
    "verified_matches": 0,
    "wave1_atomic_inventory": 252,
    "wave1_unique_strong_keys": 231,
    "wave_atomic_to_master_anchor": 45,
    "wave_refines_master": 38,
    "wave_refines_master_unique_master_atomic": 17,
}
EXPECTED_MASTER_LAYER_COUNTS = {
    "paper": 24,
    "theme_big_question": 68,
    "printed_question": 501,
    "atomic_part": 470,
    "question_anchor": 139,
}
EXPECTED_OUTPUT_FILES = frozenset(
    {
        "README.md",
        "build_crosswalk.py",
        "coverage_summary.json",
        "crosswalk_records.jsonl",
        "crosswalk_schema.json",
        "run_mutation_tests.py",
        "test_crosswalk.py",
        "validate_crosswalk.py",
    }
)
EXPECTED_SOURCE_BINDING_COUNT = 31
EXPECTED_AUTHORITY_GATE_KEYS = frozenset(
    {
        "answer_verified",
        "catalog_update_allowed",
        "catalog_write_allowed",
        "chemistry_correctness_verified",
        "chemistry_reviewed",
        "component_registration_allowed",
        "component_reuse_allowed",
        "diagnosis_allowed",
        "difficulty_verified",
        "direct_source_pixel_reuse_allowed",
        "formal_ingest_allowed",
        "formal_promotion_allowed",
        "formalization_allowed",
        "generation_allowed",
        "human_chemistry_review_complete",
        "human_identity_reviewed",
        "human_review_complete",
        "human_reviewed",
        "human_source_reviewed",
        "human_taxonomy_reviewed",
        "human_visual_reviewed",
        "independent_model_reviewed",
        "measured_difficulty_verified",
        "official",
        "official_answer_claim_allowed",
        "official_scoring_claim_allowed",
        "publication_allowed",
        "retrieval_allowed",
        "retrieval_ready",
        "reuse_allowed",
        "rights_cleared",
        "rubric_verified",
        "teaching_use_allowed",
        "unattended_retrieval_allowed",
    }
)
EXPECTED_CONTRACTS = {
    "anchor_to_atomic_promotion_allowed": False,
    "candidate_namespace_only": True,
    "master_mutation_allowed": False,
    "master_source_read_only": True,
    "pixel_reuse_allowed": False,
    "split_to_single_master_atomic_overlay_allowed": False,
    "wave1_source_read_only": True,
}

AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "verified": False,
    "retrieval_allowed": False,
    "retrieval_ready": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "official": False,
    "pixel_reuse_allowed": False,
    "master_mutation_allowed": False,
    "wave1_mutation_allowed": False,
}


class MasterWave1WorkbenchError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _Snapshot:
    manifest_self_sha256: str
    output_binding_count: int
    source_binding_count: int
    master_layers: dict[str, list[dict[str, Any]]]
    master_nodes: dict[tuple[str, str], dict[str, Any]]
    wave_atomic: dict[str, dict[str, Any]]
    relations_by_master: dict[str, list[dict[str, Any]]]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _manifest_self_hash(manifest: dict[str, Any]) -> str:
    clone = deepcopy(manifest)
    clone["manifest_self_sha256"] = None
    return _sha256(_canonical_bytes(clone))


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MasterWave1WorkbenchError(
            "master_wave1_data_invalid", f"{label} is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise MasterWave1WorkbenchError(
            "master_wave1_data_invalid", f"{label} must be a JSON object"
        )
    return value


def _jsonl_rows(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MasterWave1WorkbenchError(
            "master_wave1_data_invalid", f"{label} is not valid UTF-8"
        ) from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MasterWave1WorkbenchError(
                "master_wave1_data_invalid",
                f"{label} contains invalid JSONL at line {line_number}",
            ) from exc
        if not isinstance(row, dict):
            raise MasterWave1WorkbenchError(
                "master_wave1_data_invalid", f"{label} row must be an object"
            )
        rows.append(row)
    return rows


def _false_gate_object(
    value: Any, label: str, *, exact_keys: frozenset[str] | None = None
) -> None:
    if not isinstance(value, dict) or not value:
        raise MasterWave1WorkbenchError(
            "master_wave1_gate_invalid", f"{label} protected gates are missing"
        )
    if exact_keys is not None and set(value) != exact_keys:
        raise MasterWave1WorkbenchError(
            "master_wave1_gate_invalid", f"{label} protected gate set drifted"
        )
    if any(item is not False for item in value.values()):
        raise MasterWave1WorkbenchError(
            "master_wave1_gate_elevated", f"{label} authority gate was elevated"
        )


def _binding_index(
    value: Any, label: str, *, expected_count: int | None = None
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise MasterWave1WorkbenchError(
            "master_wave1_binding_invalid", f"{label} bindings are missing"
        )
    if expected_count is not None and len(value) != expected_count:
        raise MasterWave1WorkbenchError(
            "master_wave1_binding_invalid", f"{label} binding count drifted"
        )
    indexed: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise MasterWave1WorkbenchError(
                "master_wave1_binding_invalid", f"{label} binding must be an object"
            )
        path = item.get("path")
        if (
            not isinstance(path, str)
            or not path
            or "\\" in path
            or PurePosixPath(path).is_absolute()
            or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
            or path in indexed
        ):
            raise MasterWave1WorkbenchError(
                "master_wave1_binding_invalid", f"{label} binding path is invalid"
            )
        if (
            type(item.get("bytes")) is not int
            or int(item["bytes"]) < 0
            or not isinstance(item.get("sha256"), str)
            or len(str(item["sha256"])) != 64
        ):
            raise MasterWave1WorkbenchError(
                "master_wave1_binding_invalid", f"{label} binding digest is invalid"
            )
        indexed[path] = item
    return indexed


def _binding_matches(
    binding: Any, source_bindings: dict[str, dict[str, Any]], label: str
) -> None:
    if not isinstance(binding, dict):
        raise MasterWave1WorkbenchError(
            "master_wave1_binding_invalid", f"{label} binding is missing"
        )
    path = binding.get("path")
    registered = source_bindings.get(path) if isinstance(path, str) else None
    if registered is None or any(
        binding.get(key) != registered.get(key) for key in ("path", "bytes", "sha256")
    ):
        raise MasterWave1WorkbenchError(
            "master_wave1_binding_mismatch",
            f"{label} binding does not match the source inventory",
        )


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise MasterWave1WorkbenchError(
            "master_wave1_data_invalid", f"{label} identifier is missing"
        )
    try:
        validate_identifier(value, label)
    except SecurityError as exc:
        raise MasterWave1WorkbenchError(
            "master_wave1_data_invalid", f"{label} identifier is invalid"
        ) from exc
    return value


def _scalar(value: Any) -> Any:
    projected = _evidence_value(value)
    return projected if isinstance(projected, (str, int, float, bool)) else None


def _label(kind: str, row: dict[str, Any]) -> Any:
    value = {
        "paper": row.get("paper_title"),
        "theme_big_question": row.get("theme_title"),
        "printed_question": row.get("printed_question_number_literal"),
        "atomic_part": row.get("printed_number_literal"),
    }[kind]
    return _scalar(value)


def _order(kind: str, row: dict[str, Any]) -> Any:
    value = row.get(
        {
            "paper": "paper_order",
            "theme_big_question": "theme_order",
            "printed_question": "printed_question_order",
            "atomic_part": "atomic_part_order",
        }[kind]
    )
    return _scalar(value)


class MasterWave1WorkbenchReader:
    """Strict runtime-verified, candidate-only master/Wave1 workbench reader."""

    def __init__(self, shchem_root: Path):
        self.shchem_root = shchem_root.absolute()
        self.product_root = (self.shchem_root / PRODUCT_RELATIVE).absolute()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise MasterWave1WorkbenchError(
                "master_wave1_path_escape",
                "crosswalk product escaped the chemistry root",
            )

    @staticmethod
    def _verify_bytes(
        root: Path, bindings: dict[str, dict[str, Any]]
    ) -> dict[str, bytes]:
        verified: dict[str, bytes] = {}
        for relative, binding in bindings.items():
            try:
                path = _checked_exact_path(root, relative)
                raw = path.read_bytes()
            except (OSError, ReadOnlyDataError) as exc:
                raise MasterWave1WorkbenchError(
                    "master_wave1_binding_unavailable",
                    "a bound artifact is unavailable",
                ) from exc
            if len(raw) != binding["bytes"] or _sha256(raw) != binding["sha256"]:
                raise MasterWave1WorkbenchError(
                    "master_wave1_hash_mismatch",
                    "a bound artifact failed bytes or SHA-256 verification",
                )
            verified[relative] = raw
        return verified

    @staticmethod
    def _verify_upstream_manifests(
        source_bytes: dict[str, bytes], source_bindings: dict[str, dict[str, Any]]
    ) -> None:
        master = _json_object(source_bytes[MASTER_MANIFEST], "master manifest")
        if master.get("status") != "PASS_THEME_HIERARCHY_MASTER_INDEX_GATES_CLOSED":
            raise MasterWave1WorkbenchError(
                "master_wave1_source_invalid", "master manifest status drifted"
            )
        inventory = master.get("counts", {}).get("atomic_item_type_inventory", {})
        if not isinstance(inventory, dict) or inventory.get("total") != 470:
            raise MasterWave1WorkbenchError(
                "master_wave1_count_mismatch", "master atomic inventory drifted"
            )
        master_outputs = _binding_index(master.get("output_bindings"), "master output")
        for relative in MASTER_FILES.values():
            registered = source_bindings[relative]
            upstream = master_outputs.get(relative)
            if upstream is None or any(
                upstream.get(key) != registered.get(key)
                for key in ("path", "bytes", "sha256")
            ):
                raise MasterWave1WorkbenchError(
                    "master_wave1_source_binding_mismatch",
                    "master output binding does not match the crosswalk source binding",
                )

        wave = _json_object(source_bytes[WAVE1_MANIFEST], "Wave1 manifest")
        if wave.get("status") != "ISOLATED_CANDIDATE_NOT_FORMAL_NOT_RETRIEVAL_READY":
            raise MasterWave1WorkbenchError(
                "master_wave1_source_invalid", "Wave1 manifest status drifted"
            )
        counts = wave.get("counts")
        if not isinstance(counts, dict) or counts.get("atomic_parts") != 252:
            raise MasterWave1WorkbenchError(
                "master_wave1_count_mismatch", "Wave1 atomic inventory drifted"
            )
        _false_gate_object(wave.get("gates"), "Wave1 manifest")
        artifacts = _binding_index(wave.get("generated_artifacts"), "Wave1 output")
        for relative in (
            WAVE1_ATOMIC,
            f"{WAVE1_ROOT}/paper_records.jsonl",
            f"{WAVE1_ROOT}/theme_big_question_records.jsonl",
            f"{WAVE1_ROOT}/printed_question_records.jsonl",
            f"{WAVE1_ROOT}/visual_crop_manifest.jsonl",
        ):
            basename = relative.removeprefix(f"{WAVE1_ROOT}/")
            registered = source_bindings[relative]
            upstream = artifacts.get(basename)
            if upstream is None or any(
                upstream.get(key) != registered.get(key) for key in ("bytes", "sha256")
            ):
                raise MasterWave1WorkbenchError(
                    "master_wave1_source_binding_mismatch",
                    "Wave1 output binding does not match the crosswalk source binding",
                )

    @staticmethod
    def _unique_rows(
        rows: list[dict[str, Any]], id_field: str, label: str
    ) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for row in rows:
            node_id = _identifier(row.get(id_field), id_field)
            if node_id in indexed:
                raise MasterWave1WorkbenchError(
                    "master_wave1_duplicate_node", f"duplicate {label} node"
                )
            gates = row.get("gates")
            _false_gate_object(gates, f"{label} row")
            indexed[node_id] = row
        return indexed

    @staticmethod
    def _verify_relation_rows(
        records: list[dict[str, Any]],
        source_bindings: dict[str, dict[str, Any]],
        master_nodes: dict[tuple[str, str], dict[str, Any]],
        wave_atomic: dict[str, dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        if len(records) != EXPECTED_COUNTS["crosswalk_records"]:
            raise MasterWave1WorkbenchError(
                "master_wave1_count_mismatch", "crosswalk row count drifted"
            )
        relation_counts: Counter[str] = Counter()
        relations_by_master: dict[str, list[dict[str, Any]]] = defaultdict(list)
        crosswalk_ids: set[str] = set()
        wave_ids: set[str] = set()
        strong_keys: set[tuple[str, str, str]] = set()
        label_blocked = 0
        overlay_allowed = 0
        split_masters: set[str] = set()
        matched_masters: set[str] = set()

        for row in records:
            crosswalk_id = _identifier(row.get("crosswalk_id"), "crosswalk_id")
            if crosswalk_id in crosswalk_ids:
                raise MasterWave1WorkbenchError(
                    "master_wave1_duplicate_relation", "duplicate crosswalk_id"
                )
            crosswalk_ids.add(crosswalk_id)
            relation = row.get("relation_type")
            if relation not in {
                "exact_1_to_1",
                "wave_refines_master",
                "wave_atomic_to_master_anchor",
            }:
                raise MasterWave1WorkbenchError(
                    "master_wave1_relation_invalid", "unsupported crosswalk relation"
                )
            relation_counts[relation] += 1
            if (
                row.get("candidate_only") is not True
                or row.get("matching_method") != "exact_strong_key_only"
                or row.get("suffix_or_fuzzy_matching_used") is not False
                or row.get("pixel_reuse_allowed") is not False
            ):
                raise MasterWave1WorkbenchError(
                    "master_wave1_relation_invalid", "crosswalk boundary drifted"
                )
            _false_gate_object(
                row.get("authority_gates"),
                "crosswalk row",
                exact_keys=EXPECTED_AUTHORITY_GATE_KEYS,
            )
            strong = row.get("strong_key")
            if not isinstance(strong, dict) or set(strong) != {
                "source_package_id",
                "source_part_file_sha256",
                "upstream_part_id",
            }:
                raise MasterWave1WorkbenchError(
                    "master_wave1_strong_key_invalid", "crosswalk strong key drifted"
                )
            key = (
                _identifier(strong.get("source_package_id"), "source_package_id"),
                str(strong.get("source_part_file_sha256")),
                _identifier(strong.get("upstream_part_id"), "upstream_part_id"),
            )
            if len(key[1]) != 64:
                raise MasterWave1WorkbenchError(
                    "master_wave1_strong_key_invalid", "strong key part hash is invalid"
                )
            strong_keys.add(key)
            for group_name in ("batch_bindings", "package_bindings"):
                group = row.get(group_name)
                if not isinstance(group, dict) or not group:
                    raise MasterWave1WorkbenchError(
                        "master_wave1_binding_invalid", "row bindings are missing"
                    )
                for binding_name, binding in group.items():
                    _binding_matches(
                        binding, source_bindings, f"{group_name}.{binding_name}"
                    )
            part_binding = row["package_bindings"].get("source_part_file")
            if part_binding.get("sha256") != key[1]:
                raise MasterWave1WorkbenchError(
                    "master_wave1_strong_key_invalid",
                    "strong key is not bound to its exact source part file",
                )

            checks = row.get("checks")
            required_green = {
                "all_identity_parent_crop_checks_green",
                "crop_manifest_cross_binding_match",
                "crop_original_source_sha256_match",
                "crop_page_match",
                "crop_path_match",
                "crop_sha256_match",
                "master_parent_chain_valid",
                "package_manifest_hash_match",
                "paper_source_identity_match",
                "part_file_hash_match",
                "strong_key_exact",
                "theme_parent_chain_match",
                "wave_parent_chain_valid",
            }
            if not isinstance(checks, dict) or any(
                checks.get(name) is not True for name in required_green
            ):
                raise MasterWave1WorkbenchError(
                    "master_wave1_check_invalid",
                    "a required crosswalk check is not green",
                )
            if checks.get("hierarchy_promotion_attempted") is not False:
                raise MasterWave1WorkbenchError(
                    "master_wave1_hierarchy_promotion",
                    "hierarchy promotion is forbidden",
                )
            crop_evidence = row.get("crop_evidence")
            if not isinstance(crop_evidence, list) or not crop_evidence:
                raise MasterWave1WorkbenchError(
                    "master_wave1_crop_binding_invalid",
                    "crosswalk crop evidence is missing",
                )
            for crop in crop_evidence:
                if not isinstance(crop, dict) or set(crop) != {
                    "crop_id",
                    "crop_path",
                    "crop_sha256",
                    "original_source_sha256",
                    "source_page_number",
                }:
                    raise MasterWave1WorkbenchError(
                        "master_wave1_crop_binding_invalid",
                        "crosswalk crop evidence drifted",
                    )
                crop_path = crop.get("crop_path")
                if (
                    not isinstance(crop_path, str)
                    or "\\" in crop_path
                    or PurePosixPath(crop_path).is_absolute()
                    or any(
                        part in {"", ".", ".."}
                        for part in PurePosixPath(crop_path).parts
                    )
                    or not crop_path.startswith(
                        "kb/formal/candidates/intake_round_2026-08-02/"
                    )
                    or len(str(crop.get("crop_sha256"))) != 64
                    or len(str(crop.get("original_source_sha256"))) != 64
                    or type(crop.get("source_page_number")) is not int
                ):
                    raise MasterWave1WorkbenchError(
                        "master_wave1_crop_binding_invalid",
                        "crosswalk crop evidence is invalid",
                    )

            master = row.get("master")
            wave = row.get("wave1")
            cardinality = row.get("endpoint_cardinality")
            if (
                not isinstance(master, dict)
                or not isinstance(wave, dict)
                or not isinstance(cardinality, dict)
            ):
                raise MasterWave1WorkbenchError(
                    "master_wave1_relation_invalid", "crosswalk endpoint is missing"
                )
            master_id = _identifier(master.get("endpoint_id"), "master_endpoint_id")
            wave_id = _identifier(wave.get("endpoint_id"), "wave1_endpoint_id")
            if wave_id in wave_ids or wave_id not in wave_atomic:
                raise MasterWave1WorkbenchError(
                    "master_wave1_relation_invalid",
                    "Wave1 endpoint cardinality drifted",
                )
            wave_ids.add(wave_id)
            if wave.get("endpoint_type") != "atomic_part":
                raise MasterWave1WorkbenchError(
                    "master_wave1_relation_invalid", "Wave1 endpoint level drifted"
                )

            if relation == "exact_1_to_1":
                expected_cardinality = {
                    "master_anchor_endpoints_for_strong_key": 0,
                    "master_atomic_endpoints_for_strong_key": 1,
                    "wave_rows_for_strong_key": 1,
                }
                if (
                    master.get("endpoint_type") != "atomic_part"
                    or cardinality != expected_cardinality
                    or row.get("identity_mapping_allowed") is not True
                    or master_id
                    not in {key[1] for key in master_nodes if key[0] == "atomic_part"}
                ):
                    raise MasterWave1WorkbenchError(
                        "master_wave1_cardinality_invalid",
                        "exact relation cardinality drifted",
                    )
                label_missing = (
                    checks.get("master_label_missing_wave_label_present") is True
                )
                if label_missing:
                    label_blocked += 1
                allowed = row.get("overlay_allowed") is True
                if allowed:
                    overlay_allowed += 1
                if (
                    allowed is not (row.get("label_overlay_allowed") is True)
                    or allowed is not (checks.get("label_match") is True)
                    or allowed is label_missing
                ):
                    raise MasterWave1WorkbenchError(
                        "master_wave1_overlay_invalid", "exact overlay gate drifted"
                    )
                matched_masters.add(master_id)
                relations_by_master[master_id].append(row)
            elif relation == "wave_refines_master":
                if (
                    master.get("endpoint_type") != "atomic_part"
                    or cardinality.get("master_atomic_endpoints_for_strong_key") != 1
                    or cardinality.get("master_anchor_endpoints_for_strong_key") != 0
                    or type(cardinality.get("wave_rows_for_strong_key")) is not int
                    or cardinality["wave_rows_for_strong_key"] < 2
                    or row.get("overlay_allowed") is not False
                    or row.get("label_overlay_allowed") is not False
                    or row.get("identity_mapping_allowed") is not False
                    or master_id
                    not in {key[1] for key in master_nodes if key[0] == "atomic_part"}
                ):
                    raise MasterWave1WorkbenchError(
                        "master_wave1_cardinality_invalid",
                        "split relation cardinality drifted",
                    )
                split_masters.add(master_id)
                matched_masters.add(master_id)
                relations_by_master[master_id].append(row)
            else:
                if (
                    master.get("endpoint_type") != "question_anchor"
                    or cardinality
                    != {
                        "master_anchor_endpoints_for_strong_key": 1,
                        "master_atomic_endpoints_for_strong_key": 0,
                        "wave_rows_for_strong_key": 1,
                    }
                    or checks.get("anchor_layer_preserved") is not True
                    or row.get("overlay_allowed") is not False
                    or row.get("label_overlay_allowed") is not False
                    or row.get("identity_mapping_allowed") is not False
                    or ("question_anchor", master_id) not in master_nodes
                ):
                    raise MasterWave1WorkbenchError(
                        "master_wave1_cardinality_invalid",
                        "anchor relation cardinality drifted",
                    )

        if relation_counts != Counter(
            {
                "exact_1_to_1": EXPECTED_COUNTS["exact_1_to_1"],
                "wave_refines_master": EXPECTED_COUNTS["wave_refines_master"],
                "wave_atomic_to_master_anchor": EXPECTED_COUNTS[
                    "wave_atomic_to_master_anchor"
                ],
            }
        ):
            raise MasterWave1WorkbenchError(
                "master_wave1_count_mismatch", "crosswalk relation counts drifted"
            )
        if (
            len(wave_ids) != EXPECTED_COUNTS["wave1_atomic_inventory"]
            or len(strong_keys) != EXPECTED_COUNTS["wave1_unique_strong_keys"]
            or len(split_masters)
            != EXPECTED_COUNTS["wave_refines_master_unique_master_atomic"]
            or len(matched_masters)
            != EXPECTED_COUNTS["matched_master_atomic_endpoints"]
            or overlay_allowed != EXPECTED_COUNTS["candidate_namespace_overlay_allowed"]
            or label_blocked
            != EXPECTED_COUNTS["exact_identity_mapped_label_overlay_blocked"]
        ):
            raise MasterWave1WorkbenchError(
                "master_wave1_count_mismatch", "derived crosswalk counts drifted"
            )
        for master_id in split_masters:
            rows = relations_by_master[master_id]
            declared = {
                row["endpoint_cardinality"]["wave_rows_for_strong_key"] for row in rows
            }
            keys = {
                (
                    row["strong_key"]["source_package_id"],
                    row["strong_key"]["source_part_file_sha256"],
                    row["strong_key"]["upstream_part_id"],
                )
                for row in rows
            }
            if len(declared) != 1 or declared != {len(rows)} or len(keys) != 1:
                raise MasterWave1WorkbenchError(
                    "master_wave1_cardinality_invalid", "split group membership drifted"
                )
        return dict(relations_by_master)

    def _snapshot(self) -> _Snapshot:
        try:
            manifest_path = _checked_exact_path(self.product_root, "manifest.json")
            manifest_raw = manifest_path.read_bytes()
        except (OSError, ReadOnlyDataError) as exc:
            raise MasterWave1WorkbenchError(
                "master_wave1_product_unavailable", "crosswalk product is unavailable"
            ) from exc
        manifest = _json_object(manifest_raw, "crosswalk manifest")
        computed_self_hash = _manifest_self_hash(manifest)
        if (
            manifest.get("product_id") != PRODUCT_ID
            or manifest.get("candidate_only") is not True
            or manifest.get("matching_method") != "exact_strong_key_only"
            or manifest.get("suffix_or_fuzzy_matching_used") is not False
            or manifest.get("status")
            != "PASS_CANDIDATE_ONLY_HASH_BOUND_CROSSWALK_GATES_CLOSED"
            or manifest.get("manifest_self_hash_contract")
            != "sha256(canonical UTF-8 JSON with manifest_self_sha256 set to null)"
            or manifest.get("manifest_self_sha256") != computed_self_hash
            or computed_self_hash != EXPECTED_MANIFEST_SELF_SHA256
        ):
            raise MasterWave1WorkbenchError(
                "master_wave1_manifest_invalid",
                "crosswalk manifest or self hash drifted",
            )
        if manifest.get("fixed_counts") != EXPECTED_COUNTS:
            raise MasterWave1WorkbenchError(
                "master_wave1_count_mismatch", "crosswalk fixed counts drifted"
            )
        if manifest.get("contracts") != EXPECTED_CONTRACTS:
            raise MasterWave1WorkbenchError(
                "master_wave1_contract_invalid", "crosswalk contracts drifted"
            )
        _false_gate_object(
            manifest.get("authority_gates"),
            "crosswalk manifest",
            exact_keys=EXPECTED_AUTHORITY_GATE_KEYS,
        )

        output_bindings = _binding_index(
            manifest.get("output_bindings"),
            "crosswalk output",
            expected_count=len(EXPECTED_OUTPUT_FILES),
        )
        if set(output_bindings) != EXPECTED_OUTPUT_FILES:
            raise MasterWave1WorkbenchError(
                "master_wave1_binding_invalid", "crosswalk output set drifted"
            )
        output_bytes = self._verify_bytes(self.product_root, output_bindings)
        source_bindings = _binding_index(
            manifest.get("source_bindings"),
            "crosswalk source",
            expected_count=EXPECTED_SOURCE_BINDING_COUNT,
        )
        required_sources = {
            MASTER_MANIFEST,
            *MASTER_FILES.values(),
            WAVE1_MANIFEST,
            WAVE1_ATOMIC,
            f"{WAVE1_ROOT}/paper_records.jsonl",
            f"{WAVE1_ROOT}/theme_big_question_records.jsonl",
            f"{WAVE1_ROOT}/printed_question_records.jsonl",
            f"{WAVE1_ROOT}/visual_crop_manifest.jsonl",
        }
        if not required_sources.issubset(source_bindings):
            raise MasterWave1WorkbenchError(
                "master_wave1_binding_invalid", "crosswalk source set is incomplete"
            )
        source_bytes = self._verify_bytes(self.shchem_root, source_bindings)
        self._verify_upstream_manifests(source_bytes, source_bindings)

        coverage = _json_object(
            output_bytes["coverage_summary.json"], "coverage summary"
        )
        if (
            coverage.get("product_id") != PRODUCT_ID
            or coverage.get("candidate_only") is not True
            or coverage.get("counts") != EXPECTED_COUNTS
            or coverage.get("non_additivity")
            != {
                "master_atomic_inventory": 470,
                "must_not_be_added_as_independent_inventory": True,
                "verified_match_count": 0,
                "wave1_atomic_inventory": 252,
            }
            or coverage.get("overlay_policy")
            != {
                "anchor_records_overlay_allowed": 0,
                "eligible_exact_label_consistent_records": 165,
                "master_label_missing_wave_label_records": 4,
                "master_mutation_allowed": False,
                "namespace": "candidate_only",
                "pixel_reuse_allowed": False,
                "split_records_overlay_allowed": 0,
            }
        ):
            raise MasterWave1WorkbenchError(
                "master_wave1_coverage_invalid", "crosswalk coverage contract drifted"
            )
        _false_gate_object(
            coverage.get("authority_gates"),
            "coverage summary",
            exact_keys=EXPECTED_AUTHORITY_GATE_KEYS,
        )

        master_layers: dict[str, list[dict[str, Any]]] = {}
        master_nodes: dict[tuple[str, str], dict[str, Any]] = {}
        id_fields = {
            "paper": "paper_id",
            "theme_big_question": "theme_big_question_id",
            "printed_question": "printed_question_id",
            "atomic_part": "atomic_part_id",
            "question_anchor": "question_anchor_id",
        }
        for kind, relative in MASTER_FILES.items():
            rows = _jsonl_rows(source_bytes[relative], f"master {kind}")
            if len(rows) != EXPECTED_MASTER_LAYER_COUNTS[kind]:
                raise MasterWave1WorkbenchError(
                    "master_wave1_count_mismatch", f"master {kind} count drifted"
                )
            indexed = self._unique_rows(rows, id_fields[kind], f"master {kind}")
            master_layers[kind] = rows
            master_nodes.update(
                {(kind, node_id): row for node_id, row in indexed.items()}
            )

        wave_rows = _jsonl_rows(source_bytes[WAVE1_ATOMIC], "Wave1 atomic")
        if len(wave_rows) != EXPECTED_COUNTS["wave1_atomic_inventory"]:
            raise MasterWave1WorkbenchError(
                "master_wave1_count_mismatch", "Wave1 atomic row count drifted"
            )
        wave_atomic = self._unique_rows(wave_rows, "atomic_part_id", "Wave1 atomic")
        records = _jsonl_rows(output_bytes["crosswalk_records.jsonl"], "crosswalk")
        relations_by_master = self._verify_relation_rows(
            records, source_bindings, master_nodes, wave_atomic
        )
        if (
            len(master_layers["atomic_part"]) - len(relations_by_master)
            != EXPECTED_COUNTS["unmatched_master_atomic_endpoints"]
        ):
            raise MasterWave1WorkbenchError(
                "master_wave1_count_mismatch", "unmapped master atomic count drifted"
            )

        return _Snapshot(
            manifest_self_sha256=computed_self_hash,
            output_binding_count=len(output_bindings),
            source_binding_count=len(source_bindings),
            master_layers=master_layers,
            master_nodes=master_nodes,
            wave_atomic=wave_atomic,
            relations_by_master=relations_by_master,
        )

    @staticmethod
    def _classification(row: dict[str, Any]) -> dict[str, Any]:
        axes = {
            "K": _tag_evidence(
                row.get("primary_knowledge_K"),
                row.get("supporting_knowledge_K"),
                row.get("knowledge_K_evidence"),
            ),
            "A": _tag_evidence(row.get("ability_A")),
            "C": _tag_evidence(row.get("context_C")),
            "R": _tag_evidence(row.get("response_R_evidence"), row.get("response_R")),
            "RP": _tag_evidence(
                row.get("representation_RP_evidence"), row.get("representation_RP")
            ),
            "D": _tag_evidence(row.get("difficulty")),
        }
        return axes

    @staticmethod
    def _node_summary(kind: str, row: dict[str, Any], node_id: str) -> dict[str, Any]:
        label = _label(kind, row)
        return {
            "node_type": kind,
            "node_id": node_id,
            "label": label,
            "label_status": "known" if label is not None else "unknown",
            "order": _order(kind, row),
        }

    def _parent_chain(
        self, atom: dict[str, Any], snapshot: _Snapshot
    ) -> dict[str, Any]:
        atomic_id = str(atom["atomic_part_id"])
        known: dict[str, tuple[str, dict[str, Any]]] = {
            "atomic_part": (atomic_id, atom)
        }
        missing: dict[str, Any] | None = None

        printed_id = atom.get("parent_printed_question_id")
        printed = (
            snapshot.master_nodes.get(("printed_question", printed_id))
            if isinstance(printed_id, str) and printed_id
            else None
        )
        if printed is None:
            missing = {
                "expected_parent_type": "printed_question",
                "node_id": printed_id if isinstance(printed_id, str) else None,
                "status": "unknown_pending_human_review",
            }
        else:
            known["printed_question"] = (str(printed_id), printed)
            theme_id = printed.get("parent_theme_big_question_id")
            theme = (
                snapshot.master_nodes.get(("theme_big_question", theme_id))
                if isinstance(theme_id, str) and theme_id
                else None
            )
            if theme is None:
                missing = {
                    "expected_parent_type": "theme_big_question",
                    "node_id": theme_id if isinstance(theme_id, str) else None,
                    "status": "unknown_pending_human_review",
                }
            else:
                known["theme_big_question"] = (str(theme_id), theme)
                paper_id = theme.get("parent_paper_id")
                paper = (
                    snapshot.master_nodes.get(("paper", paper_id))
                    if isinstance(paper_id, str) and paper_id
                    else None
                )
                if paper is None:
                    missing = {
                        "expected_parent_type": "paper",
                        "node_id": paper_id if isinstance(paper_id, str) else None,
                        "status": "unknown_pending_human_review",
                    }
                else:
                    known["paper"] = (str(paper_id), paper)

        ordered_types = (
            "paper",
            "theme_big_question",
            "printed_question",
            "atomic_part",
        )
        nodes = [
            self._node_summary(kind, known[kind][1], known[kind][0])
            for kind in ordered_types
            if kind in known
        ]
        return {
            "complete": missing is None and len(nodes) == 4,
            "status": "complete"
            if missing is None and len(nodes) == 4
            else "missing_parent_pending_review",
            "hierarchy_ids": {
                "paper_id": known.get("paper", (None,))[0],
                "theme_big_question_id": known.get("theme_big_question", (None,))[0],
                "printed_question_id": known.get("printed_question", (None,))[0],
                "atomic_part_id": atomic_id,
            },
            "nodes": nodes,
            "missing_parent": missing,
        }

    @staticmethod
    def _relation_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {
                "state": "unmapped",
                "relation_type": None,
                "relation_count": 0,
                "overlay_allowed": False,
                "label_overlay_allowed": False,
                "pixel_reuse_allowed": False,
            }
        relation = rows[0]["relation_type"]
        result = {
            "state": "exact" if relation == "exact_1_to_1" else "split",
            "relation_type": relation,
            "relation_count": len(rows),
            "overlay_allowed": all(row["overlay_allowed"] is True for row in rows),
            "label_overlay_allowed": all(
                row["label_overlay_allowed"] is True for row in rows
            ),
            "pixel_reuse_allowed": False,
            "wave1_node_ids": [row["wave1"]["endpoint_id"] for row in rows],
        }
        return result

    def _master_item(self, atom: dict[str, Any], snapshot: _Snapshot) -> dict[str, Any]:
        node_id = str(atom["atomic_part_id"])
        return {
            "node_type": "atomic_part",
            "node_id": node_id,
            "title_or_literal": _label("atomic_part", atom),
            "order": _order("atomic_part", atom),
            "source_layer": atom.get("source_layer")
            if isinstance(atom.get("source_layer"), str)
            else None,
            "item_type": _scalar(atom.get("item_type")) or atom.get("core_item_type"),
            "classification_status": atom.get("classification_status")
            if isinstance(atom.get("classification_status"), str)
            else "unknown",
            "classification": self._classification(atom),
            "parent_chain": self._parent_chain(atom, snapshot),
            "crosswalk_summary": self._relation_summary(
                snapshot.relations_by_master.get(node_id, [])
            ),
        }

    @staticmethod
    def _relation_detail(rows: list[dict[str, Any]]) -> dict[str, Any]:
        summary = MasterWave1WorkbenchReader._relation_summary(rows)
        result = {
            "namespace": "master_wave1_crosswalk_candidate_only",
            **summary,
            "records": [
                {
                    "crosswalk_id": row["crosswalk_id"],
                    "relation_type": row["relation_type"],
                    "master_node_id": row["master"]["endpoint_id"],
                    "wave1_node_id": row["wave1"]["endpoint_id"],
                    "endpoint_cardinality": dict(row["endpoint_cardinality"]),
                    "identity_mapping_allowed": row["identity_mapping_allowed"],
                    "overlay_allowed": row["overlay_allowed"],
                    "label_overlay_allowed": row["label_overlay_allowed"],
                    "pixel_reuse_allowed": False,
                    "unresolved_reasons": list(row.get("unresolved_reasons", [])),
                }
                for row in rows
            ],
        }
        return result

    @staticmethod
    def _wave_axis(value: Any) -> dict[str, Any]:
        items = []
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                items.append(
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "confidence": item.get("confidence"),
                        "review_status": item.get("review_status")
                        or "blocked_pending_review",
                    }
                )
        return {
            "candidate_values": items,
            "evidence_state": "candidate_pending_human" if items else "unknown",
            "human_verified": False,
        }

    @classmethod
    def _wave_overlay(
        cls, rows: list[dict[str, Any]], snapshot: _Snapshot
    ) -> dict[str, Any]:
        if not rows:
            return {"available": False, "reason": "master_atomic_unmapped"}
        row = rows[0]
        if row["relation_type"] != "exact_1_to_1":
            return {"available": False, "reason": "split_relation_overlay_forbidden"}
        if (
            row["overlay_allowed"] is not True
            or row["label_overlay_allowed"] is not True
        ):
            return {"available": False, "reason": "label_overlay_blocked"}
        node_id = row["wave1"]["endpoint_id"]
        wave = snapshot.wave_atomic[node_id]
        difficulty = wave.get("difficulty")
        difficulty = difficulty if isinstance(difficulty, dict) else {}
        cognitive_prelabel = difficulty.get("cognitive_prelabel")
        return {
            "available": True,
            "namespace": "wave1_candidate_overlay",
            "node_id": node_id,
            "classification": {
                "item_type": wave.get("item_type"),
                "item_type_status": wave.get("item_type_status")
                or "blocked_pending_review",
                "classification_status": wave.get("classification_status")
                or "blocked_pending_review",
                "K": cls._wave_axis(wave.get("knowledge_K")),
                "A": cls._wave_axis(wave.get("ability_A")),
                "C": cls._wave_axis(wave.get("context_C")),
                "R": cls._wave_axis(wave.get("response_R")),
                "RP": cls._wave_axis(wave.get("representation_RP")),
                "D": {
                    "candidate_values": [cognitive_prelabel]
                    if isinstance(cognitive_prelabel, str) and cognitive_prelabel
                    else [],
                    "evidence_state": "candidate_pending_human"
                    if cognitive_prelabel
                    else "unknown",
                    "human_verified": False,
                    "measured_difficulty": None,
                    "measured_difficulty_verified": False,
                },
            },
            "authority": dict(AUTHORITY),
        }

    @staticmethod
    def _integrity(snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "manifest_self_sha256": snapshot.manifest_self_sha256,
            "hash_verified_on_read": True,
            "source_binding_count": snapshot.source_binding_count,
            "output_binding_count": snapshot.output_binding_count,
            "fail_closed": True,
        }

    def visual_scan_identity_index(self) -> dict[str, Any]:
        """Return a freshly verified internal identity set for scan joins.

        This is deliberately not exposed by the existing master HTTP DTOs.  It
        lets an independent read-only scan layer prove membership and disjointness
        without weakening or reshaping the frozen 470-row workbench contract.
        """
        snapshot = self._snapshot()
        master_atomic_ids = {
            str(row["atomic_part_id"])
            for row in snapshot.master_layers["atomic_part"]
        }
        exact_master_to_wave1: dict[str, str] = {}
        for master_id, rows in snapshot.relations_by_master.items():
            if len(rows) != 1:
                continue
            row = rows[0]
            if (
                row.get("relation_type") == "exact_1_to_1"
                and row.get("identity_mapping_allowed") is True
                and row.get("endpoint_cardinality")
                == {
                    "master_anchor_endpoints_for_strong_key": 0,
                    "master_atomic_endpoints_for_strong_key": 1,
                    "wave_rows_for_strong_key": 1,
                }
            ):
                exact_master_to_wave1[master_id] = str(row["wave1"]["endpoint_id"])
        if (
            len(master_atomic_ids) != 470
            or len(exact_master_to_wave1) != 169
            or not set(exact_master_to_wave1).issubset(master_atomic_ids)
            or len(set(exact_master_to_wave1.values())) != 169
        ):
            raise MasterWave1WorkbenchError(
                "master_wave1_visual_identity_invalid",
                "master visual-scan identity index drifted",
            )
        return {
            "master_atomic_ids": frozenset(master_atomic_ids),
            "exact_master_ids": frozenset(exact_master_to_wave1),
            "exact_master_to_wave1": dict(exact_master_to_wave1),
            "integrity": self._integrity(snapshot),
        }

    def _exact_reference_answer_index(
        self, snapshot: _Snapshot
    ) -> dict[str, dict[str, Any]]:
        # Validate all five Wave1 scan batches once, then project only source
        # answer text across proven exact identities.  No evidence descriptor
        # or crop byte enters this mapping.
        from .question_visual_scan import QuestionVisualScanReader

        visual_reader = QuestionVisualScanReader(self.shchem_root)
        wave_answers: dict[str, dict[str, Any]] = {}
        for _, visual_snapshot in visual_reader._validated_catalog():
            for wave_node_id, record in visual_snapshot.by_node_id.items():
                wave_answers[wave_node_id] = project_reference_answer(
                    record["answer"], record["risks_and_limits"]
                )
        if len(wave_answers) != 252:
            raise MasterWave1WorkbenchError(
                "master_wave1_reference_answer_invalid",
                "Wave1 reference-answer inventory drifted",
            )
        result: dict[str, dict[str, Any]] = {}
        for master_id, rows in snapshot.relations_by_master.items():
            if len(rows) != 1:
                continue
            relation = rows[0]
            if (
                relation.get("relation_type") != "exact_1_to_1"
                or relation.get("identity_mapping_allowed") is not True
            ):
                continue
            wave1_node_id = str(relation["wave1"]["endpoint_id"])
            projection = wave_answers.get(wave1_node_id)
            if projection is None:
                raise MasterWave1WorkbenchError(
                    "master_wave1_reference_answer_invalid",
                    "an exact Wave1 answer projection is unavailable",
                )
            result[master_id] = deepcopy(projection)
        if len(result) != 169:
            raise MasterWave1WorkbenchError(
                "master_wave1_reference_answer_invalid",
                "exact reference-answer projection count drifted",
            )
        return result

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        complete = sum(
            self._parent_chain(row, snapshot)["complete"]
            for row in snapshot.master_layers["atomic_part"]
        )
        return {
            "product_id": PRODUCT_ID,
            "scope": "candidate_only_read_only_master_atomic_workbench",
            "counts": {
                "master_atomic_inventory": 470,
                "wave1_atomic_inventory_non_additive": 252,
                "exact_identity": 169,
                "candidate_overlay_allowed": 165,
                "label_overlay_blocked": 4,
                "split_wave_rows": 38,
                "split_master_targets": 17,
                "anchor_wave_rows": 45,
                "unmapped_master_atomic": 284,
                "verified_matches": 0,
                "complete_master_parent_chains": complete,
                "incomplete_master_parent_chains": 470 - complete,
            },
            "non_additivity": {
                "master_atomic_inventory": 470,
                "wave1_atomic_inventory": 252,
                "must_not_be_added": True,
                "reason": "Wave1 is a candidate-only refinement/crosswalk view over bound source identities, not independent master inventory",
            },
            "anchor_boundary": {
                "wave_rows": 45,
                "master_endpoint_type": "question_anchor",
                "included_in_master_atomic_details": False,
                "overlay_allowed": False,
            },
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def list_atomic(self, *, limit: int, offset: int) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise MasterWave1WorkbenchError(
                "master_wave1_invalid_pagination",
                "limit must be between 1 and 200",
                400,
            )
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise MasterWave1WorkbenchError(
                "master_wave1_invalid_pagination",
                "offset must be between 0 and 10000",
                400,
            )
        snapshot = self._snapshot()
        rows = snapshot.master_layers["atomic_part"]
        page = rows[offset : offset + limit]
        items = [self._master_item(row, snapshot) for row in page]
        return {
            "product_id": PRODUCT_ID,
            "scope": "candidate_only_read_only_master_atomic_workbench",
            "items": items,
            "count": len(items),
            "total": len(rows),
            "limit": limit,
            "offset": offset,
            "read_only": True,
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def atomic_detail(self, node_id: str) -> dict[str, Any]:
        try:
            validate_identifier(node_id, "node_id")
        except SecurityError as exc:
            raise MasterWave1WorkbenchError(
                "master_wave1_invalid_node_id", "master atomic node_id is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        atom = snapshot.master_nodes.get(("atomic_part", node_id))
        if atom is None:
            raise MasterWave1WorkbenchError(
                "master_wave1_node_not_found", "master atomic node was not found", 404
            )
        rows = snapshot.relations_by_master.get(node_id, [])
        reference_answer = {
            "availability": ABSENT,
            "reference_answer_text": None,
            "source_authority": NONE,
            "independently_verified": False,
            "quality_note": (
                "该主索引节点没有安全的Wave1 exact映射；不继承答案或题面像素。"
            ),
        }
        if len(rows) == 1:
            relation = rows[0]
            if (
                relation.get("relation_type") == "exact_1_to_1"
                and relation.get("identity_mapping_allowed") is True
            ):
                reference_answer = self._exact_reference_answer_index(snapshot)[
                    node_id
                ]
        return {
            "product_id": PRODUCT_ID,
            "scope": "candidate_only_read_only_master_atomic_workbench",
            "node": self._master_item(atom, snapshot),
            "crosswalk_relation": self._relation_detail(rows),
            "wave1_candidate_overlay": self._wave_overlay(rows, snapshot),
            "reference_answer": reference_answer,
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

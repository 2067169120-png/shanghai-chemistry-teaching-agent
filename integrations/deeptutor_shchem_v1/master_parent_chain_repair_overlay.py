from __future__ import annotations

"""Fail-closed, read-only sidecar for the Datong Master parent-chain repair.

The sidecar deliberately does not mutate or reshape the canonical Master470
workbench.  Its denominator remains 470 (427 complete + 43 pending); the 56
candidate effective atomic units live only below the 43 original Master rows.
"""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

PRODUCT_ID = "MASTER-PARENT-CHAIN-REPAIR-DT2025-H1-MID-V1-2026-08-27"
PRODUCT_RELATIVE = Path(
    "kb/classification/"
    "master_parent_chain_repair_datong_2025_h1_mid_v1_2026-08-27"
)
CENTRAL_RELATIVE = Path(
    "kb/classification/theme_hierarchy_master_index_v1_2026-08-03"
)

EXPECTED_PRODUCT_MANIFEST_BYTES = 4164
EXPECTED_PRODUCT_MANIFEST_SHA256 = (
    "2268a1ab02515351472676a313e70bdd8f6c04b9698fc6c29e396090b8a1900c"
)
EXPECTED_PRODUCT_PAYLOAD_SHA256 = (
    "0d0020f2c0ab8128f0b8657c5b7b58eee15d1cbb2deb4a8fb1776601b207e718"
)
EXPECTED_CENTRAL_MANIFEST_BYTES = 100154
EXPECTED_CENTRAL_MANIFEST_SHA256 = (
    "ae55367aa57dad65cc5d7eb5e25b14531d214745b700fa1e099bb762bb1c9e7c"
)

EXPECTED_PRODUCT_OUTPUTS = frozenset(
    {
        "README.md",
        "product_config.json",
        "repair_record_schema.json",
        "repair_records.jsonl",
        "theme_batches.json",
        "master_repair_projection.json",
        "coverage_report.json",
        "textbook_mapping_summary.json",
        "answer_alignment_summary.json",
        "unresolved_issues.json",
        "source_manifest.json",
        "build_product.py",
        "validate_parent_chain_repair.py",
        "run_mutation_tests.py",
        "test_parent_chain_repair.py",
    }
)
EXPECTED_CENTRAL_OUTPUTS = frozenset(
    {
        f"{CENTRAL_RELATIVE.as_posix()}/paper_records.jsonl",
        f"{CENTRAL_RELATIVE.as_posix()}/theme_big_question_records.jsonl",
        f"{CENTRAL_RELATIVE.as_posix()}/printed_question_records.jsonl",
        f"{CENTRAL_RELATIVE.as_posix()}/atomic_part_records.jsonl",
        f"{CENTRAL_RELATIVE.as_posix()}/question_anchor_records.jsonl",
        f"{CENTRAL_RELATIVE.as_posix()}/cross_layer_aliases.jsonl",
        f"{CENTRAL_RELATIVE.as_posix()}/overlap_groups.jsonl",
        f"{CENTRAL_RELATIVE.as_posix()}/theme_profiles.jsonl",
        f"{CENTRAL_RELATIVE.as_posix()}/human_review_queue.jsonl",
        f"{CENTRAL_RELATIVE.as_posix()}/coverage_summary.json",
    }
)

EXPECTED_COUNTS = {
    "papers": 1,
    "theme_groups": 5,
    "printed_questions": 41,
    "source_master_atomics": 43,
    "effective_atomics": 56,
    "split_source_master_atomics": 10,
    "dependency_edges": 8,
    "difficulty_factors": 560,
}
EXPECTED_TEXTBOOK_MAPPING_ENTRIES = 82
EXPECTED_CENTRAL_COUNTS = {
    "master_atomic_total": 470,
    "complete_parent_chain_atomics": 427,
    "original_pending_atomics": 43,
}
EXPECTED_BATCH_COUNTS = {
    1: {
        "printed_questions": 9,
        "source_master_atomics": 9,
        "effective_atomics": 15,
        "split_source_master_atomics": 4,
        "dependency_edges": 2,
        "page_span": [1, 2],
    },
    2: {
        "printed_questions": 9,
        "source_master_atomics": 9,
        "effective_atomics": 11,
        "split_source_master_atomics": 2,
        "dependency_edges": 2,
        "page_span": [2, 3],
    },
    3: {
        "printed_questions": 10,
        "source_master_atomics": 10,
        "effective_atomics": 12,
        "split_source_master_atomics": 2,
        "dependency_edges": 1,
        "page_span": [3, 4],
    },
    4: {
        "printed_questions": 6,
        "source_master_atomics": 8,
        "effective_atomics": 9,
        "split_source_master_atomics": 1,
        "dependency_edges": 1,
        "page_span": [4, 5],
    },
    5: {
        "printed_questions": 7,
        "source_master_atomics": 7,
        "effective_atomics": 9,
        "split_source_master_atomics": 1,
        "dependency_edges": 2,
        "page_span": [5, 6],
    },
}
EXPECTED_FACTOR_IDS = (
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
EXPECTED_AUTHORITY_GATE_KEYS = frozenset(
    {
        "answer_verified",
        "generation_allowed",
        "human_chemistry_reviewed",
        "human_reviewed",
        "measured_difficulty_verified",
        "official",
        "pixel_reuse_allowed",
        "publication_allowed",
        "retrieval_allowed",
        "retrieval_ready",
        "rubric_verified",
        "teaching_use_allowed",
        "verified",
    }
)
AUTHORITY = {
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
    "central_master_modified": False,
}

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_DTO_KEYS = frozenset(
    {
        "answer_text",
        "reference_answer_text",
        "solution_text",
        "source_url",
        "url",
        "path",
        "local_path",
        "source_path",
        "crop_path",
        "sha256",
        "hash",
    }
)
_FORBIDDEN_DTO_STRING = re.compile(
    r"(?i:https?://|file:/+|(?<![a-z0-9])[a-z]:[\\/]"
    r"|\\\\[^\\/\s]+[\\/]"
    r"|(?<![A-Za-z0-9_./\\])(?:sh-chem-db/|kb/|staging/|runtime/|integrations/))"
)


class MasterParentChainRepairOverlayError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _Snapshot:
    records: tuple[dict[str, Any], ...]
    batches: tuple[dict[str, Any], ...]
    projection: dict[str, Any]
    coverage: dict[str, Any]
    central_papers: dict[str, dict[str, Any]]
    central_themes: dict[str, dict[str, Any]]
    central_printed: dict[str, dict[str, Any]]
    central_atomics: dict[str, dict[str, Any]]
    central_unassigned_ids: frozenset[str]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256(raw)


def _duplicate_rejector(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("UTF-8 BOM is not accepted")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_duplicate_rejector,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_json_invalid",
            f"{label} is not strict UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_json_invalid",
            f"{label} must be a JSON object",
        )
    return value


def _strict_jsonl(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("UTF-8 BOM is not accepted")
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_jsonl_invalid",
            f"{label} is not strict UTF-8 JSONL",
        ) from exc
    lines = text.splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_jsonl_invalid",
            f"{label} contains an empty JSONL row",
        )
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        try:
            value = json.loads(
                line,
                object_pairs_hook=_duplicate_rejector,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_jsonl_invalid",
                f"{label} row {line_number} is not strict JSON",
            ) from exc
        if not isinstance(value, dict):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_jsonl_invalid",
                f"{label} row {line_number} must be a JSON object",
            )
        rows.append(value)
    return rows


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_binding_invalid", f"{label} path is invalid"
        )
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_binding_invalid", f"{label} path is invalid"
        )
    return relative


def _read_exact(root: Path, relative: str, label: str) -> bytes:
    pure = _safe_relative(relative, label)
    try:
        root_resolved = root.resolve(strict=True)
        if root.is_symlink() or not root_resolved.is_dir():
            raise OSError("root is not an exact directory")
        cursor = root
        for part in pure.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise OSError("symlink is forbidden")
        resolved = cursor.resolve(strict=True)
        if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
            raise OSError("file escaped its fixed root")
        return resolved.read_bytes()
    except OSError as exc:
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_product_unavailable",
            f"{label} is unavailable",
        ) from exc


def _binding_index(
    rows: Any,
    *,
    expected_paths: frozenset[str],
    label: str,
    central: bool,
) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != len(expected_paths):
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_binding_invalid",
            f"{label} output binding count drifted",
        )
    result: dict[str, dict[str, Any]] = {}
    expected_keys = {"path", "bytes", "sha256", "role"} if central else {
        "path",
        "bytes",
        "sha256",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_binding_invalid",
                f"{label} output binding shape drifted",
            )
        path = row.get("path")
        size = row.get("bytes")
        digest = row.get("sha256")
        _safe_relative(path, label)
        if (
            path in result
            or path not in expected_paths
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or (central and row.get("role") != "generated_output")
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_binding_invalid",
                f"{label} output binding is invalid",
            )
        result[path] = row
    if set(result) != expected_paths:
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_binding_invalid",
            f"{label} output set drifted",
        )
    return result


def _verify_outputs(
    root: Path,
    bindings: dict[str, dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for relative, binding in bindings.items():
        raw = _read_exact(root, relative, f"{label} {relative}")
        if len(raw) != binding["bytes"] or _sha256(raw) != binding["sha256"]:
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_output_drift",
                f"{label} output bytes drifted",
            )
        if relative.endswith(".json"):
            parsed[relative] = _strict_json(raw, f"{label} {relative}")
        elif relative.endswith(".jsonl"):
            parsed[relative] = _strict_jsonl(raw, f"{label} {relative}")
    return parsed


def _index(
    rows: Iterable[dict[str, Any]], id_field: str, label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        node_id = row.get(id_field)
        if not isinstance(node_id, str) or not node_id or node_id in result:
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_central_identity_invalid",
                f"{label} identity inventory drifted",
            )
        result[node_id] = row
    return result


def _all_false(value: Any, *, exact_keys: frozenset[str] | None = None) -> bool:
    return (
        isinstance(value, dict)
        and (exact_keys is None or set(value) == exact_keys)
        and bool(value)
        and all(item is False for item in value.values())
    )


def _known_int(value: Any, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_order_invalid", f"{label} is invalid"
        )
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_record_invalid", f"{label} is invalid"
        )
    return list(value)


def _reject_unsafe_dto(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).casefold()
            if (
                lowered in _FORBIDDEN_DTO_KEYS
                or lowered.endswith(("_path", "_url", "_sha256", "_hash"))
                or "answer_text" in lowered
            ):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_projection_leak",
                    "candidate sidecar contains a forbidden locator, digest, or answer body",
                )
            _reject_unsafe_dto(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_unsafe_dto(nested)
    elif isinstance(value, str) and _FORBIDDEN_DTO_STRING.search(value):
        raise MasterParentChainRepairOverlayError(
            "master_parent_repair_projection_leak",
            "candidate sidecar contains a forbidden local locator or URL",
        )


class MasterParentChainRepairOverlayReader:
    """Read and verify the candidate repair on every call."""

    def __init__(self, shchem_root: Path):
        self.shchem_root = shchem_root.resolve()
        self.product_root = self.shchem_root / PRODUCT_RELATIVE
        self.central_root = self.shchem_root
        if not self.product_root.resolve().is_relative_to(self.shchem_root):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_path_escape",
                "candidate repair product escaped the chemistry root",
            )

    def _central_snapshot(
        self,
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        frozenset[str],
    ]:
        relative = f"{CENTRAL_RELATIVE.as_posix()}/manifest.json"
        raw = _read_exact(self.central_root, relative, "central Master manifest")
        if (
            len(raw) != EXPECTED_CENTRAL_MANIFEST_BYTES
            or _sha256(raw) != EXPECTED_CENTRAL_MANIFEST_SHA256
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_central_manifest_drift",
                "central Master manifest bytes drifted",
            )
        manifest = _strict_json(raw, "central Master manifest")
        if (
            manifest.get("schema_version") != "1.0.0-master-index-manifest"
            or manifest.get("master_index_id")
            != "theme-hierarchy-master-index-v1-2026-08-03"
            or manifest.get("status")
            != "PASS_THEME_HIERARCHY_MASTER_INDEX_GATES_CLOSED"
            or manifest.get("hierarchy")
            != "paper -> theme_big_question -> printed_question -> atomic_part"
            or manifest.get("manifest_self_sha256") is not None
            or not _all_false(manifest.get("gate_state"))
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_central_manifest_invalid",
                "central Master manifest payload drifted",
            )
        expected = manifest.get("expected_counts")
        if not isinstance(expected, dict) or any(
            expected.get(key) != value
            for key, value in {
                "physical_papers": 24,
                "physical_themes": 68,
                "physical_printed_questions": 501,
                "physical_atomic_parts": 470,
            }.items()
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_central_count_mismatch",
                "central Master fixed counts drifted",
            )
        bindings = _binding_index(
            manifest.get("output_bindings"),
            expected_paths=EXPECTED_CENTRAL_OUTPUTS,
            label="central Master",
            central=True,
        )
        parsed = _verify_outputs(
            self.central_root, bindings, label="central Master"
        )

        prefix = CENTRAL_RELATIVE.as_posix()
        papers = _index(
            parsed[f"{prefix}/paper_records.jsonl"], "paper_id", "central papers"
        )
        themes = _index(
            parsed[f"{prefix}/theme_big_question_records.jsonl"],
            "theme_big_question_id",
            "central themes",
        )
        printed = _index(
            parsed[f"{prefix}/printed_question_records.jsonl"],
            "printed_question_id",
            "central printed questions",
        )
        atomics = _index(
            parsed[f"{prefix}/atomic_part_records.jsonl"],
            "atomic_part_id",
            "central atomic parts",
        )
        if tuple(map(len, (papers, themes, printed, atomics))) != (24, 68, 501, 470):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_central_count_mismatch",
                "central Master hierarchy counts drifted",
            )

        unassigned: set[str] = set()
        for atomic_id, atomic in atomics.items():
            printed_id = atomic.get("parent_printed_question_id")
            printed_row = printed.get(printed_id) if isinstance(printed_id, str) else None
            theme_id = (
                printed_row.get("parent_theme_big_question_id")
                if printed_row is not None
                else None
            )
            theme_row = themes.get(theme_id) if isinstance(theme_id, str) else None
            paper_id = theme_row.get("parent_paper_id") if theme_row is not None else None
            paper_row = papers.get(paper_id) if isinstance(paper_id, str) else None
            if printed_row is None or theme_row is None or paper_row is None:
                unassigned.add(atomic_id)
        if len(unassigned) != EXPECTED_CENTRAL_COUNTS["original_pending_atomics"]:
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_central_unassigned_mismatch",
                "central Master unassigned atomic count drifted",
            )
        return papers, themes, printed, atomics, frozenset(unassigned)

    def _candidate_snapshot(self) -> tuple[
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
        dict[str, Any],
        dict[str, Any],
    ]:
        raw = _read_exact(self.product_root, "manifest.json", "repair manifest")
        if (
            len(raw) != EXPECTED_PRODUCT_MANIFEST_BYTES
            or _sha256(raw) != EXPECTED_PRODUCT_MANIFEST_SHA256
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_manifest_drift",
                "candidate repair manifest bytes drifted",
            )
        manifest = _strict_json(raw, "repair manifest")
        payload = {
            key: value
            for key, value in manifest.items()
            if key != "manifest_payload_sha256"
        }
        if (
            manifest.get("schema_version") != "1.0.0"
            or manifest.get("product_id") != PRODUCT_ID
            or manifest.get("status")
            != "candidate_repair_machine_checked_pending_human"
            or manifest.get("central_master_modified") is not False
            or manifest.get("manifest_payload_sha256")
            != EXPECTED_PRODUCT_PAYLOAD_SHA256
            or _canonical_sha256(payload) != EXPECTED_PRODUCT_PAYLOAD_SHA256
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_manifest_invalid",
                "candidate repair manifest payload drifted",
            )
        bindings = _binding_index(
            manifest.get("output_files"),
            expected_paths=EXPECTED_PRODUCT_OUTPUTS,
            label="candidate repair",
            central=False,
        )
        parsed = _verify_outputs(
            self.product_root, bindings, label="candidate repair"
        )
        schema = parsed["repair_record_schema.json"]
        rows = parsed["repair_records.jsonl"]
        try:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except Exception as exc:  # jsonschema exposes several schema subclasses.
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_schema_invalid",
                "candidate repair record schema is invalid",
            ) from exc
        for row_number, row in enumerate(rows, 1):
            error = next(iter(validator.iter_errors(row)), None)
            if error is not None:
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_record_schema_invalid",
                    f"candidate repair row {row_number} fails its frozen schema",
                )

        batches_doc = parsed["theme_batches.json"]
        projection = parsed["master_repair_projection.json"]
        coverage = parsed["coverage_report.json"]
        answer_summary = parsed["answer_alignment_summary.json"]
        batches = batches_doc.get("batches")
        if not isinstance(batches, list):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_batch_invalid",
                "candidate theme batches are missing",
            )
        expected_coverage = {
            "target_master_atomic_total": 43,
            "completed_source_master_atomic_count": 43,
            "remaining_source_master_atomic_count": 0,
            "effective_atomic_count": 56,
            "printed_question_count": 41,
            "complete_theme_batch_count": 5,
            "split_source_master_atomic_count": 10,
            "all_included_batches_complete": True,
            "central_master_modified": False,
        }
        manifest_counts = manifest.get("counts")
        if (
            not isinstance(manifest_counts, dict)
            or manifest_counts != coverage
            or any(coverage.get(key) != value for key, value in expected_coverage.items())
            or answer_summary.get("effective_atomic_count") != 56
            or answer_summary.get("reference_answer_present_count") != 0
            or answer_summary.get("reference_answer_absent_count") != 56
            or answer_summary.get("official_answer_count") != 0
            or answer_summary.get("independently_verified_count") != 0
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_count_mismatch",
                "candidate repair aggregate counts drifted",
            )
        return tuple(rows), tuple(batches), projection, coverage

    def _snapshot(self) -> _Snapshot:
        papers, themes, printed, atomics, unassigned = self._central_snapshot()
        rows, batches, projection, coverage = self._candidate_snapshot()
        return _Snapshot(
            records=rows,
            batches=batches,
            projection=projection,
            coverage=coverage,
            central_papers=papers,
            central_themes=themes,
            central_printed=printed,
            central_atomics=atomics,
            central_unassigned_ids=unassigned,
        )

    @staticmethod
    def _validate_record_boundary(row: dict[str, Any]) -> None:
        if (
            row.get("schema_version") != "1.0.0-master-parent-chain-repair"
            or row.get("status") != "candidate_repair_machine_checked_pending_human"
            or not _all_false(
                row.get("authority_gates"), exact_keys=EXPECTED_AUTHORITY_GATE_KEYS
            )
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_authority_invalid",
                "a candidate repair row crossed its authority boundary",
            )
        answer = row.get("answer")
        if (
            not isinstance(answer, dict)
            or answer.get("availability") != "absent"
            or answer.get("authority") != "none"
            or answer.get("alignment_status")
            != "no_answer_material_available_in_captured_source"
            or answer.get("answer_verified") is not False
            or answer.get("independently_verified") is not False
            or answer.get("official") is not False
            or answer.get("reference_answer_text") is not None
            or answer.get("source_answer_path") is not None
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_answer_boundary_invalid",
                "a candidate repair row no longer has the frozen absent-answer boundary",
            )
        chain = row.get("repaired_parent_chain")
        if (
            not isinstance(chain, dict)
            or chain.get("status") != "candidate_complete_not_applied_to_master"
            or chain.get("applied_to_central_master") is not False
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_application_boundary_invalid",
                "a candidate repair row appears applied to central Master",
            )

    @staticmethod
    def _factor_candidates(row: dict[str, Any]) -> list[dict[str, Any]]:
        difficulty = row.get("difficulty")
        factors = difficulty.get("factors") if isinstance(difficulty, dict) else None
        if (
            not isinstance(factors, list)
            or tuple(factor.get("dimension_id") for factor in factors)
            != EXPECTED_FACTOR_IDS
            or difficulty.get("is_measured") is not False
            or difficulty.get("measured_difficulty") is not None
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_difficulty_invalid",
                "a candidate repair row no longer has ten ordered candidate factors",
            )
        return [
            {"dimension_id": factor["dimension_id"], "value": factor.get("value")}
            for factor in factors
        ]

    @staticmethod
    def _textbook_mapping_candidate(row: dict[str, Any]) -> dict[str, Any]:
        mapping = row.get("textbook_mapping")
        entries = mapping.get("entries") if isinstance(mapping, dict) else None
        if (
            not isinstance(entries, list)
            or not entries
            or mapping.get("mapping_status")
            != "complete_directory_level_unit_unknown"
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_textbook_mapping_invalid",
                "a candidate effective atomic lacks a complete textbook directory mapping",
            )
        projected: list[dict[str, Any]] = []
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or entry.get("volume_id") != "TB-M1"
                or entry.get("volume_title") != "必修第一册"
                or entry.get("publisher") != "上海科学技术出版社"
                or not isinstance(entry.get("chapter_id"), str)
                or not isinstance(entry.get("chapter_title"), str)
                or not isinstance(entry.get("section_number"), str)
                or not isinstance(entry.get("section_title"), str)
                or entry.get("status") != "toc_direct_directory_mapping"
                or entry.get("evidence_level") != "L1_LOCAL_TEXTBOOK"
                or entry.get("knowledge_role") not in {"primary", "supporting"}
                or not isinstance(entry.get("knowledge_tag"), str)
                or entry.get("unit_id") is not None
                or entry.get("unit_status")
                != "unknown_no_verified_atomic_unit_registry"
            ):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_textbook_mapping_invalid",
                    "a candidate textbook volume-chapter-section mapping drifted",
                )
            projected.append(
                {
                    "knowledge_tag": entry["knowledge_tag"],
                    "knowledge_role": entry["knowledge_role"],
                    "publisher": entry["publisher"],
                    "volume_id": entry["volume_id"],
                    "volume_title": entry["volume_title"],
                    "chapter_id": entry["chapter_id"],
                    "chapter_title": entry["chapter_title"],
                    "section_number": entry["section_number"],
                    "section_title": entry["section_title"],
                    "mapping_status": entry["status"],
                    "evidence_level": entry["evidence_level"],
                    "unit_status": entry["unit_status"],
                }
            )
        return {
            "status": mapping["mapping_status"],
            "entries": projected,
            "human_reviewed": False,
        }

    @staticmethod
    def _evidence_projection(row: dict[str, Any]) -> dict[str, Any]:
        viewed = row.get("viewed_evidence")
        if not isinstance(viewed, list) or not viewed:
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_evidence_invalid",
                "a candidate effective atomic lacks viewed evidence",
            )
        question_ids: list[str] = []
        question_pages: set[int] = set()
        shared_pages: set[int] = set()
        shared_ids = set(row["dependency"]["shared_material_crop_ids"])
        observed_shared: set[str] = set()
        shared_pages_by_id: dict[str, set[int]] = {
            crop_id: set() for crop_id in row["dependency"]["shared_material_crop_ids"]
        }
        for item in viewed:
            crop_id = item.get("crop_id") if isinstance(item, dict) else None
            role = item.get("evidence_role") if isinstance(item, dict) else None
            page = item.get("source_page") if isinstance(item, dict) else None
            if (
                not isinstance(crop_id, str)
                or not crop_id
                or role not in {"question", "question_parent_context", "shared_material"}
                or isinstance(page, bool)
                or not isinstance(page, int)
                or page < 1
                or item.get("visual_inspection_status")
                != "actually_viewed_by_primary_model_in_repair_turn"
            ):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_evidence_invalid",
                    "a viewed evidence descriptor drifted",
                )
            if role in {"question", "question_parent_context"}:
                question_ids.append(crop_id)
                question_pages.add(page)
            if crop_id in shared_ids:
                observed_shared.add(crop_id)
                shared_pages.add(page)
                shared_pages_by_id[crop_id].add(page)
        if not question_ids or observed_shared != shared_ids:
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_evidence_invalid",
                "question or shared-material visual closure is incomplete",
            )
        return {
            "question_crop_ids": list(dict.fromkeys(question_ids)),
            "question_page_numbers": sorted(question_pages),
            "shared_materials": [
                {
                    "crop_id": crop_id,
                    "page_numbers": sorted(shared_pages_by_id[crop_id]),
                }
                for crop_id in row["dependency"]["shared_material_crop_ids"]
            ],
            "shared_material_page_numbers": sorted(shared_pages),
        }

    def _build_overlay(self, snapshot: _Snapshot) -> dict[str, Any]:
        records_by_effective: dict[str, dict[str, Any]] = {}
        records_by_master: dict[str, list[dict[str, Any]]] = defaultdict(list)
        records_by_batch: dict[str, list[dict[str, Any]]] = defaultdict(list)
        owner_by_effective: dict[str, str] = {}
        order_by_effective: dict[str, tuple[int, int]] = {}
        dependencies: dict[str, tuple[str, ...]] = {}
        repair_ids: set[str] = set()
        printed_ids: set[str] = set()
        source_identities: set[str] = set()

        for row in snapshot.records:
            self._validate_record_boundary(row)
            target = row.get("target")
            chain = row.get("repaired_parent_chain")
            boundary = row.get("boundary_repair")
            dependency = row.get("dependency")
            theme = row.get("theme")
            source_identity = row.get("source_identity")
            if not all(
                isinstance(value, dict)
                for value in (target, chain, boundary, dependency, theme, source_identity)
            ):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_record_invalid",
                    "candidate repair row structure drifted",
                )
            repair_id = row.get("repair_id")
            master_id = target.get("master_atomic_part_id")
            printed_id = target.get("master_printed_question_id")
            effective_id = chain.get("atomic_part_id")
            batch_id = row.get("batch_id")
            if any(
                not isinstance(value, str) or not value
                for value in (repair_id, master_id, printed_id, effective_id, batch_id)
            ):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_identity_invalid",
                    "candidate repair identity is invalid",
                )
            if (
                repair_id in repair_ids
                or effective_id in records_by_effective
                or master_id not in snapshot.central_unassigned_ids
                or master_id not in snapshot.central_atomics
                or printed_id not in snapshot.central_printed
                or snapshot.central_atomics[master_id].get("parent_printed_question_id")
                != printed_id
                or target.get("completion_denominator_unit") != "source_master_atomic"
                or target.get("current_parent_chain_status")
                != "incomplete_printed_parent_theme_missing"
                or target.get("current_printed_parent_theme_big_question_id") is not None
                or boundary.get("source_master_atomic_id") != master_id
                or chain.get("printed_question_id") != printed_id
            ):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_identity_invalid",
                    "candidate repair identity or original unassigned binding drifted",
                )
            repair_ids.add(repair_id)
            printed_ids.add(printed_id)
            records_by_effective[effective_id] = row
            records_by_master[master_id].append(row)
            records_by_batch[batch_id].append(row)
            owner_by_effective[effective_id] = master_id
            theme_order = _known_int(chain.get("theme_order"), "theme order")
            atomic_order = _known_int(
                chain.get("atomic_order_in_theme"), "effective atomic order"
            )
            order_by_effective[effective_id] = (theme_order, atomic_order)
            prior = tuple(
                _string_list(
                    dependency.get("prior_atomic_part_ids"),
                    "prior effective atomic ids",
                )
            )
            if len(prior) != len(set(prior)):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_dependency_invalid",
                    "a dependency list repeats an effective atomic id",
                )
            dependencies[effective_id] = prior
            source_identities.add(_canonical_sha256(source_identity))
            self._factor_candidates(row)
            self._evidence_projection(row)

        if (
            len(snapshot.records) != EXPECTED_COUNTS["effective_atomics"]
            or len(records_by_master) != EXPECTED_COUNTS["source_master_atomics"]
            or len(printed_ids) != EXPECTED_COUNTS["printed_questions"]
            or set(records_by_master) != snapshot.central_unassigned_ids
            or len(source_identities) != 1
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_identity_closure_invalid",
                "candidate repair does not exactly cover the 43 central unassigned atomics",
            )

        # Validate every immediate edge and compute its transitive prior closure.
        for effective_id, prior_ids in dependencies.items():
            current_order = order_by_effective[effective_id]
            current_batch = records_by_effective[effective_id]["batch_id"]
            for prior_id in prior_ids:
                if (
                    prior_id not in records_by_effective
                    or records_by_effective[prior_id]["batch_id"] != current_batch
                    or order_by_effective[prior_id] >= current_order
                ):
                    raise MasterParentChainRepairOverlayError(
                        "master_parent_repair_dependency_invalid",
                        "a candidate dependency is missing, cross-theme, or not forward-only",
                    )

        closure_cache: dict[str, tuple[str, ...]] = {}

        def closure(effective_id: str, stack: frozenset[str] = frozenset()) -> tuple[str, ...]:
            if effective_id in closure_cache:
                return closure_cache[effective_id]
            if effective_id in stack:
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_dependency_invalid",
                    "candidate dependency graph contains a cycle",
                )
            values: set[str] = set()
            for prior_id in dependencies[effective_id]:
                values.add(prior_id)
                values.update(closure(prior_id, stack | {effective_id}))
            ordered = tuple(sorted(values, key=order_by_effective.__getitem__))
            closure_cache[effective_id] = ordered
            return ordered

        for effective_id in records_by_effective:
            closure(effective_id)
        edge_count = sum(len(value) for value in dependencies.values())
        if edge_count != EXPECTED_COUNTS["dependency_edges"]:
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_dependency_invalid",
                "candidate dependency edge count drifted",
            )

        projection = snapshot.projection
        projection_rows = projection.get("records")
        completed_ids = projection.get("completed_source_master_atomic_ids")
        if (
            projection.get("product_id") != PRODUCT_ID
            or projection.get("status")
            != "candidate_projection_not_applied_to_central_master"
            or projection.get("target_master_atomic_total") != 43
            or projection.get("completed_source_master_atomic_count") != 43
            or projection.get("remaining_source_master_atomic_count") != 0
            or projection.get("remaining_source_master_atomic_ids") != []
            or not isinstance(completed_ids, list)
            or set(completed_ids) != snapshot.central_unassigned_ids
            or not isinstance(projection_rows, list)
            or len(projection_rows) != 43
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_projection_invalid",
                "candidate Master projection closure drifted",
            )
        projection_by_master = {
            row.get("master_atomic_part_id"): row for row in projection_rows
        }
        if set(projection_by_master) != snapshot.central_unassigned_ids:
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_projection_invalid",
                "candidate Master projection identity set drifted",
            )

        batch_by_id: dict[str, dict[str, Any]] = {}
        for batch in snapshot.batches:
            batch_id = batch.get("batch_id") if isinstance(batch, dict) else None
            order = batch.get("theme_order") if isinstance(batch, dict) else None
            if (
                not isinstance(batch_id, str)
                or batch_id in batch_by_id
                or order not in EXPECTED_BATCH_COUNTS
                or batch.get("status")
                != "complete_theme_batch_candidate_machine_checked_pending_human"
                or batch.get("paper_id") != "MASTER-PAPER-eca872096473cd6ec91a"
            ):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_batch_invalid",
                    "candidate complete-theme batch identity drifted",
                )
            batch_by_id[batch_id] = batch
        if len(batch_by_id) != 5 or set(batch_by_id) != set(records_by_batch):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_batch_invalid",
                "candidate complete-theme batch set drifted",
            )

        theme_groups: list[dict[str, Any]] = []
        split_master_count = 0
        total_factor_count = 0
        total_textbook_mapping_entries = 0
        all_effective_ids: set[str] = set()
        for batch_id, batch in batch_by_id.items():
            rows = records_by_batch[batch_id]
            theme_order = batch["theme_order"]
            expected_batch = EXPECTED_BATCH_COUNTS[theme_order]
            theme_id = batch.get("theme_big_question_id")
            paper_id = batch.get("paper_id")
            if (
                theme_id not in snapshot.central_themes
                or paper_id not in snapshot.central_papers
                or snapshot.central_themes[theme_id].get("parent_paper_id") != paper_id
                or batch.get("page_span") != expected_batch["page_span"]
            ):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_parent_chain_invalid",
                    "candidate paper/theme parent binding drifted",
                )

            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                if (
                    row["repaired_parent_chain"].get("paper_id") != paper_id
                    or row["repaired_parent_chain"].get("theme_big_question_id")
                    != theme_id
                    or row["theme"].get("order") != theme_order
                    or row["theme"].get("page_span") != batch.get("page_span")
                    or row["theme"].get("title_literal")
                    != batch.get("theme_title_literal")
                    or row["theme"].get("heading_literal")
                    != batch.get("theme_heading_literal")
                    or row["theme"].get("context_literal")
                    != batch.get("theme_context_literal")
                ):
                    raise MasterParentChainRepairOverlayError(
                        "master_parent_repair_parent_chain_invalid",
                        "a candidate row disagrees with its complete-theme parent",
                    )
                grouped[row["target"]["master_atomic_part_id"]].append(row)

            effective_orders = sorted(
                row["repaired_parent_chain"]["atomic_order_in_theme"] for row in rows
            )
            if effective_orders != list(range(1, len(rows) + 1)):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_order_invalid",
                    "effective atomic order is not complete within a theme",
                )

            printed_order_by_id: dict[str, int] = {}
            for row in rows:
                printed_id = row["target"]["master_printed_question_id"]
                printed_order = _known_int(
                    row["repaired_parent_chain"].get("printed_order_in_theme"),
                    "printed order",
                )
                previous = printed_order_by_id.setdefault(printed_id, printed_order)
                if previous != printed_order:
                    raise MasterParentChainRepairOverlayError(
                        "master_parent_repair_order_invalid",
                        "one printed question has conflicting candidate order",
                    )
            if sorted(printed_order_by_id.values()) != list(
                range(1, len(printed_order_by_id) + 1)
            ):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_order_invalid",
                    "printed-question order is not complete within a theme",
                )

            atomic_chain: list[dict[str, Any]] = []
            for master_id, master_rows in grouped.items():
                master_rows.sort(key=lambda row: row["boundary_repair"]["split_index"])
                split_count = len(master_rows)
                relation = "split_1_to_n" if split_count > 1 else "retain_1_to_1"
                if (
                    [row["boundary_repair"]["split_index"] for row in master_rows]
                    != list(range(1, split_count + 1))
                    or any(
                        row["boundary_repair"].get("split_count") != split_count
                        or row["boundary_repair"].get("relation") != relation
                        for row in master_rows
                    )
                ):
                    raise MasterParentChainRepairOverlayError(
                        "master_parent_repair_split_invalid",
                        "candidate 1-to-N split membership drifted",
                    )
                projection_row = projection_by_master[master_id]
                effective_ids = [
                    row["repaired_parent_chain"]["atomic_part_id"] for row in master_rows
                ]
                if (
                    projection_row.get("effective_atomic_part_ids") != effective_ids
                    or projection_row.get("boundary_relation") != relation
                    or projection_row.get("master_printed_question_id")
                    != master_rows[0]["target"]["master_printed_question_id"]
                    or projection_row.get("paper_id") != paper_id
                    or projection_row.get("theme_big_question_id") != theme_id
                    or projection_row.get("batch_id") != batch_id
                    or projection_row.get("applied_to_central_master") is not False
                ):
                    raise MasterParentChainRepairOverlayError(
                        "master_parent_repair_projection_invalid",
                        "candidate split projection disagrees with repair records",
                    )
                if split_count > 1:
                    split_master_count += 1

                candidate_units: list[dict[str, Any]] = []
                for row in master_rows:
                    chain = row["repaired_parent_chain"]
                    effective_id = chain["atomic_part_id"]
                    classification = row["classification"]
                    evidence = self._evidence_projection(row)
                    prior_ids = list(dependencies[effective_id])
                    prior_master_ids = list(
                        dict.fromkeys(owner_by_effective[value] for value in prior_ids)
                    )
                    factors = self._factor_candidates(row)
                    textbook_mapping = self._textbook_mapping_candidate(row)
                    total_factor_count += len(factors)
                    total_textbook_mapping_entries += len(textbook_mapping["entries"])
                    all_effective_ids.add(effective_id)
                    candidate_units.append(
                        {
                            "atomic_part_id": effective_id,
                            "effective_sequence_in_theme": chain[
                                "atomic_order_in_theme"
                            ],
                            "atomic_sequence_in_printed": chain[
                                "atomic_order_in_printed"
                            ],
                            "split_index": row["boundary_repair"]["split_index"],
                            "split_count": row["boundary_repair"]["split_count"],
                            "item_type": classification["item_type"],
                            "selection_rule": classification["selection_rule"],
                            "classification_candidate": {
                                "primary_K": classification["primary_K"],
                                "supporting_K": deepcopy(classification["supporting_K"]),
                                "A": deepcopy(classification["A"]),
                                "C": deepcopy(classification["C"]),
                                "R": deepcopy(classification["R"]),
                                "RP": deepcopy(classification["RP"]),
                                "human_reviewed": False,
                            },
                            "textbook_mapping_candidate": textbook_mapping,
                            "difficulty_candidate": {
                                "cognitive_prelabel": row["difficulty"][
                                    "cognitive_prelabel"
                                ],
                                "ten_factors": factors,
                                "is_measured": False,
                                "human_reviewed": False,
                            },
                            "visible_summary_zh": row["visible_summary_zh"],
                            "response_requirement_zh": row[
                                "response_requirement_zh"
                            ],
                            "question_crop_ids": evidence["question_crop_ids"],
                            "question_page_numbers": evidence[
                                "question_page_numbers"
                            ],
                            "shared_material_crop_ids": deepcopy(
                                row["dependency"]["shared_material_crop_ids"]
                            ),
                            "shared_materials": evidence["shared_materials"],
                            "shared_material_page_numbers": evidence[
                                "shared_material_page_numbers"
                            ],
                            "cross_page_prior_dependency": effective_id
                            in set(batch["cross_page_effective_atomic_ids"]),
                            "dependency": {
                                "prior_effective_atomic_part_ids": prior_ids,
                                "prior_effective_closure_ids": list(
                                    closure_cache[effective_id]
                                ),
                                "prior_source_master_atomic_ids": prior_master_ids,
                            },
                            "answer": {
                                "availability": "absent",
                                "authority": "none",
                                "alignment_status": row["answer"][
                                    "alignment_status"
                                ],
                                "official": False,
                                "verified": False,
                                "independently_verified": False,
                            },
                            "authority": deepcopy(AUTHORITY),
                        }
                    )

                first = master_rows[0]
                atomic_chain.append(
                    {
                        "atomic_part_id": master_id,
                        "printed_question_id": first["target"][
                            "master_printed_question_id"
                        ],
                        "printed_sequence_in_theme": first[
                            "repaired_parent_chain"
                        ]["printed_order_in_theme"],
                        "first_effective_sequence_in_theme": min(
                            unit["effective_sequence_in_theme"]
                            for unit in candidate_units
                        ),
                        "candidate_parent_chain": {
                            "paper_id": paper_id,
                            "theme_big_question_id": theme_id,
                            "printed_question_id": first["target"][
                                "master_printed_question_id"
                            ],
                            "status": "candidate_complete_not_applied_to_master",
                            "applied_to_central_master": False,
                        },
                        "boundary_relation": relation,
                        "candidate_effective_units": candidate_units,
                        "human_reviewed": False,
                    }
                )

            atomic_chain.sort(
                key=lambda row: (
                    row["printed_sequence_in_theme"],
                    row["first_effective_sequence_in_theme"],
                )
            )
            immediate_edges = [
                {
                    "effective_atomic_part_id": effective_id,
                    "depends_on_effective_atomic_part_id": prior_id,
                }
                for effective_id in sorted(
                    (
                        value
                        for value, record in records_by_effective.items()
                        if record["batch_id"] == batch_id
                    ),
                    key=order_by_effective.__getitem__,
                )
                for prior_id in dependencies[effective_id]
            ]

            shared_materials: list[dict[str, Any]] = []
            declared_shared = _string_list(
                batch.get("shared_material_crop_ids"), "theme shared materials"
            )
            for material_sequence, crop_id in enumerate(declared_shared, 1):
                used_master_ids: list[str] = []
                used_effective_ids: list[str] = []
                pages: set[int] = set()
                for row in rows:
                    if crop_id not in row["dependency"]["shared_material_crop_ids"]:
                        continue
                    effective_id = row["repaired_parent_chain"]["atomic_part_id"]
                    used_effective_ids.append(effective_id)
                    used_master_ids.append(row["target"]["master_atomic_part_id"])
                    evidence = self._evidence_projection(row)
                    material = next(
                        (
                            item
                            for item in evidence["shared_materials"]
                            if item["crop_id"] == crop_id
                        ),
                        None,
                    )
                    if material is None:
                        raise MasterParentChainRepairOverlayError(
                            "master_parent_repair_shared_material_invalid",
                            "an effective atomic lost its shared-material page binding",
                        )
                    pages.update(material["page_numbers"])
                if not used_effective_ids:
                    # A theme-level material may be declared for only a subset; it
                    # still must have appeared in at least one viewed record.
                    for row in rows:
                        for item in row["viewed_evidence"]:
                            if item.get("crop_id") == crop_id:
                                pages.add(item["source_page"])
                    if not pages:
                        raise MasterParentChainRepairOverlayError(
                            "master_parent_repair_shared_material_invalid",
                            "a declared shared material lacks viewed evidence",
                        )
                shared_materials.append(
                    {
                        "crop_id": crop_id,
                        "material_sequence": material_sequence,
                        "page_numbers": sorted(pages),
                        "used_by_source_master_atomic_ids": list(
                            dict.fromkeys(used_master_ids)
                        ),
                        "used_by_effective_atomic_part_ids": used_effective_ids,
                        "evidence_status": "actually_viewed_candidate",
                    }
                )

            actual_counts = {
                "printed_questions": len(printed_order_by_id),
                "source_master_atomics": len(grouped),
                "effective_atomics": len(rows),
                "split_source_master_atomics": sum(
                    len(value) > 1 for value in grouped.values()
                ),
                "dependency_edges": len(immediate_edges),
                "page_span": batch["page_span"],
            }
            declared_count_checks = {
                "printed_questions": batch.get("printed_question_count"),
                "source_master_atomics": batch.get("source_master_atomic_count"),
                "effective_atomics": batch.get("effective_atomic_count"),
                "split_source_master_atomics": batch.get(
                    "split_source_master_atomic_count"
                ),
                "dependency_edges": batch.get("dependency_edge_count"),
                "page_span": batch.get("page_span"),
            }
            if actual_counts != expected_batch or declared_count_checks != expected_batch:
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_batch_count_mismatch",
                    "candidate complete-theme counts drifted",
                )
            if set(batch.get("source_master_atomic_ids", [])) != set(grouped):
                raise MasterParentChainRepairOverlayError(
                    "master_parent_repair_batch_invalid",
                    "candidate complete-theme source Master membership drifted",
                )

            theme_groups.append(
                {
                    "theme": {
                        "id": theme_id,
                        "sequence": theme_order,
                        "title": batch["theme_title_literal"],
                        "heading": batch["theme_heading_literal"],
                        "page_span": deepcopy(batch["page_span"]),
                        "parent_chain_status": "candidate_complete_not_applied_to_master",
                    },
                    "counts": {
                        key: actual_counts[key]
                        for key in (
                            "printed_questions",
                            "source_master_atomics",
                            "effective_atomics",
                            "split_source_master_atomics",
                            "dependency_edges",
                        )
                    },
                    "shared_context": {
                        "context_literal": batch["theme_context_literal"],
                        "shared_materials": shared_materials,
                    },
                    "dependency_edges": immediate_edges,
                    "atomic_chain": atomic_chain,
                    "authority": deepcopy(AUTHORITY),
                }
            )

        theme_groups.sort(key=lambda group: group["theme"]["sequence"])
        if (
            [group["theme"]["sequence"] for group in theme_groups]
            != [1, 2, 3, 4, 5]
            or len(all_effective_ids) != EXPECTED_COUNTS["effective_atomics"]
            or split_master_count != EXPECTED_COUNTS["split_source_master_atomics"]
            or total_factor_count != EXPECTED_COUNTS["difficulty_factors"]
            or total_textbook_mapping_entries
            != EXPECTED_TEXTBOOK_MAPPING_ENTRIES
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_aggregate_invalid",
                "candidate repair aggregate closure drifted",
            )

        first_identity = snapshot.records[0]["source_identity"]
        if (
            first_identity.get("calendar_year") != 2025
            or first_identity.get("academic_year") != "2025学年"
            or first_identity.get("semester") != "第一学期"
            or first_identity.get("grade") != "高一"
            or first_identity.get("district") != "unknown"
            or first_identity.get("paper_type") != "高一第一学期期中校考"
            or first_identity.get("official_status") != "nonofficial"
            or first_identity.get("school_attribution") != "上海市大同中学"
            or first_identity.get("school_attribution_status")
            != "title_attribution_only"
        ):
            raise MasterParentChainRepairOverlayError(
                "master_parent_repair_source_identity_invalid",
                "candidate repair source identity boundary drifted",
            )

        result = {
            "schema_version": "1.0.0-master-parent-chain-repair-sidecar",
            "scope": "candidate_only_read_only_master_parent_chain_repair",
            "counts": deepcopy(EXPECTED_COUNTS),
            "completion": {
                "denominator_unit": "source_master_atomic",
                "candidate_completed": 43,
                "candidate_total": 43,
                "candidate_remaining": 0,
                "candidate_status": "machine_candidate_complete_pending_central_confirmation",
                "candidate_label_zh": "候选整理43/43完成",
                "central_label_zh": "中央原记录待确认",
                "human_reviewed": False,
            },
            "central_master": {
                "atomic_total": 470,
                "complete_parent_chain_atomics": 427,
                "original_pending_atomics": 43,
                "candidate_overlay_atomics": 43,
                "applied": False,
                "human_confirmed": False,
                "denominator_unchanged": True,
            },
            "authority": deepcopy(AUTHORITY),
            "integrity": {
                "verified_on_read": True,
                "candidate_manifest_payload_verified": True,
                "all_candidate_manifest_outputs_verified": True,
                "all_central_manifest_outputs_verified": True,
                "strict_json_and_jsonl_verified": True,
                "record_schema_verified": True,
                "textbook_directory_mapping_verified": True,
                "central_unassigned_exact_set_verified": True,
                "dependency_forward_closure_verified": True,
                "fail_closed": True,
            },
            "paper_groups": [
                {
                    "paper": {
                        "id": "MASTER-PAPER-eca872096473cd6ec91a",
                        "title": "2025学年第一学期高一化学期中考试试卷",
                        "page_span": [1, 6],
                        "source_metadata": {
                            "calendar_year": 2025,
                            "academic_year": "2025学年",
                            "semester": "第一学期",
                            "grade": "高一",
                            "district": "unknown",
                            "paper_type": "高一第一学期期中校考",
                            "official_status": "nonofficial",
                            "school_attribution": "上海市大同中学",
                            "school_attribution_status": "title_attribution_only",
                            "source_account": first_identity["source_account"],
                            "evidence_level": first_identity["evidence_level"],
                        },
                        "status": "candidate_overlay_not_applied_to_central_master",
                    },
                    "theme_groups": theme_groups,
                }
            ],
        }
        _reject_unsafe_dto(result)
        return result

    def overlay(self) -> dict[str, Any]:
        """Return a newly verified safe sidecar; never mutate central Master."""

        return self._build_overlay(self._snapshot())

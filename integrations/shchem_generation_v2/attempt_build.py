"""Attempt-scoped R18 build transaction primitives.

This module intentionally knows nothing about the legacy fixed output tree.
Callers provide the protected snapshot explicitly, build only below
``temporary_root``, and publish by one atomic directory rename.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .r17_boundary import (
    R17BoundaryError,
    SnapshotGraph,
    _guard_signature,
    _has_reparse_attribute,
    _inside,
    _mkdir_guarded,
    assert_safe_regular_tree,
    atomic_commit_directory,
    canonical_sha256,
    exclusive_create_bundle,
    portable_relative_path_error,
)
from .schema_validation import SchemaValidationError, validate

ATTEMPT_ID_PATTERN = re.compile(r"R18-BUILD-ATTEMPT-[0-9a-f]{32}\Z")
ATTEMPT_MANIFEST = "attempt_manifest.json"
PRODUCTION_BUILD_MODE = "production_prefreeze"
TEST_FIXTURE_BUILD_MODE = "isolated_transaction_test_fixture"
VERSION_ID = "SHCHEM-GEN-V2-DEMO-20260813-R18"
PAPER_ID = "PAPER-GEN-V2-20260813-018"
TRUSTED_WORKSPACE = Path(__file__).resolve().parents[2]
PRODUCER_RELATIVE_ROOT = Path("integrations/shchem_generation_v2")
PRODUCER_FINGERPRINT_ALGORITHM = (
    "canonical-sha256-of-sorted-owned-producer-files-v1"
)
STRUCTURED_DOMAIN_PATH_KEYS = frozenset(
    {
        "gas_path",
        "oxygen_collection_path",
        "open_cell_pressure_path",
        "required_path",
    }
)
OPAQUE_LEGACY_JSON_PATHS = frozenset(
    {
        "inputs/legacy_records/R18_PREFREEZE_INVALIDATED_CONTENT_METADATA.json",
        "inputs/legacy_records/R13_FREEZE_INVALIDATED.json",
        "inputs/legacy_records/R13_FREEZE_INVALIDATION_CORRECTION.json",
    }
)
PRIVATE_TEXT_PATTERNS = (
    ("windows_drive_absolute_path", re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]")),
    ("windows_unc_path", re.compile(r"(?<!\\)\\\\[^\\\s]+\\[^\\\s]+")),
    ("windows_user_directory", re.compile(r"(?i)[\\/]Users[\\/]")),
    ("posix_user_directory", re.compile(r"(?i)(?:/home/|/Users/)")),
    ("api_key", re.compile(r"(?i)(?:api[_-]?key|WEREAD_API_KEY)\s*[:=]\s*[^\s,;]{6,}")),
    ("oauth_secret", re.compile(r"(?i)(?:oauth|client[_-]?secret)\s*[:=]\s*[^\s,;]{6,}")),
    ("access_token", re.compile(r"(?i)(?:access|refresh)[_-]?token\s*[:=]\s*[^\s,;]{6,}")),
    ("bearer_token", re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[^\s,;]{6,}")),
)

PRODUCTION_SCHEMA_BINDINGS: dict[str, tuple[str, str]] = {
    "candidate/frozen_paper.json": (
        "integrations/shchem_generation_v2/schemas/paper_candidate.schema.json",
        "integrations/shchem_generation_v2/schemas/paper_candidate.schema.json",
    ),
    "candidate/task_card.json": (
        "integrations/shchem_generation_v2/schemas/task_card.schema.json",
        "integrations/shchem_generation_v2/schemas/task_card.schema.json",
    ),
    "figures/component_registry_r18.json": (
        "integrations/shchem_generation_v2/schemas/component_registry.schema.json",
        "integrations/shchem_generation_v2/schemas/component_registry.schema.json",
    ),
    "candidate/sol_generator_receipt.json": (
        "integrations/shchem_generation_v2/schemas/sol_generator_receipt.schema.json",
        "integrations/shchem_generation_v2/schemas/sol_generator_receipt.schema.json",
    ),
    "candidate/r18_producer_receipt.json": (
        "integrations/shchem_generation_v2/schemas/r18_producer_receipt.schema.json",
        "integrations/shchem_generation_v2/schemas/r18_producer_receipt.schema.json",
    ),
    "candidate/delivery_status.json": (
        "integrations/shchem_generation_v2/schemas/prefreeze_delivery_status.schema.json",
        "integrations/shchem_generation_v2/schemas/prefreeze_delivery_status.schema.json",
    ),
    "inputs/execution/generator_execution_metadata.json": (
        "integrations/shchem_generation_v2/schemas/generator_execution_self_report_r18.schema.json",
        "integrations/shchem_generation_v2/schemas/generator_execution_self_report_r18.schema.json",
    ),
    "controller/deterministic_check_request.json": (
        "sh-chem-db/kb/machine_governance_v2/schemas/deterministic_check_request.schema.json",
        "sh-chem-db/kb/machine_governance_v2/schemas/deterministic_check_request.schema.json",
    ),
    "controller/deterministic_check_report.json": (
        "sh-chem-db/kb/machine_governance_v2/schemas/deterministic_check_report.schema.json",
        "sh-chem-db/kb/machine_governance_v2/schemas/deterministic_check_report.schema.json",
    ),
    "candidate/generator_provenance_receipt.json": (
        "sh-chem-db/kb/machine_governance_v2/schemas/generator_provenance_receipt_r18.schema.json",
        "sh-chem-db/kb/machine_governance_v2/schemas/generator_provenance_receipt_r18.schema.json",
    ),
}

# The required generated core.  Vendored schemas and evidence inputs are
# intentionally additional inventory members and are enumerated by the final
# manifest rather than guessed here.
REQUIRED_CORE_PATHS = frozenset(
    {
        "candidate/delivery_status.json",
        "candidate/frozen_paper.json",
        "candidate/generator_provenance_receipt.json",
        "candidate/r18_producer_receipt.json",
        "candidate/sol_generator_receipt.json",
        "candidate/task_card.json",
        "candidate/week_plan.json",
        "inputs/execution/generator_execution_metadata.json",
        *OPAQUE_LEGACY_JSON_PATHS,
        "controller/deterministic_check_report.json",
        "controller/deterministic_check_request.json",
        "controller/subject_answer.json",
        "controller/subject_question.json",
        "coordination/prefreeze_receipt_r18.json",
        "figures/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.spec.json",
        "figures/assets/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.png",
        "figures/assets/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.svg",
        "figures/component_registry_r18.json",
        "reports/components.json",
        "reports/classification_vocabulary_mutations.json",
        "reports/conservation.json",
        "reports/controller_machine_pass.json",
        "reports/coverage_matrix.json",
        "reports/coverage_matrix_validation.json",
        "reports/cross_question_leakage.json",
        "reports/dedup.json",
        "reports/deterministic_atomic_scope.json",
        "reports/figure.json",
        "reports/figure_topology_mutations.json",
        "reports/figure_visual_qa.json",
        "reports/figure_visual_qa/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.onebit-print.png",
        "reports/figure_visual_qa/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.quarter-grayscale.png",
        "reports/inverse.json",
        "reports/mutations/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.disconnected-gas-path.png",
        "reports/mutations/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.disconnected-gas-path.svg",
        "reports/p32_display_parser.json",
        "reports/question_subject_isolation.json",
        "reports/question_subject_semantic_mutations.json",
        "reports/r13_invalidation_correction.json",
        "reports/rubric_name_requirement_mutations.json",
        "reports/schema.json",
        "reports/student_visible_prompt_safety_mutations.json",
        "reports/versioned_schema.json",
    }
)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def portable_locator_file(raw: str) -> str:
    """Return the filesystem part of a portable path[#/json-pointer] locator."""

    file_part, separator, fragment = raw.partition("#")
    if portable_relative_path_error(file_part) is not None:
        raise R17BoundaryError("portable_locator:file_path_invalid")
    if separator and (not fragment or not fragment.startswith("/")):
        raise R17BoundaryError("portable_locator:json_pointer_invalid")
    return file_part


def named_path_is_unbound_output(pointer: str) -> bool:
    return pointer.rsplit("/", 1)[-1].casefold().endswith("output_path")


def json_path_is_opaque_bound_evidence(relative: str) -> bool:
    if relative in OPAQUE_LEGACY_JSON_PATHS:
        return True
    parts = relative.replace("\\", "/").split("/")
    return (
        len(parts) >= 5
        and parts[:3] == ["sh-chem-db", "staging", "wechat"]
        and parts[-1] == "article_capture.json"
    )


def _owned_producer_paths(integration_root: Path) -> tuple[Path, ...]:
    root = _absolute(integration_root)
    schema_root = root / "schemas"
    renderer = root / "render_svg_png.mjs"
    if not root.is_dir() or not schema_root.is_dir() or not renderer.is_file():
        raise R17BoundaryError("producer_fingerprint:required_source_missing")
    paths = [
        *(path for path in root.glob("*.py") if path.is_file()),
        *(path for path in schema_root.rglob("*.json") if path.is_file()),
        renderer,
    ]
    ordered = tuple(sorted(paths, key=lambda path: path.as_posix().casefold()))
    folded = [path.as_posix().casefold() for path in ordered]
    if len(folded) != len(set(folded)):
        raise R17BoundaryError("producer_fingerprint:casefold_alias")
    return ordered


def compute_owned_producer_fingerprint(
    *,
    reference_root: Path,
    integration_root: Path | None = None,
    graph: SnapshotGraph | None = None,
) -> dict[str, Any]:
    """Independently enumerate and hash the complete owned producer set."""

    root = _absolute(reference_root)
    integration = _absolute(integration_root or (root / PRODUCER_RELATIVE_ROOT))
    if integration != _absolute(root / PRODUCER_RELATIVE_ROOT):
        raise R17BoundaryError("producer_fingerprint:integration_root_not_canonical")
    selected_graph = graph or SnapshotGraph(root)
    before = _owned_producer_paths(integration)
    rows: list[dict[str, object]] = []
    for path in before:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise R17BoundaryError(
                "producer_fingerprint:source_outside_reference_root"
            ) from exc
        snapshot = selected_graph.read(path)
        rows.append(
            {
                "path": relative,
                "sha256": snapshot.sha256,
                "bytes": snapshot.byte_length,
            }
        )
    if before != _owned_producer_paths(integration):
        raise R17BoundaryError("producer_fingerprint:owned_set_changed_during_hash")
    if graph is None:
        checkpoint = selected_graph.revalidate()
        if checkpoint.get("status") != "pass":
            raise R17BoundaryError(
                "producer_fingerprint:source_changed_during_hash"
            )
    return {
        "producer_version_id": VERSION_ID,
        "algorithm": PRODUCER_FINGERPRINT_ALGORITHM,
        "source_file_count": len(rows),
        "source_tree_sha256": canonical_sha256(rows),
        "files": rows,
    }


def validate_claimed_producer_fingerprint(
    *,
    reference_root: Path,
    claimed: object,
    graph: SnapshotGraph | None = None,
) -> dict[str, Any]:
    actual = compute_owned_producer_fingerprint(
        reference_root=reference_root,
        graph=graph,
    )
    if claimed != actual:
        raise R17BoundaryError(
            "attempt_package:producer_fingerprint_closed_set_mismatch"
        )
    return actual


def _stat_signature(path: Path) -> tuple[object, ...]:
    observed = os.lstat(path)
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
        getattr(observed, "st_file_attributes", 0),
    )


def _closed_directory_inventory(
    workspace: Path,
    directory: Path,
) -> tuple[tuple[object, ...], ...]:
    """Return an exact, non-following inventory for one protected directory."""

    root = _absolute(workspace)
    selected = _absolute(directory)
    if not _inside(root, selected):
        raise R17BoundaryError("protected_snapshot:directory_outside_workspace")
    assert_safe_regular_tree(selected)
    rows: list[tuple[object, ...]] = []
    root_stat = os.lstat(selected)
    rows.append(
        (
            "directory",
            ".",
            root_stat.st_dev,
            root_stat.st_ino,
            root_stat.st_mode,
            getattr(root_stat, "st_file_attributes", 0),
        )
    )
    for current, directory_names, file_names in os.walk(selected, followlinks=False):
        current_path = Path(current)
        for name in sorted(directory_names):
            child = current_path / name
            observed = os.lstat(child)
            rows.append(
                (
                    "directory",
                    child.relative_to(selected).as_posix(),
                    observed.st_dev,
                    observed.st_ino,
                    observed.st_mode,
                    getattr(observed, "st_file_attributes", 0),
                )
            )
        for name in sorted(file_names):
            child = current_path / name
            observed = os.lstat(child)
            rows.append(
                (
                    "file",
                    child.relative_to(selected).as_posix(),
                    observed.st_dev,
                    observed.st_ino,
                    observed.st_mode,
                    observed.st_size,
                    observed.st_mtime_ns,
                    observed.st_ctime_ns,
                    getattr(observed, "st_file_attributes", 0),
                )
            )
    return tuple(sorted(rows, key=lambda row: (str(row[0]), str(row[1]).casefold())))


@dataclass(frozen=True)
class ProtectedPathSnapshot:
    """One prewrite snapshot spanning present files and absent leaves."""

    workspace: Path
    paths: tuple[Path, ...]
    present: SnapshotGraph
    existed: dict[str, bool]
    kinds: dict[str, str]
    directory_inventories: dict[str, tuple[tuple[object, ...], ...]]
    absent_parent_signatures: dict[str, tuple[object, ...]]

    @classmethod
    def capture(cls, workspace: Path, paths: Iterable[Path]) -> ProtectedPathSnapshot:
        root = _absolute(workspace)
        normalized = tuple(_absolute(path) for path in paths)
        folded = [os.path.normcase(os.fspath(path)).casefold() for path in normalized]
        if len(folded) != len(set(folded)):
            raise R17BoundaryError("protected_snapshot:duplicate_or_casefold_alias")
        graph = SnapshotGraph(root)
        existed: dict[str, bool] = {}
        kinds: dict[str, str] = {}
        directory_inventories: dict[str, tuple[tuple[object, ...], ...]] = {}
        parents: dict[str, tuple[object, ...]] = {}
        for path in normalized:
            if not _inside(root, path):
                raise R17BoundaryError("protected_snapshot:path_outside_workspace")
            relative = path.relative_to(root).as_posix()
            present = os.path.lexists(path)
            existed[relative] = present
            if present:
                observed = os.lstat(path)
                if stat.S_ISLNK(observed.st_mode) or _has_reparse_attribute(observed):
                    raise R17BoundaryError(
                        f"protected_snapshot:reparse_forbidden:{relative}"
                    )
                if stat.S_ISREG(observed.st_mode):
                    if observed.st_nlink != 1:
                        raise R17BoundaryError(
                            f"protected_snapshot:hardlink_forbidden:{relative}"
                        )
                    kinds[relative] = "file"
                    graph.read(path)
                elif stat.S_ISDIR(observed.st_mode):
                    kinds[relative] = "directory"
                    directory_inventories[relative] = _closed_directory_inventory(
                        root, path
                    )
                    for current, _, file_names in os.walk(path, followlinks=False):
                        for name in sorted(file_names):
                            graph.read(Path(current) / name)
                else:
                    raise R17BoundaryError(
                        f"protected_snapshot:unsupported_leaf:{relative}"
                    )
            else:
                kinds[relative] = "absent"
                parent = path.parent
                if not parent.is_dir():
                    raise R17BoundaryError(
                        f"protected_snapshot:absent_parent_missing:{relative}"
                    )
                _guard_signature(root, parent)
                parents[parent.relative_to(root).as_posix()] = _stat_signature(parent)
        value = cls(
            root,
            normalized,
            graph,
            existed,
            kinds,
            directory_inventories,
            parents,
        )
        value.assert_unchanged(label="capture")
        return value

    def assert_unchanged(self, *, label: str) -> None:
        graph_result = self.present.revalidate()
        errors = list(graph_result.get("errors", []))
        for path in self.paths:
            relative = path.relative_to(self.workspace).as_posix()
            if os.path.lexists(path) != self.existed[relative]:
                errors.append(f"existence_changed:{relative}")
                continue
            if not self.existed[relative]:
                continue
            try:
                observed = os.lstat(path)
            except OSError as exc:
                errors.append(f"protected_path_unreadable:{relative}:{type(exc).__name__}")
                continue
            actual_kind = (
                "file"
                if stat.S_ISREG(observed.st_mode)
                else "directory"
                if stat.S_ISDIR(observed.st_mode)
                else "unsupported"
            )
            if actual_kind != self.kinds[relative]:
                errors.append(f"kind_changed:{relative}")
                continue
            if actual_kind == "directory":
                try:
                    actual_inventory = _closed_directory_inventory(self.workspace, path)
                except (OSError, R17BoundaryError) as exc:
                    errors.append(
                        f"directory_inventory_unreadable:{relative}:{type(exc).__name__}"
                    )
                    continue
                if actual_inventory != self.directory_inventories[relative]:
                    errors.append(f"directory_inventory_changed:{relative}")
        for relative, expected in self.absent_parent_signatures.items():
            parent = self.workspace / relative
            try:
                actual = _stat_signature(parent)
            except OSError as exc:
                errors.append(f"absent_parent_unreadable:{relative}:{type(exc).__name__}")
                continue
            if actual != expected:
                errors.append(f"absent_parent_changed:{relative}")
        if errors:
            raise R17BoundaryError(
                f"protected_snapshot:{label}:changed:" + ",".join(sorted(set(errors)))
            )


@dataclass(frozen=True)
class AttemptBuildLayout:
    workspace: Path
    store_root: Path
    attempt_id: str
    temporary_root: Path

    @classmethod
    def plan(
        cls,
        *,
        workspace: Path,
        store_root: Path,
        attempt_id: str,
        nonce: str | None = None,
    ) -> AttemptBuildLayout:
        if ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None:
            raise R17BoundaryError("attempt_layout:attempt_id_invalid")
        root = _absolute(workspace)
        store = _absolute(store_root)
        if not _inside(root, store):
            raise R17BoundaryError("attempt_layout:store_outside_workspace")
        token = nonce or uuid.uuid4().hex
        if re.fullmatch(r"[0-9a-f]{32}", token) is None:
            raise R17BoundaryError("attempt_layout:nonce_invalid")
        temporary = store / ".tmp" / f"{attempt_id}.{token}"
        return cls(root, store, attempt_id, temporary)

    @property
    def package_root(self) -> Path:
        return self.store_root / "packages" / self.attempt_id

    @property
    def reservation_path(self) -> Path:
        return self.store_root / "reservations" / f"{self.attempt_id}.json"

    @property
    def commit_path(self) -> Path:
        return self.store_root / "commits" / f"{self.attempt_id}.json"

    @property
    def candidate_dir(self) -> Path:
        return self.temporary_root / "candidate"

    @property
    def report_dir(self) -> Path:
        return self.temporary_root / "reports"

    @property
    def controller_dir(self) -> Path:
        return self.temporary_root / "controller"

    @property
    def figure_dir(self) -> Path:
        return self.temporary_root / "figures"

    @property
    def asset_dir(self) -> Path:
        return self.figure_dir / "assets"

    @property
    def coordination_dir(self) -> Path:
        return self.temporary_root / "coordination"

    @property
    def manifest_path(self) -> Path:
        return self.temporary_root / ATTEMPT_MANIFEST

    def relative(self, path: Path) -> str:
        absolute = _absolute(path)
        if not _inside(self.temporary_root, absolute):
            raise R17BoundaryError("attempt_layout:reference_outside_attempt")
        return absolute.relative_to(self.temporary_root).as_posix()

    def ref(self, path: Path) -> dict[str, object]:
        target = _absolute(path)
        payload = target.read_bytes()
        return {
            "path": self.relative(target),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }

    def reserve(self) -> dict[str, Any]:
        _mkdir_guarded(self.workspace, self.store_root)
        for parent in (
            self.store_root / "reservations",
            self.store_root / "packages",
            self.store_root / "commits",
            self.store_root / ".tmp",
        ):
            _mkdir_guarded(self.workspace, parent)
            folded = self.attempt_id.casefold()
            for child in parent.iterdir():
                stem = (
                    child.stem
                    if parent.name in {"reservations", "commits"}
                    else child.name.split(".")[0]
                )
                if stem.casefold() == folded:
                    raise FileExistsError(
                        f"attempt_layout:attempt_id_or_case_alias_already_used:{child.name}"
                    )
        reservation = {
            "schema_version": "1.0.0",
            "record_type": "r18_build_attempt_reservation",
            "attempt_id": self.attempt_id,
            "reusable": False,
            "evidence_authority": False,
            "publication_authorized": False,
        }
        reservation["self_hash"] = canonical_sha256(reservation)
        payload = (
            json.dumps(
                reservation,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        result = exclusive_create_bundle(
            workspace=self.workspace,
            files=[(self.reservation_path, payload)],
        )
        os.mkdir(self.temporary_root)
        return result


def _iter_json_paths(value: object, pointer: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if (
            {"path", "sha256", "bytes"} <= set(value)
            and isinstance(value.get("path"), str)
            and isinstance(value.get("sha256"), str)
            and isinstance(value.get("bytes"), int)
            and not isinstance(value.get("bytes"), bool)
        ):
            yield pointer, value
        for key, child in value.items():
            yield from _iter_json_paths(child, f"{pointer}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_json_paths(child, f"{pointer}/{index}")


def _iter_text_values(value: object, pointer: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_text_values(child, f"{pointer}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_text_values(child, f"{pointer}/{index}")
    elif isinstance(value, str):
        yield pointer, value


def validate_json_privacy(value: object, *, label: str) -> None:
    for pointer, text in _iter_text_values(value):
        for finding_type, pattern in PRIVATE_TEXT_PATTERNS:
            if pattern.search(text):
                raise R17BoundaryError(
                    f"attempt_package:private_text:{finding_type}:{label}:{pointer}"
                )


def _iter_named_path_values(
    value: object, pointer: str = "$"
) -> Iterable[tuple[str, object]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_pointer = f"{pointer}/{key}"
            # A small, explicit set of chemistry/topology contracts uses
            # ``*_path`` for structured domain paths.  All other named path
            # fields retain strict filesystem type/portability validation.
            structured_domain_path = (
                key in STRUCTURED_DOMAIN_PATH_KEYS
                and isinstance(child, (dict, list))
            )
            if (key == "path" or key.endswith("_path")) and not structured_domain_path:
                yield child_pointer, child
            yield from _iter_named_path_values(child, child_pointer)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_named_path_values(child, f"{pointer}/{index}")


def exact_path_binding_keys(
    value: dict[str, Any], key: str
) -> tuple[str, str] | None:
    """Resolve one unambiguous path-to-hash/bytes sibling convention."""

    if key != "path" and not key.endswith("_path"):
        return None
    candidate_keys: list[str] = []
    if key == "path":
        if (
            isinstance(value.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None
        ):
            candidate_keys.append("sha256")
        else:
            candidate_keys.extend(
                candidate
                for candidate in value
                if candidate.endswith("_file_sha256")
            )
    else:
        stem = key[:-5]
        candidate_keys.append(f"{stem}_sha256")
        path_fields = [
            candidate
            for candidate, candidate_value in value.items()
            if isinstance(candidate_value, str)
            and (candidate == "path" or candidate.endswith("_path"))
        ]
        if len(path_fields) == 1:
            candidate_keys.append("sha256")
    matches = [
        candidate
        for candidate in dict.fromkeys(candidate_keys)
        if isinstance(value.get(candidate), str)
        and re.fullmatch(r"[0-9a-f]{64}", value[candidate]) is not None
    ]
    if len(matches) > 1:
        raise R17BoundaryError(f"path_binding:ambiguous:{key}")
    if not matches:
        return None
    sha_key = matches[0]
    bytes_key = (
        "bytes"
        if key == "path" or sha_key == "sha256"
        else f"{key[:-5]}_bytes"
    )
    return sha_key, bytes_key


def _iter_byte_bound_paths(
    value: object, pointer: str = "$"
) -> Iterable[tuple[str, str, str, int | None]]:
    if isinstance(value, dict):
        for key, raw_path in value.items():
            if not isinstance(raw_path, str) or not (
                key == "path" or key.endswith("_path")
            ):
                continue
            try:
                binding = exact_path_binding_keys(value, key)
            except R17BoundaryError as exc:
                raise R17BoundaryError(
                    f"attempt_package:ambiguous_path_hash_binding:{pointer}/{key}"
                ) from exc
            if binding is not None:
                sha_key, bytes_key = binding
                expected_bytes = value.get(bytes_key)
                if bytes_key in value and (
                    isinstance(expected_bytes, bool)
                    or not isinstance(expected_bytes, int)
                    or expected_bytes < 0
                ):
                    raise R17BoundaryError(
                        f"attempt_package:invalid_path_bytes_binding:{pointer}/{key}"
                    )
                if bytes_key not in value:
                    expected_bytes = None
                yield (
                    f"{pointer}/{key}",
                    raw_path,
                    value[sha_key],
                    expected_bytes,
                )
        for key, child in value.items():
            yield from _iter_byte_bound_paths(child, f"{pointer}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_byte_bound_paths(child, f"{pointer}/{index}")


def _manifest_value(
    layout: AttemptBuildLayout,
    *,
    build_mode: str,
) -> dict[str, Any]:
    if build_mode not in {PRODUCTION_BUILD_MODE, TEST_FIXTURE_BUILD_MODE}:
        raise R17BoundaryError("attempt_manifest:build_mode_invalid")
    rows = []
    for path in sorted(
        (item for item in layout.temporary_root.rglob("*") if item.is_file()),
        key=lambda item: layout.relative(item),
    ):
        if path == layout.manifest_path:
            continue
        rows.append(layout.ref(path))
    inventory_sha256 = canonical_sha256(rows)
    value: dict[str, Any] = {
        "schema_version": "1.0.0",
        "record_type": "r18_attempt_build_manifest",
        "attempt_id": layout.attempt_id,
        "build_mode": build_mode,
        "package_state": "validated_ready_for_atomic_directory_commit",
        "file_count": len(rows),
        "inventory_sha256": inventory_sha256,
        "files": rows,
        "required_core_paths": sorted(REQUIRED_CORE_PATHS),
        "legacy_fixed_paths_used_as_inputs": False,
        "current_pointer_updated": False,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "publication_allowed": False,
    }
    value["self_hash"] = canonical_sha256(value)
    return value


def write_attempt_manifest(
    layout: AttemptBuildLayout,
    *,
    build_mode: str,
) -> dict[str, Any]:
    if layout.manifest_path.exists():
        raise FileExistsError("attempt_manifest:already_exists")
    value = _manifest_value(layout, build_mode=build_mode)
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    exclusive_create_bundle(
        workspace=layout.workspace,
        files=[(layout.manifest_path, payload)],
    )
    return value


def _snapshot_json_object(
    graph: SnapshotGraph,
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        value = graph.read(path).json_value()
    except (OSError, UnicodeError, ValueError) as exc:
        raise R17BoundaryError(
            f"attempt_package:{label}_unreadable:{type(exc).__name__}"
        ) from exc
    if not isinstance(value, dict):
        raise R17BoundaryError(f"attempt_package:{label}_object_required")
    return value


def _record_hash_matches(record: dict[str, Any], field: str) -> bool:
    unsigned = copy.deepcopy(record)
    claimed = unsigned.pop(field, None)
    return isinstance(claimed, str) and claimed == canonical_sha256(unsigned)


def _validate_production_contract(
    root: Path,
    *,
    package_graph: SnapshotGraph,
) -> dict[str, Any]:
    """Validate trusted schemas plus cross-document R18 prefreeze semantics."""

    trusted_graph = SnapshotGraph(TRUSTED_WORKSPACE)
    documents: dict[str, dict[str, Any]] = {}
    for document_relative, (
        packaged_schema_relative,
        trusted_schema_relative,
    ) in PRODUCTION_SCHEMA_BINDINGS.items():
        document_path = root / document_relative
        packaged_schema_path = root / packaged_schema_relative
        trusted_schema_path = TRUSTED_WORKSPACE / trusted_schema_relative
        packaged_schema = package_graph.read(packaged_schema_path)
        trusted_schema = trusted_graph.read(trusted_schema_path)
        if packaged_schema.data != trusted_schema.data:
            raise R17BoundaryError(
                f"attempt_package:trusted_schema_byte_mismatch:{packaged_schema_relative}"
            )
        document = _snapshot_json_object(
            package_graph,
            document_path,
            label=document_relative,
        )
        try:
            trusted_schema_value = json.loads(trusted_schema.data.decode("utf-8"))
            validate(document, trusted_schema_value)
        except (UnicodeError, ValueError, SchemaValidationError) as exc:
            raise R17BoundaryError(
                f"attempt_package:production_schema_invalid:{document_relative}:{exc}"
            ) from exc
        documents[document_relative] = document

    metadata = documents["inputs/execution/generator_execution_metadata.json"]
    paper = documents["candidate/frozen_paper.json"]
    task_card = documents["candidate/task_card.json"]
    sol = documents["candidate/sol_generator_receipt.json"]
    producer = documents["candidate/r18_producer_receipt.json"]
    provenance = documents["candidate/generator_provenance_receipt.json"]
    request = documents["controller/deterministic_check_request.json"]
    report = documents["controller/deterministic_check_report.json"]

    versioned = (metadata, paper, task_card, sol, producer, provenance)
    if any(record.get("version_id") != VERSION_ID for record in versioned):
        raise R17BoundaryError("attempt_package:production_version_binding_mismatch")
    paper_bound = (paper, task_card, sol, producer, provenance)
    if any(record.get("paper_id") != PAPER_ID for record in paper_bound):
        raise R17BoundaryError("attempt_package:production_paper_binding_mismatch")
    execution = metadata.get("reported_execution")
    request_subject = request.get("subject")
    subject_pair = (
        request_subject.get("subject_pair_sha256")
        if isinstance(request_subject, dict)
        else None
    )
    expected_provenance_receipt_id = (
        f"GENERATOR-PROVENANCE-R18-{PAPER_ID}-{subject_pair[:16]}"
        if isinstance(subject_pair, str)
        else None
    )
    if (
        sol.get("execution_provenance") != execution
        or provenance.get("reported_execution") != execution
        or sol.get("receipt_id") != execution.get("receipt_id")
        or provenance.get("receipt_id") != expected_provenance_receipt_id
        or provenance.get("subject_pair_sha256") != subject_pair
        or provenance.get("controller_subject") != request_subject
    ):
        raise R17BoundaryError("attempt_package:execution_provenance_not_exact")
    if not _record_hash_matches(sol, "output_sha256"):
        raise R17BoundaryError("attempt_package:sol_receipt_self_hash_mismatch")
    if not _record_hash_matches(producer, "self_hash"):
        raise R17BoundaryError("attempt_package:producer_receipt_self_hash_mismatch")
    if not _record_hash_matches(provenance, "self_hash"):
        raise R17BoundaryError("attempt_package:provenance_receipt_self_hash_mismatch")
    report_subject = report.get("subject")
    report_subject_projection = (
        {
            key: report_subject.get(key)
            for key in ("question", "answer", "subject_pair_sha256")
        }
        if isinstance(report_subject, dict)
        else None
    )
    if (
        report.get("passed") is not True
        or report.get("human_reviewed") is not False
        or request.get("human_reviewed") is not False
        or report_subject_projection != request.get("subject")
        or report_subject.get("question_path_valid") is not True
        or report_subject.get("answer_path_valid") is not True
    ):
        raise R17BoundaryError("attempt_package:deterministic_contract_not_passed")

    prefreeze = _snapshot_json_object(
        package_graph,
        root / "coordination/prefreeze_receipt_r18.json",
        label="prefreeze_receipt",
    )
    delivery = _snapshot_json_object(
        package_graph,
        root / "candidate/delivery_status.json",
        label="delivery_status",
    )
    fingerprints = (
        prefreeze.get("producer_fingerprint"),
        sol.get("producer_fingerprint"),
        producer.get("producer_fingerprint"),
    )
    if not all(isinstance(value, dict) for value in fingerprints) or not (
        fingerprints[0] == fingerprints[1] == fingerprints[2]
    ):
        raise R17BoundaryError("attempt_package:producer_fingerprint_mismatch")
    verified_fingerprint = validate_claimed_producer_fingerprint(
        reference_root=root,
        claimed=fingerprints[0],
        graph=package_graph,
    )
    if (
        prefreeze.get("record_type") != "generation_v2_prefreeze_self_check"
        or prefreeze.get("version_id") != VERSION_ID
        or prefreeze.get("content_status")
        != "prefreeze_self_check_pass_root_review_required"
        or prefreeze.get("root_external_reconciliation_verified") is not False
        or prefreeze.get("review_dispatch_created") is not False
        or prefreeze.get("governance_chain_created") is not False
        or prefreeze.get("controller_registration_requested") is not False
        or prefreeze.get("publication_requested") is not False
        or prefreeze.get("human_reviewed") is not False
        or prefreeze.get("publication_allowed") is not False
    ):
        raise R17BoundaryError("attempt_package:prefreeze_boundary_invalid")
    if (
        delivery.get("record_type") != "generation_v2_prefreeze_delivery_status"
        or delivery.get("teacher_managed_delivery_candidate") is not False
        or delivery.get("delivery_scope") is not None
        or delivery.get("teacher_action_required") is not False
        or delivery.get("human_reviewed") is not False
        or delivery.get("teaching_use_allowed") is not False
        or delivery.get("external_publication_allowed") is not False
        or delivery.get("official_claim_allowed") is not False
        or delivery.get("official_publication_allowed") is not False
        or delivery.get("publication_allowed") is not False
        or delivery.get("release_allowed") is not False
    ):
        raise R17BoundaryError("attempt_package:delivery_boundary_invalid")
    legacy_snapshots = {
        relative: package_graph.read(root / relative)
        for relative in OPAQUE_LEGACY_JSON_PATHS
    }
    r18_invalidation_relative = (
        "inputs/legacy_records/R18_PREFREEZE_INVALIDATED_CONTENT_METADATA.json"
    )
    r13_initial_relative = "inputs/legacy_records/R13_FREEZE_INVALIDATED.json"
    r13_correction_relative = (
        "inputs/legacy_records/R13_FREEZE_INVALIDATION_CORRECTION.json"
    )
    if (
        prefreeze.get("superseded_prefreeze_invalidation")
        != legacy_snapshots[r18_invalidation_relative].ref(root)
        or prefreeze.get("predecessor_invalidation", {}).get(
            "initial_authoritative_record"
        )
        != legacy_snapshots[r13_initial_relative].ref(root)
        or prefreeze.get("predecessor_invalidation", {}).get("drift_correction")
        != legacy_snapshots[r13_correction_relative].ref(root)
    ):
        raise R17BoundaryError("attempt_package:opaque_legacy_binding_mismatch")
    invalidation_report = _snapshot_json_object(
        package_graph,
        root / "reports/r13_invalidation_correction.json",
        label="r13_invalidation_correction_report",
    )
    if (
        invalidation_report.get("status") != "pass"
        or invalidation_report.get("r13_invalidated") is not True
        or invalidation_report.get("r13_active") is not False
        or invalidation_report.get("errors") != []
        or invalidation_report.get("initial_sha256")
        != legacy_snapshots[r13_initial_relative].sha256
        or invalidation_report.get("correction_sha256")
        != legacy_snapshots[r13_correction_relative].sha256
    ):
        raise R17BoundaryError("attempt_package:opaque_legacy_validation_invalid")
    checkpoint = trusted_graph.revalidate()
    if checkpoint.get("status") != "pass":
        raise R17BoundaryError("attempt_package:trusted_schema_changed_during_validation")
    return {
        "validated_schema_count": len(PRODUCTION_SCHEMA_BINDINGS),
        "execution_receipt_id": execution["receipt_id"],
        "producer_source_tree_sha256": verified_fingerprint[
            "source_tree_sha256"
        ],
    }


def validate_attempt_package(
    package_root: Path,
    *,
    expected_attempt_id: str,
    allow_test_fixture: bool = False,
) -> dict[str, Any]:
    root = _absolute(package_root)
    if ATTEMPT_ID_PATTERN.fullmatch(expected_attempt_id) is None:
        raise R17BoundaryError("attempt_package:attempt_id_invalid")
    tree = assert_safe_regular_tree(root)
    package_graph = SnapshotGraph(root)
    manifest_path = root / ATTEMPT_MANIFEST
    manifest = _snapshot_json_object(
        package_graph,
        manifest_path,
        label="manifest",
    )
    if manifest.get("attempt_id") != expected_attempt_id:
        raise R17BoundaryError("attempt_package:attempt_id_mismatch")
    expected_manifest_fields = {
        "schema_version",
        "record_type",
        "attempt_id",
        "build_mode",
        "package_state",
        "file_count",
        "inventory_sha256",
        "files",
        "required_core_paths",
        "legacy_fixed_paths_used_as_inputs",
        "current_pointer_updated",
        "human_reviewed",
        "teaching_use_allowed",
        "publication_allowed",
        "self_hash",
    }
    if set(manifest) != expected_manifest_fields:
        raise R17BoundaryError("attempt_package:manifest_fields_not_exact")
    build_mode = manifest.get("build_mode")
    if build_mode == TEST_FIXTURE_BUILD_MODE and not allow_test_fixture:
        raise R17BoundaryError("attempt_package:test_fixture_not_selectable")
    if build_mode not in {PRODUCTION_BUILD_MODE, TEST_FIXTURE_BUILD_MODE}:
        raise R17BoundaryError("attempt_package:build_mode_invalid")
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("record_type") != "r18_attempt_build_manifest"
        or manifest.get("package_state")
        != "validated_ready_for_atomic_directory_commit"
        or manifest.get("required_core_paths") != sorted(REQUIRED_CORE_PATHS)
        or manifest.get("legacy_fixed_paths_used_as_inputs") is not False
        or manifest.get("current_pointer_updated") is not False
        or manifest.get("human_reviewed") is not False
        or manifest.get("teaching_use_allowed") is not False
        or manifest.get("publication_allowed") is not False
    ):
        raise R17BoundaryError("attempt_package:manifest_boundary_invalid")
    unsigned = copy.deepcopy(manifest)
    claimed = unsigned.pop("self_hash", None)
    if claimed != canonical_sha256(unsigned):
        raise R17BoundaryError("attempt_package:manifest_self_hash_mismatch")
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise R17BoundaryError("attempt_package:inventory_missing")
    if manifest.get("file_count") != len(rows):
        raise R17BoundaryError("attempt_package:file_count_mismatch")
    if manifest.get("inventory_sha256") != canonical_sha256(rows):
        raise R17BoundaryError("attempt_package:inventory_hash_mismatch")
    declared: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"}:
            raise R17BoundaryError("attempt_package:inventory_ref_not_exact")
        raw = row.get("path")
        if portable_relative_path_error(raw) is not None:
            raise R17BoundaryError(f"attempt_package:path_not_portable:{raw}")
        assert isinstance(raw, str)
        folded = raw.casefold()
        if folded in declared:
            raise R17BoundaryError(f"attempt_package:casefold_alias:{raw}")
        declared[folded] = row
        path = root / raw
        try:
            payload = package_graph.read(path).data
        except (OSError, R17BoundaryError) as exc:
            raise R17BoundaryError(
                f"attempt_package:declared_file_missing_or_unsafe:{raw}"
            ) from exc
        if row.get("sha256") != hashlib.sha256(payload).hexdigest():
            raise R17BoundaryError(f"attempt_package:declared_hash_mismatch:{raw}")
        if row.get("bytes") != len(payload):
            raise R17BoundaryError(f"attempt_package:declared_bytes_mismatch:{raw}")
    actual = {
        path.relative_to(root).as_posix().casefold()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != set(declared):
        raise R17BoundaryError("attempt_package:unmanaged_or_missing_inventory_member")
    if not REQUIRED_CORE_PATHS <= {row["path"] for row in rows}:
        missing = sorted(REQUIRED_CORE_PATHS - {row["path"] for row in rows})
        raise R17BoundaryError(f"attempt_package:required_core_missing:{missing}")

    production = (
        _validate_production_contract(root, package_graph=package_graph)
        if build_mode == PRODUCTION_BUILD_MODE
        else {"validated_schema_count": 0}
    )

    ref_count = 0
    json_paths = [
        root / row["path"]
        for row in rows
        if str(row["path"]).lower().endswith(".json")
    ] + [manifest_path]
    for path in sorted(json_paths, key=lambda item: item.relative_to(root).as_posix()):
        relative_json_path = path.relative_to(root).as_posix()
        try:
            document = package_graph.read(path).json_value()
        except (OSError, UnicodeError, ValueError) as exc:
            raise R17BoundaryError(
                f"attempt_package:json_unreadable:{path.name}:{type(exc).__name__}"
            ) from exc
        # JSON Schema ``properties.path`` entries describe a future value; the
        # child is itself a schema object, not a runtime filesystem locator.
        # Trusted schema bytes were already matched above, so reference closure
        # applies only to non-schema package records.
        if "schemas" in path.relative_to(root).parts:
            continue
        validate_json_privacy(document, label=relative_json_path)
        if json_path_is_opaque_bound_evidence(relative_json_path):
            continue
        for pointer, raw_path in _iter_named_path_values(document):
            if not isinstance(raw_path, str):
                raise R17BoundaryError(
                    f"attempt_package:named_path_not_string:{path.name}:{pointer}"
                )
            if portable_relative_path_error(raw_path) is not None:
                raise R17BoundaryError(
                    f"attempt_package:host_or_nonportable_named_path:{path.name}:{pointer}"
                )
            try:
                locator_file = portable_locator_file(raw_path)
            except R17BoundaryError as exc:
                raise R17BoundaryError(
                    f"attempt_package:named_path_locator_invalid:{path.name}:{pointer}"
                ) from exc
            if (
                not os.path.lexists(root / locator_file)
                and not named_path_is_unbound_output(pointer)
            ):
                raise R17BoundaryError(
                    f"attempt_package:named_path_outside_attempt_or_missing:{path.name}:{pointer}"
                )
        for pointer, raw_path, expected_sha256, expected_bytes in _iter_byte_bound_paths(
            document
        ):
            if portable_relative_path_error(raw_path) is not None:
                raise R17BoundaryError(
                    f"attempt_package:bound_path_not_portable:{path.name}:{pointer}"
                )
            target = root / raw_path
            try:
                payload = package_graph.read(target).data
            except (OSError, R17BoundaryError) as exc:
                raise R17BoundaryError(
                    f"attempt_package:bound_path_outside_attempt_or_missing:{path.name}:{pointer}"
                ) from exc
            if hashlib.sha256(payload).hexdigest() != expected_sha256:
                raise R17BoundaryError(
                    f"attempt_package:bound_path_hash_mismatch:{path.name}:{pointer}"
                )
            if expected_bytes is not None and len(payload) != expected_bytes:
                raise R17BoundaryError(
                    f"attempt_package:bound_path_bytes_mismatch:{path.name}:{pointer}"
                )
        for pointer, reference in _iter_json_paths(document):
            raw = reference.get("path")
            if portable_relative_path_error(raw) is not None:
                raise R17BoundaryError(
                    f"attempt_package:ref_path_not_portable:{path.name}:{pointer}"
                )
            assert isinstance(raw, str)
            target = root / raw
            try:
                payload = package_graph.read(target).data
            except (OSError, R17BoundaryError) as exc:
                raise R17BoundaryError(
                    f"attempt_package:ref_outside_attempt_or_missing:{path.name}:{pointer}:{raw}"
                ) from exc
            if reference.get("sha256") != hashlib.sha256(payload).hexdigest():
                raise R17BoundaryError(
                    f"attempt_package:ref_hash_mismatch:{path.name}:{pointer}:{raw}"
                )
            if reference.get("bytes") != len(payload):
                raise R17BoundaryError(
                    f"attempt_package:ref_bytes_mismatch:{path.name}:{pointer}:{raw}"
                )
            ref_count += 1
    checkpoint = package_graph.revalidate()
    if checkpoint.get("status") != "pass":
        raise R17BoundaryError(
            "attempt_package:snapshot_changed_during_validation:"
            + ",".join(checkpoint.get("errors", []))
        )
    if assert_safe_regular_tree(root) != tree:
        raise R17BoundaryError("attempt_package:tree_changed_during_validation")
    return {
        "status": "pass",
        "attempt_id": expected_attempt_id,
        "build_mode": build_mode,
        "file_count": tree["file_count"],
        "inventory_file_count": len(rows),
        "verified_reference_count": ref_count,
        "inventory_sha256": manifest["inventory_sha256"],
        "current_pointer_updated": False,
        "publication_allowed": False,
        **production,
    }


def _write_commit_attestation(
    layout: AttemptBuildLayout,
    *,
    validation: dict[str, Any],
) -> dict[str, Any]:
    graph = SnapshotGraph(layout.workspace)
    manifest_payload = graph.read(
        layout.package_root / ATTEMPT_MANIFEST
    ).data
    tree = assert_safe_regular_tree(layout.package_root)
    record: dict[str, Any] = {
        "schema_version": "1.0.0",
        "record_type": "r18_attempt_commit_attestation",
        "attempt_id": layout.attempt_id,
        "build_mode": validation["build_mode"],
        "package_path": layout.package_root.relative_to(layout.workspace).as_posix(),
        "manifest": {
            "path": layout.package_root.joinpath(ATTEMPT_MANIFEST)
            .relative_to(layout.workspace)
            .as_posix(),
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
            "bytes": len(manifest_payload),
        },
        "inventory_sha256": validation["inventory_sha256"],
        "package_tree_identity_sha256": tree["identity_sha256"],
        "committed": True,
        "current_pointer_updated": False,
        "publication_allowed": False,
    }
    record["self_hash"] = canonical_sha256(record)
    payload = (
        json.dumps(
            record,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    exclusive_create_bundle(
        workspace=layout.workspace,
        files=[(layout.commit_path, payload)],
    )
    checkpoint = graph.revalidate()
    if checkpoint.get("status") != "pass":
        raise R17BoundaryError("attempt_commit_attestation:package_manifest_changed")
    return record


def _safe_remove_attempt_tree(layout: AttemptBuildLayout, path: Path) -> None:
    target = _absolute(path)
    allowed = {_absolute(layout.temporary_root), _absolute(layout.package_root)}
    if target not in allowed or not _inside(layout.store_root, target):
        raise R17BoundaryError("attempt_cleanup:target_not_exact_allowlisted_tree")
    if not os.path.lexists(target):
        return
    observed = os.lstat(target)
    if stat.S_ISLNK(observed.st_mode) or _has_reparse_attribute(observed):
        raise R17BoundaryError("attempt_cleanup:reparse_root_forbidden")
    if not stat.S_ISDIR(observed.st_mode):
        raise R17BoundaryError("attempt_cleanup:root_not_directory")
    # Validate the whole tree without following links, but permit hard-linked
    # regular leaves to be unlinked from this exact attempt root.  Following a
    # directory reparse point is never allowed during cleanup.
    for current, directory_names, file_names in os.walk(target, followlinks=False):
        current_path = Path(current)
        for name in directory_names:
            child = current_path / name
            child_stat = os.lstat(child)
            if stat.S_ISLNK(child_stat.st_mode) or _has_reparse_attribute(child_stat):
                raise R17BoundaryError("attempt_cleanup:internal_reparse_forbidden")
            if not stat.S_ISDIR(child_stat.st_mode):
                raise R17BoundaryError("attempt_cleanup:directory_identity_invalid")
        for name in file_names:
            child = current_path / name
            child_stat = os.lstat(child)
            if stat.S_ISLNK(child_stat.st_mode) or _has_reparse_attribute(child_stat):
                raise R17BoundaryError("attempt_cleanup:internal_reparse_forbidden")
            if not stat.S_ISREG(child_stat.st_mode):
                raise R17BoundaryError("attempt_cleanup:non_regular_leaf")
    shutil.rmtree(target)


def _safe_remove_commit_attestation(layout: AttemptBuildLayout) -> None:
    target = _absolute(layout.commit_path)
    if not os.path.lexists(target):
        return
    if not _inside(layout.store_root, target):
        raise R17BoundaryError("attempt_cleanup:commit_outside_store")
    observed = os.lstat(target)
    if (
        stat.S_ISLNK(observed.st_mode)
        or _has_reparse_attribute(observed)
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_nlink != 1
    ):
        raise R17BoundaryError("attempt_cleanup:commit_leaf_unsafe")
    target.unlink()


def execute_attempt_transaction(
    *,
    layout: AttemptBuildLayout,
    protected: ProtectedPathSnapshot,
    builder: Callable[[AttemptBuildLayout], None],
    build_mode: str = PRODUCTION_BUILD_MODE,
    fault_injector: Callable[[str, AttemptBuildLayout], None] | None = None,
) -> dict[str, Any]:
    """Run a non-reusable build attempt and publish no pointer or live output."""

    def fault(phase: str) -> None:
        if fault_injector is not None:
            fault_injector(phase, layout)

    if _absolute(protected.workspace) != _absolute(layout.workspace):
        raise R17BoundaryError("attempt_transaction:protected_workspace_mismatch")
    protected.assert_unchanged(label="before_any_write")
    layout.reserve()
    commit_attested = False
    try:
        fault("after_reservation")
        builder(layout)
        fault("before_manifest")
        write_attempt_manifest(layout, build_mode=build_mode)
        validate_attempt_package(
            layout.temporary_root,
            expected_attempt_id=layout.attempt_id,
            allow_test_fixture=build_mode == TEST_FIXTURE_BUILD_MODE,
        )
        protected.assert_unchanged(label="before_commit")
        fault("before_commit")
        commit = atomic_commit_directory(
            workspace=layout.workspace,
            staged_root=layout.temporary_root,
            destination=layout.package_root,
        )
        fault("after_commit")
        result = validate_attempt_package(
            layout.package_root,
            expected_attempt_id=layout.attempt_id,
            allow_test_fixture=build_mode == TEST_FIXTURE_BUILD_MODE,
        )
        protected.assert_unchanged(label="after_commit")
        commit_record = _write_commit_attestation(layout, validation=result)
        commit_attested = True
        protected.assert_unchanged(label="after_commit_attestation")
        return {
            **result,
            **commit,
            "reservation_retained": True,
            "commit_attestation_retained": True,
            "commit_attestation_self_hash": commit_record["self_hash"],
        }
    except Exception as original:
        cleanup_errors: list[str] = []
        if commit_attested or os.path.lexists(layout.commit_path):
            try:
                _safe_remove_commit_attestation(layout)
            except Exception as exc:  # cleanup errors are fail-closed evidence
                cleanup_errors.append(f"commit:{type(exc).__name__}:{exc}")
        for label, cleanup in (
            ("package", layout.package_root),
            ("temporary", layout.temporary_root),
        ):
            try:
                _safe_remove_attempt_tree(layout, cleanup)
            except Exception as exc:  # cleanup errors leave no selectable commit marker
                cleanup_errors.append(f"{label}:{type(exc).__name__}:{exc}")
        if cleanup_errors:
            raise R17BoundaryError(
                "attempt_transaction:cleanup_incomplete_unselectable:"
                + ",".join(cleanup_errors)
            ) from original
        raise


def select_immutable_attempt(
    *,
    workspace: Path,
    store_root: Path,
    attempt_id: str,
) -> dict[str, Any]:
    """Select only a committed attempt package; legacy live is never a source."""

    if ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None:
        raise R17BoundaryError("attempt_selector:attempt_id_invalid")
    workspace_root = _absolute(workspace)
    store = _absolute(store_root)
    if not _inside(workspace_root, store):
        raise R17BoundaryError("attempt_selector:store_outside_workspace")
    _guard_signature(workspace_root, store)
    package_parent = store / "packages"
    reservation_parent = store / "reservations"
    commit_parent = store / "commits"
    for label, parent in (
        ("packages", package_parent),
        ("reservations", reservation_parent),
        ("commits", commit_parent),
    ):
        if not parent.is_dir():
            raise R17BoundaryError(f"attempt_selector:{label}_directory_missing")
        _guard_signature(workspace_root, parent)
    package_aliases = [
        child
        for child in package_parent.iterdir()
        if child.name.casefold() == attempt_id.casefold()
    ] if package_parent.is_dir() else []
    reservation_name = f"{attempt_id}.json"
    reservation_aliases = [
        child
        for child in reservation_parent.iterdir()
        if child.name.casefold() == reservation_name.casefold()
    ]
    commit_aliases = [
        child
        for child in commit_parent.iterdir()
        if child.name.casefold() == reservation_name.casefold()
    ]
    if (
        len(package_aliases) != 1
        or package_aliases[0].name != attempt_id
        or len(reservation_aliases) != 1
        or reservation_aliases[0].name != reservation_name
        or len(commit_aliases) != 1
        or commit_aliases[0].name != reservation_name
    ):
        raise R17BoundaryError("attempt_selector:missing_or_casefold_alias")
    package = package_aliases[0]
    reservation = reservation_aliases[0]
    commit_path = commit_aliases[0]
    if not reservation.is_file() or not commit_path.is_file() or not package.is_dir():
        raise R17BoundaryError("attempt_selector:not_committed")
    _guard_signature(workspace_root, package)
    _guard_signature(workspace_root, reservation)
    _guard_signature(workspace_root, commit_path)
    graph = SnapshotGraph(workspace_root)
    reservation_record = _snapshot_json_object(
        graph,
        reservation,
        label="selector_reservation",
    )
    unsigned = copy.deepcopy(reservation_record)
    claimed = unsigned.pop("self_hash", None) if isinstance(unsigned, dict) else None
    if (
        not isinstance(reservation_record, dict)
        or set(reservation_record)
        != {
            "schema_version",
            "record_type",
            "attempt_id",
            "reusable",
            "evidence_authority",
            "publication_authorized",
            "self_hash",
        }
        or reservation_record.get("schema_version") != "1.0.0"
        or reservation_record.get("record_type") != "r18_build_attempt_reservation"
        or reservation_record.get("attempt_id") != attempt_id
        or reservation_record.get("reusable") is not False
        or reservation_record.get("evidence_authority") is not False
        or reservation_record.get("publication_authorized") is not False
        or claimed != canonical_sha256(unsigned)
    ):
        raise R17BoundaryError("attempt_selector:reservation_contract_invalid")

    commit_record = _snapshot_json_object(
        graph,
        commit_path,
        label="selector_commit_attestation",
    )
    commit_unsigned = copy.deepcopy(commit_record)
    commit_claimed = commit_unsigned.pop("self_hash", None)
    expected_commit_fields = {
        "schema_version",
        "record_type",
        "attempt_id",
        "build_mode",
        "package_path",
        "manifest",
        "inventory_sha256",
        "package_tree_identity_sha256",
        "committed",
        "current_pointer_updated",
        "publication_allowed",
        "self_hash",
    }
    expected_package_relative = package.relative_to(workspace_root).as_posix()
    expected_manifest_relative = package.joinpath(ATTEMPT_MANIFEST).relative_to(
        workspace_root
    ).as_posix()
    manifest_ref = commit_record.get("manifest")
    if (
        set(commit_record) != expected_commit_fields
        or commit_record.get("schema_version") != "1.0.0"
        or commit_record.get("record_type") != "r18_attempt_commit_attestation"
        or commit_record.get("attempt_id") != attempt_id
        or commit_record.get("build_mode") != PRODUCTION_BUILD_MODE
        or commit_record.get("package_path") != expected_package_relative
        or commit_record.get("committed") is not True
        or commit_record.get("current_pointer_updated") is not False
        or commit_record.get("publication_allowed") is not False
        or commit_claimed != canonical_sha256(commit_unsigned)
        or not isinstance(manifest_ref, dict)
        or set(manifest_ref) != {"path", "sha256", "bytes"}
        or manifest_ref.get("path") != expected_manifest_relative
    ):
        raise R17BoundaryError("attempt_selector:commit_contract_invalid")
    manifest_snapshot = graph.read(package / ATTEMPT_MANIFEST)
    if (
        manifest_ref.get("sha256") != manifest_snapshot.sha256
        or manifest_ref.get("bytes") != manifest_snapshot.byte_length
    ):
        raise R17BoundaryError("attempt_selector:commit_manifest_binding_invalid")
    result = validate_attempt_package(package, expected_attempt_id=attempt_id)
    tree = assert_safe_regular_tree(package)
    if (
        commit_record.get("inventory_sha256") != result.get("inventory_sha256")
        or commit_record.get("package_tree_identity_sha256")
        != tree.get("identity_sha256")
    ):
        raise R17BoundaryError("attempt_selector:commit_package_binding_invalid")
    checkpoint = graph.revalidate()
    if checkpoint.get("status") != "pass":
        raise R17BoundaryError("attempt_selector:control_record_changed_during_selection")
    return {
        **result,
        "package_root": package,
        "commit_attestation": commit_path,
        "legacy_live_selected": False,
    }

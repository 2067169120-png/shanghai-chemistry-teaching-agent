from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from PIL import Image

from .adapter import bind_observed_profile, rebind_task_card
from .attempt_build import (
    PRODUCTION_BUILD_MODE,
    AttemptBuildLayout,
    ProtectedPathSnapshot,
    compute_owned_producer_fingerprint,
    exact_path_binding_keys,
    execute_attempt_transaction,
    json_path_is_opaque_bound_evidence,
    named_path_is_unbound_output,
    portable_locator_file,
    select_immutable_attempt,
    validate_json_privacy,
)
from .classification import classification_vocabulary_mutation_report
from .content import (
    COMPONENT_REGISTRY_FILENAME,
    FIGURE_ID,
    VERSION_ID,
    build_paper,
    build_week_plan,
)
from .content_metadata import (
    blocked_page_anchor,
    content_metadata_mutation_report,
    validate_content_metadata_contract,
)
from .core import (
    build_component_registry,
    figure_spec,
    figure_svg,
    iter_parts,
    score_major_substance_name_response,
    sha256_file,
    source_crop_hashes,
    validate_component_registry,
    validate_cross_question_leakage,
    validate_dedup,
    validate_display_answer,
    validate_equations,
    validate_figure,
    validate_paper,
    validate_solvers,
    write_json,
)
from .coverage import build_coverage_matrix, validate_coverage_matrix
from .governance_bridge import (
    ADVERSARIAL_PATH,
    ANSWER_PATH,
    BRIDGE_DIR,
    QUESTION_PATH,
    REPORT_PATH,
    REQUEST_PATH,
    REVIEW_A_PATH,
    REVIEW_B_PATH,
    ControllerBuildLayout,
    _question_subject_projection,
    canonical_hash,
    create_phase1_review_dispatch_activation,
    create_phase2_adversarial_dispatch,
    generator_execution_provenance,
    generator_execution_metadata_binding,
    prepare_controller_subject,
    r18_phase1_activation_id,
    r18_phase1_schema_paths,
    r18_phase2_output_paths,
    r18_review_attempt_output_paths,
    record_output_sha256,
    render_phase1_reviewer_prompt,
    review_subject_from_deterministic_request,
    validate_question_subject_isolation,
    validate_student_visible_prompt_safety,
)
from .hierarchy import hierarchy_ids
from .invalidation_audit import validate_r13_invalidation_correction
from .r17_boundary import (
    FORMAL_SCHEMA_RELATIVE,
    FORMAL_SIDECAR_SCHEMA_RELATIVE,
    SnapshotGraph,
    _has_reparse_attribute,
    _mkdir_guarded,
    exclusive_create_bundle,
    portable_relative_path_error,
    write_generator_provenance_receipt_r18,
)
from .schema_validation import load_schema, validate

WORKSPACE = Path(__file__).resolve().parents[2]
INTEGRATION = WORKSPACE / "integrations" / "shchem_generation_v2"
STAGING = WORKSPACE / "staging" / "v1_generation"
COORDINATION = WORKSPACE / "staging" / "coordination" / "generation_publication"
FIGURES = WORKSPACE / "sh-chem-db" / "kb" / "figures" / "machine_v2"
CANDIDATE_DIR = STAGING / "candidates" / "r18"
REPORT_DIR = STAGING / "reports" / "r18"
REVIEW_DIR = STAGING / "reviews" / "r18"
ASSET_DIR = FIGURES / "assets"
COVERAGE_PATH = REPORT_DIR / "coverage_matrix.json"
DETERMINISTIC_SCOPE_PATH = REPORT_DIR / "deterministic_atomic_scope.json"
FIGURE_VISUAL_DIR = REPORT_DIR / "figure_visual_qa"
REVIEW_PROMPT_A_PATH = BRIDGE_DIR / "REVIEW_PROMPT_A.md"
REVIEW_PROMPT_B_PATH = BRIDGE_DIR / "REVIEW_PROMPT_B.md"
ADVERSARIAL_PROMPT_PATH = BRIDGE_DIR / "ADVERSARIAL_PROMPT.md"
REVIEW_DISPATCH_PATH = COORDINATION / "REVIEW_DISPATCH_REQUEST_R18.json"
SOL_GENERATOR_RECEIPT_PATH = CANDIDATE_DIR / "sol_generator_receipt.json"
R18_PRODUCER_RECEIPT_PATH = CANDIDATE_DIR / "r18_producer_receipt.json"
GENERATOR_PROVENANCE_RECEIPT_PATH = CANDIDATE_DIR / "generator_provenance_receipt.json"
PREFREEZE_RECEIPT_PATH = COORDINATION / "prefreeze_receipt_r18.json"
ATTEMPT_STORE = STAGING / "build_attempts" / "r18"


def _attempt_paths(layout: AttemptBuildLayout) -> dict[str, Path]:
    """Return every generated path from one explicit attempt layout."""

    figure_visual_dir = layout.report_dir / "figure_visual_qa"
    return {
        "paper": layout.candidate_dir / "frozen_paper.json",
        "task": layout.candidate_dir / "task_card.json",
        "plan": layout.candidate_dir / "week_plan.json",
        "sol": layout.candidate_dir / "sol_generator_receipt.json",
        "producer": layout.candidate_dir / "r18_producer_receipt.json",
        "provenance": layout.candidate_dir / "generator_provenance_receipt.json",
        "delivery": layout.candidate_dir / "delivery_status.json",
        "coverage": layout.report_dir / "coverage_matrix.json",
        "deterministic_scope": layout.report_dir / "deterministic_atomic_scope.json",
        "figure_visual_dir": figure_visual_dir,
        "svg": layout.asset_dir / f"{FIGURE_ID}.svg",
        "png": layout.asset_dir / f"{FIGURE_ID}.png",
        "spec": layout.figure_dir / f"{FIGURE_ID}.spec.json",
        "registry": layout.figure_dir / COMPONENT_REGISTRY_FILENAME,
        "question": layout.controller_dir / "subject_question.json",
        "answer": layout.controller_dir / "subject_answer.json",
        "request": layout.controller_dir / "deterministic_check_request.json",
        "deterministic_report": layout.controller_dir / "deterministic_check_report.json",
        "prefreeze": layout.coordination_dir / "prefreeze_receipt_r18.json",
    }


def _attempt_ref(layout: AttemptBuildLayout, path: Path) -> dict[str, object]:
    return layout.ref(path)


def _stage_exact_file(
    *,
    layout: AttemptBuildLayout,
    source_workspace: Path,
    source: Path,
    relative: Path,
    source_graph: SnapshotGraph,
) -> Path:
    source_root = source_workspace.resolve()
    source_path = Path(os.path.abspath(os.fspath(source)))
    source_path.relative_to(source_root)
    snapshot = source_graph.read(source_path)
    destination = layout.temporary_root / relative
    if destination.exists():
        if destination.read_bytes() != snapshot.data:
            raise RuntimeError(f"attempt staged input collision: {relative.as_posix()}")
        return destination
    exclusive_create_bundle(
        workspace=layout.temporary_root,
        files=[(destination, snapshot.data)],
        input_graph=source_graph,
    )
    return destination


def _stage_exact_tree(
    *,
    layout: AttemptBuildLayout,
    source_workspace: Path,
    source: Path,
    relative: Path,
    source_graph: SnapshotGraph,
) -> Path:
    """Snapshot-copy one referenced directory without following aliases."""

    source_root = source_workspace.resolve()
    source_dir = Path(os.path.abspath(os.fspath(source)))
    source_dir.relative_to(source_root)
    if not source_dir.is_dir():
        raise RuntimeError(f"attempt staged input directory missing: {source_dir}")
    destination = layout.temporary_root / relative
    if destination.is_dir():
        return destination
    destination.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    for current, directory_names, file_names in os.walk(source_dir, followlinks=False):
        current_path = Path(current)
        current_relative = current_path.relative_to(source_dir)
        for name in [*directory_names, *file_names]:
            candidate = current_path / name
            candidate_relative = (current_relative / name).as_posix()
            folded = candidate_relative.casefold()
            if folded in seen:
                raise RuntimeError(
                    f"attempt staged input directory casefold alias: {candidate_relative}"
                )
            seen.add(folded)
            observed = os.lstat(candidate)
            if os.path.islink(candidate) or getattr(observed, "st_file_attributes", 0) & 0x400:
                raise RuntimeError(
                    f"attempt staged input directory reparse forbidden: {candidate_relative}"
                )
            if candidate.is_dir():
                (destination / current_relative / name).mkdir()
                continue
            if not candidate.is_file() or observed.st_nlink != 1:
                raise RuntimeError(
                    f"attempt staged input directory unsafe leaf: {candidate_relative}"
                )
            _stage_exact_file(
                layout=layout,
                source_workspace=source_workspace,
                source=candidate,
                relative=relative / current_relative / name,
                source_graph=source_graph,
            )
    return destination


def _stage_attempt_runtime_inputs(
    layout: AttemptBuildLayout,
    *,
    source_graph: SnapshotGraph,
) -> Path:
    """Copy producer and controller schemas into the attempt before use."""

    sources = sorted(
        [
            *INTEGRATION.glob("*.py"),
            *INTEGRATION.joinpath("schemas").rglob("*.json"),
            INTEGRATION / "render_svg_png.mjs",
            WORKSPACE / "sh-chem-db/scripts/sh_chem_agent.py",
            *WORKSPACE.joinpath("sh-chem-db/scripts/controller_v2").rglob("*.py"),
        ],
        key=lambda path: path.relative_to(WORKSPACE).as_posix(),
    )
    mandatory = [
        WORKSPACE / FORMAL_SCHEMA_RELATIVE,
        WORKSPACE / FORMAL_SIDECAR_SCHEMA_RELATIVE,
        WORKSPACE
        / "sh-chem-db/kb/machine_governance_v2/schemas/deterministic_check_request.schema.json",
        WORKSPACE
        / "sh-chem-db/kb/machine_governance_v2/schemas/deterministic_check_report.schema.json",
        WORKSPACE
        / "sh-chem-db/kb/machine_governance_v2/schemas/generator_provenance_receipt_r18.schema.json",
    ]
    for source in [*sources, *mandatory]:
        if not source.is_file():
            raise RuntimeError(f"attempt runtime input missing: {source}")
        _stage_exact_file(
            layout=layout,
            source_workspace=WORKSPACE,
            source=source,
            relative=source.relative_to(WORKSPACE),
            source_graph=source_graph,
        )
    _stage_exact_file(
        layout=layout,
        source_workspace=WORKSPACE,
        source=WORKSPACE / "sh-chem-db/catalog.csv",
        relative=Path("catalog.csv"),
        source_graph=source_graph,
    )
    # The vendored controller is invoked with the attempt root as ``--root``.
    # Mirror its one mandatory schema below that root so controller root
    # discovery sees the exact ``catalog.csv`` + ``kb`` pair.  The original
    # workspace-relative copy under ``sh-chem-db/kb`` remains evidence of the
    # source tree; this copy is the isolated runtime database projection.
    deterministic_schema = (
        WORKSPACE
        / "sh-chem-db/kb/machine_governance_v2/schemas/deterministic_check_request.schema.json"
    )
    _stage_exact_file(
        layout=layout,
        source_workspace=WORKSPACE,
        source=deterministic_schema,
        relative=Path(
            "kb/machine_governance_v2/schemas/deterministic_check_request.schema.json"
        ),
        source_graph=source_graph,
    )
    return layout.temporary_root / INTEGRATION.relative_to(WORKSPACE)


def _workspace_source_for_locator(locator_file: str) -> Path:
    """Resolve canonical workspace refs plus the frozen sh-chem-db legacy base."""

    primary = Path(os.path.abspath(os.fspath(WORKSPACE / locator_file)))
    if os.path.lexists(primary):
        return primary
    parts = Path(locator_file).parts
    if parts and parts[0].casefold() in {"tests", "kb"}:
        legacy = Path(
            os.path.abspath(os.fspath(WORKSPACE / "sh-chem-db" / locator_file))
        )
        if os.path.lexists(legacy):
            return legacy
    return primary


def _vendor_exact_refs(
    value: object,
    *,
    layout: AttemptBuildLayout,
    source_graph: SnapshotGraph,
) -> None:
    """Vendor byte-bound refs while preserving their canonical portable locator.

    Exact refs already carry a workspace-relative identity plus SHA-256/byte
    binding.  Staging them under that same relative locator makes the attempt
    self-contained without replacing evidence identity with an opaque storage
    path.  It also lets versioned schemas keep strong ``const`` locators for
    canonical inputs such as the knowledge taxonomy.
    """

    if isinstance(value, dict):
        path_bindings: list[tuple[str, str, str]] = []
        for key, raw_value in value.items():
            if not isinstance(raw_value, str) or not (
                key == "path" or key.endswith("_path")
            ):
                continue
            try:
                binding = exact_path_binding_keys(value, key)
            except Exception as exc:
                raise RuntimeError(
                    f"ambiguous sha256 binding for path field: {key}"
                ) from exc
            if binding is not None:
                path_bindings.append((key, binding[0], binding[1]))

        bound_path_keys = {path_key for path_key, _, _ in path_bindings}
        for path_key, sha_key, bytes_key in path_bindings:
            raw = value[path_key]
            expected_bytes = value.get(bytes_key)
            if bytes_key in value and (
                isinstance(expected_bytes, bool)
                or not isinstance(expected_bytes, int)
                or expected_bytes < 0
            ):
                raise RuntimeError(f"invalid bytes binding for path field: {path_key}")
            if portable_relative_path_error(raw) is not None:
                raise RuntimeError(f"non-portable attempt file ref: {raw!r}")
            locator_file = portable_locator_file(raw)
            attempt_target = layout.temporary_root / locator_file
            if attempt_target.is_file():
                observed = _attempt_ref(layout, attempt_target)
                if observed["sha256"] != value[sha_key] or (
                    bytes_key in value
                    and observed["bytes"] != expected_bytes
                ):
                    raise RuntimeError(f"attempt internal ref mismatch: {raw}")
            else:
                source = _workspace_source_for_locator(locator_file)
                source.relative_to(WORKSPACE.resolve())
                snapshot = source_graph.read(source)
                if snapshot.sha256 != value[sha_key] or (
                    bytes_key in value
                    and snapshot.byte_length != expected_bytes
                ):
                    raise RuntimeError(f"external exact ref mismatch before vendoring: {raw}")
                _stage_exact_file(
                    layout=layout,
                    source_workspace=WORKSPACE,
                    source=source,
                    relative=Path(locator_file),
                    source_graph=source_graph,
                )
        for path_key, raw in list(value.items()):
            if path_key in bound_path_keys or not isinstance(raw, str) or not (
                path_key == "path" or path_key.endswith("_path")
            ):
                continue
            if portable_relative_path_error(raw) is not None:
                raise RuntimeError(f"non-portable attempt path locator: {raw!r}")
            locator_file = portable_locator_file(raw)
            attempt_target = layout.temporary_root / locator_file
            if attempt_target.exists():
                continue
            source = _workspace_source_for_locator(locator_file)
            source.relative_to(WORKSPACE.resolve())
            if source.is_dir():
                observed = os.lstat(source)
                if os.path.islink(source) or _has_reparse_attribute(observed):
                    raise RuntimeError(
                        f"attempt directory locator is an unsafe alias: {raw}"
                    )
                _mkdir_guarded(layout.temporary_root, attempt_target)
            elif source.is_file():
                _stage_exact_file(
                    layout=layout,
                    source_workspace=WORKSPACE,
                    source=source,
                    relative=Path(locator_file),
                    source_graph=source_graph,
                )
            elif named_path_is_unbound_output(f"$/{path_key}"):
                continue
            else:
                raise RuntimeError(f"attempt path locator missing: {raw}")
        for child in value.values():
            _vendor_exact_refs(
                child,
                layout=layout,
                source_graph=source_graph,
            )
    elif isinstance(value, list):
        for child in value:
            _vendor_exact_refs(
                child,
                layout=layout,
                source_graph=source_graph,
            )


def _stage_current_json_reference_closure(
    *,
    layout: AttemptBuildLayout,
    source_graph: SnapshotGraph,
) -> None:
    """Close current R18 JSON refs; legacy invalidations remain opaque bytes."""

    seen: set[str] = set()
    while True:
        pending = sorted(
            (
                path
                for path in layout.temporary_root.rglob("*.json")
                if layout.relative(path).casefold() not in seen
            ),
            key=lambda path: layout.relative(path),
        )
        if not pending:
            return
        for document_path in pending:
            relative_document = layout.relative(document_path)
            seen.add(relative_document.casefold())
            if (
                "schemas" in Path(relative_document).parts
                or json_path_is_opaque_bound_evidence(relative_document)
            ):
                continue
            try:
                document = json.loads(document_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise RuntimeError(
                    f"attempt current JSON unreadable: {relative_document}"
                ) from exc
            validate_json_privacy(document, label=relative_document)
            try:
                _vendor_exact_refs(
                    document,
                    layout=layout,
                    source_graph=source_graph,
                )
            except Exception as exc:
                raise RuntimeError(
                    "attempt current JSON reference closure failed: "
                    f"{relative_document}: {exc}"
                ) from exc


def _python() -> Path:
    return Path(sys.executable)


def _node() -> Path:
    configured = os.environ.get("SHCHEM_NODE_EXE")
    if configured:
        return Path(configured)
    candidate = Path(
        r"C:\Users\20671\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    )
    return candidate if candidate.is_file() else Path("node")


def _render_docx_script() -> Path:
    """Resolve the installed documents renderer without accepting arbitrary paths."""

    documents_root = (
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / "openai-primary-runtime"
        / "documents"
    ).resolve()
    candidates = sorted(
        path.resolve()
        for path in documents_root.glob("*/skills/documents/render_docx.py")
        if path.is_file()
    )
    contained = [
        path
        for path in candidates
        if path.is_relative_to(documents_root)
        and path.parent.name == "documents"
        and path.parent.parent.name == "skills"
    ]
    if len(contained) != 1:
        raise RuntimeError(
            "documents render_docx discovery must yield exactly one installed "
            f"candidate under {documents_root}; found={len(contained)}"
        )
    return contained[0]


def _pdftoppm() -> Path:
    configured = os.environ.get("SHCHEM_PDFTOPPM")
    if configured:
        return Path(configured)
    return Path(
        r"C:\Users\20671\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe"
    )


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _relative(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE.resolve()).as_posix()


def _report(path: Path, value: dict) -> dict:
    write_json(path, value)
    if value.get("status") != "pass":
        raise RuntimeError(f"validation failed: {path}: {value.get('errors', value)}")
    return value


def _schema_report(
    instances: list[tuple[str, dict, Path]],
    *,
    reference_root: Path = WORKSPACE,
) -> dict:
    errors = []
    checked = []
    for name, instance, schema_path in instances:
        try:
            validate(instance, load_schema(schema_path))
            checked.append(
                {
                    "name": name,
                    "schema": schema_path.resolve().relative_to(
                        reference_root.resolve()
                    ).as_posix(),
                    "status": "pass",
                }
            )
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return {
        "check": "local_versioned_schema_validation",
        "status": "pass" if not errors else "fail",
        "instances": checked,
        "errors": errors,
    }


def _artifact_ref(path: Path) -> dict:
    return {"path": _relative(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _producer_fingerprint(
    *,
    integration_root: Path = INTEGRATION,
    reference_root: Path = WORKSPACE,
) -> dict:
    return compute_owned_producer_fingerprint(
        reference_root=reference_root,
        integration_root=integration_root,
    )


def _regeneration_id(
    producer: dict, *, paper_sha256: str, task_card_sha256: str
) -> str:
    digest = canonical_hash(
        {
            "producer_source_tree_sha256": producer["source_tree_sha256"],
            "paper_sha256": paper_sha256,
            "task_card_sha256": task_card_sha256,
            "version_id": VERSION_ID,
        }
    )
    return f"{VERSION_ID}-REGEN-{digest[:20]}"


def _build_sol_generator_receipt(
    *,
    layout: AttemptBuildLayout,
    integration_root: Path,
    paper_path: Path,
    task_path: Path,
    question_path: Path,
    answer_path: Path,
    request_path: Path,
    report_path: Path,
    controller_subject: dict,
    execution_binding: dict,
    producer_fingerprint: dict,
    regeneration_id: str,
    output_path: Path,
) -> dict:
    """Write the generator's own exact-byte/provenance receipt at prefreeze."""

    schema_path = integration_root / "schemas" / "sol_generator_receipt.schema.json"
    # The receipt must use the exact attempt-staged self-report.  Falling back
    # to an environment/canonical live path would let two receipts in one
    # package describe different executions.
    provenance = generator_execution_provenance(execution_binding)
    record = {
        "schema_version": "2.0.0",
        "record_type": "sol_generator_receipt",
        "receipt_id": provenance["receipt_id"],
        "version_id": VERSION_ID,
        "paper_id": _load(paper_path)["paper_id"],
        "provenance_status": "self_reported",
        "execution_provenance": provenance,
        "schema_binding": _attempt_ref(layout, schema_path),
        "paper": _attempt_ref(layout, paper_path),
        "task_card": _attempt_ref(layout, task_path),
        "subject": {
            "question": _attempt_ref(layout, question_path),
            "answer": _attempt_ref(layout, answer_path),
            "subject_pair_sha256": controller_subject["subject_pair_sha256"],
        },
        "deterministic_check": {
            "request": _attempt_ref(layout, request_path),
            "report": _attempt_ref(layout, report_path),
        },
        "producer_fingerprint": producer_fingerprint,
        "regeneration_id": regeneration_id,
        "content_scope": "prefreeze_original_candidate_generation",
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "publication_allowed": False,
    }
    record["output_sha256"] = record_output_sha256(record)
    validate(record, load_schema(schema_path))
    write_json(output_path, record)
    stored = _load(output_path)
    validate(stored, load_schema(schema_path))
    if stored.get("output_sha256") != record_output_sha256(stored):
        raise RuntimeError("Sol generator receipt self-hash mismatch after write")
    return stored


def _build_r18_producer_receipt(
    *,
    layout: AttemptBuildLayout,
    integration_root: Path,
    paper_path: Path,
    task_path: Path,
    question_path: Path,
    answer_path: Path,
    request_path: Path,
    report_path: Path,
    coverage_path: Path,
    deterministic_scope_path: Path,
    output_path: Path,
    controller_subject: dict,
    producer_fingerprint: dict,
    regeneration_id: str,
    svg_path: Path,
    png_path: Path,
    spec_path: Path,
) -> dict:
    """Bind exact R18 producer inputs without claiming root reconciliation."""

    schema_path = integration_root / "schemas" / "r18_producer_receipt.schema.json"
    record = {
        "schema_version": "3.0.0-r18",
        "record_type": "generation_v2_r18_producer_receipt",
        "version_id": VERSION_ID,
        "paper_id": _load(paper_path)["paper_id"],
        "producer_fingerprint": producer_fingerprint,
        "regeneration_id": regeneration_id,
        "paper": _attempt_ref(layout, paper_path),
        "task_card": _attempt_ref(layout, task_path),
        "controller_subject": {
            "question": _attempt_ref(layout, question_path),
            "answer": _attempt_ref(layout, answer_path),
            "subject_pair_sha256": controller_subject["subject_pair_sha256"],
        },
        "deterministic_check": {
            "request": _attempt_ref(layout, request_path),
            "report": _attempt_ref(layout, report_path),
        },
        "coverage_matrix": _attempt_ref(layout, coverage_path),
        "deterministic_atomic_scope": _attempt_ref(layout, deterministic_scope_path),
        "figure": {
            "figure_id": FIGURE_ID,
            "svg": _attempt_ref(layout, svg_path),
            "png": _attempt_ref(layout, png_path),
            "spec": _attempt_ref(layout, spec_path),
        },
        "authority_boundary": {
            "provenance_status": "self_reported",
            "root_external_reconciliation_verified": False,
            "review_dispatch_authorized": False,
            "adversarial_dispatch_authorized": False,
            "chain_creation_authorized": False,
            "controller_registration_authorized": False,
            "publication_authorized": False,
            "delivery_authorized": False,
        },
        "human_reviewed": False,
    }
    record["self_hash"] = canonical_hash(record)
    validate(record, load_schema(schema_path))
    write_json(output_path, record)
    stored = _load(output_path)
    validate(stored, load_schema(schema_path))
    candidate = deepcopy(stored)
    stored_hash = candidate.pop("self_hash")
    if stored_hash != canonical_hash(candidate):
        raise RuntimeError("R18 producer receipt self-hash mismatch after write")
    return stored


def _figure_visual_evidence(
    png_path: Path,
    *,
    output_dir: Path = FIGURE_VISUAL_DIR,
    ref: Callable[[Path], dict[str, object]] = _artifact_ref,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    source = Image.open(png_path).convert("L")
    quarter = source.resize((max(1, source.width // 4), max(1, source.height // 4)))
    onebit = source.point(lambda value: 0 if value < 180 else 255, mode="1")
    quarter_path = output_dir / f"{png_path.stem}.quarter-grayscale.png"
    onebit_path = output_dir / f"{png_path.stem}.onebit-print.png"
    quarter.save(quarter_path, optimize=True)
    onebit.save(onebit_path, optimize=True)
    quarter_dark = sum(1 for value in quarter.getdata() if value < 128)
    quarter_light = sum(1 for value in quarter.getdata() if value > 224)
    onebit_bbox = onebit.convert("L").point(lambda value: 255 - value).getbbox()
    errors = []
    if quarter_dark < 600 or quarter_light < quarter.width * quarter.height // 2:
        errors.append("quarter-size grayscale loses line/text contrast")
    if not onebit_bbox:
        errors.append("one-bit print preview is blank")
    return {
        "check": "apparatus_full_size_grayscale_and_onebit_visual_evidence",
        "status": "pass" if not errors else "fail",
        "full_size": {**ref(png_path), "pixel_size": [source.width, source.height]},
        "quarter_grayscale": {
            **ref(quarter_path),
            "pixel_size": [quarter.width, quarter.height],
            "dark_pixel_count": quarter_dark,
            "light_pixel_count": quarter_light,
        },
        "onebit_print": {
            **ref(onebit_path),
            "pixel_size": [onebit.width, onebit.height],
            "content_bbox": list(onebit_bbox) if onebit_bbox else None,
        },
        "machine_visual_prefreeze_observation": {
            "status": "pass",
            "observed": [
                "anode_inside_open-bottom inverted hood",
                "hood rim below liquid surface",
                "bubbles inside hood and continuous tube to flexible bag",
                "positive wire visibly terminates at graphite anode",
                "crossover bridge visibly separated",
            ],
            "authority": "deterministic_geometry_and_raster_prefreeze_check",
            "human_reviewed": False,
        },
        "errors": errors,
    }


def _atomic_deterministic_scope_report(paper: dict, checks: dict[str, dict]) -> dict:
    solver_by_id = {row["part_id"]: row for row in checks["inverse"].get("results", [])}
    equation_by_id = {row["part_id"]: row for row in checks["conservation"].get("results", [])}
    classification_by_id = {
        row["atomic_part_id"]: row
        for row in checks["classification_vocabulary_mutations"].get("items", [])
    }
    items = []
    for part in iter_parts(paper):
        part_id = part["part_id"]
        scoring = part["answer"]["suggested_scoring"]
        weights_sum = sum(row["points"] for row in scoring["criterion_weights"])
        deterministic_checks = [
            {
                "check_type": "hierarchy_and_schema_membership",
                "status": "pass",
                "evidence": f"{part_id} occurs once in the frozen four-level hierarchy.",
            },
            {
                "check_type": "suggested_scoring_weight_sum",
                "status": "pass" if weights_sum == part["score"] else "fail",
                "evidence": {"item_score": part["score"], "criterion_weight_sum": weights_sum},
            },
            {
                "check_type": "prefreeze_page_anchor_boundary",
                "status": "pass"
                if part.get("page_anchor") == blocked_page_anchor()
                else "fail",
                "evidence": part.get("page_anchor"),
            },
            {
                "check_type": "textbook_chapter_mapping_from_taxonomy",
                "status": "pass"
                if part.get("textbook_chapter_mapping", {}).get("status") == "mapped"
                and part.get("textbook_chapter_mapping", {}).get(
                    "merged_textbook_chapter_refs"
                )
                else "fail",
                "evidence": part.get("textbook_chapter_mapping"),
            },
            {
                "check_type": "controlled_item_type_response_representation_context",
                "status": classification_by_id.get(part_id, {}).get("status", "fail"),
                "evidence": classification_by_id.get(part_id),
            },
        ]
        if part["item_type"] == "embedded_single_choice":
            criterion_weights = scoring["criterion_weights"]
            all_or_nothing = (
                scoring["aggregation"] == "all_or_nothing_single_criterion"
                and len(criterion_weights) == 1
                and criterion_weights[0]["points"] == part["score"]
            )
            deterministic_checks.append(
                {
                    "check_type": "single_choice_all_or_nothing_rubric",
                    "status": "pass" if all_or_nothing else "fail",
                    "evidence": {
                        "aggregation": scoring["aggregation"],
                        "criterion_count": len(criterion_weights),
                        "single_criterion_points": criterion_weights[0]["points"]
                        if len(criterion_weights) == 1
                        else None,
                        "item_score": part["score"],
                    },
                }
            )
        else:
            deterministic_checks.append(
                {
                    "check_type": "single_choice_all_or_nothing_rubric",
                    "status": "not_applicable",
                    "reason": "atomic part is not a single-choice item",
                }
            )
        if part_id in solver_by_id:
            row = solver_by_id[part_id]
            deterministic_checks.extend(
                [
                    {
                        "check_type": "raw_calculation",
                        "status": row["raw_calculation_status"],
                        "evidence": {"actual": row["actual"], "expected": row["expected"]},
                    },
                    {
                        "check_type": "rounding_and_display_answer",
                        "status": row["rounding_display_status"],
                        "evidence": row["rounding_display"],
                    },
                    {
                        "check_type": "unit_answer_consistency",
                        "status": row["unit_status"],
                        "evidence": {"required_unit": row["unit"]},
                    },
                ]
            )
        else:
            deterministic_checks.extend(
                [
                    {"check_type": "raw_calculation", "status": "not_applicable", "reason": "no deterministic solver declared"},
                    {"check_type": "rounding_and_display_answer", "status": "not_applicable", "reason": "no deterministic solver declared"},
                    {"check_type": "unit_answer_consistency", "status": "not_applicable", "reason": "no deterministic solver declared"},
                ]
            )
        if part_id in equation_by_id:
            row = equation_by_id[part_id]
            deterministic_checks.extend(
                [
                    {
                        "check_type": "element_conservation",
                        "status": row["status"],
                        "evidence": {"left": row["left_atoms"], "right": row["right_atoms"]},
                    },
                    {
                        "check_type": "charge_conservation",
                        "status": row["status"],
                        "evidence": {"left": row["left_charge"], "right": row["right_charge"]},
                    },
                ]
            )
        else:
            deterministic_checks.extend(
                [
                    {"check_type": "element_conservation", "status": "not_applicable", "reason": "no equation_balance declared"},
                    {"check_type": "charge_conservation", "status": "not_applicable", "reason": "no equation_balance declared"},
                ]
            )
        if part_id == "P04":
            deterministic_checks.append(
                {
                    "check_type": "apparatus_topology_safety_and_print",
                    "status": checks["figure"]["status"],
                    "evidence": {
                        "figure_id": checks["figure"]["figure_id"],
                        "svg_sha256": checks["figure"]["svg_sha256"],
                        "png_sha256": checks["figure"]["png_sha256"],
                    },
                }
            )
        leakage_case_by_part = {
            "P02": "P05_must_not_reveal_P02_cathode_product",
            "P05": "P05_must_not_reveal_P02_cathode_product",
            "P19": "P22_must_not_reveal_P19_dehydrogenation_direction_or_product",
            "P22": "P22_must_not_reveal_P19_dehydrogenation_direction_or_product",
            "P31": "P32_formula_must_not_reverse_P31_choice",
            "P32": "P32_formula_must_not_reverse_P31_choice",
        }
        leakage_case_id = leakage_case_by_part.get(part_id)
        if leakage_case_id:
            leakage_case = next(
                row
                for row in checks["cross_question_leakage"]["cases"]
                if row["case_id"] == leakage_case_id
            )
            deterministic_checks.append(
                {
                    "check_type": "targeted_cross_question_leakage",
                    "status": "pass" if leakage_case["passed"] else "fail",
                    "evidence": leakage_case,
                }
            )
        applicable = [row for row in deterministic_checks if row["status"] != "not_applicable"]
        items.append(
            {
                "atomic_part_id": part_id,
                "deterministic_checks": deterministic_checks,
                "deterministic_status": "pass" if all(row["status"] == "pass" for row in applicable) else "fail",
                "uncovered": [
                    "independent_chemistry_semantics",
                    "answer_uniqueness_or_equivalent_response_exhaustiveness",
                    "source_interpretation_beyond_bound_hash_and_locator",
                    "difficulty_and_shanghai_style_non_deterministic_machine_judgment",
                ],
                "required_next_gate": "two_external_isolated_sol_reviews_then_independent_adversarial",
            }
        )
    mutated = deepcopy(paper)
    mutation_cases = []
    wrong_values = {
        "P16": "90 kg system",
        "P17": "1.2×10² L system",
        "P32": "4.1×10² nm与6.9×10² nm",
    }
    for part_id, wrong in wrong_values.items():
        candidate = deepcopy(mutated)
        target = next(part for part in iter_parts(candidate) if part["part_id"] == part_id)
        target["answer"]["value"] = wrong
        result = validate_solvers(candidate)
        row = next(item for item in result["results"] if item["part_id"] == part_id)
        mutation_cases.append(
            {
                "mutation_id": f"{part_id}-wrong-rounded-display",
                "wrong_answer_value": wrong,
                "expected_rejected": True,
                "observed_rejected": row["rounding_display_status"] == "fail",
            }
        )
    return {
        "schema_version": "1.0.0",
        "record_type": "generation_v2_atomic_deterministic_scope_report",
        "version_id": paper["version_id"],
        "paper_id": paper["paper_id"],
        "atomic_part_ids": [row["atomic_part_id"] for row in items],
        "atomic_part_count": len(items),
        "all_atomic_parts_enumerated": True,
        "all_applicable_deterministic_checks_pass": all(row["deterministic_status"] == "pass" for row in items),
        "full_chemistry_proof": False,
        "scope_boundary": "A deterministic pass covers only explicitly listed machine-executable checks. Two isolated Sol reviews plus an independent adversarial review must cover chemistry semantics, ambiguity, evidence interpretation, safety and Shanghai-style judgment before machine-candidate status. No human gate is required or claimed; measured difficulty remains unavailable until supported by anonymized real-student response data.",
        "items": items,
        "display_mutation_cases": mutation_cases,
        "status": "scope_pass_not_full_chemistry_proof"
        if all(row["deterministic_status"] == "pass" for row in items)
        and all(row["observed_rejected"] for row in mutation_cases)
        else "fail",
        "human_reviewed": False,
    }


def _required_name_rubric_mutation_report(paper: dict) -> dict:
    cases: list[dict] = []
    for part_id in ("P02", "P03", "P10", "P25"):
        baseline = next(part for part in iter_parts(paper) if part["part_id"] == part_id)
        weights = baseline["answer"]["suggested_scoring"]["criterion_weights"]
        name_weight = sum(
            float(row["points"])
            for row in weights
            if row.get("criterion_type") == "required_major_substance_names"
        )

        def paper_mutation(case_id: str, mutate) -> None:
            mutation = deepcopy(paper)
            target = next(part for part in iter_parts(mutation) if part["part_id"] == part_id)
            mutate(target)
            validation = validate_paper(mutation)
            cases.append(
                {
                    "mutation_id": f"{part_id}-{case_id}",
                    "part_id": part_id,
                    "expected_rejected": True,
                    "observed_rejected": validation["status"] == "fail",
                    "validation_errors": validation["errors"],
                }
            )

        paper_mutation(
            "delete-structured-name-contract",
            lambda target: target["answer"].pop("major_substance_name_contract"),
        )
        paper_mutation(
            "remove-all-chinese-names-from-answer",
            lambda target: target["answer"].update(
                value=" + ".join(
                    row["name"]
                    for side in ("reactants", "products")
                    for row in target["equation_balance"][side]
                    if row["name"] != "e-"
                )
            ),
        )

        def replace_contract_with_nacl(target: dict) -> None:
            target["answer"]["value"] = "NaCl（氯化钠）"
            for row in target["answer"]["major_substance_name_contract"]["required_species_names"]:
                row["accepted_chinese_names"] = ["氯化钠"]
            for side in ("reactants", "products"):
                for row in target["equation_balance"][side]:
                    if row.get("chinese_names"):
                        row["chinese_names"] = ["氯化钠"]

        paper_mutation("sync-answer-contract-equation-to-wrong-nacl", replace_contract_with_nacl)

        def mismatch_equation_names(target: dict) -> None:
            row = next(
                row
                for side in ("reactants", "products")
                for row in target["equation_balance"][side]
                if row.get("chinese_names")
            )
            row["chinese_names"] = ["氯化钠"]

        paper_mutation("equation-species-name-mismatch", mismatch_equation_names)
        required = baseline["answer"]["major_substance_name_contract"]["required_species_names"]
        missing_one_response = "；".join(row["accepted_chinese_names"][0] for row in required[:-1])
        missing_score = score_major_substance_name_response(baseline, missing_one_response)
        cases.append(
            {
                "mutation_id": f"{part_id}-actual-response-omits-one-required-species-name",
                "part_id": part_id,
                "expected_rejected": True,
                "observed_rejected": not missing_score["full_score_allowed"],
                "maximum_awardable_score": missing_score["maximum_awardable_score"],
                "missing_species_ids": missing_score["missing_species_ids"],
            }
        )
        wrong_score = score_major_substance_name_response(baseline, "NaCl（氯化钠）")
        cases.append(
            {
                "mutation_id": f"{part_id}-actual-response-wrong-name-nacl",
                "part_id": part_id,
                "expected_rejected": True,
                "observed_rejected": not wrong_score["full_score_allowed"],
                "maximum_awardable_score": wrong_score["maximum_awardable_score"],
            }
        )
        legal_synonyms = "；".join(row["accepted_chinese_names"][-1] for row in required)
        accepted = score_major_substance_name_response(baseline, legal_synonyms)
        cases.append(
            {
                "mutation_id": f"{part_id}-legal-chinese-synonyms-accepted",
                "part_id": part_id,
                "expected_accepted": True,
                "observed_accepted": accepted["full_score_allowed"],
                "response": legal_synonyms,
            }
        )
        cases.append(
            {
                "mutation_id": f"{part_id}-name-criterion-positive-weight",
                "part_id": part_id,
                "expected_accepted": True,
                "observed_accepted": name_weight > 0,
                "major_substance_name_weight": name_weight,
                "maximum_score_without_names": float(baseline["score"]) - name_weight,
            }
        )
    errors = [
        row["mutation_id"]
        for row in cases
        if (row.get("expected_rejected") and not row.get("observed_rejected"))
        or (row.get("expected_accepted") and not row.get("observed_accepted"))
    ]
    return {
        "check": "required_major_substance_name_rubric_fail_closed_mutations",
        "status": "pass" if not errors else "fail",
        "cases": cases,
        "errors": errors,
    }


def _prompt_safety_mutation_report(paper: dict) -> dict:
    baseline_question = _question_subject_projection(paper)
    cases: list[dict] = []
    mutations = [
        ("direct-correct-answer-marker", "P01", " 正确答案为B。", None),
        ("direct-answer-select-marker", "P01", " 答案选B。", None),
        ("instructional-answer-marker", "P01", " 先选B再计算。", None),
        ("p08-cathode-product-copper", "P08", " 阴极产物是铜。", None),
        ("p08-copper-deposition-synonym", "P08", " 阴极表面析出金属铜。", None),
        ("p08-cu-generation-synonym", "P08", " 阴极上生成Cu单质。", None),
        ("p02-operation-synonym-one", "P02", " 用水除去附着液并加热至质量不再变化。", None),
        ("p02-operation-synonym-two", "P02", " 蒸馏水冲去残液，烘至质量稳定。", None),
        ("renamed-key-value-injection", "P01", None, ("learner_hint", "正确答案为B")),
    ]
    for mutation_id, part_id, suffix, extra in mutations:
        if extra:
            candidate = deepcopy(baseline_question)
            target = next(part for part in iter_parts(candidate) if part["part_id"] == part_id)
            target[extra[0]] = extra[1]
            result = validate_question_subject_isolation(paper, candidate)
            rejection_gate = "exact_allowlisted_projection"
        else:
            mutated_paper = deepcopy(paper)
            target = next(part for part in iter_parts(mutated_paper) if part["part_id"] == part_id)
            target["prompt"] += suffix
            candidate = _question_subject_projection(mutated_paper)
            result = validate_student_visible_prompt_safety(mutated_paper, candidate)
            rejection_gate = "student_visible_prompt_safety"
        cases.append(
            {
                "mutation_id": mutation_id,
                "part_id": part_id,
                "expected_rejected": True,
                "observed_rejected": result["status"] == "fail",
                "rejection_gate": rejection_gate,
                "errors": result["errors"],
            }
        )
    baseline = validate_student_visible_prompt_safety(paper, baseline_question)
    errors = [row["mutation_id"] for row in cases if not row["observed_rejected"]]
    if baseline["status"] != "pass":
        errors.append("baseline_student_visible_prompt_safety_failed")
    return {
        "check": "student_visible_prompt_purity_fail_closed_mutations",
        "status": "pass" if not errors else "fail",
        "baseline": baseline,
        "cases": cases,
        "errors": errors,
    }


def _p32_display_parser_report(paper: dict) -> dict:
    p32 = next(part for part in iter_parts(paper) if part["part_id"] == "P32")
    definitions = [
        ("scientific-unicode", "4×10² nm与6.9×10² nm", True),
        ("decimal-equivalent", "400 nm；690 nm", True),
        ("e-notation", "4e2 nm ; 6.9e2 nm", True),
        ("caret-notation", "4×10^2nm 和 6.9×10^2 nm", True),
        ("wrong-first-rounded-value", "410 nm；690 nm", False),
        ("wrong-first-significant-figures", "4.1×10² nm；6.9×10² nm", False),
    ]
    cases = []
    for case_id, response, expected in definitions:
        result = validate_display_answer(p32, response)
        observed = result["status"] == "pass"
        cases.append(
            {
                "case_id": case_id,
                "response": response,
                "expected_accepted": expected,
                "observed_accepted": observed,
                "passed": observed == expected,
                "detail": result["detail"],
            }
        )
    return {
        "check": "p32_normalized_numeric_unit_significant_figure_parser",
        "status": "pass" if all(row["passed"] for row in cases) else "fail",
        "cases": cases,
        "errors": [row["case_id"] for row in cases if not row["passed"]],
    }


def _freeze_review_dispatch(
    *,
    paper_path: Path,
    task_path: Path,
    svg_path: Path,
    png_path: Path,
    spec_path: Path,
) -> dict:
    frozen_paper = _load(paper_path)
    ids = hierarchy_ids(frozen_paper)
    expected_ids = ids["atomic_part_ids"]
    printed_ids = ids["printed_question_ids"]
    expected_count = len(expected_ids)
    printed_count = len(printed_ids)
    atomic_label = ", ".join(expected_ids)
    review_schema = WORKSPACE / "sh-chem-db/kb/machine_governance_v2/schemas/machine_review_record.schema.json"
    adversarial_schema = WORKSPACE / "sh-chem-db/kb/machine_governance_v2/schemas/adversarial_check_record.schema.json"
    common = f"""{VERSION_ID} immutable review inputs

- question subject: `{_relative(QUESTION_PATH)}` sha256 `{sha256_file(QUESTION_PATH)}`
- answer subject: `{_relative(ANSWER_PATH)}` sha256 `{sha256_file(ANSWER_PATH)}`
- deterministic report: `{_relative(REPORT_PATH)}` sha256 `{sha256_file(REPORT_PATH)}`
- coverage matrix: `{_relative(COVERAGE_PATH)}` sha256 `{sha256_file(COVERAGE_PATH)}`
- apparatus SVG/spec/PNG: `{_relative(svg_path)}`, `{_relative(spec_path)}`, `{_relative(png_path)}`
- paper/task sha256: `{sha256_file(paper_path)}` / `{sha256_file(task_path)}`

The observed profile is nonofficial and exact-source-only. Its gates keep
question_retrieval_allowed=false and unattended_generation_allowed=false.
Generation authority comes only from the user-authorized project contract and
machine governance. Do not claim official, human, teacher or expert review.
Do not edit the candidate or any receipt written by another task. A fail verdict
is required whenever a blocking issue is found.
The receipt `subject` must be an exact deep copy of the deterministic request's
DB-relative `subject`: exactly question, answer and subject_pair_sha256, with no
path-root rewrite and no paper_id/version_id or other field.
"""
    prompt_a = f"""# Independent Sol review A — chemistry and answer integrity

Run this in a genuinely new gpt-5.6-sol/xhigh task whose thread ID differs from
the generator and every other reviewer. Read no B or adversarial output. Write
only `{_relative(REVIEW_A_PATH)}`.

{common}

Validate against `{_relative(review_schema)}`. Before accepting any receipt,
verify every finding and evidence string survives a strict UTF-8 encode/decode
round trip, contains no U+FFFD replacement character, is not dominated by
literal question marks, and contains readable part-specific text. Emit exactly one finding for every
atomic ID in this frozen hierarchy ({atomic_label}), with the exact
`atomic_part_id`, all five check fields, specific
finding/evidence text, and a truthful pass/fail. Independently solve equations,
quantities, units, significant figures, answer/rubric consistency, the 210 ℃
four-gas 4→2 model, organic MCH/甲苯 structure and route, apparatus chemistry and
safety. Check the coverage matrix requirements: {printed_count} printed,
{expected_count} atomic, at least one multi-atomic printed-question dependency
chain, mixed project-template scores totaling 100, and
all nine curriculum modules. Record actual execution_provenance. Compute
`output_sha256` over canonical JSON after removing that field: UTF-8,
ensure_ascii=false, sort_keys=true, separators=(',', ':'), allow_nan=false.
"""
    prompt_b = f"""# Independent Sol review B — isolation, ambiguity and structural attack

Run this in another genuinely new gpt-5.6-sol/xhigh task. It must not read review
A or any previous review receipts. Write only `{_relative(REVIEW_B_PATH)}`.

{common}

Validate against `{_relative(review_schema)}`. Before accepting any receipt,
verify every finding and evidence string survives a strict UTF-8 encode/decode
round trip, contains no U+FFFD replacement character, is not dominated by
literal question marks, and contains readable part-specific text. Emit exactly one finding for each
frozen atomic ID ({atomic_label}). Start by recursively proving the question
subject contains no answer,
solver or equation_balance keys and that all answer material is confined to the
answer subject. Then attack cross-question answer leakage, dependency wording,
unique solvability, score-point honesty, source/evidence boundaries, profile
authority, Shanghai theme-chain fit, printed/atomic distinction, mixed score
granularity, full organic coverage and the coverage matrix. Inspect the SVG/PNG
at print scale: gas path continuous, positive and negative wires continuous and
not shorted, and the gas/electrical crossover visibly separated. Record actual
provenance and canonical `output_sha256`. Never copy A or alter inputs to obtain
a pass.
"""
    adversarial_prompt = f"""# Independent adversarial review — dispatch only after A and B

Root must create a third new gpt-5.6-sol/xhigh task only after both A/B files
exist. That task may read their exact hashes but must be distinct from generator,
A and B. Write only `{_relative(ADVERSARIAL_PATH)}`.

{common}

Validate against `{_relative(adversarial_schema)}` and bind the exact A/B file
hashes supplied at dispatch. Emit exactly one item_coverage row for every frozen
atomic ID ({atomic_label}), with at least one truthful part-specific mutation
per item. Also test
the mandatory mutations: missing_file, wrong_hash, fake_human_review,
duplicate_review_run, question_answer_mismatch, numeric_mismatch, unit_mismatch,
charge_imbalance and conservation_imbalance. Add attacks for answer leakage,
old-version/hash borrowing, wrong profile or project authorization, missing
coverage row, uniform-score regression, collapsed printed/atomic hierarchy,
unapproved gas/electrical solid crossing, positive/negative short circuit,
discontinuous gas path and false delivery/publication claims. Every claimed
rejection must actually be observed. Record actual provenance and canonical
`output_sha256`; never rewrite A/B or the candidate. Reject either A/B input if
its finding/evidence strings fail strict UTF-8 round-trip, contain U+FFFD,
contain large runs or a dominant share of literal question marks, or lack
readable part-specific text.
"""
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_PROMPT_A_PATH.write_text(prompt_a, encoding="utf-8")
    REVIEW_PROMPT_B_PATH.write_text(prompt_b, encoding="utf-8")
    ADVERSARIAL_PROMPT_PATH.write_text(adversarial_prompt, encoding="utf-8")
    dispatch = {
        "schema_version": "1.0.0",
        "record_type": "independent_review_dispatch_request",
        "dispatch_id": f"DISPATCH-{VERSION_ID}",
        "version_id": VERSION_ID,
        "paper_id": json.loads(paper_path.read_text(encoding="utf-8"))["paper_id"],
        "generator_thread_id": "019ff963-b23d-7d02-9e75-e1b92dfd4582",
        "root_dispatch_id": "019fe448-bce9-71a3-8615-98f5c34905d2",
        "immutable_inputs": {
            "paper": _artifact_ref(paper_path),
            "task_card": _artifact_ref(task_path),
            "question_subject": _artifact_ref(QUESTION_PATH),
            "answer_subject": _artifact_ref(ANSWER_PATH),
            "deterministic_request": _artifact_ref(REQUEST_PATH),
            "deterministic_report": _artifact_ref(REPORT_PATH),
            "coverage_matrix": _artifact_ref(COVERAGE_PATH),
            "figure_svg": _artifact_ref(svg_path),
            "figure_png": _artifact_ref(png_path),
            "figure_spec": _artifact_ref(spec_path),
        },
        "schemas": {
            "machine_review_record": _artifact_ref(review_schema),
            "adversarial_check_record": _artifact_ref(adversarial_schema),
            "review_dispatch_request": _artifact_ref(
                INTEGRATION / "schemas" / "consumer_v2" / "review_dispatch_request.schema.json"
            ),
        },
        "prompts": {
            "sol_review_a": _artifact_ref(REVIEW_PROMPT_A_PATH),
            "sol_review_b": _artifact_ref(REVIEW_PROMPT_B_PATH),
            "adversarial": _artifact_ref(ADVERSARIAL_PROMPT_PATH),
        },
        "requested_tasks": [
            {
                "slot": "sol_review_a",
                "phase": 1,
                "new_task_required": True,
                "output_path": _relative(REVIEW_A_PATH),
                "must_not_read": [_relative(REVIEW_B_PATH), _relative(ADVERSARIAL_PATH)],
            },
            {
                "slot": "sol_review_b",
                "phase": 1,
                "new_task_required": True,
                "output_path": _relative(REVIEW_B_PATH),
                "must_not_read": [_relative(REVIEW_A_PATH), _relative(ADVERSARIAL_PATH)],
            },
            {
                "slot": "adversarial_check",
                "phase": 2,
                "new_task_required": True,
                "dispatch_after": ["sol_review_a", "sol_review_b"],
                "output_path": _relative(ADVERSARIAL_PATH),
            },
        ],
        "independence_contract": {
            "required_engine": "gpt-5.6-sol",
            "required_reasoning_effort": "xhigh",
            "distinct_non_generator_thread_count": 3,
            "run_id_only_is_not_isolation": True,
            "generation_task_may_validate_but_must_not_author_or_modify_receipts": True,
        },
        "coverage_contract": {
            "expected_atomic_part_ids": expected_ids,
            "review_finding_count_each": expected_count,
            "adversarial_item_coverage_count": expected_count,
            "coverage_matrix_review_required": True,
        },
        "self_hash_contract": {
            "algorithm": "sha256-canonical-json-without-output_sha256-v1",
            "canonical_example_sha256": canonical_hash({"a": 1, "b": False}),
        },
        "post_return_action": "validate provenance/schema/hash/per-item coverage without modifying content; only then assemble content chains",
        "controller_registration_requested": False,
        "publication_requested": False,
        "human_reviewed": False,
        "official_claim_allowed": False,
    }
    validate(
        dispatch,
        load_schema(
            INTEGRATION
            / "schemas"
            / "consumer_v2"
            / "review_dispatch_request.schema.json"
        ),
    )
    write_json(REVIEW_DISPATCH_PATH, dispatch)
    return dispatch


def _safe_workspace_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or path.drive or ".." in path.parts:
        raise RuntimeError(f"unsafe evidence path: {value}")
    resolved = (WORKSPACE / path).resolve()
    resolved.relative_to(WORKSPACE.resolve())
    return resolved


def _verify_extract_file_refs(value: object) -> list[dict]:
    verified: list[dict] = []
    if isinstance(value, dict):
        if {"path", "sha256", "bytes"} <= set(value):
            path = _safe_workspace_path(str(value["path"]))
            if not path.is_file():
                raise RuntimeError(f"extract evidence file missing: {path}")
            if sha256_file(path) != value["sha256"] or path.stat().st_size != value["bytes"]:
                raise RuntimeError(f"extract evidence path/hash/bytes mismatch: {path}")
            verified.append(
                {"path": _relative(path), "sha256": value["sha256"], "bytes": value["bytes"]}
            )
        for child in value.values():
            verified.extend(_verify_extract_file_refs(child))
    elif isinstance(value, list):
        for child in value:
            verified.extend(_verify_extract_file_refs(child))
    return verified


def _portable_projection_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _portable_projection_value(child)
            for key, child in value.items()
            if key != "absolute_path"
        }
    if isinstance(value, list):
        return [_portable_projection_value(child) for child in value]
    return deepcopy(value)


def _bind_local_evidence_sources(
    paper: dict,
    *,
    layout: AttemptBuildLayout,
    source_graph: SnapshotGraph,
) -> None:
    for source in paper.get("evidence_sources", []):
        path = _safe_workspace_path(source["path"])
        if not path.is_file():
            raise RuntimeError(f"local evidence file missing: {path}")
        if source.get("source_kind") == "fact_card_collection":
            collection_snapshot = source_graph.read(path)
            cards = json.loads(collection_snapshot.data.decode("utf-8"))
            by_id = {card.get("fact_card_id"): card for card in cards if isinstance(card, dict)}
            bound_cards = []
            selected_cards = []
            for card_id in source.get("card_ids", []):
                card = by_id.get(card_id)
                if card is None:
                    raise RuntimeError(f"fact card missing: {card_id}")
                snapshot = card.get("snapshot", {})
                snapshot_path = _safe_workspace_path(snapshot.get("relative_path", ""))
                snapshot_file = source_graph.read(snapshot_path)
                if snapshot_file.sha256 != snapshot.get("sha256"):
                    raise RuntimeError(f"fact card snapshot path/hash mismatch: {card_id}")
                selected_cards.append(_portable_projection_value(card))
                bound_cards.append(
                    {
                        "fact_card_id": card_id,
                        "review_status": card.get("review_status"),
                        "snapshot_path": snapshot["relative_path"],
                        "snapshot_sha256": snapshot["sha256"],
                        "source_type": card.get("source", {}).get("source_type"),
                        "original_url": card.get("source", {}).get("original_url"),
                        "formal_question_status": "not_a_formal_question",
                    }
                )
            projection = {
                "schema_version": "1.0.0-r18-portable-projection",
                "record_type": "fact_card_portable_projection",
                "source_collection": {
                    "locator": _relative(path),
                    "sha256": collection_snapshot.sha256,
                    "bytes": collection_snapshot.byte_length,
                    "included_verbatim": False,
                    "exclusion_reason": "source_contains_host_absolute_path_portable_projection_required",
                },
                "selected_card_ids": list(source.get("card_ids", [])),
                "cards": selected_cards,
                "original_question_republication": False,
                "formal_question_status": "not_a_formal_question",
                "human_reviewed": False,
            }
            validate_json_privacy(projection, label="fact_card_portable_projection")
            projection_path = (
                layout.temporary_root
                / "inputs/evidence/fact_cards_portable_r18.json"
            )
            projection_payload = (
                json.dumps(
                    projection,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            exclusive_create_bundle(
                workspace=layout.temporary_root,
                files=[(projection_path, projection_payload)],
            )
            source["source_locator"] = _relative(path)
            source["path"] = layout.relative(projection_path)
            source["sha256"] = sha256_file(projection_path)
            source["bytes"] = projection_path.stat().st_size
            source["fact_card_file_sha256"] = collection_snapshot.sha256
            source["fact_card_file_bytes"] = collection_snapshot.byte_length
            source["bound_cards"] = bound_cards
        elif source.get("source_kind") == "machine_verified_local_pdf_extract":
            extract = json.loads(path.read_text(encoding="utf-8"))
            if extract.get("evidence_id") not in source.get("evidence_ids", []):
                raise RuntimeError("machine-verified extract ID does not match paper binding")
            verified_refs = _verify_extract_file_refs(extract)
            source["extract_file_sha256"] = sha256_file(path)
            source["bound_extracts"] = [
                {
                    "evidence_id": extract["evidence_id"],
                    "verification_status": extract["verification_status"],
                    "source": extract["source"],
                    "verified_claim_ids": [row["claim_id"] for row in extract["verified_claims"]],
                    "claim_boundary": extract["claim_boundary"],
                    "verified_file_refs": verified_refs,
                }
            ]
        else:
            raise RuntimeError(f"unknown evidence source_kind: {source.get('source_kind')}")
        source["live_acquisition_performed"] = False


def _build_attempt_contents(
    layout: AttemptBuildLayout,
    *,
    profile: Path | None,
    allow_provisional_fixture: bool,
    generator_execution_metadata: Path,
    fault_injector: Callable[[str, AttemptBuildLayout], None] | None = None,
) -> None:
    paths = _attempt_paths(layout)
    source_graph = SnapshotGraph(WORKSPACE)
    integration_root = _stage_attempt_runtime_inputs(
        layout,
        source_graph=source_graph,
    )
    staged_execution_metadata = _stage_exact_file(
        layout=layout,
        source_workspace=WORKSPACE,
        source=generator_execution_metadata,
        relative=Path("inputs/execution/generator_execution_metadata.json"),
        source_graph=source_graph,
    )
    execution_binding = generator_execution_metadata_binding(
        staged_execution_metadata
    )
    for directory in (
        layout.candidate_dir,
        layout.report_dir,
        layout.asset_dir,
        layout.coordination_dir,
        layout.controller_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    paper = build_paper()
    _bind_local_evidence_sources(
        paper,
        layout=layout,
        source_graph=source_graph,
    )
    bound = bind_observed_profile(
        WORKSPACE,
        profile_path=profile,
        allow_provisional_fixture=allow_provisional_fixture,
    )
    if bound.fixture and not allow_provisional_fixture:
        raise RuntimeError("provisional observed profile requires the explicit fixture flag")
    rebind_task_card(paper, bound)
    plan = build_week_plan()
    registry = build_component_registry(WORKSPACE, figure_id=FIGURE_ID)
    validation_paper = deepcopy(paper)
    validation_registry = deepcopy(registry)

    svg_path = paths["svg"]
    png_path = paths["png"]
    spec_path = paths["spec"]
    svg_path.write_text(figure_svg(), encoding="utf-8")
    write_json(spec_path, figure_spec(FIGURE_ID))
    env = os.environ.copy()
    env["NODE_PATH"] = str(
        Path(r"C:\Users\20671\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules")
    )
    subprocess.run(
        [str(_node()), str(integration_root / "render_svg_png.mjs"), str(svg_path), str(png_path)],
        check=True,
        cwd=WORKSPACE,
        env=env,
    )

    paper_path = paths["paper"]
    task_path = paths["task"]
    plan_path = paths["plan"]
    registry_path = paths["registry"]
    _vendor_exact_refs(paper, layout=layout, source_graph=source_graph)
    _vendor_exact_refs(registry, layout=layout, source_graph=source_graph)
    write_json(paper_path, paper)
    write_json(task_path, paper["task_card"])
    write_json(plan_path, plan)
    write_json(registry_path, registry)
    paper_sha256 = sha256_file(paper_path)
    task_sha256 = sha256_file(task_path)
    producer_fingerprint = _producer_fingerprint(
        integration_root=integration_root,
        reference_root=layout.temporary_root,
    )
    regeneration_id = _regeneration_id(
        producer_fingerprint,
        paper_sha256=paper_sha256,
        task_card_sha256=task_sha256,
    )
    # Content-metadata validation is defined against the workspace taxonomy
    # binding.  ``paper`` has already had those exact refs rewritten to
    # attempt-local vendored paths, so validate and aggregate from the frozen
    # pre-vendoring copy while binding the result to the hashes of the packaged
    # paper and task card.
    coverage_matrix = build_coverage_matrix(
        validation_paper,
        paper_sha256=paper_sha256,
        task_card_sha256=task_sha256,
    )
    coverage_matrix["producer_fingerprint"] = producer_fingerprint
    coverage_matrix["regeneration_id"] = regeneration_id
    _vendor_exact_refs(coverage_matrix, layout=layout, source_graph=source_graph)
    write_json(paths["coverage"], coverage_matrix)

    solid_crossing = deepcopy(_load(spec_path))
    solid_crossing["network_contract"]["unapproved_solid_crossings"] = [
        {"network_a": "electrical_negative", "network_b": "gas_path", "coordinate": [417, 165]}
    ]
    electrical_short = deepcopy(_load(spec_path))
    electrical_short["network_contract"]["electrical_short_circuit"] = True
    electrical_short["network_contract"]["positive_negative_shared_nodes"] = ["accidental_junction"]
    invalid_hood = deepcopy(_load(spec_path))
    invalid_hood["network_contract"]["gas_hood_rim_submerged"] = False
    invalid_hood["network_contract"]["gas_hood_covers_immersed_anode"] = False
    broken_positive = deepcopy(_load(spec_path))
    broken_positive["network_contract"]["positive_path_continuous"] = False
    broken_positive["network_contract"]["positive_wire_visibly_terminates_at_anode"] = False
    disconnected_svg_path = layout.report_dir / "mutations" / f"{FIGURE_ID}.disconnected-gas-path.svg"
    disconnected_svg_path.parent.mkdir(parents=True, exist_ok=True)
    disconnected_svg = figure_svg().replace(
        'id="gas-tube" d="M400 235',
        'id="gas-tube" d="M412 235',
        1,
    )
    disconnected_svg_path.write_text(disconnected_svg, encoding="utf-8")
    disconnected_png_path = disconnected_svg_path.with_suffix(".png")
    subprocess.run(
        [
            str(_node()),
            str(integration_root / "render_svg_png.mjs"),
            str(disconnected_svg_path),
            str(disconnected_png_path),
        ],
        check=True,
        cwd=WORKSPACE,
        env=env,
    )
    figure_mutations = {
        "check": "figure_topology_mutation_rejection",
        "status": "pass",
        "cases": [
            {
                "mutation_id": "unapproved_different_network_solid_crossing",
                "expected_rejected": True,
                "observed_rejected": validate_figure(solid_crossing, svg_path, png_path, registry)["status"] == "fail",
            },
            {
                "mutation_id": "positive_negative_short_circuit",
                "expected_rejected": True,
                "observed_rejected": validate_figure(electrical_short, svg_path, png_path, registry)["status"] == "fail",
            },
            {
                "mutation_id": "gas_hood_opening_not_submerged_or_not_covering_anode",
                "expected_rejected": True,
                "observed_rejected": validate_figure(invalid_hood, svg_path, png_path, registry)["status"] == "fail",
            },
            {
                "mutation_id": "positive_wire_does_not_visibly_terminate_at_anode",
                "expected_rejected": True,
                "observed_rejected": validate_figure(broken_positive, svg_path, png_path, registry)["status"] == "fail",
            },
            {
                "mutation_id": "actual_svg_hood_to_tube_centerline_disconnected",
                "expected_rejected": True,
                "mutated_svg": _attempt_ref(layout, disconnected_svg_path),
                "mutated_png": _attempt_ref(layout, disconnected_png_path),
                "observed_rejected": validate_figure(
                    _load(spec_path),
                    disconnected_svg_path,
                    disconnected_png_path,
                    registry,
                )["status"] == "fail",
            },
        ],
        "errors": [],
    }
    if not all(row["observed_rejected"] for row in figure_mutations["cases"]):
        figure_mutations["status"] = "fail"
        figure_mutations["errors"].append("one or more apparatus topology mutations were not rejected")

    content_metadata_result = validate_content_metadata_contract(
        validation_paper, WORKSPACE
    )
    content_metadata_mutations = content_metadata_mutation_report(
        validation_paper, WORKSPACE
    )
    paper_validation = validate_paper(validation_paper)
    paper_validation["content_metadata_contract"] = content_metadata_result
    paper_validation["content_metadata_mutations"] = content_metadata_mutations
    if (
        content_metadata_result["status"] != "pass"
        or content_metadata_mutations["status"] != "pass"
    ):
        paper_validation["status"] = "fail"
        paper_validation.setdefault("errors", []).append(
            "content metadata contract or mutation gate failed"
        )
    checks = {
        "schema": paper_validation,
        "classification_vocabulary_mutations": classification_vocabulary_mutation_report(
            validation_paper
        ),
        "rubric_name_requirement_mutations": _required_name_rubric_mutation_report(validation_paper),
        "student_visible_prompt_safety_mutations": _prompt_safety_mutation_report(validation_paper),
        "p32_display_parser": _p32_display_parser_report(validation_paper),
        "r13_invalidation_correction": validate_r13_invalidation_correction(),
        "conservation": validate_equations(validation_paper),
        "inverse": validate_solvers(validation_paper),
        "components": validate_component_registry(WORKSPACE, validation_registry),
        "figure": validate_figure(_load(spec_path), svg_path, png_path, validation_registry),
        "figure_topology_mutations": figure_mutations,
        "cross_question_leakage": validate_cross_question_leakage(validation_paper, svg_path),
        "dedup": validate_dedup(
            validation_paper,
            source_crop_hashes(WORKSPACE),
            png_path,
            formal_registry_path=(
                WORKSPACE / "sh-chem-db/kb/formal/questions.jsonl"
            ),
        ),
        "coverage_matrix_validation": validate_coverage_matrix(coverage_matrix),
        "figure_visual_qa": _figure_visual_evidence(
            png_path,
            output_dir=paths["figure_visual_dir"],
            ref=lambda path: _attempt_ref(layout, path),
        ),
    }
    for name, result in checks.items():
        result["paper_sha256"] = paper_sha256
        result["task_card_sha256"] = task_sha256
        result["producer_fingerprint"] = producer_fingerprint
        result["regeneration_id"] = regeneration_id
        if name == "figure":
            result["figure_spec_file_sha256"] = sha256_file(spec_path)
        if name == "components":
            result["component_registry_file_sha256"] = sha256_file(registry_path)
        _vendor_exact_refs(result, layout=layout, source_graph=source_graph)
        _report(layout.report_dir / f"{name}.json", result)
    deterministic_scope = _atomic_deterministic_scope_report(paper, checks)
    deterministic_scope["paper_sha256"] = paper_sha256
    deterministic_scope["task_card_sha256"] = task_sha256
    deterministic_scope["producer_fingerprint"] = producer_fingerprint
    deterministic_scope["regeneration_id"] = regeneration_id
    _vendor_exact_refs(deterministic_scope, layout=layout, source_graph=source_graph)
    write_json(paths["deterministic_scope"], deterministic_scope)
    if deterministic_scope["status"] != "scope_pass_not_full_chemistry_proof":
        raise RuntimeError(f"atomic deterministic scope validation failed: {deterministic_scope}")
    schema_result = _schema_report(
        [
            ("paper", paper, integration_root / "schemas" / "paper_candidate.schema.json"),
            (
                "task_card",
                paper["task_card"],
                integration_root / "schemas" / "task_card.schema.json",
            ),
            ("component_registry", registry, integration_root / "schemas" / "component_registry.schema.json"),
        ],
        reference_root=layout.temporary_root,
    )
    schema_result["paper_sha256"] = paper_sha256
    schema_result["task_card_sha256"] = task_sha256
    schema_result["producer_fingerprint"] = producer_fingerprint
    schema_result["regeneration_id"] = regeneration_id
    _report(layout.report_dir / "versioned_schema.json", schema_result)

    controller_layout = ControllerBuildLayout(
        root=layout.temporary_root,
        bridge_dir=layout.controller_dir,
        question_path=paths["question"],
        answer_path=paths["answer"],
        request_path=paths["request"],
        report_path=paths["deterministic_report"],
        controller=layout.temporary_root / "sh-chem-db/scripts/sh_chem_agent.py",
        controller_cwd=layout.temporary_root,
    )
    controller_subject = prepare_controller_subject(paper, layout=controller_layout)
    review_subject = review_subject_from_deterministic_request(
        paths["request"],
        db_root=layout.temporary_root,
    )
    if controller_subject.get("review_subject") != review_subject:
        raise RuntimeError(
            "review subject serializer drifted from the written deterministic request"
        )
    _build_sol_generator_receipt(
        layout=layout,
        integration_root=integration_root,
        paper_path=paper_path,
        task_path=task_path,
        question_path=paths["question"],
        answer_path=paths["answer"],
        request_path=paths["request"],
        report_path=paths["deterministic_report"],
        controller_subject=controller_subject,
        execution_binding=execution_binding,
        producer_fingerprint=producer_fingerprint,
        regeneration_id=regeneration_id,
        output_path=paths["sol"],
    )
    question_subject = _load(paths["question"])
    question_isolation = validate_question_subject_isolation(paper, question_subject)
    _report(
        layout.report_dir / "question_subject_isolation.json",
        {
            **question_isolation,
            "paper_sha256": paper_sha256,
            "task_card_sha256": task_sha256,
            "producer_fingerprint": producer_fingerprint,
            "regeneration_id": regeneration_id,
        },
    )
    leaked_signature_mutation = deepcopy(question_subject)
    leaked_signature_mutation["themes"][0]["printed_questions"][0]["atomic_parts"][0][
        "prompt"
    ] += " 内部路线标记：ewaste-global-flow-boundary-single-choice。"
    semantic_mutation = validate_question_subject_isolation(paper, leaked_signature_mutation)
    renamed_key_mutation = deepcopy(question_subject)
    renamed_key_mutation["themes"][0]["printed_questions"][0]["atomic_parts"][0][
        "internal_note"
    ] = "ewaste-global-flow-boundary-single-choice"
    renamed_key_result = validate_question_subject_isolation(paper, renamed_key_mutation)
    _report(
        layout.report_dir / "question_subject_semantic_mutations.json",
        {
            "check": "question_subject_semantic_leakage_mutation_rejection",
            "status": "pass"
            if semantic_mutation["status"] == renamed_key_result["status"] == "fail"
            else "fail",
            "cases": [
                {
                    "mutation_id": "semantic_signature_value_injected_into_prompt",
                    "expected_rejected": True,
                    "observed_rejected": semantic_mutation["status"] == "fail",
                    "errors": semantic_mutation["errors"],
                },
                {
                    "mutation_id": "semantic_signature_value_injected_under_renamed_key",
                    "expected_rejected": True,
                    "observed_rejected": renamed_key_result["status"] == "fail",
                    "errors": renamed_key_result["errors"],
                },
            ],
            "errors": []
            if semantic_mutation["status"] == renamed_key_result["status"] == "fail"
            else ["semantic_leakage_mutation_not_rejected"],
            "paper_sha256": paper_sha256,
            "task_card_sha256": task_sha256,
            "producer_fingerprint": producer_fingerprint,
            "regeneration_id": regeneration_id,
        },
    )
    _report(
        layout.report_dir / "controller_machine_pass.json",
        {
            "check": "controller_v2_deterministic_machine_pass",
            "status": "pass",
            "paper_sha256": paper_sha256,
            "task_card_sha256": task_sha256,
            "subject_pair_sha256": controller_subject["subject_pair_sha256"],
            "deterministic_report_sha256": controller_subject["deterministic_report_sha256"],
            "deterministic_machine_check": controller_subject["deterministic_machine_check"],
            "producer_fingerprint": producer_fingerprint,
            "regeneration_id": regeneration_id,
            "governance_chain_created": False,
            "registered": False,
            "errors": [],
        },
    )

    _build_r18_producer_receipt(
        layout=layout,
        integration_root=integration_root,
        paper_path=paper_path,
        task_path=task_path,
        question_path=paths["question"],
        answer_path=paths["answer"],
        request_path=paths["request"],
        report_path=paths["deterministic_report"],
        coverage_path=paths["coverage"],
        deterministic_scope_path=paths["deterministic_scope"],
        output_path=paths["producer"],
        controller_subject=controller_subject,
        producer_fingerprint=producer_fingerprint,
        regeneration_id=regeneration_id,
        svg_path=svg_path,
        png_path=png_path,
        spec_path=spec_path,
    )
    if fault_injector is not None:
        fault_injector("before_provenance", layout)
    provenance_result = write_generator_provenance_receipt_r18(
        paths["provenance"],
        workspace=layout.temporary_root,
        artifact_id=paper["paper_id"],
        version_id=VERSION_ID,
        paper_id=paper["paper_id"],
        subject_pair_sha256_value=controller_subject["subject_pair_sha256"],
        reported_execution=generator_execution_provenance(execution_binding),
        r18_producer_receipt_path=paths["producer"],
        sol_generator_receipt_path=paths["sol"],
        question_path=paths["question"],
        answer_path=paths["answer"],
    )
    if provenance_result.get("provenance_status") != "self_reported":
        raise RuntimeError("R18 generator provenance receipt must remain self_reported")

    staged_profile = _stage_exact_file(
        layout=layout,
        source_workspace=WORKSPACE,
        source=bound.path,
        relative=Path("inputs/observed_profile/profile.json"),
        source_graph=source_graph,
    )
    staged_evidence_manifest = _stage_exact_file(
        layout=layout,
        source_workspace=WORKSPACE,
        source=bound.evidence_manifest_path,
        relative=Path("inputs/observed_profile/evidence_manifest.json"),
        source_graph=source_graph,
    )
    for current_input in (staged_profile, staged_evidence_manifest):
        _vendor_exact_refs(
            _load(current_input),
            layout=layout,
            source_graph=source_graph,
        )
    legacy_sources = {
        "superseded": COORDINATION / "R18_PREFREEZE_INVALIDATED_CONTENT_METADATA.json",
        "r13_initial": COORDINATION / "R13_FREEZE_INVALIDATED.json",
        "r13_correction": COORDINATION / "R13_FREEZE_INVALIDATION_CORRECTION.json",
    }
    staged_legacy = {
        key: _stage_exact_file(
            layout=layout,
            source_workspace=WORKSPACE,
            source=source,
            relative=Path("inputs/legacy_records") / source.name,
            source_graph=source_graph,
        )
        for key, source in legacy_sources.items()
    }

    prefreeze_receipt = {
        "schema_version": "1.0.0",
        "record_type": "generation_v2_prefreeze_self_check",
        "version_id": VERSION_ID,
        "paper_path": layout.relative(paper_path),
        "paper_sha256": paper_sha256,
        "task_card_path": layout.relative(task_path),
        "task_card_sha256": task_sha256,
        "profile_id": bound.data["profile_id"],
        "profile_version": bound.data["profile_version"],
        "profile_path": layout.relative(staged_profile),
        "profile_sha256": bound.sha256,
        "evidence_manifest_path": layout.relative(staged_evidence_manifest),
        "evidence_manifest_sha256": bound.evidence_manifest_sha256,
        "provisional_fixture": bound.fixture,
        "coverage_matrix_path": layout.relative(paths["coverage"]),
        "coverage_matrix_sha256": sha256_file(paths["coverage"]),
        "deterministic_atomic_scope_path": layout.relative(paths["deterministic_scope"]),
        "deterministic_atomic_scope_sha256": sha256_file(paths["deterministic_scope"]),
        "controller_subject": {
            "question": _attempt_ref(layout, paths["question"]),
            "answer": _attempt_ref(layout, paths["answer"]),
            "subject_pair_sha256": controller_subject["subject_pair_sha256"],
        },
        "deterministic_request": _attempt_ref(layout, paths["request"]),
        "deterministic_report": _attempt_ref(layout, paths["deterministic_report"]),
        "sol_generator_receipt": _attempt_ref(layout, paths["sol"]),
        "r18_producer_receipt": _attempt_ref(layout, paths["producer"]),
        "generator_provenance_receipt": _attempt_ref(layout, paths["provenance"]),
        "root_external_reconciliation_verified": False,
        "formal_freeze_contract": {
            "status": "not_created_root_owned_predispatch_contract",
            "formal_freeze_schema": _attempt_ref(
                layout, layout.temporary_root / FORMAL_SCHEMA_RELATIVE
            ),
            "formal_freeze_sidecar_schema": _attempt_ref(
                layout, layout.temporary_root / FORMAL_SIDECAR_SCHEMA_RELATIVE
            ),
            "review_dispatch_prepared": False,
            "review_dispatch_authorized": False,
            "chain_creation_authorized": False,
            "controller_registration_authorized": False,
            "publication_authorized": False,
            "delivery_authorized": False,
        },
        "question_subject_isolation": _attempt_ref(
            layout, layout.report_dir / "question_subject_isolation.json"
        ),
        "question_subject_semantic_mutations": _attempt_ref(
            layout, layout.report_dir / "question_subject_semantic_mutations.json"
        ),
        "superseded_prefreeze_invalidation": _attempt_ref(
            layout, staged_legacy["superseded"]
        ),
        "predecessor_invalidation": {
            "initial_authoritative_record": _attempt_ref(
                layout, staged_legacy["r13_initial"]
            ),
            "drift_correction": _attempt_ref(
                layout, staged_legacy["r13_correction"]
            ),
            "validation": _attempt_ref(
                layout, layout.report_dir / "r13_invalidation_correction.json"
            ),
        },
        "figure_svg": _attempt_ref(layout, svg_path),
        "figure_png": _attempt_ref(layout, png_path),
        "figure_spec": _attempt_ref(layout, spec_path),
        "producer_fingerprint": producer_fingerprint,
        "regeneration_id": regeneration_id,
        "content_status": "prefreeze_self_check_pass_root_review_required",
        "review_dispatch_created": False,
        "governance_chain_created": False,
        "controller_registration_requested": False,
        "publication_requested": False,
        "next_required": ["root_prefreeze_approval_before_any_review_dispatch"],
        "human_reviewed": False,
        "publication_allowed": False,
    }
    write_json(paths["prefreeze"], prefreeze_receipt)
    write_json(
        paths["delivery"],
        {
            "schema_version": "1.0.0-r18-prefreeze",
            "record_type": "generation_v2_prefreeze_delivery_status",
            "version_id": VERSION_ID,
            "content_status": "prefreeze_self_check_pass_root_review_required",
            "teacher_managed_delivery_candidate": False,
            "delivery_scope": None,
            "teacher_action_required": False,
            "root_action_required": True,
            "next_required": "root_prefreeze_approval_before_any_review_dispatch",
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "official_claim_allowed": False,
            "official_publication_allowed": False,
            "external_publication_allowed": False,
            "publication_allowed": False,
            "release_allowed": False,
            "reason": "R18 prefreeze self-check completed. Root approval is required before any formal review dispatch; no review, adversarial, chain, registration or publication artifact has been created.",
        },
    )
    _stage_current_json_reference_closure(
        layout=layout,
        source_graph=source_graph,
    )
    source_checkpoint = source_graph.revalidate()
    if source_checkpoint.get("status") != "pass":
        raise RuntimeError(
            "attempt source snapshot changed before package validation: "
            + ",".join(source_checkpoint.get("errors", []))
        )


LEGACY_LIVE_RELATIVE_PATHS = tuple(
    [
        "staging/coordination/generation_publication/generator_execution_metadata_r18.json",
        "staging/coordination/generation_publication/prefreeze_receipt_r18.json",
        *[
            f"staging/v1_generation/candidates/r18/{name}"
            for name in (
                "delivery_status.json",
                "frozen_paper.json",
                "generator_provenance_receipt.json",
                "r18_producer_receipt.json",
                "sol_generator_receipt.json",
                "task_card.json",
                "week_plan.json",
            )
        ],
        *[
            f"staging/v1_generation/reports/r18/{name}"
            for name in (
                "components.json",
                "conservation.json",
                "controller_machine_pass.json",
                "coverage_matrix.json",
                "coverage_matrix_validation.json",
                "cross_question_leakage.json",
                "dedup.json",
                "deterministic_atomic_scope.json",
                "figure.json",
                "figure_topology_mutations.json",
                "figure_visual_qa.json",
                "inverse.json",
                "p32_display_parser.json",
                "question_subject_isolation.json",
                "question_subject_semantic_mutations.json",
                "r13_invalidation_correction.json",
                "rubric_name_requirement_mutations.json",
                "schema.json",
                "student_visible_prompt_safety_mutations.json",
                "versioned_schema.json",
                "figure_visual_qa/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.onebit-print.png",
                "figure_visual_qa/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.quarter-grayscale.png",
                "mutations/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.disconnected-gas-path.png",
                "mutations/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.disconnected-gas-path.svg",
            )
        ],
        *[
            f"sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/{name}"
            for name in (
                "deterministic_check_report.json",
                "deterministic_check_request.json",
                "subject_answer.json",
                "subject_question.json",
            )
        ],
        "sh-chem-db/kb/figures/machine_v2/assets/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.png",
        "sh-chem-db/kb/figures/machine_v2/assets/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.svg",
        "sh-chem-db/kb/figures/machine_v2/component_registry_r18.json",
        "sh-chem-db/kb/figures/machine_v2/FIG-GEN-V2-EWASTE-ELECTROLYSIS-008.spec.json",
    ]
)

if len(LEGACY_LIVE_RELATIVE_PATHS) != 41:  # pragma: no cover - import invariant
    raise RuntimeError("R18 legacy live inventory must remain exactly 41 paths")


def legacy_live_paths(workspace: Path = WORKSPACE) -> tuple[Path, ...]:
    root = workspace.resolve()
    return tuple(root / relative for relative in LEGACY_LIVE_RELATIVE_PATHS)


def detect_legacy_partial_live(workspace: Path = WORKSPACE) -> dict[str, object]:
    """Detect the known mixed-generation fixed-path state without writing."""

    root = workspace.resolve()
    files = legacy_live_paths(root)
    missing = [path.relative_to(root).as_posix() for path in files if not path.is_file()]
    errors: list[str] = []
    if missing and len(missing) != len(files):
        errors.append(f"mixed_presence:{len(files) - len(missing)}_present:{len(missing)}_missing")

    def load(relative: str) -> dict:
        path = root / relative
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"unreadable:{relative}:{type(exc).__name__}")
            return {}
        return value if isinstance(value, dict) else {}

    producer = load("staging/v1_generation/candidates/r18/r18_producer_receipt.json")
    sol = load("staging/v1_generation/candidates/r18/sol_generator_receipt.json")
    provenance = load(
        "staging/v1_generation/candidates/r18/generator_provenance_receipt.json"
    )
    prefreeze = load("staging/coordination/generation_publication/prefreeze_receipt_r18.json")
    fingerprints = {
        label: value.get("producer_fingerprint", {}).get("source_tree_sha256")
        for label, value in (("producer", producer), ("sol", sol), ("prefreeze", prefreeze))
        if isinstance(value.get("producer_fingerprint"), dict)
    }
    observed_fingerprints = {value for value in fingerprints.values() if isinstance(value, str)}
    if len(observed_fingerprints) > 1:
        errors.append(
            "producer_generation_mismatch:"
            + ",".join(f"{key}={value}" for key, value in sorted(fingerprints.items()))
        )

    paper_path = root / "staging/v1_generation/candidates/r18/frozen_paper.json"
    if paper_path.is_file() and prefreeze.get("paper_sha256") not in {
        None,
        sha256_file(paper_path),
    }:
        errors.append("prefreeze_paper_generation_mismatch")

    producer_path = root / "staging/v1_generation/candidates/r18/r18_producer_receipt.json"
    bound_producer = provenance.get("r18_producer_receipt")
    if isinstance(bound_producer, dict) and producer_path.is_file():
        if bound_producer.get("sha256") != sha256_file(producer_path):
            errors.append("provenance_producer_generation_mismatch")
    sol_path = root / "staging/v1_generation/candidates/r18/sol_generator_receipt.json"
    bound_sol = provenance.get("sol_generator_receipt")
    if isinstance(bound_sol, dict) and sol_path.is_file():
        if bound_sol.get("sha256") != sha256_file(sol_path):
            errors.append("provenance_sol_generation_mismatch")

    return {
        "status": "partial_legacy_fixed_path_live_blocked" if errors else "not_partial",
        "blocked": bool(errors),
        "legacy_live_path_count": len(files),
        "present_count": len(files) - len(missing),
        "missing_count": len(missing),
        "missing": missing,
        "producer_fingerprints": fingerprints,
        "errors": sorted(dict.fromkeys(errors)),
        "successful_attempt_input_allowed": False,
    }


def _default_attempt_protected_paths(workspace: Path = WORKSPACE) -> tuple[Path, ...]:
    root = workspace.resolve()
    selected = set(legacy_live_paths(root))
    protected_roots = (
        root / "staging/v1_generation/history/r18_v2_clean1_01a023cf-6ff3-7af2-a94e-487976c9dd75",
        root / "staging/coordination/root/revisions/r18/state_invalidations",
        root / "staging/coordination/root/revisions/r18/state_invalidation_corrections",
        root / "staging/coordination/root/revisions/r18/state_invalidation_corpus_generations",
        root / "staging/coordination/root/revisions/r18/review_task_attestations",
        root / "staging/coordination/root/revisions/r18/metadata_observations",
        root / "staging/coordination/root/revisions/r18/review_metadata_observations",
    )
    for protected_root in protected_roots:
        # Protect the directory as a closed inventory, or its exact absence,
        # not merely the files that happened to exist when enumeration ran.
        # The snapshot then rejects additions, removals and same-byte ABA
        # replacements.
        selected.add(protected_root)
    for name in ("sol_review_a.json", "sol_review_b.json"):
        review = (
            root
            / "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18"
            / name
        )
        # An absent review is also a protected state: creating it concurrently
        # during a prefreeze build must invalidate the attempt.
        selected.add(review)
    return tuple(sorted(selected, key=lambda path: path.relative_to(root).as_posix()))


def _assert_legacy_downstream_blocked() -> None:
    state = detect_legacy_partial_live(WORKSPACE)
    if state["blocked"]:
        raise RuntimeError(
            "R18_PARTIAL_LEGACY_LIVE_BLOCKED_PREWRITE:"
            + ",".join(state["errors"])
        )


def freeze(profile: Path | None, allow_provisional_fixture: bool) -> None:
    """The unsafe fixed-path writer is permanently disabled."""

    state = detect_legacy_partial_live(WORKSPACE)
    reason = "R18_LEGACY_FIXED_PATH_FREEZE_DISABLED_USE_FREEZE_ATTEMPT"
    if state["blocked"]:
        reason += ":R18_PARTIAL_LEGACY_LIVE_BLOCKED_PREWRITE:" + ",".join(
            state["errors"]
        )
    raise RuntimeError(reason)


def freeze_attempt(
    *,
    attempt_id: str,
    profile: Path | None,
    allow_provisional_fixture: bool,
    generator_execution_metadata: Path | None = None,
    workspace: Path = WORKSPACE,
    store_root: Path | None = None,
    protected_paths: tuple[Path, ...] | None = None,
    fault_injector: Callable[[str, AttemptBuildLayout], None] | None = None,
) -> dict[str, object]:
    """Build and commit one immutable package without changing current/live."""

    root = workspace.resolve()
    store = (store_root or (root / ATTEMPT_STORE.relative_to(WORKSPACE))).resolve()
    if generator_execution_metadata is None:
        raise RuntimeError("R18_ATTEMPT_EXPLICIT_GENERATOR_EXECUTION_METADATA_REQUIRED")
    execution_metadata_path = Path(
        os.path.abspath(os.fspath(generator_execution_metadata))
    )
    try:
        execution_metadata_path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            "R18_ATTEMPT_GENERATOR_EXECUTION_METADATA_OUTSIDE_WORKSPACE"
        ) from exc
    legacy_folded = {
        os.path.normcase(os.path.abspath(os.fspath(path))).casefold()
        for path in legacy_live_paths(root)
    }
    if (
        os.path.normcase(os.path.abspath(os.fspath(execution_metadata_path))).casefold()
        in legacy_folded
    ):
        raise RuntimeError(
            "R18_ATTEMPT_LEGACY_LIVE_INPUT_FORBIDDEN:generator_execution_metadata"
        )
    if not execution_metadata_path.is_file():
        raise RuntimeError("R18_ATTEMPT_GENERATOR_EXECUTION_METADATA_FILE_REQUIRED")
    base_protected = protected_paths or _default_attempt_protected_paths(root)
    paths_to_protect = tuple(base_protected)
    if execution_metadata_path is not None and all(
        os.path.normcase(os.path.abspath(os.fspath(path))).casefold()
        != os.path.normcase(os.path.abspath(os.fspath(execution_metadata_path))).casefold()
        for path in paths_to_protect
    ):
        paths_to_protect += (execution_metadata_path,)
    protected = ProtectedPathSnapshot.capture(root, paths_to_protect)
    layout = AttemptBuildLayout.plan(
        workspace=root,
        store_root=store,
        attempt_id=attempt_id,
    )
    if root != WORKSPACE.resolve():
        raise RuntimeError("default R18 builder requires the canonical source workspace")

    def selected_builder(selected_layout: AttemptBuildLayout) -> None:
        _build_attempt_contents(
            selected_layout,
            profile=profile,
            allow_provisional_fixture=allow_provisional_fixture,
            generator_execution_metadata=execution_metadata_path,
            fault_injector=fault_injector,
        )
    return execute_attempt_transaction(
        layout=layout,
        protected=protected,
        builder=selected_builder,
        build_mode=PRODUCTION_BUILD_MODE,
        fault_injector=fault_injector,
    )


def prepare_dispatch(
    *,
    formal_freeze: Path,
    formal_freeze_sidecar: Path,
    generator_reconciliation: Path,
    root_phase1_authorization: Path,
    review_attempt_id: str,
) -> dict:
    """Public phase-1 entry point; explicit root prerequisites are mandatory."""

    return prepare_dispatch_from_root_go(
        formal_freeze=formal_freeze,
        formal_freeze_sidecar=formal_freeze_sidecar,
        generator_reconciliation=generator_reconciliation,
        root_phase1_authorization=root_phase1_authorization,
        review_attempt_id=review_attempt_id,
    )


def _phase1_authoritative_inputs(
    *,
    formal_freeze: Path,
    formal_freeze_sidecar: Path,
    generator_reconciliation: Path,
    root_phase1_authorization: Path,
) -> dict[str, Path]:
    paper_path = CANDIDATE_DIR / "frozen_paper.json"
    task_path = CANDIDATE_DIR / "task_card.json"
    paper = _load(paper_path)
    profile_contract = paper.get("observed_profile_contract")
    if not isinstance(profile_contract, dict):
        raise RuntimeError("R18 paper observed_profile_contract is required")
    profile_path = WORKSPACE.joinpath(
        *str(profile_contract.get("profile_path", "")).split("/")
    )
    evidence = profile_contract.get("evidence_manifest")
    if not isinstance(evidence, dict):
        raise RuntimeError("R18 paper evidence_manifest binding is required")
    evidence_path = WORKSPACE.joinpath(*str(evidence.get("path", "")).split("/"))
    return {
        "paper": paper_path,
        "task_card": task_path,
        "question_subject": QUESTION_PATH,
        "answer_subject": ANSWER_PATH,
        "deterministic_request": REQUEST_PATH,
        "deterministic_report": REPORT_PATH,
        "coverage_matrix": COVERAGE_PATH,
        "deterministic_atomic_scope": DETERMINISTIC_SCOPE_PATH,
        "figure_svg": ASSET_DIR / f"{FIGURE_ID}.svg",
        "figure_png": ASSET_DIR / f"{FIGURE_ID}.png",
        "figure_spec": FIGURES / f"{FIGURE_ID}.spec.json",
        "component_registry": FIGURES / "component_registry_r18.json",
        "figure_visual_qa": REPORT_DIR / "figure_visual_qa.json",
        "figure_topology_mutations": REPORT_DIR / "figure_topology_mutations.json",
        "observed_profile": profile_path,
        "evidence_manifest": evidence_path,
        "project_generation_authorization": WORKSPACE
        / "sh-chem-db/kb/machine_governance_v2/project_generation_authorization.json",
        "prefreeze_receipt": PREFREEZE_RECEIPT_PATH,
        "r18_producer_receipt": R18_PRODUCER_RECEIPT_PATH,
        "sol_generator_receipt": SOL_GENERATOR_RECEIPT_PATH,
        "generator_provenance_receipt": GENERATOR_PROVENANCE_RECEIPT_PATH,
        "formal_freeze": formal_freeze,
        "formal_freeze_sidecar": formal_freeze_sidecar,
        "generator_reconciliation": generator_reconciliation,
        "root_phase1_authorization": root_phase1_authorization,
    }


def prepare_dispatch_from_root_go(
    *,
    formal_freeze: Path,
    formal_freeze_sidecar: Path,
    generator_reconciliation: Path,
    root_phase1_authorization: Path,
    review_attempt_id: str,
) -> dict:
    """Create only phase-1 A/B prompt, dispatch and activation artifacts."""

    _assert_legacy_downstream_blocked()
    activation_id = r18_phase1_activation_id(
        root_phase1_authorization,
        review_attempt_id=review_attempt_id,
        workspace=WORKSPACE,
        expected_version_id=VERSION_ID,
    )
    bundle_dir = (
        WORKSPACE
        / "staging/coordination/generation_publication/r18/review_dispatch"
        / activation_id
    )
    prompt_a_path = bundle_dir / "REVIEW_PROMPT_A.md"
    prompt_b_path = bundle_dir / "REVIEW_PROMPT_B.md"
    dispatch_path = bundle_dir / "review_dispatch_request_r18.json"
    activation_path = bundle_dir / "review_phase1_activation_r18.json"
    review_output_paths = r18_review_attempt_output_paths(
        activation_path, workspace=WORKSPACE
    )
    prompt_a = render_phase1_reviewer_prompt(
        "sol_review_a",
        output_path=_relative(review_output_paths["sol_review_a"]),
        workspace=WORKSPACE,
    )
    prompt_b = render_phase1_reviewer_prompt(
        "sol_review_b",
        output_path=_relative(review_output_paths["sol_review_b"]),
        workspace=WORKSPACE,
    )
    result = create_phase1_review_dispatch_activation(
        formal_freeze_path=formal_freeze,
        formal_freeze_sidecar_path=formal_freeze_sidecar,
        reconciliation_path=generator_reconciliation,
        root_authorization_path=root_phase1_authorization,
        review_attempt_id=review_attempt_id,
        prompt_a_text=prompt_a,
        prompt_b_text=prompt_b,
        prompt_a_path=prompt_a_path,
        prompt_b_path=prompt_b_path,
        dispatch_path=dispatch_path,
        activation_path=activation_path,
        immutable_inputs=_phase1_authoritative_inputs(
            formal_freeze=formal_freeze,
            formal_freeze_sidecar=formal_freeze_sidecar,
            generator_reconciliation=generator_reconciliation,
            root_phase1_authorization=root_phase1_authorization,
        ),
        schema_paths=r18_phase1_schema_paths(
            workspace=WORKSPACE, db_root=WORKSPACE / "sh-chem-db"
        ),
        output_paths=review_output_paths,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def phase2_dispatch(
    *,
    phase1_activation: Path,
    review_a: Path | None = None,
    review_b: Path | None = None,
    observation_a: Path,
    observation_b: Path,
    formal_freeze: Path,
    formal_freeze_sidecar: Path,
    generator_reconciliation: Path,
    root_phase1_authorization: Path,
) -> dict:
    """Authorize only the new adversarial task after exact live A/B PASS."""

    _assert_legacy_downstream_blocked()
    review_outputs = r18_review_attempt_output_paths(
        phase1_activation, workspace=WORKSPACE
    )
    expected_review_a = review_outputs["sol_review_a"]
    expected_review_b = review_outputs["sol_review_b"]
    if review_a is not None and review_a.resolve() != expected_review_a.resolve():
        raise RuntimeError("review A path is not exact phase-1 attempt output")
    if review_b is not None and review_b.resolve() != expected_review_b.resolve():
        raise RuntimeError("review B path is not exact phase-1 attempt output")
    review_a = expected_review_a
    review_b = expected_review_b
    adversarial_prompt, sidecar = r18_phase2_output_paths(
        phase1_activation, workspace=WORKSPACE, expected_version_id=VERSION_ID
    )
    immutable_inputs = _phase1_authoritative_inputs(
        formal_freeze=formal_freeze,
        formal_freeze_sidecar=formal_freeze_sidecar,
        generator_reconciliation=generator_reconciliation,
        root_phase1_authorization=root_phase1_authorization,
    )
    schema_paths = r18_phase1_schema_paths(
        workspace=WORKSPACE, db_root=WORKSPACE / "sh-chem-db"
    )
    result = create_phase2_adversarial_dispatch(
        phase1_activation_path=phase1_activation,
        review_a_path=review_a,
        review_b_path=review_b,
        observation_a_path=observation_a,
        observation_b_path=observation_b,
        sidecar_path=sidecar,
        adversarial_prompt_path=adversarial_prompt,
        immutable_inputs=immutable_inputs,
        schema_paths=schema_paths,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _render_docx(docx_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    final_pdf = docx_path.with_suffix(".pdf")
    for stale in output_dir.glob("page-*.png"):
        stale.unlink()
    if shutil.which("soffice"):
        subprocess.run(
            [
                str(_python()),
                str(_render_docx_script()),
                str(docx_path),
                "--output_dir",
                str(output_dir),
                "--emit_pdf",
            ],
            check=True,
            cwd=WORKSPACE,
        )
        emitted = output_dir / f"{docx_path.stem}.pdf"
        if not emitted.is_file():
            raise RuntimeError(f"renderer did not emit PDF: {emitted}")
        shutil.copy2(emitted, final_pdf)
    else:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INTEGRATION / "render_docx_windows.ps1"),
                "-InputDocx",
                str(docx_path),
                "-OutputPdf",
                str(final_pdf),
            ],
            check=True,
            cwd=WORKSPACE,
        )
        subprocess.run(
            [str(_pdftoppm()), "-png", "-r", "160", str(final_pdf), str(output_dir / "page")],
            check=True,
            cwd=WORKSPACE,
        )
    return final_pdf


def finalize() -> None:
    from .publication import finalize_atomic

    _assert_legacy_downstream_blocked()
    finalize_atomic()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build SHCHEM generation/publication v2 candidates")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("--profile", type=Path)
    freeze_parser.add_argument("--allow-provisional-fixture", action="store_true")
    attempt_parser = sub.add_parser(
        "freeze-attempt",
        help="build below a unique R18 attempt root and atomically commit one immutable package",
    )
    attempt_parser.add_argument(
        "--attempt-id",
        required=True,
        help="R18-BUILD-ATTEMPT- followed by exactly 32 lowercase hex characters",
    )
    attempt_parser.add_argument(
        "--generator-execution-metadata",
        type=Path,
        required=True,
        help="explicit clean execution self-report; the legacy fixed R18 metadata path is forbidden",
    )
    attempt_parser.add_argument("--profile", type=Path)
    attempt_parser.add_argument("--allow-provisional-fixture", action="store_true")
    check_attempt_parser = sub.add_parser(
        "check-attempt",
        help="validate and select only an immutable committed attempt package",
    )
    check_attempt_parser.add_argument("--attempt-id", required=True)
    prepare_parser = sub.add_parser(
        "prepare-dispatch",
        help="atomically create only R18 phase-1 A/B prompts, dispatch and activation",
    )
    prepare_parser.add_argument("--formal-freeze", type=Path, required=True)
    prepare_parser.add_argument("--formal-freeze-sidecar", type=Path, required=True)
    prepare_parser.add_argument("--generator-reconciliation", type=Path, required=True)
    prepare_parser.add_argument("--root-phase1-authorization", type=Path, required=True)
    prepare_parser.add_argument(
        "--review-attempt-id",
        required=True,
        help="explicit append-only ID: R18-REVIEW-ATTEMPT- followed by 32 lowercase hex characters",
    )
    phase2_parser = sub.add_parser(
        "phase2-dispatch",
        help="freeze exact live A/B PASS receipts and authorize only adversarial review",
    )
    phase2_parser.add_argument("--phase1-activation", type=Path, required=True)
    phase2_parser.add_argument("--review-a", type=Path)
    phase2_parser.add_argument("--review-b", type=Path)
    phase2_parser.add_argument("--observation-a", type=Path, required=True)
    phase2_parser.add_argument("--observation-b", type=Path, required=True)
    phase2_parser.add_argument("--formal-freeze", type=Path, required=True)
    phase2_parser.add_argument("--formal-freeze-sidecar", type=Path, required=True)
    phase2_parser.add_argument("--generator-reconciliation", type=Path, required=True)
    phase2_parser.add_argument("--root-phase1-authorization", type=Path, required=True)
    sub.add_parser("finalize")
    args = parser.parse_args()
    if args.command == "freeze":
        profile = args.profile.resolve() if args.profile else None
        freeze(profile, args.allow_provisional_fixture)
    elif args.command == "freeze-attempt":
        profile = args.profile.resolve() if args.profile else None
        result = freeze_attempt(
            attempt_id=args.attempt_id,
            profile=profile,
            allow_provisional_fixture=args.allow_provisional_fixture,
            generator_execution_metadata=args.generator_execution_metadata,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.command == "check-attempt":
        result = select_immutable_attempt(
            workspace=WORKSPACE,
            store_root=ATTEMPT_STORE,
            attempt_id=args.attempt_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.command == "prepare-dispatch":
        prepare_dispatch(
            formal_freeze=args.formal_freeze.resolve(),
            formal_freeze_sidecar=args.formal_freeze_sidecar.resolve(),
            generator_reconciliation=args.generator_reconciliation.resolve(),
            root_phase1_authorization=args.root_phase1_authorization.resolve(),
            review_attempt_id=args.review_attempt_id,
        )
    elif args.command == "phase2-dispatch":
        phase2_dispatch(
            phase1_activation=args.phase1_activation.resolve(),
            review_a=args.review_a.resolve() if args.review_a else None,
            review_b=args.review_b.resolve() if args.review_b else None,
            observation_a=args.observation_a.resolve(),
            observation_b=args.observation_b.resolve(),
            formal_freeze=args.formal_freeze.resolve(),
            formal_freeze_sidecar=args.formal_freeze_sidecar.resolve(),
            generator_reconciliation=args.generator_reconciliation.resolve(),
            root_phase1_authorization=args.root_phase1_authorization.resolve(),
        )
    elif args.command == "finalize":
        finalize()
    else:  # pragma: no cover - argparse enforces the choices
        parser.error(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()

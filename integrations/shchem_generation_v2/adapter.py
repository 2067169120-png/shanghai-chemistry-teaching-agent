from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


ALLOWED_VALUE_STATUS = {"evidence", "unknown", "project_template"}


class ObservedProfileUnavailable(RuntimeError):
    pass


class ObservedProfileContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class BoundObservedProfile:
    path: Path
    data: dict
    sha256: str
    evidence_manifest_path: Path
    evidence_manifest_sha256: str
    fixture: bool = False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ObservedProfileContractError(f"JSON object required: {path}")
    return value


def _validate_bound_value(name: str, item: object) -> None:
    if not isinstance(item, dict):
        raise ObservedProfileContractError(f"organization.{name} must be an object")
    status = item.get("value_status")
    if status not in ALLOWED_VALUE_STATUS:
        raise ObservedProfileContractError(
            f"organization.{name}.value_status invalid: {status!r}"
        )
    refs = item.get("evidence_refs")
    if not isinstance(refs, list):
        raise ObservedProfileContractError(
            f"organization.{name}.evidence_refs must be an array"
        )
    if status == "evidence" and not refs:
        raise ObservedProfileContractError(
            f"organization.{name} claims evidence without evidence_refs"
        )


def validate_observed_profile(data: dict) -> None:
    for field in (
        "profile_id",
        "profile_version",
        "evidence_manifest",
        "organization",
        "gates",
    ):
        if field not in data:
            raise ObservedProfileContractError(f"missing observed profile field: {field}")
    organization = data["organization"]
    if not isinstance(organization, dict):
        raise ObservedProfileContractError("organization must be an object")
    for name in ("theme_count", "numbering_mode", "total_score", "duration_minutes"):
        _validate_bound_value(name, organization.get(name))
    hierarchy = data.get("hierarchy") or {}
    if hierarchy.get("required_path") != [
        "paper",
        "theme_big_question",
        "printed_question",
        "atomic_part",
    ]:
        raise ObservedProfileContractError("four-level hierarchy is not bound")
    if hierarchy.get("standalone_choice_section_allowed") is not False:
        raise ObservedProfileContractError("standalone choice section must fail closed")
    gates = data["gates"]
    if gates.get("read_only_style_profile_consumption_allowed") is not True:
        raise ObservedProfileContractError("profile is not allowed for read-only style use")
    if any(
        gates.get(key) is True
        for key in (
            "question_retrieval_allowed",
            "unattended_generation_allowed",
            "official_claim_allowed",
            "human_reviewed",
            "publication_allowed",
            "release_allowed",
        )
    ):
        raise ObservedProfileContractError("observed profile overclaims an unavailable gate")


def discover_observed_profile(workspace: Path) -> Path | None:
    root = workspace / "sh-chem-db" / "kb" / "shanghai_observed_standard_v1"
    if not root.exists():
        return None
    candidates: list[Path] = []
    for path in root.rglob("*.json"):
        lowered = path.name.lower()
        if "schema" in path.parts or lowered.endswith("schema.json"):
            continue
        try:
            value = _load_json(path)
        except (OSError, ValueError, ObservedProfileContractError):
            continue
        if "profile_id" in value and "organization" in value and "evidence_manifest" in value:
            candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)
    return candidates[0]


def bind_observed_profile(
    workspace: Path,
    *,
    profile_path: Path | None = None,
    allow_provisional_fixture: bool = False,
) -> BoundObservedProfile:
    fixture = False
    if profile_path is None:
        profile_path = discover_observed_profile(workspace)
    if profile_path is None and allow_provisional_fixture:
        profile_path = (
            workspace
            / "integrations"
            / "shchem_generation_v2"
            / "fixtures"
            / "observed_profile_provisional.fixture.json"
        )
        fixture = True
    if profile_path is None:
        raise ObservedProfileUnavailable(
            "No produced shanghai_observed_standard_v1 profile is available; "
            "final candidate promotion is blocked."
        )
    profile_path = profile_path.resolve()
    data = _load_json(profile_path)
    validate_observed_profile(data)
    manifest_ref = data["evidence_manifest"]
    manifest_path = Path(str(manifest_ref["path"]))
    if not manifest_path.is_absolute():
        manifest_path = (workspace / manifest_path).resolve()
    if not manifest_path.is_file():
        raise ObservedProfileContractError(
            f"observed evidence manifest missing: {manifest_path}"
        )
    actual_manifest_hash = _sha256(manifest_path)
    expected_manifest_hash = manifest_ref.get("sha256")
    if expected_manifest_hash != actual_manifest_hash:
        raise ObservedProfileContractError(
            "observed evidence manifest sha256 mismatch: "
            f"expected {expected_manifest_hash}, got {actual_manifest_hash}"
        )
    return BoundObservedProfile(
        path=profile_path,
        data=data,
        sha256=_sha256(profile_path),
        evidence_manifest_path=manifest_path,
        evidence_manifest_sha256=actual_manifest_hash,
        fixture=fixture,
    )


def rebind_task_card(paper: dict, bound: BoundObservedProfile) -> None:
    workspace = Path(__file__).resolve().parents[2]

    def portable_path(path: Path) -> str:
        """Persist workspace-relative paths so exported JSON never leaks host identity."""
        resolved = path.resolve()
        try:
            return resolved.relative_to(workspace.resolve()).as_posix()
        except ValueError as exc:
            raise ObservedProfileContractError(
                f"bound observed-profile evidence is outside the workspace: {resolved}"
            ) from exc

    organization = bound.data["organization"]
    authorization_path = (
        workspace / "sh-chem-db/kb/machine_governance_v2/project_generation_authorization.json"
    ).resolve()
    if not authorization_path.is_file():
        raise ObservedProfileContractError("project generation authorization is missing")
    authorization = _load_json(authorization_path)
    if (
        authorization.get("authorization_version") != "1.0.0"
        or authorization.get("profile_role") != "read_only_structure_observation"
        or authorization.get("profile_authorization_claimed") is not False
        or authorization.get("source_question_republication_allowed") is not False
    ):
        raise ObservedProfileContractError("project generation authorization boundary mismatch")
    expected = {
        "theme_count": paper["task_card"]["theme_count"]["value"],
        "numbering_mode": paper["task_card"]["numbering_mode"]["value"],
        "total_score": paper["task_card"]["total_score"]["value"],
        "duration_minutes": paper["task_card"]["duration_minutes"]["value"],
    }
    mismatches = []
    bindings = {}
    for name, paper_value in expected.items():
        observed = organization[name]
        value = observed.get("value")
        if value != paper_value:
            mismatches.append(f"{name}: paper={paper_value!r}, profile={value!r}")
        bindings[name] = {
            "value": value,
            "value_status": observed["value_status"],
            "evidence_refs": list(observed["evidence_refs"]),
        }
    if mismatches:
        raise ObservedProfileContractError(
            "paper fixture is incompatible with produced observed profile: "
            + "; ".join(mismatches)
        )
    for name, binding in bindings.items():
        paper["task_card"][name] = dict(binding)
    generation_policy = {
        "profile_usage": "read_only_structure_observation",
        "profile_generation_authority_claimed": False,
        "original_question_generation": True,
        "source_question_republication": False,
        "project_generation_authorization": {
            "path": portable_path(authorization_path),
            "sha256": _sha256(authorization_path),
            "version": authorization["authorization_version"],
            "authorization_id": authorization["authorization_id"],
        },
    }
    paper["task_card"]["generation_policy"] = dict(generation_policy)
    paper["generation_policy"] = dict(generation_policy)
    paper["observed_profile_contract"] = {
        "adapter_version": "observed-profile-adapter/1.0.0",
        "profile_id": bound.data["profile_id"],
        "profile_version": bound.data["profile_version"],
        "profile_path": portable_path(bound.path),
        "profile_sha256": bound.sha256,
        "evidence_manifest": {
            "path": portable_path(bound.evidence_manifest_path),
            "sha256": bound.evidence_manifest_sha256,
        },
        "field_bindings": bindings,
        "profile_gates": {
            "question_retrieval_allowed": bound.data["gates"]["question_retrieval_allowed"],
            "unattended_generation_allowed": bound.data["gates"]["unattended_generation_allowed"],
        },
        "provisional_fixture": bound.fixture,
        "global_template_claim_allowed": False,
        "official_claim_allowed": False,
        "applicability": data_applicability if (data_applicability := bound.data.get("applicability")) else {},
        "limitations": list(bound.data.get("limitations", [])),
        "publication_allowed": False,
    }

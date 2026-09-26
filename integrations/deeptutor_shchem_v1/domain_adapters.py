from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

from .adapters import AdapterUnavailable

GENERATION_ADAPTER_CONTRACT = "shchem.generation_domain_adapter.v2"
GENERATION_DOMAIN_VERSION = "2.0.0"
CONTENT_PROVIDER_VERSION = "shchem-content-provider/2.0.0"
GATEWAY_API_VERSION = "generation-gateway/1.0.0"


class GenerationDomainAdapter:
    """Fail-closed bridge to generation's versioned public surfaces only."""

    def __init__(self) -> None:
        self._workspace = Path(__file__).resolve().parents[2]
        self._version: str | None = None
        self._gateway_api_version: str | None = None
        self._gateway_contract: Any | None = None
        self._build_job: Any | None = None
        self._query_job: Any | None = None
        self._machine_qa_status: Any | None = None
        self._figure_get: Any | None = None
        self._zip_get: Any | None = None
        self._default_profile: Path | None = None
        self._content_provider_version: str | None = None
        self._content_revision: str | None = None
        self._export_content_bundle: Any | None = None
        self._select_plan: Any | None = None
        self._student_content_export: Any | None = None
        self._student_content_status: Any | None = None
        self._candidate_paper_path: Path | None = None
        self._load_error: str | None = None
        try:
            from integrations import shchem_generation_v2 as package
            from integrations.shchem_generation_v2 import content_provider, gateway_api

            self._version = getattr(package, "__version__", None)
            self._gateway_api_version = getattr(gateway_api, "API_VERSION", None)
            self._content_provider_version = getattr(
                content_provider, "PROVIDER_VERSION", None
            )
            self._content_revision = getattr(content_provider, "VERSION_ID", None)
            if self._version != GENERATION_DOMAIN_VERSION:
                raise RuntimeError("generation domain version mismatch")
            if self._gateway_api_version != GATEWAY_API_VERSION:
                raise RuntimeError("generation gateway API version mismatch")
            if self._content_provider_version != CONTENT_PROVIDER_VERSION:
                raise RuntimeError("generation content provider version mismatch")
            self._gateway_contract = getattr(gateway_api, "gateway_contract", None)
            self._build_job = getattr(gateway_api, "build_generation_job", None)
            self._query_job = getattr(gateway_api, "query_generation_job", None)
            self._machine_qa_status = getattr(
                gateway_api, "get_machine_qa_status", None
            )
            self._figure_get = getattr(gateway_api, "get_figure", None)
            self._zip_get = getattr(gateway_api, "get_candidate_zip", None)
            self._default_profile = Path(
                inspect.signature(gateway_api.build_generation_job)
                .parameters["profile_path"]
                .default
            ).resolve()
            self._export_content_bundle = getattr(
                content_provider, "export_content_bundle", None
            )
            self._select_plan = getattr(content_provider, "select_plan", None)
            candidate_paper = getattr(content_provider, "DEFAULT_PAPER", None)
            self._candidate_paper_path = (
                Path(candidate_paper).resolve() if candidate_paper else None
            )
            self._student_content_export = getattr(
                gateway_api, "build_student_learning_content", None
            ) or getattr(content_provider, "build_student_learning_content", None)
            self._student_content_status = getattr(
                gateway_api, "student_learning_content_provider_status", None
            ) or getattr(
                content_provider, "student_learning_content_provider_status", None
            )
            required = (
                self._gateway_contract,
                self._build_job,
                self._query_job,
                self._machine_qa_status,
                self._figure_get,
                self._zip_get,
                self._export_content_bundle,
                self._select_plan,
            )
            if not all(callable(value) for value in required):
                raise RuntimeError("generation public surface is incomplete")
        except (
            ImportError,
            OSError,
            RuntimeError,
            AttributeError,
            TypeError,
            SyntaxError,
        ) as exc:
            self._load_error = f"{type(exc).__name__}:{exc}"

    @staticmethod
    def _unwrap(response: Any, operation: str) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise AdapterUnavailable(
                "generation_contract_mismatch",
                f"generation {operation} returned a non-object",
            )
        if response.get("api_version") != GATEWAY_API_VERSION:
            raise AdapterUnavailable(
                "generation_contract_mismatch",
                f"generation {operation} API version mismatch",
            )
        if response.get("ok") is not True:
            error = response.get("error") if isinstance(response.get("error"), dict) else {}
            raise AdapterUnavailable(
                str(error.get("code") or "generation_operation_blocked").lower(),
                str(error.get("message") or f"generation {operation} failed"),
                error.get("details"),
            )
        data = response.get("data")
        if not isinstance(data, dict):
            raise AdapterUnavailable(
                "generation_contract_mismatch",
                f"generation {operation} omitted data",
            )
        return dict(data)

    def status(self) -> dict[str, Any]:
        contract = None
        if callable(self._gateway_contract):
            try:
                contract = self._gateway_contract()
            except (OSError, RuntimeError, TypeError, ValueError):
                contract = None
        available = callable(self._build_job) and isinstance(contract, dict)
        hierarchy: dict[str, Any] | None = None
        hierarchy_reason: str | None = None
        try:
            hierarchy = self._candidate_subject_hierarchy()
        except AdapterUnavailable as exc:
            hierarchy_reason = exc.code
        return {
            "adapter_contract_version": GENERATION_ADAPTER_CONTRACT,
            "domain_version": self._version,
            "available": available,
            "gateway_api_version": self._gateway_api_version,
            "content_provider_version": self._content_provider_version,
            "capabilities": {
                "candidate_staging": callable(self._build_job),
                "diagnostic_content_dynamic_subject_hierarchy": callable(
                    self._export_content_bundle
                )
                and callable(self._select_plan),
                "content_only_child_governance": True,
                "student_formal_content_export": callable(
                    self._student_content_export
                ),
                "student_formal_content_provider_status": callable(
                    self._student_content_status
                ),
                "external_zip_delivery_attestation": callable(self._zip_get),
                "figure_candidate": callable(self._figure_get),
                "formal_release": False,
            },
            "observed_profile_path": (
                str(self._default_profile) if self._default_profile else None
            ),
            "public_contract": contract,
            "candidate_subject_hierarchy": hierarchy,
            "candidate_subject_hierarchy_reason": hierarchy_reason,
            "reason": self._load_error,
        }

    @staticmethod
    def _sha256_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _candidate_subject_hierarchy(self) -> dict[str, Any]:
        path = self._candidate_paper_path
        if path is None or not path.is_file() or self._workspace not in path.parents:
            raise AdapterUnavailable(
                "generation_candidate_hierarchy_unavailable",
                "generation content provider did not publish a readable candidate paper binding",
            )
        try:
            paper = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterUnavailable(
                "generation_candidate_hierarchy_invalid",
                "generation candidate subject hierarchy is unreadable",
            ) from exc
        printed_ids: list[str] = []
        atomic_ids: list[str] = []
        atomic_to_printed: dict[str, str] = {}
        try:
            for theme in paper["themes"]:
                for printed in theme["printed_questions"]:
                    printed_ids.append(str(printed["printed_question_id"]))
                    for part in printed["atomic_parts"]:
                        atomic_id = str(part["part_id"])
                        atomic_ids.append(atomic_id)
                        atomic_to_printed[atomic_id] = str(
                            printed["printed_question_id"]
                        )
        except (KeyError, TypeError) as exc:
            raise AdapterUnavailable(
                "generation_candidate_hierarchy_invalid",
                "generation candidate subject hierarchy is incomplete",
            ) from exc
        if (
            not printed_ids
            or not atomic_ids
            or len(set(printed_ids)) != len(printed_ids)
            or len(set(atomic_ids)) != len(atomic_ids)
            or set(printed_ids).intersection(atomic_ids)
        ):
            raise AdapterUnavailable(
                "generation_candidate_hierarchy_invalid",
                "generation candidate printed/atomic identifiers are not unique and disjoint",
            )
        if paper.get("version_id") != self._content_revision:
            raise AdapterUnavailable(
                "generation_public_candidate_binding_stale",
                "generation content provider revision and published candidate paper disagree",
            )
        return {
            "contract_version": "shchem.candidate_subject_hierarchy.v1",
            "paper_id": paper.get("paper_id"),
            "version_id": paper.get("version_id"),
            "paper_sha256": self._sha256_path(path),
            "printed_count": len(printed_ids),
            "atomic_count": len(atomic_ids),
            "printed_question_ids": printed_ids,
            "atomic_part_ids": atomic_ids,
            "atomic_to_printed": atomic_to_printed,
            "counts_derived_not_constant": True,
            "human_reviewed": False,
        }

    @staticmethod
    def validate_content_bundle(
        bundle: dict[str, Any], hierarchy: dict[str, Any]
    ) -> dict[str, Any]:
        catalog = bundle.get("catalog") if isinstance(bundle, dict) else None
        expected_atomic = hierarchy.get("atomic_part_ids")
        expected_printed = hierarchy.get("printed_question_ids")
        if not (
            isinstance(catalog, list)
            and catalog
            and isinstance(expected_atomic, list)
            and expected_atomic
            and isinstance(expected_printed, list)
            and expected_printed
            and all(isinstance(value, str) and value for value in expected_atomic)
            and all(isinstance(value, str) and value for value in expected_printed)
            and len(set(expected_atomic)) == len(expected_atomic)
            and len(set(expected_printed)) == len(expected_printed)
            and not set(expected_atomic).intersection(expected_printed)
        ):
            raise AdapterUnavailable(
                "generation_content_catalog_contract_mismatch",
                "candidate hierarchy or content catalog is structurally invalid",
            )
        catalog_atomic = [row.get("atomic_part_id") for row in catalog]
        catalog_printed_all = [row.get("printed_question_id") for row in catalog]
        catalog_printed = list(dict.fromkeys(catalog_printed_all))
        if (
            bundle.get("paper_sha256") != hierarchy.get("paper_sha256")
            or bundle.get("paper_id") != hierarchy.get("paper_id")
            or bundle.get("version_id") != hierarchy.get("version_id")
            or bundle.get("item_count") != len(catalog)
            or catalog_atomic != expected_atomic
            or len(set(catalog_atomic)) != len(catalog_atomic)
            or catalog_printed != expected_printed
            or any(value in set(expected_printed) for value in catalog_atomic)
        ):
            raise AdapterUnavailable(
                "generation_content_candidate_set_mismatch",
                "content catalog does not exactly match the current candidate atomic hierarchy",
            )
        exact_registered_ids = [
            row.get("atomic_part_id")
            for row in catalog
            if isinstance(row.get("governance"), dict)
            and row["governance"].get("state")
            == "automated_verified_candidate"
            and row["governance"].get("central_registry_exact_member") is True
            and row["governance"].get("human_reviewed") is False
        ]
        if exact_registered_ids != expected_atomic:
            raise AdapterUnavailable(
                "generation_content_registry_exact_set_mismatch",
                "registered child set does not exactly match current candidate atomic IDs",
            )
        return {
            "contract_version": "shchem.dynamic_content_governance.v1",
            "printed_count": len(expected_printed),
            "atomic_count": len(expected_atomic),
            "exact_registered_child_count": len(exact_registered_ids),
            "printed_question_ids": list(expected_printed),
            "atomic_part_ids": list(expected_atomic),
            "exact_registered_child_ids": exact_registered_ids,
            "registry_exact_matches_current_candidate": True,
            "counts_derived_not_constant": True,
            "human_reviewed": False,
        }

    def observed_profile_path(self) -> Path | None:
        return self._default_profile

    def build_candidate(
        self, profile_path: Path | None = None, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not callable(self._build_job):
            raise AdapterUnavailable(
                "generation_domain_unavailable",
                "generation public candidate staging is unavailable",
                self.status(),
            )
        public_request = {
            "intent": "demo_weekpack",
            **(request or {}),
        }
        public_request.pop("profile_path", None)
        public_request.pop("governance_chain_path", None)
        try:
            response = self._build_job(
                public_request,
                profile_path=profile_path or self._default_profile,
                governance_chain=None,
            )
        except Exception as exc:
            raise AdapterUnavailable(
                "generation_candidate_stage_failed",
                f"generation public staging failed: {type(exc).__name__}",
            ) from exc
        record = self._unwrap(response, "generation_job.build")
        if record.get("status") != "staging_candidate_created_awaiting_controller_native_chain":
            raise AdapterUnavailable(
                "generation_contract_mismatch",
                "generation job did not enter the documented staging state",
            )
        return {
            "status": "candidate_staged",
            "adapter_contract_version": GENERATION_ADAPTER_CONTRACT,
            "domain_version": self._version,
            "generation_job": record,
            "content_qa": {
                "status": "pending",
                "blockers": list(record.get("next_required", [])),
            },
            "delivery_qa": {
                "status": "blocked",
                "blockers": ["external_final_zip_attestation_required"],
            },
            "machine_only": True,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        }

    def diagnostic_content_selection(
        self,
        *,
        output_path: Path,
        strategy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not callable(self._export_content_bundle) or not callable(self._select_plan):
            raise AdapterUnavailable(
                "generation_content_provider_unavailable",
                "generation diagnostic content provider is unavailable",
            )
        hierarchy = self._candidate_subject_hierarchy()
        try:
            bundle = self._export_content_bundle(output_path=output_path)
        except Exception as exc:
            raise AdapterUnavailable(
                "generation_content_catalog_blocked",
                f"generation dynamic content catalog failed closed: {type(exc).__name__}",
            ) from exc
        if not (
            bundle.get("provider_version") == CONTENT_PROVIDER_VERSION
            and bundle.get("content_status") == "automated_verified_content"
            and bundle.get("human_reviewed") is False
        ):
            raise AdapterUnavailable(
                "generation_content_catalog_contract_mismatch",
                "generation content catalog claim boundary is invalid",
            )
        governed = self.validate_content_bundle(bundle, hierarchy)
        catalog = bundle["catalog"]
        child_chain = [
            {
                "atomic_part_id": item.get("atomic_part_id"),
                "printed_question_id": item.get("printed_question_id"),
                "theme_id": item.get("theme_id"),
                "governance": item.get("governance"),
            }
            for item in catalog
        ]
        selection = self._select_plan(bundle, strategy or {})
        return {
            "provider_version": CONTENT_PROVIDER_VERSION,
            "subject_hierarchy": governed,
            "catalog_item_count": governed["atomic_count"],
            "eligible_atomic_part_ids": governed["atomic_part_ids"],
            "diagnostic_selection": selection,
            "content_only_child_chain": child_chain,
            "content_status": "automated_verified_content",
            "teacher_managed_delivery_claimed": False,
            "human_reviewed": False,
        }

    def prepare_candidate(
        self,
        request: dict[str, Any],
        profile_path: Path | None = None,
        content_output_path: Path | None = None,
    ) -> dict[str, Any]:
        candidate = self.build_candidate(profile_path, request)
        blockers = list(candidate["generation_job"].get("next_required", []))
        content_selection: dict[str, Any] | None = None
        if content_output_path is not None:
            try:
                content_selection = self.diagnostic_content_selection(
                    output_path=content_output_path,
                    strategy=request.get("content_strategy")
                    if isinstance(request.get("content_strategy"), dict)
                    else None,
                )
            except AdapterUnavailable as exc:
                blockers.append(exc.code)
        return {
            "status": "blocked",
            "candidate": candidate,
            "diagnostic_content": content_selection,
            "post_preflight_inputs": None,
            "blockers": sorted(set(blockers + ["external_final_zip_attestation_required"])),
            "sequence": [
                "generation_public_job_staged",
                "content_only_child_chains_registered",
                "controller_post_generation_preflight",
                "external_zip_delivery_attestation",
            ],
            "machine_only": True,
            "human_reviewed": False,
        }

    def render_student_week_content(
        self, *, diagnosis: dict[str, Any], plan: dict[str, Any]
    ) -> dict[str, Any]:
        if not callable(self._student_content_export):
            raise AdapterUnavailable(
                "generation_student_content_export_unavailable",
                "generation does not expose student_learning_generation_content_provider_v1",
            )
        try:
            content = self._student_content_export(diagnosis=diagnosis, plan=plan)
        except Exception as exc:
            raise AdapterUnavailable(
                "generation_student_content_export_failed",
                f"generation student content export failed: {type(exc).__name__}",
            ) from exc
        if not isinstance(content, dict) or content.get("contract_version") != (
            "student_learning_generation_content_provider_v1"
        ):
            raise AdapterUnavailable(
                "generation_student_content_export_contract_mismatch",
                "generation student content export contract mismatch",
            )
        return content

    def student_formal_content_status(self) -> dict[str, Any]:
        if not callable(self._student_content_status):
            return {
                "contract_version": "student_learning_generation_content_provider_v1",
                "ready": False,
                "production_ready": False,
                "fixture_only": False,
                "required_document_count": 8,
                "root_external_reconciliation_ready": False,
                "blockers": [
                    "generation_student_content_provider_status_unavailable"
                ],
            }
        try:
            status = self._student_content_status()
        except Exception as exc:
            return {
                "contract_version": "student_learning_generation_content_provider_v1",
                "ready": False,
                "production_ready": False,
                "fixture_only": False,
                "required_document_count": 8,
                "root_external_reconciliation_ready": False,
                "blockers": [
                    f"generation_student_content_provider_status_failed:{type(exc).__name__}"
                ],
            }
        if not isinstance(status, dict):
            status = {}
        ready = (
            callable(self._student_content_export)
            and status.get("contract_version")
            == "student_learning_generation_content_provider_v1"
            and status.get("ready") is True
            and status.get("production_ready") is True
            and status.get("fixture_only") is False
            and status.get("required_document_count") == 8
            and status.get("root_external_reconciliation_ready") is True
        )
        blockers = list(status.get("blockers", []))
        if not ready and not blockers:
            blockers.append("generation_student_content_provider_not_ready")
        return {
            **status,
            "contract_version": "student_learning_generation_content_provider_v1",
            "ready": ready,
            "blockers": sorted(set(str(value) for value in blockers)),
        }

    def figure_get(self, figure_id: str) -> dict[str, Any]:
        if not callable(self._figure_get):
            raise AdapterUnavailable(
                "generation_domain_unavailable",
                "generation public figure interface is unavailable",
            )
        try:
            response = self._figure_get(
                figure_id,
                format_name="svg",
                download=False,
                profile_path=self._default_profile,
                governance_chain=None,
            )
        except Exception as exc:
            raise AdapterUnavailable(
                "generation_figure_failed",
                f"generation figure lookup failed: {type(exc).__name__}",
            ) from exc
        data = self._unwrap(response, "figure.get")
        return {
            **data,
            "status": "candidate",
            "adapter_contract_version": GENERATION_ADAPTER_CONTRACT,
            "domain_version": self._version,
            "media_type": "image/svg+xml",
            "machine_only": True,
            "human_reviewed": False,
            "publication_allowed": False,
        }

    @staticmethod
    def validate_external_delivery_attestation(data: dict[str, Any]) -> dict[str, Any]:
        attestation = data.get("external_delivery_attestation")
        blockers: list[str] = []
        hierarchy = data.get("subject_hierarchy")
        expected_atomic = (
            hierarchy.get("atomic_part_ids")
            if isinstance(hierarchy, dict)
            else None
        )
        expected_printed = (
            hierarchy.get("printed_question_ids")
            if isinstance(hierarchy, dict)
            else None
        )
        if not (
            isinstance(expected_atomic, list)
            and expected_atomic
            and len(set(expected_atomic)) == len(expected_atomic)
            and isinstance(expected_printed, list)
            and expected_printed
            and len(set(expected_printed)) == len(expected_printed)
            and not set(expected_atomic).intersection(expected_printed)
        ):
            blockers.append("current_candidate_subject_hierarchy_required")
        if not isinstance(attestation, dict):
            blockers.append("external_final_zip_attestation_required")
        else:
            required = {
                "record_type": "external_teacher_managed_delivery_attestation",
                "teacher_managed_delivery_candidate": True,
                "delivery_scope": "teacher_managed_private_delivery",
                "attestation_included_in_bound_archive": False,
                "human_reviewed": False,
                "external_publication_allowed": False,
                "publication_allowed": False,
                "release_allowed": False,
            }
            for key, expected in required.items():
                if attestation.get(key) != expected:
                    blockers.append(f"external_attestation_boundary_mismatch:{key}")
            if attestation.get("archive_sha256") != data.get("sha256"):
                blockers.append("external_attestation_archive_hash_mismatch")
            content_governance = attestation.get("content_governance")
            registry_binding = (
                content_governance.get("registry_binding")
                if isinstance(content_governance, dict)
                else None
            )
            atomic_bindings = (
                registry_binding.get("atomic")
                if isinstance(registry_binding, dict)
                else None
            )
            registered_ids = (
                [
                    row.get("artifact_id")
                    for row in atomic_bindings
                    if isinstance(row, dict)
                    and row.get("exact_registry_member") is True
                ]
                if isinstance(atomic_bindings, list)
                else []
            )
            if not (
                isinstance(expected_atomic, list)
                and registered_ids == expected_atomic
                and registry_binding.get("atomic_count") == len(expected_atomic)
                and registry_binding.get("all_exact_members") is True
            ):
                blockers.append(
                    "delivery_registry_exact_set_must_match_current_candidate_atomic_ids"
                )
        return {
            "allowed": not blockers,
            "status": "ready" if not blockers else "blocked",
            "scope": "teacher_managed_delivery_candidate",
            "candidate_sha256": data.get("sha256") if not blockers else None,
            "attestation": attestation if not blockers else None,
            "printed_count": (
                len(expected_printed) if isinstance(expected_printed, list) else None
            ),
            "atomic_count": (
                len(expected_atomic) if isinstance(expected_atomic, list) else None
            ),
            "blockers": blockers,
            "external_publication_allowed": False,
            "official_claim_allowed": False,
            "human_reviewed": False,
        }

    def publication_candidate_status(self) -> dict[str, Any]:
        return {
            "adapter_contract_version": GENERATION_ADAPTER_CONTRACT,
            "domain_version": self._version,
            "ready": False,
            "status": "blocked",
            "publication_allowed": False,
            "human_reviewed": False,
            "blockers": ["external_publication_interface_intentionally_closed"],
        }


class GenerationFormalContentProvider:
    """Student-domain ContentProvider backed only by generation's public export."""

    def __init__(self, generation: GenerationDomainAdapter) -> None:
        self.generation = generation

    def status(self) -> dict[str, Any]:
        generation_status = self.generation.status()
        status = self.generation.student_formal_content_status()
        blockers = list(status.get("blockers", []))
        if generation_status.get("available") is not True:
            blockers.append("generation_domain_unavailable")
        ready = status.get("ready") is True and not blockers
        return {
            **status,
            "provider_id": "integrations.shchem_generation_v2.public_export",
            "provider_mode": "production",
            "ready": ready,
            "production_ready": ready,
            "fixture_only": False,
            "required_document_count": 8,
            "generation_domain_version": generation_status.get("domain_version"),
            "blockers": sorted(set(blockers)),
        }

    def render(
        self, *, diagnosis: dict[str, Any], plan: dict[str, Any]
    ) -> dict[str, Any]:
        return self.generation.render_student_week_content(
            diagnosis=diagnosis, plan=plan
        )

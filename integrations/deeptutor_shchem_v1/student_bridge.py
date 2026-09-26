from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any


class StudentBridgeError(RuntimeError):
    pass


class StudentDomainBridge:
    """Call-only bridge to the student domain's frozen Gateway API v1."""

    def __init__(
        self,
        data_root: Path | None,
        capabilities: dict[str, str],
        controller_root: Path | None = None,
        formal_content_provider: Any | None = None,
    ):
        self.data_root = data_root.resolve() if data_root else None
        self.controller_root = controller_root.resolve() if controller_root else None
        self.capabilities = dict(capabilities)
        self._formal_content_provider = formal_content_provider
        self._api: Any | None = None
        self._store: Any | None = None
        self._scope_type: Any | None = None
        self._api_version: str | None = None
        self._domain_version: str | None = None
        self._load_error: str | None = None
        if self.data_root and self.capabilities:
            try:
                from integrations.student_learning_v1.domain import (
                    DOMAIN_VERSION,
                    StudentScope,
                    StudentStore,
                )
                from integrations.student_learning_v1.gateway_api import (
                    API_VERSION,
                    GatewayStudentLearningAPI,
                )

                self._api = GatewayStudentLearningAPI(
                    self.data_root,
                    controller_root=self.controller_root,
                    formal_content_provider=formal_content_provider,
                )
                self._store = StudentStore(self.data_root)
                self._scope_type = StudentScope
                self._api_version = API_VERSION
                self._domain_version = DOMAIN_VERSION
            except (
                ImportError,
                OSError,
                ValueError,
                TypeError,
                SyntaxError,
            ) as exc:
                self._load_error = f"{type(exc).__name__}:{exc}"

    def status(self) -> dict[str, Any]:
        available = self._api is not None
        execute = getattr(self._api, "execute_real_diagnosis", None)
        execute_parameters = (
            inspect.signature(execute).parameters if callable(execute) else {}
        )
        deprecated_binding_required = bool(
            "controller_issuance_binding" in execute_parameters
            and execute_parameters["controller_issuance_binding"].default
            is inspect.Parameter.empty
        )
        real_two_stage = bool(
            available
            and callable(getattr(self._api, "prepare_real_diagnosis", None))
            and callable(
                getattr(self._api, "issue_real_diagnosis_receipt", None)
            )
            and callable(execute)
            and not deprecated_binding_required
        )
        bundle_builder = getattr(self._api, "build_week_bundle", None)
        bundle_parameters = (
            inspect.signature(bundle_builder).parameters
            if callable(bundle_builder)
            else {}
        )
        real_week_bundle = bool(
            available
            and callable(bundle_builder)
            and "prepare_id" in bundle_parameters
        )
        provider_status_method = getattr(
            self._api, "formal_content_provider_status", None
        )
        provider_status = (
            provider_status_method()
            if callable(provider_status_method)
            else {
                "contract_version": "student_learning_generation_content_provider_v1",
                "ready": False,
                "blockers": ["generation_provider_status_contract_required"],
            }
        )
        real_week_bundle = real_week_bundle and provider_status.get("ready") is True
        return {
            "configured": self.data_root is not None and bool(self.capabilities),
            "available": available,
            "api_version": self._api_version,
            "domain_version": self._domain_version,
            "contract_version": "shchem.student-image-local-hold.v1",
            "capabilities": {
                "private_upload": available,
                "diagnosis": available,
                "weekly_bundle_manifest": available,
                "formal_generation_provider": provider_status.get("ready") is True,
                "real_diagnosis": real_two_stage,
                "real_week_bundle": real_week_bundle,
            },
            "formal_content_provider": provider_status,
            "real_diagnosis_orchestration": (
                "student_prepare_then_student_live_controller_issue_then_student_execute_with_internal_revalidation"
                if real_two_stage
                else "unavailable"
            ),
            "reason": self._load_error,
            "deprecated_controller_issuance_binding_required": (
                deprecated_binding_required
            ),
        }

    def learning_view(self, *, profile_id: str) -> dict[str, Any]:
        if self._api is None:
            raise StudentBridgeError("student learning-view interface is unavailable")
        try:
            view = self._api.select_student(
                profile_id=profile_id,
                capability=self._capability(profile_id),
            )
        except Exception as exc:
            raise StudentBridgeError(
                f"student learning-view failed: {type(exc).__name__}:{exc}"
            ) from exc
        history = view.get("learning_history")
        if not isinstance(history, dict):
            raise StudentBridgeError("student learning history contract mismatch")
        if history.get("profile_id") != profile_id:
            raise StudentBridgeError("student learning history profile mismatch")
        if history.get("human_reviewed") is not False:
            raise StudentBridgeError("student learning history review overclaim")
        return {
            "profile_id": profile_id,
            "metadata": view.get("metadata", {}),
            "input_event_count": view.get("input_event_count", 0),
            "timeline_event_count": view.get("timeline_event_count", 0),
            "mastery_history": history.get("mastery_history", []),
            "focus_history": history.get("focus_history", []),
            "spaced_review_due": history.get("spaced_review_due", []),
            "latest_week_over_week_delta": history.get(
                "latest_week_over_week_delta"
            ),
            "next_week_objectives": history.get("next_week_objectives", []),
            "history_head_sha256": history.get("history_head_sha256"),
            "long_term_teaching_effectiveness_verified": False,
            "machine_only": True,
            "human_reviewed": False,
        }

    def _capability(self, profile_id: str) -> str:
        capability = self.capabilities.get(profile_id)
        if not capability:
            raise StudentBridgeError("student capability is unavailable")
        return capability

    def _scope(self, profile_id: str) -> Any:
        if self._store is None or self._scope_type is None:
            raise StudentBridgeError("student private-domain bridge is unavailable")
        return self._scope_type(
            profile_id=profile_id, capability=self._capability(profile_id)
        )

    def import_image(
        self,
        *,
        profile_id: str,
        image_bytes: bytes,
        mime_type: str,
        source_ref: str,
        input_deidentified: bool | None,
        input_contains_face: bool | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        del input_deidentified, input_contains_face
        if self._api is None:
            raise StudentBridgeError("student Gateway API is unavailable")
        try:
            result = self._api.save_raw_error_image(
                profile_id=profile_id,
                capability=self._capability(profile_id),
                image_bytes=image_bytes,
                media_type=mime_type,
                source_ref=source_ref,
            )
            record = result["media"]
            receipt = result["sanitization_receipt"]
        except Exception as exc:
            raise StudentBridgeError(
                f"student image import failed: {type(exc).__name__}"
            ) from exc
        if receipt.get("contract_version") != "shchem.student-image-local-hold.v1":
            raise StudentBridgeError("student image local-hold contract version mismatch")
        if receipt.get("raw_model_access_allowed") is not False:
            raise StudentBridgeError(
                "student image receipt permits raw model access"
            )
        if receipt.get("ocr_invoked") is not False:
            raise StudentBridgeError("student image receipt does not prove zero recognition")
        return record, receipt

    def read_sanitized_for_model(self, profile_id: str, media_id: str) -> bytes:
        scope = self._scope(profile_id)
        try:
            return self._store.read_sanitized_image_for_model(scope, media_id)
        except Exception as exc:
            raise StudentBridgeError(
                f"sanitized image access rejected: {type(exc).__name__}"
            ) from exc

    def append_structured_input(
        self, *, profile_id: str, kind: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self._api is None:
            raise StudentBridgeError("student Gateway API is unavailable")
        event_type = {
            "grades": "score",
            "progress": "grade_progress",
            "attempt": "attempt",
            "timing": "timing",
            "other_local_input": "declared_weakness",
        }.get(kind)
        if event_type is None:
            raise StudentBridgeError("student input kind has no domain mapping")
        try:
            return self._api.record_input(
                profile_id=profile_id,
                capability=self._capability(profile_id),
                input_type=event_type,
                payload=payload,
            )
        except Exception as exc:
            raise StudentBridgeError(
                f"student structured input rejected: {type(exc).__name__}"
            ) from exc

    def diagnose(
        self,
        *,
        profile_id: str,
        synthetic: bool,
        controller_receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._api is None:
            raise StudentBridgeError("student diagnosis interface is unavailable")
        if not synthetic:
            raise StudentBridgeError(
                "real diagnosis requires the versioned prepare/controller/execute path"
            )
        try:
            job = self._api.run_diagnosis(
                profile_id=profile_id,
                capability=self._capability(profile_id),
                mode="synthetic",
                controller_receipt=controller_receipt,
            )
            diagnosis = job["result"]["diagnosis"]
        except Exception as exc:
            raise StudentBridgeError(
                f"student diagnosis failed: {type(exc).__name__}:{exc}"
            ) from exc
        if diagnosis.get("schema_version") != "student_diagnosis_v2":
            raise StudentBridgeError("student diagnosis schema version mismatch")
        expected_scope = (
            "synthetic_fixture_only"
            if synthetic
            else "machine_only_real_student_candidate"
        )
        if diagnosis.get("claim_scope") != expected_scope:
            raise StudentBridgeError("student diagnosis claim scope mismatch")
        if diagnosis.get("human_reviewed") is not False:
            raise StudentBridgeError("student diagnosis human-review overclaim")
        if not synthetic and diagnosis.get("teaching_effectiveness_unverified") is not True:
            raise StudentBridgeError("real diagnosis effectiveness boundary missing")
        diagnosis = dict(diagnosis)
        diagnosis["student_domain_job_id"] = job.get("job_id")
        diagnosis["student_domain_api_version"] = self._api_version
        return diagnosis

    def prepare_real_diagnosis(self, *, profile_id: str) -> dict[str, Any]:
        if self._api is None or not callable(
            getattr(self._api, "prepare_real_diagnosis", None)
        ):
            raise StudentBridgeError(
                "student real-diagnosis preparation interface is unavailable"
            )
        try:
            job = self._api.prepare_real_diagnosis(
                profile_id=profile_id,
                capability=self._capability(profile_id),
            )
            preparation = job["result"]["preparation"]
        except Exception as exc:
            raise StudentBridgeError(
                f"student real-diagnosis preparation failed: {type(exc).__name__}:{exc}"
            ) from exc
        if (
            preparation.get("contract_version")
            != "student_learning_real_diagnosis_prepare_v1"
            or preparation.get("profile_id") != profile_id
            or preparation.get("state")
            != "awaiting_controller_receipt_issuance"
            or preparation.get("human_reviewed") is not False
            or not isinstance(preparation.get("controller_input_without_receipt"), dict)
        ):
            raise StudentBridgeError(
                "student real-diagnosis preparation contract mismatch"
            )
        return dict(preparation)

    def execute_real_diagnosis(
        self,
        *,
        profile_id: str,
        prepare_id: str,
    ) -> dict[str, Any]:
        if self._api is None or not callable(
            getattr(self._api, "execute_real_diagnosis", None)
        ):
            raise StudentBridgeError(
                "student real-diagnosis execution interface is unavailable"
            )
        execute = self._api.execute_real_diagnosis
        parameters = inspect.signature(execute).parameters
        forbidden_caller_parameters = {
            "controller_receipt",
            "controller_issuance_binding",
        }
        required_forbidden = {
            name
            for name in forbidden_caller_parameters
            if name in parameters
            and parameters[name].default is inspect.Parameter.empty
        }
        if required_forbidden:
            raise StudentBridgeError(
                "student execute API still requires caller-supplied controller material"
            )
        try:
            job = execute(
                profile_id=profile_id,
                capability=self._capability(profile_id),
                prepare_id=prepare_id,
            )
            diagnosis = job["result"]["diagnosis"]
        except Exception as exc:
            raise StudentBridgeError(
                f"student real-diagnosis execution failed: {type(exc).__name__}:{exc}"
            ) from exc
        if diagnosis.get("schema_version") != "student_diagnosis_v2":
            raise StudentBridgeError("student diagnosis schema version mismatch")
        if diagnosis.get("claim_scope") != "machine_only_real_student_candidate":
            raise StudentBridgeError("student diagnosis claim scope mismatch")
        if diagnosis.get("human_reviewed") is not False:
            raise StudentBridgeError("student diagnosis human-review overclaim")
        if diagnosis.get("teaching_effectiveness_unverified") is not True:
            raise StudentBridgeError("real diagnosis effectiveness boundary missing")
        preflight = diagnosis.get("controller_diagnose_preflight")
        if not (
            isinstance(preflight, dict)
            and preflight.get("ready") is True
            and preflight.get("receipt_live_revalidated") is True
            and preflight.get("human_reviewed") is False
        ):
            raise StudentBridgeError(
                "student diagnosis lacks live controller receipt verification"
            )
        diagnosis = dict(diagnosis)
        diagnosis["student_domain_job_id"] = job.get("job_id")
        diagnosis["student_domain_api_version"] = self._api_version
        return diagnosis

    def issue_real_diagnosis_receipt(
        self, *, profile_id: str, prepare_id: str
    ) -> dict[str, Any]:
        issue = getattr(self._api, "issue_real_diagnosis_receipt", None)
        if self._api is None or not callable(issue):
            raise StudentBridgeError(
                "student controller-receipt issue interface is unavailable"
            )
        try:
            job = issue(
                profile_id=profile_id,
                capability=self._capability(profile_id),
                prepare_id=prepare_id,
            )
            result = job["result"]
            receipt = result["controller_receipt"]
            issuance = result["issuance"]
        except Exception as exc:
            raise StudentBridgeError(
                f"student controller-receipt issue failed: {type(exc).__name__}:{exc}"
            ) from exc
        if not (
            receipt.get("contract_version") == "central_diagnosis_adapter_v2"
            and receipt.get("allowed") is True
            and receipt.get("live_central_state_used") is True
            and receipt.get("human_reviewed") is False
            and issuance.get("contract_version")
            == "student_learning_controller_issue_cache_v1"
            and issuance.get("prepare_id") == prepare_id
            and issuance.get("human_reviewed") is False
        ):
            raise StudentBridgeError(
                "student controller-receipt issue contract mismatch"
            )
        return {
            "student_domain_job_id": job.get("job_id"),
            "student_domain_api_version": self._api_version,
            "prepare_id": prepare_id,
            "receipt_contract_version": receipt.get("contract_version"),
            "receipt_sha256": receipt.get("receipt_sha256"),
            "controller_contract_version": receipt.get(
                "controller_contract_version"
            ),
            "controller_version": receipt.get("controller_version"),
            "preflight_output_sha256": issuance.get(
                "preflight_output_sha256"
            ),
            "validated_checks": list(issuance.get("validated_checks", [])),
            "live_controller_receipt_issued": True,
            "caller_supplied_receipt_used": False,
            "machine_only": True,
            "human_reviewed": False,
        }

    def build_week_bundle(
        self,
        *,
        profile_id: str,
        synthetic: bool,
        week_number: int,
        controller_receipt: dict[str, Any] | None = None,
        prepare_id: str | None = None,
    ) -> tuple[dict[str, Any], Path]:
        if self._api is None or self.data_root is None:
            raise StudentBridgeError("student weekly-bundle interface is unavailable")
        if not synthetic and not prepare_id:
            raise StudentBridgeError(
                "student real weekly bundle requires an issued prepare_id"
            )
        build = self._api.build_week_bundle
        build_parameters = inspect.signature(build).parameters
        arguments: dict[str, Any] = {
            "profile_id": profile_id,
            "capability": self._capability(profile_id),
            "week_number": week_number,
            "mode": "synthetic" if synthetic else "real",
        }
        if "controller_receipt" in build_parameters:
            arguments["controller_receipt"] = controller_receipt
        if "prepare_id" in build_parameters:
            arguments["prepare_id"] = prepare_id
        try:
            job = build(**arguments)
            result = job["result"]
            manifest = result["manifest"]
            verification = result["verification"]
            bundle_path = Path(str(result["bundle_path"])).resolve()
        except Exception as exc:
            raise StudentBridgeError(
                f"student weekly bundle failed: {type(exc).__name__}:{exc}"
            ) from exc
        if manifest.get("schema_version") != "student_week_zip_manifest_v1":
            raise StudentBridgeError("student weekly manifest version mismatch")
        expected_verification = (
            "PASS_STRUCTURE_FIXTURE_ONLY_NOT_CONTENT_COMPLETE"
            if synthetic
            else "PASS_MACHINE_ONLY_FORMAL_CONTENT_CANDIDATE"
        )
        if verification.get("status") != expected_verification:
            raise StudentBridgeError("student weekly bundle verification failed")
        profile_root = (
            self.data_root / "private_profiles" / profile_id[:2] / profile_id
        ).resolve()
        if profile_root not in bundle_path.parents or not bundle_path.is_file():
            raise StudentBridgeError("student weekly bundle path rejected")
        return (
            {
                "manifest": manifest,
                "verification": verification,
                "learning_history": result.get("learning_history", {}),
                "student_domain_job_id": job.get("job_id"),
                "student_domain_api_version": self._api_version,
            },
            bundle_path,
        )

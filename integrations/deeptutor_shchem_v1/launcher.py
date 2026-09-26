from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from .adapters import AdapterUnavailable, ControllerRpcAdapter
from .config import (
    CONTRACT_VERSION,
    AppConfig,
    Principal,
    ServingReleaseBinding,
    WorkbenchReleaseRuntime,
    _is_loopback,
    token_digest,
)
from .http_app import GatewayHTTPServer, create_server
from .intake_imports import default_intake_import_root
from .workbench_product_registry import (
    REGISTRY_FILE_SHA256,
    REGISTRY_ID,
    REGISTRY_SCHEMA_SHA256,
    REGISTRY_SCHEMA_VERSION,
    WorkbenchProductRegistryReader,
)

LAUNCHER_SCHEMA = "shchem.webui.launcher.v1"
SESSION_SCHEMA = "shchem.webui.session.v1"
EXPECTED_CONTROLLER_CONTRACT = "2.0.0"
MIN_TOKEN_BYTES = 32
MAX_TOKEN_BYTES = 512
TOKEN_ENV = "SHCHEM_WEBUI_SESSION_TOKEN"
TOKEN_FILE = "session.token"
SESSION_FILE = "session.json"
STOP_FILE = "stop.json"
FAILURE_FILE = "startup-failure.json"
CHILD_OUTPUT_FILE = "child-runtime.log"
STARTUP_INTENT_FILE = "startup-intent.json"
LIFECYCLE_LOCK_FILE = ".launcher-lifecycle.v1.lock"
EXPECTED_TABS = (
    "home",
    "feature-roadmap",
    "candidate-review",
    "overview",
    "generation",
    "retrieval",
    "kb",
    "taxonomy",
    "students",
    "artifacts",
)
EXPECTED_PRIMARY_TABS = (
    "home",
    "library",
    "students",
    "prep",
)
EXPECTED_SECONDARY_TABS = (
    "materials",
    "curriculum",
    "analytics",
    "presentations",
    "hotspots",
    "templates",
    "review",
    "ai",
    "settings",
)
EXPECTED_PRESERVED_HASH_ROUTES = (
    "home",
    "materials",
    "curriculum",
    "library",
    "students",
    "analytics",
    "prep",
    "presentations",
    "hotspots",
    "review",
    "templates",
    "ai",
    "settings",
)
EXPECTED_CAPABILITIES = (
    "kb_retrieval_read",
    "tag_patch_candidate_write",
    "model_provider_settings_write",
    "model_provider_synthetic_probe_execute",
    "intake_import_write",
    "intake_visual_execute",
    "workbench_release_prepare_write",
    "workbench_release_activate_write",
    "review_task_write",
    "review_candidate_write",
    "review_decision_write",
)
LEGACY_CAPABILITIES = (
    "kb_retrieval_read",
    "tag_patch_candidate_write",
    "model_provider_settings_write",
    "model_provider_synthetic_probe_execute",
    "intake_import_write",
    "intake_visual_execute",
    "review_task_write",
    "review_candidate_write",
    "review_decision_write",
)
RELEASE_STORE_POLICY = "current_user_localappdata_external_v1"
RELEASE_EFFECT_POLICY = "next_clean_restart_only"
_LIFECYCLE_THREAD_LOCK = threading.Lock()


class LauncherError(RuntimeError):
    """A fail-closed launcher error whose message never contains credentials."""


class _LauncherGatewayHTTPServer(GatewayHTTPServer):
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,
                1,
            )
        super().server_bind()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    return _workspace_root() / "runtime/deeptutor_shchem/webui.launcher.json"


def default_session_dir() -> Path:
    base = Path(
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_RUNTIME_DIR")
        or tempfile.gettempdir()
    )
    return base / "ShanghaiChem" / "WebUI"


def default_release_root() -> Path:
    base = Path(
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_DATA_HOME")
        or tempfile.gettempdir()
    )
    return base / "ShanghaiChem" / "WorkbenchReleases" / "v1"


def default_review_workbench_root() -> Path:
    """Project-external local state for teacher review workflow records."""

    base = Path(
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_DATA_HOME")
        or tempfile.gettempdir()
    )
    return base / "ShanghaiChem" / "ReviewWorkbench" / "v1"


def _strict_object(
    value: Any, *, name: str, required: set[str], optional: set[str] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LauncherError(f"{name} must be a JSON object")
    optional = optional or set()
    keys = set(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing:
        raise LauncherError(f"{name} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise LauncherError(f"{name} contains unsupported fields: {', '.join(unknown)}")
    return value


@dataclass(frozen=True)
class StudentProfile:
    profile_id: str
    capability_env: str


@dataclass(frozen=True)
class LauncherSettings:
    config_path: Path
    bind_host: str
    port: int
    controller_timeout_seconds: float
    startup_timeout_seconds: float
    health_timeout_seconds: float
    principal_id: str
    capabilities: tuple[str, ...]
    students: tuple[StudentProfile, ...]
    auto_connect: bool = False
    release_enabled: bool = False
    release_store_policy: str = RELEASE_STORE_POLICY
    release_effect_policy: str = RELEASE_EFFECT_POLICY

    @property
    def workspace_root(self) -> Path:
        return _workspace_root()

    @property
    def shchem_root(self) -> Path:
        return self.workspace_root / "sh-chem-db"

    @property
    def overlay_root(self) -> Path:
        return self.workspace_root / "runtime/deeptutor_shchem/overlay"

    @property
    def controller_script(self) -> Path:
        return self.shchem_root / "scripts/sh_chem_agent.py"


def load_settings(config_path: str | Path | None = None) -> LauncherSettings:
    path = Path(config_path or default_config_path()).resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LauncherError("launcher configuration is missing or invalid JSON") from exc
    root = _strict_object(
        raw,
        name="launcher configuration",
        required={
            "schema_version",
            "mode",
            "network",
            "controller",
            "ccswitch",
            "auth",
            "student_learning",
        },
        optional={"release_control"},
    )
    if root["schema_version"] != LAUNCHER_SCHEMA:
        raise LauncherError("launcher configuration schema_version drift detected")
    if root["mode"] != "controller":
        raise LauncherError("launcher only permits controller mode")

    network = _strict_object(
        root["network"],
        name="network",
        required={
            "bind_host",
            "port",
            "startup_timeout_seconds",
            "health_timeout_seconds",
        },
    )
    host = network["bind_host"]
    port = network["port"]
    if not isinstance(host, str) or host != "127.0.0.1":
        raise LauncherError("bind_host must be the explicit loopback address 127.0.0.1")
    if not _is_loopback(host):
        raise LauncherError("bind_host must be a loopback address")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise LauncherError("port must be an integer between 1 and 65535")

    controller = _strict_object(
        root["controller"],
        name="controller",
        required={"contract_version", "timeout_seconds"},
    )
    if controller["contract_version"] != EXPECTED_CONTROLLER_CONTRACT:
        raise LauncherError("controller contract drift detected")

    ccswitch = _strict_object(
        root["ccswitch"], name="ccswitch", required={"enabled"}
    )
    if ccswitch["enabled"] is not False:
        raise LauncherError("CCSwitch must remain disabled for the local WebUI launcher")

    auth = _strict_object(
        root["auth"],
        name="auth",
        required={"principal_id", "role", "capabilities"},
        optional={"auto_connect"},
    )
    if auth["role"] != "teacher":
        raise LauncherError("launcher principal role must remain teacher")
    principal_id = auth["principal_id"]
    if not isinstance(principal_id, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_.-]{2,63}", principal_id
    ):
        raise LauncherError("principal_id is invalid")
    capabilities_value = auth["capabilities"]
    if not isinstance(capabilities_value, list) or any(
        not isinstance(item, str) for item in capabilities_value
    ):
        raise LauncherError("auth.capabilities must be a string array")
    capabilities = tuple(capabilities_value)
    auto_connect = auth.get("auto_connect", False)
    if not isinstance(auto_connect, bool):
        raise LauncherError("auth.auto_connect must be boolean")
    release_enabled = "release_control" in root
    if release_enabled:
        release_control = _strict_object(
            root["release_control"],
            name="release_control",
            required={"enabled", "store_policy", "effect_policy"},
        )
        if (
            release_control["enabled"] is not True
            or release_control["store_policy"] != RELEASE_STORE_POLICY
            or release_control["effect_policy"] != RELEASE_EFFECT_POLICY
        ):
            raise LauncherError("release-control policy drift detected")
    else:
        release_control = {
            "enabled": False,
            "store_policy": RELEASE_STORE_POLICY,
            "effect_policy": RELEASE_EFFECT_POLICY,
        }
    expected_capabilities = (
        EXPECTED_CAPABILITIES if release_enabled else LEGACY_CAPABILITIES
    )
    if capabilities != expected_capabilities:
        raise LauncherError("teacher capability configuration drift detected")

    student_learning = _strict_object(
        root["student_learning"],
        name="student_learning",
        required={"profiles"},
    )
    profiles_value = student_learning["profiles"]
    if not isinstance(profiles_value, list):
        raise LauncherError("student_learning.profiles must be an array")
    profiles: list[StudentProfile] = []
    seen: set[str] = set()
    for index, value in enumerate(profiles_value):
        item = _strict_object(
            value,
            name=f"student_learning.profiles[{index}]",
            required={"profile_id", "capability_env"},
        )
        profile_id = item["profile_id"]
        capability_env = item["capability_env"]
        if not isinstance(profile_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile_id
        ):
            raise LauncherError("student profile_id is invalid")
        if profile_id in seen:
            raise LauncherError("duplicate student profile_id")
        if not isinstance(capability_env, str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{2,127}", capability_env
        ):
            raise LauncherError("student capability_env is invalid")
        profiles.append(StudentProfile(profile_id, capability_env))
        seen.add(profile_id)

    def positive_timeout(name: str, value: Any, maximum: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise LauncherError(f"{name} must be numeric")
        result = float(value)
        if not 0 < result <= maximum:
            raise LauncherError(f"{name} is outside the permitted range")
        return result

    return LauncherSettings(
        config_path=path,
        bind_host=host,
        port=port,
        controller_timeout_seconds=positive_timeout(
            "controller.timeout_seconds", controller["timeout_seconds"], 300
        ),
        startup_timeout_seconds=positive_timeout(
            "network.startup_timeout_seconds",
            network["startup_timeout_seconds"],
            300,
        ),
        health_timeout_seconds=positive_timeout(
            "network.health_timeout_seconds", network["health_timeout_seconds"], 30
        ),
        principal_id=principal_id,
        capabilities=capabilities,
        students=tuple(profiles),
        auto_connect=auto_connect,
        release_enabled=release_enabled,
        release_store_policy=str(release_control["store_policy"]),
        release_effect_policy=str(release_control["effect_policy"]),
    )


def generate_session_token() -> str:
    token = secrets.token_urlsafe(48)
    validate_session_token(token)
    return token


def validate_session_token(token: str) -> None:
    if not isinstance(token, str):
        raise LauncherError("session token must be text")
    size = len(token.encode("utf-8"))
    if not MIN_TOKEN_BYTES <= size <= MAX_TOKEN_BYTES:
        raise LauncherError(
            f"session token must be between {MIN_TOKEN_BYTES} and {MAX_TOKEN_BYTES} bytes"
        )
    if any(character.isspace() or ord(character) < 33 for character in token):
        raise LauncherError("session token contains forbidden whitespace or controls")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_overlay(settings: LauncherSettings) -> str:
    overlay = settings.overlay_root
    manifest_path = overlay / "overlay.manifest.json"
    if not overlay.is_dir() or not manifest_path.is_file():
        raise LauncherError("required WebUI overlay or manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LauncherError("WebUI overlay manifest is invalid") from exc
    if manifest.get("gateway_contract") != CONTRACT_VERSION:
        raise LauncherError("WebUI gateway contract drift detected")
    if manifest.get("mount_strategy") != "loopback_sidecar":
        raise LauncherError("WebUI mount strategy drift detected")
    navigation = manifest.get("navigation", {})
    if tuple(navigation.get("tabs", ())) != EXPECTED_PRIMARY_TABS:
        raise LauncherError("WebUI must contain the expected teacher-first routes")
    if (
        tuple(navigation.get("secondary_tabs", ())) != EXPECTED_SECONDARY_TABS
        or tuple(navigation.get("all_preserved_hash_routes", ()))
        != EXPECTED_PRESERVED_HASH_ROUTES
    ):
        raise LauncherError("WebUI secondary route compatibility drift detected")
    if (
        tuple(navigation.get("legacy_tabs", ())) != EXPECTED_TABS
        or navigation.get("legacy_tabs_hidden") is not True
    ):
        raise LauncherError("WebUI legacy route compatibility drift detected")
    if navigation.get("primary_route") != "home":
        raise LauncherError("WebUI primary route must be the teacher home")
    claims = manifest.get("claims")
    if not isinstance(claims, dict) or any(
        claims.get(name) is not False
        for name in (
            "human_reviewed",
            "official",
            "publication_allowed",
            "retrieval_ready",
            "generation_allowed",
            "teaching_use_allowed",
        )
    ) or claims.get("candidate_only") is not True:
        raise LauncherError("WebUI authority boundary drift detected")
    workbench_registry = manifest.get("workbench_product_registry")
    if workbench_registry != {
        "registry_id": REGISTRY_ID,
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "registry_file_sha256": REGISTRY_FILE_SHA256,
        "registry_schema_sha256": REGISTRY_SCHEMA_SHA256,
        "dynamic_counts": True,
        "cross_scope_sum_allowed": False,
    }:
        raise LauncherError("WebUI product registry binding drift detected")
    model_provider_settings = manifest.get("model_provider_settings")
    if model_provider_settings != {
        "scope": "local_teacher_model_provider_settings_and_fixed_synthetic_probe",
        "settings_endpoint": "/api/v1/settings/model-providers",
        "profile_endpoint_template": "/api/v1/settings/model-providers/{profile_id}",
        "credential_endpoint_template": "/api/v1/settings/model-providers/{profile_id}/credential",
        "synthetic_test_endpoint_template": "/api/v1/settings/model-providers/{profile_id}/test",
        "synthetic_test_status_endpoint_template": "/api/v1/settings/model-providers/{profile_id}/test/{probe_run_id}",
        "synthetic_test_cancel_endpoint_template": "/api/v1/settings/model-providers/{profile_id}/test/{probe_run_id}/cancel",
        "write_capability": "model_provider_settings_write",
        "synthetic_probe_execute_capability": "model_provider_synthetic_probe_execute",
        "credential_storage": "windows_credential_manager_current_user_non_roaming",
        "api_key_in_project_files": False,
        "api_key_in_browser_storage": False,
        "api_key_in_logs_or_responses": False,
        "arbitrary_base_url_allowed": False,
        "real_student_image_egress_default": False,
        "commercial_question_image_egress_default": False,
        "synthetic_probe_invocation_enabled": True,
        "production_model_invocation_enabled": False,
        "fixed_synthetic_only": True,
        "synthetic_prompt_sha256": "48543a5c638a299313060725e611d2d27dc163eb4044ac91e7b017453a591e3f",
        "synthetic_prompt_bytes": 101,
        "no_response_content_storage": True,
        "no_auto_retry": True,
        "no_redirect": True,
        "offline_workbench_without_key": True,
    }:
        raise LauncherError("WebUI model-provider settings boundary drift detected")
    workbench_release_control = manifest.get("workbench_release_control")
    if workbench_release_control != {
        "scope": "local_candidate_browse_version_control",
        "status_endpoint": "/api/v1/workbench/releases/status",
        "candidate_endpoint": "/api/v1/workbench/releases/candidates",
        "regression_start_endpoint_template": "/api/v1/workbench/releases/{release_id}/regressions",
        "regression_status_endpoint_template": "/api/v1/workbench/releases/{release_id}/regressions/{run_id}",
        "regression_cancel_endpoint_template": "/api/v1/workbench/releases/{release_id}/regressions/{run_id}/cancel",
        "select_endpoint_template": "/api/v1/workbench/releases/{release_id}/select",
        "rollback_endpoint_template": "/api/v1/workbench/releases/{release_id}/rollback",
        "prepare_capability": "workbench_release_prepare_write",
        "activate_capability": "workbench_release_activate_write",
        "fixed_server_regression_recipe": True,
        "client_supplied_commands_allowed": False,
        "client_supplied_paths_allowed": False,
        "client_supplied_environments_allowed": False,
        "client_supplied_receipts_allowed": False,
        "dynamic_candidate_counts": True,
        "automatic_activation": False,
        "selection_effect_policy": RELEASE_EFFECT_POLICY,
        "hot_swap_allowed": False,
        "api_key_included": False,
        "student_private_domain_included": False,
        "candidate_only": True,
        "human_reviewed": False,
        "official": False,
        "retrieval_ready": False,
        "teaching_use_allowed": False,
        "generation_allowed": False,
        "publication_allowed": False,
        "external_release_allowed": False,
    }:
        raise LauncherError("WebUI candidate release-control boundary drift detected")
    candidate_review = manifest.get("candidate_review_only", {})
    if (
        candidate_review.get("source_namespace") != "candidate_review_only"
        or candidate_review.get("same_five_sources_refined_not_additional") is not True
        or candidate_review.get("additional_paper_count") != 0
        or candidate_review.get("expected_counts")
        != {
            "papers": 5,
            "theme_big_questions": 25,
            "printed_questions": 207,
            "atomic_parts": 252,
            "duplicate_or_near_duplicate_candidates": 2,
        }
        or candidate_review.get("read_only") is not True
        or candidate_review.get("exact_file_allowlist") is not True
        or candidate_review.get("manifest_hash_and_bytes_verified") is not True
        or candidate_review.get("hash_verified_question_crop_serving") is not True
        or candidate_review.get("answer_crop_serving") is not False
        or candidate_review.get("original_source_serving") is not False
        or candidate_review.get("whole_page_serving") is not False
        or candidate_review.get("arbitrary_file_read_present") is not False
        or any(
            candidate_review.get(name) is not False
            for name in (
                "mutation_endpoint_present",
                "apply_endpoint_present",
                "human_reviewed",
                "formal",
                "formal_promotion_allowed",
                "retrieval_ready",
                "teaching_use_allowed",
                "generation_allowed",
                "publication_allowed",
                "promotion_allowed",
                "apply_available",
            )
        )
    ):
        raise LauncherError("WebUI candidate_review_only boundary drift detected")
    retrieval = manifest.get("retrieval_slice1", {})
    if (
        retrieval.get("read_only") is not True
        or retrieval.get("write_endpoint_present") is not False
        or retrieval.get("ingest_endpoint_present") is not False
        or retrieval.get("automatic_generation_input") is not False
    ):
        raise LauncherError("WebUI read-only retrieval boundary drift detected")
    tag_patch = manifest.get("slice2_tag_patch", {})
    authority = tag_patch.get("authority", {})
    if (
        tag_patch.get("create_list_get_only") is not True
        or tag_patch.get("apply_present") is not False
        or authority.get("candidate_only") is not True
        or authority.get("publication_allowed") is not False
    ):
        raise LauncherError("WebUI candidate-only tag boundary drift detected")
    files = manifest.get("files")
    expected_files = {"index.html", "app.js", "styles.css"}
    if not isinstance(files, dict) or set(files) != expected_files:
        raise LauncherError("WebUI overlay file inventory drift detected")
    html = (overlay / "index.html").read_text(encoding="utf-8")
    for tab in EXPECTED_TABS:
        if f'id="tab-{tab}"' not in html:
            raise LauncherError("WebUI eight-tab markup is incomplete")
    for name in sorted(expected_files):
        path = overlay / name
        expected_hash = files.get(name, {}).get("sha256")
        if not path.is_file() or not isinstance(expected_hash, str):
            raise LauncherError("WebUI overlay file is missing from its manifest")
        if not hmac.compare_digest(_sha256_file(path), expected_hash):
            raise LauncherError(f"WebUI overlay hash mismatch: {name}")
    return _sha256_file(manifest_path)


def _verify_required_paths(settings: LauncherSettings) -> str:
    if not settings.shchem_root.is_dir():
        raise LauncherError("Shanghai Chemistry knowledge base is missing")
    if not (settings.shchem_root / "catalog.csv").is_file():
        raise LauncherError("Shanghai Chemistry catalog is missing")
    if not settings.controller_script.is_file():
        raise LauncherError("Shanghai Chemistry controller is missing")
    overlay_manifest_hash = _verify_overlay(settings)
    return overlay_manifest_hash


def _capture_release_runtime(
    settings: LauncherSettings,
    *,
    expected_serving: dict[str, Any] | None = None,
) -> tuple[WorkbenchReleaseRuntime | None, str]:
    """Capture one serving release, optionally requiring an exact startup intent."""

    if not settings.release_enabled:
        overlay_hash = _verify_required_paths(settings)
        return None, overlay_hash

    from .workbench_release_control import WorkbenchReleaseStore
    from .workbench_release_regression import WorkbenchReleaseRegressionVerifier
    from .workbench_release_snapshot import FrozenWorkbenchBrowseReader

    if not settings.shchem_root.is_dir() or not settings.controller_script.is_file():
        raise LauncherError("required Shanghai Chemistry runtime is missing")
    release_root = default_release_root().absolute()
    try:
        verifier = WorkbenchReleaseRegressionVerifier(
            release_root, project_root=settings.workspace_root
        )
        store = WorkbenchReleaseStore(
            release_root,
            project_root=settings.workspace_root,
            pass_receipt_verifier=verifier,
        )
        pointer = store.read_active_pointer()
        if pointer is None:
            overlay_hash = _verify_required_paths(settings)
            registry = WorkbenchProductRegistryReader(
                settings.shchem_root, settings.overlay_root
            ).registry()
            binding = ServingReleaseBinding(
                mode="bootstrap_live",
                release_id=None,
                selected_revision=None,
                candidate_manifest_sha256=None,
                candidate_manifest_bytes=None,
                closure_sha256=overlay_hash,
                browse_snapshot_id=None,
                ui_build_id=registry["ui_build_id"],
                data_snapshot_id=registry["data_snapshot_id"],
                backend_build_id=None,
            )
            reader = None
        else:
            candidate = store.read_candidate(pointer["release_id"])
            if (
                candidate["manifest_sha256"]
                != pointer["candidate_manifest_sha256"]
                or candidate["manifest_bytes"]
                != pointer["candidate_manifest_bytes"]
            ):
                raise LauncherError("selected release candidate binding mismatched")
            reader = FrozenWorkbenchBrowseReader.from_candidate_root(
                store.candidates_root / pointer["release_id"]
            )
            reader.assert_current_backend(settings.workspace_root)
            verification = reader.verification()
            registry = reader.registry()
            manifest = reader.manifest()
            if (
                manifest.get("browse_snapshot_id")
                != verification.get("browse_snapshot_id")
                or manifest.get("backend_build_id")
                != verification.get("backend_build_id")
                or registry.get("data_snapshot_id")
                != manifest.get("data_snapshot_id")
            ):
                raise LauncherError("selected frozen browse snapshot identity mismatched")
            binding = ServingReleaseBinding(
                mode="frozen",
                release_id=pointer["release_id"],
                selected_revision=pointer["revision"],
                candidate_manifest_sha256=candidate["manifest_sha256"],
                candidate_manifest_bytes=candidate["manifest_bytes"],
                closure_sha256=candidate["manifest"]["browse_closure"][
                    "closure_sha256"
                ],
                browse_snapshot_id=verification["browse_snapshot_id"],
                ui_build_id=registry["ui_build_id"],
                data_snapshot_id=registry["data_snapshot_id"],
                backend_build_id=verification["backend_build_id"],
            )
            overlay_hash = candidate["manifest"]["browse_closure"][
                "closure_sha256"
            ]
        binding.validate()
        public = binding.public_dict()
        if expected_serving is not None and public != expected_serving:
            raise LauncherError("selected release changed after startup intent")
        runtime = WorkbenchReleaseRuntime(
            release_root=release_root,
            serving=binding,
            frozen_reader=reader,
        )
        runtime.validate()
        return runtime, overlay_hash
    except LauncherError:
        raise
    except Exception as exc:
        raise LauncherError("workbench release selection failed closed") from exc


def _probe_controller(settings: LauncherSettings, state_root: Path) -> None:
    adapter = ControllerRpcAdapter(
        settings.shchem_root,
        settings.controller_script,
        settings.controller_timeout_seconds,
        (state_root,),
    )
    try:
        status = adapter.call("status", {})
    except AdapterUnavailable as exc:
        raise LauncherError(f"controller preflight failed closed: {exc.code}") from exc
    if status.get("ok") is not True:
        raise LauncherError("controller status is not healthy")
    if status.get("contract_version") != EXPECTED_CONTROLLER_CONTRACT:
        raise LauncherError("live controller contract drift detected")


def _identity(
    settings: LauncherSettings,
    overlay_manifest_hash: str,
    serving_release: dict[str, Any] | None = None,
) -> str:
    value = {
        "launcher_schema": LAUNCHER_SCHEMA,
        "config_sha256": _sha256_file(settings.config_path),
        "workspace_root": str(settings.workspace_root),
        "shchem_root": str(settings.shchem_root),
        "controller_sha256": _sha256_file(settings.controller_script),
        "overlay_manifest_sha256": overlay_manifest_hash,
        "bind_host": settings.bind_host,
        "port": settings.port,
        "capabilities": list(settings.capabilities),
        "release_control": {
            "enabled": settings.release_enabled,
            "store_policy": settings.release_store_policy,
            "effect_policy": settings.release_effect_policy,
            "serving_release": serving_release,
        },
        "students": [profile.profile_id for profile in settings.students],
        "ccswitch_enabled": False,
    }
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(details.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(details, "st_file_attributes", 0) & reparse_flag)


def _assert_no_link_like_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    for component in (absolute, *absolute.parents):
        if os.path.lexists(component) and _is_link_or_reparse(component):
            raise LauncherError(
                "launcher state path must not contain symlinks or reparse points"
            )


def _windows_sid() -> str:
    try:
        completed = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LauncherError("could not determine the current Windows identity") from exc
    match = re.search(r"S-\d-(?:\d+-)+\d+", completed.stdout)
    if not match:
        raise LauncherError("could not determine the current Windows SID")
    return match.group(0)


def _lock_windows_acl(path: Path, *, directory: bool) -> None:
    sid = _windows_sid()
    script = """
$ErrorActionPreference = 'Stop'
$target = $env:SHCHEM_ACL_TARGET
$identity = [System.Security.Principal.SecurityIdentifier]::new($env:SHCHEM_ACL_SID)
$isDirectory = $env:SHCHEM_ACL_DIRECTORY -eq '1'
if ($isDirectory) {
    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $identity,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
} else {
    $acl = [System.Security.AccessControl.FileSecurity]::new()
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $identity,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
}
$acl.SetAccessRuleProtection($true, $false)
$acl.SetOwner($identity)
[void]$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $target -AclObject $acl
"""
    allowed_environment = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed_environment
    }
    environment.update(
        {
            "SHCHEM_ACL_TARGET": str(path),
            "SHCHEM_ACL_SID": sid,
            "SHCHEM_ACL_DIRECTORY": "1" if directory else "0",
        }
    )
    last_error: OSError | subprocess.SubprocessError | None = None
    for delay in (0.0, 0.05, 0.2):
        if delay:
            time.sleep(delay)
        try:
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except (OSError, subprocess.SubprocessError) as exc:
            last_error = exc
    raise LauncherError("could not apply owner-only ACL to launcher state") from last_error


def _secure_directory(path: Path, workspace_root: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    _assert_no_link_like_components(absolute)
    resolved = absolute.resolve()
    if _is_within(resolved, workspace_root):
        raise LauncherError("session state and tokens must remain outside the project")
    resolved.mkdir(parents=True, exist_ok=True)
    _assert_no_link_like_components(absolute)
    if absolute.resolve() != resolved or not resolved.is_dir():
        raise LauncherError("launcher state directory identity changed during setup")
    os.chmod(resolved, stat.S_IRWXU)
    if os.name == "nt":
        _lock_windows_acl(resolved, directory=True)
    _assert_private_path(resolved, directory=True)
    return resolved


def _secure_file(path: Path) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    if os.name == "nt":
        _lock_windows_acl(path, directory=False)


def _assert_private_path(path: Path, *, directory: bool) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise LauncherError("launcher private state is missing") from exc
    if _is_link_or_reparse(path):
        raise LauncherError("launcher private state must not be a link or reparse point")
    if directory:
        if not stat.S_ISDIR(details.st_mode):
            raise LauncherError("launcher private state directory is invalid")
    else:
        if not stat.S_ISREG(details.st_mode):
            raise LauncherError("launcher private state file is invalid")
        if details.st_nlink != 1:
            raise LauncherError("launcher private state file must not be hard-linked")
    if os.name != "nt":
        if stat.S_IMODE(details.st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
            raise LauncherError("launcher private state permissions are too broad")
        return
    last_error: OSError | subprocess.SubprocessError | None = None
    for delay in (0.0, 0.05, 0.2):
        if delay:
            time.sleep(delay)
        try:
            owner = subprocess.run(
                ["whoami"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip()
            completed = subprocess.run(
                ["icacls", str(path)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            break
        except (OSError, subprocess.SubprocessError) as exc:
            last_error = exc
    else:
        raise LauncherError("could not verify launcher private-state ACL") from last_error
    ace_lines = [line for line in completed.stdout.splitlines() if ":(" in line]
    acl_text = "\n".join(ace_lines).casefold()
    if (
        len(ace_lines) != 1
        or "(f)" not in acl_text
        or (owner.casefold() not in acl_text and _windows_sid().casefold() not in acl_text)
    ):
        raise LauncherError("launcher private-state ACL is not owner-only")


def _assert_private_file(path: Path) -> None:
    _assert_private_path(path, directory=False)


@contextlib.contextmanager
def _launcher_lifecycle_lock(directory: Path):
    """Serialize start/stop so token and session ownership cannot be crossed."""

    path = directory / LIFECYCLE_LOCK_FILE
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    with _LIFECYCLE_THREAD_LOCK:
        descriptor = os.open(path, flags, 0o600)
        try:
            details = os.fstat(descriptor)
            path_details = path.lstat()
            if (
                not stat.S_ISREG(details.st_mode)
                or getattr(details, "st_nlink", 1) != 1
                or path.is_symlink()
                or not os.path.samestat(details, path_details)
            ):
                raise LauncherError("launcher lifecycle lock is unsafe")
            if details.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            _secure_file(path)
            _assert_private_file(path)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _atomic_private_write(path: Path, content: str) -> None:
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    replaced = False
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _secure_file(temp_path)
        os.replace(temp_path, path)
        replaced = True
        _secure_file(path)
        _assert_private_file(path)
    except Exception:
        if replaced:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_private_write(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _write_one_shot_json(path: Path, value: dict[str, Any]) -> None:
    """Publish an owner-only signal that the consumer may remove immediately."""

    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        # Complete permission and ACL verification before publication.  Once
        # replaced into STOP_FILE the child is allowed to consume and remove it
        # immediately, so path-based post-publication checks would be racy.
        _secure_file(temp_path)
        _assert_private_file(temp_path)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        _assert_private_file(path)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LauncherError(f"launcher state is missing or invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise LauncherError(f"launcher state is invalid: {path.name}")
    return value


def _file_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        details = path.stat()
    except OSError:
        return None
    return (
        int(details.st_dev),
        int(details.st_ino),
        int(details.st_size),
        int(details.st_mtime_ns),
    )


def _read_stop_candidate(
    path: Path, expected_identity: tuple[int, int, int, int]
) -> tuple[dict[str, Any], tuple[int, int, int, int] | None]:
    """Verify one stop candidate without blocking the HTTP accept loop."""

    if _file_identity(path) != expected_identity:
        return {}, None
    try:
        value = _read_json(path)
    except LauncherError:
        value = {}
    verified_identity = _file_identity(path)
    if verified_identity != expected_identity:
        return {}, None
    return value, verified_identity


def _read_token(session_dir: Path) -> str:
    path = session_dir / TOKEN_FILE
    try:
        _assert_private_file(path)
        token = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LauncherError("session token file is missing") from exc
    validate_session_token(token)
    return token


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def build_app_config(
    settings: LauncherSettings,
    session_dir: Path,
    token: str,
    *,
    release_runtime: WorkbenchReleaseRuntime | None = None,
) -> AppConfig:
    validate_session_token(token)
    student_capabilities: dict[str, str] = {}
    for profile in settings.students:
        capability = os.environ.get(profile.capability_env, "")
        if not capability:
            raise LauncherError(
                f"required student capability environment variable is missing: "
                f"{profile.capability_env}"
            )
        student_capabilities[profile.profile_id] = capability
    student_ids = tuple(student_capabilities)
    config = AppConfig(
        bind_host=settings.bind_host,
        port=settings.port,
        mode="controller",
        state_root=session_dir / "gateway-state",
        shchem_root=settings.shchem_root,
        overlay_root=settings.overlay_root,
        student_data_root=None,
        student_capabilities=student_capabilities,
        controller_script=settings.controller_script,
        controller_timeout_seconds=settings.controller_timeout_seconds,
        ccswitch_enabled=False,
        model_provider_metadata_root=session_dir / "model-provider-settings",
        intake_import_root=default_intake_import_root(),
        review_workbench_root=default_review_workbench_root(),
        workbench_release_runtime=release_runtime,
        personal_auto_auth=settings.auto_connect,
        principals=[
            Principal(
                settings.principal_id,
                "teacher",
                token_digest(token),
                student_ids,
                settings.capabilities,
            )
        ],
        students=student_ids,
        allowed_origins=(
            f"http://127.0.0.1:{settings.port}",
            f"http://localhost:{settings.port}",
        ),
    )
    config.validate()
    return config


def _create_launcher_server(config: AppConfig) -> GatewayHTTPServer:
    if os.name != "nt":
        return create_server(config)
    config.validate()
    return _LauncherGatewayHTTPServer((config.bind_host, config.port), config)


def _health_request(
    host: str,
    port: int,
    token: str,
    timeout: float,
    *,
    expected_serving_release: dict[str, Any] | None = None,
) -> dict[str, Any]:
    display_host = f"[{host}]" if ":" in host else host
    origin = f"http://{display_host}:{port}"
    request = urlrequest.Request(
        f"{origin}/api/v1/status",
        headers={"Authorization": f"Bearer {token}", "Origin": origin},
        method="GET",
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024)
            status_code = response.status
    except (OSError, urlerror.URLError, urlerror.HTTPError) as exc:
        raise LauncherError("WebUI health request failed") from exc
    if status_code != 200:
        raise LauncherError("WebUI health endpoint returned a non-success status")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LauncherError("WebUI health response is invalid") from exc
    data = envelope.get("data", {}) if isinstance(envelope, dict) else {}
    controller = data.get("controller", {}) if isinstance(data, dict) else {}
    controller_status = (
        data.get("controller_status", {}) if isinstance(data, dict) else {}
    )
    delivery = data.get("delivery_policy", {}) if isinstance(data, dict) else {}
    if (
        envelope.get("contract_version") != CONTRACT_VERSION
        or data.get("service") != "deeptutor-shchem-gateway"
        or data.get("mode") != "controller"
        or controller.get("available") is not True
        or controller.get("live_status") != "executed"
        or controller_status.get("ok") is not True
        or controller_status.get("contract_version")
        != EXPECTED_CONTROLLER_CONTRACT
        or data.get("ccswitch", {}).get("enabled") is not False
        or data.get("machine_only") is not True
        or data.get("human_reviewed") is not False
        or data.get("collection_enabled") is not False
        or delivery.get("external_publication_allowed") is not False
        or delivery.get("official_claim_allowed") is not False
        or (
            expected_serving_release is not None
            and data.get("serving_release") != expected_serving_release
        )
    ):
        raise LauncherError("WebUI health contract or safety boundary failed closed")
    return envelope


def _state_url(state: dict[str, Any]) -> str:
    host = str(state.get("bind_host", ""))
    port = state.get("port")
    if not _is_loopback(host) or isinstance(port, bool) or not isinstance(port, int):
        raise LauncherError("launcher session address is invalid")
    display_host = f"[{host}]" if ":" in host else host
    return f"http://{display_host}:{port}/overlay/"


def _load_session(session_dir: Path) -> dict[str, Any]:
    state = _read_json(session_dir / SESSION_FILE)
    if state.get("schema_version") != SESSION_SCHEMA:
        raise LauncherError("launcher session schema drift detected")
    if not isinstance(state.get("instance_id"), str):
        raise LauncherError("launcher session instance is invalid")
    _state_url(state)
    return state


def _current_identity_from_state(state: dict[str, Any]) -> str:
    config_path = state.get("config_path")
    if not isinstance(config_path, str):
        raise LauncherError("launcher session config binding is missing")
    settings = load_settings(config_path)
    serving = state.get("serving_release")
    if settings.release_enabled:
        if not isinstance(serving, dict):
            raise LauncherError("running WebUI serving release binding is missing")
        mode = serving.get("mode")
        if mode == "bootstrap_live":
            overlay_manifest_hash = _verify_required_paths(settings)
        elif mode == "frozen":
            overlay_manifest_hash = serving.get("closure_sha256")
            if not isinstance(overlay_manifest_hash, str) or not re.fullmatch(
                r"[0-9a-f]{64}", overlay_manifest_hash
            ):
                raise LauncherError("running frozen release identity is invalid")
        else:
            raise LauncherError("running WebUI serving release mode is invalid")
        return _identity(settings, overlay_manifest_hash, serving)
    _release_runtime, overlay_manifest_hash = _capture_release_runtime(settings)
    return _identity(settings, overlay_manifest_hash)


def health_session(session_dir: str | Path | None = None) -> dict[str, Any]:
    directory = Path(session_dir or default_session_dir()).resolve()
    state = _load_session(directory)
    current_identity = _current_identity_from_state(state)
    if not hmac.compare_digest(str(state.get("config_identity", "")), current_identity):
        raise LauncherError("running WebUI configuration drift detected")
    token = _read_token(directory)
    _health_request(
        str(state["bind_host"]),
        int(state["port"]),
        token,
        float(state.get("health_timeout_seconds", 5)),
        expected_serving_release=(
            state.get("serving_release") if isinstance(state.get("serving_release"), dict) else None
        ),
    )
    return state


def _cleanup_session_files(directory: Path, instance_id: str | None = None) -> None:
    session_path = directory / SESSION_FILE
    if instance_id and session_path.exists():
        try:
            current = _read_json(session_path)
        except LauncherError:
            return
        if current.get("instance_id") != instance_id:
            return
    for name in (
        SESSION_FILE,
        TOKEN_FILE,
        STOP_FILE,
        FAILURE_FILE,
        STARTUP_INTENT_FILE,
    ):
        path = directory / name
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _child_output_path(directory: Path) -> Path:
    return directory.with_name(f".{directory.name}.{CHILD_OUTPUT_FILE}")


def _cleanup_child_output(directory: Path, *, timeout_seconds: float = 2.0) -> None:
    """Remove the detached child's log after Windows releases its file handle.

    A child can already be reported as exited while Windows is still closing the
    inherited stdout/stderr handle.  Treat that short sharing violation as a
    bounded shutdown race, but fail closed if the owner-only file stays locked.
    """

    path = _child_output_path(directory)
    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    while True:
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise LauncherError(
                    "WebUI child output remained locked after process exit"
                ) from exc
            time.sleep(0.05)


def _child_command(
    settings: LauncherSettings, session_dir: Path, instance_id: str
) -> list[str]:
    entrypoint = settings.workspace_root / "runtime/deeptutor_shchem/launcher.py"
    return [
        sys.executable,
        str(entrypoint),
        "_serve",
        "--config",
        str(settings.config_path),
        "--session-dir",
        str(session_dir),
        "--instance-id",
        instance_id,
    ]


def _presentation_runtime_environment() -> dict[str, str]:
    """Locate the optional bundled presentation toolchain without UI output.

    Missing presentation dependencies do not block the personal question
    workbench. In that case the PPT API reports its own concise 503 while all
    offline browsing and assembly routes remain available.
    """

    home = Path.home()
    dependency_root = (
        home
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies"
    )
    node = dependency_root / "node/bin/node.exe"
    node_modules = dependency_root / "node/node_modules"
    bin_dir = dependency_root / "bin/override"
    python = dependency_root / "python/python.exe"
    skills = sorted(
        (
            home
            / ".codex/plugins/cache/openai-primary-runtime/presentations"
        ).glob("*/skills/presentations"),
        key=lambda value: value.parent.parent.name,
        reverse=True,
    )
    skill_dir = next(
        (
            value
            for value in skills
            if (value / "container_tools/render_slides.py").is_file()
            and (value / "container_tools/slides_test.py").is_file()
            and (value / "container_tools/create_montage.py").is_file()
        ),
        None,
    )
    if (
        not node.is_file()
        or not node_modules.is_dir()
        or not bin_dir.is_dir()
        or not python.is_file()
        or skill_dir is None
    ):
        return {}
    return {
        "RUNTIME_NODE": str(node),
        "RUNTIME_NODE_MODULES": str(node_modules),
        "RUNTIME_BIN_DIR": str(bin_dir),
        "PRESENTATIONS_PYTHON": str(python),
        "PRESENTATIONS_SKILL_DIR": str(skill_dir),
    }


def _spawn_child(
    settings: LauncherSettings, session_dir: Path, instance_id: str
) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment.pop("SHCHEM_GATEWAY_TEACHER_TOKEN", None)
    environment.pop(TOKEN_ENV, None)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.update(_presentation_runtime_environment())
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "env": environment,
        "cwd": str(settings.workspace_root),
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    try:
        # A detached Windows process whose three standard handles all target NUL can
        # intermittently fail when its native ACL probes launch. Keep stdout/stderr on
        # a valid owner-only file. It contains no credential output and is removed by
        # the parent after the child exits (or before the next start after a crash).
        output_path = _child_output_path(session_dir)
        _atomic_private_write(output_path, "")
        with output_path.open("ab", buffering=0) as output:
            kwargs["stdout"] = output
            kwargs["stderr"] = output
            return subprocess.Popen(
                _child_command(settings, session_dir, instance_id), **kwargs
            )
    except OSError as exc:
        raise LauncherError("could not start the WebUI process") from exc


def _process_alive(pid: Any) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information, False, pid
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(
            handle, ctypes.byref(exit_code)
        ):
            return False
        return exit_code.value == still_active
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _abort_failed_start(
    directory: Path,
    instance_id: str,
    child: subprocess.Popen[bytes] | None,
) -> None:
    if child is None:
        _cleanup_session_files(directory, instance_id)
        _cleanup_child_output(directory)
        return
    try:
        _write_json(directory / STOP_FILE, {"instance_id": instance_id})
    except LauncherError:
        pass
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            child.terminate()
        except OSError:
            pass
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                child.kill()
            except OSError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise LauncherError(
                    "failed WebUI process could not be terminated"
                ) from exc
    if child.poll() is None:
        raise LauncherError("failed WebUI process could not be terminated")
    _cleanup_session_files(directory, instance_id)
    _cleanup_child_output(directory)


def start_session(
    config_path: str | Path | None = None,
    session_dir: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    settings = load_settings(config_path)
    directory = _secure_directory(
        Path(session_dir or default_session_dir()), settings.workspace_root
    )
    with _launcher_lifecycle_lock(directory):
        return _start_session_locked(settings, directory)


def _start_session_locked(
    settings: LauncherSettings, directory: Path
) -> tuple[dict[str, Any], str]:
    session_path = directory / SESSION_FILE
    if session_path.exists():
        try:
            existing = health_session(directory)
        except LauncherError:
            raise LauncherError(
                "an unhealthy or drifted session already exists; run stop before start"
            )
        raise LauncherError(f"WebUI is already healthy at {_state_url(existing)}")
    _cleanup_session_files(directory)
    _cleanup_child_output(directory)
    release_runtime, overlay_manifest_hash = _capture_release_runtime(settings)
    serving_release = (
        release_runtime.serving.public_dict()
        if release_runtime is not None
        else None
    )
    if not _port_available(settings.bind_host, settings.port):
        raise LauncherError("configured loopback port is already in use")
    _probe_controller(settings, directory / "gateway-state")
    token = generate_session_token()
    _atomic_private_write(directory / TOKEN_FILE, token)
    instance_id = uuid.uuid4().hex
    config_identity = _identity(
        settings, overlay_manifest_hash, serving_release
    )
    _write_json(
        directory / STARTUP_INTENT_FILE,
        {
            "schema_version": "shchem.webui.startup_intent.v1",
            "instance_id": instance_id,
            "config_identity": config_identity,
            "serving_release": serving_release,
        },
    )
    try:
        child = _spawn_child(settings, directory, instance_id)
    except Exception:
        _abort_failed_start(directory, instance_id, None)
        raise
    deadline = time.monotonic() + settings.startup_timeout_seconds
    last_error: LauncherError | None = None
    try:
        while time.monotonic() < deadline:
            if child.poll() is not None:
                break
            failure_path = directory / FAILURE_FILE
            if failure_path.exists():
                failure = _read_json(failure_path)
                reason = str(failure.get("reason", "startup failed"))
                last_error = LauncherError(reason)
                break
            if session_path.exists():
                try:
                    state = _load_session(directory)
                    if state.get("instance_id") != instance_id:
                        raise LauncherError("launcher instance binding mismatch")
                    if not hmac.compare_digest(
                        str(state.get("config_identity", "")),
                        config_identity,
                    ):
                        raise LauncherError("launcher child configuration drift detected")
                    _health_request(
                        settings.bind_host,
                        settings.port,
                        token,
                        settings.health_timeout_seconds,
                        expected_serving_release=serving_release,
                    )
                    return state, token
                except LauncherError as exc:
                    last_error = exc
            time.sleep(0.1)
    except Exception:
        _abort_failed_start(directory, instance_id, child)
        raise
    _abort_failed_start(directory, instance_id, child)
    if last_error:
        raise LauncherError(f"WebUI failed to become healthy: {last_error}")
    raise LauncherError("WebUI failed to become healthy before the startup timeout")


def stop_session(
    session_dir: str | Path | None = None, *, timeout_seconds: float = 15
) -> dict[str, Any]:
    directory = Path(session_dir or default_session_dir()).resolve()
    with _launcher_lifecycle_lock(directory):
        return _stop_session_locked(directory, timeout_seconds=timeout_seconds)


def _stop_session_locked(
    directory: Path, *, timeout_seconds: float
) -> dict[str, Any]:
    state = _load_session(directory)
    instance_id = str(state["instance_id"])
    _write_one_shot_json(directory / STOP_FILE, {"instance_id": instance_id})
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not (directory / SESSION_FILE).exists():
            if _process_alive(state.get("pid")):
                time.sleep(0.1)
                continue
            _cleanup_session_files(directory, instance_id)
            _cleanup_child_output(directory)
            return state
        if not _process_alive(state.get("pid")):
            _cleanup_session_files(directory, instance_id)
            _cleanup_child_output(directory)
            return state
        time.sleep(0.1)
    raise LauncherError("WebUI did not stop cleanly before the timeout")


def serve_session(
    config_path: str | Path,
    session_dir: str | Path,
    instance_id: str,
) -> int:
    settings = load_settings(config_path)
    directory = _secure_directory(Path(session_dir), settings.workspace_root)
    os.environ.pop(TOKEN_ENV, None)
    token = _read_token(directory)
    intent = _read_json(directory / STARTUP_INTENT_FILE)
    if (
        intent.get("schema_version") != "shchem.webui.startup_intent.v1"
        or intent.get("instance_id") != instance_id
        or set(intent)
        != {
            "schema_version",
            "instance_id",
            "config_identity",
            "serving_release",
        }
    ):
        raise LauncherError("launcher startup intent is invalid")
    expected_serving = intent.get("serving_release")
    if settings.release_enabled and not isinstance(expected_serving, dict):
        raise LauncherError("launcher startup release intent is missing")
    if not settings.release_enabled and expected_serving is not None:
        raise LauncherError("legacy launcher intent contains a release binding")
    release_runtime, overlay_manifest_hash = _capture_release_runtime(
        settings,
        expected_serving=(
            expected_serving if isinstance(expected_serving, dict) else None
        ),
    )
    config_identity = _identity(
        settings, overlay_manifest_hash, expected_serving
    )
    if not hmac.compare_digest(
        str(intent.get("config_identity", "")), config_identity
    ):
        raise LauncherError("launcher startup intent identity mismatched")
    app_config = build_app_config(
        settings, directory, token, release_runtime=release_runtime
    )
    for profile in settings.students:
        os.environ.pop(profile.capability_env, None)
    _probe_controller(settings, directory / "gateway-state")
    try:
        server = _create_launcher_server(app_config)
    except OSError as exc:
        raise LauncherError("configured loopback port could not be bound") from exc
    try:
        release_snapshot = (
            server.service.frozen_browse.registry()
            if server.service.frozen_browse is not None
            else server.service.workbench_product_registry.registry()
        )
    except Exception as exc:
        server.server_close()
        raise LauncherError(
            "workbench release snapshot activation failed closed"
        ) from exc
    if release_runtime is not None:
        expected_identity = release_runtime.serving
        if (
            release_snapshot.get("ui_build_id") != expected_identity.ui_build_id
            or release_snapshot.get("data_snapshot_id")
            != expected_identity.data_snapshot_id
        ):
            server.server_close()
            raise LauncherError(
                "workbench serving release registry identity mismatched"
            )
    state = {
        "schema_version": SESSION_SCHEMA,
        "instance_id": instance_id,
        "pid": os.getpid(),
        "started_at": _utc_now(),
        "bind_host": settings.bind_host,
        "port": int(server.server_address[1]),
        "url": f"http://{settings.bind_host}:{server.server_address[1]}/overlay/",
        "config_path": str(settings.config_path),
        "config_identity": config_identity,
        "health_timeout_seconds": settings.health_timeout_seconds,
        "token_storage": "owner_only_file_outside_project",
        "ccswitch_enabled": False,
        "mode": "controller",
        "ui_build_id": release_snapshot["ui_build_id"],
        "data_snapshot_id": release_snapshot["data_snapshot_id"],
        "product_registry_id": release_snapshot["registry_id"],
    }
    if release_runtime is not None:
        state["serving_release"] = release_runtime.serving.public_dict()
    try:
        (directory / STARTUP_INTENT_FILE).unlink()
    except FileNotFoundError:
        server.server_close()
        _cleanup_session_files(directory, instance_id)
        raise LauncherError("launcher startup intent disappeared before commit")
    try:
        _write_json(directory / SESSION_FILE, state)
    except Exception:
        server.server_close()
        _cleanup_session_files(directory, instance_id)
        raise
    server.timeout = 0.25
    rejected_stop_identity: tuple[int, int, int, int] | None = None
    submitted_stop_identity: tuple[int, int, int, int] | None = None
    stop_future: Future[
        tuple[dict[str, Any], tuple[int, int, int, int] | None]
    ] | None = None
    stop_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="shchem-launcher-stop-verifier",
    )
    try:
        while True:
            stop_path = directory / STOP_FILE
            if stop_future is not None and stop_future.done():
                try:
                    stop, verified_identity = stop_future.result()
                except Exception:  # noqa: BLE001
                    stop, verified_identity = {}, submitted_stop_identity
                stop_future = None
                if (
                    verified_identity is not None
                    and stop.get("instance_id") == instance_id
                ):
                    break
                rejected_stop_identity = verified_identity or submitted_stop_identity
                submitted_stop_identity = None
            stop_identity = _file_identity(stop_path)
            if (
                stop_future is None
                and stop_identity is not None
                and stop_identity != rejected_stop_identity
            ):
                submitted_stop_identity = stop_identity
                stop_future = stop_executor.submit(
                    _read_stop_candidate,
                    stop_path,
                    stop_identity,
                )
            server.handle_request()
    finally:
        stop_executor.shutdown(wait=True, cancel_futures=True)
        server.server_close()
        _cleanup_session_files(directory, instance_id)
        os.environ.pop(TOKEN_ENV, None)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed ShanghaiChem loopback WebUI launcher"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="start a detached loopback WebUI")
    start.add_argument("--config", default=str(default_config_path()))
    start.add_argument("--session-dir", default=str(default_session_dir()))
    start.add_argument(
        "--copy-token",
        action="store_true",
        help="copy the one-time local access token to the Windows clipboard",
    )
    health = commands.add_parser("health", help="authenticate and check live status")
    health.add_argument("--session-dir", default=str(default_session_dir()))
    stop = commands.add_parser("stop", help="request a clean local shutdown")
    stop.add_argument("--session-dir", default=str(default_session_dir()))
    internal = commands.add_parser("_serve", help=argparse.SUPPRESS)
    internal.add_argument("--config", required=True)
    internal.add_argument("--session-dir", required=True)
    internal.add_argument("--instance-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "start":
            settings = load_settings(args.config)
            state, token = start_session(args.config, args.session_dir)
            print(f"上海化学题库工作台已启动：{_state_url(state)}")
            copied = False
            if settings.auto_connect:
                print("个人模式已启用自动本机连接；打开网页即可直接使用。")
            elif args.copy_token and os.name == "nt":
                try:
                    completed = subprocess.run(
                        ["clip.exe"],
                        input=token.encode("ascii"),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    copied = completed.returncode == 0
                except (OSError, subprocess.SubprocessError, UnicodeError):
                    copied = False
            if settings.auto_connect:
                pass
            elif copied:
                print("本地访问口令已复制到剪贴板；维护页面中点击“从剪贴板读取”即可。")
            else:
                print(f"本地访问口令（仅显示一次）：{token}")
            print("查看状态：python runtime/deeptutor_shchem/launcher.py health")
            print("安全关闭：python runtime/deeptutor_shchem/launcher.py stop")
            return 0
        if args.command == "health":
            state = health_session(args.session_dir)
            print(f"工作台状态正常：{_state_url(state)}")
            print("控制器在线；访问口令有效；仅限本机回环地址；CCSwitch 未启用")
            return 0
        if args.command == "stop":
            state = stop_session(args.session_dir)
            print(f"工作台已关闭：{_state_url(state)}")
            return 0
        if args.command == "_serve":
            return serve_session(args.config, args.session_dir, args.instance_id)
    except (LauncherError, OSError, ValueError) as exc:
        if args.command == "_serve":
            try:
                directory = Path(args.session_dir).resolve()
                directory.mkdir(parents=True, exist_ok=True)
                _write_json(directory / FAILURE_FILE, {"reason": str(exc)})
            except (LauncherError, OSError):
                return 2
        else:
            print(f"工作台启动器错误：{exc}", file=sys.stderr)
        return 2
    return 2


__all__ = [
    "LauncherError",
    "LauncherSettings",
    "build_app_config",
    "default_config_path",
    "default_session_dir",
    "generate_session_token",
    "health_session",
    "load_settings",
    "main",
    "serve_session",
    "start_session",
    "stop_session",
    "validate_session_token",
]

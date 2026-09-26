from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CONTRACT_VERSION = "shchem.gateway.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID = re.compile(r"^WBREL-[0-9a-f]{64}$")
_REVISION = re.compile(r"^WBREV-[0-9a-f]{32}$")


class ConfigError(ValueError):
    """Raised when a gateway configuration violates a safety invariant."""


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Principal:
    principal_id: str
    role: str
    token_sha256: str
    students: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    def can_access_student(self, student_id: str) -> bool:
        return (
            self.role == "teacher"
            and "*" in self.students
            or student_id in self.students
        )


@dataclass(frozen=True)
class ServingReleaseBinding:
    """Byte identities for the browse release fixed for one service process.

    ``selected_revision`` is the pointer revision captured at startup.  A later
    explicit selection may change the store pointer, but it never mutates this
    process' serving binding; the new selection takes effect only after a clean
    restart.
    """

    mode: Literal["bootstrap_live", "frozen"]
    release_id: str | None
    selected_revision: str | None
    candidate_manifest_sha256: str | None
    candidate_manifest_bytes: int | None
    closure_sha256: str
    browse_snapshot_id: str | None
    ui_build_id: str
    data_snapshot_id: str
    backend_build_id: str | None

    def validate(self) -> None:
        if self.mode not in {"bootstrap_live", "frozen"}:
            raise ConfigError("serving release mode is invalid")
        for label, value in (
            ("closure_sha256", self.closure_sha256),
            ("ui_build_id", self.ui_build_id),
            ("data_snapshot_id", self.data_snapshot_id),
        ):
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ConfigError(f"serving release {label} is invalid")
        if self.mode == "bootstrap_live":
            if any(
                value is not None
                for value in (
                    self.release_id,
                    self.selected_revision,
                    self.candidate_manifest_sha256,
                    self.candidate_manifest_bytes,
                    self.browse_snapshot_id,
                    self.backend_build_id,
                )
            ):
                raise ConfigError("bootstrap serving release contains frozen identity")
            return
        if (
            not isinstance(self.release_id, str)
            or not _RELEASE_ID.fullmatch(self.release_id)
            or not isinstance(self.selected_revision, str)
            or not _REVISION.fullmatch(self.selected_revision)
            or not isinstance(self.candidate_manifest_sha256, str)
            or not _SHA256.fullmatch(self.candidate_manifest_sha256)
            or isinstance(self.candidate_manifest_bytes, bool)
            or not isinstance(self.candidate_manifest_bytes, int)
            or self.candidate_manifest_bytes < 1
            or not isinstance(self.browse_snapshot_id, str)
            or not _SHA256.fullmatch(self.browse_snapshot_id)
            or not isinstance(self.backend_build_id, str)
            or not _SHA256.fullmatch(self.backend_build_id)
        ):
            raise ConfigError("frozen serving release identity is incomplete")

    def public_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "release_id": self.release_id,
            "selected_revision": self.selected_revision,
            "candidate_manifest_sha256": self.candidate_manifest_sha256,
            "candidate_manifest_bytes": self.candidate_manifest_bytes,
            "closure_sha256": self.closure_sha256,
            "browse_snapshot_id": self.browse_snapshot_id,
            "ui_build_id": self.ui_build_id,
            "data_snapshot_id": self.data_snapshot_id,
            "backend_build_id": self.backend_build_id,
            "candidate_only": True,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        }


@dataclass(frozen=True)
class WorkbenchReleaseRuntime:
    """Non-serializable launcher-to-service release binding."""

    release_root: Path
    serving: ServingReleaseBinding
    frozen_reader: Any | None = field(default=None, repr=False, compare=False)

    def validate(self) -> None:
        self.serving.validate()
        if not self.release_root.is_absolute():
            raise ConfigError("workbench release root must be absolute")
        if self.serving.mode == "frozen" and self.frozen_reader is None:
            raise ConfigError("frozen serving release reader is missing")
        if self.serving.mode == "bootstrap_live" and self.frozen_reader is not None:
            raise ConfigError("bootstrap serving release cannot have a frozen reader")


@dataclass
class AppConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8765
    max_request_bytes: int = 8 * 1024 * 1024
    max_upload_bytes: int = 6 * 1024 * 1024
    mode: str = "controller"
    state_root: Path = Path("runtime/deeptutor_shchem/private_state")
    shchem_root: Path = Path("sh-chem-db")
    overlay_root: Path = Path("runtime/deeptutor_shchem/overlay")
    student_data_root: Path | None = None
    student_capabilities: dict[str, str] = field(default_factory=dict, repr=False)
    fixture_path: Path | None = None
    controller_script: Path | None = None
    controller_timeout_seconds: float = 90.0
    ccswitch_enabled: bool = False
    ccswitch_base_url: str = "http://127.0.0.1:15721"
    ccswitch_timeout_seconds: float = 5.0
    model_provider_metadata_root: Path | None = None
    intake_import_root: Path | None = None
    review_workbench_root: Path | None = None
    workbench_release_runtime: WorkbenchReleaseRuntime | None = field(
        default=None, repr=False
    )
    personal_auto_auth: bool = False
    principals: list[Principal] = field(default_factory=list)
    students: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    def validate(self) -> None:
        if not _is_loopback(self.bind_host):
            raise ConfigError("bind_host must be a loopback address")
        if not 0 <= self.port <= 65535:
            raise ConfigError("port must be between 0 and 65535")
        if self.max_request_bytes < 1024:
            raise ConfigError("max_request_bytes is too small")
        if not 1 <= self.max_upload_bytes <= self.max_request_bytes:
            raise ConfigError(
                "max_upload_bytes must be positive and within request limit"
            )
        if self.mode not in {"controller", "mock"}:
            raise ConfigError("mode must be 'controller' or 'mock'")
        if self.mode == "mock" and self.fixture_path is None:
            raise ConfigError("mock mode requires fixture_path")
        if self.ccswitch_base_url != "http://127.0.0.1:15721":
            raise ConfigError("CCSwitch may only use http://127.0.0.1:15721")
        if self.ccswitch_timeout_seconds <= 0 or self.controller_timeout_seconds <= 0:
            raise ConfigError("timeouts must be positive")
        if not self.principals:
            raise ConfigError("at least one authentication principal is required")
        if not isinstance(self.personal_auto_auth, bool):
            raise ConfigError("personal_auto_auth must be boolean")
        if self.personal_auto_auth and sum(
            principal.role == "teacher" for principal in self.principals
        ) != 1:
            raise ConfigError(
                "personal_auto_auth requires exactly one teacher principal"
            )
        if self.workbench_release_runtime is not None:
            self.workbench_release_runtime.validate()
        seen_ids: set[str] = set()
        seen_tokens: set[str] = set()
        for principal in self.principals:
            if principal.role not in {"teacher", "student"}:
                raise ConfigError(f"unsupported role for {principal.principal_id}")
            if principal.principal_id in seen_ids:
                raise ConfigError("duplicate principal_id")
            if principal.token_sha256 in seen_tokens:
                raise ConfigError("authentication tokens must be unique")
            if len(principal.token_sha256) != 64:
                raise ConfigError("invalid token digest")
            if len(principal.capabilities) != len(set(principal.capabilities)):
                raise ConfigError("principal capabilities must be unique")
            for capability in principal.capabilities:
                if (
                    not isinstance(capability, str)
                    or not capability
                    or len(capability) > 128
                    or any(
                        character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-"
                        for character in capability
                    )
                ):
                    raise ConfigError("invalid principal capability")
            seen_ids.add(principal.principal_id)
            seen_tokens.add(principal.token_sha256)

    @classmethod
    def from_file(cls, config_path: str | Path) -> AppConfig:
        path = Path(config_path).resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        base = path.parent

        def resolve_path(value: str | None) -> Path | None:
            if not value:
                return None
            candidate = Path(value)
            return (
                (base / candidate).resolve()
                if not candidate.is_absolute()
                else candidate.resolve()
            )

        def lexical_path(value: str | None) -> Path | None:
            """Keep reparse evidence for security-sensitive mutable state roots."""

            if not value:
                return None
            candidate = Path(value).expanduser()
            combined = base / candidate if not candidate.is_absolute() else candidate
            return Path(os.path.abspath(combined))

        principals: list[Principal] = []
        for item in raw.get("auth", {}).get("principals", []):
            env_name = str(item.get("token_env", ""))
            if not env_name:
                raise ConfigError(
                    "each principal requires token_env; inline tokens are forbidden"
                )
            token = os.environ.get(env_name, "")
            if not token:
                raise ConfigError(
                    f"required authentication environment variable is missing: {env_name}"
                )
            principals.append(
                Principal(
                    principal_id=str(item["principal_id"]),
                    role=str(item["role"]),
                    token_sha256=token_digest(token),
                    students=tuple(str(value) for value in item.get("students", [])),
                    capabilities=tuple(
                        str(value) for value in item.get("capabilities", [])
                    ),
                )
            )

        network = raw.get("network", {})
        limits = raw.get("limits", {})
        controller = raw.get("controller", {})
        ccswitch = raw.get("ccswitch", {})
        student_domain = raw.get("student_learning", {})
        model_provider_settings = raw.get("model_provider_settings", {})
        intake_imports = raw.get("intake_imports", {})
        review_workbench = raw.get("review_workbench", {})
        auth = raw.get("auth", {})
        configured_students = tuple(str(value) for value in raw.get("students", []))
        student_capabilities: dict[str, str] = {}
        for item in student_domain.get("profiles", []):
            profile_id = str(item.get("profile_id", ""))
            capability_env = str(item.get("capability_env", ""))
            if not profile_id or not capability_env:
                raise ConfigError(
                    "student_learning profiles require profile_id and capability_env"
                )
            capability = os.environ.get(capability_env, "")
            if not capability:
                raise ConfigError(
                    f"required student capability environment variable is missing: {capability_env}"
                )
            student_capabilities[profile_id] = capability
        if student_capabilities:
            configured_students = tuple(student_capabilities)
        config = cls(
            bind_host=str(network.get("bind_host", "127.0.0.1")),
            port=int(network.get("port", 8765)),
            max_request_bytes=int(limits.get("max_request_bytes", 8 * 1024 * 1024)),
            max_upload_bytes=int(limits.get("max_upload_bytes", 6 * 1024 * 1024)),
            mode=str(raw.get("mode", "controller")),
            state_root=resolve_path(str(raw.get("state_root", "private_state")))
            or base,
            shchem_root=resolve_path(str(raw.get("shchem_root", "../../sh-chem-db")))
            or base,
            overlay_root=resolve_path(str(raw.get("overlay_root", "overlay"))) or base,
            student_data_root=resolve_path(student_domain.get("data_root")),
            student_capabilities=student_capabilities,
            fixture_path=resolve_path(raw.get("fixture_path")),
            controller_script=resolve_path(controller.get("script")),
            controller_timeout_seconds=float(controller.get("timeout_seconds", 90)),
            ccswitch_enabled=bool(ccswitch.get("enabled", False)),
            ccswitch_base_url=str(ccswitch.get("base_url", "http://127.0.0.1:15721")),
            ccswitch_timeout_seconds=float(ccswitch.get("timeout_seconds", 5)),
            model_provider_metadata_root=resolve_path(
                model_provider_settings.get("metadata_root")
            ),
            intake_import_root=lexical_path(intake_imports.get("state_root")),
            review_workbench_root=resolve_path(review_workbench.get("state_root")),
            personal_auto_auth=bool(auth.get("personal_auto_auth", False)),
            principals=principals,
            students=configured_students,
            allowed_origins=tuple(
                str(value) for value in network.get("allowed_origins", [])
            ),
        )
        config.validate()
        return config

    def public_summary(self) -> dict[str, Any]:
        value = {
            "contract_version": CONTRACT_VERSION,
            "mode": self.mode,
            "bind_host": self.bind_host,
            "port": self.port,
            "request_limit_bytes": self.max_request_bytes,
            "upload_limit_bytes": self.max_upload_bytes,
            "ccswitch_enabled": self.ccswitch_enabled,
            "ccswitch_base_url": self.ccswitch_base_url
            if self.ccswitch_enabled
            else None,
        }
        if self.workbench_release_runtime is not None:
            value["serving_release"] = (
                self.workbench_release_runtime.serving.public_dict()
            )
        return value

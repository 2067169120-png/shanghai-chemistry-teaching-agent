from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .presentation_workbench import (
    PresentationToolchain,
    PresentationWorkbenchError,
    compose_deck_json,
    render_presentation_bundle,
    validate_deck_json,
    validate_presentation_input,
    write_output_manifest,
)

PRESENTATION_PROJECT_SCHEMA_VERSION = "shchem.presentation-project.v1"
PRESENTATION_OUTLINE_SCHEMA_VERSION = "shchem.presentation-outline-projection.v1"
PRESENTATION_VERSION_SCHEMA_VERSION = "shchem.presentation-version.v1"
PRESENTATION_JOB_SCHEMA_VERSION = "shchem.presentation-render-job.v1"

ARTIFACT_FILENAMES = {
    "deck_json": "deck.json",
    "pptx": "lesson_presentation.pptx",
    "qa_report": "qa_report.json",
    "preview_montage": "rendered_montage.png",
}
ARTIFACT_CONTENT_TYPES = {
    "deck_json": "application/json",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "qa_report": "application/json",
    "preview_montage": "image/png",
}

_PROJECT_ID = re.compile(r"PPTPRJ-[0-9a-f]{32}")
_VERSION_ID = re.compile(r"PPTVER-[0-9a-f]{64}")
_JOB_ID = re.compile(r"PPTJOB-[0-9a-f]{64}")
_OUTLINE_REVISION = re.compile(r"PPTOL-[0-9a-f]{32}")
_ATTEMPT_TOKEN = re.compile(r"PPTATT-[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "cancel_requested"})
_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})
_MAX_ASSET_BYTES = 32 * 1024 * 1024
_CLIENT_OUTPUT_PATH_KEYS = frozenset(
    {
        "artifactpath",
        "assetroot",
        "cwd",
        "destinationpath",
        "exportpath",
        "manifestpath",
        "outputdir",
        "outputdirectory",
        "outputpath",
        "outputroot",
        "pptxpath",
        "previewmontagepath",
        "qapath",
        "renderdir",
        "stateroot",
        "toolchain",
        "workdir",
    }
)


class _RenderCancelled(Exception):
    pass


class _StaleRenderAttempt(Exception):
    """Stop a worker whose persisted render-attempt lease is no longer current."""


class _InterprocessRLock:
    """A re-entrant process-local lock backed by one cross-process file lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    @staticmethod
    def _lock_file(stream: Any) -> None:
        deadline = time.monotonic() + 30.0
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise PresentationWorkbenchError(
                        "presentation_state_busy",
                        "PPT 状态正在被另一个本机进程更新，请稍后重试。",
                        503,
                    ) from exc
                time.sleep(0.01)

    @staticmethod
    def _unlock_file(stream: Any) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def acquire(self) -> bool:
        self._thread_lock.acquire()
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            return True
        stream = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.path.open("a+b")
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
            self._lock_file(stream)
            self._local.stream = stream
            self._local.depth = 1
            return True
        except Exception:
            if stream is not None:
                stream.close()
            self._thread_lock.release()
            raise

    def release(self) -> None:
        depth = getattr(self._local, "depth", 0)
        if depth <= 0:
            raise RuntimeError("cannot release an un-acquired presentation state lock")
        try:
            if depth == 1:
                stream = self._local.stream
                try:
                    self._unlock_file(stream)
                finally:
                    stream.close()
                    del self._local.stream
                    del self._local.depth
            else:
                self._local.depth = depth - 1
        finally:
            self._thread_lock.release()

    def __enter__(self) -> _InterprocessRLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()


_STATE_LOCKS_GUARD = threading.Lock()
_STATE_LOCKS: dict[str, _InterprocessRLock] = {}


def _state_lock(root: Path) -> _InterprocessRLock:
    key = os.path.normcase(str(root.resolve()))
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.get(key)
        if lock is None:
            lock = _InterprocessRLock(root / ".presentation-state.lock")
            _STATE_LOCKS[key] = lock
        return lock


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _json_clone(value: Any, *, code: str, message: str) -> Any:
    try:
        return json.loads(_canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise PresentationWorkbenchError(code, message, 400) from exc


def _record_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_json({key: item for key, item in value.items() if key != "record_sha256"})


def _version_digest(project_id: str, presentation_input: Any, deck_json: Any) -> str:
    return _sha256_json(
        {
            "project_id": project_id,
            "presentation_input": presentation_input,
            "deck_json": deck_json,
        }
    )


def _normalize_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _reject_client_output_paths(value: Any, *, location: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise PresentationWorkbenchError(
                    "presentation_request_invalid",
                    "PPT 请求字段必须使用字符串名称。",
                    400,
                )
            if _normalize_key(key) in _CLIENT_OUTPUT_PATH_KEYS:
                raise PresentationWorkbenchError(
                    "presentation_client_output_path_forbidden",
                    "PPT 输出目录和产物路径由本机任务服务固定，客户端不能指定。",
                    400,
                    details={"field": f"{location}.{key}"},
                )
            _reject_client_output_paths(item, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_client_output_paths(item, location=f"{location}[{index}]")


def _identity(owner_id: str, session_id: str) -> tuple[str, str, str]:
    if (
        not isinstance(owner_id, str)
        or not owner_id
        or len(owner_id) > 240
        or any(ord(character) < 32 for character in owner_id)
        or not isinstance(session_id, str)
        or not session_id
        or len(session_id) > 240
        or any(ord(character) < 32 for character in session_id)
    ):
        raise PresentationWorkbenchError(
            "presentation_owner_invalid",
            "当前教师会话身份不正确。",
            403,
        )
    owner_sha256 = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
    session_sha256 = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    namespace_key = hashlib.sha256(
        f"{owner_sha256}:{session_sha256}".encode("ascii")
    ).hexdigest()
    return owner_sha256, session_sha256, namespace_key


def _contained(root: Path, candidate: Path, *, code: str, message: str) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PresentationWorkbenchError(code, message, 409) from exc
    return resolved


def _atomic_write_json(root: Path, path: Path, value: Mapping[str, Any]) -> None:
    _contained(
        root,
        path,
        code="presentation_state_path_invalid",
        message="PPT 任务状态路径不正确。",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _contained(
        root,
        path.parent,
        code="presentation_state_path_invalid",
        message="PPT 任务状态目录不正确。",
    )
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(_canonical_json_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_write_bytes(root: Path, path: Path, value: bytes) -> None:
    _contained(
        root,
        path,
        code="presentation_asset_path_invalid",
        message="PPT 素材路径不正确。",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _contained(
        root,
        path.parent,
        code="presentation_asset_path_invalid",
        message="PPT 素材目录不正确。",
    )
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _image_info(data: bytes) -> tuple[str, str, list[int]]:
    if (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        and len(data) >= 24
        and data[12:16] == b"IHDR"
        and data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    ):
        width, height = struct.unpack(">II", data[16:24])
        if (
            0 < width <= 100_000
            and 0 < height <= 100_000
            and width * height <= 100_000_000
        ):
            return ".png", "image/png", [width, height]
    if data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                break
            length = int.from_bytes(data[index : index + 2], "big")
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            } and index + 7 <= len(data):
                width = int.from_bytes(data[index + 5 : index + 7], "big")
                height = int.from_bytes(data[index + 3 : index + 5], "big")
                if width > 0 and height > 0 and width * height <= 100_000_000:
                    return ".jpg", "image/jpeg", [width, height]
                break
            index += max(length, 2)
    raise PresentationWorkbenchError(
        "presentation_asset_format_invalid",
        "课件素材必须是可核验尺寸的 PNG 或 JPEG 位图。",
        415,
    )


def _validate_projection_binding(
    deck: Mapping[str, Any], presentation_input: Mapping[str, Any]
) -> None:
    """Keep editable projections bound to the frozen source input and assets."""

    if "input_digest" in deck and deck.get("input_digest") != _sha256_json(
        presentation_input
    ):
        raise PresentationWorkbenchError(
            "presentation_outline_input_mismatch",
            "PPT 页纲不属于这个项目的课程输入。",
            409,
        )
    theme = presentation_input.get("theme")
    input_assets = theme.get("assets", []) if isinstance(theme, Mapping) else []
    allowed: dict[str, Mapping[str, Any]] = {
        str(asset.get("asset_id")): asset
        for asset in input_assets
        if isinstance(asset, Mapping) and isinstance(asset.get("asset_id"), str)
    }
    slides = deck.get("slides", [])
    if not isinstance(slides, list):
        return
    for slide in slides:
        if not isinstance(slide, Mapping):
            continue
        for projected in slide.get("assets", []):
            if not isinstance(projected, Mapping):
                raise PresentationWorkbenchError(
                    "presentation_outline_asset_mismatch",
                    "PPT 页纲素材投影不正确。",
                    409,
                )
            source = allowed.get(str(projected.get("asset_id")))
            if source is None or any(
                projected.get(field) != source.get(field)
                for field in ("path", "sha256", "pixel_dimensions")
            ):
                raise PresentationWorkbenchError(
                    "presentation_outline_asset_mismatch",
                    "PPT 页纲只能引用课程输入中已冻结的素材。",
                    409,
                )


class PresentationJobManager:
    """Persistent, owner/session-bound presentation project and render queue.

    Project outline projections are mutable only through CAS. Frozen versions
    are content-addressed and immutable. Render results are always local
    candidate artifacts awaiting a real teacher's full-slide visual review.
    """

    def __init__(
        self,
        state_root: str | Path,
        toolchain: PresentationToolchain,
        *,
        max_workers: int = 1,
        input_validator: Callable[..., dict[str, Any]] = validate_presentation_input,
        deck_composer: Callable[..., dict[str, Any]] = compose_deck_json,
        deck_validator: Callable[[Mapping[str, Any]], dict[str, Any]] = validate_deck_json,
        renderer: Callable[..., dict[str, Any]] = render_presentation_bundle,
        manifest_writer: Callable[[Path], dict[str, Any]] = write_output_manifest,
    ) -> None:
        if type(max_workers) is not int or not 1 <= max_workers <= 4:
            raise ValueError("presentation max_workers must be between 1 and 4")
        if toolchain is None or not callable(getattr(toolchain, "validate", None)):
            raise PresentationWorkbenchError(
                "presentation_toolchain_required",
                "PPT 工具链必须由调用方显式提供。",
                503,
            )
        toolchain.validate()
        self.root = Path(state_root).expanduser().resolve()
        if self.root.exists() and not self.root.is_dir():
            raise PresentationWorkbenchError(
                "presentation_state_root_invalid",
                "PPT 状态目录不可用。",
                503,
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self.toolchain = toolchain
        self._input_validator = input_validator
        self._deck_composer = deck_composer
        self._deck_validator = deck_validator
        self._renderer = renderer
        self._manifest_writer = manifest_writer
        self._lock = _state_lock(self.root)
        self._closed = False
        self._cancel_events: dict[tuple[str, str], threading.Event] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="shchem-presentation-render",
        )
        self._recover_interrupted_jobs()

    def _ensure_open(self) -> None:
        if self._closed:
            raise PresentationWorkbenchError(
                "presentation_manager_closed",
                "PPT 后台任务服务正在关闭。",
                503,
            )

    def _namespace(
        self, owner_id: str, session_id: str, *, create: bool = False
    ) -> tuple[Path, str, str]:
        owner_sha256, session_sha256, key = _identity(owner_id, session_id)
        path = self.root / "tenants" / key
        _contained(
            self.root,
            path,
            code="presentation_owner_invalid",
            message="当前教师会话目录不正确。",
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path, owner_sha256, session_sha256

    def _project_root(self, namespace: Path, project_id: str) -> Path:
        if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
            raise PresentationWorkbenchError(
                "presentation_project_not_found", "找不到这个 PPT 项目。", 404
            )
        return _contained(
            namespace,
            namespace / "projects" / project_id,
            code="presentation_project_not_found",
            message="找不到这个 PPT 项目。",
        )

    def _version_path(self, project_root: Path, version_id: str) -> Path:
        if not isinstance(version_id, str) or _VERSION_ID.fullmatch(version_id) is None:
            raise PresentationWorkbenchError(
                "presentation_version_not_found", "找不到这个 PPT 版本。", 404
            )
        return _contained(
            project_root,
            project_root / "versions" / f"{version_id}.json",
            code="presentation_version_not_found",
            message="找不到这个 PPT 版本。",
        )

    def _job_root(self, namespace: Path, job_id: str) -> Path:
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            raise PresentationWorkbenchError(
                "presentation_job_not_found", "找不到这个 PPT 生成任务。", 404
            )
        return _contained(
            namespace,
            namespace / "jobs" / job_id,
            code="presentation_job_not_found",
            message="找不到这个 PPT 生成任务。",
        )

    def _load_record(
        self,
        path: Path,
        *,
        schema_version: str,
        not_found_code: str,
        not_found_message: str,
        corrupt_code: str,
        corrupt_message: str,
    ) -> dict[str, Any]:
        path = _contained(
            self.root,
            path,
            code=corrupt_code,
            message=corrupt_message,
        )
        if not path.is_file():
            raise PresentationWorkbenchError(not_found_code, not_found_message, 404)
        try:
            raw = path.read_bytes()
            if len(raw) > 64 * 1024 * 1024:
                raise ValueError("record too large")
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise PresentationWorkbenchError(corrupt_code, corrupt_message, 409) from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != schema_version
            or not isinstance(value.get("record_sha256"), str)
            or _SHA256.fullmatch(value["record_sha256"]) is None
            or value["record_sha256"] != _record_sha256(value)
        ):
            raise PresentationWorkbenchError(corrupt_code, corrupt_message, 409)
        return value

    def _save_record(self, path: Path, value: dict[str, Any]) -> None:
        value["record_sha256"] = _record_sha256(value)
        _atomic_write_json(self.root, path, value)

    @staticmethod
    def _public_project(value: Mapping[str, Any]) -> dict[str, Any]:
        return deepcopy(
            {
                key: item
                for key, item in value.items()
                if key not in {"owner_sha256", "session_sha256", "record_sha256"}
            }
        )

    @staticmethod
    def _public_outline(value: Mapping[str, Any]) -> dict[str, Any]:
        return deepcopy(
            {
                key: item
                for key, item in value.items()
                if key not in {"owner_sha256", "session_sha256", "record_sha256"}
            }
        )

    @staticmethod
    def _public_version(value: Mapping[str, Any]) -> dict[str, Any]:
        return deepcopy(
            {
                key: item
                for key, item in value.items()
                if key not in {"owner_sha256", "session_sha256", "record_sha256"}
            }
        )

    @staticmethod
    def _public_job(value: Mapping[str, Any]) -> dict[str, Any]:
        public = {
            key: item
            for key, item in value.items()
            if key
            not in {
                "owner_sha256",
                "session_sha256",
                "record_sha256",
                "attempt_token",
            }
        }
        public["candidate_only"] = True
        public["candidate_status"] = "candidate_only"
        public["teacher_review_status"] = "pending_teacher_review"
        public["publication_allowed"] = False
        public["retry_required"] = bool(
            value.get("status") in {"failed", "cancelled"} and value.get("retryable")
        )
        return deepcopy(public)

    def _load_project(
        self, namespace: Path, owner_sha256: str, session_sha256: str, project_id: str
    ) -> tuple[Path, dict[str, Any]]:
        project_root = self._project_root(namespace, project_id)
        value = self._load_record(
            project_root / "project.json",
            schema_version=PRESENTATION_PROJECT_SCHEMA_VERSION,
            not_found_code="presentation_project_not_found",
            not_found_message="找不到这个 PPT 项目。",
            corrupt_code="presentation_project_corrupt",
            corrupt_message="PPT 项目记录损坏，已停止读取。",
        )
        if (
            value.get("project_id") != project_id
            or value.get("owner_sha256") != owner_sha256
            or value.get("session_sha256") != session_sha256
        ):
            raise PresentationWorkbenchError(
                "presentation_project_not_found", "找不到这个 PPT 项目。", 404
            )
        return project_root, value

    def _load_outline(
        self, project_root: Path, project_id: str, owner_sha256: str, session_sha256: str
    ) -> dict[str, Any]:
        value = self._load_record(
            project_root / "outline.json",
            schema_version=PRESENTATION_OUTLINE_SCHEMA_VERSION,
            not_found_code="presentation_outline_not_found",
            not_found_message="这个 PPT 项目还没有页纲。",
            corrupt_code="presentation_outline_corrupt",
            corrupt_message="PPT 页纲记录损坏，已停止生成。",
        )
        if (
            value.get("project_id") != project_id
            or value.get("owner_sha256") != owner_sha256
            or value.get("session_sha256") != session_sha256
            or not isinstance(value.get("revision"), str)
            or _OUTLINE_REVISION.fullmatch(value["revision"]) is None
            or value.get("presentation_input_sha256")
            != _sha256_json(value.get("presentation_input"))
            or value.get("deck_json_sha256") != _sha256_json(value.get("deck_json"))
        ):
            raise PresentationWorkbenchError(
                "presentation_outline_corrupt", "PPT 页纲记录不一致，已停止生成。", 409
            )
        return value

    def _load_version(
        self,
        project_root: Path,
        project_id: str,
        version_id: str,
        owner_sha256: str,
        session_sha256: str,
    ) -> dict[str, Any]:
        value = self._load_record(
            self._version_path(project_root, version_id),
            schema_version=PRESENTATION_VERSION_SCHEMA_VERSION,
            not_found_code="presentation_version_not_found",
            not_found_message="找不到这个 PPT 版本。",
            corrupt_code="presentation_version_corrupt",
            corrupt_message="PPT 版本记录损坏，已停止生成。",
        )
        digest = _version_digest(
            project_id, value.get("presentation_input"), value.get("deck_json")
        )
        if (
            value.get("project_id") != project_id
            or value.get("version_id") != version_id
            or version_id != f"PPTVER-{digest}"
            or value.get("version_sha256") != digest
            or value.get("owner_sha256") != owner_sha256
            or value.get("session_sha256") != session_sha256
            or value.get("presentation_input_sha256")
            != _sha256_json(value.get("presentation_input"))
            or value.get("deck_json_sha256") != _sha256_json(value.get("deck_json"))
        ):
            raise PresentationWorkbenchError(
                "presentation_version_corrupt", "PPT 版本记录不一致，已停止生成。", 409
            )
        return value

    def _load_job(
        self, namespace: Path, owner_sha256: str, session_sha256: str, job_id: str
    ) -> tuple[Path, dict[str, Any]]:
        job_root = self._job_root(namespace, job_id)
        value = self._load_record(
            job_root / "job.json",
            schema_version=PRESENTATION_JOB_SCHEMA_VERSION,
            not_found_code="presentation_job_not_found",
            not_found_message="找不到这个 PPT 生成任务。",
            corrupt_code="presentation_job_corrupt",
            corrupt_message="PPT 任务记录损坏，已停止下载与重试。",
        )
        if (
            value.get("job_id") != job_id
            or value.get("owner_sha256") != owner_sha256
            or value.get("session_sha256") != session_sha256
            or value.get("status") not in _ACTIVE_JOB_STATUSES | _TERMINAL_JOB_STATUSES
            or type(value.get("attempt")) is not int
            or value["attempt"] < 1
            or (
                value.get("status") in _ACTIVE_JOB_STATUSES
                and (
                    not isinstance(value.get("attempt_token"), str)
                    or _ATTEMPT_TOKEN.fullmatch(value["attempt_token"]) is None
                )
            )
            or (
                value.get("attempt_token") is not None
                and (
                    not isinstance(value.get("attempt_token"), str)
                    or _ATTEMPT_TOKEN.fullmatch(value["attempt_token"]) is None
                )
            )
        ):
            raise PresentationWorkbenchError(
                "presentation_job_corrupt", "PPT 任务记录不一致，已停止下载与重试。", 409
            )
        return job_root, value

    def create_project(
        self, owner_id: str, session_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._ensure_open()
        if not isinstance(payload, Mapping) or not set(payload).issubset(
            {"title_zh", "description_zh"}
        ) or "title_zh" not in payload:
            raise PresentationWorkbenchError(
                "presentation_project_request_invalid",
                "新建 PPT 项目需要课时名称，可选填写说明。",
                400,
            )
        title = payload.get("title_zh")
        description = payload.get("description_zh", "")
        if (
            not isinstance(title, str)
            or not 1 <= len(title.strip()) <= 200
            or not isinstance(description, str)
            or len(description) > 2000
        ):
            raise PresentationWorkbenchError(
                "presentation_project_request_invalid", "PPT 项目名称或说明不正确。", 400
            )
        namespace, owner_sha256, session_sha256 = self._namespace(
            owner_id, session_id, create=True
        )
        with self._lock:
            self._ensure_open()
            project_id = f"PPTPRJ-{uuid.uuid4().hex}"
            project_root = self._project_root(namespace, project_id)
            project_root.mkdir(parents=True, exist_ok=False)
            (project_root / "assets").mkdir()
            now = _utc_now()
            value = {
                "schema_version": PRESENTATION_PROJECT_SCHEMA_VERSION,
                "project_id": project_id,
                "owner_sha256": owner_sha256,
                "session_sha256": session_sha256,
                "title_zh": title.strip(),
                "description_zh": description.strip(),
                "status": "draft",
                "candidate_only": True,
                "candidate_status": "candidate_only",
                "teacher_review_status": "pending_teacher_review",
                "publication_allowed": False,
                "created_at": now,
                "updated_at": now,
            }
            self._save_record(project_root / "project.json", value)
            return self._public_project(value)

    def get_project(self, owner_id: str, session_id: str, project_id: str) -> dict[str, Any]:
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            _, value = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
            return self._public_project(value)

    def store_asset(
        self,
        owner_id: str,
        session_id: str,
        project_id: str,
        *,
        filename: str,
        data: bytes,
    ) -> dict[str, Any]:
        """Atomically store one content-addressed PNG/JPEG before outline freeze."""

        self._ensure_open()
        if (
            not isinstance(filename, str)
            or not 1 <= len(filename) <= 180
            or filename != filename.strip()
            or filename in {".", ".."}
            or Path(filename).name != filename
            or any(character in filename for character in "/\\<>:\"|?*")
            or any(ord(character) < 32 for character in filename)
            or filename.endswith((".", " "))
        ):
            raise PresentationWorkbenchError(
                "presentation_asset_filename_invalid",
                "课件素材文件名必须是受限的单一文件名。",
                400,
            )
        if type(data) is not bytes or not 1 <= len(data) <= _MAX_ASSET_BYTES:
            raise PresentationWorkbenchError(
                "presentation_asset_size_invalid",
                "课件素材大小不在允许范围内。",
                413,
            )
        suffix, content_type, dimensions = _image_info(data)
        supplied_suffix = Path(filename).suffix.casefold()
        if (
            suffix == ".png"
            and supplied_suffix != ".png"
            or suffix == ".jpg"
            and supplied_suffix not in {".jpg", ".jpeg"}
        ):
            raise PresentationWorkbenchError(
                "presentation_asset_extension_mismatch",
                "课件素材扩展名与实际 PNG/JPEG 格式不一致。",
                415,
            )
        digest = _sha256_bytes(data)
        stored_name = f"{digest}{suffix}"
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            project_root, _ = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
            asset_root = _contained(
                project_root,
                project_root / "assets",
                code="presentation_asset_root_invalid",
                message="PPT 项目素材目录不正确。",
            )
            target = _contained(
                asset_root,
                asset_root / stored_name,
                code="presentation_asset_path_invalid",
                message="PPT 素材路径不正确。",
            )
            outline_path = project_root / "outline.json"
            outline_exists = outline_path.exists()
            if outline_exists:
                # Validate the freeze marker instead of trusting an arbitrary
                # filesystem entry named outline.json.
                self._load_outline(
                    project_root, project_id, owner_sha256, session_sha256
                )
            if target.exists():
                if not target.is_file():
                    raise PresentationWorkbenchError(
                        "presentation_asset_path_invalid",
                        "PPT 素材内容寻址路径不是普通文件。",
                        409,
                    )
                try:
                    observed = target.read_bytes()
                except OSError as exc:
                    raise PresentationWorkbenchError(
                        "presentation_asset_missing", "PPT 素材无法读取。", 409
                    ) from exc
                if observed != data or _sha256_bytes(observed) != digest:
                    raise PresentationWorkbenchError(
                        "presentation_asset_drift",
                        "同一内容地址下的课件素材已变化，已停止覆盖。",
                        409,
                    )
                idempotent_replay = True
            else:
                if outline_exists:
                    raise PresentationWorkbenchError(
                        "presentation_assets_frozen",
                        "页纲已经创建，项目素材集合已冻结；请新建项目后再添加素材。",
                        409,
                    )
                _atomic_write_bytes(asset_root, target, data)
                if not target.is_file() or target.read_bytes() != data:
                    raise PresentationWorkbenchError(
                        "presentation_asset_write_failed",
                        "PPT 素材原子写入后校验失败。",
                        503,
                    )
                idempotent_replay = False
            return {
                "project_id": project_id,
                "asset_id": f"PPTASSET-{digest[:32]}",
                "original_filename": filename,
                "path": stored_name,
                "sha256": digest,
                "size_bytes": len(data),
                "content_type": content_type,
                "pixel_dimensions": dimensions,
                "content_addressed": True,
                "immutable": True,
                "idempotent_replay": idempotent_replay,
            }

    def list_projects(self, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        if not namespace.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        with self._lock:
            for path in sorted((namespace / "projects").glob("PPTPRJ-*/project.json")):
                project_id = path.parent.name
                try:
                    _, value = self._load_project(
                        namespace, owner_sha256, session_sha256, project_id
                    )
                except PresentationWorkbenchError:
                    continue
                rows.append(self._public_project(value))
        return sorted(rows, key=lambda row: (row["created_at"], row["project_id"]))

    def create_outline(
        self,
        owner_id: str,
        session_id: str,
        project_id: str,
        presentation_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._ensure_open()
        if not isinstance(presentation_input, Mapping):
            raise PresentationWorkbenchError(
                "presentation_input_invalid", "PPT 课程输入必须是结构化对象。", 400
            )
        _reject_client_output_paths(presentation_input)
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            project_root, project = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
        asset_root = _contained(
            project_root,
            project_root / "assets",
            code="presentation_asset_root_invalid",
            message="PPT 项目素材目录不正确。",
        )
        validated_input = self._input_validator(presentation_input, asset_root=asset_root)
        validated_input = _json_clone(
            validated_input,
            code="presentation_input_invalid",
            message="PPT 课程输入无法安全保存。",
        )
        deck = self._deck_composer(validated_input, asset_root=asset_root)
        deck = self._deck_validator(deck)
        deck = _json_clone(
            deck,
            code="presentation_deck_invalid",
            message="PPT 页纲无法安全保存。",
        )
        _reject_client_output_paths(deck)
        _validate_projection_binding(deck, validated_input)
        now = _utc_now()
        with self._lock:
            project_root, project = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
            outline_path = project_root / "outline.json"
            if outline_path.is_file():
                existing = self._load_outline(
                    project_root, project_id, owner_sha256, session_sha256
                )
                if (
                    existing["presentation_input_sha256"] == _sha256_json(validated_input)
                    and existing["deck_json_sha256"] == _sha256_json(deck)
                ):
                    public = self._public_outline(existing)
                    public["idempotent_replay"] = True
                    return public
                raise PresentationWorkbenchError(
                    "presentation_outline_already_exists",
                    "这个项目已有页纲，请使用带修订号的更新操作。",
                    409,
                )
            value = {
                "schema_version": PRESENTATION_OUTLINE_SCHEMA_VERSION,
                "project_id": project_id,
                "owner_sha256": owner_sha256,
                "session_sha256": session_sha256,
                "revision": f"PPTOL-{uuid.uuid4().hex}",
                "presentation_input": validated_input,
                "presentation_input_sha256": _sha256_json(validated_input),
                "deck_json": deck,
                "deck_json_sha256": _sha256_json(deck),
                "candidate_only": True,
                "candidate_status": "candidate_only",
                "teacher_review_status": "pending_teacher_review",
                "publication_allowed": False,
                "created_at": now,
                "updated_at": now,
            }
            self._save_record(outline_path, value)
            project["updated_at"] = now
            self._save_record(project_root / "project.json", project)
            public = self._public_outline(value)
            public["idempotent_replay"] = False
            return public

    def get_outline(
        self, owner_id: str, session_id: str, project_id: str
    ) -> dict[str, Any]:
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            project_root, _ = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
            return self._public_outline(
                self._load_outline(project_root, project_id, owner_sha256, session_sha256)
            )

    def update_outline(
        self,
        owner_id: str,
        session_id: str,
        project_id: str,
        deck_json: Mapping[str, Any],
        *,
        expected_revision: str,
    ) -> dict[str, Any]:
        self._ensure_open()
        if (
            not isinstance(expected_revision, str)
            or _OUTLINE_REVISION.fullmatch(expected_revision) is None
            or not isinstance(deck_json, Mapping)
        ):
            raise PresentationWorkbenchError(
                "presentation_outline_update_invalid", "页纲更新请求不正确。", 400
            )
        _reject_client_output_paths(deck_json)
        normalized_deck = self._deck_validator(deck_json)
        normalized_deck = _json_clone(
            normalized_deck,
            code="presentation_deck_invalid",
            message="PPT 页纲无法安全保存。",
        )
        _reject_client_output_paths(normalized_deck)
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            project_root, project = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
            current = self._load_outline(
                project_root, project_id, owner_sha256, session_sha256
            )
            if current["revision"] != expected_revision:
                raise PresentationWorkbenchError(
                    "presentation_outline_revision_conflict",
                    "页纲已被其他操作更新，请刷新后再修改。",
                    409,
                    details={"current_revision": current["revision"]},
                )
            _validate_projection_binding(normalized_deck, current["presentation_input"])
            digest = _sha256_json(normalized_deck)
            if digest == current["deck_json_sha256"]:
                public = self._public_outline(current)
                public["idempotent_replay"] = True
                return public
            now = _utc_now()
            current["revision"] = f"PPTOL-{uuid.uuid4().hex}"
            current["deck_json"] = normalized_deck
            current["deck_json_sha256"] = digest
            current["updated_at"] = now
            self._save_record(project_root / "outline.json", current)
            project["updated_at"] = now
            self._save_record(project_root / "project.json", project)
            public = self._public_outline(current)
            public["idempotent_replay"] = False
            return public

    def create_version(
        self,
        owner_id: str,
        session_id: str,
        project_id: str,
        *,
        expected_outline_revision: str,
    ) -> dict[str, Any]:
        self._ensure_open()
        if (
            not isinstance(expected_outline_revision, str)
            or _OUTLINE_REVISION.fullmatch(expected_outline_revision) is None
        ):
            raise PresentationWorkbenchError(
                "presentation_version_request_invalid", "冻结版本需要当前页纲修订号。", 400
            )
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            project_root, _ = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
            outline = self._load_outline(
                project_root, project_id, owner_sha256, session_sha256
            )
            if outline["revision"] != expected_outline_revision:
                raise PresentationWorkbenchError(
                    "presentation_outline_revision_conflict",
                    "页纲已变化，请刷新后再冻结版本。",
                    409,
                    details={"current_revision": outline["revision"]},
                )
            asset_root = _contained(
                project_root,
                project_root / "assets",
                code="presentation_asset_root_invalid",
                message="PPT 项目素材目录不正确。",
            )
            self._input_validator(outline["presentation_input"], asset_root=asset_root)
            self._deck_validator(outline["deck_json"])
            _validate_projection_binding(
                outline["deck_json"], outline["presentation_input"]
            )
            digest = _version_digest(
                project_id, outline["presentation_input"], outline["deck_json"]
            )
            version_id = f"PPTVER-{digest}"
            version_path = self._version_path(project_root, version_id)
            if version_path.is_file():
                existing = self._load_version(
                    project_root,
                    project_id,
                    version_id,
                    owner_sha256,
                    session_sha256,
                )
                public = self._public_version(existing)
                public["idempotent_replay"] = True
                return public
            now = _utc_now()
            value = {
                "schema_version": PRESENTATION_VERSION_SCHEMA_VERSION,
                "project_id": project_id,
                "version_id": version_id,
                "version_sha256": digest,
                "owner_sha256": owner_sha256,
                "session_sha256": session_sha256,
                "source_outline_revision": outline["revision"],
                "presentation_input": deepcopy(outline["presentation_input"]),
                "presentation_input_sha256": outline["presentation_input_sha256"],
                "deck_json": deepcopy(outline["deck_json"]),
                "deck_json_sha256": outline["deck_json_sha256"],
                "immutable": True,
                "candidate_only": True,
                "candidate_status": "candidate_only",
                "teacher_review_status": "pending_teacher_review",
                "publication_allowed": False,
                "created_at": now,
            }
            self._save_record(version_path, value)
            public = self._public_version(value)
            public["idempotent_replay"] = False
            return public

    def get_version(
        self,
        owner_id: str,
        session_id: str,
        project_id: str,
        version_id: str,
    ) -> dict[str, Any]:
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            project_root, _ = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
            return self._public_version(
                self._load_version(
                    project_root,
                    project_id,
                    version_id,
                    owner_sha256,
                    session_sha256,
                )
            )

    def list_versions(
        self, owner_id: str, session_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        rows: list[dict[str, Any]] = []
        with self._lock:
            project_root, _ = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
            for path in sorted((project_root / "versions").glob("PPTVER-*.json")):
                try:
                    value = self._load_version(
                        project_root,
                        project_id,
                        path.stem,
                        owner_sha256,
                        session_sha256,
                    )
                except PresentationWorkbenchError:
                    continue
                rows.append(self._public_version(value))
        return sorted(rows, key=lambda row: (row["created_at"], row["version_id"]))

    @staticmethod
    def _render_job_id(owner_sha256: str, session_sha256: str, version_id: str) -> str:
        digest = hashlib.sha256(
            f"{owner_sha256}:{session_sha256}:{version_id}".encode("ascii")
        ).hexdigest()
        return f"PPTJOB-{digest}"

    def start_render(
        self,
        owner_id: str,
        session_id: str,
        project_id: str,
        version_id: str,
    ) -> dict[str, Any]:
        self._ensure_open()
        namespace, owner_sha256, session_sha256 = self._namespace(
            owner_id, session_id, create=True
        )
        with self._lock:
            self._ensure_open()
            project_root, _ = self._load_project(
                namespace, owner_sha256, session_sha256, project_id
            )
            version = self._load_version(
                project_root,
                project_id,
                version_id,
                owner_sha256,
                session_sha256,
            )
            job_id = self._render_job_id(owner_sha256, session_sha256, version_id)
            job_root = self._job_root(namespace, job_id)
            job_path = job_root / "job.json"
            if job_path.is_file():
                _, existing = self._load_job(
                    namespace, owner_sha256, session_sha256, job_id
                )
                if existing.get("project_id") != project_id:
                    raise PresentationWorkbenchError(
                        "presentation_job_corrupt",
                        "PPT 版本与已有任务不一致，已停止生成。",
                        409,
                    )
                public = self._public_job(existing)
                public["idempotent_replay"] = True
                return public
            job_root.mkdir(parents=True, exist_ok=False)
            now = _utc_now()
            attempt_token = f"PPTATT-{uuid.uuid4().hex}"
            job = {
                "schema_version": PRESENTATION_JOB_SCHEMA_VERSION,
                "job_id": job_id,
                "project_id": project_id,
                "version_id": version_id,
                "version_sha256": version["version_sha256"],
                "owner_sha256": owner_sha256,
                "session_sha256": session_sha256,
                "status": "queued",
                "attempt": 1,
                "attempt_token": attempt_token,
                "retryable": False,
                "progress": {
                    "stage": "queued",
                    "percent": 0,
                    "message_zh": "已进入本机 PPT 生成队列。",
                },
                "artifacts": [],
                "qa_status": None,
                "candidate_only": True,
                "candidate_status": "candidate_only",
                "teacher_review_status": "pending_teacher_review",
                "publication_allowed": False,
                "error": None,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "completed_at": None,
            }
            self._save_record(job_path, job)
            self._cancel_events[(job_id, attempt_token)] = threading.Event()
            self._executor.submit(
                self._run_render,
                namespace,
                owner_sha256,
                session_sha256,
                job_id,
                attempt_token,
            )
            public = self._public_job(job)
            public["idempotent_replay"] = False
            return public

    def _checkpoint(
        self,
        namespace: Path,
        owner_sha256: str,
        session_sha256: str,
        job_id: str,
        attempt_token: str,
        stage: str,
        percent: int,
        message_zh: str,
    ) -> dict[str, Any]:
        with self._lock:
            job_root, job = self._load_job(
                namespace, owner_sha256, session_sha256, job_id
            )
            if (
                job.get("attempt_token") != attempt_token
                or job.get("status") not in _ACTIVE_JOB_STATUSES
            ):
                raise _StaleRenderAttempt()
            event = self._cancel_events.get((job_id, attempt_token))
            if job["status"] == "cancel_requested" or (event is not None and event.is_set()):
                raise _RenderCancelled()
            job["status"] = "running"
            job["updated_at"] = _utc_now()
            if job.get("started_at") is None:
                job["started_at"] = job["updated_at"]
            job["progress"] = {
                "stage": stage,
                "percent": percent,
                "message_zh": message_zh,
            }
            self._save_record(job_root / "job.json", job)
            return job

    def _run_render(
        self,
        namespace: Path,
        owner_sha256: str,
        session_sha256: str,
        job_id: str,
        attempt_token: str,
    ) -> None:
        try:
            job = self._checkpoint(
                namespace,
                owner_sha256,
                session_sha256,
                job_id,
                attempt_token,
                "validating_version",
                10,
                "正在核对不可变版本和页纲。",
            )
            with self._lock:
                project_root, _ = self._load_project(
                    namespace,
                    owner_sha256,
                    session_sha256,
                    job["project_id"],
                )
                version = self._load_version(
                    project_root,
                    job["project_id"],
                    job["version_id"],
                    owner_sha256,
                    session_sha256,
                )
            if version["version_sha256"] != job["version_sha256"]:
                raise PresentationWorkbenchError(
                    "presentation_version_drift",
                    "PPT 不可变版本哈希不一致，已停止生成。",
                    409,
                )
            asset_root = _contained(
                project_root,
                project_root / "assets",
                code="presentation_asset_root_invalid",
                message="PPT 项目素材目录不正确。",
            )
            self._input_validator(version["presentation_input"], asset_root=asset_root)
            deck = self._deck_validator(version["deck_json"])
            _validate_projection_binding(deck, version["presentation_input"])
            if _sha256_json(deck) != version["deck_json_sha256"]:
                raise PresentationWorkbenchError(
                    "presentation_version_drift",
                    "PPT 页纲验证结果与冻结版本不一致。",
                    409,
                )
            job = self._checkpoint(
                namespace,
                owner_sha256,
                session_sha256,
                job_id,
                attempt_token,
                "rendering",
                35,
                "正在生成 PPTX、逐页预览和机器版面检查。",
            )
            with self._lock:
                job_root, current = self._load_job(
                    namespace, owner_sha256, session_sha256, job_id
                )
                if (
                    current.get("attempt_token") != attempt_token
                    or current.get("status") not in _ACTIVE_JOB_STATUSES
                ):
                    raise _StaleRenderAttempt()
                attempt = current["attempt"]
            output_root = job_root / f"attempt-{attempt:04d}" / "output"
            _contained(
                job_root,
                output_root,
                code="presentation_output_path_invalid",
                message="PPT 输出目录不正确。",
            )
            output_root.mkdir(parents=True, exist_ok=False)
            self._renderer(
                deck,
                asset_root=asset_root,
                output_dir=output_root,
                toolchain=self.toolchain,
                filename=ARTIFACT_FILENAMES["pptx"],
            )
            self._checkpoint(
                namespace,
                owner_sha256,
                session_sha256,
                job_id,
                attempt_token,
                "verifying_artifacts",
                82,
                "正在核对固定产物、路径和 SHA-256。",
            )
            manifest = self._manifest_writer(output_root)
            if (
                not isinstance(manifest, Mapping)
                or manifest.get("artifact_id") != deck.get("deck_id")
                or manifest.get("publication_allowed") is not False
                or manifest.get("teacher_confirmation_required") is not True
            ):
                raise PresentationWorkbenchError(
                    "presentation_output_manifest_invalid",
                    "PPT 输出清单未保持产物身份或候选边界。",
                    409,
                )
            artifacts = self._collect_artifacts(job_root, output_root, version)
            qa_path = output_root / ARTIFACT_FILENAMES["qa_report"]
            try:
                qa = json.loads(qa_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PresentationWorkbenchError(
                    "presentation_qa_report_invalid", "PPT 质量报告无法读取。", 409
                ) from exc
            if not isinstance(qa, Mapping) or qa.get("machine_checks_passed") is not True:
                raise PresentationWorkbenchError(
                    "presentation_machine_qa_failed",
                    "PPT 候选未通过机器版面检查，不能作为可下载结果。",
                    409,
                )
            if (
                qa.get("deck_id") != deck.get("deck_id")
                or qa.get("artifact_id") != deck.get("deck_id")
                or qa.get("publication_allowed") is not False
                or qa.get("teacher_confirmation_required") is not True
                or qa.get("qa_status")
                not in {
                    "pending_full_page_visual_review",
                    "pass_layout_candidate_teacher_review_pending",
                }
                or not isinstance(qa.get("chemistry_review"), Mapping)
                or qa["chemistry_review"].get("human_reviewed") is not False
            ):
                raise PresentationWorkbenchError(
                    "presentation_candidate_boundary_invalid",
                    "PPT 质量报告未通过机器检查或未保持教师复核边界。",
                    409,
                )
            now = _utc_now()
            with self._lock:
                job_root, current = self._load_job(
                    namespace, owner_sha256, session_sha256, job_id
                )
                if (
                    current.get("attempt_token") != attempt_token
                    or current.get("status") not in _ACTIVE_JOB_STATUSES
                ):
                    raise _StaleRenderAttempt()
                event = self._cancel_events.get((job_id, attempt_token))
                if current["status"] == "cancel_requested" or (
                    event is not None and event.is_set()
                ):
                    raise _RenderCancelled()
                current["status"] = "completed"
                current["retryable"] = False
                current["updated_at"] = now
                current["completed_at"] = now
                current["progress"] = {
                    "stage": "teacher_review_pending",
                    "percent": 100,
                    "message_zh": "候选 PPT 已生成，仍需教师逐页视觉复核。",
                }
                current["artifacts"] = artifacts
                current["qa_status"] = qa["qa_status"]
                current["error"] = None
                self._save_record(job_root / "job.json", current)
                self._cancel_events.pop((job_id, attempt_token), None)
        except _StaleRenderAttempt:
            return
        except _RenderCancelled:
            self._finish_cancelled(
                namespace,
                owner_sha256,
                session_sha256,
                job_id,
                attempt_token,
            )
        except Exception as exc:  # noqa: BLE001 - persist every worker failure as terminal state.
            self._finish_failed(
                namespace,
                owner_sha256,
                session_sha256,
                job_id,
                attempt_token,
                exc,
            )

    def _collect_artifacts(
        self, job_root: Path, output_root: Path, version: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        resolved_output = _contained(
            job_root,
            output_root,
            code="presentation_output_path_invalid",
            message="PPT 输出目录逃逸，已停止下载。",
        )
        artifacts: list[dict[str, Any]] = []
        for artifact_id, filename in ARTIFACT_FILENAMES.items():
            path = _contained(
                resolved_output,
                resolved_output / filename,
                code="presentation_artifact_path_invalid",
                message="PPT 产物路径逃逸，已停止下载。",
            )
            if not path.is_file():
                raise PresentationWorkbenchError(
                    "presentation_artifact_incomplete",
                    "PPT 生成未产生完整的四个固定文件。",
                    409,
                    details={"artifact_id": artifact_id},
                )
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise PresentationWorkbenchError(
                    "presentation_artifact_missing", "PPT 产物无法读取。", 409
                ) from exc
            if not data:
                raise PresentationWorkbenchError(
                    "presentation_artifact_empty", "PPT 产物为空，已停止下载。", 409
                )
            if artifact_id == "pptx" and not data.startswith(b"PK"):
                raise PresentationWorkbenchError(
                    "presentation_pptx_invalid", "PPTX 文件格式不正确。", 409
                )
            if artifact_id == "preview_montage" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise PresentationWorkbenchError(
                    "presentation_preview_invalid", "PPT 预览拼图格式不正确。", 409
                )
            if artifact_id == "deck_json":
                try:
                    deck = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise PresentationWorkbenchError(
                        "presentation_deck_artifact_invalid", "Deck JSON 产物不正确。", 409
                    ) from exc
                self._deck_validator(deck)
                if _sha256_json(deck) != version["deck_json_sha256"]:
                    raise PresentationWorkbenchError(
                        "presentation_deck_artifact_drift",
                        "Deck JSON 产物与冻结版本不一致。",
                        409,
                    )
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "filename": filename,
                    "content_type": ARTIFACT_CONTENT_TYPES[artifact_id],
                    "sha256": _sha256_bytes(data),
                    "size_bytes": len(data),
                }
            )
        return artifacts

    def _finish_cancelled(
        self,
        namespace: Path,
        owner_sha256: str,
        session_sha256: str,
        job_id: str,
        attempt_token: str,
    ) -> None:
        with self._lock:
            try:
                job_root, job = self._load_job(
                    namespace, owner_sha256, session_sha256, job_id
                )
            except PresentationWorkbenchError:
                return
            if (
                job.get("attempt_token") != attempt_token
                or job.get("status") not in _ACTIVE_JOB_STATUSES
            ):
                return
            now = _utc_now()
            job["status"] = "cancelled"
            job["retryable"] = True
            job["updated_at"] = now
            job["completed_at"] = now
            job["progress"] = {
                "stage": "cancelled",
                "percent": 100,
                "message_zh": "PPT 生成任务已取消，未接受任何候选产物。",
            }
            job["artifacts"] = []
            job["qa_status"] = None
            job["error"] = {
                "code": "presentation_render_cancelled",
                "message_zh": "PPT 生成任务已取消。",
                "http_status": 409,
                "details": {},
            }
            self._save_record(job_root / "job.json", job)
            self._cancel_events.pop((job_id, attempt_token), None)

    def _finish_failed(
        self,
        namespace: Path,
        owner_sha256: str,
        session_sha256: str,
        job_id: str,
        attempt_token: str,
        exc: Exception,
    ) -> None:
        with self._lock:
            try:
                job_root, job = self._load_job(
                    namespace, owner_sha256, session_sha256, job_id
                )
            except PresentationWorkbenchError:
                return
            if (
                job.get("attempt_token") != attempt_token
                or job.get("status") not in _ACTIVE_JOB_STATUSES
            ):
                return
            if job["status"] == "cancel_requested":
                self._finish_cancelled(
                    namespace,
                    owner_sha256,
                    session_sha256,
                    job_id,
                    attempt_token,
                )
                return
            code = getattr(exc, "code", "presentation_render_failed")
            status = getattr(exc, "status", getattr(exc, "http_status", 409))
            details = getattr(exc, "details", {})
            if isinstance(exc, PresentationWorkbenchError):
                message = str(exc)
            else:
                message = "PPT 生成失败；未接受部分产物，可显式重试。"
            now = _utc_now()
            job["status"] = "failed"
            job["retryable"] = True
            job["updated_at"] = now
            job["completed_at"] = now
            job["progress"] = {
                "stage": "failed",
                "percent": 100,
                "message_zh": message,
            }
            job["artifacts"] = []
            job["qa_status"] = None
            job["error"] = {
                "code": code if isinstance(code, str) and code else "presentation_render_failed",
                "message_zh": message,
                "http_status": status if isinstance(status, int) else 409,
                "details": dict(details) if isinstance(details, Mapping) else {},
            }
            self._save_record(job_root / "job.json", job)
            self._cancel_events.pop((job_id, attempt_token), None)

    def get_job(self, owner_id: str, session_id: str, job_id: str) -> dict[str, Any]:
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            _, job = self._load_job(namespace, owner_sha256, session_sha256, job_id)
            return self._public_job(job)

    def cancel_job(
        self, owner_id: str, session_id: str, job_id: str
    ) -> dict[str, Any]:
        self._ensure_open()
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            job_root, job = self._load_job(
                namespace, owner_sha256, session_sha256, job_id
            )
            if job["status"] == "cancelled":
                return self._public_job(job)
            if job["status"] not in {"queued", "running", "cancel_requested"}:
                raise PresentationWorkbenchError(
                    "presentation_job_not_cancellable",
                    "这个 PPT 任务已结束，不能取消。",
                    409,
                )
            job["status"] = "cancel_requested"
            job["updated_at"] = _utc_now()
            job["progress"] = {
                "stage": "cancel_requested",
                "percent": job.get("progress", {}).get("percent", 0),
                "message_zh": "已请求取消；当前安全阶段结束后停止。",
            }
            self._save_record(job_root / "job.json", job)
            attempt_token = job.get("attempt_token")
            if not isinstance(attempt_token, str):
                raise PresentationWorkbenchError(
                    "presentation_job_corrupt",
                    "PPT 任务缺少当前生成尝试标识，已停止取消。",
                    409,
                )
            self._cancel_events.setdefault(
                (job_id, attempt_token), threading.Event()
            ).set()
            return self._public_job(job)

    def retry_job(
        self, owner_id: str, session_id: str, job_id: str
    ) -> dict[str, Any]:
        self._ensure_open()
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            self._ensure_open()
            job_root, job = self._load_job(
                namespace, owner_sha256, session_sha256, job_id
            )
            if job["status"] not in {"failed", "cancelled"} or job.get("retryable") is not True:
                raise PresentationWorkbenchError(
                    "presentation_job_not_retryable",
                    "这个 PPT 任务当前不能重试。",
                    409,
                )
            job["status"] = "queued"
            job["attempt"] += 1
            attempt_token = f"PPTATT-{uuid.uuid4().hex}"
            job["attempt_token"] = attempt_token
            job["retryable"] = False
            job["updated_at"] = _utc_now()
            job["started_at"] = None
            job["completed_at"] = None
            job["progress"] = {
                "stage": "queued",
                "percent": 0,
                "message_zh": "已显式重试并进入本机 PPT 队列。",
            }
            job["artifacts"] = []
            job["qa_status"] = None
            job["error"] = None
            self._save_record(job_root / "job.json", job)
            self._cancel_events[(job_id, attempt_token)] = threading.Event()
            self._executor.submit(
                self._run_render,
                namespace,
                owner_sha256,
                session_sha256,
                job_id,
                attempt_token,
            )
            return self._public_job(job)

    def _artifact_file(
        self,
        owner_id: str,
        session_id: str,
        job_id: str,
        artifact_id: str,
    ) -> tuple[Path, str, Mapping[str, Any]]:
        if artifact_id not in ARTIFACT_FILENAMES:
            raise PresentationWorkbenchError(
                "presentation_artifact_invalid", "PPT 下载文件标识不正确。", 400
            )
        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            job_root, job = self._load_job(
                namespace, owner_sha256, session_sha256, job_id
            )
            if job["status"] != "completed":
                raise PresentationWorkbenchError(
                    "presentation_artifact_not_ready", "PPT 候选文件尚未生成完成。", 409
                )
            record = next(
                (
                    item
                    for item in job.get("artifacts", [])
                    if isinstance(item, Mapping) and item.get("artifact_id") == artifact_id
                ),
                None,
            )
            if not isinstance(record, Mapping):
                raise PresentationWorkbenchError(
                    "presentation_artifact_missing", "PPT 产物记录缺失。", 404
                )
            attempt = job["attempt"]
            output_root = _contained(
                job_root,
                job_root / f"attempt-{attempt:04d}" / "output",
                code="presentation_output_path_invalid",
                message="PPT 输出目录逃逸，已停止下载。",
            )
            path = _contained(
                output_root,
                output_root / ARTIFACT_FILENAMES[artifact_id],
                code="presentation_artifact_path_invalid",
                message="PPT 产物路径逃逸，已停止下载。",
            )
            if record.get("filename") != ARTIFACT_FILENAMES[artifact_id] or not path.is_file():
                raise PresentationWorkbenchError(
                    "presentation_artifact_missing", "PPT 产物不存在。", 404
                )
            return path, ARTIFACT_CONTENT_TYPES[artifact_id], record

    def artifact_path(
        self,
        owner_id: str,
        session_id: str,
        job_id: str,
        artifact_id: str,
    ) -> tuple[Path, str]:
        path, content_type, record = self._artifact_file(
            owner_id, session_id, job_id, artifact_id
        )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PresentationWorkbenchError(
                "presentation_artifact_missing", "PPT 产物无法读取。", 404
            ) from exc
        if len(data) != record.get("size_bytes") or _sha256_bytes(data) != record.get("sha256"):
            self._mark_artifact_drift(owner_id, session_id, job_id)
            raise PresentationWorkbenchError(
                "presentation_artifact_drift", "PPT 产物已变化，请重新生成。", 409
            )
        return path, content_type

    def artifact_bytes(
        self,
        owner_id: str,
        session_id: str,
        job_id: str,
        artifact_id: str,
    ) -> tuple[bytes, str, str]:
        path, content_type, record = self._artifact_file(
            owner_id, session_id, job_id, artifact_id
        )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PresentationWorkbenchError(
                "presentation_artifact_missing", "PPT 产物无法读取。", 404
            ) from exc
        if len(data) != record.get("size_bytes") or _sha256_bytes(data) != record.get("sha256"):
            self._mark_artifact_drift(owner_id, session_id, job_id)
            raise PresentationWorkbenchError(
                "presentation_artifact_drift", "PPT 产物已变化，请重新生成。", 409
            )
        return data, content_type, path.name

    def _mark_artifact_drift(
        self, owner_id: str, session_id: str, job_id: str
    ) -> None:
        """Turn a formerly completed but mutated bundle into an explicit retry state."""

        namespace, owner_sha256, session_sha256 = self._namespace(owner_id, session_id)
        with self._lock:
            try:
                job_root, job = self._load_job(
                    namespace, owner_sha256, session_sha256, job_id
                )
            except PresentationWorkbenchError:
                return
            if job.get("status") != "completed":
                return
            now = _utc_now()
            job["status"] = "failed"
            job["retryable"] = True
            job["updated_at"] = now
            job["completed_at"] = now
            job["progress"] = {
                "stage": "failed",
                "percent": 100,
                "message_zh": "PPT 产物哈希已变化，必须显式重试。",
            }
            job["artifacts"] = []
            job["qa_status"] = None
            job["error"] = {
                "code": "presentation_artifact_drift",
                "message_zh": "PPT 产物哈希已变化，必须显式重试。",
                "http_status": 409,
                "details": {"recovery_action": "retry_job"},
            }
            self._save_record(job_root / "job.json", job)

    def _recover_interrupted_jobs(self) -> None:
        with self._lock:
            for path in sorted(self.root.glob("tenants/*/jobs/PPTJOB-*/job.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if (
                    not isinstance(raw, dict)
                    or raw.get("schema_version") != PRESENTATION_JOB_SCHEMA_VERSION
                    or raw.get("status") not in _ACTIVE_JOB_STATUSES
                    or raw.get("record_sha256") != _record_sha256(raw)
                ):
                    continue
                now = _utc_now()
                if raw["status"] == "cancel_requested":
                    raw["status"] = "cancelled"
                    code = "presentation_render_cancelled_on_restart"
                    message = "服务重启时任务已处于取消流程，未接受任何产物。"
                else:
                    raw["status"] = "failed"
                    code = "presentation_render_interrupted"
                    message = "上次 PPT 生成被服务重启中断，可显式重试。"
                raw["retryable"] = True
                raw["updated_at"] = now
                raw["completed_at"] = now
                raw["progress"] = {
                    "stage": raw["status"],
                    "percent": 100,
                    "message_zh": message,
                }
                raw["artifacts"] = []
                raw["qa_status"] = None
                raw["error"] = {
                    "code": code,
                    "message_zh": message,
                    "http_status": 409,
                    "details": {"recovery_action": "retry_job"},
                }
                self._save_record(path, raw)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)


__all__ = [
    "ARTIFACT_CONTENT_TYPES",
    "ARTIFACT_FILENAMES",
    "PRESENTATION_JOB_SCHEMA_VERSION",
    "PRESENTATION_OUTLINE_SCHEMA_VERSION",
    "PRESENTATION_PROJECT_SCHEMA_VERSION",
    "PRESENTATION_VERSION_SCHEMA_VERSION",
    "PresentationJobManager",
    "PresentationWorkbenchError",
]

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Principal

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ALLOWED_UPLOAD_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/json": ".json",
    "text/csv": ".csv",
    "text/plain": ".txt",
}


class SecurityError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def validate_identifier(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value) or ".." in value:
        raise SecurityError("invalid_identifier", f"invalid {label}")
    return value


def safe_join(root: Path, *parts: str) -> Path:
    resolved_root = root.resolve()
    for part in parts:
        if not isinstance(part, str) or "\x00" in part:
            raise SecurityError("invalid_path", "invalid path component")
        candidate = Path(part)
        if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
            raise SecurityError(
                "path_not_allowed", "path leaves the configured root", 403
            )
    resolved = resolved_root.joinpath(*parts).resolve()
    try:
        common = Path(os.path.commonpath([str(resolved_root), str(resolved)]))
    except ValueError as exc:
        raise SecurityError(
            "path_not_allowed", "path leaves the configured root", 403
        ) from exc
    if common != resolved_root:
        raise SecurityError("path_not_allowed", "path leaves the configured root", 403)
    return resolved


def authenticate(authorization: str | None, principals: list[Principal]) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise SecurityError(
            "authentication_required", "Bearer authentication required", 401
        )
    token = authorization[7:]
    if len(token) < 16 or len(token) > 4096:
        raise SecurityError(
            "invalid_credentials", "invalid authentication credentials", 401
        )
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    for principal in principals:
        if hmac.compare_digest(digest, principal.token_sha256):
            return principal
    raise SecurityError(
        "invalid_credentials", "invalid authentication credentials", 401
    )


def authorize_student(principal: Principal, student_id: str) -> None:
    validate_identifier(student_id, "student_id")
    if not principal.can_access_student(student_id):
        raise SecurityError("student_scope_denied", "student access denied", 403)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def canonical_json_sha256(payload: Any) -> str:
    data = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def audit_student_ref(student_id: str | None) -> str | None:
    if not student_id:
        return None
    return hashlib.sha256(
        ("shchem-audit-v1:" + student_id).encode("utf-8")
    ).hexdigest()[:20]


@dataclass
class AuditLogger:
    path: Path

    def write(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        safe_event = dict(event)
        safe_event.pop("authorization", None)
        safe_event.pop("token", None)
        safe_event["student_ref"] = audit_student_ref(
            safe_event.pop("student_id", None)
        )
        line = json.dumps(
            safe_event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")

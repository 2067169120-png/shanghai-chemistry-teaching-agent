"""Atomic, local recovery of the unfinished editor, separate from formal drafts.

No generation validation is applied to incomplete text. Images are references
only: loading this file neither opens missing source documents nor calls a model.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

from .desktop_state import utc_now

SCHEMA = "shchem.preparation-recovery.v1"
MAX_BYTES = 16 * 1024 * 1024
_TEXT = ("topic", "audience", "lesson_route", "lesson_timing", "objective", "materials")
_ADVANCED = {"learning_and_experiment", "template_and_delivery", "homework_and_strategy"}
_IMAGE_FIELDS = {"asset_id", "sha256", "caption", "source", "purpose", "content_type", "width", "height"}


class PreparationRecoveryError(ValueError):
    pass


def validate_editor_payload(payload) -> dict:
    """Check the editor shape without trimming or discarding unfinished content."""
    allowed = {*_TEXT, "output_kind", "advanced", "image_assets", "image_input_mode", "lesson_design"}
    if not isinstance(payload, Mapping) or set(payload) - allowed:
        raise PreparationRecoveryError("恢复表单包含不支持的字段，原副本保留。")
    if payload.get("output_kind") not in {"joint", "ppt", "lesson_plan"}:
        raise PreparationRecoveryError("恢复表单的输出类型无法识别。")
    if any(not isinstance(payload.get(key), str) for key in _TEXT):
        raise PreparationRecoveryError("恢复表单的常用字段不完整。")
    timing = re.fullmatch(r"([1-9]\d*)课时×([1-9]\d*)分钟", payload["lesson_timing"])
    if not timing or int(timing[1]) > 12 or int(timing[2]) > 180:
        raise PreparationRecoveryError("恢复表单的课时设置不受支持。")
    advanced = payload.get("advanced")
    if not isinstance(advanced, Mapping) or set(advanced) != _ADVANCED or any(not isinstance(v, str) for v in advanced.values()):
        raise PreparationRecoveryError("恢复表单的精细设置不完整。")
    if payload.get("image_input_mode", "local_only") not in {"local_only", "vision"}:
        raise PreparationRecoveryError("恢复表单的图片用法无法识别。")
    images = payload.get("image_assets", [])
    if not isinstance(images, list) or len(images) > 12:
        raise PreparationRecoveryError("恢复表单的图片列表不受支持。")
    seen = set()
    for image in images:
        if (not isinstance(image, Mapping) or set(image) != _IMAGE_FIELDS
                or any(not isinstance(image[k], str) for k in _IMAGE_FIELDS - {"width", "height"})
                or any(type(image[k]) is not int or image[k] <= 0 for k in ("width", "height"))
                or image["content_type"] not in {"image/png", "image/jpeg", "image/webp"}
                or not image["asset_id"].startswith("IMG-") or not image["sha256"]
                or not all(image[k].strip() for k in ("caption", "source", "purpose"))
                or image["asset_id"] in seen):
            raise PreparationRecoveryError("恢复副本含无法完整还原的图片引用，未丢弃原信息。")
        seen.add(image["asset_id"])
    if "lesson_design" in payload:
        from .desktop_lesson_design import validate_design
        try:
            validate_design(payload["lesson_design"])
        except ValueError as exc:
            raise PreparationRecoveryError(str(exc)) from exc
    # Exact public form fields only; credentials and paths are not part of this contract.
    return deepcopy(dict(payload))


class PreparationRecoveryStore:
    def __init__(self, state_root: str | Path):
        self.root = Path(state_root).resolve() / "recovery"
        self.path = self.root / "preparation.v1.json"
        self._lock = threading.RLock()
        self._sequence = -1

    def load(self) -> dict | None:
        with self._lock:
            if not self.path.exists():
                return None
            try:
                if self.path.stat().st_size > MAX_BYTES:
                    raise PreparationRecoveryError("恢复副本过大，未覆盖原文件。")
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if (not isinstance(value, dict) or set(value) != {"schema_version", "saved_at", "dirty", "payload"}
                        or value.get("schema_version") != SCHEMA
                        or type(value.get("dirty")) is not bool or not isinstance(value.get("saved_at"), str)):
                    raise PreparationRecoveryError("恢复副本格式或版本不受支持，原文件保留。")
                value["payload"] = validate_editor_payload(value.get("payload"))
                return value
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise PreparationRecoveryError("恢复副本无法读取，原文件保留；请勿直接覆盖。") from exc

    def save(self, payload, *, dirty: bool, sequence: int) -> dict | None:
        """An older queued writer cannot replace a newer close-time snapshot."""
        payload = validate_editor_payload(payload)
        if type(dirty) is not bool or type(sequence) is not int or sequence < 0:
            raise PreparationRecoveryError("恢复保存参数不正确。")
        with self._lock:
            if sequence <= self._sequence:
                return None
            value = {"schema_version": SCHEMA, "saved_at": utc_now(), "dirty": dirty, "payload": payload}
            data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(data) > MAX_BYTES:
                raise PreparationRecoveryError("当前表单超过恢复副本容量，未截断；请正式保存或分课处理。")
            temporary = None
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix=".preparation-", suffix=".tmp", dir=self.root)
                temporary = Path(name)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                temporary = None
            except OSError as exc:
                raise PreparationRecoveryError("恢复副本保存失败，请检查磁盘空间和目录权限。") from exc
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            self._sequence = sequence
            return value

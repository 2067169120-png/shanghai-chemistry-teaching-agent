"""Source-bound, local-only crop overlays and frozen preview receipts.

No candidate, original image, answer text or chemistry-review gate is changed.
The registry lives on the facade, while each service call can be a new instance.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import sqlite3
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock

from PIL import Image

SCHEMA_VERSION = "shchem.personal-visual-crop.v1"
PREVIEW_POLICY = "source-bound-normalized-xywh-v1"
COPY_WARNING = (
    "已带入备课的文字和图片是独立副本，不会自动更新；请重新带入完整主题并核对旧副本。"
)
_INITIALIZATION_LOCK = RLock()
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class PersonalVisualCropError(ValueError):
    def __init__(self, message):
        self.code = "personal_visual_crop_invalid"
        self.message_zh = message
        super().__init__(message)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def validate_bbox(value, *, original=False):
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "width", "height"}:
        raise PersonalVisualCropError("裁剪范围须为归一化左上角x、y及宽、高。")
    if any(
        type(number) not in (int, float) or not math.isfinite(number)
        for number in value.values()
    ):
        raise PersonalVisualCropError("裁剪范围必须是有限数值，不能使用布尔值。")
    x, y, width, height = (value[field] for field in ("x", "y", "width", "height"))
    limit = 1.000001 if original else 1.0
    if not (
        0 <= x < 1
        and 0 <= y < 1
        and 0 < width <= 1
        and 0 < height <= 1
        and x + width <= limit
        and y + height <= limit
    ):
        raise PersonalVisualCropError("裁剪范围超出原页或为空；没有自动修正边界。")
    return {field: float(value[field]) for field in ("x", "y", "width", "height")}


def pixel_bounds(bbox, width, height, *, snap_roundoff=True):
    """Render normalized geometry; remove only division/multiplication roundoff."""

    def exact_integer(value):
        nearest = round(value)
        return nearest if snap_roundoff and abs(value - nearest) <= 1e-9 else value

    return {
        "left": math.floor(exact_integer(bbox["x"] * width)),
        "top": math.floor(exact_integer(bbox["y"] * height)),
        "right": math.ceil(exact_integer((bbox["x"] + bbox["width"]) * width)),
        "bottom": math.ceil(exact_integer((bbox["y"] + bbox["height"]) * height)),
    }


def crop_bytes(raw, bbox, width, height):
    bounds = pixel_bounds(bbox, width, height)
    with Image.open(io.BytesIO(raw)) as source:
        cropped = source.crop(
            tuple(bounds[field] for field in ("left", "top", "right", "bottom"))
        )
        stream = io.BytesIO()
        cropped.save(stream, "PNG")
    return stream.getvalue()


def source_binding(batch_id, snapshot, evidence, page):
    return {
        "batch_id": batch_id,
        "candidate_revision": snapshot["revision_token"],
        "candidate_sha256": snapshot["candidate_sha256"],
        "evidence_id": evidence["evidence_id"],
        "original_evidence_sha256": digest(evidence),
        **{
            field: evidence[field]
            for field in ("source_file_id", "source_role", "page_number", "page_sha256")
        },
        "source_sha256": page["source_sha256"],
    }


def _validate_binding(value):
    fields = {
        "batch_id",
        "candidate_revision",
        "candidate_sha256",
        "evidence_id",
        "original_evidence_sha256",
        "source_file_id",
        "source_role",
        "page_number",
        "page_sha256",
        "source_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise PersonalVisualCropError("裁剪来源绑定不完整。")
    if (
        not isinstance(value["batch_id"], str)
        or not re.fullmatch(r"DESKTOPBATCH-[a-f0-9]{32}", value["batch_id"])
        or not isinstance(value["candidate_revision"], str)
        or not re.fullmatch(r"rev_[0-9]{8}_[0-9a-f]{64}", value["candidate_revision"])
        or value["source_role"] not in {"question", "answer", "handout"}
        or type(value["page_number"]) is not int
        or value["page_number"] < 1
    ):
        raise PersonalVisualCropError("裁剪来源页或识别版本不正确。")
    for field in (
        "candidate_sha256",
        "original_evidence_sha256",
        "page_sha256",
        "source_sha256",
    ):
        if not isinstance(value[field], str) or not _HASH.fullmatch(value[field]):
            raise PersonalVisualCropError("裁剪来源摘要不正确。")
    for field in ("evidence_id", "source_file_id"):
        if not isinstance(value[field], str) or not _ID.fullmatch(value[field]):
            raise PersonalVisualCropError("裁剪证据定位不正确。")
    return deepcopy(value)


def _seal(value):
    row = deepcopy(value)
    row.pop("revision", None)
    row["revision"] = digest(row)
    return row


def validate_record(value):
    fields = {
        "schema_version",
        "binding",
        "original_bbox",
        "previous_bbox",
        "bbox",
        "edit_version",
        "previous_revision",
        "edit_origin",
        "teacher_reviewed",
        "revision",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise PersonalVisualCropError("个人裁剪记录不完整。")
    _validate_binding(value["binding"])
    validate_bbox(value["bbox"])
    validate_bbox(value["original_bbox"], original=True)
    validate_bbox(value["previous_bbox"], original=True)
    if (
        type(value["edit_version"]) is not int
        or value["edit_version"] < 1
        or value["edit_origin"] not in {"teacher", "ai_source_review"}
        or value["teacher_reviewed"] is not False
        or not isinstance(value["previous_revision"], str)
        or not _HASH.fullmatch(value["previous_revision"])
        or value != _seal(value)
    ):
        raise PersonalVisualCropError("个人裁剪记录与版本不一致。")
    return deepcopy(value)


def resolved_crop(binding, original_bbox, record=None):
    _validate_binding(binding)
    original = validate_bbox(original_bbox, original=True)
    stored = validate_record(record) if record else None
    bound = bool(stored and stored["binding"] == binding)
    bbox = deepcopy(stored["bbox"] if bound else original)
    revision = (
        stored["revision"]
        if bound
        else digest({"binding": binding, "bbox": original, "edit_version": 0})
    )
    return {
        "binding": deepcopy(binding),
        "original_bbox": original,
        "bbox": bbox,
        "crop_revision": revision,
        "stored_revision": stored["revision"] if stored else None,
        "active": bound,
        "stale": bool(stored and not bound),
    }


class CropPreviewRegistry:
    def __init__(self):
        self.lock = RLock()
        self.previews = {}

    def add(self, receipt):
        with self.lock:
            if len(self.previews) >= 32:
                raise PersonalVisualCropError("裁剪预览过多，请关闭不用的预览后重试。")
            preview_id = "PVCRPREVIEW-" + uuid.uuid4().hex
            frozen = deepcopy(
                dict(receipt, preview_id=preview_id, preview_policy=PREVIEW_POLICY)
            )
            frozen["preview_revision"] = digest(frozen)
            self.previews[preview_id] = frozen
            return deepcopy(frozen)

    def get(self, preview_id, preview_revision):
        with self.lock:
            row = self.previews.get(preview_id) if isinstance(preview_id, str) else None
            if row is None or row["preview_revision"] != preview_revision:
                raise PersonalVisualCropError("裁剪预览已失效，请重新预览后确认。")
            check = {
                key: value for key, value in row.items() if key != "preview_revision"
            }
            if (
                digest(check) != preview_revision
                or row["preview_policy"] != PREVIEW_POLICY
            ):
                raise PersonalVisualCropError("裁剪预览内容已经变化，请重新预览。")
            return deepcopy(row)

    def discard(self, preview_id):
        with self.lock:
            return (
                self.previews.pop(preview_id, None) is not None
                if isinstance(preview_id, str)
                else False
            )


def preview_registry(facade):
    with _INITIALIZATION_LOCK:
        registry = getattr(facade, "_personal_visual_crop_registry", None)
        if registry is None:
            registry = CropPreviewRegistry()
            facade._personal_visual_crop_registry = registry
        return registry


class PersonalVisualCropStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "personal-visual-crops.sqlite3"
        self._lock = RLock()

    def _check_path(self):
        for path in (self.path, self.root, *self.root.parents):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise PersonalVisualCropError("个人裁剪目录不能使用链接。")

    @contextmanager
    def _connect(self, *, readonly=False):
        self._check_path()
        if not readonly:
            self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro" if readonly else self.path,
            timeout=10,
            uri=readonly,
        )
        try:
            with connection:
                if readonly:
                    connection.execute("PRAGMA query_only=ON")
                else:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS overlays (batch_id TEXT NOT NULL,evidence_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(batch_id,evidence_id))"
                    )
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS history (batch_id TEXT NOT NULL,evidence_id TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(batch_id,evidence_id,version))"
                    )
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _batch(connection, batch_id):
        result = {}
        for evidence_id, payload in connection.execute(
            "SELECT evidence_id,payload FROM overlays WHERE batch_id=?", (batch_id,)
        ):
            row = validate_record(json.loads(payload))
            if (
                row["binding"]["batch_id"] != batch_id
                or row["binding"]["evidence_id"] != evidence_id
            ):
                raise PersonalVisualCropError("个人裁剪记录与批次证据不一致。")
            result[evidence_id] = row
        return result

    def get_batch(self, batch_id):
        self._check_path()
        if not self.path.is_file():
            return {}
        with self._lock, self._connect(readonly=True) as connection:
            return self._batch(connection, batch_id)

    @staticmethod
    def batch_revision(records):
        return digest({key: value["revision"] for key, value in records.items()})

    def history(self, batch_id, evidence_id):
        self._check_path()
        if not self.path.is_file():
            return []
        with self._lock, self._connect(readonly=True) as connection:
            rows = [
                validate_record(json.loads(row[0]))
                for row in connection.execute(
                    "SELECT payload FROM history WHERE batch_id=? AND evidence_id=? ORDER BY version",
                    (batch_id, evidence_id),
                )
            ]
            if any(
                row["binding"]["batch_id"] != batch_id
                or row["binding"]["evidence_id"] != evidence_id
                for row in rows
            ):
                raise PersonalVisualCropError("个人裁剪历史与证据不一致。")
            return rows

    def save(self, state, bbox, *, expected_batch_revision, edit_origin="teacher"):
        bbox = validate_bbox(bbox)
        binding = _validate_binding(state["binding"])
        if not isinstance(edit_origin, str) or edit_origin not in {
            "teacher",
            "ai_source_review",
        }:
            raise PersonalVisualCropError("裁剪修订来源不正确。")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            records = self._batch(connection, binding["batch_id"])
            if self.batch_revision(records) != expected_batch_revision:
                raise PersonalVisualCropError("个人裁剪已有新版本，请重新预览。")
            old = records.get(binding["evidence_id"])
            current = resolved_crop(binding, state["original_bbox"], old)
            if current != state:
                raise PersonalVisualCropError("裁剪来源或范围已经变化，请重新预览。")
            row = validate_record(
                _seal(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "binding": binding,
                        "original_bbox": state["original_bbox"],
                        "previous_bbox": state["bbox"],
                        "bbox": bbox,
                        "previous_revision": state["crop_revision"],
                        "edit_version": old["edit_version"] + 1 if old else 1,
                        "edit_origin": edit_origin,
                        "teacher_reviewed": False,
                    }
                )
            )
            payload = json.dumps(
                row, ensure_ascii=False, sort_keys=True, allow_nan=False
            )
            connection.execute(
                "INSERT OR REPLACE INTO overlays(batch_id,evidence_id,payload) VALUES (?,?,?)",
                (binding["batch_id"], binding["evidence_id"], payload),
            )
            connection.execute(
                "INSERT INTO history(batch_id,evidence_id,version,payload) VALUES (?,?,?,?)",
                (
                    binding["batch_id"],
                    binding["evidence_id"],
                    row["edit_version"],
                    payload,
                ),
            )
            return row

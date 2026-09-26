"""Private, source-bound checkpoints after extraction and crop review succeed.

One scope binds the complete source/page set, shard layout, model revision and
request policy. Preview is read-only. A caller must hold the batch's OS lock
from snapshot validation through candidate/descriptor persistence.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from .desktop_visual_import_v2 import SOURCE_ROLES, _SAFE_BATCH_ID
from .intake_batches_v2 import (
    IntakeBatchV2Error, VisualPixelPage, VisualShardRequest,
    _make_shards, _validate_fragment, intake_batch_visual_fragment_v2_schema,
)

# Bump whenever extraction or crop-review semantics change incompatibly.
REVIEW_CONTRACT = "shchem.desktop-reviewed-visual-shard.v1"
MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024


class VisualCheckpointError(IntakeBatchV2Error):
    def __init__(self, code: str = "visual_checkpoint_invalid") -> None:
        messages = {
            "visual_checkpoint_invalid": "已保存的图片分片无法核验；未重新发送，请检查本地记录。",
            "visual_checkpoint_stale": "图片处理进度已变化，请重新预览后继续。",
            "visual_checkpoint_scope_changed": "页面、模型或分片范围已变化，请重新预览。",
            "visual_checkpoint_write_failed": "图片分片未能安全保存，本次停止；已保存分片保留。",
        }
        super().__init__(code, messages[code], 409)
        self.message_zh = messages[code]


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _request_subject(request: VisualShardRequest) -> dict[str, Any]:
    return {
        "batch_id": request.batch_id, "shard_id": request.shard_id,
        "shard_index": request.shard_index, "source_role": request.source_role,
        "pages": [page.public_manifest() for page in request.pages],
    }


def preview_shards(pages: Sequence[VisualPixelPage], *, batch_id: str, max_pages_per_shard: int = 2) -> tuple[VisualShardRequest, ...]:
    """Use the core's role order and selected-source ordinals, including gaps.

Native-only files can leave gaps in desktop source_order. The visual core
renumbers the selected files within each role; the frozen preview must agree.
    """
    normalized = []
    for role in SOURCE_ROLES:
        source_ids = list(dict.fromkeys(page.source_file_id for page in pages if page.source_role == role))
        orders = {source_id: index for index, source_id in enumerate(source_ids, 1)}
        normalized.extend(replace(page, source_order=orders[page.source_file_id]) for page in pages if page.source_role == role)
    return tuple(_make_shards(normalized, batch_id=batch_id, max_pages_per_shard=max_pages_per_shard))


class VisualShardCheckpoints:
    def __init__(
        self, root: Path, *, batch_id: str, sources: Sequence[Mapping[str, Any]],
        profile_id: str, profile_revision: str, model_label: str,
        request_policy: Mapping[str, Any], requests: Sequence[VisualShardRequest],
    ) -> None:
        if _SAFE_BATCH_ID.fullmatch(batch_id) is None or not requests:
            raise VisualCheckpointError()
        self.requests = tuple(requests)
        self.scope = {
            "contract": REVIEW_CONTRACT, "batch_id": batch_id,
            "sources": deepcopy(list(sources)),
            "profile_id": profile_id, "profile_revision": profile_revision,
            "model_label": model_label, "request_policy": deepcopy(dict(request_policy)),
            "fragment_schema_sha256": _sha(intake_batch_visual_fragment_v2_schema()),
            "shards": [_request_subject(request) for request in requests],
        }
        self.scope_sha256 = _sha(self.scope)
        self.root = Path(root)
        self.batch_root = self.root / batch_id
        self.directory = self.batch_root / self.scope_sha256
        self._by_id = {request.shard_id: request for request in self.requests}
        if len(self._by_id) != len(self.requests) or any(request.batch_id != batch_id for request in requests):
            raise VisualCheckpointError()
        self._approved_records: dict[str, str | None] | None = None

    def _paths_safe(self) -> None:
        for path in (self.root, self.batch_root, self.directory):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise VisualCheckpointError()
        if not self.directory.resolve().is_relative_to(self.root.resolve()):
            raise VisualCheckpointError()

    def _read(self, path: Path) -> tuple[dict[str, Any], str] | None:
        self._paths_safe()
        if path.is_symlink():
            raise VisualCheckpointError()
        if not path.exists():
            return None
        try:
            with path.open("rb") as handle:
                raw = handle.read(MAX_CHECKPOINT_BYTES + 1)
            if len(raw) > MAX_CHECKPOINT_BYTES:
                raise ValueError("size")
            def unique_pairs(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate key")
                    value[key] = item
                return value
            value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")))
            if not isinstance(value, dict):
                raise ValueError("object")
            return value, hashlib.sha256(raw).hexdigest()
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise VisualCheckpointError() from exc

    def _path(self, request: VisualShardRequest) -> Path:
        expected = self._by_id.get(request.shard_id)
        if expected is None or _request_subject(request) != _request_subject(expected):
            raise VisualCheckpointError("visual_checkpoint_scope_changed")
        return self.directory / f"shard-{request.shard_index:04d}.json"

    def _record(self, request: VisualShardRequest) -> tuple[dict[str, Any], str] | None:
        stored = self._read(self._path(request))
        if stored is None:
            return None
        value, raw_sha = stored
        if (
            set(value) != {"contract", "scope_sha256", "request_sha256", "fragment_sha256", "fragment", "crop_review", "candidate_only", "human_reviewed"}
            or value["contract"] != REVIEW_CONTRACT or value["scope_sha256"] != self.scope_sha256
            or value["request_sha256"] != _sha(_request_subject(request))
            or value["crop_review"] != "machine_pass" or value["candidate_only"] is not True
            or value["human_reviewed"] is not False or not isinstance(value["fragment"], dict)
            or value["fragment_sha256"] != _sha(value["fragment"])
        ):
            raise VisualCheckpointError()
        try:
            fragment = _validate_fragment(value["fragment"], request=request)
        except (IntakeBatchV2Error, TypeError, ValueError, KeyError) as exc:
            raise VisualCheckpointError() from exc
        if not fragment["evidence"]:
            raise VisualCheckpointError()
        return fragment, raw_sha

    def inspect(self) -> dict[str, Any]:
        self._paths_safe()
        scope_record = self._read(self.directory / "scope.json")
        if scope_record is not None and scope_record[0] != self.scope:
            raise VisualCheckpointError()
        expected_names = {"scope.json", *(self._path(request).name for request in self.requests)}
        if self.directory.exists() and any(path.name not in expected_names for path in self.directory.glob("*.json")):
            raise VisualCheckpointError()
        records, complete = {}, []
        for request in self.requests:
            record = self._record(request)
            if record is not None and scope_record is None:
                raise VisualCheckpointError()
            records[request.shard_id] = record[1] if record else None
            if record is not None:
                complete.append(request.shard_id)
        other_scopes = self.batch_root.exists() and any(path.name != self.scope_sha256 and path.is_dir() for path in self.batch_root.iterdir())
        return {
            "scope_sha256": self.scope_sha256,
            "revision": _sha({"scope": self.scope_sha256, "records": records}),
            "records": records, "completed_shard_ids": complete,
            "total_shards": len(self.requests), "completed_shards": len(complete),
            "pending_shards": len(self.requests) - len(complete),
            "has_other_scopes": other_scopes,
        }

    def approve_snapshot(self, expected_revision: str) -> dict[str, Any]:
        snapshot = self.inspect()
        if snapshot["revision"] != expected_revision:
            raise VisualCheckpointError("visual_checkpoint_stale")
        self._approved_records = snapshot["records"]
        return snapshot

    def load(self, request: VisualShardRequest) -> Mapping[str, Any] | None:
        if self._approved_records is None:
            raise VisualCheckpointError("visual_checkpoint_stale")
        record = self._record(request)
        if (record[1] if record else None) != self._approved_records[request.shard_id]:
            raise VisualCheckpointError("visual_checkpoint_stale")
        return record[0] if record else None

    def _write_new(self, path: Path, value: Mapping[str, Any]) -> None:
        self._paths_safe()
        raw = _bytes(value)
        if len(raw) > MAX_CHECKPOINT_BYTES or path.exists() or path.is_symlink():
            raise VisualCheckpointError("visual_checkpoint_write_failed")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._paths_safe()
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists() or path.is_symlink():
                raise VisualCheckpointError("visual_checkpoint_stale")
            os.replace(temporary, path)
        except OSError as exc:
            raise VisualCheckpointError("visual_checkpoint_write_failed") from exc
        finally:
            if temporary.exists():
                temporary.unlink()

    def save_reviewed(self, request: VisualShardRequest, fragment: Mapping[str, Any]) -> None:
        if self.load(request) is not None:
            raise VisualCheckpointError("visual_checkpoint_stale")
        validated = _validate_fragment(fragment, request=request)
        if not validated["evidence"]:
            raise VisualCheckpointError()
        scope_path = self.directory / "scope.json"
        scope_record = self._read(scope_path)
        if scope_record is None:
            self._write_new(scope_path, self.scope)
        elif scope_record[0] != self.scope:
            raise VisualCheckpointError()
        self._write_new(self._path(request), {
            "contract": REVIEW_CONTRACT, "scope_sha256": self.scope_sha256,
            "request_sha256": _sha(_request_subject(request)), "fragment_sha256": _sha(validated),
            "fragment": validated, "crop_review": "machine_pass",
            "candidate_only": True, "human_reviewed": False,
        })


class ResumableVisualShardProvider:
    """Only the desktop two-stage adapter may create a new checkpoint."""

    def __init__(self, checkpoints: VisualShardCheckpoints, provider=None, *, progress_callback=None, should_cancel=None):
        from .desktop_visual_import_adapters import StructuredVisualShardProviderAdapter
        if provider is not None and not isinstance(provider, StructuredVisualShardProviderAdapter):
            raise TypeError("Only a crop-reviewed desktop adapter can persist observations")
        self.checkpoints, self.provider = checkpoints, provider
        self.progress_callback, self.should_cancel = progress_callback, should_cancel
        self.completed = 0

    def analyze_shard(self, request: VisualShardRequest) -> Mapping[str, Any]:
        if self.should_cancel is not None and self.should_cancel():
            raise IntakeBatchV2Error("cancelled", "视觉导入已取消；已保存分片保留。", 409)
        fragment = self.checkpoints.load(request)
        reused = fragment is not None
        if fragment is None:
            if self.provider is None:
                raise VisualCheckpointError("visual_checkpoint_stale")
            fragment = self.provider.analyze_shard(request)
            # analyze_shard returns only after all actual crop reviews pass.
            self.checkpoints.save_reviewed(request, fragment)
        self.completed += 1
        if self.progress_callback is not None:
            self.progress_callback({
                "stage": "visual_shard_completed", "batch_id": request.batch_id,
                "completed_shards": self.completed, "total_shards": len(self.checkpoints.requests),
                "reused": reused,
            })
        return fragment

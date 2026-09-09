"""Read-only access to saved native preparation drafts.

Preparation drafts are written by :meth:`DesktopWorkbenchFacade.create_preparation_draft`.
This service deliberately does not update the desktop state: callers can use it
to populate a reopen dialog without turning a task result, blueprint, or failed
generation record into a teacher-editable request.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .desktop_preparation import DesktopPreparationError, normalize_preparation_payload
from .desktop_state import DesktopStateStore

DRAFT_KIND = "preparation"
CONTRACT_VERSION = "lesson-blueprint/2.0.0"
_OUTPUT_KINDS = frozenset({"ppt", "lesson_plan", "joint"})
_CORE_FIELDS = (
    "topic",
    "audience",
    "lesson_route",
    "lesson_timing",
    "objective",
    "materials",
)


class PreparationDraftError(ValueError):
    """A safe, local error for reading a saved preparation draft."""

    def __init__(self, code: str, message_zh: str) -> None:
        self.code = code
        self.message_zh = message_zh
        super().__init__(message_zh)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PreparationDraftError(
            "preparation_draft_invalid", "备课草稿记录不是可安全读取的 JSON。"
        ) from exc


def _revision(record: Mapping[str, Any]) -> str:
    """Hash the complete persisted record, not only the reconstructed payload."""

    return hashlib.sha256(_canonical_bytes(record)).hexdigest()


class PreparationDraftService:
    """Read and validate persisted teacher preparation drafts without writes."""

    def __init__(self, state: DesktopStateStore) -> None:
        self.state = state

    @staticmethod
    def _invalid(
        message_zh: str = "备课草稿记录不完整或已损坏。",
    ) -> PreparationDraftError:
        return PreparationDraftError("preparation_draft_invalid", message_zh)

    @classmethod
    def _payload(cls, record: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(record, Mapping):
            raise cls._invalid()
        if (
            record.get("kind") != DRAFT_KIND
            or record.get("contract_version") != CONTRACT_VERSION
            or record.get("status") != "draft"
        ):
            raise cls._invalid("该记录不是可读回的备课草稿。")

        output_kind = record.get("output_kind")
        if not isinstance(output_kind, str) or output_kind not in _OUTPUT_KINDS:
            raise cls._invalid("备课草稿的输出类型不正确。")

        core_fields = record.get("core_fields")
        if not isinstance(core_fields, Mapping) or set(core_fields) != set(
            _CORE_FIELDS
        ):
            raise cls._invalid("备课草稿的六个常用字段不完整。")

        advanced = record.get("advanced")
        if not isinstance(advanced, Mapping):
            raise cls._invalid("备课草稿的精细设置字段不正确。")

        created_at = record.get("created_at")
        if not isinstance(created_at, str) or not created_at.strip():
            raise cls._invalid("备课草稿缺少保存时间。")

        payload = {
            "output_kind": output_kind,
            **{field: deepcopy(core_fields[field]) for field in _CORE_FIELDS},
            "advanced": deepcopy(dict(advanced)),
        }
        if "image_assets" in record:
            payload["image_assets"] = deepcopy(record["image_assets"])
        try:
            normalize_preparation_payload(payload)
        except DesktopPreparationError as exc:
            raise cls._invalid(exc.message_zh) from exc
        return payload

    def _record(self, draft_id: str) -> tuple[dict[str, Any], dict[str, Any], str]:
        if not isinstance(draft_id, str) or not draft_id:
            raise PreparationDraftError("preparation_draft_missing", "备课草稿不存在。")
        records = self.state.snapshot().get("drafts")
        if not isinstance(records, Mapping):
            raise PreparationDraftError("preparation_draft_missing", "备课草稿不存在。")
        record = records.get(draft_id)
        if not isinstance(record, dict):
            raise PreparationDraftError("preparation_draft_missing", "备课草稿不存在。")
        payload = self._payload(record)
        revision = _revision(record)
        return record, payload, revision

    def options(self) -> list[dict[str, Any]]:
        """Return up to 50 valid drafts, newest first, without writing state."""

        records = self.state.snapshot().get("drafts")
        if not isinstance(records, Mapping):
            return []
        valid: list[tuple[str, dict[str, Any], str]] = []
        for draft_id, record in records.items():
            if (
                not isinstance(draft_id, str)
                or not draft_id
                or not isinstance(record, dict)
            ):
                continue
            try:
                self._payload(record)
                revision = _revision(record)
            except PreparationDraftError:
                continue
            valid.append((draft_id, record, revision))

        valid.sort(
            key=lambda item: (str(item[1].get("created_at", "")), item[0]),
            reverse=True,
        )
        return [
            {
                "draft_id": draft_id,
                "revision": revision,
                "title": str(record["core_fields"]["topic"]),
                "created_at": record["created_at"],
            }
            for draft_id, record, revision in valid[:50]
        ]

    def load(self, draft_id: str, expected_revision: str) -> dict[str, Any]:
        """Load a draft only when its complete-record revision still matches."""

        if not isinstance(expected_revision, str) or not expected_revision:
            raise PreparationDraftError(
                "preparation_draft_stale", "请重新选择备课草稿版本。"
            )
        _record_value, payload, revision = self._record(draft_id)
        if revision != expected_revision:
            raise PreparationDraftError(
                "preparation_draft_stale",
                "备课草稿已发生变化，请重新选择最新版本。",
            )
        return {
            "draft_id": draft_id,
            "revision": revision,
            "payload": payload,
        }


__all__ = [
    "CONTRACT_VERSION",
    "DRAFT_KIND",
    "PreparationDraftError",
    "PreparationDraftService",
]

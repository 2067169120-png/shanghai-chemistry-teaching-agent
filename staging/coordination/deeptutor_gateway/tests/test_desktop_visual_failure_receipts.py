from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import integrations.deeptutor_shchem_v1.desktop_facade as facade_module
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopVisualImportReceipt,
    DesktopWorkbenchFacade,
)

_BATCH_ID = "DESKTOPBATCH-" + "a" * 32
_RAW_PROVIDER_DETAIL = "private detail https://provider.invalid/v1?api_key=secret"


def _source_manifest() -> dict[str, Any]:
    return {
        "source_file_id": "SRC-QUESTION-001",
        "role": "question",
        "order_index": 1,
        "group_id": "fixture-group",
        "filename": "question.png",
        "mime_type": "image/png",
        "size_bytes": 128,
        "source_sha256": hashlib.sha256(b"fixture-question").hexdigest(),
        "content_addressed": True,
    }


def _failed_result(code: str) -> SimpleNamespace:
    source = _source_manifest()
    plan = SimpleNamespace(
        source_import_states=(
            {
                "source_file_id": source["source_file_id"],
                "import_state": "visual_only_required",
            },
        ),
        sources=(source,),
        native_quick_count=0,
        visual_queue_count=1,
    )
    return SimpleNamespace(
        batch_id=_BATCH_ID,
        source_type="合成教师资料",
        plan=plan,
        visual_status="failed",
        blockers=(
            {
                "code": code,
                "message_zh": _RAW_PROVIDER_DETAIL,
            },
        ),
    )


@pytest.mark.parametrize(
    "code",
    (
        "provider_rejected",
        "invalid_credentials",
        "permission_denied",
        "rate_limited",
        "timeout",
        "provider_response_incomplete",
        "provider_response_too_large",
        "provider_output_invalid",
        "provider_output_evidence_invalid",
        "visual_provider_output_invalid",
        "visual_candidate_schema_invalid",
    ),
)
def test_failed_result_exposes_only_stable_teacher_guidance(code: str) -> None:
    receipt = DesktopWorkbenchFacade._visual_import_receipt_from_result(
        _failed_result(code)
    )

    assert isinstance(receipt, DesktopVisualImportReceipt)
    assert receipt.status == "failed"
    assert receipt.failure_codes == (code,)
    assert receipt.as_dict()["failure_codes"] == [code]
    assert "原因：" in receipt.message_zh
    assert "下一步：" in receipt.message_zh
    assert _RAW_PROVIDER_DETAIL not in receipt.message_zh
    assert "api_key" not in receipt.message_zh
    assert "provider.invalid" not in receipt.message_zh


def test_unknown_failure_code_is_collapsed_without_provider_text() -> None:
    receipt = DesktopWorkbenchFacade._visual_import_receipt_from_result(
        _failed_result("private_backend_failure_code")
    )

    assert receipt.failure_codes == ("unknown",)
    assert "具体失败原因无法安全确认" in receipt.message_zh
    serialized = json.dumps(receipt.as_dict(), ensure_ascii=False)
    assert "private_backend_failure_code" not in serialized
    assert _RAW_PROVIDER_DETAIL not in serialized


def test_saved_failed_receipt_recomputes_safe_message_and_round_trips_codes() -> None:
    original = DesktopWorkbenchFacade._visual_import_receipt_from_result(
        _failed_result("provider_rejected")
    )
    saved = {"receipt": {**original.as_dict(), "message_zh": _RAW_PROVIDER_DETAIL}}

    restored = DesktopWorkbenchFacade._visual_import_receipt_from_saved(saved)

    assert restored.failure_codes == ("provider_rejected",)
    assert restored.as_dict()["failure_codes"] == ["provider_rejected"]
    assert _RAW_PROVIDER_DETAIL not in restored.message_zh
    assert "原因：" in restored.message_zh


def test_unknown_saved_code_can_use_newly_recognized_bound_manifest_code() -> None:
    original = DesktopWorkbenchFacade._visual_import_receipt_from_result(
        _failed_result("private_backend_failure_code")
    )
    restored = DesktopWorkbenchFacade._visual_import_receipt_from_saved(
        {"receipt": original.as_dict()},
        fallback_failure_codes=("provider_output_evidence_invalid",),
    )
    assert restored.failure_codes == ("provider_output_evidence_invalid",)
    assert "页码或裁剪坐标" in restored.message_zh


class _SnapshotState:
    def __init__(self, drafts: dict[str, Any]) -> None:
        self._drafts = drafts

    def snapshot(self) -> dict[str, Any]:
        return {"drafts": self._drafts}


def _old_descriptor(*, source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    private_source = {
        **source,
        "archive_relative_path": f"sources/{source['source_sha256']}.png",
    }
    old_receipt = {
        "batch_id": _BATCH_ID,
        "source_type": "合成教师资料",
        "status": "failed",
        "visual_status": "failed",
        "source_count": 1,
        "native_quick_count": 0,
        "visual_queue_count": 1,
        "sources": [
            {
                "role": "question",
                "order_index": 1,
                "filename": "question.png",
                "import_state": "visual_only_required",
            }
        ],
        "message_zh": "旧版本失败提示",
        "candidate_only": True,
        "central_question_bank_write": False,
    }
    descriptor = {
        "schema_version": facade_module._VISUAL_IMPORT_DRAFT_SCHEMA,
        "kind": "desktop_visual_import_v2",
        "batch_id": _BATCH_ID,
        "source_type": "合成教师资料",
        "source_closure_sha256": facade_module._canonical_digest([private_source]),
        "sources": [private_source],
        "status": "failed",
        "visual_status": "failed",
        "receipt": old_receipt,
        "created_at": "2026-09-13T00:00:00Z",
    }
    manifest = {
        "batch_id": _BATCH_ID,
        "visual_status": "failed",
        "candidate_only": True,
        "central_question_bank_write": False,
        "visual_candidate": None,
        "visual_candidate_sha256": None,
        "plan": {"sources": [source]},
        "blockers": [
            {
                "code": "provider_rejected",
                "message_zh": _RAW_PROVIDER_DETAIL,
            }
        ],
    }
    return descriptor, manifest


def _facade_for_saved_descriptor(
    tmp_path: Path, descriptor: dict[str, Any]
) -> DesktopWorkbenchFacade:
    paths = SimpleNamespace(state_root=tmp_path)
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    facade.paths = paths  # type: ignore[assignment]
    facade._state = _SnapshotState(  # type: ignore[attr-defined]
        {facade_module._VISUAL_IMPORT_DRAFT_PREFIX + _BATCH_ID: descriptor}
    )
    return facade


def test_old_failed_descriptor_reads_code_from_source_bound_manifest(
    tmp_path: Path,
) -> None:
    source = _source_manifest()
    descriptor, manifest = _old_descriptor(source=source)
    manifest_path = tmp_path / "visual-import-v2" / "batches" / f"{_BATCH_ID}.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    facade = _facade_for_saved_descriptor(tmp_path, descriptor)

    receipts = facade.list_resumable_visual_import_batches()

    assert len(receipts) == 1
    assert receipts[0].failure_codes == ("provider_rejected",)
    assert "接口地址" in receipts[0].message_zh
    assert _RAW_PROVIDER_DETAIL not in receipts[0].message_zh
    assert "failure_codes" not in descriptor["receipt"]


def test_old_failed_descriptor_falls_back_unknown_when_manifest_source_changes(
    tmp_path: Path,
) -> None:
    source = _source_manifest()
    descriptor, manifest = _old_descriptor(source=source)
    changed_source = {**source, "filename": "other.png"}
    manifest["plan"] = {"sources": [changed_source]}
    manifest_path = tmp_path / "visual-import-v2" / "batches" / f"{_BATCH_ID}.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    facade = _facade_for_saved_descriptor(tmp_path, descriptor)

    receipts = facade.list_resumable_visual_import_batches()

    assert receipts[0].failure_codes == ("unknown",)
    assert "具体失败原因无法安全确认" in receipts[0].message_zh
    assert _RAW_PROVIDER_DETAIL not in receipts[0].message_zh


def test_old_failed_descriptor_manifest_read_is_bounded_and_fail_closed(
    tmp_path: Path,
) -> None:
    source = _source_manifest()
    descriptor, _manifest = _old_descriptor(source=source)
    manifest_path = tmp_path / "visual-import-v2" / "batches" / f"{_BATCH_ID}.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(b"{" + b"x" * (facade_module._VISUAL_FAILURE_MANIFEST_MAX_BYTES + 1))
    facade = _facade_for_saved_descriptor(tmp_path, descriptor)

    receipts = facade.list_resumable_visual_import_batches()

    assert receipts[0].failure_codes == ("unknown",)
    assert _RAW_PROVIDER_DETAIL not in receipts[0].message_zh

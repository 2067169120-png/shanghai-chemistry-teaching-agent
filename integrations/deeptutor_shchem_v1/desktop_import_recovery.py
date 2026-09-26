"""Local import progress projections and process-safe retry exclusion."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .desktop_visual_import_v2 import DesktopImportBridgeError, _SAFE_BATCH_ID


def native_failure_count(receipt: Mapping[str, Any] | None) -> int:
    if receipt is None:
        return 0
    count = receipt.get("documents_failed", 0)
    if type(count) is not int or count < 0:
        raise DesktopImportBridgeError("native_import_receipt_invalid", "文字导入结果不完整，请重新核对。", 409)
    return count


def native_rows(receipt: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if receipt is None or "documents" not in receipt:
        return []  # Legacy count-only receipts remain explicitly unavailable.
    rows = receipt["documents"]
    if not isinstance(rows, list):
        raise DesktopImportBridgeError("native_import_receipt_invalid", "逐文件导入结果不完整。", 409)
    seen: set[str] = set()
    result = []
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("source_file_id"), str) or not row["source_file_id"]
            or row["source_file_id"] in seen
            or row.get("status") not in {"completed", "failed"}
            or type(row.get("attempt_count")) is not int or row["attempt_count"] < 1
            or type(row.get("attempt_count_known", True)) is not bool
            or not isinstance(row.get("filename"), str)
        ):
            raise DesktopImportBridgeError("native_import_receipt_invalid", "逐文件导入结果无法核验。", 409)
        seen.add(row["source_file_id"])
        result.append(dict(row))
    if (
        len(rows) != receipt.get("documents_total")
        or sum(row["status"] == "failed" for row in rows) != native_failure_count(receipt)
        or sum(row["status"] == "completed" for row in rows) != receipt.get("documents_completed")
    ):
        raise DesktopImportBridgeError("native_import_receipt_invalid", "逐文件结果与汇总不一致。", 409)
    return result


@contextmanager
def import_batch_lock(root: Path, batch_id: str):
    """OS locks release on exit/crash; a leftover file is never a live lock."""
    if not isinstance(batch_id, str) or _SAFE_BATCH_ID.fullmatch(batch_id) is None:
        raise DesktopImportBridgeError("visual_import_batch_invalid", "导入批次标识不正确。", 409)
    locks = root / "locks"
    if locks.is_symlink():
        raise DesktopImportBridgeError("import_lock_invalid", "导入锁目录不安全。", 409)
    locks.mkdir(parents=True, exist_ok=True)
    path = locks / f"batch-{batch_id}.lock"
    if path.is_symlink():
        raise DesktopImportBridgeError("import_lock_invalid", "导入锁路径不安全。", 409)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DesktopImportBridgeError("import_batch_busy", "这批资料正在处理，请完成后再重试。", 409) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

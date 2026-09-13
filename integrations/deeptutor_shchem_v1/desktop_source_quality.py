"""Read-only source errata; never overwrite originals or imply teacher approval."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

QUALITY_PATH = "knowledge/lectures/source-quality-notes.json"


def source_quality_notes(workspace: Path) -> dict:
    path = workspace / QUALITY_PATH
    if not path.exists():
        return {}
    data = path.read_bytes()
    if len(data) > 2_000_000:
        raise ValueError("来源修订记录过大，未载入。")
    payload = json.loads(data)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("sources"), list):
        raise ValueError("来源修订记录格式不正确。")
    result = {}
    for source in payload["sources"]:
        digest = source["source_sha256"]
        if len(digest) != 64 or digest in result or not isinstance(source["issues"], list):
            raise ValueError("来源修订记录重复或摘要不正确。")
        for issue in source["issues"]:
            if not issue["block_indices"] or any(type(v) is not int or v < 1 for v in issue["block_indices"]):
                raise ValueError("来源修订记录缺少有效原文位置。")
            if not all(isinstance(issue[k], str) and issue[k] for k in ("id", "summary", "suggested_correction")):
                raise ValueError("来源修订记录内容不完整。")
        result[digest] = {**source, "notes_revision": hashlib.sha256(data).hexdigest()}
    return result


def apply_source_quality(item: dict, notes: dict) -> dict:
    """Attach visible evidence, and hold known flawed ranges out of use/export."""
    row = deepcopy(item)
    row["content_quality"] = {"status": "not_fully_reviewed", "human_reviewed": False, "issues": []}
    source = notes.get(row["source_sha256"])
    if not source:
        return row
    # A different extractor revision must not silently lose previously known errors.
    stale = row["source_revision"] != source["source_revision"]
    indices = {b["index"] for group in ("context_blocks", "question_blocks", "answer_blocks") for b in row[group]}
    issues = [deepcopy(issue) for issue in source["issues"] if stale or indices.intersection(issue["block_indices"])]
    row["content_quality"].update(notes_revision=source["notes_revision"], issues=issues)
    if issues:
        row["content_quality"]["status"] = "source_errata_pending_relocation" if stale else "known_source_issue_pending_revision"
        row["selection_ready"] = False
        row["export_ready"] = False
        row["warnings"] = list(row.get("warnings", [])) + [
            "原资料存在已记录的内容问题，本题仍可预览，暂不加入备课或导出。原文件未修改，以下为AI修订建议而非教师确认。",
            *(["原文解析版本变化，已知问题需重新定位。"] if stale else []),
            *[issue["summary"] + " 修订建议：" + issue["suggested_correction"] for issue in issues],
        ]
    return row

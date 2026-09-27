"""Explicit, offline reconstruction of checkpoint indexes from bound records.

The caller owns the batch OS lock. A durable fence blocks normal reads until
both indexes are committed. Old record files and exact metadata bytes survive.
No timestamps, providers, credentials or candidate-CAS history are consulted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid

from .desktop_visual_checkpoints import (
    ATTEMPT_CONTRACT, VisualCheckpointError, _bytes, _request_subject, _sha,
)

MAX_REPAIR_FILES = 1000
MAX_REPAIR_BYTES = 64 * 1024 * 1024
REPAIR_CONTRACT = "shchem.desktop-visual-index-repair.v1"
_METADATA = ("scope.json", "active-attempts.json", "repair-pending.json")


def _decode_metadata(raw):
    """Only unreadable JSON is a repairable index; ambiguity stays blocked."""
    if raw is None:
        return None

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(VisualCheckpointError("visual_checkpoint_repair_unsafe")))
    except (UnicodeError, json.JSONDecodeError):
        return None
    except (RecursionError, ValueError, TypeError) as exc:
        raise VisualCheckpointError("visual_checkpoint_repair_unsafe") from exc
    if not isinstance(value, dict):
        raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
    return value


def _summary(fragment):
    questions = [question for theme in fragment.get("theme_fragments", [])
                 for question in theme.get("printed_questions", [])]
    lines = [f"已有识别记录：{len(questions)} 个题目片段、{len(fragment['evidence'])} 处图像依据。"]
    for question in questions[:4]:
        text = str(question.get("stem") or "").strip()
        if text:
            lines.append("记录摘录：" + text[:500])
    if fragment.get("answer_candidates"):
        lines.append(f"另含 {len(fragment['answer_candidates'])} 条答案候选，仍待教师核对。")
    return "\n".join(lines)


def inspect_repair(checkpoints):
    checkpoints._paths_safe()
    if not checkpoints.directory.is_dir():
        raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
    fingerprints, metadata, records, options = {}, {}, {}, []
    count = total = 0

    def read(path):
        nonlocal count, total
        count += 1
        if count > MAX_REPAIR_FILES:
            raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
        raw = checkpoints._raw(path)
        total += len(raw) if raw else 0
        if total > MAX_REPAIR_BYTES:
            raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
        relative = path.relative_to(checkpoints.directory).as_posix()
        fingerprints[relative] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        return raw

    for name in _METADATA:
        metadata[name] = read(checkpoints.directory / name)
    scope = _decode_metadata(metadata["scope.json"])
    active = _decode_metadata(metadata["active-attempts.json"])
    _decode_metadata(metadata["repair-pending.json"])
    if scope is not None and scope != checkpoints.scope:
        raise VisualCheckpointError("visual_checkpoint_scope_changed")
    if active is not None:
        checkpoints._attempts()  # Reject foreign scopes and unsafe path selectors.
    allowed = {f"shard-{request.shard_index:04d}.json": request for request in checkpoints.requests}
    record_paths = []
    def entries(directory):
        nonlocal count
        values = []
        for path in directory.iterdir():
            count += 1
            if count > MAX_REPAIR_FILES:
                raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
            checkpoints._safe_path(path)
            values.append(path)
        return sorted(values)

    for path in entries(checkpoints.directory):
        checkpoints._safe_path(path)
        if path.name in _METADATA:
            continue
        if path.name in {"attempts", "repairs"}:
            if not path.is_dir():
                raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
            continue
        if path.name not in allowed or not path.is_file():
            raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
        record_paths.append(path)
    attempts = checkpoints.directory / "attempts"
    if attempts.exists():
        for directory in entries(attempts):
            if re.fullmatch(r"[0-9a-f]{32}", directory.name) is None:
                raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
            checkpoints._safe_path(directory)
            if not directory.is_dir():
                raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
            for path in entries(directory):
                checkpoints._safe_path(path)
                if not path.is_file():
                    raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
                if path.name == "previous-state.json":
                    _decode_metadata(read(path))
                elif path.name in allowed:
                    record_paths.append(path)
                else:
                    raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
    # Backups never contribute candidates or CAS identity. Still reject links,
    # nested arbitrary directories and excessive history before any mutation.
    repairs = checkpoints.directory / "repairs"
    if repairs.exists():
        for directory in entries(repairs):
            if not directory.is_dir() or re.fullmatch(r"[0-9a-f]{32}", directory.name) is None:
                raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
            for path in entries(directory):
                if path.name == "staged" and path.is_dir():
                    children = entries(path)
                else:
                    children = [path]
                for child in children:
                    if not child.is_file() or child.stat().st_size > 16 * 1024 * 1024:
                        raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
    issues = []
    if scope is None:
        issues.append("scope_missing" if metadata["scope.json"] is None else "scope_unreadable")
    if metadata["active-attempts.json"] is not None and active is None:
        issues.append("active_unreadable")
    elif metadata["active-attempts.json"] is None and any(path.parent != checkpoints.directory for path in record_paths):
        issues.append("active_missing")
    if metadata["repair-pending.json"] is not None:
        issues.append("repair_incomplete")
    if not issues:
        raise VisualCheckpointError("visual_checkpoint_repair_unsafe")
    seen, invalid, unavailable_reasons = set(), set(), {}
    for path in record_paths:
        raw = read(path)
        if raw is None:
            raise VisualCheckpointError("visual_checkpoint_stale")
        request = allowed[path.name]
        decoded = _decode_metadata(raw)  # Duplicate keys and excessive nesting never become repair options.
        try:
            fragment, raw_sha = checkpoints._record(request, raw=raw)
        except VisualCheckpointError:
            invalid.add(request.shard_id)
            if decoded is None:
                reason = "保存记录损坏，无法读取；这组不能从该记录恢复。"
            elif (decoded.get("scope_sha256") != checkpoints.scope_sha256
                  or decoded.get("request_sha256") != _sha(_request_subject(request))):
                reason = "记录与当前来源、页面或处理条件不一致；请先核对来源，这组不能本机恢复。"
            else:
                reason = "记录内容或图像复核状态未通过核验；这组不能从该记录恢复。"
            unavailable_reasons[request.shard_id] = reason
            continue
        relative = path.relative_to(checkpoints.directory).as_posix()
        identity = (request.shard_id, _sha(fragment))
        records[relative] = raw
        if identity in seen:
            continue  # Equal validated fragment content is one choice; no recency heuristic.
        seen.add(identity)
        options.append({
            "option_id": _sha({"path": relative, "sha256": raw_sha, "scope": checkpoints.scope_sha256}),
            "shard_id": request.shard_id, "shard_index": request.shard_index,
            "record_path": relative, "record_sha256": raw_sha,
            "summary": _summary(fragment),
        })
    options.sort(key=lambda item: (item["shard_index"], item["record_path"]))
    versions = {}
    for option in options:
        versions[option["shard_id"]] = versions.get(option["shard_id"], 0) + 1
        option["version"] = versions[option["shard_id"]]
    return {
        "revision": _sha({"scope": checkpoints.scope_sha256, "files": fingerprints}),
        "fingerprints": fingerprints, "metadata": metadata, "record_bytes": records,
        "issues": issues, "options": options, "invalid_shard_ids": sorted(invalid),
        "unavailable_reasons": unavailable_reasons,
    }


def validate_options(snapshot, option_ids):
    choices = {item["option_id"]: item for item in snapshot["options"]}
    if (not isinstance(option_ids, (tuple, list)) or not option_ids
            or any(not isinstance(item, str) for item in option_ids)
            or len(set(option_ids)) != len(option_ids) or not set(option_ids) <= choices.keys()):
        raise VisualCheckpointError("visual_checkpoint_repair_selection")
    selected = [choices[identifier] for identifier in sorted(option_ids)]
    if len({item["shard_id"] for item in selected}) != len(selected):
        raise VisualCheckpointError("visual_checkpoint_repair_selection")
    return selected


def _write_bytes(checkpoints, path, raw):
    checkpoints._safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoints._safe_path(path)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def repair_metadata(checkpoints, expected_revision, option_ids, *, should_cancel=None, revalidate=None):
    """Commit after confirmation; cancellation is honored before the write boundary.

    No cancellation is polled during the short fenced metadata commit. A failed
    commit retains its fence and immutable backups for a fresh repair preview.
    """
    def cancelled():
        if should_cancel is not None and should_cancel():
            raise VisualCheckpointError("visual_checkpoint_repair_cancelled")

    cancelled()
    snapshot = inspect_repair(checkpoints)
    if snapshot["revision"] != expected_revision:
        raise VisualCheckpointError("visual_checkpoint_stale")
    selected = validate_options(snapshot, option_ids)
    if revalidate is not None:
        revalidate()
    cancelled()
    if inspect_repair(checkpoints)["revision"] != expected_revision:
        raise VisualCheckpointError("visual_checkpoint_stale")
    cancelled()
    repair_id = uuid.uuid4().hex
    history = checkpoints.directory / "repairs" / repair_id
    attempt = checkpoints.directory / "attempts" / repair_id
    fence = checkpoints.directory / "repair-pending.json"
    active = {request.shard_id: repair_id for request in checkpoints.requests}
    replacements = {
        "active-attempts.json": _bytes({"contract": ATTEMPT_CONTRACT, "scope_sha256": checkpoints.scope_sha256, "active": active}),
        "scope.json": _bytes(checkpoints.scope),
    }
    try:
        for name, raw in snapshot["metadata"].items():
            if raw is not None:
                _write_bytes(checkpoints, history / (name + ".before"), raw)
        checkpoints._write_new(history / "receipt.json", {
            "contract": REPAIR_CONTRACT, "previous_revision": expected_revision,
            "scope_sha256": checkpoints.scope_sha256, "selected_options": list(option_ids),
            "previous_files": snapshot["fingerprints"], "active": active,
        })
        for option in selected:
            filename = f"shard-{option['shard_index']:04d}.json"
            _write_bytes(checkpoints, history / "staged" / filename, snapshot["record_bytes"][option["record_path"]])
        for name, raw in replacements.items():
            _write_bytes(checkpoints, history / (name + ".next"), raw)
        fence_bytes = _bytes({"contract": REPAIR_CONTRACT, "repair_id": repair_id,
                              "previous_revision": expected_revision, "scope_sha256": checkpoints.scope_sha256})
        _write_bytes(checkpoints, history / "fence.next", fence_bytes)
        # All source/model/page/rule and saved-byte checks run again at commit.
        if revalidate is not None:
            revalidate()
        if inspect_repair(checkpoints)["revision"] != expected_revision:
            raise VisualCheckpointError("visual_checkpoint_stale")
        checkpoints._safe_path(fence)
        os.replace(history / "fence.next", fence)
        checkpoints._safe_path(attempt)
        attempt.parent.mkdir(parents=True, exist_ok=True)
        checkpoints._safe_path(attempt)
        if attempt.exists():
            raise VisualCheckpointError("visual_checkpoint_stale")
        os.replace(history / "staged", attempt)
        # The fence remains until both replacements and their reread succeed.
        for name in ("active-attempts.json", "scope.json"):
            path = checkpoints.directory / name
            checkpoints._safe_path(path)
            os.replace(history / (name + ".next"), path)
        if any(checkpoints._raw(checkpoints.directory / name) != raw for name, raw in replacements.items()):
            raise VisualCheckpointError("visual_checkpoint_repair_incomplete")
        for option in selected:
            path = attempt / f"shard-{option['shard_index']:04d}.json"
            if hashlib.sha256(checkpoints._raw(path)).hexdigest() != option["record_sha256"]:
                raise VisualCheckpointError("visual_checkpoint_repair_incomplete")
        checkpoints._safe_path(fence)
        if checkpoints._raw(fence) != fence_bytes:
            raise VisualCheckpointError("visual_checkpoint_stale")
        fence.unlink()
    except OSError as exc:
        raise VisualCheckpointError("visual_checkpoint_write_failed") from exc
    return checkpoints.inspect(diagnostics=True)

"""Source-bound current-session label candidates, without provider invocation.

This service fills missing labels or explicitly rechecks automatic labels.
A candidate is never a teacher confirmation, and a valid quote is evidence of
source grounding, not a claim that an automatic chemistry judgement has been
independently verified.
The public preview and receipt contain metadata only, never question text.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from jsonschema import Draft202012Validator

from .desktop_word_question_attributes import (
    WordQuestionAttributeError,
    _seal,
    automatic_tags_protected,
    validate_attributes,
)
from .desktop_word_semantic_tags import OUTPUT_SCHEMA, merge_response

CANDIDATE_SCHEMA_VERSION = "shchem.offline-word-label-candidates.v1"
PLAN_SCHEMA_VERSION = "shchem.offline-word-label-plan.v2"
RECEIPT_SCHEMA_VERSION = "shchem.offline-word-label-receipt.v2"
RULE_REVISION = "codex-offline-reviewed-20260926-v1"
RECHECK_RULE_REVISION = "codex-offline-recheck-20260927-v1"
LABEL_MODES = ("missing_only", "recheck_automatic")
READING_POLICY_REVISIONS = {
    "missing_only": "complete-question-text-v1",
    "recheck_automatic": "complete-question-answer-text-v2",
}
PROVENANCE = "codex_current_session"

_ANSWER_LABEL = re.compile(
    r"【\s*(?:参考答案|答案|解析|详解|解答|分析)\s*】|"
    r"^\s*(?:参考答案|答案|解析|详解|解答|分析)\s*[:：]"
)
_ANSWER_PART_NUMBER = re.compile(r"^(?:[（(]\s*\d+\s*[）)]\s*)+")
_TABLE_DISPLAY_LINE = re.compile(
    r"【表格(?:开始：按原行列顺序(?:；未标合并即未合并)?|结束)】|"
    r"〔第\d+行(?:·第\d+(?:—\d+)?列"
    r"(?:；(?:横向合并(?:\d+列)?|纵向合并(?:起点|续接上方)|原纵向合并标记：[^〕\r\n]*))*"
    r"|：(?:行首省略\d+列(?:；行尾省略\d+列)?|行尾省略\d+列|无单元格))〕"
)


def _object(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_TEXT = {"type": "string", "minLength": 1, "maxLength": 256}
_SHA256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
CANDIDATE_SCHEMA = _object(
    {
        "schema_version": {"const": CANDIDATE_SCHEMA_VERSION},
        "provenance": {"const": PROVENANCE},
        "candidate_only": {"const": True, "type": "boolean"},
        "human_review": {"const": False, "type": "boolean"},
        "entries": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": _object(
                {
                    "key": {**_TEXT, "maxLength": 160},
                    "question_revision": _TEXT,
                    "source_sha256": _SHA256,
                    "expected_stored_attribute_revision": _SHA256,
                    "decoded": OUTPUT_SCHEMA,
                }
            ),
        },
    }
)


class OfflineWordLabelReviewError(ValueError):
    """A stable code and a safe explanation, with no source text in the error."""

    def __init__(self, code, message_zh, *, operation=None):
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.operation = deepcopy(operation) if operation is not None else None


def _require(condition, code, message):
    if not condition:
        raise OfflineWordLabelReviewError(code, message)


def validate_label_mode(mode):
    _require(
        isinstance(mode, str) and mode in LABEL_MODES,
        "invalid_label_mode", "请选择只补缺失标签或重新核对自动标签。",
    )
    return mode


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def validate_candidate_batch(value):
    """Copy the exact, deliberately provider-independent candidate contract."""
    _require(
        isinstance(value, dict)
        and not list(Draft202012Validator(CANDIDATE_SCHEMA).iter_errors(value)),
        "invalid_candidate_batch",
        "离线标签候选格式不完整，或来源、自动建议声明不正确。",
    )
    copied = deepcopy(value)
    keys = [entry["key"] for entry in copied["entries"]]
    _require(len(keys) == len(set(keys)), "duplicate_key", "离线候选不能重复包含同一道题。")
    # JSON primitives only, and no NaN/Infinity in a caller-supplied dictionary.
    try:
        _digest(copied)
    except (ValueError, TypeError, RecursionError) as exc:
        raise OfflineWordLabelReviewError(
            "invalid_candidate_batch", "离线标签候选必须是完整的JSON数据。"
        ) from exc
    return copied


def _has_substantive_answer_text(blocks):
    # Empty native cells (including vertical-merge continuations) legitimately
    # use this placeholder. Strip presentation only for this presence check;
    # keep every original character in source evidence and the reading digest.
    for block in blocks:
        for line in block["text"].splitlines():
            if _TABLE_DISPLAY_LINE.fullmatch(line.strip()):
                continue
            content = _ANSWER_LABEL.sub("", line).replace("【未提取到文字】", "")
            content = _ANSWER_PART_NUMBER.sub("", content.strip(" \t\u3000:：;；,，.。"))
            if content.strip(" \t\u3000:：;；,，.。"):  # Short answers such as 0, D or √ remain valid.
                return True
    return False


def _source_blocks(row, attributes, preview, *, mode):
    """Admit complete, unambiguous text ranges; answers never enter the unit."""
    _require(
        row.get("selection_ready") is True
        and row.get("export_ready") is True
        and row.get("boundary_status") in {"auto_detected", "manual_range"},
        "range_pending_review", "题目范围或公共材料尚待核对，未应用离线标签。",
    )
    quality = row.get("content_quality")
    _require(
        isinstance(quality, dict)
        and quality.get("status") == "not_fully_reviewed"
        and quality.get("issues") == [],
        "source_issue", "来源存在内容问题或状态不明，未应用离线标签。",
    )
    material = attributes["material_status"]
    _require(
        material["missing_context"] is False
        and material["missing_visual"] is False
        and row.get("shared_material_policy") not in {"unknown", "blocked_pending_review"},
        "missing_material", "题目缺少可确认的材料或图形，未应用离线标签。",
    )
    _require(
        not material["question_image_count"],
        "image_not_supported", "此离线流程仅接受题干与公共材料完整的纯文字题面。",
    )
    recheck = mode == "recheck_automatic"
    if recheck:
        _require(
            bool(row.get("answer_blocks")),
            "answer_range_required", "重新核对自动标签须完整读取题目与答案，当前答案范围缺失。",
        )
        _require(
            not material["answer_image_count"],
            "image_not_supported", "重新核对自动标签须有完整的纯文字题目与答案。",
        )
    full = preview.get("blocks")
    _require(isinstance(full, list), "source_blocks_missing", "原文件区块无法核对。")
    source_by_index = {block["index"]: block for block in full}
    groups = {}
    seen = set()
    for group, start, end in (
        ("question_blocks", "block_start", "question_end"),
        ("context_blocks", "context_start", "context_end"),
        ("answer_blocks", "answer_start", "block_end"),
    ):
        blocks = row.get(group)
        _require(isinstance(blocks, list), "range_missing", "题目区块范围不完整。")
        if not blocks:
            _require(
                group != "question_blocks" and row.get(start) is None,
                "range_missing", "题目区块范围不完整。",
            )
            groups[group] = []
            continue
        indices = [block.get("index") for block in blocks]
        _require(
            all(type(index) is int and index > 0 for index in indices)
            and type(row.get(start)) is int and type(row.get(end)) is int
            and indices == list(range(row[start], row[end] + 1))
            and not seen.intersection(indices),
            "range_gap", "题目范围存在缺段或重叠，未应用离线标签。",
        )
        seen.update(indices)
        for block in blocks:
            origin = source_by_index.get(block["index"])
            _require(
                origin is not None
                and isinstance(block.get("text"), str)
                and block["text"] == origin.get("text")
                and not block.get("display_only_split")
                and not block.get("text_range"),
                "range_content_mismatch", "题目区块与完整原文不一致，未应用离线标签。",
            )
            if group == "answer_blocks" and not recheck:
                # Missing-only keeps its existing question-only readability
                # policy. Neither mode admits answer text as label evidence.
                continue
            _require(
                not block.get("assets") and not origin.get("assets")
                and not any(asset.get("block_index") == block["index"] for asset in preview.get("assets", [])),
                "image_not_supported", "此离线流程仅接受完整的纯文字题目。",
            )
            _require(
                not block.get("warnings") and not origin.get("warnings")
                and "【待查看原文" not in block["text"]
                and not block.get("unsupported_assets")
                and not block.get("gaps"),
                "unsupported_source_content", "原文含未读取的对象或区块警告，未应用离线标签。",
            )
            if recheck:
                _require(
                    not origin.get("unsupported_assets") and not origin.get("gaps")
                    and not origin.get("display_only_split") and not origin.get("text_range"),
                    "unsupported_source_content", "完整原文含未读取或截取的内容，未重新核对自动标签。",
                )
        groups[group] = blocks
    _require(
        (row["answer_start"] == row["question_end"] + 1)
        if groups["answer_blocks"] else (row["question_end"] == row["block_end"]),
        "range_gap", "题目与答案范围之间存在未归属区块，未应用离线标签。",
    )
    if recheck:
        _require(
            _has_substantive_answer_text(groups["answer_blocks"]),
            "answer_range_required", "重新核对自动标签须读到答案正文，当前答案仅有空白、标记或未提取占位。",
        )
    # Keep every question option and every attached context block, in full.
    return [
        {"index": block["index"], "kind": kind, "text": block["text"], "image_sha256s": []}
        for group, kind in (("context_blocks", "shared_context"), ("question_blocks", "question_text"))
        for block in groups[group]
    ]


def _review_scope_sha256(row):
    """Bind all readable question/answer blocks, without claiming human review."""
    return _digest({
        "identity": {name: row[name] for name in (
            "key", "source_sha256", "source_revision", "index_revision",
            "extraction_revision", "revision",
        )},
        "ranges": {name: row.get(name) for name in (
            "block_start", "question_end", "answer_start", "block_end",
            "context_start", "context_end", "origin_block_start",
        )},
        "blocks": {group: row[group] for group in (
            "context_blocks", "question_blocks", "answer_blocks",
        )},
    })


def _coverage(rows):
    return {
        "question_count": len(rows),
        "primary_known": sum(row["primary_knowledge"]["id"] != "unknown" for row in rows),
        "curriculum_mapped": sum(bool(row["curriculum_candidates"]) for row in rows),
    }


class OfflineWordLabelReviewService:
    """Reuse a WordQuestionService; never create an API profile or call a model."""

    def __init__(self, words):
        self.words = words

    def _prime_locations(self, entries):
        # These are untrusted lookup hints only. _resolve still validates the
        # descriptor closure, archive bytes, question key and every revision.
        # A cold command must not parse every unrelated archived Word source.
        required = {entry["source_sha256"] for entry in entries}
        locations = {}
        for descriptor in self.words.state.snapshot().get("drafts", {}).values():
            if not isinstance(descriptor, dict) or descriptor.get("kind") != "desktop_visual_import_v2":
                continue
            batch_id = descriptor.get("batch_id")
            sources = descriptor.get("sources")
            if not isinstance(batch_id, str) or not batch_id or not isinstance(sources, list):
                continue
            for source in sources:
                if not isinstance(source, dict):
                    continue
                sha, identifier = source.get("source_sha256"), source.get("source_file_id")
                if sha in required and isinstance(identifier, str) and identifier:
                    locations.setdefault(sha, (batch_id, identifier))
        self.words._locations.update({
            entry["key"]: locations[entry["source_sha256"]]
            for entry in entries if entry["source_sha256"] in locations
        })

    def _compile(self, batch, *, mode):
        rule_revision = RECHECK_RULE_REVISION if mode == "recheck_automatic" else RULE_REVISION
        reading_policy = READING_POLICY_REVISIONS[mode]
        entries = batch["entries"]
        try:
            self._prime_locations(entries)
            rows, inventory = self.words._resolve([
                {"key": entry["key"], "revision": entry["question_revision"]}
                for entry in entries
            ], read_only=True)
            catalog = self.words._read_attribute_catalog()
            stored = self.words.attribute_store.get_many([entry["key"] for entry in entries])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OfflineWordLabelReviewError(
                "source_or_state_unavailable", "来源、题目范围、标签目录或已有属性已变化，未应用候选。"
            ) from exc
        _require(
            len(rows) == len(entries) and {row["key"] for row in rows} == {entry["key"] for entry in entries},
            "selection_mismatch", "读取到的题目与候选选择不一致。",
        )
        by_key = {row["key"]: row for row in rows}
        planned, bindings, metadata, old_rows = [], [], [], []
        for entry in entries:
            row = by_key[entry["key"]]
            old = stored.get(entry["key"])
            _require(old is not None, "stored_attributes_missing", "离线补充只接受已有属性的题目。")
            old = validate_attributes(old)
            _require(
                old["revision"] == entry["expected_stored_attribute_revision"],
                "stored_revision_changed", "已有标签版本已变化，请重新预览候选。",
            )
            _require(
                row.get("source_sha256") == entry["source_sha256"]
                and row.get("revision") == entry["question_revision"]
                and all(old[name] == row.get(name) for name in (
                    "key", "source_sha256", "source_revision", "index_revision", "extraction_revision"
                ))
                and old["question_revision"] == row.get("revision"),
                "identity_changed", "候选、已有标签与当前来源或题目版本不一致。",
            )
            _require(
                not automatic_tags_protected(old), "protected_attributes",
                "题目已有教师修改、确认或固定标签，未应用离线候选。",
            )
            source_entry = inventory.get(row.get("source_id"))
            _require(
                source_entry is not None and len(source_entry) == 4,
                "source_missing", "候选题目的完整原文件未读取。",
            )
            source, _batch_id, preview, _indexed = source_entry
            _require(
                isinstance(source.content, bytes)
                and hashlib.sha256(source.content).hexdigest() == entry["source_sha256"]
                and source.source_sha256 == entry["source_sha256"]
                and preview.get("source_sha256") == entry["source_sha256"]
                and preview.get("revision") == row["source_revision"]
                and preview.get("extraction_revision") == row["extraction_revision"],
                "source_identity_changed", "完整原文件或解析版本与候选绑定不一致。",
            )
            blocks = _source_blocks(row, old, preview, mode=mode)
            review_sha = _review_scope_sha256(row) if mode == "recheck_automatic" else None
            unit = {
                "mode": mode, "attributes": old, "images": [],
                "input": {"blocks": blocks, "source_sha256": row["source_sha256"]},
            }
            try:
                new = merge_response(entry["decoded"], unit, catalog)
            except (ValueError, TypeError, KeyError) as exc:
                raise OfflineWordLabelReviewError(
                    "invalid_candidate_evidence", "候选知识点、教材节或逐字引用依据不符合当前题目与目录。"
                ) from exc
            allowed = {
                "primary_knowledge", "supporting_knowledge", "curriculum_candidates",
                "curriculum_status", "rule_revision", "revision",
            }
            _require(
                {key: value for key, value in new.items() if key not in allowed}
                == {key: value for key, value in old.items() if key not in allowed}
                and new["supporting_knowledge"] == [
                    tag for tag in old["supporting_knowledge"]
                    if old["primary_knowledge"]["id"] == new["primary_knowledge"]["id"]
                    or new["primary_knowledge"]["id"] == "unknown"
                    or tag["id"] != new["primary_knowledge"]["id"]
                ],
                "unexpected_merge_change", "合并超出本次标签操作范围，未应用离线候选。",
            )
            changed = new != old
            if changed:
                new = validate_attributes(_seal({
                    **new, "rule_revision": rule_revision,
                    "edit_version": old["edit_version"] + 1,
                }))
            old_rows.append(old)
            planned.append(new)
            bindings.append({
                "key": row["key"], "source_sha256": entry["source_sha256"],
                "preview_sha256": _digest(preview),
                "question_sha256": _digest(row),
                "stored_attribute_revision": old["revision"],
                "planned_attribute_revision": new["revision"],
                "review_scope_sha256": review_sha,
            })
            metadata.append({
                "key": row["key"], "source_sha256": row["source_sha256"],
                "question_revision": row["revision"],
                "old_attribute_revision": old["revision"],
                "new_attribute_revision": new["revision"],
                "old_edit_version": old["edit_version"], "new_edit_version": new["edit_version"],
                "changed": changed,
                "review_scope_sha256": review_sha,
                "old_primary": old["primary_knowledge"]["id"],
                "new_primary": new["primary_knowledge"]["id"],
                "old_sections": [node["section_key"] for node in old["curriculum_candidates"]],
                "new_sections": [node["section_key"] for node in new["curriculum_candidates"]],
                "removed_supporting_duplicates": len(old["supporting_knowledge"]) - len(new["supporting_knowledge"]),
            })
        catalog_sha = _digest(catalog)
        candidate_sha = _digest(batch)
        plan_sha = _digest({
            "schema_version": PLAN_SCHEMA_VERSION, "rule_revision": rule_revision,
            "label_mode": mode, "reading_policy_revision": reading_policy,
            "candidates": batch, "catalog_sha256": catalog_sha, "bindings": bindings,
        })
        changed_count = sum(entry["changed"] for entry in metadata)
        report = {
            "schema_version": PLAN_SCHEMA_VERSION, "rule_revision": rule_revision,
            "label_mode": mode, "reading_policy_revision": reading_policy,
            "provenance": PROVENANCE, "candidate_only": True, "human_review": False,
            "provider_invoked": False, "teacher_confirmed": False,
            "plan_sha256": plan_sha, "candidate_sha256": candidate_sha,
            "catalog_sha256": catalog_sha,
            "entry_count": len(entries), "source_count": len({entry["source_sha256"] for entry in entries}),
            "changed_question_count": changed_count,
            "unchanged_question_count": len(entries) - changed_count,
            "primary_filled_count": sum(entry["old_primary"] == "unknown" and entry["new_primary"] != "unknown" for entry in metadata),
            "curriculum_filled_count": sum(not entry["old_sections"] and bool(entry["new_sections"]) for entry in metadata),
            "primary_replaced_count": sum(
                entry["old_primary"] != "unknown" and entry["new_primary"] != "unknown"
                and entry["old_primary"] != entry["new_primary"] for entry in metadata
            ),
            "curriculum_replaced_count": sum(
                bool(entry["old_sections"]) and bool(entry["new_sections"])
                and set(entry["old_sections"]) != set(entry["new_sections"]) for entry in metadata
            ),
            "before": _coverage(old_rows), "after_proposed": _coverage(planned),
            "entries": metadata,
        }
        return report, planned, {row["key"]: row["revision"] for row in old_rows}

    def preview(self, candidates, *, mode="missing_only"):
        mode = validate_label_mode(mode)
        batch = validate_candidate_batch(candidates)
        with self.words._lock:
            return self._compile(batch, mode=mode)[0]

    def apply(self, candidates, *, expected_plan_sha256, mode="missing_only"):
        valid_digest = (
            isinstance(expected_plan_sha256, str) and len(expected_plan_sha256) == 64
            and all(char in "0123456789abcdef" for char in expected_plan_sha256)
        )
        operation = {
            "stage": "candidate_validation", "attribute_write_attempted": False,
            "commit_status": "not_attempted", "readback_verified": False,
            "plan_sha256": expected_plan_sha256 if valid_digest else None,
            "expected_plan_sha256": expected_plan_sha256 if valid_digest else None,
            "label_mode": mode if isinstance(mode, str) and mode in LABEL_MODES else None,
        }
        try:
            mode = validate_label_mode(mode)
            batch = validate_candidate_batch(candidates)
            _require(
                valid_digest, "expected_plan_required", "应用候选须提供已核对预览的完整摘要。",
            )
            with self.words._lock:
                operation["stage"] = "plan_validation"
                report, planned, expected = self._compile(batch, mode=mode)
                operation["plan_sha256"] = report["plan_sha256"]
                _require(
                    report["plan_sha256"] == expected_plan_sha256,
                    "plan_changed", "标签模式、候选、来源、范围、目录或已有标签与预览不同，未应用。",
                )
                if report["changed_question_count"]:
                    operation.update(
                        stage="attribute_write", attribute_write_attempted=True,
                        commit_status="unknown",
                    )
                    try:
                        self.words.attribute_store.save_many(planned, expected_revisions=expected)
                    except WordQuestionAttributeError as exc:
                        raise OfflineWordLabelReviewError(
                            "attribute_write_conflict", "属性保存未正常返回；请核对实际记录，不要自动重试。"
                        ) from exc
                    # save_many has returned after its transaction/connection
                    # context exited. Later failures cannot undo this commit.
                    operation["commit_status"] = "committed"
                operation["stage"] = "attribute_readback"
                actual = self.words.attribute_store.get_many(list(expected))
                _require(
                    actual == {row["key"]: row for row in planned},
                    "readback_mismatch", "应用后的标签回读不符，请核对记录，不要自动重试。",
                )
                operation.update(stage="readback_verified", readback_verified=True)
                return {
                    **report, "schema_version": RECEIPT_SCHEMA_VERSION, **operation,
                    "completed": True, "applied": True,
                    "do_not_retry_automatically": False,
                    "after_actual": _coverage(list(actual.values())),
                    "saved_attribute_revisions": {key: row["revision"] for key, row in actual.items()},
                }
        except Exception as exc:
            code = getattr(exc, "code", None) or {
                "attribute_write": "attribute_write_failed",
                "attribute_readback": "attribute_readback_failed",
            }.get(operation["stage"], "offline_label_operation_failed")
            failure = {
                **operation, "completed": False, "error_code": code,
                "do_not_retry_automatically": operation["attribute_write_attempted"],
                "recovery_hint": (
                    "属性已经提交；请核对已保存标签和历史，不要自动重试。"
                    if operation["commit_status"] == "committed" else
                    "已尝试写入，提交状态未知；请先核对已保存标签和历史，不要自动重试。"
                    if operation["attribute_write_attempted"] else
                    "未尝试写入属性；修复问题后重新预览。"
                ),
            }
            if isinstance(exc, OfflineWordLabelReviewError):
                exc.operation = failure
                raise
            raise OfflineWordLabelReviewError(
                code, failure["recovery_hint"], operation=failure,
            ) from exc

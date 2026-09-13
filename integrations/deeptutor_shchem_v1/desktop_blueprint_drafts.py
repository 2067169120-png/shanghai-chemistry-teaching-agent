"""Offline, append-only teacher revisions of a saved blueprint or AI revision."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any

from .desktop_blueprint_generation import BlueprintGenerationError, validate_blueprint
from .desktop_blueprint_review import REVIEW_KIND, candidate_revision
from .desktop_state import DesktopStateStore, utc_now

DRAFT_KIND = "textbook_blueprint_teacher_draft"


class BlueprintDraftError(ValueError):
    def __init__(self, code: str, message_zh: str):
        self.code, self.message_zh = code, message_zh
        super().__init__(message_zh)


class BlueprintDraftService:
    def __init__(self, state: DesktopStateStore):
        self.state = state

    @staticmethod
    def _label(prefix: str, record: dict, source_id: str) -> str:
        try:
            stamp = (
                datetime.fromisoformat(record.get("created_at", ""))
                .astimezone()
                .strftime("%m-%d %H:%M:%S")
            )
        except (ValueError, TypeError):
            stamp = "时间未知"
        return f"{prefix} · {stamp} · {source_id[-6:]}"

    def _root(self, preview_id: str, records: dict) -> tuple[dict, str]:
        root = records.get(preview_id)
        if (
            not isinstance(root, dict)
            or root.get("kind") != "textbook_prompt_blueprint"
            or root.get("status") != "completed"
            or not isinstance(root.get("preview", {}).get("evidence"), list)
            or not root["preview"]["evidence"]
        ):
            raise BlueprintDraftError(
                "blueprint_draft_source_missing", "找不到已完成蓝图及其资料依据。"
            )
        original = root.get("result", {}).get("candidate")
        self._validate(original, len(root["preview"]["evidence"]))
        return root, candidate_revision(original)

    @staticmethod
    def _validate(candidate: Any, evidence_count: int) -> dict:
        try:
            valid = validate_blueprint(candidate, evidence_count)
            if len(json.dumps(valid, ensure_ascii=False)) > 250_000:
                raise BlueprintDraftError(
                    "blueprint_draft_too_large", "单个蓝图草稿文字过长，请精简后保存。"
                )
            if not valid["theme_center"].strip():
                raise BlueprintDraftError("blueprint_draft_empty", "请填写主题中心。")
            return valid
        except BlueprintGenerationError as exc:
            raise BlueprintDraftError(exc.code, exc.message_zh) from exc

    def _source(self, preview_id: str, source_id: str, records: dict) -> dict:
        root, root_revision = self._root(preview_id, records)
        record = records.get(source_id)
        if source_id == preview_id:
            value, label = root["result"]["candidate"], "原始生成蓝图"
        elif isinstance(record, dict) and record.get("preview_id") == preview_id:
            if (
                record.get("kind") == REVIEW_KIND
                and record.get("status") == "completed"
                and self._review_source_matches(
                    record, preview_id, root_revision, records
                )
            ):
                value = record.get("result", {}).get("candidate")
                label = self._label("AI修订", record, source_id)
            elif (
                record.get("kind") == DRAFT_KIND
                and record.get("root_revision") == root_revision
            ):
                value = record.get("candidate")
                label = self._label("教师草稿", record, source_id)
            else:
                raise BlueprintDraftError(
                    "blueprint_draft_source_invalid",
                    "该来源未完成或不属于当前蓝图版本。",
                )
        else:
            raise BlueprintDraftError(
                "blueprint_draft_source_invalid", "该来源不属于当前蓝图。"
            )
        value = self._validate(value, len(root["preview"]["evidence"]))
        return {
            "source_id": source_id,
            "source_revision": candidate_revision(value),
            "source_label": label,
            "candidate": value,
            "evidence": deepcopy(root["preview"]["evidence"]),
            "root_revision": root_revision,
            "source_note": record.get("note", "") if isinstance(record, dict) else "",
            "source_kind": record.get("kind") if isinstance(record, dict) else None,
            "handout_evidence_ids": list(self._handout_bindings(root)),
        }

    @staticmethod
    def _handout_bindings(root: dict) -> dict[str, dict]:
        """Bind saved evidence by exact compiler order, never by its title text."""
        preview = root.get("preview", {})
        handout = preview.get("handout_reference")
        if not isinstance(handout, dict):
            return {}
        evidence = handout.get("evidence")
        provenance = handout.get("local_provenance")
        all_evidence = preview.get("evidence", [])
        if (
            not isinstance(evidence, list)
            or not evidence
            or not isinstance(provenance, list)
            or len(provenance) != len(evidence)
            or type(handout.get("include_answers")) is not bool
            or len(all_evidence) < len(evidence)
            or all_evidence[-len(evidence) :] != evidence
        ):
            return {}
        bindings = {}
        start = len(all_evidence) - len(evidence) + 1
        for index, (item, source) in enumerate(zip(evidence, provenance), start):
            if (
                not isinstance(item, dict)
                or item.get("source_type") != "user_handout_question_reference"
                or not isinstance(source, dict)
                or not isinstance(source.get("key"), str)
                or not isinstance(source.get("revision"), str)
                or not isinstance(source.get("source_document"), dict)
                or not isinstance(source.get("editable_source"), dict)
            ):
                return {}
            bindings[f"E{index}"] = {
                **deepcopy(source),
                "include_answers": handout["include_answers"],
            }
        return bindings

    def handout_binding(self, preview_id: str, evidence_id: str) -> dict:
        root, _ = self._root(preview_id, self.state.snapshot()["drafts"])
        binding = self._handout_bindings(root).get(evidence_id)
        if binding is None:
            raise BlueprintDraftError(
                "blueprint_evidence_page_unavailable",
                "此资料没有完整的讲义原页绑定；可继续查看已保存的资料文字。",
            )
        return binding

    @staticmethod
    def _review_source_matches(
        review: dict,
        preview_id: str,
        root_revision: str,
        records: dict,
    ) -> bool:
        draft_id = review.get("source_draft_id")
        if draft_id is None:
            return review.get("source_candidate_revision") == root_revision
        draft = records.get(draft_id) if isinstance(draft_id, str) else None
        return (
            isinstance(draft, dict)
            and draft.get("kind") == DRAFT_KIND
            and draft.get("preview_id") == preview_id
            and draft.get("root_revision") == root_revision
            and review.get("root_candidate_revision") == root_revision
            and isinstance(draft.get("candidate"), dict)
            and candidate_revision(draft["candidate"])
            == review.get("source_candidate_revision")
        )

    def sources(self, preview_id: str) -> list[dict]:
        records = self.state.snapshot()["drafts"]
        first = self._source(preview_id, preview_id, records)
        others = sorted(
            (
                (key, value)
                for key, value in records.items()
                if isinstance(value, dict)
                and value.get("preview_id") == preview_id
                and value.get("kind") in (REVIEW_KIND, DRAFT_KIND)
            ),
            key=lambda pair: (pair[1].get("created_at", ""), pair[0]),
            reverse=True,
        )
        result = [first]
        for key, _value in others:
            try:
                result.append(self._source(preview_id, key, records))
            except BlueprintDraftError:
                # An unfinished review is not an editable complete source.
                continue
        return result

    def load_source(
        self, preview_id: str, source_id: str, expected_revision: str | None = None
    ) -> dict:
        result = self._source(preview_id, source_id, self.state.snapshot()["drafts"])
        if (
            expected_revision is not None
            and result["source_revision"] != expected_revision
        ):
            raise BlueprintDraftError(
                "blueprint_draft_stale", "来源已变化，请重新打开后再编辑。"
            )
        return result

    def save(
        self,
        preview_id: str,
        source_id: str,
        expected_revision: str,
        candidate: Any,
        note: str = "",
    ) -> dict:
        if not isinstance(expected_revision, str) or not expected_revision:
            raise BlueprintDraftError(
                "blueprint_draft_stale", "缺少来源版本，请重新打开后再编辑。"
            )
        source = self.load_source(preview_id, source_id, expected_revision)
        valid = self._validate(candidate, len(source["evidence"]))
        original = source["candidate"]
        identity = lambda value: [
            (q["printed_question_id"], q["atomic_part_id"])
            for q in value["question_chain"]
        ]
        if identity(valid) != identity(original) or [
            m["material_id"] for m in valid["shared_material_plan"]
        ] != [m["material_id"] for m in original["shared_material_plan"]]:
            raise BlueprintDraftError(
                "blueprint_draft_structure_changed",
                "请保留原材料、小题及作答单元编号和顺序，以便对照。",
            )
        if not isinstance(note, str) or len(note) > 2000:
            raise BlueprintDraftError(
                "blueprint_draft_note_invalid", "修订说明请控制在2000字以内。"
            )
        record = {
            "draft_id": "BLUEPRINT-TEACHER-" + uuid.uuid4().hex,
            "kind": DRAFT_KIND,
            "preview_id": preview_id,
            "root_revision": source["root_revision"],
            "source_id": source_id,
            "source_revision": expected_revision,
            "candidate": valid,
            "note": note.strip(),
            "created_at": utc_now(),
            "candidate_only": True,
            "teacher_review_required": True,
            "chemistry_correctness_verified": False,
            "publication_allowed": False,
            "bank_ingest_allowed": False,
        }
        self.state.save_draft(record["draft_id"], record)
        return deepcopy(record)

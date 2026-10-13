"""Read-only complete-source comparisons and one durable saved-paper exchange.

No candidate is staged in the public basket. Plans are session-local; source
versions retain canonical locators and full drafts under the existing profile
trust key, and every restore resolves original sources again.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import RLock
from uuid import uuid4

from .desktop_independent_paper import (
    _dependencies, _record, _references, _validate_draft, source_basis,
)
from .desktop_mixed_paper_drafts import DRAFT_ID, digest, validate_payload
from .desktop_mixed_paper_service import BASKET_SCHEMA, MixedPaperError, MixedPaperService, _ACTIVE, _sha
from .desktop_paper_history import MAX_BYTES, PAPER_HISTORY_ID, PaperHistory, encoded, entry
from .desktop_state import DesktopStateError, DraftConflictError, _draft_snapshot, utc_now
from .reader_cancellation import check_read_cancelled, publication_guard

_HISTORY_SUFFIX = "-source-versions.v1"
_TEMP_ROOT = Path("E:/临时文件/UserTemp/shchem-question-replacements")
# Keep the facade's source-error translation aligned with the mixed readers.
QuestionReplacementError = MixedPaperError


def _snapshot_pair(value, identity):
    meta = _draft_snapshot(value, identity)
    draft = _draft_snapshot(value, identity + ":" + DRAFT_ID)
    _record(value, identity)
    _validate_draft(draft.record, meta.record)
    return meta, draft


def _basis(meta):
    return {"basket_revision": meta["selection_sha256"][:32],
            "basket_sha256": digest(meta["items"]), "sources": deepcopy(meta["source_basis"])}


def _version_binding(meta, draft, history):
    return digest({"meta": {"revision": meta.revision, "sha256": meta.content_sha256},
                   "draft": {"revision": draft.revision, "sha256": draft.content_sha256},
                   "history": {"revision": history.snapshot.revision, "sha256": history.snapshot.content_sha256}})


def _projection(meta, rows):
    return {"schema_version": BASKET_SCHEMA, "basket_sha256": digest(meta["items"]),
            "items": deepcopy(rows), "warnings": ["分值是本次练习设置；未知来源分值不按零分推定。"]}


def _strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _student_text(value):
    """Only student content fields; titles, school names and answers are excluded."""
    output = []
    text_fields = {"text", "text_zh", "normalized_text_zh", "visible_summary_zh",
                   "response_requirement_zh", "question_text", "shared_text", "stem",
                   "context", "content", "question_body", "response_requirements"}
    omitted = {"answer", "answers", "answer_blocks", "answer_text", "teacher_notes",
               "reference_answer", "source_reference_answer", "teacher_plan", "paper",
               "source_zh", "source_name", "title", "title_zh", "chapter", "attributes",
               "label_summary", "classification", "cognitive_difficulty", "difficulty"}
    omitted.update({"answer_body", "analysis", "explanation_zh", "scoring_points", "source_scores"})
    def walk(node):
        if isinstance(node, Mapping):
            if node.get("teacher_only") is True:
                return
            for key, val in node.items():
                if key in omitted:
                    continue
                if key in text_fields and isinstance(val, str):
                    output.append(val)
                elif key in text_fields and isinstance(val, list):
                    output.extend(item for item in val if isinstance(item, str))
                    walk(val)
                elif isinstance(val, (Mapping, list)):
                    walk(val)
        elif isinstance(node, list):
            for val in node:
                walk(val)
    walk(value)
    return "\n".join(output).casefold()


def _knowledge(row, words, visual_details):
    result = set()
    if row["kind"] == "word_question":
        raw = words[row["key"]]
        if raw.get("attribute_stale") or raw.get("attribute_warning"):
            return result
        attributes = raw.get("attributes") or {}
        values = [attributes.get("primary_knowledge"), *attributes.get("supporting_knowledge", [])]
        for val in values:
            if (isinstance(val, Mapping) and val.get("id") not in {None, "unknown"}
                    and val.get("status") not in {"unknown", "blocked_pending_review"}):
                result.add(val["id"])
    elif row["kind"] == "personal_visual_theme":
        for raw in visual_details:
            if not raw.get("attribute_stale"):
                result.update(_strings((raw.get("facets") or {}).get("knowledge")))
    else:
        for atomic in row["content"].get("atomic_chain", []):
            for val in [atomic, *atomic.get("alias_units", [])]:
                labels = val.get("label_summary") or {}
                if labels.get("status") in {"unknown", "blocked_pending_review"}:
                    continue
                result.update(_strings(labels.get("primary_K")))
                result.update(_strings(labels.get("supporting_K")))
    return result - {"unknown", "blocked_pending_review"}


def _unit_identity(row):
    ref = row["source_ref"]
    if row["kind"] == "word_question":
        blocks = row["content"].get("question_blocks", [])
        return ("word", ref["source_sha256"], ref["origin_block_start"],
                tuple(block.get("index") for block in blocks))
    if row["kind"] == "personal_visual_theme":
        return ("visual", ref["batch_id"], ref["theme_id"])
    return ("core", ref["scope"], ref["paper_id"], ref["theme_id"])


def _alias_closure(row):
    """Whole-theme typed closure, never an intersection of one response unit."""
    if row["kind"] != "core_theme":
        return None
    units, explicit = set(), False
    for atomic in row["content"].get("atomic_chain", []):
        aliases = atomic.get("alias_units")
        if isinstance(aliases, list) and aliases:
            explicit = True
            for alias in aliases:
                scope = alias.get("source_scope", "wave1")
                identity = alias.get("atomic_part_id")
                if scope not in {"master", "wave1", "supplemental"} or not isinstance(identity, str) or not identity:
                    return None
                units.add((scope, identity))
        else:
            identity = atomic.get("atomic_part_id")
            if not isinstance(identity, str) or not identity:
                return None
            units.add((row["source_ref"]["scope"], identity))
    return (frozenset(units), explicit) if units else None


def _duplicates(candidate, rows):
    incoming = _alias_closure(candidate)
    for row in rows:
        if candidate["key"] == row["key"] or _unit_identity(candidate) == _unit_identity(row):
            raise MixedPaperError("候选已在当前卷中（包括暂移除题目），不能自替换或重复加入。", "replacement_duplicate")
        existing = _alias_closure(row)
        if incoming and existing and incoming[0] == existing[0] and (incoming[1] or existing[1]):
            raise MixedPaperError("候选与当前卷完整主题具有已确认的全部来源别名，不能重复替换。", "replacement_duplicate_alias")
    return ["候选题名与当前卷题名相似；仅作人工核对提示，未按题名判同题。"] if any(
        candidate.get("title_zh") == row.get("title_zh") for row in rows) else []


class QuestionReplacementService:
    def __init__(self, facade, *, mixed_service=None):
        self.facade, self.store = facade, facade.state_store
        self.service = mixed_service or MixedPaperService(facade)
        self._plans, self._lock = {}, RLock()
        self._restore_attempts = set()

    def _resolve(self, meta):
        rows, words, inventory, _ = self.service._resolve(deepcopy(meta["items"]))
        _dependencies(self.facade, rows, words)
        if source_basis(rows) != meta["source_basis"]:
            raise DraftConflictError("保存卷的原件、范围、标签或裁片已经变化，原卷保留；请重新核对来源。")
        return rows, words, inventory

    def _validate_history_state(self, state):
        if not isinstance(state, dict) or set(state) != {"meta", "draft", "editing_history", "editing_history_evidence",
                "editing_history_verified", "version_id", "created_at", "goal"}:
            raise DesktopStateError("来源版本记录不完整，不能恢复。")
        meta, draft = state["meta"], state["draft"]
        _validate_draft(draft, meta)
        if digest(meta["items"]) != meta.get("selection_sha256") or set(meta["source_basis"]) != {row["key"] for row in meta["items"]}:
            raise DesktopStateError("来源版本身份不一致，不能恢复。")
        editing = state["editing_history"]
        if not isinstance(editing, dict) or set(editing) != {"undo", "redo"}:
            raise DesktopStateError("版本的编排历史无法核验。")
        if (type(state["editing_history_verified"]) is not bool
                or (state["editing_history_evidence"] is not None and not isinstance(state["editing_history_evidence"], dict))
                or (not state["editing_history_verified"] and (editing["undo"] or editing["redo"]))):
            raise DesktopStateError("旧编排历史的信任状态无法核验。")
        for step in editing["undo"] + editing["redo"]:
            _validate_draft(step["state"], meta)

    def _history(self, value, identity, meta_snapshot):
        journal = PaperHistory(self.store, identity + _HISTORY_SUFFIX, "independent-source-versions:" + identity)
        journal.read(value, meta_snapshot, {"paper_id": identity}, self._validate_history_state)
        if journal.reset:
            raise DesktopStateError("此卷来源版本记录无法核验，已保留原记录；不能覆盖或恢复。")
        return journal

    def _editing_history(self, value, identity, meta, draft):
        journal = PaperHistory(self.store, identity + ":" + PAPER_HISTORY_ID, "independent-paper:" + identity)
        journal.read(value, draft, _basis(meta.record), lambda state: _validate_draft(state, meta.record))
        # Invalid old journals are preserved as evidence but never restore input.
        return journal, {"undo": deepcopy(journal.undo), "redo": deepcopy(journal.redo)}

    def _capture(self, value, identity, meta, draft, goal):
        journal, editing = self._editing_history(value, identity, meta, draft)
        captured = {"meta": deepcopy(meta.record), "draft": deepcopy(draft.record),
                "editing_history": editing, "editing_history_evidence": deepcopy(journal.snapshot.record),
                "editing_history_verified": not journal.reset, "version_id": "source-version-" + uuid4().hex,
                "created_at": utc_now(), "goal": deepcopy(goal)}
        if len(encoded(captured)) > MAX_BYTES - 8192:
            raise DesktopStateError("完整来源版本与编排历史超过可保留范围，当前卷未换源；请先核对并减少编排历史。")
        return captured

    def _verify_materials(self, rows, words, inventory):
        """Use established source-role preflight without creating a paper preview."""
        check_read_cancelled()
        _TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        evidence = {}
        student_pixels, answer_pixels = set(), set()
        with TemporaryDirectory(prefix="verify-", dir=_TEMP_ROOT) as temporary:
            folder = Path(temporary)
            context = self.service._core_context() if any(row["kind"] == "core_theme" for row in rows) else {}
            if words:
                from .desktop_word_question_export import _prepare
                prepared_words = [{**deepcopy(raw), "source_bytes": inventory[raw["source_id"]][0].content}
                                  for raw in words.values()]
                # Preserve the export reader's cross-question/context answer
                # separation, not just each individual segment's boundaries.
                (self.service._word_validator or _prepare)(prepared_words)
            for index, row in enumerate(rows):
                check_read_cancelled()
                ref, key = row["source_ref"], row["key"]
                if row["kind"] == "word_question":
                    raw = words[key]
                    if raw.get("selection_ready") is not True or raw.get("export_ready") is not True:
                        raise MixedPaperError("Word 完整题目段尚有范围或来源质量问题，不能替换。")
                    data = inventory[raw["source_id"]][0].content
                    if _sha(data) != raw["source_sha256"]:
                        raise MixedPaperError("Word 原件校验失败。")
                    images, blockers = {}, []
                    student = self.service._word_blocks(raw, data, ("context_blocks", "question_blocks"), folder, images, blockers)
                    answer = self.service._word_blocks(raw, data, ("answer_blocks",), folder, images, blockers)
                    if blockers:
                        raise MixedPaperError("；".join(blockers), "replacement_material_blocked")
                    question_hashes = {images[block["image_id"]]["sha256"] for block in student if block["kind"] == "image"}
                    answer_hashes = {images[block["image_id"]]["sha256"] for block in answer if block["kind"] == "image"}
                    if question_hashes & answer_hashes:
                        raise MixedPaperError("相同 Word 图片同时用于题面和答案，不能替换。")
                    student_pixels.update(question_hashes)
                    answer_pixels.update(answer_hashes)
                    evidence[key] = {"student": student, "answer": answer, "images": images}
                elif row["kind"] == "personal_visual_theme":
                    compiled, assets = self.service._visual_themes().compile(ref["selections"])
                    if len(compiled) != 1 or compiled[0]["source_ref"] != ref:
                        raise MixedPaperError("完整图片主题来源已经变化。")
                    for image in row["content"]["images"]:
                        (answer_pixels if image["teacher_only"] else student_pixels).add(image["sha256"])
                    evidence[key] = {"content": row["content"], "images": {name: _sha(data) for name, data in assets.items()}}
                else:
                    from .paper_export_workbench import build_core_export_bundle
                    request = {"title_zh": "替题来源核对", "subtitle_zh": None, "duration_minutes": 40,
                        "numbering_mode": "continuous_across_paper", "show_question_scores": False,
                        "score_per_atomic": 2, "answer_space_lines": 0,
                        "selections": [{"scope": ref["scope"], "selection_unit": "theme", "theme_id": ref["theme_id"],
                                        "target_atomic_id": None, "expected_data_snapshot_id": ref["data_snapshot_id"]}]}
                    asset_root = folder / str(index)
                    bundle = (self.service._core_bundle_builder or build_core_export_bundle)(request, **context,
                        asset_root=asset_root, bundle_id="replacement-source-verification")
                    if len(bundle["blueprint"]["theme_bundles"]) != 1:
                        raise MixedPaperError("正式候选没有保留完整主题链。")
                    preflight = bundle.get("preflight_report")
                    if (isinstance(preflight, Mapping) and (preflight.get("status") != "ready_for_renderer"
                            or preflight.get("blocker_count", 0) or preflight.get("machine_blocker_count", 0))):
                        raise MixedPaperError("正式来源的题面、图形或答案质量检查尚未通过，不能替换。")
                    hashes = self.service._core_asset_hashes(bundle, asset_root)
                    def add_assets(node, *, answer=False):
                        if isinstance(node, Mapping):
                            if node.get("asset_ref"):
                                (answer_pixels if answer else student_pixels).add(hashes[node["asset_ref"]])
                            for field, val in node.items():
                                if field == "teacher_notes" and not answer:
                                    continue
                                add_assets(val, answer=answer)
                        elif isinstance(node, list):
                            for val in node:
                                add_assets(val, answer=answer)
                    add_assets(bundle["student_plan"]["visible"])
                    for section in bundle["teacher_plan"]["visible"]["theme_sections"]:
                        for printed in section["printed_questions"]:
                            for atomic in printed["atomic_parts"]:
                                add_assets(atomic.get("teacher_notes", {}).get("source_reference_answer", {}), answer=True)
                    evidence[key] = {"student": bundle["student_plan"], "teacher": bundle["teacher_plan"], "blueprint": bundle["blueprint"],
                                     "images": hashes, "preflight": preflight}
            if student_pixels & answer_pixels:
                raise MixedPaperError("当前卷不同来源段的题面或共同材料图片与教师答案图片重复，不能省略角色核对后换源。")
        check_read_cancelled()
        return digest(evidence)

    @staticmethod
    def _validate_entry(entry):
        if not isinstance(entry, Mapping) or entry.get("payload") is None:
            raise MixedPaperError("候选定位不完整。")
        payload = entry["payload"]
        def field(name):
            return payload.get(name) if isinstance(payload, Mapping) else getattr(payload, name, None)
        key, lane = field("key"), entry.get("lane")
        if not key or entry.get("key") != key:
            raise MixedPaperError("候选显示身份与来源身份不一致，请重新检索。")
        required = (("source_identity_sha256", "data_snapshot_id") if lane in {"master", "wave1", "supplemental"}
                    else ("revision", "batch_id", "archive_source_id", "source_sha256") if lane == "word_native"
                    else ("revision", "batch_id", "theme_key") if lane == "visual_native" else None)
        if required is None or any(not isinstance(field(name), str) or not field(name) for name in required):
            raise MixedPaperError("候选缺少完整来源版本定位，请重新检索。")
        expected_kind = "theme" if lane in {"master", "wave1", "supplemental"} else "word_question_segment" if lane == "word_native" else "visual_printed_question"
        if entry.get("unit_kind", expected_kind) != expected_kind:
            raise MixedPaperError("候选不是对应来源的完整可替换单元。")
        locator = entry.get("source_locator")
        if not isinstance(locator, Mapping) or not locator:
            raise MixedPaperError("候选缺少来源定位，请重新检索。")
        for name, val in locator.items():
            actual = lane if name == "scope" else field(name)
            if actual != val:
                raise MixedPaperError("候选定位与当前来源不一致，请重新检索。")

    def _match(self, goal, row, words):
        if (not isinstance(goal, Mapping) or set(goal) - {"text", "query", "knowledge"}
                or not isinstance(goal.get("text"), str) or not 1 <= len(goal["text"].strip()) <= 500
                or not isinstance(goal.get("query", ""), str)
                or len(goal.get("query", "")) > 500
                or not isinstance(goal.get("knowledge", []), list)
                or any(not isinstance(value, str) or not re.fullmatch(r"K\d{2}", value) for value in goal.get("knowledge", []))):
            raise MixedPaperError("请填写明确教师目标，并使用已核对的关键词或知识点依据。")
        terms = list(dict.fromkeys(goal.get("query", "").casefold().split()))
        requested = list(dict.fromkeys(goal.get("knowledge", [])))
        if len(terms) > 12 or len(requested) > 30 or not (terms or requested):
            raise MixedPaperError("教师目标还缺少可核验的关键词或知识点筛选依据。")
        details = []
        if row["kind"] == "personal_visual_theme":
            service = self.service._visual_themes().questions
            details = [service._matched_row(val["batch_id"], val["key"], val["revision"])[0]
                       for val in row["source_ref"]["selections"]]
        content = words[row["key"]] if row["kind"] == "word_question" else row["content"]
        text = _student_text(content)
        known = _knowledge(row, words, details)
        if any(term not in text for term in terms) or any(value not in known for value in requested):
            raise MixedPaperError("候选题面或共同材料未实际命中全部目标筛选依据；答案、校名与相似标题不能替代依据。")
        label_evidence = []
        if requested and row["kind"] == "core_theme":
            for atomic in row["content"].get("atomic_chain", []):
                for unit in [atomic, *atomic.get("alias_units", [])]:
                    labels = unit.get("label_summary") or {}
                    for field in ("primary_K", "supporting_K"):
                        matched = sorted(set(_strings(labels.get(field))) & set(requested))
                        if matched:
                            label_evidence.append({"unit_id": unit.get("atomic_part_id"), "field": "label_summary." + field,
                                "values": matched, "status": labels.get("status", "unknown")})
        elif requested:
            raws = [words[row["key"]]] if row["kind"] == "word_question" else details
            for raw in raws:
                attributes = raw.get("attributes") or {}
                for field in ("primary_knowledge", "supporting_knowledge"):
                    values = [attributes.get(field)] if field == "primary_knowledge" else attributes.get(field, [])
                    for val in values:
                        if isinstance(val, Mapping) and val.get("id") in requested:
                            label_evidence.append({"unit_id": raw["key"], "field": "attributes." + field,
                                "values": [val["id"]], "status": val.get("status", "unknown"),
                                "origin": (attributes.get("field_origins") or {}).get(field, "unknown")})
        return {"terms": terms, "knowledge": requested, "text": "题面与共同材料的实际文字命中；答案未用于检索依据。",
                "knowledge_evidence": label_evidence, "knowledge_notice": "知识标签命中保留来源状态；自动建议和来源候选仍需教师核对。",
                "source_ref": deepcopy(row["source_ref"]), "difficulty": "unknown",
                "goal_authority": "teacher_manual", "student_diagnosis": "not_claimed"}

    def prepare(self, paper_id, target_key, *, goal, candidate_entry):
        check_read_cancelled()
        value = self.store.snapshot()
        meta, draft = _snapshot_pair(value, paper_id)
        if target_key not in draft.record["payload"]["order"]:
            raise MixedPaperError("请选择当前卷中的完整来源题目段。")
        self._history(value, paper_id, meta)
        rows, words, inventory = self._resolve(meta.record)
        self._validate_entry(candidate_entry)
        raw, replacement, candidate_words, candidate_inventory, _ = self.service.source_item_from_entry(candidate_entry)
        _dependencies(self.facade, [replacement], candidate_words)
        warnings = _duplicates(replacement, rows)
        match = self._match(goal, replacement, candidate_words)
        by_key = {row["key"]: row for row in rows}
        after_rows = [deepcopy(replacement) if row["key"] == target_key else deepcopy(row) for row in rows]
        after_meta = deepcopy(meta.record)
        after_meta["items"] = _references(after_rows)
        after_meta["selection_sha256"] = digest(after_meta["items"])
        after_meta["source_basis"] = source_basis(after_rows)
        after_draft = deepcopy(draft.record)
        payload = after_draft["payload"]
        payload["order"] = [replacement["key"] if key == target_key else key for key in payload["order"]]
        payload["settings"].pop(target_key)
        payload["settings"][replacement["key"]] = deepcopy(replacement["settings"])
        after_draft["source_basis"] = {row["key"]: {"kind": row["kind"], "source_ref": deepcopy(row["source_ref"])} for row in after_rows}
        validate_payload(payload, {row["key"]: row for row in after_rows})
        _validate_draft(after_draft, after_meta)
        before_materials = self._verify_materials(rows, words, inventory)
        after_words = {key: val for key, val in words.items() if key != target_key} | candidate_words
        after_inventory = inventory | candidate_inventory
        after_materials = self._verify_materials(after_rows, after_words, after_inventory)
        # ABA-safe fingerprints include both source and saved draft revisions.
        current = self.store.snapshot()
        actual_meta, actual_draft = _snapshot_pair(current, paper_id)
        if (actual_meta, actual_draft) != (meta, draft):
            raise DraftConflictError("读取对照期间当前卷已变化，请重新选择候选。")
        plan = {"plan_id": "replacement-plan-" + uuid4().hex, "paper_id": paper_id,
            "target_key": target_key, "new_key": replacement["key"], "paper_revision": meta.revision,
            "before_projection": _projection(meta.record, rows), "after_projection": _projection(after_meta, after_rows),
            "before_item": deepcopy(by_key[target_key]), "after_item": deepcopy(replacement),
            "goal": deepcopy(dict(goal)), "match_evidence": match, "warnings": warnings,
            "settings_notice": "新来源使用自己的初始练习设置；旧来源的逐作答单元配分与答题行数不转移。"}
        plan["plan_sha256"] = digest(plan)
        private = {"plan": deepcopy(plan), "meta": meta, "draft": draft, "candidate_entry": deepcopy(candidate_entry),
                   "after_meta": after_meta, "after_draft": after_draft,
                   "before_materials": before_materials, "after_materials": after_materials,
                   "history": self._history(current, paper_id, meta).snapshot}
        with self._lock:
            self._plans[plan["plan_id"]] = private
            while len(self._plans) > 20:
                self._plans.pop(next(iter(self._plans)))
        return deepcopy(plan)

    def _plan(self, identity, checksum, *, consume=False):
        with self._lock:
            private = self._plans.get(identity)
            if private is None or private["plan"]["plan_sha256"] != checksum:
                raise DesktopStateError("替题对照已经失效，请重新检索并核对；不能重试旧确认。")
            if consume:
                self._plans.pop(identity)
            return deepcopy(private)

    def image(self, plan_id, plan_sha256, asset_id, *, side="after"):
        private = self._plan(plan_id, plan_sha256)
        if side not in {"before", "after"}:
            raise MixedPaperError("对照图片来源不正确。")
        item = private["plan"][side + "_item"]
        if item["kind"] != "personal_visual_theme":
            raise MixedPaperError("该对照来源请通过原来源阅读器打开。")
        rows = self._resolve(private["meta"].record)[0]
        if side == "after":
            _, current, words, _, _ = self.service.source_item_from_entry(private["candidate_entry"])
            _dependencies(self.facade, [current], words)
            if source_basis([current]) != source_basis([item]):
                raise DraftConflictError("对照图片的来源或标签已变化，请重新核对。")
        return self.service._visual_themes().resolve_asset(item, asset_id, audience="teacher")["bytes"]

    def _write(self, private, after_meta, after_draft, history, undo, redo, editing):
        identity = private["plan"]["paper_id"]
        def operation(value):
            check_read_cancelled()
            meta, draft = _snapshot_pair(value, identity)
            if (meta, draft) != (private["meta"], private["draft"]):
                raise DraftConflictError("确认期间来源引用或编排已被其他窗口修改，当前卷未覆盖。")
            history.guard(value)
            # Reopen original source roles inside the winning parent
            # transaction as well: a source-only edit need not change a draft.
            before_rows, before_words, before_inventory = self._resolve(meta.record)
            after_rows, after_words, after_inventory = self._resolve(after_meta)
            before_materials = self._verify_materials(before_rows, before_words, before_inventory)
            after_materials = self._verify_materials(after_rows, after_words, after_inventory)
            if (private.get("before_materials", before_materials) != before_materials
                    or private.get("after_materials", after_materials) != after_materials):
                raise DraftConflictError("保存前原件或图片角色已变化，当前卷未修改。")
            check_read_cancelled()
            history.stage(value, undo, redo)
            value["drafts"][identity] = deepcopy(after_meta)
            value["drafts"][identity + ":" + DRAFT_ID] = deepcopy(after_draft)
            # Old records/output artifacts remain evidence; active confirmation
            # cannot authorize a new source selection.
            value["drafts"].pop(identity + ":" + _ACTIVE, None)
            value["drafts"][identity + ":" + PAPER_HISTORY_ID] = {"pending": True, **deepcopy(editing)}
        def finalize(value):
            history.finalize(value, _draft_snapshot(value, identity), {"paper_id": identity})
            journal = PaperHistory(self.store, identity + ":" + PAPER_HISTORY_ID, "independent-paper:" + identity)
            journal.finalize(value, _draft_snapshot(value, identity + ":" + DRAFT_ID), _basis(after_meta))
        try:
            with publication_guard():
                value = self.store._update(operation, finalize=finalize)
        except OSError as exc:
            raise DesktopStateError("替题保存未能确认，旧确认已失效；请重读当前卷核对，不能自动重试或覆盖。") from exc
        return value

    def commit(self, plan_id, plan_sha256):
        # One-shot even after a failed/uncertain write: no replay of a stale plan.
        private = self._plan(plan_id, plan_sha256, consume=True)
        check_read_cancelled()
        plan, meta, draft = private["plan"], private["meta"], private["draft"]
        value = self.store.snapshot()
        if _snapshot_pair(value, plan["paper_id"]) != (meta, draft):
            raise DraftConflictError("当前卷已变化，请重新生成完整替题对照。")
        rows, words, inventory = self._resolve(meta.record)
        _, candidate, candidate_words, candidate_inventory, _ = self.service.source_item_from_entry(private["candidate_entry"])
        _dependencies(self.facade, [candidate], candidate_words)
        _duplicates(candidate, rows)
        if self._match(plan["goal"], candidate, candidate_words) != plan["match_evidence"]:
            raise DraftConflictError("目标依据已变化，请重新核对候选。")
        after_rows = [candidate if row["key"] == plan["target_key"] else row for row in rows]
        if source_basis(after_rows) != private["after_meta"]["source_basis"]:
            raise DraftConflictError("候选的原件、范围、标签或裁片已经变化，当前卷未修改。")
        if self._verify_materials(rows, words, inventory) != private["before_materials"] or self._verify_materials(
                after_rows, {key: val for key, val in words.items() if key != plan["target_key"]} | candidate_words,
                inventory | candidate_inventory) != private["after_materials"]:
            raise DraftConflictError("题面、共同材料、答案或图形已变化，请重新生成对照。")
        history = self._history(value, plan["paper_id"], meta)
        if history.snapshot != private["history"]:
            raise DraftConflictError("此卷来源版本记录已变化，请重新生成对照。")
        captured = self._capture(value, plan["paper_id"], meta, draft, plan["goal"])
        result = self._write(private, private["after_meta"], private["after_draft"], history,
                             history.undo + [entry(captured, "完整来源替题", plan["target_key"])], [], {"undo": [], "redo": []})
        return {"status": "saved", "paper_id": plan["paper_id"], "new_key": plan["new_key"],
                "revision": _draft_snapshot(result, plan["paper_id"]).revision,
                "version_id": captured["version_id"], "preview_invalidated": True}

    def versions(self, paper_id):
        value = self.store.snapshot()
        meta, draft = _snapshot_pair(value, paper_id)
        history = self._history(value, paper_id, meta)
        # An explicit fresh read is the recovery entry after an uncertain
        # restore. Reusing an old version-list confirmation cannot retry it.
        with self._lock:
            self._restore_attempts = {item for item in self._restore_attempts if item[0] != paper_id}
        versions = []
        for step in reversed(history.undo + history.redo):
            state = step["state"]
            versions.append({"version_id": state["version_id"], "title": state["draft"]["payload"]["settings_ui"]["title"],
                             "created_at": state["created_at"], "goal": deepcopy(state["goal"]), "action": step["action"]})
        return {"paper_id": paper_id, "revision": _version_binding(meta, draft, history), "versions": versions,
                "history_notice": "保留最近 30 次完整来源版本；恢复来源及已核验编排历史，无法核验的旧撤销记录仅保留原证据。先核验所有原件，恢复后须重新预览。"}

    def restore(self, paper_id, version_id, expected_revision):
        attempt = (paper_id, version_id, expected_revision)
        with self._lock:
            if attempt in self._restore_attempts:
                raise DesktopStateError("旧版本恢复确认已经失效，请重读当前卷和版本列表后核对；不能自动重试。")
            self._restore_attempts.add(attempt)
        check_read_cancelled()
        value = self.store.snapshot()
        meta, draft = _snapshot_pair(value, paper_id)
        history = self._history(value, paper_id, meta)
        if _version_binding(meta, draft, history) != expected_revision:
            raise DraftConflictError("此卷来源、编排或版本历史已变化，请重新读取版本列表。")
        steps = history.undo + history.redo
        matches = [step for step in steps if step["state"]["version_id"] == version_id]
        if len(matches) != 1:
            raise DesktopStateError("所选来源版本不存在或不属于此卷，不能恢复。")
        saved = matches[0]["state"]
        self._validate_history_state(saved)
        current_rows, current_words, current_inventory = self._resolve(meta.record)
        restored_rows, restored_words, restored_inventory = self._resolve(saved["meta"])
        self._verify_materials(current_rows, current_words, current_inventory)
        self._verify_materials(restored_rows, restored_words, restored_inventory)
        captured = self._capture(value, paper_id, meta, draft, saved["goal"])
        # A restored version remains evidence and an explicit recovery entry.
        # Move it to the recent end once, rather than deleting its old journal.
        retained = [step for step in steps if step["state"]["version_id"] != version_id] + matches
        private = {"plan": {"paper_id": paper_id}, "meta": meta, "draft": draft}
        result = self._write(private, saved["meta"], saved["draft"], history,
                             retained + [entry(captured, "恢复完整来源版本", None)], [], saved["editing_history"])
        return {"status": "saved", "paper_id": paper_id,
                "revision": _draft_snapshot(result, paper_id).revision,
                "version_id": captured["version_id"], "preview_invalidated": True}

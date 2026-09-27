"""One source-bound mixed-paper draft, with session-local durable undo."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import uuid

from .desktop_state import DesktopStateError, DraftConflictError, _basket_snapshot, _draft_snapshot

DRAFT_ID = "paper-mixed-current"
DRAFT_SCHEMA = "shchem.desktop-mixed-ui-draft.v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def binding(snapshot):
    return {"draft_id": DRAFT_ID, "revision": snapshot.revision, "content_sha256": snapshot.content_sha256}


def check_binding_value(value, claimed, request=None):
    """Used inside the store transaction as well as at expensive-work gates."""
    current = _draft_snapshot(value, DRAFT_ID)
    if claimed is None and current.record is None:
        return  # Existing API clients without a saved mixed draft remain supported.
    if not isinstance(claimed, dict) or claimed != binding(current) or current.record is None:
        raise DraftConflictError("当前卷草稿已变化，请重新读取并核对两版预览。")
    if request is not None:
        payload = current.record.get("payload", {})
        ui = payload.get("settings_ui", {})
        expected = {**ui, "section_order": payload.get("order"),
                    "settings_by_key": {key: payload.get("settings", {}).get(key) for key in payload.get("order", [])}}
        actual = {key: request.get(key) for key in expected}
        if digest(actual) != digest(expected):
            raise DraftConflictError("预览请求与已保存草稿不一致，请先保存本次编排。")


def validate_payload(payload, items=None):
    if not isinstance(payload, dict) or payload.get("schema_version") != DRAFT_SCHEMA:
        raise DesktopStateError("旧草稿版本无法读取，原稿未覆盖。")
    if set(payload) - {"schema_version", "order", "excluded", "settings", "settings_ui"}:
        raise DesktopStateError("草稿含未知编排信息，原稿未覆盖。")
    order, excluded, settings = (payload.get(name) for name in ("order", "excluded", "settings"))
    if (not isinstance(order, list) or not isinstance(excluded, list) or not isinstance(settings, dict)
        or len(order) + len(excluded) > 100
        or any(not isinstance(key, str) or not key for key in order + excluded)
        or len(set(order + excluded)) != len(order + excluded)
        or set(settings) != set(order + excluded)):
        raise DesktopStateError("草稿题目顺序或移除记录不完整，原稿未覆盖。")
    ui = payload.get("settings_ui", {})
    if (not isinstance(ui, dict) or set(ui) - {"title", "subtitle", "mode", "duration_minutes", "show_question_scores"}
        or not isinstance(ui.get("title", ""), str) or len(ui.get("title", "")) > 120
        or not isinstance(ui.get("subtitle", ""), str) or len(ui.get("subtitle", "")) > 180
        or ui.get("mode", "daily_practice") not in {"daily_practice", "mock_exam"}
        or type(ui.get("duration_minutes", 40)) is not int or not 1 <= ui.get("duration_minutes", 40) <= 300
        or type(ui.get("show_question_scores", False)) is not bool):
        raise DesktopStateError("草稿卷面设置无法读取，原稿未覆盖。")
    for key, raw in settings.items():
        if not isinstance(raw, dict):
            raise DesktopStateError("草稿配分无法读取，原稿未覆盖。")
        kind = (items or {}).get(key, {}).get("kind")
        allowed = ({"points"} if kind == "word_question" else {"use_source_scores"} if kind == "personal_visual_theme"
                   else {"score_per_atomic", "answer_space_lines", "atomic_settings"} if kind == "core_theme"
                   else {"points", "use_source_scores", "score_per_atomic", "answer_space_lines", "atomic_settings"})
        if set(raw) - allowed:
            raise DesktopStateError("配分设置与题目来源类型不一致。")
        if kind == "personal_visual_theme" and raw != {"use_source_scores": True}:
            raise DesktopStateError("图片主题须保留来源分值和答题区。")
        if "points" in raw and (type(raw["points"]) not in (int, float) or not math.isfinite(raw["points"]) or not 0 < raw["points"] <= 100):
            raise DesktopStateError("本题配分不在可用范围内。")
        for name, lower, upper in (("score_per_atomic", 1, 30), ("answer_space_lines", 0, 20)):
            if name in raw and (type(raw[name]) is not int or not lower <= raw[name] <= upper):
                raise DesktopStateError("主题配分或答题行数不在可用范围内。")
        atomic = raw.get("atomic_settings", {})
        if not isinstance(atomic, dict) or len(atomic) > 10000:
            raise DesktopStateError("原有逐题配分记录无法读取。")
        for atomic_key, setting in atomic.items():
            if (not isinstance(atomic_key, str) or not atomic_key or not isinstance(setting, dict)
                or set(setting) - {"score", "answer_space_lines"}
                or ("score" in setting and (type(setting["score"]) not in (int, float) or not math.isfinite(setting["score"]) or not 0 < setting["score"] <= 100))
                or ("answer_space_lines" in setting and (type(setting["answer_space_lines"]) is not int or not 0 <= setting["answer_space_lines"] <= 20))):
                raise DesktopStateError("原有逐题配分记录无法读取。")


class MixedPaperDraftSession:
    HISTORY_LIMIT = 30

    def __init__(self, store):
        self.store = store
        self.current = None
        self.items = {}
        self.basis = {}
        self.basket = None
        self.history = []
        self.closed = False
        self._recovery = None

    @property
    def undo_count(self):
        return len(self.history)

    @property
    def undo_action(self):
        return self.history[-1][1] if self.history else ""

    @property
    def can_restart(self):
        return not self.closed and self._recovery is not None

    def invalidate(self, *, preserve_recovery=False):
        self.history.clear()
        self.current = None
        if not preserve_recovery:
            self._recovery = None

    def close(self):
        self.invalidate()
        self.closed = True

    def read(self, projection):
        if self.closed:
            raise DesktopStateError("当前组卷编辑已关闭，请重新打开。")
        self._recovery = None
        try:
            value = self.store.snapshot()
            basket = _basket_snapshot(value)
            if projection.get("basket_sha256") != basket.content_sha256:
                raise DraftConflictError("读取期间题篮已变化，请重新读取。")
            rows = projection.get("items")
            if (projection.get("schema_version") != "shchem.desktop-mixed-basket.v1" or not isinstance(rows, list) or len(rows) > 100
                or any(not isinstance(row, dict) or not isinstance(row.get("key"), str) or not row["key"]
                       or row.get("kind") not in {"word_question", "core_theme", "personal_visual_theme"}
                       or not isinstance(row.get("source_ref"), dict) or not isinstance(row.get("content"), dict)
                       or not isinstance(row.get("settings"), dict) for row in rows)
                or len({row["key"] for row in rows}) != len(rows)
                or {row["key"] for row in rows} != {row["key"] for row in basket.rows}):
                raise DesktopStateError("来源投影无法核验，请重新读取。")
            items = {row["key"]: deepcopy(row) for row in rows}
            basis = {key: {"kind": row["kind"], "source_ref": deepcopy(row["source_ref"])} for key, row in items.items()}
            current = _draft_snapshot(value, DRAFT_ID)
            if current.record is not None:
                try:
                    validate_payload(current.record.get("payload"), items)
                    old_basis = current.record.get("source_basis", {})
                    if not isinstance(old_basis, dict) or any(key in basis and ref != basis[key] for key, ref in old_basis.items()):
                        raise DraftConflictError("旧草稿的来源身份已变化，不能将原有编排套到新来源。")
                except DesktopStateError:
                    # Only a successfully read record and verified projection can
                    # propose a restart. Never repair an unreadable state file.
                    self._recovery = (current, basket, deepcopy(projection))
                    raise
            reset = self.current is not None and (
                binding(self.current) != binding(current) or self.basis != basis
                or self.basket.revision != basket.revision)
            if reset:
                self.history.clear()
            self.current, self.items, self.basis, self.basket = current, items, basis, basket
            return {"record": deepcopy(current.record), "history_reset": reset}
        except Exception:
            self.invalidate(preserve_recovery=True)
            raise

    def restart(self, projection):
        """Explicitly archive the old record and start from freshly read sources.

        The caller obtains another production projection after confirmation.
        Both it and the draft/basket versions must still match the proposal.
        """
        try:
            if not self.can_restart:
                raise DesktopStateError("请先重新读取，核对可恢复的当前来源。")
            before, basket, proposed = self._recovery
            if digest(projection) != digest(proposed):
                raise DraftConflictError("确认期间来源已变化，请重新读取后再开始。")
            rows = projection["items"]
            payload = {"schema_version": DRAFT_SCHEMA, "order": [row["key"] for row in rows],
                       "excluded": [], "settings": {row["key"]: deepcopy(row["settings"]) for row in rows},
                       "settings_ui": {"title": "化学巩固练习", "subtitle": "", "mode": "daily_practice",
                                       "duration_minutes": 40, "show_question_scores": False}}
            items = {row["key"]: deepcopy(row) for row in rows}
            validate_payload(payload, items)
            basis = {key: {"kind": row["kind"], "source_ref": deepcopy(row["source_ref"])} for key, row in items.items()}
            record = {"kind": "paper", "status": "draft", "payload": payload, "source_basis": basis}
            archive_id = "paper-mixed-before-reset-" + uuid.uuid4().hex

            def replace(value):
                current = _draft_snapshot(value, DRAFT_ID)
                actual_basket = _basket_snapshot(value)
                if binding(current) != binding(before) or actual_basket != basket:
                    raise DraftConflictError("确认期间草稿或题篮已变化，旧稿未覆盖；请重新读取。")
                if archive_id in value["drafts"]:
                    raise DesktopStateError("旧稿保留记录未能创建，请重新读取。")
                value["drafts"][archive_id] = deepcopy(before.record)
                value["drafts"][DRAFT_ID] = deepcopy(record)

            value = self.store._update(replace)
            self.current = _draft_snapshot(value, DRAFT_ID)
            self.items, self.basis, self.basket = items, basis, basket
            self.history.clear()
            self._recovery = None
            return {"record": deepcopy(self.current.record), "archive_id": archive_id}
        except Exception:
            self.invalidate()
            raise

    def _guard(self, value):
        basket = _basket_snapshot(value)
        if self.basket is None or (basket.revision, basket.content_sha256) != (self.basket.revision, self.basket.content_sha256):
            raise DraftConflictError("来源题篮已变化，请重新读取；旧撤销记录已失效。")

    def check(self):
        try:
            if self.closed or self.current is None:
                raise DesktopStateError("请先重新读取已保存草稿。")
            value = self.store.snapshot()
            self._guard(value)
            check_binding_value(value, binding(self.current))
            return binding(self.current)
        except Exception:
            self.invalidate()
            raise

    def save(self, payload, *, action="修改编排", selected=None, remember=True):
        try:
            if self.closed or self.current is None:
                raise DesktopStateError("请先重新读取已保存草稿。")
            validate_payload(payload, self.items)
            if set(payload["order"] + payload["excluded"]) != set(self.items):
                raise DesktopStateError("草稿项目与当前来源不一致，请重新读取。")
            before = self.current
            record = {**(before.record or {}), "kind": "paper", "status": "draft",
                      "payload": deepcopy(payload), "source_basis": deepcopy(self.basis)}
            current = self.store.compare_draft(before, record, guard=self._guard)
            changed = binding(current) != binding(before)
            if changed and remember and before.record is not None:
                self.history.append((before.record, action, selected))
                self.history = self.history[-self.HISTORY_LIMIT:]
            self.current = current
            return {"changed": changed, "binding": binding(current)}
        except Exception:
            self.invalidate()
            raise

    def undo(self):
        try:
            if self.closed or self.current is None or not self.history:
                raise DesktopStateError("本次编辑没有可撤销操作。")
            record, action, selected = self.history[-1]
            current = self.store.compare_draft(self.current, record, guard=self._guard)
            self.history.pop()
            self.current = current
            return {"record": deepcopy(current.record), "action": action, "selected": selected}
        except Exception:
            self.invalidate()
            raise

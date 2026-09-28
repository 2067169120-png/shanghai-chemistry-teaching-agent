"""Saved paper references over the existing mixed-paper engine and state lock.

Only source locators are retained here. Source content is always resolved by
the original readers; the public basket and exam task scopes are never swapped.
"""
from __future__ import annotations

from copy import deepcopy
import re
from uuid import uuid4

from .desktop_mixed_paper_drafts import DRAFT_ID, DRAFT_SCHEMA, digest, validate_payload
from .desktop_mixed_paper_service import MixedPaperService, _ACTIVE
from .desktop_state import (
    DesktopStateError, DesktopStateStore, DraftConflictError, _basket_snapshot,
    _draft_snapshot, utc_now,
)
from .reader_cancellation import read_cancel_scope

SCHEMA = "shchem.independent-paper.v1"
PREFIX = "independent-paper-"
_ID = re.compile(r"independent-paper-[0-9a-f]{32}")
_PREVIEW = re.compile(r"mixed-preview-[0-9a-f]{32}")


def source_basis(rows):
    return {row["key"]: {"kind": row["kind"], "source_ref": deepcopy(row["source_ref"]),
                         "content_sha256": digest(row["content"]),
                         "dependency_sha256": row.get("dependency_sha256")} for row in rows}


def _dependencies(facade, rows, words):
    # Word ranges have their own revision, while teacher/automatic labels and
    # their taxonomy change independently. Pin both without copying a bank.
    catalog = None
    if words:
        reader = getattr(facade._word_questions(), "_read_attribute_catalog", None)
        catalog = reader() if callable(reader) else None
    for row in rows:
        if row["kind"] == "word_question":
            raw = words[row["key"]]
            row["dependency_sha256"] = digest({"catalog": catalog, **{key: raw.get(key) for key in
                ("attributes", "attribute_stale", "attribute_protected", "attribute_warning")}})
    return rows


def _references(rows):
    """Canonical locators also pin core snapshots missing from older baskets."""
    result = []
    for row in rows:
        ref = row["source_ref"]
        value = {"key": row["key"], "item_kind": row["kind"],
                 "title_zh": row.get("title_zh", "标题待核对"),
                 "source_zh": row.get("source_zh", "来源待核对")}
        if row["kind"] == "word_question":
            value.update(source_ref=deepcopy(ref), word_selection={
                "key": ref["key"], "revision": ref["revision"], "points": row["settings"]["points"]})
        elif row["kind"] == "personal_visual_theme":
            value.update(source_ref=deepcopy(ref), visual_selections=deepcopy(ref["selections"]))
        else:
            value.update({name: ref[name] for name in
                          ("scope", "source_identity_sha256", "data_snapshot_id")})
        result.append(value)
    return result


def _record(value, identity):
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        raise DesktopStateError("保存卷标识不正确。")
    record = _draft_snapshot(value, identity).record
    if (not isinstance(record, dict) or record.get("schema_version") != SCHEMA
            or not isinstance(record.get("items"), list) or not 1 <= len(record["items"]) <= 100
            or not isinstance(record.get("source_basis"), dict)
            or digest(record["items"]) != record.get("selection_sha256")):
        raise DesktopStateError("保存卷的来源引用无法核验；原记录保留，请核对后重试。")
    keys = [row.get("key") if isinstance(row, dict) else None for row in record["items"]]
    if (any(not isinstance(key, str) or not key for key in keys)
            or len(set(keys)) != len(keys) or set(keys) != set(record["source_basis"])):
        raise DesktopStateError("保存卷题目身份缺失或重复；不会从公共题篮补题。")
    return record


def _validate_draft(record, meta):
    if not isinstance(record, dict) or record.get("kind") != "paper":
        raise DesktopStateError("此卷编排草稿缺失或不兼容，原记录保留；不能自动重建覆盖。")
    expected = {key: {field: ref[field] for field in ("kind", "source_ref")}
                for key, ref in meta["source_basis"].items()}
    validate_payload(record.get("payload"), expected)
    payload = record["payload"]
    if (record.get("source_basis") != expected
            or set(payload["order"] + payload["excluded"]) != set(expected)):
        raise DesktopStateError("当前卷编排与保存的来源身份不一致；原稿保留，请核对后新建。")


class _PaperState:
    """A complete mixed-engine state view, committed under the real OS lock."""

    def __init__(self, parent, identity):
        self._parent, self.identity = parent, identity
        self._lock, self.root = parent._lock, parent.root
        value = parent.snapshot()
        self._selection = _draft_snapshot(value, identity)
        self.record = _record(value, identity)
        self._prefix = identity + ":"

    def _view(self, value):
        current = _draft_snapshot(value, self.identity)
        if (current.revision, current.content_sha256) != (self._selection.revision, self._selection.content_sha256):
            raise DraftConflictError("此卷保存的来源引用已变化，请重新选择打开；未覆盖原稿。")
        if current.record != self.record:
            raise DraftConflictError("此卷的来源引用与读取版本不一致，请重新选择打开。")
        _record(value, self.identity)
        drafts = {key[len(self._prefix):]: deepcopy(record) for key, record in value["drafts"].items()
                  if key.startswith(self._prefix)}
        if any(key not in {DRAFT_ID, _ACTIVE} and not _PREVIEW.fullmatch(key) for key in drafts):
            raise DesktopStateError("此卷含无法核验的作用域记录，请保留后核对。")
        _validate_draft(drafts.get(DRAFT_ID), self.record)
        revisions = {key[len(self._prefix):]: revision for key, revision in value.get("draft_revisions", {}).items()
                     if key.startswith(self._prefix)}
        return {**deepcopy(value), "basket": deepcopy(self.record["items"]),
                "basket_revision": self.record["selection_sha256"][:32],
                "drafts": drafts, "draft_revisions": revisions}

    def snapshot(self):
        return self._view(self._parent.snapshot())

    def basket(self):
        return self.snapshot()["basket"]

    def _update(self, operation, *, basket_write=False):
        if basket_write:
            raise DesktopStateError("当前卷不能修改公共题篮或已保存的来源引用。")

        def transact(value):
            before = self._view(value)
            scoped = deepcopy(before)
            if operation(scoped) is False:
                return False
            if ({key: row for key, row in scoped.items() if key != "drafts"}
                    != {key: row for key, row in before.items() if key != "drafts"}
                    or not isinstance(scoped.get("drafts"), dict)
                    or set(before["drafts"]) - set(scoped["drafts"])):
                raise DesktopStateError("当前卷只能保存自己的草稿和预览。")
            changed = {key for key in scoped["drafts"]
                       if key not in before["drafts"] or scoped["drafts"][key] != before["drafts"][key]}
            for key in changed:
                record = scoped["drafts"][key]
                if key == DRAFT_ID:
                    _validate_draft(record, self.record)
                elif key == _ACTIVE:
                    if (not isinstance(record, dict) or set(record) != {"preview_id", "preview_hash"}
                            or record.get("preview_id") not in scoped["drafts"]
                            or not _PREVIEW.fullmatch(str(record.get("preview_id", "")))
                            or scoped["drafts"][record["preview_id"]].get("preview_hash") != record["preview_hash"]):
                        raise DesktopStateError("当前卷的活动预览与保存记录不一致。")
                elif (not _PREVIEW.fullmatch(key) or not isinstance(record, dict)
                      or record.get("preview_id") != key):
                    raise DesktopStateError("当前卷不能写入其他业务草稿。")
                elif key not in before["drafts"] and any(
                    other == key or other.endswith(":" + key) for other in value["drafts"]
                ):
                    raise DesktopStateError("预览标识已属于另一份卷或任务。")
            if not changed:
                return False
            for key in changed:
                value["drafts"][self._prefix + key] = deepcopy(scoped["drafts"][key])

        return self._view(self._parent._update(transact))

    # Reuse the established CAS implementation; it only needs snapshot/_update.
    compare_draft = DesktopStateStore.compare_draft
    draft_snapshot = DesktopStateStore.draft_snapshot
    save_draft = DesktopStateStore.save_draft


class IndependentPaperSession:
    """Facade adapter for the unchanged mixed composer and raster-number editor."""
    independent_paper = True

    def __init__(self, facade, identity):
        self.facade, self.paper_id = facade, identity
        self.state_store = _PaperState(facade.state_store, identity)
        self.number_region_state = facade.state_store
        self.service = MixedPaperService(self)

    def __getattr__(self, name):
        return getattr(self.facade, name)

    def _verify_independent_sources(self, rows, words):
        self.state_store.snapshot()
        if source_basis(_dependencies(self.facade, rows, words)) != self.state_store.record["source_basis"]:
            raise DraftConflictError("此卷的源文件、题目范围或标签依据已变化；旧稿保留，请核对来源后明确新建。")

    def _call(self, method, *args):
        with read_cancel_scope(getattr(self.facade, "_reader_stop_event", None)):
            return getattr(self.service, method)(*args)

    def basket(self):
        return self.state_store.basket()

    def paper_basket_projection(self):
        return self._call("projection")

    def open_mixed_paper_draft_session(self):
        return self._call("open_draft_session")

    def create_paper_preview(self, request):
        return self._call("create_preview", request)

    def prepare_mixed_paper_pagination(self, identity, fingerprint):
        return self._call("prepare_pagination", identity, fingerprint)

    def paper_preview_image(self, identity, image):
        return self._call("image", identity, image)

    def approve_paper_preview(self, identity, fingerprint):
        return self._call("approve", identity, fingerprint)

    def export_paper_preview(self, identity, fingerprint, *_unused):
        return self._call("export", identity, fingerprint)


class IndependentPaperLibrary:
    def __init__(self, facade):
        self.facade, self.store = facade, facade.state_store

    def list(self):
        value = self.store.snapshot()
        result = []
        for key, record in value["drafts"].items():
            if _ID.fullmatch(key):
                try:
                    meta = _record(value, key)
                    draft = _draft_snapshot(value, key + ":" + DRAFT_ID).record
                    payload = (draft or {}).get("payload")
                    _validate_draft(draft, meta)
                    result.append({"id": key, "title": payload["settings_ui"].get("title") or "未命名卷",
                                   "count": len(payload["order"]), "legacy": False,
                                   "created_at": meta.get("created_at", ""), "available": True})
                except (DesktopStateError, KeyError, TypeError):
                    result.append({"id": key, "title": "保存卷待核对", "count": None,
                                   "legacy": False, "created_at": "", "available": False})
            elif key == "paper-current" or key == DRAFT_ID or key.startswith("paper-mixed-before-reset-"):
                payload = record.get("payload", {}) if isinstance(record, dict) else {}
                payload = payload if isinstance(payload, dict) else {}
                ui = payload.get("settings_ui", {}) if isinstance(payload, dict) else {}
                ui = ui if isinstance(ui, dict) else {}
                result.append({"id": key, "title": ui.get("title") or payload.get("title") or "旧版组卷草稿",
                               "count": None, "legacy": True, "created_at": "", "available": True})
        return sorted(result, key=lambda row: (not row["legacy"], row["created_at"], row["id"]), reverse=True)

    def open(self, identity):
        session = IndependentPaperSession(self.facade, identity)
        projection = session.paper_basket_projection()
        editor = session.open_mixed_paper_draft_session()
        editor.read(projection)
        if editor.current.record is None:
            raise DesktopStateError("此卷的编排草稿缺失，原来源引用保留；不能自动重建覆盖。")
        return session

    def create(self, *, legacy_id=None):
        value = self.store.snapshot()
        basket = _basket_snapshot(value)
        old = _draft_snapshot(value, legacy_id) if legacy_id else None
        service = MixedPaperService(self.facade)
        if old is None:
            if not basket.rows:
                raise DesktopStateError("公共题篮为空，请先加入题目；已保存卷仍可继续打开。")
            rows, words, _, _ = service._resolve(list(basket.rows))
            _dependencies(self.facade, rows, words)
            payload = {"schema_version": DRAFT_SCHEMA, "order": [row["key"] for row in rows],
                       "excluded": [], "settings": {row["key"]: deepcopy(row["settings"]) for row in rows},
                       "settings_ui": {"title": "化学巩固练习", "subtitle": "", "mode": "daily_practice",
                                       "duration_minutes": 40, "show_question_scores": False}}
        else:
            if (legacy_id != DRAFT_ID and not legacy_id.startswith("paper-mixed-before-reset-")):
                raise DesktopStateError("此旧版小问草稿暂不能无损转为完整题目段；原稿保留，可明确从题篮新建。")
            record = old.record or {}
            payload = deepcopy(record.get("payload"))
            validate_payload(payload)
            basis = record.get("source_basis")
            keys = payload["order"] + payload["excluded"]
            if not isinstance(basis, dict) or set(basis) != set(keys) or not keys:
                raise DesktopStateError("旧稿缺少完整来源引用，不能按当前题篮猜测；原稿保留，可明确新建。")
            try:
                candidates = _references([{"key": key, **basis[key], "settings": payload["settings"][key]} for key in keys])
                rows, words, _, _ = service._resolve(candidates)
                _dependencies(self.facade, rows, words)
            except (KeyError, TypeError) as exc:
                raise DesktopStateError("旧稿来源定位不完整，原稿保留；请核对来源后新建。") from exc
            if {row["key"]: {"kind": row["kind"], "source_ref": row["source_ref"]} for row in rows} != basis:
                raise DraftConflictError("旧稿来源已经变化，不能继续套用旧编排；原稿保留。")
        items = {row["key"]: row for row in rows}
        validate_payload(payload, items)
        refs = _references(rows)
        # Re-resolve canonical references once, before writing any record.
        verified, words, _, _ = service._resolve(refs)
        _dependencies(self.facade, verified, words)
        if source_basis(rows) != source_basis(verified):
            raise DraftConflictError("创建期间来源已变化，请重新读取；旧稿未覆盖。")
        identity = PREFIX + uuid4().hex
        meta = {"schema_version": SCHEMA, "items": refs, "selection_sha256": digest(refs),
                "source_basis": source_basis(rows), "created_at": utc_now(), "legacy_origin": legacy_id}
        draft = {"kind": "paper", "status": "draft", "payload": payload,
                 "source_basis": {key: {"kind": row["kind"], "source_ref": deepcopy(row["source_ref"])}
                                  for key, row in items.items()}}

        def create(value):
            if old is None and _basket_snapshot(value) != basket:
                raise DraftConflictError("创建期间公共题篮已变化，请重新读取；未创建卷。")
            if old is not None and _draft_snapshot(value, legacy_id) != old:
                raise DraftConflictError("继续期间旧稿已变化，未复制或覆盖；请重新读取。")
            if identity in value["drafts"] or identity + ":" + DRAFT_ID in value["drafts"]:
                raise DraftConflictError("保存卷标识已存在，请重新新建。")
            value["drafts"][identity] = deepcopy(meta)
            value["drafts"][identity + ":" + DRAFT_ID] = deepcopy(draft)

        self.store._update(create)
        return IndependentPaperSession(self.facade, identity)

"""Exact, session-only working copies of personal textbook notes.

This module performs no IO and knows nothing about Qt, services or durable
schemas. A saved view is evidence for a baseline, never a container for drafts.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping


NEW_NOTE = "NEW"
DraftKey = tuple[str, str, str, str]


class NoteEditConflict(ValueError):
    """The saved owner no longer matches an edit's frozen baseline."""


@dataclass(frozen=True)
class NoteFields:
    title: str
    text: str
    pdf_page: int

    def __post_init__(self):
        if not isinstance(self.title, str) or not isinstance(self.text, str):
            raise TypeError("笔记标题和正文必须是原始文本。")
        if type(self.pdf_page) is not int:
            raise TypeError("笔记PDF页必须是整数。")


@dataclass(frozen=True)
class NoteDraft:
    key: DraftKey
    baseline: NoteFields
    fields: NoteFields
    base_section_revision: str
    base_note_revision: str | None
    base_study_revision: str
    base_pdf_start: int
    base_pdf_end: int
    pdf_start: int
    pdf_end: int
    saved_fields: NoteFields | None
    saved_source_revision: str
    saved_section_revision: str | None
    saved_note_revision: str | None
    source_name: str = ""
    section_title: str = ""
    confirmed: bool = False
    conflict: str | None = None
    edit_serial: int = 0

    @property
    def dirty(self) -> bool:
        return self.fields != self.baseline

    @property
    def note_id(self) -> str | None:
        return None if self.key[3] == NEW_NOTE else self.key[3]

    @property
    def selection(self) -> dict[str, str]:
        return {"source_id": self.key[0], "revision": self.key[1]}

    @property
    def page_in_range(self) -> bool:
        return self.pdf_start <= self.fields.pdf_page <= self.pdf_end


@dataclass(frozen=True)
class SaveToken:
    key: DraftKey
    fields: NoteFields
    baseline: NoteFields
    base_section_revision: str
    base_note_revision: str | None
    base_pdf_start: int
    base_pdf_end: int
    study_revision: str
    confirmed: bool
    edit_serial: int
    token_id: int
    entry_serial: int
    known_note_ids: tuple[str, ...]

    @property
    def selection(self) -> dict[str, str]:
        return {"source_id": self.key[0], "revision": self.key[1]}

    @property
    def section_id(self) -> str:
        return self.key[2]

    @property
    def note_id(self) -> str | None:
        return None if self.key[3] == NEW_NOTE else self.key[3]


@dataclass
class _Submission:
    token: SaveToken
    active: bool = True
    started: bool = False


def _required_text(value, label):
    if not isinstance(value, str) or not value:
        raise NoteEditConflict(f"{label}缺失，无法绑定笔记草稿。")
    return value


def _source(view):
    book = view["book"]
    source_id = _required_text(book["source_id"], "教材来源ID")
    revision = _required_text(book["revision"], "教材PDF版本")
    if book.get("source_sha256", revision) != revision:
        raise NoteEditConflict("教材PDF版本与来源指纹不一致。")
    return source_id, revision


def _row(rows, field, value):
    return next((row for row in rows if row[field] == value), None)


def _fields(note):
    return NoteFields(note["title"], note["text"], note["pdf_page"])


def _section(view, section_id):
    source_id, revision = _source(view)
    section = _row(view["sections"], "section_id", section_id)
    if section is None:
        return None
    if (section.get("source_id", source_id) != source_id
            or section.get("source_sha256", revision) != revision):
        raise NoteEditConflict("章节与教材来源不一致，未重新绑定草稿。")
    _required_text(section["revision"], "章节版本")
    if (type(section["pdf_start"]) is not int or type(section["pdf_end"]) is not int
            or not 1 <= section["pdf_start"] <= section["pdf_end"]):
        raise NoteEditConflict("章节PDF页范围无法核对。")
    return section


class NoteEditSession:
    """A ledger owned by one editor session; all public snapshots are immutable."""

    def __init__(self):
        self._drafts: dict[DraftKey, NoteDraft] = {}
        self._entry_serials: dict[DraftKey, int] = {}
        self._last: dict[tuple[str, str, str], DraftKey] = {}
        self._visited: list[DraftKey] = []
        self._submissions: dict[int, _Submission] = {}
        self._serial = 0

    def _next_serial(self):
        self._serial += 1
        return self._serial

    @staticmethod
    def _fresh(view, section_id, note_id):
        source_id, revision = _source(view)
        section = _section(view, section_id)
        if section is None:
            raise NoteEditConflict("原章节已缺失，草稿保留，请核对后取消本次修改。")
        note = _row(view["notes"], "note_id", note_id) if note_id is not None else None
        if note_id is not None and (note is None or note["section_id"] != section_id):
            raise NoteEditConflict("原笔记已缺失或更换章节，草稿保留。")
        note_revision = _required_text(note["revision"], "笔记版本") if note else None
        fields = _fields(note) if note else NoteFields("", "", section["pdf_start"])
        return NoteDraft(
            key=(source_id, revision, section_id, note_id or NEW_NOTE),
            baseline=fields, fields=fields, base_section_revision=section["revision"],
            base_note_revision=note_revision,
            base_study_revision=_required_text(view["study_revision"], "个人整理版本"),
            base_pdf_start=section["pdf_start"], base_pdf_end=section["pdf_end"],
            pdf_start=section["pdf_start"], pdf_end=section["pdf_end"],
            saved_fields=_fields(note) if note else None,
            saved_source_revision=revision, saved_section_revision=section["revision"],
            saved_note_revision=note_revision,
            source_name=view["book"].get("source_name", ""),
            section_title=section.get("title", ""),
        )

    def _visit(self, key):
        self._last[key[:3]] = key
        if key in self._visited:
            self._visited.remove(key)
        self._visited.append(key)

    def open(self, view: Mapping, section_id: str, note_id: str | None = None) -> NoteDraft:
        """Bind a saved owner, or restore its exact existing working copy."""
        if note_id == NEW_NOTE:
            note_id = None
        source_id, revision = _source(view)
        key = (source_id, revision, section_id, note_id or NEW_NOTE)
        self.reconcile(view)
        if key not in self._drafts:
            self._drafts[key] = self._fresh(view, section_id, note_id)
            self._entry_serials[key] = self._next_serial()
        self._visit(key)
        return self._drafts[key]

    def get(self, key: DraftKey) -> NoteDraft | None:
        return self._drafts.get(key)

    def update(self, key: DraftKey, *, title: str, text: str, pdf_page: int,
               confirmed: bool | None = None) -> NoteDraft:
        """Record raw editor fields. A real field edit always clears confirmation."""
        draft = self._drafts[key]
        fields = NoteFields(title, text, pdf_page)
        if confirmed is not None and type(confirmed) is not bool:
            raise TypeError("原页核对状态必须是明确的布尔选择。")
        changed = fields != draft.fields
        checked = draft.confirmed if confirmed is None else confirmed
        updated = replace(draft, fields=fields,
                          confirmed=False if changed or draft.conflict else checked,
                          edit_serial=draft.edit_serial + int(changed))
        self._drafts[key] = updated
        return updated

    def dirty_keys(self) -> tuple[DraftKey, ...]:
        return tuple(key for key, draft in self._drafts.items() if draft.dirty)

    def first_dirty(self) -> NoteDraft | None:
        return next((draft for draft in self._drafts.values() if draft.dirty), None)

    def last_key(self, view: Mapping, section_id: str) -> DraftKey | None:
        return self._last.get((*_source(view), section_id))

    def last_key_for_source(self, source_id: str, section_id: str) -> DraftKey | None:
        """Find an old-SHA draft without rebinding it to the current book."""
        return next((key for key in reversed(self._visited)
                     if key[0] == source_id and key[2] == section_id
                     and key in self._drafts and self._drafts[key].dirty), None)

    def _reconcile_one(self, draft, view):
        source_id, revision = _source(view)
        if source_id != draft.key[0]:
            return draft
        if revision != draft.key[1]:
            return replace(draft, confirmed=False, saved_source_revision=revision,
                           conflict="教材PDF版本已变化，原版本草稿保留，不能保存到新版本。")
        section = _section(view, draft.key[2])
        if section is None:
            return replace(draft, confirmed=False, saved_source_revision=revision,
                           saved_section_revision=None, saved_note_revision=None,
                           saved_fields=None,
                           conflict="原章节已缺失，草稿保留，请核对后取消本次修改。")
        note = _row(view["notes"], "note_id", draft.note_id) if draft.note_id else None
        note_revision = _required_text(note["revision"], "笔记版本") if note else None
        saved_fields = _fields(note) if note else None
        observed = dict(saved_source_revision=revision,
                        saved_section_revision=section["revision"],
                        saved_note_revision=note_revision, saved_fields=saved_fields,
                        pdf_start=section["pdf_start"], pdf_end=section["pdf_end"])
        if draft.note_id and (note is None or note["section_id"] != draft.key[2]):
            return replace(draft, **observed, confirmed=False,
                           conflict="原笔记已缺失或更换章节，草稿保留，不能自动重建或换章。")
        changed_section = (section["revision"] != draft.base_section_revision
                           or section["pdf_start"] != draft.base_pdf_start
                           or section["pdf_end"] != draft.base_pdf_end)
        changed_note = (note_revision != draft.base_note_revision
                        or (note is not None and saved_fields != draft.baseline))
        if changed_section or changed_note:
            if not draft.dirty:
                self._forget_submissions(draft.key)
                self._entry_serials[draft.key] = self._next_serial()
                return self._fresh(view, draft.key[2], draft.note_id)
            reason = ("章节版本或页范围已变化，原草稿保留，请核对已保存版本。" if changed_section
                      else "笔记已被其他操作修改，原草稿保留，请核对已保存版本。")
            if not section["pdf_start"] <= draft.fields.pdf_page <= section["pdf_end"]:
                reason += "草稿原页已超出当前章节范围。"
            return replace(draft, **observed, confirmed=False, conflict=reason)
        return replace(draft, **observed, conflict=None)

    def reconcile(self, view: Mapping) -> None:
        """Refresh conflict evidence; only a started exact submission can reconcile a save."""
        source_id, revision = _source(view)
        # A cancelled callback may follow a completed service call. Readback may
        # acknowledge only submissions whose operation actually began, never a
        # speculative token or an unrelated external edit with the same title.
        for submission in list(reversed(tuple(self._submissions.values()))):
            token = submission.token
            if submission.started and token.key[:2] == (source_id, revision):
                result = self._saved_result(token, view, None, require_new_revision=True)
                if result is not None:
                    self.acknowledge_save(token, view, result.note_id)
        for key, draft in tuple(self._drafts.items()):
            if key[0] == source_id:
                self._drafts[key] = self._reconcile_one(draft, view)

    def _forget_submissions(self, key):
        for token_id, submission in tuple(self._submissions.items()):
            if submission.token.key == key:
                del self._submissions[token_id]

    def _remove(self, key):
        self._forget_submissions(key)
        self._drafts.pop(key, None)
        self._entry_serials.pop(key, None)
        if self._last.get(key[:3]) == key:
            del self._last[key[:3]]
        if key in self._visited:
            self._visited.remove(key)

    def discard(self, key: DraftKey, view: Mapping | None = None) -> NoteDraft | None:
        """Explicitly discard one working copy, leaving other owners untouched."""
        draft = self._drafts.get(key)
        if draft is None:
            return None
        self._forget_submissions(key)
        if view is not None:
            # Discard is the explicit resolution which may adopt a newer saved
            # baseline; a normal refresh must never do this to a dirty draft.
            if _source(view)[0] != key[0]:
                self._remove(key)
                return None
            try:
                fresh = self._fresh(view, key[2], draft.note_id)
            except NoteEditConflict:
                self._remove(key)
                return None
            self._remove(key)
            if fresh.key in self._drafts:
                return self._drafts[fresh.key]
            self._drafts[fresh.key] = fresh
            self._entry_serials[fresh.key] = self._next_serial()
            self._visit(fresh.key)
            return fresh
        restored = replace(draft, fields=draft.baseline, confirmed=False,
                           edit_serial=draft.edit_serial + 1)
        self._drafts[key] = restored
        self._entry_serials[key] = self._next_serial()
        return restored

    def discard_all(self) -> None:
        self._drafts.clear()
        self._entry_serials.clear()
        self._last.clear()
        self._visited.clear()
        self._submissions.clear()

    def freeze_save(self, key: DraftKey, view: Mapping) -> SaveToken:
        """Validate owner versions and freeze arguments for one explicit save."""
        if _source(view) != key[:2]:
            raise NoteEditConflict("教材来源或PDF版本已变化，未重新绑定原稿。")
        self.reconcile(view)
        draft = self._drafts[key]
        if draft.conflict:
            raise NoteEditConflict(draft.conflict)
        if not draft.page_in_range:
            raise NoteEditConflict("草稿原页超出当前章节范围，输入保留，请核对原页。")
        if not draft.confirmed:
            raise NoteEditConflict("请明确确认当前文字与PDF原页已核对。")
        token = SaveToken(key=key, fields=draft.fields, baseline=draft.baseline,
                          base_section_revision=draft.base_section_revision,
                          base_note_revision=draft.base_note_revision,
                          base_pdf_start=draft.base_pdf_start, base_pdf_end=draft.base_pdf_end,
                          study_revision=_required_text(view["study_revision"], "个人整理版本"),
                          confirmed=draft.confirmed, edit_serial=draft.edit_serial,
                          token_id=self._next_serial(), entry_serial=self._entry_serials[key],
                          known_note_ids=tuple(row["note_id"] for row in view["notes"]))
        self._submissions[token.token_id] = _Submission(token)
        return token

    def _same_entry(self, token):
        draft = self._drafts.get(token.key)
        return (draft is not None and self._entry_serials.get(token.key) == token.entry_serial
                and draft.base_section_revision == token.base_section_revision
                and draft.base_note_revision == token.base_note_revision
                and draft.baseline == token.baseline)

    def is_save_active(self, token: SaveToken) -> bool:
        submission = self._submissions.get(token.token_id)
        return bool(submission and submission.token is token and submission.active
                    and self._same_entry(token) and not self._drafts[token.key].conflict)

    def mark_save_started(self, token: SaveToken) -> bool:
        """Call immediately before the service invocation, after navigation guards."""
        if not self.is_save_active(token):
            return False
        self._submissions[token.token_id].started = True
        return True

    def cancel_save(self, token: SaveToken) -> None:
        submission = self._submissions.get(token.token_id)
        if submission is not None and submission.token is token:
            submission.active = False
            if not submission.started:
                del self._submissions[token.token_id]

    def _saved_result(self, token, view, note_id, *, require_new_revision=False):
        if _source(view) != token.key[:2]:
            return None
        section = _section(view, token.section_id)
        if (section is None or section["revision"] != token.base_section_revision
                or section["pdf_start"] != token.base_pdf_start
                or section["pdf_end"] != token.base_pdf_end):
            return None
        if token.note_id is not None:
            if note_id is not None and note_id != token.note_id:
                return None
            note_id = token.note_id
        else:
            candidates = [row for row in view["notes"]
                          if row["section_id"] == token.section_id
                          and row["note_id"] not in token.known_note_ids
                          and _fields(row) == token.fields]
            if note_id is None:
                if len(candidates) != 1:
                    return None
                note_id = candidates[0]["note_id"]
            elif not any(row["note_id"] == note_id for row in candidates):
                return None
        note = _row(view["notes"], "note_id", note_id)
        if (note is None or note["section_id"] != token.section_id or _fields(note) != token.fields
                or require_new_revision and note["revision"] == token.base_note_revision):
            return None
        return self._fresh(view, token.section_id, note_id)

    def acknowledge_save(self, token: SaveToken, saved_view: Mapping,
                         note_id: str | None = None) -> NoteDraft | None:
        """Acknowledge one exact receipt; preserve edits made after submission."""
        submission = self._submissions.get(token.token_id)
        if submission is None or submission.token is not token or not self._same_entry(token):
            return None
        fresh = self._saved_result(token, saved_view, note_id)
        if fresh is None:
            return None
        current = self._drafts[token.key]
        # An older receipt cannot erase a newer external version already read.
        if (current.saved_source_revision != token.key[1]
                or current.saved_section_revision != token.base_section_revision
                or current.pdf_start != token.base_pdf_start or current.pdf_end != token.base_pdf_end
                or current.saved_note_revision not in (token.base_note_revision, fresh.base_note_revision)
                or current.saved_fields not in (None, token.baseline, token.fields)):
            return None
        other = self._drafts.get(fresh.key) if fresh.key != token.key else None
        if other is not None and (other.dirty or other.base_note_revision != fresh.base_note_revision):
            return None
        updated = replace(fresh, fields=current.fields, edit_serial=current.edit_serial)
        old_was_last = self._last.get(token.key[:3]) == token.key
        old_visit_index = self._visited.index(token.key) if token.key in self._visited else None
        self._remove(token.key)
        self._drafts[fresh.key] = updated
        self._entry_serials[fresh.key] = self._next_serial()
        if old_was_last:
            self._visit(fresh.key)
        elif old_visit_index is not None and fresh.key not in self._visited:
            self._visited.insert(old_visit_index, fresh.key)
        return updated

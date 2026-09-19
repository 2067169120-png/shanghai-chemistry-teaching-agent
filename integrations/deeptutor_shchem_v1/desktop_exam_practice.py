"""Task-scoped selections, reusing the existing source-verified paper engine.

Only reference snapshots are saved with an exam. Original Word/image files are
still required; this is neither another question bank nor a migration backup.
"""
from __future__ import annotations

from copy import deepcopy
from .desktop_exam_data import ExamError, digest
from .desktop_exam_followup import link_basket
from .desktop_mixed_paper_service import MixedPaperService, _ACTIVE
from .reader_cancellation import read_cancel_scope

SET_SCHEMA = 'shchem.exam-practice-selection.v1'


def freeze_selection(task, basket, keys):
    """Teacher-selected ordered references; do not mutate the task or basket."""
    rows = list(basket)
    if any(not isinstance(row, dict) or not isinstance(row.get('key'), str) or not row['key'] for row in rows):
        raise ExamError('题篮项目身份不完整，请重新读取；未修改任务。')
    if len({row['key'] for row in rows}) != len(rows):
        raise ExamError('题篮出现重复身份，请核对后重试。')
    changed = link_basket(task, rows, keys)
    by_key = {row['key']: row for row in rows}
    items = deepcopy([by_key[key] for key in keys])
    changed['practice_set'] = {'schema': SET_SCHEMA, 'items': items, 'sha256': digest(items)}
    selection_items(changed)
    return changed


def selection_items(task):
    """Reject corrupted/mismatched sets; never fall back silently to the basket."""
    value = task.get('practice_set')
    if not isinstance(value, dict) or value.get('schema') != SET_SCHEMA:
        raise ExamError('本任务尚无有效独立题集，请重新勾选并保存题目。')
    items = value.get('items')
    links = task.get('links')
    if (not isinstance(items, list) or not 1 <= len(items) <= 100
            or not isinstance(links, list) or len(items) != len(links)):
        raise ExamError('独立题集条目缺失或数量不一致，请重新核对。')
    seen = set()
    for row, link in zip(items, links):
        if not isinstance(row, dict) or not isinstance(link, dict):
            raise ExamError('独立题集记录格式不正确。')
        key = row.get('key')
        if (not isinstance(key, str) or not key or key in seen or key != link.get('key')
                or digest(row) != link.get('basket_fingerprint')):
            raise ExamError('独立题集身份、顺序或内容已变化，请重新核对。')
        seen.add(key)
    if digest(items) != value.get('sha256'):
        raise ExamError('独立题集校验不一致，请重新核对；不会自动换题。')
    return deepcopy(items)


def request_revision(task):
    """Attempts/export history do not alter a paper; selected content does."""
    return digest({name: task.get(name) for name in (
        'id', 'exam_id', 'question', 'goal', 'links', 'practice_set')})


class _TaskState:
    """Scope only the active-preview pointer; retain the real store and lock."""
    def __init__(self, parent, task):
        if any(not isinstance(task.get(k), str) or not task[k] for k in ('exam_id', 'id')):
            raise ExamError('复练任务或考试身份缺失。')
        self._parent = parent
        self._lock = parent._lock
        self._items = selection_items(task)
        self._active = 'exam-practice-active-' + digest([task['exam_id'], task['id']])

    def basket(self):
        return deepcopy(self._items)

    def snapshot(self):
        result = self._parent.snapshot()
        # snapshot() is detached; never rename/delete a real global draft.
        drafts = deepcopy(result['drafts'])
        drafts.pop(_ACTIVE, None)
        if self._active in drafts:
            drafts[_ACTIVE] = deepcopy(drafts[self._active])
        return {**result, 'basket': self.basket(), 'drafts': drafts}

    def save_draft(self, key, record):
        if key != _ACTIVE and not key.startswith('mixed-preview-'):
            raise ExamError('复练输出不能写入其他业务草稿。')
        return self._parent.save_draft(self._active if key == _ACTIVE else key, record)


class TaskPaperSession:
    """Facade-shaped adapter, without swapping the global basket, even briefly."""
    def __init__(self, facade, task):
        self.facade = facade
        self.state = _TaskState(facade.state_store, task)
        self.service = MixedPaperService(facade)
        self.service.state = self.state

    def _call(self, name, *args):
        with read_cancel_scope(self.facade._reader_stop_event):
            return getattr(self.service, name)(*args)

    def basket(self):
        return self.state.basket()

    def paper_basket_projection(self):
        return self._call('projection')

    def create_paper_preview(self, request):
        return self._call('create_preview', request)

    def prepare_mixed_paper_pagination(self, identity, fingerprint):
        return self._call('prepare_pagination', identity, fingerprint)

    def paper_preview_image(self, identity, image):
        return self._call('image', identity, image)

    def approve_paper_preview(self, identity, fingerprint):
        return self._call('approve', identity, fingerprint)

    def export_paper_preview(self, identity, fingerprint):
        return self._call('export', identity, fingerprint)


def paper_session(facade, task):
    # Explicitly preserve legacy tasks; saving a new selection upgrades them.
    return TaskPaperSession(facade, task) if 'practice_set' in task else facade

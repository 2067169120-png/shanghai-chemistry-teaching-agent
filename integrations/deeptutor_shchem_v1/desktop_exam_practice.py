"""Task-scoped references reuse the existing source-verified paper engine.

Original Word/image files are still required. This is not another question bank
or a migration backup. Edits and export history are detached until saved.
"""
from __future__ import annotations

from copy import deepcopy
import re
from .desktop_exam_data import ExamError, digest
from .desktop_exam_followup import link_basket
from .desktop_mixed_paper_service import MixedPaperService, _ACTIVE
from .reader_cancellation import read_cancel_scope

SET_SCHEMA = 'shchem.exam-practice-selection.v1'


def freeze_selection(task, basket, keys):
    """Teacher-selected ordered references; do not mutate task or basket."""
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
    """Reject corrupted/mismatched sets; never fall back to the basket."""
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


def revise_selection(task, keys, goal):
    """Reorder/remove saved references without consulting the global basket."""
    rows = selection_items(task)
    if (not isinstance(keys, (list, tuple)) or not keys
            or any(not isinstance(key, str) or not key for key in keys)
            or len(keys) != len(set(keys))):
        raise ExamError('请保留至少一项完整题目，且不要重复。')
    if any(key not in {row['key'] for row in rows} for key in keys):
        raise ExamError('整理时只能使用已保存题集中的题目；新增题目请从题篮明确勾选。')
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 2000:
        raise ExamError('请填写本次要检查的学习目标（不超过2000字）。')
    changed = freeze_selection(task, rows, list(keys))
    changed['goal'] = goal.strip()
    return changed


def request_revision(task):
    """Attempts/export history do not alter a paper; selected content does."""
    return digest({name: task.get(name) for name in (
        'id', 'exam_id', 'question', 'goal', 'links', 'practice_set',
        '_baseline_evidence', '_evidence_reviews')})


def append_export(current, expected, preview, result):
    """Append to fresh history, never overwrite it with an operation snapshot."""
    if request_revision(current) != request_revision(expected):
        raise ExamError('任务题集或目标已变化，请重新预览；未登记旧任务输出。')
    if not isinstance(result, dict) or result.get('pdf_status') != 'generated':
        raise ExamError('两版文件未完整生成。')
    artifacts = result.get('artifacts')
    if (not isinstance(artifacts, list) or not artifacts
            or any(not isinstance(row, dict) or not isinstance(row.get('path'), str)
                   or not row['path'] for row in artifacts)):
        raise ExamError('输出文件记录不完整，未登记到任务。')
    if not isinstance(current.get('exports'), list):
        raise ExamError('已有输出历史格式异常，请先核对，不能覆盖为新列表。')
    changed = deepcopy(current)
    entry = {'preview_id': preview.preview_id, 'preview_hash': preview.preview_hash,
             'request_revision': request_revision(expected), 'goal': expected['goal'],
             'links': deepcopy(expected['links']), 'artifacts': deepcopy(artifacts)}
    if not any(isinstance(row, dict) and all(row.get(key) == entry[key]
               for key in ('preview_id', 'preview_hash', 'artifacts'))
               for row in changed['exports']):
        changed['exports'].append(entry)
    return changed


class _TaskState:
    """A task/version view transacted by the real store's shared writer lock.

    Neither the global mixed draft nor another task's preview is a draft of
    this view. Physical preview records carry the owning task's request
    revision; changing a goal or saved selection cannot reuse old approval.
    """
    def __init__(self, parent, task):
        if any(not isinstance(task.get(k), str) or not task[k] for k in ('exam_id', 'id')):
            raise ExamError('复练任务或考试身份缺失。')
        self._parent = parent
        self._lock = parent._lock
        self._items = selection_items(task)
        scope = digest([task['exam_id'], task['id']])
        self._active = 'exam-practice-active-' + scope
        self._prefix = 'exam-practice-preview-' + scope + ':'
        self._revision = request_revision(task)

    def basket(self):
        return deepcopy(self._items)

    def _view(self, value):
        drafts, revisions = {}, {}
        for key, wrapped in value['drafts'].items():
            if not key.startswith(self._prefix):
                continue
            identity = key[len(self._prefix):]
            if (not re.fullmatch(r'mixed-preview-[0-9a-f]{32}', identity)
                    or not isinstance(wrapped, dict) or set(wrapped) != {'request_revision', 'record'}
                    or not isinstance(wrapped['record'], dict)
                    or wrapped['record'].get('preview_id') != identity
                    or not isinstance(wrapped['request_revision'], str)):
                raise ExamError('本任务预览记录无法核验，请保留现有记录并重新核对。')
            if wrapped['request_revision'] == self._revision:
                drafts[identity] = deepcopy(wrapped['record'])
                if key in value.get('draft_revisions', {}):
                    revisions[identity] = value['draft_revisions'][key]
        active = value['drafts'].get(self._active)
        if isinstance(active, dict) and active.get('preview_id') in drafts:
            drafts[_ACTIVE] = deepcopy(active)
            if self._active in value.get('draft_revisions', {}):
                revisions[_ACTIVE] = value['draft_revisions'][self._active]
        return {**deepcopy(value), 'basket': self.basket(), 'basket_revision': 'task:' + digest(self._items),
                'drafts': drafts, 'draft_revisions': revisions}

    def snapshot(self):
        return self._view(self._parent.snapshot())

    def _update(self, operation, *, basket_write=False):
        if basket_write:
            raise ExamError('复练输出不能修改题篮。')

        def transact(value):
            before = self._view(value)
            scoped = deepcopy(before)
            # Run every source hash, active pointer and expected-record gate
            # against fresh scoped state while the parent holds its OS lock.
            if operation(scoped) is False:
                return False
            if ({key: row for key, row in scoped.items() if key != 'drafts'}
                    != {key: row for key, row in before.items() if key != 'drafts'}
                    or not isinstance(scoped.get('drafts'), dict)
                    or set(before['drafts']) - set(scoped['drafts'])):
                raise ExamError('复练输出只能更新本任务预览，不能改动来源或其他状态。')
            changed = {key for key in scoped['drafts']
                       if key not in before['drafts'] or scoped['drafts'][key] != before['drafts'][key]}
            for key in changed:
                record = scoped['drafts'][key]
                if key == _ACTIVE:
                    if (not isinstance(record, dict) or set(record) != {'preview_id', 'preview_hash'}
                            or not isinstance(record.get('preview_id'), str)
                            or not re.fullmatch(r'mixed-preview-[0-9a-f]{32}', record['preview_id'])
                            or record.get('preview_id') not in scoped['drafts']
                            or not isinstance(scoped['drafts'][record['preview_id']], dict)
                            or scoped['drafts'][record['preview_id']].get('preview_hash') != record['preview_hash']):
                        raise ExamError('本任务活动预览与保存记录不一致。')
                    continue
                if (not isinstance(key, str) or not re.fullmatch(r'mixed-preview-[0-9a-f]{32}', key)
                        or not isinstance(record, dict) or record.get('preview_id') != key):
                    raise ExamError('复练输出不能写入其他业务草稿。')
                if key not in before['drafts'] and any(
                    other == key or other.endswith(':' + key) for other in value['drafts']
                ):
                    raise ExamError('预览标识已属于其他任务或版本，不能覆盖。')
            if not changed:
                return False
            for key in changed:
                value['drafts'][self._active if key == _ACTIVE else self._prefix + key] = (
                    deepcopy(scoped['drafts'][key]) if key == _ACTIVE else
                    {'request_revision': self._revision, 'record': deepcopy(scoped['drafts'][key])})

        return self._view(self._parent._update(transact))

    def save_draft(self, key, record):
        self._update(lambda value: value['drafts'].__setitem__(key, deepcopy(record)))


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
    return TaskPaperSession(facade, task) if 'practice_set' in task else facade

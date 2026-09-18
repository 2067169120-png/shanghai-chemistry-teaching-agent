"""Named search views over the existing catalogue and DesktopStateStore.

Views store teacher-selected conditions, not question text, answers, image bytes,
results or source authority. Restoring a view still runs the original search.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

LANES = frozenset({'master', 'wave1', 'supplemental', 'word_native', 'visual_native'})
STATE_KEY = 'question_search_views'
MAX_VIEWS = 30


class SearchViewError(ValueError):
    """A view cannot be read or saved without changing the teacher's intent."""


def normalize_conditions(value):
    if not isinstance(value, dict) or value.get('lane') not in LANES:
        raise SearchViewError('筛选来源无效，请重新选择题库。')
    query = value.get('query', '')
    if not isinstance(query, str) or len(query) > 2000:
        raise SearchViewError('检索词过长或格式不正确。')
    filters = value.get('filters', {})
    if not isinstance(filters, dict) or len(filters) > 30:
        raise SearchViewError('筛选标签格式不正确。')
    cleaned = {}
    for group, selected in filters.items():
        if (not isinstance(group, str) or len(group) > 80 or not isinstance(selected, (list, tuple, set))
                or len(selected) > 200 or any(not isinstance(v, str) or len(v) > 500 for v in selected)):
            raise SearchViewError('筛选标签格式不正确。')
        if selected:
            cleaned[group] = sorted(set(selected))
    curriculum = value.get('curriculum', {})
    if (not isinstance(curriculum, dict) or set(curriculum) - {'volume_id', 'chapter_id', 'section'}
            or any(not isinstance(v, str) or len(v) > 500 for v in curriculum.values())):
        raise SearchViewError('教材范围格式不正确。')
    label = value.get('curriculum_label', '')
    if not isinstance(label, str) or len(label) > 500:
        raise SearchViewError('教材范围名称不正确。')
    return {'lane': value['lane'], 'query': query.strip(), 'filters': cleaned,
            'curriculum': deepcopy(curriculum), 'curriculum_label': label}


class SearchViewStore:
    def __init__(self, state_store):
        self.state = state_store

    @staticmethod
    def _read(state):
        value = state.get(STATE_KEY, {'schema_version': 1, 'items': []})
        if (not isinstance(value, dict) or value.get('schema_version') != 1
                or not isinstance(value.get('items'), list)):
            raise SearchViewError('已保存的筛选方案暂不能读取；不会覆盖原记录。')
        seen_ids, seen_names, rows = set(), set(), []
        for row in value['items']:
            if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                    or not isinstance(row.get('name'), str) or not row['name'].strip()
                    or row['id'] in seen_ids or row['name'] in seen_names):
                raise SearchViewError('筛选方案记录需核对；不会覆盖原记录。')
            rows.append({'id': row['id'], 'name': row['name'],
                         'conditions': normalize_conditions(row.get('conditions'))})
            seen_ids.add(row['id']); seen_names.add(row['name'])
        return rows

    def list(self):
        return self._read(self.state.snapshot())

    def save(self, name, conditions, *, replace_id=None):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
            raise SearchViewError('请填写1至60字的方案名称。')
        name = name.strip()
        conditions = normalize_conditions(conditions)
        identity = replace_id or uuid4().hex

        def update(state):
            rows = self._read(state)
            index = next((i for i, row in enumerate(rows) if row['id'] == replace_id), None)
            if replace_id is not None and index is None:
                raise SearchViewError('原筛选方案已变化，请刷新后再保存。')
            if any(r['name'] == name and r['id'] != identity for r in rows):
                raise SearchViewError('已有同名方案，请换一个名称。')
            record = {'id': identity, 'name': name, 'conditions': conditions}
            if index is not None:
                rows[index] = record
            else:
                if len(rows) >= MAX_VIEWS:
                    raise SearchViewError('最多保存30个方案，请先删除不再使用的方案。')
                rows.append(record)
            state[STATE_KEY] = {'schema_version': 1, 'items': rows}
        self.state._update(update)
        return identity

    def remove(self, identity):
        def update(state):
            rows = self._read(state)
            if not any(r['id'] == identity for r in rows):
                raise SearchViewError('该方案已移除，请刷新列表。')
            state[STATE_KEY] = {'schema_version': 1, 'items': [r for r in rows if r['id'] != identity]}
        self.state._update(update)

"""Reusable teacher mappings: header identities and settings, never grade rows."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
import uuid

from .desktop_exam_data import ExamError, build_exam, column_name, digest, display
from .desktop_state import DesktopStateError, _state_file_lock


SCHEMA = 'shchem.exam-mapping-profile.v1'
ROLES = {'student_col': '学生标识列', 'class_col': '班级列',
         'total_col': '总分列', 'status_col': '缺考状态列'}
QUESTION_FIELDS = ('question', 'max_score', 'knowledge', 'chapter', 'context')
LIMITS = ('max_score', 'pass_score', 'excellent_score')
_LOCK = RLock()
_UNCHECKED = object()


def headers_for(book, config):
    try:
        sheet = next(s for s in book['sheets'] if s['name'] == config['sheet'])
        row = config.get('header_row', 1)
        if type(row) is not int or not 1 <= row < len(sheet['rows']):
            raise ValueError()
        return [display(v).strip() for v in sheet['rows'][row - 1]]
    except (KeyError, StopIteration, TypeError, ValueError, IndexError):
        raise ExamError('请先选择有效的成绩工作表和表头行。') from None


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ExamError('映射方案身份无效。')
    return value


def _header_identity(headers, column):
    if type(column) is not int or not 0 <= column < len(headers):
        raise ExamError('列映射不正确，请重新选择。')
    label = headers[column]
    if not label or headers.count(label) != 1:
        raise ExamError('方案中使用的列必须有唯一且非空的表头；请先在Excel修正空白或重复表头。')
    return label


def _profile_config(profile):
    """Validate using the importer's constraints, with an invented empty grade row."""
    headers = profile['headers']
    cfg = {'title': '映射方案校验', 'sheet': '映射结构', 'header_row': 1,
           **{k: profile[k] for k in LIMITS}, 'questions': []}
    for key in ROLES:
        label = profile['roles'][key]
        cfg[key] = headers.index(label) if label is not None else None
    for q in profile['questions']:
        cfg['questions'].append({'column': headers.index(q['header']),
                                **{k: q[k] for k in QUESTION_FIELDS}})
    row = [0] * len(headers)
    row[cfg['student_col']] = '结构校验占位'
    if cfg['status_col'] is not None:
        row[cfg['status_col']] = ''
    book = {'source_name': '', 'source_sha256': '', 'sheets': [
        {'name': cfg['sheet'], 'rows': [headers, row], 'formula_cells': [], 'hidden_rows': [], 'hidden': False}]}
    build_exam(book, cfg)


def validate_profile(profile):
    try:
        if set(profile) != {'schema', 'id', 'name', 'headers', 'roles', 'questions', *LIMITS}:
            raise ValueError()
        if profile['schema'] != SCHEMA:
            raise ValueError()
        _identity(profile['id'])
        if not isinstance(profile['name'], str) or not 1 <= len(profile['name'].strip()) <= 80:
            raise ValueError()
        headers = profile['headers']
        if not isinstance(headers, list) or not 1 <= len(headers) <= 256:
            raise ValueError()
        if any(not isinstance(x, str) or x != x.strip() for x in headers):
            raise ValueError()
        if set(profile['roles']) != set(ROLES) or profile['roles']['student_col'] is None:
            raise ValueError()
        if not isinstance(profile['questions'], list) or len(profile['questions']) > 200:
            raise ValueError()
        labels = [v for v in profile['roles'].values() if v is not None]
        for q in profile['questions']:
            if set(q) != {'header', *QUESTION_FIELDS}:
                raise ValueError()
            if any(not isinstance(q[k], str) for k in ('question', 'knowledge', 'chapter', 'context')):
                raise ValueError()
            if isinstance(q['max_score'], bool) or not isinstance(q['max_score'], (int, float)):
                raise ValueError()
            labels.append(q['header'])
        if any(not isinstance(x, str) or not x or headers.count(x) != 1 for x in labels):
            raise ValueError()
        if len(labels) != len(set(labels)):
            raise ValueError()
        for k in LIMITS:
            if isinstance(profile[k], bool) or not isinstance(profile[k], (int, float)) or not 0 <= profile[k] <= 10000:
                raise ValueError()
        _profile_config(profile)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration):
        raise ExamError('映射方案内容或版本无效；请保留原文件并重新保存方案。') from None
    return deepcopy(profile)


def profile_from_config(name, book, config, *, identity=None):
    name = str(name).strip()
    if not 1 <= len(name) <= 80:
        raise ExamError('请为映射方案填写1至80字的名称。')
    # The original import validation also checks the active sheet and draft.
    exam = build_exam(book, config)
    headers = headers_for(book, config)
    roles = {k: _header_identity(headers, config[k]) if config.get(k) is not None else None for k in ROLES}
    questions = [{'header': _header_identity(headers, q['column']),
                  **{k: q.get(k, '') for k in QUESTION_FIELDS}} for q in exam['questions']]
    profile = {'schema': SCHEMA, 'id': _identity(identity) if identity is not None else uuid.uuid4().hex,
               'name': name, 'headers': headers, 'roles': roles, 'questions': questions,
               'max_score': exam['maximum'], 'pass_score': exam['pass_score'],
               'excellent_score': exam['excellent_score']}
    return validate_profile(profile)


def _bindings(profile, headers):
    bindings = []
    for key, label in ROLES.items():
        old = profile['roles'][key]
        matches = [i for i, h in enumerate(headers) if old is not None and h == old]
        bindings.append({'key': key, 'label': label, 'header': old,
                         'column': matches[0] if len(matches) == 1 else None,
                         'required': key == 'student_col'})
    for index, q in enumerate(profile['questions']):
        matches = [i for i, h in enumerate(headers) if h == q['header']]
        bindings.append({'key': f'question:{index}', 'label': '题目 ' + q['question'], 'header': q['header'],
                         'column': matches[0] if len(matches) == 1 else None, 'required': True})
    return bindings


def _question_text(q):
    return '；'.join(f'{label}={q.get(key, "")}' for key, label in zip(
        QUESTION_FIELDS, ('题号', '满分', '主知识点', '章节', '公共材料/备注')))


def review_profile(profile, book, config):
    profile = validate_profile(profile)
    headers = headers_for(book, config)
    old = profile['headers']; changes = []
    added = Counter(headers) - Counter(old); removed = Counter(old) - Counter(headers)
    for label, n in added.items():
        changes.append(f'新增表头：{label or "空表头"}（{n}列）')
    for label, n in removed.items():
        changes.append(f'缺少原表头：{label or "空表头"}（{n}列）；如已改名，请明确重新绑定。')
    for label, n in Counter(headers).items():
        if n > 1:
            changes.append(f'当前表头重复：{label or "空表头"}（{n}列）；不能按旧列序号套用。')
    for label in dict.fromkeys(old):
        if old.count(label) == headers.count(label) == 1 and old.index(label) != headers.index(label):
            changes.append(f'列位置变化：{label or "空表头"} {column_name(old.index(label))} → {column_name(headers.index(label))}')
    bindings = _bindings(profile, headers)
    for b in bindings:
        if b['header'] is not None and b['column'] is None:
            changes.append(f'{b["label"]}无法唯一匹配“{b["header"]}”，请在下表选择当前列。')
        current = config.get(b['key']) if b['key'] in ROLES else None
        if b['key'] in ROLES and current != b['column']:
            current_label = headers[current] if type(current) is int and 0 <= current < len(headers) else '不选择'
            changes.append(f'{b["label"]}：当前草稿“{current_label}” → 方案“{b["header"] or "不选择"}”')
    for k, label in zip(LIMITS, ('试卷满分', '达标线', '高分线')):
        if config.get(k) != profile[k]:
            changes.append(f'{label}：当前草稿 {config.get(k)} → 方案 {profile[k]}')
    current_questions = {headers[q['column']]: q for q in config.get('questions', [])
                         if type(q.get('column')) is int and 0 <= q['column'] < len(headers)}
    saved_headers = set()
    for q in profile['questions']:
        saved_headers.add(q['header']); current = current_questions.get(q['header'])
        if current is None:
            changes.append(f'题目映射将加入：{q["header"]} · {_question_text(q)}')
        elif any(str(current.get(k, '')).strip() != str(q[k]).strip() and
                 not (k == 'max_score' and _same_number(current.get(k), q[k])) for k in QUESTION_FIELDS):
            changes.append(f'题目映射变化：{q["header"]}\n当前：{_question_text(current)}\n方案：{_question_text(q)}')
    for header, q in current_questions.items():
        if header not in saved_headers:
            changes.append(f'当前草稿题目将移除：{header} · {_question_text(q)}')
    if not changes:
        changes.append('列标题、列顺序与当前题目映射一致；仍须核对本次考试题号、满分及知识点。')
    return {'headers': headers, 'bindings': bindings, 'changes': changes}


def _same_number(a, b):
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def resolve_profile(profile, book, current_config, selections=None):
    profile = validate_profile(profile)
    review = review_profile(profile, book, current_config)
    selected = selections or {}; cfg = deepcopy(current_config)
    cfg.update({k: profile[k] for k in LIMITS}); cfg['questions'] = []
    for b in review['bindings']:
        column = selected.get(b['key'], b['column'])
        if column is None:
            if b['key'].startswith('question:') and b['key'] in selected:
                continue  # A teacher explicitly excludes this question for this import.
            if b['key'] == 'student_col' or (b['header'] is not None and b['key'] not in selected):
                raise ExamError(f'{b["label"]}尚未明确绑定；请核对缺少或重复的列标题。')
        elif type(column) is not int or not 0 <= column < len(review['headers']) or not review['headers'][column]:
            raise ExamError(f'{b["label"]}列无效或表头为空，请重新选择。')
        if b['key'] in ROLES:
            cfg[b['key']] = column
        else:
            q = profile['questions'][int(b['key'].split(':')[1])]
            cfg['questions'].append({'column': column, **{k: q[k] for k in QUESTION_FIELDS}})
    build_exam(book, cfg)
    return cfg


class ExamMappingProfileStore:
    def __init__(self, state_root):
        self.root = Path(state_root) / 'exam-mapping-profiles'

    @contextmanager
    def _writing(self):
        try:
            with _LOCK, _state_file_lock(self.root.resolve()):
                yield
        except DesktopStateError:
            raise ExamError('映射方案正被其他窗口使用或无法保存，请重新选择后重试；当前草稿保留。') from None

    def save(self, name, book, config, identity=None, *, expected_revision=_UNCHECKED):
        profile = profile_from_config(name, book, config, identity=identity)
        with self._writing():
            path = self.root / (profile['id'] + '.json')
            # Validate an existing record even for direct callers without a revision.
            # A damaged historical scheme must never be silently replaced.
            existing = self.load(profile['id']) if path.exists() else None
            if identity is not None and existing is None:
                raise ExamError('选中的映射方案已被删除，请重新选择；当前草稿保留。')
            if any(key != profile['id'] and label.casefold() == profile['name'].casefold() for key, label in self.entries()):
                raise ExamError('已有同名映射方案；请选择该方案后更新，或填写不同名称。')
            if expected_revision is not _UNCHECKED:
                current = digest(existing) if existing is not None else None
                if current != expected_revision:
                    raise ExamError('映射方案已在其他窗口变化，请重新选择方案后核对；未覆盖最新方案。')
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix='.mapping-', dir=self.root)
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        json.dump(profile, f, ensure_ascii=False, allow_nan=False, indent=2)
                        f.flush(); os.fsync(f.fileno())
                    os.replace(name, path)
                finally:
                    Path(name).unlink(missing_ok=True)
            except OSError:
                raise ExamError('映射方案未能保存；当前导入草稿仍保留。') from None
        return deepcopy(profile)

    def entries(self):
        result = []
        for path in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                p = self.load(path.stem); result.append((p['id'], p['name']))
            except ExamError:
                continue
        return result

    def load(self, identity):
        path = self.root / (_identity(identity) + '.json')
        try:
            profile = validate_profile(json.loads(path.read_text(encoding='utf-8')))
            if profile['id'] != identity:
                raise ValueError()
            return profile
        except (ValueError, KeyError, OSError):
            raise ExamError('映射方案无法读取，请保留原文件并重新保存方案。') from None

    def delete(self, identity, *, expected_revision=_UNCHECKED):
        with self._writing():
            identity = _identity(identity)
            current = self.load(identity)
            if expected_revision is not _UNCHECKED and digest(current) != expected_revision:
                raise ExamError('映射方案已在其他窗口变化，请重新选择后核对；未删除最新方案。')
            try:
                (self.root / (identity + '.json')).unlink()
            except OSError:
                raise ExamError('映射方案未能删除；当前导入草稿仍保留。') from None

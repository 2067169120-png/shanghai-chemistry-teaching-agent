"""Independent, source-free JSON transfer of versioned knowledge/question links.

Only the dedicated relation service can commit. Importing this document never
restores books, Word archives, question attributes or preparation ZIP contents.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile

from .desktop_knowledge_question_links import digest, validate_collection
from .reader_cancellation import check_read_cancelled, publication_guard

SCHEMA = 'shchem.knowledge-question-links-transfer.v1'
PLAN_SCHEMA = 'shchem.knowledge-question-links-merge-preview.v1'
# The collection validator independently caps canonical relation data at 8MiB.
# Allow formatting overhead while bounding the actual input before decoding.
MAX_TRANSFER_BYTES = 16 * 1024 * 1024


class KnowledgeQuestionLinksTransferError(ValueError):
    def __init__(self, message):
        super().__init__(message); self.message_zh = message


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _path(value):
    if not isinstance(value, (str, os.PathLike)) or isinstance(value, str) and not value.strip():
        raise KnowledgeQuestionLinksTransferError('请选择独立的关联JSON文件。')
    path = Path(value)
    if path.is_symlink() or path.is_junction() or path.is_dir():
        raise KnowledgeQuestionLinksTransferError('关联JSON文件位置无法核验，请选择普通文件。')
    return path


def _file_bytes(value):
    check_read_cancelled(); path = _path(value)
    try:
        path_before = path.stat()
        with path.open('rb') as handle:
            before = os.fstat(handle.fileno())
            if before.st_size > MAX_TRANSFER_BYTES:
                raise KnowledgeQuestionLinksTransferError('关联JSON超过16MiB，未读取或恢复。')
            raw = handle.read(MAX_TRANSFER_BYTES + 1)
            after = os.fstat(handle.fileno())
        current = _path(path).stat()
    except OSError as exc:
        raise KnowledgeQuestionLinksTransferError('关联JSON无法读取，请重新选择。') from exc
    signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    # On Windows a renamed file can expose birth-time through Path.stat but
    # change-time through fstat. Compare ctime only within the same API.
    identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if (signature(before) != signature(after) or signature(path_before) != signature(current)
            or identity(after) != identity(current)):
        raise KnowledgeQuestionLinksTransferError('关联JSON在读取期间变化，请重新检查。')
    if len(raw) > MAX_TRANSFER_BYTES:
        raise KnowledgeQuestionLinksTransferError('关联JSON超过16MiB，未读取或恢复。')
    check_read_cancelled(); return raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise KnowledgeQuestionLinksTransferError('关联JSON含重复字段，未恢复。')
        result[key] = value
    return result


def _nonfinite(_value):
    raise KnowledgeQuestionLinksTransferError('关联JSON含无效数字，未恢复。')


def _decode(raw):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_nonfinite)
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'collection', 'content_sha256'}
                or value['schema_version'] != SCHEMA or not _sha(value['content_sha256'])):
            raise KnowledgeQuestionLinksTransferError('关联JSON格式或版本不受支持，未恢复。')
        collection = validate_collection(value['collection'])
        if collection != value['collection'] or digest(collection) != value['content_sha256']:
            raise KnowledgeQuestionLinksTransferError('关联JSON的内容摘要不一致，未恢复。')
        return collection, value['content_sha256']
    except KnowledgeQuestionLinksTransferError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise KnowledgeQuestionLinksTransferError('关联JSON内容无法核验，未恢复。') from exc


def _snapshot(service):
    value = service.snapshot()
    if (not isinstance(value, dict) or set(value) != {'revision', 'content_sha256', 'collection'}
            or not isinstance(value['revision'], str) or not value['revision'] or not _sha(value['content_sha256'])):
        raise KnowledgeQuestionLinksTransferError('当前关联集合版本无法核验。')
    collection = validate_collection(value['collection'])
    if collection != value['collection']:
        raise KnowledgeQuestionLinksTransferError('当前关联集合内容无法核验。')
    return deepcopy(value)


def _same_snapshot(first, second):
    return (first['revision'], first['content_sha256'], first['collection']) == (
        second['revision'], second['content_sha256'], second['collection'])


def _merge(service, incoming, expected):
    plan = service.plan_merge(incoming, expected=expected)
    fields = {'expected_revision', 'expected_content_sha256', 'input_sha256', 'incoming',
              'collection', 'new', 'duplicates', 'conflicts'}
    if (not isinstance(plan, dict) or set(plan) != fields
            or plan['expected_revision'] != expected['revision']
            or plan['expected_content_sha256'] != expected['content_sha256']
            or plan['incoming'] != incoming or plan['input_sha256'] != digest(incoming)):
        raise KnowledgeQuestionLinksTransferError('关联合并计划无法核验。')
    local = {row['id']: row for row in expected['collection']['records']}
    rows = {row['id']: row for row in incoming['records']}
    wanted = {'new': {key for key in rows if key not in local},
              'duplicates': {key for key in rows if key in local and rows[key] == local[key]},
              'conflicts': {key for key in rows if key in local and rows[key] != local[key]}}
    for name, ids in wanted.items():
        if (not isinstance(plan[name], list) or any(not isinstance(key, str) for key in plan[name])
                or len(plan[name]) != len(ids) or set(plan[name]) != ids):
            raise KnowledgeQuestionLinksTransferError('关联合并的新增、重复或冲突记录无法核验。')
    merged = validate_collection(plan['collection'])
    union = {**local, **{key: rows[key] for key in wanted['new']}}
    if {row['id']: row for row in merged['records']} != union:
        raise KnowledgeQuestionLinksTransferError('关联合并计划不能覆盖当前记录。')
    return deepcopy(plan)


def _inspect(service, path):
    raw = _file_bytes(path); incoming, content_digest = _decode(raw)
    expected = _snapshot(service); backend_plan = _merge(service, incoming, expected)
    local = {row['id']: row for row in expected['collection']['records']}
    rows = {row['id']: row for row in incoming['records']}
    plan = {'schema_version': PLAN_SCHEMA, 'input_sha256': sha256(raw).hexdigest(),
            'input_digest': content_digest, 'input_bytes': len(raw),
            'current_store_revision': expected['revision'],
            'current_store_content_sha256': expected['content_sha256'],
            'incoming': deepcopy(incoming), 'new_links': deepcopy(backend_plan['new']),
            'duplicates': deepcopy(backend_plan['duplicates']), 'conflicts': deepcopy(backend_plan['conflicts']),
            'conflict_details': [{'id': key, 'current_reason': local[key]['reason'],
                'incoming_reason': rows[key]['reason'], 'current_version': local[key]['version'],
                'incoming_version': rows[key]['version'], 'current_removed': local[key]['removed'],
                'incoming_removed': rows[key]['removed']} for key in backend_plan['conflicts']],
            'counts': {name: len(backend_plan[name]) for name in ('new', 'duplicates', 'conflicts')},
            'endpoint_status': 'pending'}
    plan['plan_sha256'] = digest(plan)
    check_read_cancelled()
    if not _same_snapshot(expected, _snapshot(service)):
        raise KnowledgeQuestionLinksTransferError('当前关联集合已变化，请重新检查合并计划。')
    # Bind the plan to the exact file bytes, including a formatting-only edit.
    if sha256(_file_bytes(path)).hexdigest() != plan['input_sha256']:
        raise KnowledgeQuestionLinksTransferError('关联JSON已变化，请重新检查合并计划。')
    return plan, expected, backend_plan


def inspect_knowledge_question_links(service, path):
    """Pure preview: no source endpoint reads and no association/profile writes."""
    return _inspect(service, path)[0]


def export_knowledge_question_links(service, path, *, expected_revision=None):
    """Atomically export only the dedicated collection, never source archives."""
    check_read_cancelled(); target = _path(path); snapshot = _snapshot(service)
    if expected_revision is not None and expected_revision != snapshot['revision']:
        raise KnowledgeQuestionLinksTransferError('当前关联集合已变化，请刷新后再导出。')
    value = {'schema_version': SCHEMA, 'collection': snapshot['collection'],
             'content_sha256': digest(snapshot['collection'])}
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > MAX_TRANSFER_BYTES:
        raise KnowledgeQuestionLinksTransferError('关联JSON超过16MiB，未导出。')
    temporary = None; unchanged = False
    try:
        if target.is_file() and target.stat().st_size == len(raw):
            unchanged = _file_bytes(target) == raw
        if not unchanged:
            handle, name = tempfile.mkstemp(prefix='.knowledge-links-', suffix='.tmp', dir=target.parent)
            temporary = Path(name)
            with os.fdopen(handle, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        check_read_cancelled()
        with publication_guard():
            if not _same_snapshot(snapshot, _snapshot(service)):
                raise KnowledgeQuestionLinksTransferError('当前关联集合已变化，未替换导出文件。')
            _path(target)
            if temporary is not None:
                os.replace(temporary, target); temporary = None
            elif _file_bytes(target) != raw:
                raise KnowledgeQuestionLinksTransferError('导出文件在核对期间变化，请重新导出。')
        return {'path': str(target.resolve()), 'bytes_count': len(raw), 'file_sha256': sha256(raw).hexdigest(),
                'content_sha256': value['content_sha256'], 'store_revision': snapshot['revision'],
                'records_count': len(snapshot['collection']['records']), 'written': not unchanged}
    except OSError as exc:
        raise KnowledgeQuestionLinksTransferError('关联JSON未能完整导出，原文件保持。') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def restore_knowledge_question_links(service, path, plan, *, conflict_policy='block', guard=None):
    """Recheck file and addressed store, then CAS append only nonconflicting links."""
    if not isinstance(conflict_policy, str) or conflict_policy not in {'block', 'keep_current'}:
        raise KnowledgeQuestionLinksTransferError('请选择保留当前冲突记录或取消恢复。')
    if guard is not None and not callable(guard):
        raise KnowledgeQuestionLinksTransferError('恢复前的工作台状态检查无法调用。')
    if not isinstance(plan, dict):
        raise KnowledgeQuestionLinksTransferError('请先检查关联JSON的合并计划。')
    current, expected, backend_plan = _inspect(service, path)
    if plan != current or digest(plan) != digest(current):
        raise KnowledgeQuestionLinksTransferError('文件、关联集合或合并计划已变化，请重新检查。')
    if current['conflicts'] and conflict_policy == 'block':
        raise KnowledgeQuestionLinksTransferError('关联JSON存在冲突，未恢复；可明确选择保留当前记录。')
    def checked():
        if guard is not None:
            guard()
        check_read_cancelled()
        if sha256(_file_bytes(path)).hexdigest() != current['input_sha256']:
            raise KnowledgeQuestionLinksTransferError('关联JSON在提交前变化，未恢复。')
    # The service acquires words -> publication -> state locks, including a
    # duplicate-only merge. Do not invert that order with an outer event lock.
    result = service.commit_merge(backend_plan, expected=expected, guard=checked,
                                  keep_current=conflict_policy == 'keep_current')
    return {'new_count': current['counts']['new'], 'duplicate_count': current['counts']['duplicates'],
            'conflict_count': current['counts']['conflicts'],
            'kept_current_conflicts': conflict_policy == 'keep_current',
            'endpoint_status': 'pending', 'result': deepcopy(result)}

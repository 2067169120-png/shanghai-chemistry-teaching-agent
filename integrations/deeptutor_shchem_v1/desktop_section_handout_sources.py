"""Read-only choices of exact Word sources already used in this course.

A course-use binding establishes neither a textbook relationship nor a review
decision. Working card edits are independent of the sealed Original evidence.
"""
from collections import Counter
from copy import deepcopy
import re

from .desktop_handout_knowledge import SOURCE_SCHEMA
from .desktop_handout_sources import handout_source_context, validate_handout_source
from .desktop_lesson_composition import FLAGS, digest, validate_resource
from .reader_cancellation import ReadCancelled, check_read_cancelled

_CANDIDATE_FIELDS = {'source', 'resource_ids', 'label', 'binding_sha256'}
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_RESOURCE_ID = re.compile(r'CR-[a-f0-9]{32}\Z')


def _document_source(value):
    source = validate_handout_source(value)
    if source['schema'] != SOURCE_SCHEMA:
        raise ValueError('本课已用讲义须保留完整的八字段原Word来源。')
    return source


def _course_bindings(resources):
    if not isinstance(resources, list):
        raise ValueError('当前课堂资源记录不完整，请重新打开课堂。')
    ids = Counter(row.get('id') for row in resources
        if isinstance(row, dict) and isinstance(row.get('id'), str))
    groups, unavailable, duplicate_ids = {}, [], set()
    for row in resources:
        check_read_cancelled()
        if (not isinstance(row, dict) or not isinstance(row.get('original'), dict)
                or row['original'].get('kind') != 'handout_knowledge'):
            continue
        resource_id = row.get('id')
        related_ids = [resource_id] if isinstance(resource_id, str) else []
        if isinstance(resource_id, str) and ids[resource_id] != 1:
            if resource_id not in duplicate_ids:
                unavailable.append({'resource_ids': related_ids,
                    'reason': '课堂中该讲义资源的标识不唯一，请重新核对。'})
                duplicate_ids.add(resource_id)
            continue
        try:
            checked = validate_resource(row)
            source = _document_source(checked['original']['evidence']['handout_source'])
        except (ValueError, TypeError, KeyError) as exc:
            unavailable.append({'resource_ids': related_ids,
                'reason': '课堂讲义的封存来源无法核验：' + str(exc)})
            continue
        key = digest(source)
        group = groups.setdefault(key, {'source': source, 'resources': []})
        group['resources'].append({'resource_id': checked['id'],
            'original_sha256': checked['original_sha256']})
    for group in groups.values():
        group['resources'].sort(key=lambda row: row['resource_id'])
    return groups, unavailable


def _candidate(group):
    return {'source': deepcopy(group['source']),
        'resource_ids': [row['resource_id'] for row in group['resources']],
        'label': group['source']['source_name'],
        'binding_sha256': digest({'schema': 'shchem.course-handout-source.v1', **group})}


def _verify_document(facade, source):
    context = handout_source_context(facade, source)
    preview = context.get('preview') if isinstance(context, dict) else None
    if (not isinstance(context, dict) or context.get('document_source') != source
            or not isinstance(preview, dict)
            or any(preview.get(preview_key) != source[source_key]
                for preview_key, source_key in (('source_name', 'source_name'),
                    ('source_sha256', 'source_sha256'), ('revision', 'source_revision')))):
        raise ValueError('讲义原Word读取结果与封存来源不一致，请重新选择。')


def course_handout_sources(facade, resources):
    """List only source-verified documents bound by sealed course knowledge.

    Full source identity, including archive batch, path, role and extraction
    revision, determines grouping. A missing/stale group remains unavailable;
    it never becomes another same-name or same-byte archive's choice.
    """
    check_read_cancelled()
    groups, invalid = _course_bindings(resources)
    sources, unavailable = [], deepcopy(invalid)
    for group in groups.values():
        check_read_cancelled()
        choice = _candidate(group)
        try:
            _verify_document(facade, choice['source'])
        except ReadCancelled:
            raise
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            unavailable.append({'resource_ids': choice['resource_ids'],
                'reason': '本课已用讲义当前不可用：' + str(exc)})
            continue
        sources.append(choice)
    # A slow source read cannot authorize a card removed/replaced meanwhile.
    current_groups, current_invalid = _course_bindings(resources)
    if current_groups != groups or current_invalid != invalid:
        raise ValueError('课堂讲义的封存资源在读取期间变化，请重新选择。')
    check_read_cancelled()
    return {'sources': sources, 'unavailable': unavailable, **FLAGS}


def recheck_course_handout_source(facade, resources, candidate):
    """Require the same source and sealed course references immediately again."""
    if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_FIELDS:
        raise ValueError('本课已用讲义选择记录不完整，请重新选择。')
    source = _document_source(candidate['source'])
    ids = candidate['resource_ids']
    if (not isinstance(ids, list) or not ids
            or any(not isinstance(value, str) or not _RESOURCE_ID.fullmatch(value) for value in ids)
            or len(ids) != len(set(ids)) or candidate['label'] != source['source_name']
            or not isinstance(candidate['binding_sha256'], str)
            or not _SHA.fullmatch(candidate['binding_sha256'])):
        raise ValueError('本课已用讲义选择的课堂绑定不完整，请重新选择。')
    expected = deepcopy(candidate)
    fresh = course_handout_sources(facade, resources)
    if any(row == expected for row in fresh['sources']):
        return True
    related = [row['reason'] for row in fresh['unavailable']
        if set(row['resource_ids']) & set(ids)]
    reason = related[0] if related else '课堂封存来源或引用该来源的资源已变化。'
    raise ValueError('本课已用讲义已失效，请重新选择。' + reason)

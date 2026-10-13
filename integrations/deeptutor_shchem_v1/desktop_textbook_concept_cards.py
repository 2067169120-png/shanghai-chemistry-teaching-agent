"""Version-bound textbook summaries, separate from classroom display text."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

from .desktop_preparation_sources import PreparationSourcesService, _digest
from .desktop_textbook_bookshelf import TextbookBookshelfService
from .reader_cancellation import check_read_cancelled

_PAGE = re.compile(r'CONCEPTPAGE-([a-f0-9]{64})-([1-9][0-9]{0,5})\Z')
LOCATION_FIELDS = {'volume_id', 'chapter_id', 'section_key', 'supplement_node_key', 'scope_revision'}


def concept_options(workspace, snapshot=None):
    """Metadata only. Use registered page maps, never an inferred offset."""
    shelf = TextbookBookshelfService(workspace)
    snapshot = shelf.snapshot() if snapshot is None else snapshot
    if not snapshot.get('concepts'):
        return []
    _registry, directory_revision, _parents, _sections = shelf._directory()
    raw = PreparationSourcesService(workspace)._concepts()
    sections = {r['section_key']: r for r in snapshot['sections']}
    supplements = {r['supplement_node_key']: r for r in snapshot.get('supplements', [])}
    volumes = {r['volume_id']: r for r in snapshot['volumes']}
    rows = []
    for public in snapshot['concepts']:
        record = raw.get(public['concept_id'])
        if record is None or _digest(record) != public['revision']:
            raise ValueError('教材知识摘要在读取期间变化，请刷新后重选。')
        supplement = public.get('resolved_supplement_node_key')
        scope = sections.get(public['section_key']) or supplements.get(supplement)
        location = {k: public.get(k) for k in ('volume_id', 'chapter_id', 'section_key')}
        location.update(supplement_node_key=supplement, scope_revision=scope['revision'] if scope else None)
        printed = 'unknown'
        if scope and len(scope['pdf_pages']) == len(scope['printed_pages']):
            mapping = dict(zip(scope['pdf_pages'], scope['printed_pages']))
            if all(p in mapping for p in public['pdf_pages']):
                printed = [mapping[p] for p in public['pdf_pages']]
        # Retain conflicting printed-page claims in the raw snapshot, without
        # presenting either claim as a verified page mapping.
        if record.get('printed_pages') not in (None, [], 'unknown', printed):
            printed = 'unknown'
        volume = volumes.get(public['volume_id'], {})
        selection = {'kind': 'textbook_concept', 'concept_id': public['concept_id'],
                     'revision': public['revision'], 'directory_revision': directory_revision,
                     'location': location}
        rows.append({**public, 'origin': 'textbook_concept', 'summary': public['statement'],
            'source_name': Path(record['source_path']).name, 'printed_pages': printed,
            'book_key': 'registered:' + (public['volume_id'] or 'unknown'),
            'section_filter_key': (public['section_key'] or supplement or 'unmapped:' + (public['volume_id'] or 'unknown')),
            'volume_title': volume.get('volume_title', '册名待核对'),
            'section_title': scope['title'] if scope else '待归章节',
            'limitation': str(record.get('verification_caveat') or '知识摘要候选；使用前请对照教材原页核对。'),
            'selection': selection})
    if shelf._directory()[1] != directory_revision:
        raise ValueError('教材目录在读取期间变化，请刷新后重选。')
    return rows


def checked_concept(workspace, selection, *, read_source=False):
    if (not isinstance(selection, dict)
            or set(selection) != {'kind', 'concept_id', 'revision', 'directory_revision', 'location'}
            or selection['kind'] != 'textbook_concept'):
        raise ValueError('教材知识摘要选择记录不完整，请刷新后重选。')
    def current():
        row = next((r for r in concept_options(workspace) if r['selection'] == selection), None)
        if row is None:
            raise ValueError('知识摘要、教材目录或补充栏目已变化，请重新选择。')
        raw = PreparationSourcesService(workspace)._concepts().get(selection['concept_id'])
        if raw is None or _digest(raw) != selection['revision']:
            raise ValueError('知识摘要已变化，请重新选择。')
        return row, raw
    row, raw = current()
    source = None
    if read_source:
        check_read_cancelled()
        source = PreparationSourcesService(workspace).textbook_source(selection['concept_id'], selection['revision'])
        if (source['pdf_pages'] != row['pdf_pages'] or source['source_sha256'] != raw['source_sha256']
                or current() != (row, raw)):
            raise ValueError('知识摘要或原书在读取期间变化，请刷新后重选。')
    return row, raw, source


def import_concept_card(facade, selection):
    from .desktop_lesson_composition import block, resource
    row, raw, source = checked_concept(facade.paths.workspace_root, selection, read_source=True)
    evidence = {'source_path': str((Path(facade.paths.workspace_root) / raw['source_path']).resolve()),
                'source_sha256': source['source_sha256'], 'source_revision': row['revision'],
                'record_id': row['concept_id'], 'pdf_pages': row['pdf_pages'], 'printed_pages': row['printed_pages'],
                'concept_source': {'record': deepcopy(raw), 'directory_revision': selection['directory_revision'],
                                   'location': deepcopy(selection['location'])}}
    return resource('knowledge_note', row['title'], [block('text', 'knowledge', row['statement']),
                    block('text', 'teacher_note', row['limitation'])], evidence)


def validate_concept_source(evidence):
    """Old note cards retain their schema; new summaries carry the exact record."""
    source = evidence['concept_source']
    if (not isinstance(source, dict) or set(source) != {'record', 'directory_revision', 'location'}
            or not isinstance(source['record'], dict) or not isinstance(source['location'], dict)
            or set(source['location']) != LOCATION_FIELDS
            or not re.fullmatch('[a-f0-9]{64}', str(source['directory_revision']))):
        raise ValueError('知识摘要的原记录与目录版本不完整。')
    record = source['record']
    if (record.get('concept_id') != evidence['record_id'] or _digest(record) != evidence['source_revision']
            or record.get('source_sha256') != evidence['source_sha256']
            or record.get('pdf_pages') != evidence['pdf_pages']
            or any(not isinstance(record.get(k), str) or not record[k] for k in ('title', 'statement', 'source_path'))):
        raise ValueError('知识摘要原记录与来源摘要不一致。')
    for key, value in source['location'].items():
        if value is not None and (not isinstance(value, str) or not value
                                  or key == 'scope_revision' and not re.fullmatch('[a-f0-9]{64}', value)):
            raise ValueError('知识摘要的章节或补充栏目身份无效。')


def concept_page_selection(row, pdf_page):
    if type(pdf_page) is not int or pdf_page not in row['pdf_pages']:
        raise ValueError('请选择这张知识摘要明确引用的PDF原页。')
    identity = {'selection': row['selection'], 'pdf_pages': row['pdf_pages'], 'printed_pages': row['printed_pages']}
    return {'visual_asset_id': f"CONCEPTPAGE-{sha256(row['concept_id'].encode('utf8')).hexdigest()}-{pdf_page}",
            'revision': _digest({'concept': identity, 'pdf_page': pdf_page})}


class ConceptTextbookPageCatalog:
    def __init__(self, workspace):
        self.workspace = workspace

    def source(self, visual_asset_id, revision):
        match = _PAGE.fullmatch(visual_asset_id) if isinstance(visual_asset_id, str) else None
        if match is None:
            raise ValueError('知识摘要原页身份无效，请重新选择。')
        page = int(match[2])
        rows = [r for r in concept_options(self.workspace)
                if sha256(r['concept_id'].encode('utf8')).hexdigest() == match[1]]
        if (len(rows) != 1 or page not in rows[0]['pdf_pages']
                or concept_page_selection(rows[0], page)['revision'] != revision):
            raise ValueError('知识摘要或教材目录已变化，请重新选择原页。')
        row, _raw, source = checked_concept(self.workspace, rows[0]['selection'], read_source=True)
        if row['volume_id'] is None:
            raise ValueError('此摘要尚未对应已登记教材，原图来源待核对。')
        printed = row['printed_pages']
        selection = row['selection']
        return {'visual_asset_id': visual_asset_id, 'revision': revision,
            'volume_id': row['volume_id'], 'source_title': row['volume_title'],
            'source_sha256': source['source_sha256'], 'pdf_page': page,
            'printed_page': printed[row['pdf_pages'].index(page)] if isinstance(printed, list) else None,
            'pdf_bytes': source['pdf_bytes'], 'label': '知识摘要原页：' + row['title'],
            'source_kind': 'textbook_concept_page', 'record_id': row['concept_id'],
            'concept_revision': row['revision'], 'directory_revision': selection['directory_revision'],
            'concept_location': deepcopy(selection['location'])}

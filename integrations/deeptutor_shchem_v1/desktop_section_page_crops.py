"""Real registered/supplement section pages, independent of knowledge records."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

from .desktop_lesson_composition import digest
from .desktop_textbook_bookshelf import TextbookBookshelfService
from .reader_cancellation import check_read_cancelled

PAGE = re.compile(r'SECTIONPAGE-(registered|supplement)-([a-f0-9]{2,800})-([1-9][0-9]{0,5})\Z')
ORIGIN_FIELDS = ('source_kind', 'section_selection', 'section_pdf_pages', 'section_printed_pages',
                 'section_snapshot_sha256', 'section_content_sha256', 'section_registry_logical_sha256',
                 'directory_revision')


def _without_paths(value):
    if isinstance(value, dict):
        return {key: _without_paths(row) for key, row in value.items() if key != 'source_path'}
    if isinstance(value, list):
        return [_without_paths(row) for row in value]
    return value


def _scope(selection):
    mode = selection.get('mode') if isinstance(selection, dict) else None
    key = 'section_key' if mode == 'registered' else 'supplement_node_key'
    if (mode not in {'registered', 'supplement'} or set(selection) != {'mode', key, 'revision'}
            or not isinstance(selection[key], str) or not 1 <= len(selection[key]) <= 200
            or not isinstance(selection['revision'], str)
            or re.fullmatch('[a-f0-9]{64}', selection['revision']) is None):
        raise ValueError('教材原页须保留真实完整章节和当前目录版本。')
    return deepcopy(selection), key


def _source_identity(selection, source, registry, directory_revision):
    chosen, key = _scope(selection)
    pages = source.get('pdf_pages')
    data = source.get('pdf_bytes')
    if (source.get(key) != chosen[key] or not isinstance(data, bytes) or not data
            or sha256(data).hexdigest() != source.get('source_sha256')
            or not isinstance(pages, list) or not pages or pages != sorted(set(pages))
            or any(type(page) is not int or page < 1 for page in pages)):
        raise ValueError('本节原书、完整页列表或章节身份无法核对。')
    metadata = {name: deepcopy(value) for name, value in source.items() if name != 'pdf_bytes'}
    logical = _without_paths(metadata)
    for name in ('registry_sha256', 'product_sha256', 'revision', 'source_name'):
        logical.pop(name, None)
    # Asset positions and reading hints remain source semantics. Only the
    # physical registered PDF path is a separately confirmable location.
    printed = source.get('printed_pages')
    printed = deepcopy(printed) if isinstance(printed, list) and len(printed) == len(pages) else 'unknown'
    registry_logical = digest(_without_paths(registry))
    return {'source_kind': 'section_textbook_page', 'section_selection': chosen,
        'section_pdf_pages': deepcopy(pages), 'section_printed_pages': printed,
        'section_snapshot_sha256': digest(metadata),
        'section_content_sha256': digest({'scope': {k: v for k, v in chosen.items() if k != 'revision'},
                                         'metadata': logical}),
        'section_registry_logical_sha256': registry_logical,
        'directory_revision': directory_revision}


def section_page_selection(selection, source, pdf_page, registry, directory_revision):
    identity = _source_identity(selection, source, registry, directory_revision)
    if type(pdf_page) is not int or pdf_page not in identity['section_pdf_pages']:
        raise ValueError('请选择本节逐页列表中的一个完整原页。')
    chosen, key = _scope(selection)
    return {'visual_asset_id': f"SECTIONPAGE-{chosen['mode']}-{chosen[key].encode('utf8').hex()}-{pdf_page}",
            'revision': digest({'identity': identity, 'volume_id': source['volume_id'],
                'source_sha256': source['source_sha256'], 'pdf_page': pdf_page})}


class SectionTextbookPageCatalog:
    def __init__(self, workspace):
        self.workspace = Path(workspace).absolute()
        self.shelf = TextbookBookshelfService(workspace)

    def read_scope(self, selection):
        chosen, key = _scope(selection)
        source = (self.shelf.section_source(chosen[key], chosen['revision'])
                  if chosen['mode'] == 'registered' else
                  self.shelf.supplement_source(chosen[key], chosen['revision']))
        registry, directory_revision, _parents, _sections = self.shelf._directory()
        identity = _source_identity(chosen, source, registry, directory_revision)
        return source, registry, identity

    def rows(self, selection):
        source, registry, identity = self.read_scope(selection)
        rows = []
        for index, page in enumerate(identity['section_pdf_pages']):
            printed = identity['section_printed_pages']
            rows.append({'origin': 'section_textbook_page', 'pdf_page': page,
                'printed_page': printed[index] if isinstance(printed, list) else None,
                'caption': source.get('title', source['source_title']) + f' · PDF第{page}页',
                'label': f'打开本节完整原页并框选 · PDF {page}',
                'relation': '本节完整原页', 'volume_title': source['source_title'],
                'section_title': source.get('title', source['source_title']),
                'selection': section_page_selection(selection, source, page, registry, identity['directory_revision'])})
        return rows

    def source(self, visual_asset_id, revision, *, expected_origin=None, original_source_path=None):
        match = PAGE.fullmatch(visual_asset_id) if isinstance(visual_asset_id, str) else None
        if match is None or not isinstance(revision, str) or re.fullmatch('[a-f0-9]{64}', revision) is None:
            raise ValueError('章节原页身份无法核对，请重新选择本节完整原页。')
        try:
            scope_key = bytes.fromhex(match[2]).decode('utf8')
        except (ValueError, UnicodeError) as exc:
            raise ValueError('章节原页身份无法核对，请重新选择。') from exc
        mode, page = match[1], int(match[3])
        registry, current, _parents, sections = self.shelf._directory()
        if mode == 'registered':
            rows = [row for row in sections if row['section_key'] == scope_key]
            selected = {'mode': mode, 'section_key': scope_key, 'revision': current}
        else:
            rows = [row for row in self.shelf.snapshot()['supplements'] if row['supplement_node_key'] == scope_key]
            selected = {'mode': mode, 'supplement_node_key': scope_key,
                        'revision': rows[0]['revision'] if len(rows) == 1 else ''}
        if len(rows) != 1:
            raise ValueError('本节或补充栏目已缺失、重复或变化，请重新选择原页。')
        source, registry, identity = self.read_scope(selected)
        if page not in identity['section_pdf_pages']:
            raise ValueError('该原页不在当前完整章节的逐页列表中。')
        current_selection = section_page_selection(selected, source, page, registry, identity['directory_revision'])
        if current_selection['revision'] != revision:
            # A receipt can retain its sealed identity through a physical move
            # only. The ordinary page picker never takes this reader-only path.
            volumes = [row for row in registry['volumes'] if row['volume_id'] == source['volume_id']
                       and row['source_sha256'] == source['source_sha256']]
            actual = self.workspace / volumes[0]['source_path'] if len(volumes) == 1 else None
            if (not isinstance(expected_origin, dict) or expected_origin.get('source_kind') != 'section_textbook_page'
                    or actual is None or not isinstance(original_source_path, str)
                    or actual.absolute() == Path(original_source_path).absolute()
                    or expected_origin.get('visual_asset_id') != visual_asset_id
                    or expected_origin.get('revision') != revision
                    or any(expected_origin.get(key) != identity[key] for key in (
                        'section_pdf_pages', 'section_printed_pages', 'section_content_sha256',
                        'section_registry_logical_sha256'))
                    or expected_origin.get('source_sha256') != source['source_sha256']
                    or expected_origin.get('volume_id') != source['volume_id']):
                raise ValueError('章节目录、原书或来源版本已变化，请重新选择原页。')
            chosen, key = _scope(expected_origin.get('section_selection'))
            if chosen['mode'] != mode or chosen[key] != scope_key:
                raise ValueError('章节裁片的原始范围已变化。')
            identity = {key: deepcopy(expected_origin[key]) for key in ORIGIN_FIELDS}
        printed = identity['section_printed_pages']
        printed_page = printed[identity['section_pdf_pages'].index(page)] if isinstance(printed, list) else None
        check_read_cancelled()
        return {'visual_asset_id': visual_asset_id, 'revision': revision,
            'volume_id': source['volume_id'], 'source_title': source['source_title'],
            'source_sha256': source['source_sha256'], 'pdf_page': page, 'printed_page': printed_page,
            'pdf_bytes': source['pdf_bytes'], 'label': f"本节原页：{source.get('title', source['source_title'])}",
            **identity}

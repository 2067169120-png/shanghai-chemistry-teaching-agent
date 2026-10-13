"""Read-only chapter handoff to the existing source-bound Word question browser."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from . import desktop_textbook_asset_catalog as catalog
from . import desktop_textbook_assets as metadata
from .desktop_preparation_sources import PreparationSourceError
from .desktop_textbook_bookshelf import TextbookBookshelfService
from .desktop_word_question_filters import chapter_filter_id, section_filter_id


def _error(message):
    raise PreparationSourceError(message)


def _pages(node):
    from .desktop_textbook_section_reader import _page_range
    return _page_range(node['content_pdf_pages'])


def _mapped(node, revision, *, origin, title, pages, source_sha256):
    return {
        'schema_version': 1, 'mode': 'curriculum', 'entry_origin': origin,
        'title': title, 'source_sha256': source_sha256, 'pdf_pages': pages,
        'directory_revision': revision, 'node_revision': catalog._canonical_sha256(node),
        'volume_id': node['volume_id'], 'chapter_id': node['chapter_id'],
        'section_key': node['node_key'], 'candidate_only': True, 'human_reviewed': False,
        'statement': '按已保存的教材标签筛选；自动建议仍待教师核对。完整预览后可加入当前备课。',
    }


def question_filter_context(context, attribute_catalog):
    """Validate the complete directory node before producing filter identities.

    A stale context fails closed. An explicitly requested text search contains
    no inferred curriculum IDs. This function has no state or file access.
    """
    if not isinstance(context, dict) or type(context.get('schema_version')) is not int or context['schema_version'] != 1:
        _error('教材选题范围无效，请返回教材页重新打开。')
    title = metadata._text(context.get('title'), 200)
    metadata._sha256(context.get('source_sha256'))
    pages = context.get('pdf_pages')
    if (not isinstance(pages, list) or not pages or len(pages) > metadata.MAX_PAGES
            or any(type(page) is not int or not 1 <= page <= metadata.MAX_PAGES for page in pages)
            or pages != list(range(pages[0], pages[-1] + 1))):
        _error('教材选题页范围无效，请返回教材页重新核对。')
    if context.get('mode') == 'text_search':
        if any(context.get(field) is not None for field in ('volume_id', 'chapter_id', 'section_key', 'node_revision')):
            _error('文字检索不能带入未经核对的教材归属。')
        query = metadata._text(context.get('search_query'), 200)
        return {'selection': {}, 'query': query, 'title': title, 'mapped': False}
    if context.get('mode') != 'curriculum':
        _error('教材选题方式无效，请返回教材页重新打开。')
    rows = attribute_catalog.get('nodes') if isinstance(attribute_catalog, dict) else None
    matches = [row for row in rows if isinstance(row, dict) and row.get('node_key') == context.get('section_key')] if isinstance(rows, list) else []
    if (len(matches) != 1 or catalog._canonical_sha256(matches[0]) != context.get('node_revision')
            or any(matches[0].get(field) != context.get(field) for field in ('volume_id', 'chapter_id', 'source_sha256'))
            or _pages(matches[0]) != pages):
        _error('本节目录或版本已变化，请返回教材页刷新；也可明确选择“查看全部题目”。')
    node = matches[0]
    return {'selection': {
        'book': [node['volume_id']],
        'chapter': [chapter_filter_id(node['volume_id'], node['chapter_id'])],
        'section': [section_filter_id(node['volume_id'], node['chapter_id'], node['node_key'])],
    }, 'query': '', 'title': title, 'mapped': True}


class TextbookQuestionContextService:
    def __init__(self, workspace, personal_study=None):
        self.workspace = Path(workspace).absolute()
        self.personal_study = personal_study

    def resolve(self, request):
        try:
            if not isinstance(request, dict):
                _error('请先选择一个教材章节。')
            if request.get('mode') == 'registered':
                result = self._registered(request)
            elif request.get('mode') == 'personal' and self.personal_study is not None:
                result = self._personal(request)
            else:
                _error('请先选择一个教材章节。')
            result['validation_request'] = deepcopy(request)
            return result
        except PreparationSourceError:
            raise
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise PreparationSourceError(str(exc) if hasattr(exc, 'message_zh') else
                                         '教材原文件、章节版本或页范围无法核对，请刷新后重选。') from exc

    def validate(self, context):
        if not isinstance(context, dict) or self.resolve(context.get('validation_request')) != context:
            _error('教材章节或原书版本已变化，请返回教材页刷新后重新打开题目。')
        return True

    def _registered(self, request):
        key = metadata._text(request.get('section_key'))
        revision = metadata._sha256(request.get('revision'))
        shelf = TextbookBookshelfService(self.workspace)
        registry, current, _, sections = shelf._directory()
        if revision != current:
            _error('教材目录版本已变化，请刷新书架后重选本节。')
        node = next((row for row in registry['nodes'] if row['node_key'] == key), None)
        section = next((row for row in sections if row['section_key'] == key), None)
        if node is None or section is None:
            _error('所选教材节已缺失，请刷新后重选。')
        catalog._read_pdf(self.workspace, catalog._source_relative(node['source_path']),
                          node['source_sha256'], max(section['pdf_pages']))
        if shelf._directory()[1] != revision:
            _error('教材目录在原书核对期间已变化，请刷新后重选。')
        return _mapped(node, revision, origin='registered_textbook', title=section['title'],
                       pages=section['pdf_pages'], source_sha256=node['source_sha256'])

    def _personal(self, request):
        selection = deepcopy(request.get('selection'))
        section_id = metadata._text(request.get('section_id'))
        study_revision = metadata._sha256(request.get('study_revision'))
        source = self.personal_study.read(selection, section_id=section_id, study_revision=study_revision)
        title = metadata._text(source['title'], 200)
        sha = metadata._sha256(source['source_sha256'])
        pages = source['pdf_pages']
        # A personal book remains usable when the activated directory is absent.
        # Only exact original bytes AND the entire page vector establish a link.
        try:
            registry, revision, _ = catalog._directory(self.workspace)
        except catalog.TextbookAssetCatalogError:
            registry, revision = {'nodes': []}, None
        matches = [node for node in registry['nodes'] if node['source_sha256'] == sha and _pages(node) == pages]
        if self.personal_study.book_study(selection)['study_revision'] != study_revision:
            _error('个人教材章节在原书核对期间已变化，请刷新后重选。')
        if revision is not None and catalog._directory(self.workspace)[1] != revision:
            _error('教材目录在原书核对期间已变化，请刷新后重选。')
        binding = {'selection': selection, 'section_id': section_id, 'study_revision': study_revision}
        if len(matches) == 1:
            result = _mapped(matches[0], revision, origin='personal_textbook', title=title,
                             pages=pages, source_sha256=sha)
        else:
            query = re.sub(r'^\s*\d+(?:[.．]\d+)*\s*', '', title).strip() or title
            result = {'schema_version': 1, 'mode': 'text_search', 'entry_origin': 'personal_textbook',
                      'title': title, 'source_sha256': sha, 'pdf_pages': pages, 'search_query': query,
                      'candidate_only': True, 'human_reviewed': False,
                      'statement': '本节尚无唯一教材标签对应；当前按章节文字检索，可修改搜索词。请逐题核对来源和适用范围。'}
        result['personal_binding'] = binding
        return result

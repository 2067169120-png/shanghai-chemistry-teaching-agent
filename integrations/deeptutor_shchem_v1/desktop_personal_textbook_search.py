"""Read-only, version-bound search of one archived PDF and its saved study.

No OCR, model, network request or durable index is involved. Native text, saved
directory entries and saved notes are distinct results with real PDF positions.
"""
from copy import deepcopy
import hashlib
import io
import unicodedata

from .desktop_preparation_sources import PreparationSourceError
from .desktop_textbook_workspace import _digest
from .reader_cancellation import ReadCancelled, check_read_cancelled

SEARCH_REVISION = 'personal-textbook-search/1'
MAX_QUERY = 160
MAX_PAGE_STREAM = 8 * 1024 * 1024
MAX_PAGE_TEXT = 1_000_000
CONTENT_TYPES = ('all', 'original_text', 'organized')


def normalized(text):
    """Normalize searchable characters, while retaining unmodified excerpts."""
    # Element symbols and formulas are case-sensitive (CO is not Co).
    return ''.join(unicodedata.normalize('NFKC', text).replace('−', '-').split())


def _match(text, needle):
    value = normalized(text); position = value.find(needle)
    if position < 0: return None
    # Locate the corresponding original prefix without rewriting chemical
    # subscripts, spaces or symbols in the excerpt. Prefix lengths also handle
    # normalization of multi-character compatibility forms and combining marks.
    left, right = 0, len(text)
    while left < right:
        middle = (left+right+1)//2
        if len(normalized(text[:middle])) <= position: left = middle
        else: right = middle-1
    start = max(0, left-60); end = min(len(text), max(start+320, left+len(needle)+60))
    return dict(excerpt=text[start:end], excerpt_start=start, excerpt_end=end, occurrences=value.count(needle))


class PersonalTextbookSearchService:
    def __init__(self, facade): self.facade = facade

    def _source(self, selection, section_id, study_revision):
        check_read_cancelled()
        view = self.facade.personal_textbook_study().book_study(selection)
        if study_revision is not None and view['study_revision'] != study_revision:
            raise PreparationSourceError('章节或知识笔记已变化，请重新打开书内查找。')
        source = self.facade.personal_textbook_source(selection, section_id=section_id,
                                                     study_revision=study_revision)
        data = source.get('pdf_bytes'); book = view['book']
        if (not isinstance(data, bytes) or source.get('source_id') != selection['source_id']
                or source.get('revision') != selection['revision']
                or source.get('source_sha256') != selection['revision']
                or book.get('revision') != selection['revision']
                or source.get('page_count') != book['page_count']
                or hashlib.sha256(data).hexdigest() != selection['revision']):
            raise PreparationSourceError('原书内容与选择版本不一致，请刷新后重新查找。')
        pages = source.get('pdf_pages')
        section = next((s for s in view['sections'] if s['section_id'] == section_id), None)
        wanted = list(range(section['pdf_start'], section['pdf_end']+1)) if section else list(range(1, book['page_count']+1))
        if section_id is not None and section is None or pages != wanted:
            raise PreparationSourceError('查找章节或PDF页范围已变化，请重新选择。')
        check_read_cancelled()
        return view, source

    def search(self, selection, query, *, section_id=None, study_revision=None,
               content='all', offset=0, limit=25, expected=None, progress_callback=None):
        if (not isinstance(query, str) or not 1 <= len(query.strip()) <= MAX_QUERY
                or not normalized(query) or any(ord(c) < 32 and not c.isspace() for c in query)
                or content not in CONTENT_TYPES or type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= 100
                or expected is not None and (not isinstance(expected, str) or len(expected) != 64)):
            raise PreparationSourceError('请输入1—160字的查找文字，并选择有效的范围。')
        query = query.strip(); needle = normalized(query)
        view, source = self._source(selection, section_id, study_revision)
        pages = source['pdf_pages']; allowed = set(pages); rows = []; errors = []
        native_pages = empty_pages = 0
        binding = dict(source_id=source['source_id'], revision=source['revision'],
            source_sha256=source['source_sha256'], bytes_count=len(source['pdf_bytes']), page_count=source['page_count'])
        def known_chapters(page):
            return [{'section_id': s['section_id'], 'revision': s['revision'], 'title': s['title'],
                     'pdf_start': s['pdf_start'], 'pdf_end': s['pdf_end']}
                    for s in view['sections'] if s['pdf_start'] <= page <= s['pdf_end']]
        def row(kind, identity, title, source_pages, text, **extra):
            match = _match(text, needle)
            if match is None: return
            rows.append(dict(kind=kind, key=identity, title=title, pdf_pages=source_pages,
                printed_pages=None, chapters=known_chapters(source_pages[0]), **binding, **match, **extra))
        if content in ('all', 'original_text'):
            from pypdf import PdfReader
            try:
                reader = PdfReader(io.BytesIO(source['pdf_bytes']))
                if reader.is_encrypted or len(reader.pages) != binding['page_count']:
                    raise ValueError('PDF version mismatch')
            except ReadCancelled: raise
            except Exception as exc:
                raise PreparationSourceError('原书文字暂时无法读取，可继续阅读原页或查找已保存目录与笔记。') from exc
            for index, page in enumerate(pages):
                check_read_cancelled()
                try:
                    pdf_page = reader.pages[page-1]; stream = pdf_page.get_contents()
                    if stream is not None and len(stream.get_data()) > MAX_PAGE_STREAM:
                        raise ValueError('page content exceeds native text budget')
                    text = pdf_page.extract_text() or ''
                    if not isinstance(text, str) or len(text) > MAX_PAGE_TEXT:
                        raise ValueError('page text exceeds native text budget')
                    check_read_cancelled()
                except ReadCancelled: raise
                except Exception:
                    errors.append(page)
                else:
                    if normalized(text): native_pages += 1
                    else: empty_pages += 1
                    row('original_text', f"{binding['source_id']}:PDF:{page}", f'PDF第{page}页原文', [page], text)
                if progress_callback: progress_callback(dict(completed=index+1, total=len(pages), pdf_page=page))
            # Release the large parsed PDF before the second verified read.
            pdf_page = stream = None
            reader.close(); del reader
        if content in ('all', 'organized'):
            for section in view['sections']:
                check_read_cancelled()
                if section_id is not None and section['section_id'] != section_id: continue
                row('section', section['section_id'], section['title'], [section['pdf_start']], section['title'],
                    section_id=section['section_id'], section_revision=section['revision'],
                    pdf_start=section['pdf_start'], pdf_end=section['pdf_end'])
            for note in view['notes']:
                check_read_cancelled()
                if section_id is not None and note['section_id'] != section_id: continue
                note_pages = note.get('source_evidence', {}).get('pdf_pages', [note['pdf_page']])
                if not note_pages or any(p not in allowed for p in note_pages):
                    raise PreparationSourceError('知识笔记原页超出当前范围，请刷新后重新查找。')
                row('knowledge_note', note['note_id'], note['title'], deepcopy(note_pages),
                    note['title']+'\n'+note['text'], section_id=note['section_id'], note_revision=note['revision'],
                    note_text=note['text'])
        del source
        check_read_cancelled()
        fresh_view, fresh_source = self._source(selection, section_id, view['study_revision'])
        fresh_binding = dict(source_id=fresh_source['source_id'], revision=fresh_source['revision'],
            source_sha256=fresh_source['source_sha256'], bytes_count=len(fresh_source['pdf_bytes']),
            page_count=fresh_source['page_count'])
        if fresh_binding != binding or fresh_source['pdf_pages'] != pages or fresh_view != view:
            raise PreparationSourceError('查找期间原书或已保存整理发生变化，请重新查找。')
        del fresh_source
        counts = {kind: sum(r['kind'] == kind for r in rows) for kind in ('original_text', 'section', 'knowledge_note')}
        identity = _digest(dict(renderer=SEARCH_REVISION, source=binding, study_revision=view['study_revision'],
            query=query, content=content, section_id=section_id, pdf_pages=pages, results_sha256=_digest(rows),
            native_pages=native_pages, empty_pages=empty_pages, unreadable_pages=errors))
        if expected is not None and identity != expected:
            raise PreparationSourceError('查找结果依据已变化，请重新查找后定位原页。')
        check_read_cancelled()
        return dict(selection=deepcopy(selection), source=binding, source_name=view['book']['source_name'],
            study_revision=view['study_revision'], search_identity=identity, query=query, content=content,
            section_id=section_id, section_title=next((s['title'] for s in view['sections'] if s['section_id'] == section_id), None),
            pdf_pages=pages, items=rows[offset:offset+limit], total=len(rows),
            total_occurrences=sum(r['occurrences'] for r in rows), counts=counts, offset=offset, limit=limit,
            has_more=offset+limit<len(rows), native_searched=content != 'organized', native_text_pages=native_pages,
            no_native_text_pages=empty_pages, unreadable_pages=errors)

"""Explicit knowledge-card pages, bound to the current note and original PDF."""
from hashlib import sha256
import json
import re

from .desktop_textbook_bookshelf import TextbookBookshelfService
from .reader_cancellation import check_read_cancelled

_PAGE = re.compile(r'NOTEPAGE-([a-f0-9]{64})-([1-9][0-9]{0,5})\Z')


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf8')).hexdigest()


def note_page_selection(row, pdf_page):
    pages = row.get('pdf_pages')
    if (not isinstance(pages, list) or type(pdf_page) is not int or pdf_page not in pages
            or not isinstance(row.get('note_id'), str) or not row['note_id']):
        raise ValueError('请选择这张知识卡明确引用的PDF原页。')
    identity = {k: row[k] for k in ('note_id', 'section_key', 'revision', 'directory_revision',
                                   'textbook_sha256', 'pdf_pages', 'printed_pages')}
    return {'visual_asset_id': f"NOTEPAGE-{sha256(row['note_id'].encode('utf8')).hexdigest()}-{pdf_page}",
            'revision': _digest({'note': identity, 'pdf_page': pdf_page})}


class NoteTextbookPageCatalog:
    def __init__(self, workspace):
        self.shelf = TextbookBookshelfService(workspace)

    def _row(self, visual_asset_id, revision):
        match = _PAGE.fullmatch(visual_asset_id) if isinstance(visual_asset_id, str) else None
        if match is None:
            raise ValueError('知识卡原页身份无效，请重新选择。')
        page = int(match[2])
        rows = [r for r in self.shelf.snapshot()['reading_notes']
                if sha256(r['note_id'].encode('utf8')).hexdigest() == match[1]]
        if (len(rows) != 1 or page not in rows[0]['pdf_pages']
                or note_page_selection(rows[0], page)['revision'] != revision):
            raise ValueError('知识卡或教材目录已变化，请重新选择知识卡原页。')
        return rows[0], page

    def source(self, visual_asset_id, revision):
        row, page = self._row(visual_asset_id, revision)
        selected = {k: row[k] for k in ('note_id', 'section_key', 'revision', 'directory_revision')}
        self.shelf.reading_reference([selected])
        source = self.shelf.section_source(row['section_key'], row['directory_revision'])
        if (source['source_sha256'] != row['textbook_sha256']
                or self._row(visual_asset_id, revision)[0] != row):
            raise ValueError('知识卡或教材原书在读取期间变化，请刷新后重选。')
        printed = row['printed_pages']
        printed_page = (printed[row['pdf_pages'].index(page)]
                        if isinstance(printed, list) and len(printed) == len(row['pdf_pages']) else None)
        check_read_cancelled()
        return {'visual_asset_id': visual_asset_id, 'revision': revision,
                'volume_id': row['volume_id'], 'source_title': source['source_title'],
                'source_sha256': source['source_sha256'], 'pdf_page': page,
                'printed_page': printed_page, 'pdf_bytes': source['pdf_bytes'],
                'label': f"知识卡原页：{row['title']}", 'source_kind': 'knowledge_note_page',
                'record_id': row['note_id'], 'section_key': row['section_key'],
                'note_revision': row['revision'], 'directory_revision': row['directory_revision']}

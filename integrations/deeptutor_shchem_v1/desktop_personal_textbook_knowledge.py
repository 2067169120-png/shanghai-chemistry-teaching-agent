"""Page-bound personal knowledge candidates, explicit review and batch notes."""
from __future__ import annotations

from copy import deepcopy
import io
import re

from .desktop_textbook_personal_study import FLAGS, PersonalTextbookStudyService, _text
from .desktop_textbook_workspace import _digest
from .desktop_preparation_sources import PreparationSourceError
from .desktop_import_identity import identity_guard
from .desktop_state import utc_now
from .reader_cancellation import check_read_cancelled

DRAFT_SCHEMA = 'shchem.personal-textbook-knowledge-draft.v1'
EVIDENCE_SCHEMA = 'shchem.personal-textbook-knowledge-evidence.v1'
MAX_PAGES = 8
MAX_ITEMS = 20
MAX_DRAFTS = 200
METHODS = {'pdf_native_text': '本机文字摘录', 'model_visual': 'AI原页归纳'}
DRAFT_FIELDS = {'schema', 'draft_id', 'source_id', 'source_sha256', 'source_name', 'section_id',
    'section_revision', 'section_title', 'pdf_pages', 'method', 'items', 'review_edits', 'created_at', 'warnings', 'revision', *FLAGS}

def fail(message):
    raise PreparationSourceError(message)

def knowledge_schema():
    item = {'type': 'object', 'additionalProperties': False, 'properties': {
        'title': {'type': 'string', 'minLength': 1, 'maxLength': 160},
        'text': {'type': 'string', 'minLength': 1, 'maxLength': 1200},
        'pdf_pages': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1, 'maxItems': MAX_PAGES, 'uniqueItems': True},
        'source_quote': {'type': 'string', 'minLength': 1, 'maxLength': 1200},
        'limitation': {'type': 'string', 'minLength': 1, 'maxLength': 800}},
        'required': ['title', 'text', 'pdf_pages', 'source_quote', 'limitation']}
    return {'type': 'object', 'additionalProperties': False,
        'properties': {'items': {'type': 'array', 'items': item, 'minItems': 1, 'maxItems': MAX_ITEMS}}, 'required': ['items']}

def validate_note_evidence(value, source_sha256, page_count):
    fields = {'schema', 'draft_id', 'candidate_id', 'source_sha256', 'method', 'pdf_pages', 'source_quote', 'limitation'}
    if (not isinstance(value, dict) or set(value) != fields or value['schema'] != EVIDENCE_SCHEMA
            or value['source_sha256'] != source_sha256 or not isinstance(value['method'], str) or value['method'] not in METHODS
            or not re.fullmatch(r'BKN-[0-9a-f]{32}', str(value['draft_id']))
            or not re.fullmatch(r'KITEM-[0-9a-f]{32}', str(value['candidate_id']))
            or not isinstance(value['pdf_pages'], list) or not 1 <= len(value['pdf_pages']) <= MAX_PAGES
            or any(type(p) is not int or not 1 <= p <= page_count for p in value['pdf_pages'])
            or value['pdf_pages'] != sorted(set(value['pdf_pages']))):
        fail('知识笔记的原页与候选来源不完整，请保留记录并检查备份。')
    _text(value['source_quote'], 1200, '候选依据')
    _text(value['limitation'], 800, '候选适用范围')

def validate_drafts(record):
    rows = record.get('knowledge_drafts', [])
    books = {r['source_id']: r for r in record['books']}
    if not isinstance(rows, list) or len(rows) > MAX_DRAFTS:
        fail('教材知识候选记录无法核对，请保留旧记录并检查备份。')
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != DRAFT_FIELDS or row.get('schema') != DRAFT_SCHEMA
                or not isinstance(row.get('source_id'), str) or row['source_id'] not in books
                or not re.fullmatch(r'BKN-[0-9a-f]{32}', str(row.get('draft_id', ''))) or row['draft_id'] in seen
                or row.get('revision') != _digest({k: v for k, v in row.items() if k != 'revision'})
                or row.get('source_sha256') != books[row['source_id']]['source_sha256']
                or any(row.get(k) is not v for k, v in FLAGS.items())
                or not isinstance(row.get('items'), list) or not 1 <= len(row['items']) <= MAX_ITEMS
                or not isinstance(row.get('pdf_pages'), list) or not 1 <= len(row['pdf_pages']) <= MAX_PAGES
                or any(type(p) is not int or not 1 <= p <= books[row['source_id']]['page_count'] for p in row['pdf_pages'])
                or row['pdf_pages'] != list(range(row['pdf_pages'][0], row['pdf_pages'][-1] + 1))
                or not isinstance(row.get('method'), str) or row['method'] not in METHODS
                or not re.fullmatch(r'BSEC-[0-9a-f]{32}', str(row.get('section_id', '')))
                or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('section_revision', '')))
                or row.get('source_name') != books[row['source_id']]['source_name']
                or not isinstance(row.get('created_at'), str) or not row['created_at']
                or not isinstance(row.get('warnings'), list) or any(not isinstance(v, str) for v in row['warnings'])):
            fail('教材知识候选的原书、页序或版本不完整，未覆盖旧记录。')
        _text(row['section_title'], 160, '候选章节名称')
        ids = set()
        for item in row['items']:
            if not isinstance(item, dict) or set(item) != {'candidate_id', 'title', 'text', 'source_evidence', *FLAGS}:
                fail('知识候选条目格式不完整。')
            validate_note_evidence(item.get('source_evidence'), row['source_sha256'], books[row['source_id']]['page_count'])
            evidence = item['source_evidence']
            if (item.get('candidate_id') in ids or item.get('candidate_id') != evidence['candidate_id']
                    or evidence['draft_id'] != row['draft_id'] or evidence['method'] != row['method']
                    or not set(evidence['pdf_pages']).issubset(row['pdf_pages'])
                    or any(item.get(k) is not v for k, v in FLAGS.items())):
                fail('知识候选条目与本批来源不一致，请重新核对。')
            _text(item.get('title'), 160, '候选名称'); _text(item.get('text'), 1200, '候选正文')
            ids.add(item['candidate_id'])
        decoded = [{'title': i['title'], 'text': i['text'], 'pdf_pages': i['source_evidence']['pdf_pages'],
            'source_quote': i['source_evidence']['source_quote'], 'limitation': i['source_evidence']['limitation']} for i in row['items']]
        seed = {key: row[key] for key in ('source_sha256', 'section_id', 'section_revision', 'pdf_pages', 'method')}
        seed['items'] = decoded
        if row['draft_id'] != 'BKN-' + _digest(seed)[:32] or any(item['candidate_id'] != 'KITEM-' + _digest({'draft_id': row['draft_id'], 'index': n, 'item': decoded[n]})[:32] for n, item in enumerate(row['items'])):
            fail('知识候选内容与来源身份不一致，请重新核对。')
        validate_review_edits(row['review_edits'], ids)
        seen.add(row['draft_id'])
    return rows

def validate_review_edits(edits, candidate_ids):
    if (not isinstance(edits, list) or len(edits) > MAX_ITEMS
            or any(not isinstance(e, dict) or set(e) != {'candidate_id', 'title', 'text'}
                or not isinstance(e['candidate_id'], str) or e['candidate_id'] not in candidate_ids
                or not isinstance(e['title'], str) or len(e['title']) > 160
                or not isinstance(e['text'], str) or len(e['text']) > 1200 for e in edits)
            or len({e['candidate_id'] for e in edits}) != len(edits)):
        fail('修订进度格式不完整，或文字超过1200字；未覆盖已保存进度。')
    from .desktop_preparation import _reject_sensitive
    for edit in edits:
        _reject_sensitive(edit['title']); _reject_sensitive(edit['text'])

class PersonalTextbookKnowledgeService:
    def __init__(self, workspace):
        self.workspace = workspace
        self.study = PersonalTextbookStudyService(workspace)

    def _context(self, selection, study_revision, section_id):
        view = self.study.book_study(selection)
        if view['study_revision'] != study_revision:
            fail('章节或笔记已变化，请刷新后重新选择知识整理范围。')
        section = next((s for s in view['sections'] if s['section_id'] == section_id), None)
        if section is None:
            fail('请先选择已保存的教材章节。')
        source = self.workspace.read_book(selection['source_id'], selection['revision'])
        return view, section, source

    def prepare(self, selection, study_revision, section_id, pdf_start, pdf_end):
        view, section, source = self._context(selection, study_revision, section_id)
        if (type(pdf_start) is not int or type(pdf_end) is not int
                or not section['pdf_start'] <= pdf_start <= pdf_end <= section['pdf_end']
                or pdf_end - pdf_start + 1 > MAX_PAGES):
            fail(f'每批请选择当前章节内连续1至{MAX_PAGES}个PDF页，不会自动扩大范围。')
        return {'selection': deepcopy(selection), 'study_revision': study_revision,
            'section_id': section_id, 'section_revision': section['revision'],
            'section_title': section['title'], 'pdf_pages': list(range(pdf_start, pdf_end + 1)),
            'source_name': source['source_name'], 'source_sha256': selection['revision'],
            'pdf_bytes': source['pdf_bytes']}

    def create(self, prepared, decoded, method):
        from jsonschema import Draft202012Validator
        try:
            Draft202012Validator(knowledge_schema()).validate(decoded)
        except Exception:
            fail('知识候选结构不完整，本批未保存；请减少页数或重新整理。')
        if not isinstance(method, str) or method not in METHODS:
            fail('知识整理方式无法核对。')
        self._context(prepared['selection'], prepared['study_revision'], prepared['section_id'])
        if any(not set(item['pdf_pages']).issubset(prepared['pdf_pages']) for item in decoded['items']):
            fail('模型返回的知识超出本次原页范围，本批未保存。')
        seed = {'source_sha256': prepared['source_sha256'], 'section_id': prepared['section_id'],
            'section_revision': prepared['section_revision'], 'pdf_pages': prepared['pdf_pages'], 'method': method, 'items': decoded['items']}
        draft_id = 'BKN-' + _digest(seed)[:32]
        items = []
        for number, item in enumerate(decoded['items']):
            candidate_id = 'KITEM-' + _digest({'draft_id': draft_id, 'index': number, 'item': item})[:32]
            evidence = {'schema': EVIDENCE_SCHEMA, 'draft_id': draft_id, 'candidate_id': candidate_id,
                'source_sha256': prepared['source_sha256'], 'method': method, 'pdf_pages': list(item['pdf_pages']),
                'source_quote': item['source_quote'], 'limitation': item['limitation']}
            items.append({'candidate_id': candidate_id, 'title': item['title'], 'text': item['text'], 'source_evidence': evidence, **FLAGS})
        draft = {'schema': DRAFT_SCHEMA, 'draft_id': draft_id, 'source_id': prepared['selection']['source_id'],
            'source_sha256': prepared['source_sha256'], 'source_name': prepared['source_name'],
            'section_id': prepared['section_id'], 'section_revision': prepared['section_revision'],
            'section_title': prepared['section_title'], 'pdf_pages': list(prepared['pdf_pages']),
            'method': method, 'items': items, 'review_edits': [], 'created_at': utc_now(),
            'warnings': ['候选文字和公式须逐条对照原页；图表仍需单独选用。',
                'AI原页归纳为模型改述，引用依据亦待核对，不当作已核验教材原句。' if method == 'model_visual'
                else '本机文字摘录未读取原图，不会补全无法提取的公式或图表。'], **FLAGS}
        draft['revision'] = _digest(draft)
        validate_drafts({'books': [self.workspace.read_book(prepared['selection']['source_id'], prepared['source_sha256'])], 'knowledge_drafts': [draft]})
        return draft

    def preview_native(self, selection, study_revision, section_id, pdf_start, pdf_end):
        prepared = self.prepare(selection, study_revision, section_id, pdf_start, pdf_end)
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(prepared['pdf_bytes']))
        items = []
        for number in prepared['pdf_pages']:
            check_read_cancelled()
            page = reader.pages[number - 1]
            contents = page.get_contents()
            if contents and len(contents.get_data()) > 8 * 1024 * 1024:
                fail('本页文字数据过大，请缩小范围或改用原页归纳。')
            text = page.extract_text() or ''
            for paragraph in re.split(r'\n\s*\n', text):
                paragraph = paragraph.strip()
                if not paragraph:
                    continue
                # Split at real line boundaries; never truncate a long formula.
                chunks, current = [], ''
                for line in paragraph.splitlines():
                    if len(line) > 1200:
                        fail('本页存在超过1200字的不可分段文字，请手工整理或改用AI原页归纳。')
                    next_text = current + ('\n' if current else '') + line
                    if len(next_text) > 1200:
                        chunks.append(current); current = line
                    else:
                        current = next_text
                if current: chunks.append(current)
                for chunk in chunks:
                    items.append({'title': chunk.splitlines()[0][:160], 'text': chunk,
                        'pdf_pages': [number], 'source_quote': chunk,
                        'limitation': '原PDF文字提取，可能漏掉图片、排版及公式；请对照原页修订。'})
                    if len(items) > MAX_ITEMS:
                        fail(f'本批文字超过{MAX_ITEMS}条候选，请减少页数；没有截断或保存本批。')
        if not items:
            fail('这些页没有可读的原生文字。可选AI原页归纳，或阅读原页后手工记笔记。')
        return self.create(prepared, {'items': items}, 'pdf_native_text')

    def drafts(self, selection, section_id):
        self.study.book_study(selection)
        record = self.workspace._record(self.workspace.state.snapshot())
        return deepcopy([row for row in validate_drafts(record) if row['source_id'] == selection['source_id'] and row['section_id'] == section_id])

    def save_draft(self, selection, study_revision, draft):
        view, section, _source = self._context(selection, study_revision, draft.get('section_id'))
        validate_drafts({'books': [view['book']], 'knowledge_drafts': [draft]})
        if draft['source_id'] != selection['source_id'] or draft['section_revision'] != section['revision']:
            fail('知识候选与当前教材章节不一致，请重新整理。')
        with identity_guard(self.workspace.paths.state_root):
            check_read_cancelled()
            self._context(selection, study_revision, draft['section_id'])
            def edit(value):
                record = self.workspace._record(value)
                if self.study._view(record, self.study._book(record, selection))['study_revision'] != study_revision:
                    fail('章节或笔记在保存前变化，未覆盖旧记录。')
                rows = deepcopy(validate_drafts(record))
                old = next((r for r in rows if r['draft_id'] == draft['draft_id']), None)
                if old is not None: return False
                if len(rows) >= MAX_DRAFTS: fail('知识候选批次达到保存上限，请先备份并整理已有候选。')
                rows.append(deepcopy(draft)); record['knowledge_drafts'] = rows
                validate_drafts(record); value['textbook_workspace'] = record
                check_read_cancelled()
            self.workspace.state._update(edit)
        return next(r for r in self.drafts(selection, draft['section_id']) if r['draft_id'] == draft['draft_id'])

    def save_progress(self, selection, study_revision, draft_id, draft_revision, edits):
        """Retain edited text only; checks and lesson selection are never restored."""
        with identity_guard(self.workspace.paths.state_root):
            check_read_cancelled()
            record = self.workspace._record(self.workspace.state.snapshot())
            draft = next((r for r in validate_drafts(record) if r['draft_id'] == draft_id), None)
            if draft is None or draft['revision'] != draft_revision or draft['source_id'] != selection.get('source_id'):
                fail('候选批次已变化，请重新打开后核对；未覆盖修订进度。')
            _view, section, _source = self._context(selection, study_revision, draft['section_id'])
            if section['revision'] != draft['section_revision']:
                fail('候选对应的章节已变化，原批次仍保留；请重新整理。')
            validate_review_edits(edits, {i['candidate_id'] for i in draft['items']})
            def edit(value):
                current = self.workspace._record(value)
                if self.study._view(current, self.study._book(current, selection))['study_revision'] != study_revision:
                    fail('章节或笔记在保存前变化，未覆盖修订进度。')
                rows = deepcopy(validate_drafts(current))
                old = next((r for r in rows if r['draft_id'] == draft_id and r['revision'] == draft_revision), None)
                if old is None: fail('候选批次在保存前变化，未覆盖修订进度。')
                if old['review_edits'] == edits: return False
                old['review_edits'] = deepcopy(edits)
                old['revision'] = _digest({k: v for k, v in old.items() if k != 'revision'})
                current['knowledge_drafts'] = rows; validate_drafts(current)
                value['textbook_workspace'] = current; check_read_cancelled()
            self.workspace.state._update(edit)
        return next(r for r in self.drafts(selection, draft['section_id']) if r['draft_id'] == draft_id)

    def adopt(self, selection, study_revision, draft_id, draft_revision, chosen):
        if (not isinstance(chosen, list) or not 1 <= len(chosen) <= MAX_ITEMS
                or any(not isinstance(r, dict) or set(r) != {'candidate_id', 'title', 'text', 'confirmed'}
                    or not isinstance(r['candidate_id'], str) or r['confirmed'] is not True for r in chosen)
                or len({r['candidate_id'] for r in chosen}) != len(chosen)):
            fail('请逐条核对候选并勾选确认，再保存为本次可选知识笔记。')
        rows = validate_drafts(self.workspace._record(self.workspace.state.snapshot()))
        draft = next((r for r in rows if r['draft_id'] == draft_id), None)
        if draft is None or draft['revision'] != draft_revision:
            fail('知识候选已变化或缺失，请重新打开后核对。')
        _view, section, _source = self._context(selection, study_revision, draft['section_id'])
        if draft['source_id'] != selection['source_id'] or section['revision'] != draft['section_revision']:
            fail('知识候选对应的章节范围已变化，请重新整理；原候选仍保留。')
        available = {r['candidate_id']: r for r in draft['items']}
        added, reused = [], []
        def edit(doc, book):
            fresh = validate_drafts(self.workspace._record(self.workspace.state.snapshot()))
            if not any(r['draft_id'] == draft_id and r['revision'] == draft_revision for r in fresh):
                fail('知识候选在保存前变化，未覆盖任何旧笔记。')
            for request in chosen:
                check_read_cancelled()
                candidate = available.get(request['candidate_id'])
                if candidate is None: fail('所选知识不属于当前候选批次。')
                _text(request['title'], 160, '知识笔记名称'); _text(request['text'], 1200, '知识笔记正文')
                note_id = 'BNOTE-' + _digest({'draft_id': draft_id, 'candidate_id': request['candidate_id']})[:32]
                old = next((r for r in doc['notes'] if r['note_id'] == note_id), None)
                if old:
                    if old['title'] != request['title'] or old['text'] != request['text']:
                        fail('该候选已有内容不同的知识笔记，未覆盖旧内容；请在知识笔记中编辑。')
                    reused.append(note_id); continue
                now = utc_now()
                doc['notes'].append({'note_id': note_id, 'section_id': draft['section_id'], 'title': request['title'],
                    'text': request['text'], 'pdf_page': candidate['source_evidence']['pdf_pages'][0],
                    'teacher_confirmed': True, 'origin': 'teacher_adapted_candidate',
                    'source_evidence': deepcopy(candidate['source_evidence']), 'created_at': now, 'updated_at': now, **FLAGS})
                added.append(note_id)
            return bool(added)
        view = self.study._save(selection, study_revision, edit)
        return {'view': view, 'added': len(added), 'reused': len(reused), 'note_ids': added + reused, **FLAGS}

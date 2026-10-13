"""Synthetic source-bound candidates; atomic review, resume and backup."""
from copy import deepcopy
import io
import json
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from test_personal_textbook_study import book_fixture as book_fixture, chapter, note, selected
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import TextbookWorkspaceService, _digest
from integrations.deeptutor_shchem_v1.desktop_textbook_personal_study import PersonalTextbookStudyService, FLAGS
from integrations.deeptutor_shchem_v1.desktop_personal_textbook_knowledge import PersonalTextbookKnowledgeService, validate_drafts
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, read_cancel_scope, TaskCancellationEvent


def native_pdf(path, pages=3, *, long_line=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    font_id = writer._add_object(font)
    for page_number in range(1, pages + 1):
        page = writer.add_blank_page(600, 840)
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font_id})})
        stream = DecodedStreamObject()
        line = long_line if long_line is not None else f'A -> B; condition X; mass {page_number}.0 g.'
        stream.set_data((f'q 0.18 0.66 0.96 rg 35 595 530 110 re f Q '
            f'BT /F1 16 Tf 60 675 Td (Synthetic knowledge {page_number}) Tj 0 -30 Td ({line}) Tj ET').encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return path


@pytest.fixture
def native_fixture(tmp_path):
    paths = DesktopPaths.from_workspace(tmp_path / 'workspace', state_root=tmp_path / 'state')
    state = DesktopStateStore(paths.state_root); workspace = TextbookWorkspaceService(paths, state)
    source = native_pdf(tmp_path / 'original' / '合成流程教材.pdf')
    original = source.read_bytes(); workspace.commit_books(workspace.preview_books([source]))
    study = PersonalTextbookStudyService(workspace); book = study.books()[0]
    return SimpleNamespace(paths=paths, state=state, workspace=workspace, study=study,
        source=source, original=original, selection={k: book[k] for k in ('source_id', 'revision')})


def decoded_items():
    return {'items': [
        {'title': '合成知识甲', 'text': 'A → B（条件X）；用量 2.0 g。', 'pdf_pages': [2],
         'source_quote': '合成原页依据甲，须教师核对。', 'limitation': '合成流程测试材料；不代表真实化学事实。'},
        {'title': '合成知识乙', 'text': '保留条件Y、单位与来源。', 'pdf_pages': [2, 3],
         'source_quote': '合成原页依据乙，须教师核对。', 'limitation': '所选两页共同依据；未读其他页。'}]}


def draft_for(f, *, view=None, save=True):
    view = view or chapter(f)
    service = PersonalTextbookKnowledgeService(f.workspace)
    section = view['sections'][0]
    prepared = service.prepare(f.selection, view['study_revision'], section['section_id'], 2, 3)
    draft = service.create(prepared, decoded_items(), 'model_visual')
    if save: draft = service.save_draft(f.selection, view['study_revision'], draft)
    return service, view, draft


def choose(draft):
    return [{k: row[k] for k in ('candidate_id', 'title', 'text')} | {'confirmed': True} for row in draft['items']]


def test_native_preview_reads_exact_pages_without_writing_or_truncation(native_fixture):
    f = native_fixture; view = chapter(f)
    before = f.state.path.read_bytes()
    service = PersonalTextbookKnowledgeService(f.workspace)
    draft = service.preview_native(f.selection, view['study_revision'], view['sections'][0]['section_id'], 2, 3)
    assert [r['source_evidence']['pdf_pages'] for r in draft['items']] == [[2], [3]]
    assert 'mass 2.0 g.' in draft['items'][0]['text'] and 'mass 3.0 g.' in draft['items'][1]['text']
    assert 'mass 1.0 g.' not in json.dumps(draft)
    assert all(r['text'] == r['source_evidence']['source_quote'] for r in draft['items'])
    assert draft['review_edits'] == [] and draft['method'] == 'pdf_native_text'
    assert f.state.path.read_bytes() == before and f.source.read_bytes() == f.original
    assert all(draft[key] is value for key, value in FLAGS.items())


def test_scanned_pages_require_visual_or_manual_and_do_not_write(book_fixture):
    f = book_fixture; view = chapter(f); before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError, match='没有可读的原生文字'):
        PersonalTextbookKnowledgeService(f.workspace).preview_native(
            f.selection, view['study_revision'], view['sections'][0]['section_id'], 2, 3)
    assert f.state.path.read_bytes() == before


def test_native_long_line_fails_whole_batch(tmp_path):
    paths = DesktopPaths.from_workspace(tmp_path / 'workspace', state_root=tmp_path / 'state')
    state = DesktopStateStore(paths.state_root); workspace = TextbookWorkspaceService(paths, state)
    source = native_pdf(tmp_path / 'long.pdf', pages=1, long_line='X' * 1201)
    workspace.commit_books(workspace.preview_books([source]))
    study = PersonalTextbookStudyService(workspace); book = study.books()[0]
    selection = {k: book[k] for k in ('source_id', 'revision')}
    view = study.save_section(selection, study.book_study(selection)['study_revision'], title='长行合成章',
        pdf_start=1, pdf_end=1, range_confirmed=True)
    before = state.path.read_bytes()
    with pytest.raises(PreparationSourceError, match='超过1200字'):
        PersonalTextbookKnowledgeService(workspace).preview_native(selection, view['study_revision'], view['sections'][0]['section_id'], 1, 1)
    assert state.path.read_bytes() == before


def test_eight_selected_pages_are_complete_and_ninth_page_is_rejected(tmp_path):
    paths = DesktopPaths.from_workspace(tmp_path / 'workspace', state_root=tmp_path / 'state')
    state = DesktopStateStore(paths.state_root); workspace = TextbookWorkspaceService(paths, state)
    source = native_pdf(tmp_path / 'ten-pages.pdf', pages=10)
    workspace.commit_books(workspace.preview_books([source]))
    study = PersonalTextbookStudyService(workspace); book = study.books()[0]
    selection = {k: book[k] for k in ('source_id', 'revision')}
    view = study.save_section(selection, study.book_study(selection)['study_revision'], title='十页合成章',
        pdf_start=1, pdf_end=10, range_confirmed=True)
    before = state.path.read_bytes(); service = PersonalTextbookKnowledgeService(workspace)
    draft = service.preview_native(selection, view['study_revision'], view['sections'][0]['section_id'], 2, 9)
    assert draft['pdf_pages'] == list(range(2, 10)) and len(draft['items']) == 8
    with pytest.raises(PreparationSourceError, match='1至8'):
        service.preview_native(selection, view['study_revision'], view['sections'][0]['section_id'], 1, 9)
    assert state.path.read_bytes() == before


@pytest.mark.parametrize('start,end', [(1, 3), (2, 4), (3, 2), (True, 3), (2, 2.5)])
def test_scope_errors_leave_state_intact(book_fixture, start, end):
    f = book_fixture; view = chapter(f); before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError):
        PersonalTextbookKnowledgeService(f.workspace).prepare(
            f.selection, view['study_revision'], view['sections'][0]['section_id'], start, end)
    assert f.state.path.read_bytes() == before


@pytest.mark.parametrize('change', ['out_of_scope', 'duplicate_page', 'extra', 'empty', 'too_long', 'boolean_page'])
def test_untrusted_model_items_cannot_save(book_fixture, change):
    f = book_fixture; service, view, draft = draft_for(f, save=False)
    prepared = service.prepare(f.selection, view['study_revision'], view['sections'][0]['section_id'], 2, 3)
    decoded = decoded_items()
    if change == 'out_of_scope': decoded['items'][0]['pdf_pages'] = [1]
    if change == 'duplicate_page': decoded['items'][0]['pdf_pages'] = [2, 2]
    if change == 'extra': decoded['items'][0]['official'] = True
    if change == 'empty': decoded['items'] = []
    if change == 'too_long': decoded['items'][0]['text'] = '字' * 1201
    if change == 'boolean_page': decoded['items'][0]['pdf_pages'] = [True]
    before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError): service.create(prepared, decoded, 'model_visual')
    assert f.state.path.read_bytes() == before and service.drafts(f.selection, draft['section_id']) == []


def test_candidate_save_is_durable_and_repeat_does_not_write(book_fixture):
    f = book_fixture; service, view, draft = draft_for(f)
    assert service.drafts(f.selection, draft['section_id']) == [draft]
    assert f.study.book_study(f.selection) == view
    before = f.state.path.read_bytes()
    assert service.save_draft(f.selection, view['study_revision'], draft) == draft
    assert f.state.path.read_bytes() == before


def test_resume_keeps_edits_but_no_confirmations_and_old_draft_cannot_adopt(book_fixture):
    f = book_fixture; service, view, draft = draft_for(f)
    edits = [{k: r[k] for k in ('candidate_id', 'title', 'text')} for r in draft['items']]
    edits[0]['text'] = '教师修订：A → B（条件X）；用量 2.0 g。'
    resumed = service.save_progress(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], edits)
    assert resumed['review_edits'] == edits and resumed['items'] == draft['items'] and resumed['revision'] != draft['revision']
    assert all('confirmed' not in r for r in resumed['review_edits'])
    before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError, match='已变化'):
        service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], choose(draft))
    assert service.save_progress(f.selection, view['study_revision'], resumed['draft_id'], resumed['revision'], edits) == resumed
    assert f.state.path.read_bytes() == before
    assert service.save_draft(f.selection, view['study_revision'], draft) == resumed


@pytest.mark.parametrize('change', ['unconfirmed', 'unknown', 'invalid_second', 'duplicate'])
def test_adoption_batch_is_atomic_and_requires_per_item_check(book_fixture, change):
    f = book_fixture; service, view, draft = draft_for(f); chosen = choose(draft)
    if change == 'unconfirmed': chosen[1]['confirmed'] = False
    if change == 'unknown': chosen[1]['candidate_id'] = 'KITEM-' + '0' * 32
    if change == 'invalid_second': chosen[1]['text'] = ' '
    if change == 'duplicate': chosen.append(deepcopy(chosen[0]))
    before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError):
        service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], chosen)
    assert f.state.path.read_bytes() == before and f.study.book_study(f.selection)['notes'] == []


def test_adopt_edit_reference_repeat_preserve_evidence_and_closed_gates(book_fixture):
    f = book_fixture; service, view, draft = draft_for(f)
    chosen = choose(draft); chosen[0]['text'] = 'A → B（教师修订条件X）；用量 2.0 g。'
    adopted = service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], chosen)
    view = adopted['view']; assert (adopted['added'], adopted['reused']) == (2, 0)
    before = f.state.path.read_bytes()
    same = service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], chosen)
    assert (same['added'], same['reused']) == (0, 2) and f.state.path.read_bytes() == before
    for row, candidate in zip(view['notes'], draft['items'], strict=True):
        assert row['origin'] == 'teacher_adapted_candidate' and row['teacher_confirmed'] is True
        assert row['source_evidence'] == candidate['source_evidence']
        assert all(row[key] is value for key, value in FLAGS.items())
    first = view['notes'][0]
    edited = f.study.save_note(f.selection, view['study_revision'], **{k: first[k] for k in
        ('section_id', 'note_id', 'title', 'pdf_page', 'teacher_confirmed')}, text='二次教师修订，条件X；2.0 g。')
    assert edited['notes'][0]['source_evidence'] == first['source_evidence']
    reference = f.study.reference(f.selection, edited['study_revision'], [selected(edited)[0], selected(edited, 1)[0]])
    assert '二次教师修订' in reference['materials'] and '完整依据PDF页：[2, 3]' in reference['materials']
    assert '候选原页依据（待核对）' in reference['materials'] and 'AI改述' in reference['warnings'][0]
    before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError, match='内容不同'):
        service.adopt(f.selection, edited['study_revision'], draft['draft_id'], draft['revision'], chosen)
    assert f.state.path.read_bytes() == before


def test_all_evidence_pages_block_chapter_shrink_and_source_move(book_fixture):
    f = book_fixture; service, view, draft = draft_for(f)
    view = service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], choose(draft))['view']
    before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError):
        f.study.save_section(f.selection, view['study_revision'], section_id=draft['section_id'],
            title='缩小章', pdf_start=2, pdf_end=2, range_confirmed=True)
    row = view['notes'][1]
    with pytest.raises(PreparationSourceError, match='原页保持来源绑定'):
        f.study.save_note(f.selection, view['study_revision'], **{k: row[k] for k in
            ('section_id', 'note_id', 'title', 'text', 'teacher_confirmed')}, pdf_page=3)
    assert f.state.path.read_bytes() == before


def test_changed_chapter_study_and_archive_each_block_adoption(book_fixture):
    f = book_fixture; service, view, draft = draft_for(f)
    current = note(f, view)
    with pytest.raises(PreparationSourceError, match='已变化'):
        service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], choose(draft))
    current = f.study.save_section(f.selection, current['study_revision'], section_id=draft['section_id'],
        title='改变的章名', pdf_start=2, pdf_end=3, range_confirmed=True)
    before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError, match='章节范围已变化'):
        service.adopt(f.selection, current['study_revision'], draft['draft_id'], draft['revision'], choose(draft))
    assert f.state.path.read_bytes() == before
    path = f.workspace.root / (f.selection['revision'] + '.pdf'); path.write_bytes(b'corrupt')
    with pytest.raises(PreparationSourceError):
        service.save_progress(f.selection, current['study_revision'], draft['draft_id'], draft['revision'], [])
    assert f.state.path.read_bytes() == before


@pytest.mark.parametrize('change', ['content', 'rights', 'evidence', 'progress_confirmation', 'unsorted'])
def test_rehashed_backup_payload_cannot_forge_candidates(book_fixture, change):
    f = book_fixture; service, view, draft = draft_for(f); changed = deepcopy(draft)
    if change == 'content': changed['items'][0]['text'] = '伪造内容'
    if change == 'rights': changed['generation_allowed'] = True
    if change == 'evidence': changed['items'][0]['source_evidence']['source_sha256'] = '0' * 64
    if change == 'progress_confirmation': changed['review_edits'] = [choose(draft)[0]]
    if change == 'unsorted': changed['items'][1]['source_evidence']['pdf_pages'] = [3, 2]
    changed['revision'] = _digest({k: v for k, v in changed.items() if k != 'revision'})
    before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError): validate_drafts({'books': [view['book']], 'knowledge_drafts': [changed]})
    assert f.state.path.read_bytes() == before


def test_cancel_before_publication_leaves_entire_candidate_batch_intact(book_fixture):
    f = book_fixture; service, view, draft = draft_for(f, save=False)
    event = TaskCancellationEvent(); event.set(); before = f.state.path.read_bytes()
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        service.save_draft(f.selection, view['study_revision'], draft)
    assert f.state.path.read_bytes() == before
    draft = service.save_draft(f.selection, view['study_revision'], draft); before = f.state.path.read_bytes()
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], choose(draft))
    assert f.state.path.read_bytes() == before


def test_cancel_between_items_rolls_back_first_pending_note(book_fixture, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_personal_textbook_knowledge as module
    f = book_fixture; service, view, draft = draft_for(f); event = TaskCancellationEvent()
    before = f.state.path.read_bytes(); real_text = module._text; calls = []
    def text(value, limit, label):
        result = real_text(value, limit, label); calls.append(label)
        if label == '知识笔记正文': event.set()
        return result
    monkeypatch.setattr(module, '_text', text)
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], choose(draft))
    assert '知识笔记正文' in calls and f.state.path.read_bytes() == before
    assert f.study.book_study(f.selection)['notes'] == []


def test_candidate_resume_and_adopted_notes_roundtrip_in_book_backup(book_fixture, tmp_path):
    f = book_fixture; service, view, draft = draft_for(f)
    edits = [{k: r[k] for k in ('candidate_id', 'title', 'text')} for r in draft['items']]
    edits[0]['title'] = '保留的教师修订名称'
    draft = service.save_progress(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], edits)
    chosen = choose(draft); chosen[0]['title'] = edits[0]['title']
    view = service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], chosen)['view']
    bundle = tmp_path / 'candidate-books.zip'; f.workspace.export_bundle(bundle)
    restored_root = tmp_path / 'restored'; f.workspace.restore_bundle(bundle, restored_root)
    paths = DesktopPaths.from_workspace(tmp_path / 'another-workspace', state_root=restored_root)
    restored = PersonalTextbookKnowledgeService(TextbookWorkspaceService(paths, DesktopStateStore(restored_root)))
    assert restored.drafts(f.selection, draft['section_id']) == [draft]
    assert restored.study.book_study(f.selection) == view
    assert restored.workspace.read_book(f.selection['source_id'], f.selection['revision'])['pdf_bytes'] == f.original


@pytest.mark.parametrize('change', ['forged_note_evidence', 'missing_candidate'])
def test_note_backup_cannot_detach_or_replace_original_candidate_evidence(book_fixture, change):
    f = book_fixture; service, view, draft = draft_for(f)
    service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], choose(draft))
    state = json.loads(f.state.path.read_text(encoding='utf-8'))
    if change == 'forged_note_evidence':
        state['textbook_workspace']['personal_study']['notes'][0]['source_evidence']['source_quote'] = '伪造的原页依据'
    else:
        state['textbook_workspace']['knowledge_drafts'] = []
    f.state.path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError, match='原始候选依据不一致'):
        f.study.book_study(f.selection)
    assert f.state.path.read_bytes() == before and f.source.read_bytes() == f.original

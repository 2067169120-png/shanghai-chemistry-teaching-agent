"""Real native PDFs, saved/scanned-book study and immutable search boundaries."""
from copy import deepcopy
from hashlib import sha256
from threading import Event
from types import SimpleNamespace
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from test_personal_textbook_study import book_fixture, chapter, note
from test_personal_textbook_knowledge import native_fixture, draft_for, choose
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import TextbookWorkspaceService
from integrations.deeptutor_shchem_v1.desktop_textbook_personal_study import PersonalTextbookStudyService
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError
from integrations.deeptutor_shchem_v1.desktop_personal_textbook_search import PersonalTextbookSearchService, normalized, _match
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, read_cancel_scope


def text_pdf(path, texts):
    path.parent.mkdir(parents=True,exist_ok=True); writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    for text in texts:
        page = writer.add_blank_page(600,840)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream = DecodedStreamObject()
        escaped = text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        stream.set_data(('q 0.2 0.6 0.8 rg 30 400 500 80 re f Q '+
            (f'BT /F1 16 Tf 60 675 Td ({escaped}) Tj ET' if text else '')).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path); return path


def make_book(tmp_path, texts):
    paths = DesktopPaths.from_workspace(tmp_path/'workspace',state_root=tmp_path/'state')
    state = DesktopStateStore(paths.state_root); workspace = TextbookWorkspaceService(paths,state)
    source = text_pdf(tmp_path/'original'/'原生文字教材.pdf',texts); original = source.read_bytes()
    workspace.commit_books(workspace.preview_books([source])); study = PersonalTextbookStudyService(workspace)
    book = study.books()[0]; facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    facade.paths,facade._state = paths,state
    return SimpleNamespace(paths=paths,state=state,workspace=workspace,study=study,source=source,original=original,
        selection={k:book[k] for k in ('source_id','revision')},facade=facade)


def frozen_files(f):
    return {str(p): (sha256(p.read_bytes()).hexdigest(),p.stat().st_size,p.stat().st_mtime_ns)
        for root in (f.paths.state_root,f.source.parent) for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def text_book(tmp_path):
    texts = ['', 'scanned-looking page with native label', 'H2SO4 and H+; Ka; condition X', '']
    texts += [f'Native chapter page {i}' for i in range(5,13)]
    texts += ['EDGE ; EDGE at page thirteen','EDGE at the last page']
    return make_book(tmp_path,texts)


def search(f, query, **options):
    return f.facade.personal_textbook_search(f.selection,query,**options)


def test_whole_book_includes_pages_after_eight_and_exact_original_excerpt(text_book):
    f = text_book; view = chapter(f,title='后半部',start=9,end=14); before = frozen_files(f)
    progress = []; result = search(f,'EDGE',progress_callback=progress.append)
    assert result['total'] == 2 and result['total_occurrences'] == 3
    assert [r['pdf_pages'] for r in result['items']] == [[13],[14]]
    assert result['pdf_pages'] == list(range(1,15)) and len(progress) == 14 and progress[-1]['pdf_page'] == 14
    assert result['native_text_pages'] == 12 and result['no_native_text_pages'] == 2 and not result['unreadable_pages']
    row = result['items'][0]; assert row['excerpt'] == 'EDGE ; EDGE at page thirteen'
    assert row['kind'] == 'original_text' and row['printed_pages'] is None and row['chapters'][0]['title'] == '后半部'
    assert result['source']['revision'] == sha256(f.original).hexdigest()
    assert frozen_files(f) == before


def test_current_saved_chapter_scope_does_not_silently_expand(text_book):
    f = text_book; view = chapter(f,title='之前的原生段',start=9,end=12); section = view['sections'][0]
    result = search(f,'EDGE',section_id=section['section_id'],study_revision=view['study_revision'])
    assert result['total'] == 0 and result['pdf_pages'] == [9,10,11,12]
    assert result['section_title'] == '之前的原生段' and result['native_text_pages'] == 4
    with pytest.raises(PreparationSourceError,match='章节'):
        search(f,'EDGE',section_id=section['section_id'],study_revision='a'*64)


def test_chemical_unicode_matching_keeps_original_pdf_and_note_characters(text_book):
    f = text_book; before = frozen_files(f)
    result = search(f,'H₂SO₄'); assert [r['pdf_pages'] for r in result['items']] == [[3]]
    assert 'H2SO4' in result['items'][0]['excerpt'] and 'H₂SO₄' not in result['items'][0]['excerpt']
    assert search(f,'H⁺')['total'] == 1 and normalized('H₂O + Cl⁻') == normalized('H2O+Cl-')
    raw = '原文：Kₐ、c(H⁺)、OH⁻； e\u0301。'
    match = _match(raw,normalized('c(H+)')); assert raw[match['excerpt_start']:match['excerpt_end']] == match['excerpt']
    assert 'c(H⁺)' in match['excerpt'] and _match(raw,normalized('é')) is not None
    assert frozen_files(f) == before


def test_chemical_formula_case_remains_distinct_in_original_and_saved_notes(tmp_path):
    f = make_book(tmp_path, ['CO gas', 'Co metal', 'NO gas', 'No symbol'])
    view = chapter(f, title='化学式与元素符号', start=1, end=4)
    view = note(f, view, title='CO气体', text='CO与氧化还原。', page=1)
    view = note(f, view, title='Co元素', text='Co元素符号。', page=2)
    before = frozen_files(f)
    for query, page, title in [('CO', 1, 'CO气体'), ('Co', 2, 'Co元素')]:
        result = search(f, query)
        assert [r['pdf_pages'] for r in result['items']] == [[page], [page]]
        assert result['items'][0]['excerpt'] == ('CO gas' if page == 1 else 'Co metal')
        assert result['items'][1]['title'] == title
    assert [r['pdf_pages'] for r in search(f, 'NO')['items']] == [[3]]
    assert [r['pdf_pages'] for r in search(f, 'No')['items']] == [[4]]
    assert normalized('CO') != normalized('Co') and frozen_files(f) == before


def test_scan_book_searches_saved_directory_and_notes_as_separate_kinds(book_fixture):
    f = book_fixture; view = chapter(f,title='氢键与分子间作用力')
    view = note(f,view,title='氢键比较',text='比较氢键形成条件与原图。',page=2)
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade); facade.paths,facade._state = f.paths,f.state
    before = frozen_files(f); result = facade.personal_textbook_search(f.selection,'氢键')
    assert result['native_text_pages'] == 0 and result['no_native_text_pages'] == 4
    assert result['counts'] == dict(original_text=0,section=1,knowledge_note=1)
    assert {r['kind'] for r in result['items']} == {'section','knowledge_note'}
    row = result['items'][1]; assert row['note_text'] == view['notes'][0]['text']
    assert row['pdf_pages'] == [2] and row['note_revision'] == view['notes'][0]['revision']
    assert frozen_files(f) == before


def test_saved_candidate_note_keeps_all_evidence_pages_and_does_not_search_source_quote(native_fixture):
    f = native_fixture; service,view,draft = draft_for(f)
    view = service.adopt(f.selection,view['study_revision'],draft['draft_id'],draft['revision'],choose(draft))['view']
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade); facade.paths,facade._state=f.paths,f.state
    before = frozen_files(f)
    result = facade.personal_textbook_search(f.selection,'条件Y',content='organized')
    assert result['native_searched'] is False and result['total'] == 1
    assert result['items'][0]['pdf_pages'] == [2,3] and result['items'][0]['kind'] == 'knowledge_note'
    assert facade.personal_textbook_search(f.selection,'合成原页依据乙',content='organized')['total'] == 0
    assert frozen_files(f) == before and all(not n['human_reviewed'] for n in view['notes'])


def test_results_have_explicit_pagination_and_stable_search_identity(tmp_path):
    f = make_book(tmp_path,[f'EDGE {i}' for i in range(31)]); before = frozen_files(f)
    first = search(f,'EDGE'); second = search(f,'EDGE',offset=25,expected=first['search_identity'])
    assert first['total'] == 31 and first['has_more'] and len(first['items']) == 25
    assert second['total'] == 31 and not second['has_more'] and len(second['items']) == 6
    assert second['items'][-1]['pdf_pages'] == [31] and second['search_identity'] == first['search_identity']
    assert frozen_files(f) == before


@pytest.mark.parametrize('query', ['', '   ', 'x'*161, None])
def test_empty_or_long_query_does_not_read_a_book(text_book,query):
    f = text_book; f.facade.personal_textbook_source = lambda *a,**kw:pytest.fail('invalid query must not read source')
    with pytest.raises(PreparationSourceError,match='1—160'): search(f,query)


def test_no_match_reports_entire_processed_scope(text_book):
    result = search(text_book,'no-such-material')
    assert result['total'] == 0 and result['pdf_pages'] == list(range(1,15))
    assert result['counts'] == dict.fromkeys(('original_text','section','knowledge_note'),0)


@pytest.mark.parametrize('change', ['bytes','source_id','revision','page_count','pdf_pages'])
def test_even_custom_reader_results_must_match_verified_book_identity(text_book,change):
    f = text_book; reader = f.facade.personal_textbook_source
    def bad(*a,**kw):
        result = reader(*a,**kw)
        if change == 'bytes': result['pdf_bytes'] += b'changed'
        elif change == 'pdf_pages': result['pdf_pages'] = [1,2]
        elif change == 'page_count': result['page_count'] += 1
        else: result[change] = 'wrong'
        return result
    f.facade.personal_textbook_source=bad; before=frozen_files(f)
    with pytest.raises(PreparationSourceError): search(f,'EDGE')
    assert frozen_files(f) == before


def test_archive_changed_between_search_reads_is_rejected_without_state_rewrite(text_book):
    f=text_book; state_before=f.state.path.read_bytes(); archive=f.workspace.root/(f.selection['revision']+'.pdf')
    def alter(progress):
        if progress['completed']==14: archive.write_bytes(f.original+b'changed')
    with pytest.raises(PreparationSourceError,match='归档'): search(f,'EDGE',progress_callback=alter)
    assert f.state.path.read_bytes()==state_before and f.source.read_bytes()==f.original


def test_saved_study_change_during_search_discards_results(text_book):
    f=text_book; original=f.source.read_bytes()
    def alter(progress):
        if progress['completed']==14: chapter(f,title='新保存章节',start=9,end=14)
    with pytest.raises(PreparationSourceError,match='章节或知识笔记'): search(f,'EDGE',progress_callback=alter)
    assert len(f.study.book_study(f.selection)['sections'])==1 and f.source.read_bytes()==original


@pytest.mark.parametrize('late',[False,True])
def test_cancel_does_not_return_partial_results_or_write_index(text_book,late):
    f=text_book; event=Event(); before=frozen_files(f)
    if not late:event.set()
    def progress(value):
        if value['completed']==9:event.set()
    with read_cancel_scope(event,check_boundaries=False),pytest.raises(ReadCancelled):
        search(f,'EDGE',progress_callback=progress if late else None)
    assert frozen_files(f)==before


def test_page_extraction_errors_are_explicit_and_other_pages_remain_searchable(text_book,monkeypatch):
    from pypdf._page import PageObject
    original=PageObject.extract_text
    def text(page,*args,**kw):
        value=original(page,*args,**kw)
        if 'page 8' in value: raise ValueError('synthetic extraction error')
        return value
    monkeypatch.setattr(PageObject,'extract_text',text)
    result=search(text_book,'EDGE')
    assert result['total']==2 and result['unreadable_pages']==[8]
    assert result['native_text_pages']+result['no_native_text_pages']+len(result['unreadable_pages'])==14


def test_expired_identity_cannot_be_used_for_paging_or_location(text_book):
    f=text_book; result=search(f,'EDGE'); view=chapter(f,title='新增已保存知识范围',start=9,end=14)
    with pytest.raises(PreparationSourceError,match='结果依据'):
        search(f,'EDGE',expected=result['search_identity'])
    with pytest.raises(PreparationSourceError,match='章节或知识笔记'):
        search(f,'EDGE',study_revision=result['study_revision'],expected=result['search_identity'])

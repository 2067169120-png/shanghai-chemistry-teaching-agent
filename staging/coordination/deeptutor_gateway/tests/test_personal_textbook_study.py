"""Original-byte intake and teacher chapter/note integrity, with real PDFs."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError
from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import TextbookWorkspaceService, SCHEMA
from integrations.deeptutor_shchem_v1.desktop_textbook_personal_study import PersonalTextbookStudyService, FLAGS


def pdf(path, pages=4):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(600, 840)
        stream = DecodedStreamObject()
        stream.set_data(f"q 0.12 {0.35 + index * .08} 0.85 rg 60 340 480 220 re f Q".encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return path


@pytest.fixture
def book_fixture(tmp_path):
    root, state_root = tmp_path / 'workspace', tmp_path / 'state'
    paths = DesktopPaths.from_workspace(root, state_root=state_root)
    state = DesktopStateStore(state_root)
    workspace = TextbookWorkspaceService(paths, state)
    source = pdf(tmp_path / 'original' / '合成教材.pdf')
    original = source.read_bytes()
    workspace.commit_books(workspace.preview_books([source]))
    study = PersonalTextbookStudyService(workspace)
    book = study.books()[0]
    selection = {key: book[key] for key in ('source_id', 'revision')}
    return SimpleNamespace(root=root, state_root=state_root, paths=paths, state=state,
        workspace=workspace, study=study, source=source, original=original, selection=selection)


def chapter(f, *, title='合成章一', start=2, end=3):
    view = f.study.book_study(f.selection)
    return f.study.save_section(f.selection, view['study_revision'], title=title,
        pdf_start=start, pdf_end=end, range_confirmed=True)


def note(f, view, *, title='合成笔记', text='A → B（条件X）；用量 2.0 g。', page=2):
    return f.study.save_note(f.selection, view['study_revision'], section_id=view['sections'][-1]['section_id'],
        title=title, text=text, pdf_page=page, teacher_confirmed=True)


def selected(view, index=0):
    return [{key: view['notes'][index][key] for key in ('note_id', 'revision')}]


def test_preview_read_only_and_whole_pdf_duplicate_same_name_new_bytes(tmp_path):
    paths = DesktopPaths.from_workspace(tmp_path / 'root', state_root=tmp_path / 'state')
    state = DesktopStateStore(paths.state_root)
    service = TextbookWorkspaceService(paths, state)
    first, second = pdf(tmp_path / 'a' / 'book.pdf', 2), pdf(tmp_path / 'b' / 'book.pdf', 3)
    before = first.read_bytes()
    preview = service.preview_books([first, first, second])
    assert not paths.state_root.exists() and [r['duplicate'] for r in preview['sources']] == [False, True, False]
    saved = service.commit_books(preview)
    assert (saved['added'], saved['reused']) == (2, 1)
    assert first.read_bytes() == before and len(service.books()) == 2
    assert service.commit_books(service.preview_books([first]))['reused'] == 1
    for book in service.books():
        assert book['source_id'] == 'BOOK-' + book['source_sha256']
        assert book['human_reviewed'] is False and book['year'] is None and book['source_url'] is None
        assert service.read_book(book['source_id'], book['source_sha256'])['page_count'] == book['page_count']


def test_source_change_after_preview_and_archive_corruption_preserve_old(book_fixture):
    f = book_fixture
    preview = f.workspace.preview_books([f.source]);pdf(f.source, 2)
    with pytest.raises(PreparationSourceError, match='发生变化'):
        f.workspace.commit_books(preview)
    assert len(f.workspace.books()) == 1
    path = f.workspace.root / (f.selection['revision'] + '.pdf')
    path.write_bytes(b'changed')
    with pytest.raises(PreparationSourceError):
        f.study.read(f.selection)
    f.source.write_bytes(f.original)
    with pytest.raises(PreparationSourceError, match='未覆盖'):
        f.workspace.commit_books(f.workspace.preview_books([f.source]))
    assert path.read_bytes() == b'changed'


def test_partial_cancel_continues_and_keeps_already_saved(tmp_path):
    paths = DesktopPaths.from_workspace(tmp_path / 'root', state_root=tmp_path / 'state')
    service = TextbookWorkspaceService(paths, DesktopStateStore(paths.state_root))
    files = [pdf(tmp_path / f'{i}.pdf', i + 1) for i in range(3)]
    progress = []
    result = service.commit_books(service.preview_books(files), progress_callback=progress.append,
                                 should_cancel=lambda: bool(progress))
    assert result['added'] == 1 and len(service.books()) == 1
    result = service.commit_books(service.preview_books(files))
    assert (result['added'], result['reused']) == (2, 1)


def test_teacher_chapter_note_reference_preserve_source_and_closed_gates(book_fixture):
    f = book_fixture;before = f.state.snapshot()['textbook_workspace']['books']
    view = note(f, chapter(f))
    assert f.study.read(f.selection, section_id=view['sections'][0]['section_id'], study_revision=view['study_revision'])['pdf_pages'] == [2, 3]
    assert f.study.read(f.selection)['pdf_pages'] == [1, 2, 3, 4]
    reference = f.study.reference(f.selection, view['study_revision'], selected(view))
    for text in ('A → B（条件X）；用量 2.0 g。', f.selection['revision'], 'PDF第2页', '印刷页码：待核对', '教师手工输入'):
        assert text in reference['materials']
    for row in (*view['sections'], *view['notes']):
        assert all(row[key] is value for key, value in FLAGS.items())
    assert view['sections'][0]['printed_pages'] is None
    assert f.source.read_bytes() == f.original
    assert f.state.snapshot()['textbook_workspace']['books'] == before
    assert f.study.books()[0]['section_count'] == f.study.books()[0]['note_count'] == 1
    assert not list(f.root.glob('**/concepts.jsonl'))


@pytest.mark.parametrize('bad', [dict(pdf_start=True), dict(pdf_end=5), dict(pdf_start=3, pdf_end=2),
                                   dict(range_confirmed=False), dict(title='')])
def test_invalid_section_never_writes(book_fixture, bad):
    f = book_fixture;before = f.state.path.read_bytes()
    fields = dict(title='合成章', pdf_start=2, pdf_end=3, range_confirmed=True);fields.update(bad)
    with pytest.raises(PreparationSourceError):
        f.study.save_section(f.selection, f.study.book_study(f.selection)['study_revision'], **fields)
    assert f.state.path.read_bytes() == before


@pytest.mark.parametrize('bad', [dict(pdf_page=True), dict(pdf_page=1), dict(teacher_confirmed=False),
                                   dict(text='x' * 1201), dict(title='')])
def test_invalid_note_never_writes(book_fixture, bad):
    f = book_fixture;view = chapter(f);before = f.state.path.read_bytes()
    fields = dict(section_id=view['sections'][0]['section_id'], title='合成笔记', text='内容', pdf_page=2, teacher_confirmed=True)
    fields.update(bad)
    with pytest.raises(PreparationSourceError):
        f.study.save_note(f.selection, view['study_revision'], **fields)
    assert f.state.path.read_bytes() == before


def test_repeated_save_is_no_op_and_editing_keeps_ids(book_fixture):
    f = book_fixture;view = note(f, chapter(f));before = f.state.path.read_bytes()
    row = view['notes'][0]
    same = f.study.save_note(f.selection, view['study_revision'], **{key: row[key] for key in
        ('section_id', 'title', 'text', 'pdf_page', 'teacher_confirmed', 'note_id')})
    assert same == view and f.state.path.read_bytes() == before
    changed = f.study.save_note(f.selection, view['study_revision'], **{key: row[key] for key in
        ('section_id', 'title', 'pdf_page', 'teacher_confirmed', 'note_id')}, text='新条件 Y，3 g。')
    assert changed['notes'][0]['note_id'] == row['note_id'] and changed['notes'][0]['revision'] != row['revision']


def test_stale_save_and_reference_are_rejected_without_overwrite(book_fixture):
    f = book_fixture;old = chapter(f);new = note(f, old);before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError, match='保存前变化'):
        f.study.save_section(f.selection, old['study_revision'], title='旧覆盖', pdf_start=1, pdf_end=4, range_confirmed=True)
    with pytest.raises(PreparationSourceError, match='已变化'):
        f.study.reference(f.selection, old['study_revision'], selected(new))
    assert f.state.path.read_bytes() == before


def test_shrinking_chapter_cannot_strand_existing_note(book_fixture):
    f = book_fixture;view = note(f, chapter(f));before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError, match='排除已有笔记'):
        f.study.save_section(f.selection, view['study_revision'], section_id=view['sections'][0]['section_id'],
            title='旧章节', pdf_start=3, pdf_end=3, range_confirmed=True)
    assert f.state.path.read_bytes() == before


def test_notes_from_other_book_or_chapter_cannot_be_rebound(book_fixture):
    f = book_fixture;one = note(f, chapter(f))
    two = chapter(f, title='合成章二', start=4, end=4)
    with pytest.raises(PreparationSourceError, match='不属于当前章节'):
        f.study.save_note(f.selection, two['study_revision'], section_id=two['sections'][-1]['section_id'],
            note_id=one['notes'][0]['note_id'], title='错配', text='内容', pdf_page=4, teacher_confirmed=True)
    second = pdf(f.source.parent / '另一本.pdf', 3)
    f.workspace.commit_books(f.workspace.preview_books([second]))
    book = f.study.books()[1];selection = {key: book[key] for key in ('source_id', 'revision')}
    with pytest.raises(PreparationSourceError, match='不属于当前教材'):
        f.study.save_section(selection, f.study.book_study(selection)['study_revision'],
            section_id=one['sections'][0]['section_id'], title='错配', pdf_start=1, pdf_end=2, range_confirmed=True)


@pytest.mark.parametrize('change', ['duplicate', 'old_revision', 'unknown', 'extra'])
def test_reference_exact_selection_only(book_fixture, change):
    f = book_fixture;view = note(f, chapter(f));chosen = selected(view)
    if change == 'duplicate':chosen += deepcopy(chosen)
    if change == 'old_revision':chosen[0]['revision'] = '0' * 64
    if change == 'unknown':chosen[0]['note_id'] = 'BNOTE-' + '0' * 32
    if change == 'extra':chosen[0]['text'] = '注入'
    with pytest.raises(PreparationSourceError):
        f.study.reference(f.selection, view['study_revision'], chosen)


def test_corrupt_study_is_not_repaired_by_an_unrelated_book_import(book_fixture):
    f = book_fixture;view = note(f, chapter(f))
    state = json.loads(f.state.path.read_text(encoding='utf-8'))
    state['textbook_workspace']['personal_study']['notes'][0]['generation_allowed'] = True
    f.state.path.write_text(json.dumps(state), encoding='utf-8');before = f.state.path.read_bytes()
    with pytest.raises(PreparationSourceError, match='状态不完整'):
        f.study.books()
    with pytest.raises(PreparationSourceError):
        f.workspace.preview_books([f.source])
    assert f.state.path.read_bytes() == before and view['notes'][0]['generation_allowed'] is False


def test_legacy_books_need_no_candidate_file_or_migration(book_fixture):
    f = book_fixture;before = f.state.path.read_bytes()
    assert f.state.snapshot()['textbook_workspace']['schema'] == SCHEMA
    assert 'personal_study' not in f.state.snapshot()['textbook_workspace']
    assert len(f.study.books()) == 1 and f.study.book_study(f.selection)['notes'] == []
    assert f.state.path.read_bytes() == before


def test_textbook_backup_roundtrip_includes_whole_pdf_chapters_and_notes(book_fixture, tmp_path):
    f = book_fixture;view = note(f, chapter(f));bundle = tmp_path / 'books.zip'
    f.workspace.export_bundle(bundle)
    restored_root = tmp_path / 'restored'
    f.workspace.restore_bundle(bundle, restored_root)
    paths = DesktopPaths.from_workspace(tmp_path / 'other', state_root=restored_root)
    study = PersonalTextbookStudyService(TextbookWorkspaceService(paths, DesktopStateStore(restored_root)))
    assert study.book_study(f.selection) == view
    assert study.read(f.selection)['pdf_bytes'] == f.original
    with pytest.raises(FileExistsError):
        f.workspace.export_bundle(bundle)

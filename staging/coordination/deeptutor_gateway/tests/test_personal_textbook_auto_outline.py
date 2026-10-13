"""Real local PDF directory detection, durable candidates and conflict handling."""
import io
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_bookshelf import colored_pdf, replace_book
from test_textbook_section_reader import section_fixture as section_fixture

from integrations.deeptutor_shchem_v1 import desktop_textbook_auto_outline as outline
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    PreparationSourceError,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_textbook_personal_study import (
    FLAGS,
    PersonalTextbookStudyService,
)
from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import (
    TextbookWorkspaceService,
)
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled


def source_pdf(path, *, mode="bookmarks", same_page=False):
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(colored_pdf(6))))
    if mode == "bookmarks":
        first = writer.add_outline_item("第一章 合成学习目录", 1)
        writer.add_outline_item("1.1 合成物质分类", 1, parent=first)
        writer.add_outline_item("1.2 合成变化", 1 if same_page else 3, parent=first)
        second = writer.add_outline_item("第二章 合成实验目录", 4)
        writer.add_outline_item("2.1 合成观察", 4, parent=second)
    if mode == "headings":
        font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
        texts = {0: ['Contents', '1.1 Matter .... 1', '1.2 Change .... 4'],
                 1: ['1.1 Matter', 'Body example.'], 2: ['1.1 Matter', 'Repeated page header.'],
                 3: ['Prose mentions Contents without being a directory.'], 4: ['1.2 Change']}
        for index, lines in texts.items():
            page = writer.pages[index]
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                DictionaryObject({NameObject('/F1'): font})})
            stream = DecodedStreamObject()
            stream.set_data(('BT /F1 16 Tf 60 740 Td ' + ' '.join(f'({line}) Tj 0 -24 Td' for line in lines) + ' ET').encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer.write(path)
    return path


def fixture_for(tmp_path, *, mode="bookmarks", same_page=False, workspace_root=None):
    paths = DesktopPaths.from_workspace(workspace_root or tmp_path / 'workspace', state_root=tmp_path / 'state')
    state = DesktopStateStore(paths.state_root)
    workspace = TextbookWorkspaceService(paths, state)
    source = source_pdf(tmp_path / 'original' / '合成自动目录教材.pdf', mode=mode, same_page=same_page)
    workspace.commit_books(workspace.preview_books([source]))
    study = PersonalTextbookStudyService(workspace)
    selection = {key: study.books()[0][key] for key in ('source_id', 'revision')}
    return SimpleNamespace(paths=paths, state=state, workspace=workspace, study=study, selection=selection,
                           source=source, original=source.read_bytes())


@pytest.fixture
def outline_fixture(tmp_path):
    return fixture_for(tmp_path)


def chosen(preview):
    return [row['candidate_id'] for row in preview['rows']]


def test_heading_row_limit_does_not_extend_range_into_unread_pages(tmp_path, monkeypatch):
    f = fixture_for(tmp_path, mode="headings")
    monkeypatch.setattr(outline, 'MAX_OUTLINE_ROWS', 1)
    result = f.study.preview_outline(f.selection)
    assert len(result['rows']) == 1
    row = result['rows'][0]
    assert (row['pdf_start'], row['pdf_end']) == (2, 2)
    assert row['evidence']['end_basis'] == 'scan_limit'


def test_duplicate_bookmarks_in_same_batch_reuse_exact_title_and_range(tmp_path):
    f = fixture_for(tmp_path, mode='none')
    writer = PdfWriter(); writer.append(PdfReader(io.BytesIO(colored_pdf(6))))
    for _ in range(3):
        writer.add_outline_item('重复合成目录', 1)
    writer.add_outline_item('下一合成目录', 4)
    raw = io.BytesIO(); writer.write(raw)
    replacement = tmp_path / '重复书签.pdf'; replacement.write_bytes(raw.getvalue())
    f.workspace.commit_books(f.workspace.preview_books([replacement]))
    book = next(row for row in f.study.books() if row['source_name'] == replacement.name)
    selection = {key: book[key] for key in ('source_id', 'revision')}
    preview = f.study.preview_outline(selection)
    assert len(preview['rows']) == 4
    result = f.study.save_outline(selection, preview, chosen(preview))
    assert result['added'] == 3 and result['reused'] == 1
    assert len(result['view']['sections']) == 3
    assert all(row['already_saved'] for row in f.study.preview_outline(selection)['rows'])


def disk(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_bookmarks_preserve_group_titles_and_physical_pages_without_writes(outline_fixture):
    f = outline_fixture; before = disk(f.paths.state_root)
    result = f.study.preview_outline(f.selection)
    assert [row['title'] for row in result['rows']] == ['1.1 合成物质分类', '1.2 合成变化', '2.1 合成观察']
    assert [(row['pdf_start'], row['pdf_end']) for row in result['rows']] == [(2, 3), (4, 4), (5, 6)]
    assert result['rows'][0]['evidence']['group_path'] == ['第一章 合成学习目录']
    assert all(row['evidence']['method'] == 'pdf_bookmark' for row in result['rows'])
    assert disk(f.paths.state_root) == before and f.source.read_bytes() == f.original


def test_detect_save_read_note_reference_and_repeat_preserve_manual_records(outline_fixture):
    f = outline_fixture; view = f.study.book_study(f.selection)
    view = f.study.save_section(f.selection, view['study_revision'], title='原教师章节', pdf_start=1, pdf_end=2, range_confirmed=True)
    view = f.study.save_note(f.selection, view['study_revision'], section_id=view['sections'][0]['section_id'],
                            title='原笔记', text='保留原来的条件与单位 2.0 g。', pdf_page=1, teacher_confirmed=True)
    original_sections, original_notes = deepcopy(view['sections']), deepcopy(view['notes'])
    preview = f.study.preview_outline(f.selection)
    result = f.study.save_outline(f.selection, preview, chosen(preview))
    assert (result['added'], result['reused']) == (3, 0)
    view = result['view']
    assert view['sections'][:1] == original_sections and view['notes'] == original_notes
    section = view['sections'][1]
    assert section['range_confirmed'] is False and section['origin'] == 'auto_outline'
    assert all(section[key] is value for key, value in FLAGS.items()) and section['printed_pages'] is None
    read = f.study.read(f.selection, section_id=section['section_id'], study_revision=view['study_revision'])
    assert read['pdf_pages'] == [2, 3] and read['pdf_bytes'] == f.original and '范围待核对' in read['statement']
    view = f.study.save_note(f.selection, view['study_revision'], section_id=section['section_id'],
                            title='新教师笔记', text='A → B；用量 1.0 g。', pdf_page=2, teacher_confirmed=True)
    selected = [{key: view['notes'][-1][key] for key in ('note_id', 'revision')}]
    assert 'A → B' in f.study.reference(f.selection, view['study_revision'], selected)['materials']
    current = disk(f.paths.state_root)
    repeat = f.study.preview_outline(f.selection)
    result = f.study.save_outline(f.selection, repeat, chosen(repeat))
    assert (result['added'], result['reused']) == (0, 3) and disk(f.paths.state_root) == current


def test_candidate_can_be_confirmed_without_changing_title_or_pages(outline_fixture):
    f = outline_fixture; preview = f.study.preview_outline(f.selection)
    view = f.study.save_outline(f.selection, preview, chosen(preview))['view']; row = view['sections'][0]
    after = f.study.save_section(f.selection, view['study_revision'], section_id=row['section_id'],
                                title=row['title'], pdf_start=row['pdf_start'], pdf_end=row['pdf_end'], range_confirmed=True)
    assert after['sections'][0]['range_confirmed'] is True
    assert after['sections'][0]['outline_evidence'] == row['outline_evidence']
    assert after['sections'][0]['human_reviewed'] is False


def test_teacher_edit_keeps_original_detected_basis_and_rejects_excluding_note(outline_fixture):
    f = outline_fixture; preview = f.study.preview_outline(f.selection)
    view = f.study.save_outline(f.selection, preview, chosen(preview))['view']; row = view['sections'][0]
    view = f.study.save_note(f.selection, view['study_revision'], section_id=row['section_id'],
                            title='原页笔记', text='原页条件 X。', pdf_page=3, teacher_confirmed=True)
    before = disk(f.paths.state_root)
    with pytest.raises(PreparationSourceError, match='排除已有笔记'):
        f.study.save_section(f.selection, view['study_revision'], section_id=row['section_id'],
                             title='教师核对标题', pdf_start=2, pdf_end=2, range_confirmed=True)
    assert disk(f.paths.state_root) == before
    view = f.study.save_section(f.selection, view['study_revision'], section_id=row['section_id'],
                                title='教师核对标题', pdf_start=2, pdf_end=4, range_confirmed=True)
    assert view['sections'][0]['outline_evidence'] == row['outline_evidence']


@pytest.mark.parametrize('mutation', ['title', 'page', 'gate', 'duplicate', 'missing'])
def test_altered_preview_or_selection_cannot_write(outline_fixture, mutation):
    f = outline_fixture; preview = f.study.preview_outline(f.selection); ids = chosen(preview)
    if mutation == 'title': preview['rows'][0]['title'] = '未经识别的标题'
    if mutation == 'page': preview['rows'][0]['pdf_start'] = 1
    if mutation == 'gate': preview['human_reviewed'] = True
    if mutation == 'duplicate': ids.append(ids[0])
    if mutation == 'missing': ids.append('OUT-' + '0' * 32)
    before = disk(f.paths.state_root)
    with pytest.raises(PreparationSourceError): f.study.save_outline(f.selection, preview, ids)
    assert disk(f.paths.state_root) == before


def test_teacher_save_after_detection_invalidates_pending_batch(outline_fixture):
    f = outline_fixture; preview = f.study.preview_outline(f.selection)
    f.study.save_section(f.selection, preview['study_revision'], title='识别后教师新增', pdf_start=1, pdf_end=2, range_confirmed=True)
    before = disk(f.paths.state_root)
    with pytest.raises(PreparationSourceError, match='预览后变化'):
        f.study.save_outline(f.selection, preview, chosen(preview))
    assert disk(f.paths.state_root) == before


def test_manual_chapter_with_same_title_and_pages_is_reused_not_overwritten(outline_fixture):
    f = outline_fixture; view = f.study.book_study(f.selection)
    view = f.study.save_section(f.selection, view['study_revision'], title='1.1 合成物质分类',
                                pdf_start=2, pdf_end=3, range_confirmed=True)
    old = deepcopy(view['sections'][0]); preview = f.study.preview_outline(f.selection)
    assert preview['rows'][0]['already_saved']
    result = f.study.save_outline(f.selection, preview, chosen(preview))
    assert (result['added'], result['reused']) == (2, 1) and result['view']['sections'][0] == old


def test_pdf_replaced_after_detection_never_writes(outline_fixture):
    f = outline_fixture; preview = f.study.preview_outline(f.selection)
    archive = f.workspace.root / (f.selection['revision'] + '.pdf'); archive.write_bytes(b'changed')
    before = disk(f.paths.state_root)
    with pytest.raises(PreparationSourceError): f.study.save_outline(f.selection, preview, chosen(preview))
    assert disk(f.paths.state_root) == before


def test_plain_headings_skip_contents_and_repeated_headers(tmp_path):
    f = fixture_for(tmp_path, mode='headings'); preview = f.study.preview_outline(f.selection)
    assert [row['title'] for row in preview['rows']] == ['1.1 Matter', '1.2 Change']
    assert [(row['pdf_start'], row['pdf_end']) for row in preview['rows']] == [(2, 4), (5, 6)]
    assert all(row['evidence']['method'] == 'pdf_text_heading' for row in preview['rows'])
    assert any('文字层' in notice for notice in preview['notices'])


def test_image_only_book_retains_whole_book_and_manual_entry(tmp_path):
    f = fixture_for(tmp_path, mode='image_only'); before = disk(f.paths.state_root)
    preview = f.study.preview_outline(f.selection)
    assert not preview['rows'] and any('扫描图像' in notice for notice in preview['notices'])
    assert f.study.read(f.selection)['pdf_pages'] == list(range(1, 7)) and disk(f.paths.state_root) == before


def test_same_page_bookmarks_do_not_invent_page_split(tmp_path):
    f = fixture_for(tmp_path, same_page=True); preview = f.study.preview_outline(f.selection)
    assert preview['rows'][0]['pdf_start'] == preview['rows'][0]['pdf_end'] == 2
    assert preview['rows'][0]['evidence']['end_basis'] == 'same_page'
    assert any('重复或倒置' in notice for notice in preview['notices'])


def test_cancelled_detection_does_not_save_candidates(outline_fixture, monkeypatch):
    f = outline_fixture; before = disk(f.paths.state_root)
    def stop(): raise ReadCancelled('stopped')
    monkeypatch.setattr(outline, 'check_read_cancelled', stop)
    with pytest.raises(ReadCancelled): f.study.preview_outline(f.selection)
    assert disk(f.paths.state_root) == before


def test_exact_registered_pdf_uses_directory_and_rechecks_activation(asset_fixture, tmp_path):
    f = asset_fixture
    replace_book(f, colored_pdf())
    paths = DesktopPaths.from_workspace(f.root, state_root=tmp_path / 'private-state')
    workspace = TextbookWorkspaceService(paths, DesktopStateStore(paths.state_root))
    workspace.commit_books(workspace.preview_books([f.book])); study = PersonalTextbookStudyService(workspace)
    selection = {key: study.books()[0][key] for key in ('source_id', 'revision')}
    preview = study.preview_outline(selection)
    assert len(preview['rows']) == 12 and all(row['evidence']['method'] == 'registered_directory' for row in preview['rows'])
    assert [(row['pdf_start'], row['pdf_end']) for row in preview['rows'][:1]] == [(7, 15)]
    f.registry['title'] = '另一个激活目录版本'; f.activate()
    before = disk(paths.state_root)
    with pytest.raises(PreparationSourceError, match='预览后变化'):
        study.save_outline(selection, preview, chosen(preview))
    assert disk(paths.state_root) == before


def test_same_sha_in_multiple_registered_volumes_does_not_guess_from_filename(asset_fixture, tmp_path):
    f = asset_fixture
    paths = DesktopPaths.from_workspace(f.root, state_root=tmp_path / 'ambiguous-state')
    workspace = TextbookWorkspaceService(paths, DesktopStateStore(paths.state_root))
    workspace.commit_books(workspace.preview_books([f.book])); study = PersonalTextbookStudyService(workspace)
    selection = {key: study.books()[0][key] for key in ('source_id', 'revision')}
    before = disk(paths.state_root)
    assert study.preview_outline(selection)['rows'] == [] and disk(paths.state_root) == before


def test_new_outline_survives_original_byte_backup_restore(outline_fixture, tmp_path):
    f = outline_fixture; preview = f.study.preview_outline(f.selection)
    view = f.study.save_outline(f.selection, preview, chosen(preview))['view']
    backup = tmp_path / 'new-backup.zip'; f.workspace.export_bundle(backup)
    destination = tmp_path / 'restored'; f.workspace.restore_bundle(backup, destination)
    restored_paths = DesktopPaths.from_workspace(f.paths.workspace_root, state_root=destination)
    restored = PersonalTextbookStudyService(TextbookWorkspaceService(restored_paths, DesktopStateStore(destination)))
    assert restored.book_study(f.selection)['sections'] == view['sections']
    assert restored.read(f.selection)['pdf_bytes'] == f.original

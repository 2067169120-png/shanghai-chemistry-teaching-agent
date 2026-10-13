"""Selected Word/PPT delivery and real document-preview routes, without Office activation."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import json

import pytest
from test_lesson_design import sample
from test_lesson_design_ui import desk
from test_question_explorer_ui import window
from test_desktop_studio_ui import settle
from test_composition_render import app as render_app
from test_preview_navigation import qt_app, pdf_report
from integrations.deeptutor_shchem_v1 import desktop_lesson_output as output
from integrations.deeptutor_shchem_v1 import office_conversion as office
from integrations.deeptutor_shchem_v1 import desktop_local_pagination as local
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    enable_composition, add_resource, resource, block)
from integrations.deeptutor_shchem_v1.desktop_composition_render import arrange_composition
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, read_cancel_scope


def bundle(root, composition=False):
    payload = sample()
    if composition:
        plan = enable_composition(payload['lesson_design'])
        c = plan['composition']
        item = resource('personal_text', '课堂任务', [block('text', 'personal', '课堂正文唯一标记')],
                        {'source_path': '作者输入', 'source_sha256': 'unknown', 'source_revision': 'unknown'})
        c = add_resource(c, item, {'page_id': c['pages'][0], 'x': 12, 'y': 12, 'width': 186, 'height': 273})
        plan['composition'] = arrange_composition(c, [], {})
        payload['lesson_design'] = plan
    facade = SimpleNamespace(paths=SimpleNamespace(task_root=root/'tasks'))
    record = output.export_design(facade, payload)
    payload['lesson_design']['exports'].append(record)
    return facade, payload, record


@pytest.mark.parametrize('composition', [False, True])
def test_primary_copy_has_exactly_word_and_classroom_ppt_and_preserves_registered_bytes(tmp_path, render_app, composition):
    facade, p, record = bundle(tmp_path/'source', composition)
    before = deepcopy(p)
    names = output.primary_output_names(record)
    assert names == ('lesson_plan.docx', 'student_presentation.pptx' if composition else 'lesson_presentation.pptx')
    folder = Path(output.copy_primary_bundle(facade, p['lesson_design'], record['id'], tmp_path))
    assert {f.name for f in folder.iterdir()} == {'教案.docx', '课堂演示.pptx'}
    for name, alias in zip(names, ('教案.docx', '课堂演示.pptx')):
        assert (folder/alias).read_bytes() == output.checked_file(facade, p['lesson_design'], record['id'], name).read_bytes()
    assert p == before


def test_primary_copy_uses_selected_historical_version_and_does_not_overwrite(tmp_path, render_app):
    facade, p, old = bundle(tmp_path/'source')
    p['lesson_design']['nodes'][0]['student_task'] = '第二版课堂任务'
    newer = output.export_design(facade, p)
    p['lesson_design']['exports'].append(newer)
    first = Path(output.copy_primary_bundle(facade, p['lesson_design'], old['id'], tmp_path))
    second = Path(output.copy_primary_bundle(facade, p['lesson_design'], old['id'], tmp_path))
    assert first != second
    assert (second/'教案.docx').read_bytes() == output.checked_file(facade, p['lesson_design'], old['id'], 'lesson_plan.docx').read_bytes()
    assert (second/'教案.docx').read_bytes() != output.checked_file(facade, p['lesson_design'], newer['id'], 'lesson_plan.docx').read_bytes()


@pytest.mark.parametrize('problem', ['cancel', 'source_changed'])
def test_interrupted_primary_copy_removes_only_its_new_folder(tmp_path, render_app, monkeypatch, problem):
    facade, p, record = bundle(tmp_path/'source')
    destination = tmp_path/'delivery'; destination.mkdir()
    (destination/'teacher-file.docx').write_bytes(b'existing teacher work')
    event = Event(); real_copy = output.shutil.copyfile; calls = []
    def copying(source, target):
        real_copy(source, target); calls.append(source)
        if problem == 'cancel': event.set()
        elif len(calls) == 2: Path(calls[0]).write_bytes(b'changed after copying')
    monkeypatch.setattr(output.shutil, 'copyfile', copying)
    with pytest.raises(ReadCancelled if problem == 'cancel' else ValueError), read_cancel_scope(event):
        output.copy_primary_bundle(facade, p['lesson_design'], record['id'], destination)
    assert [f.name for f in destination.iterdir()] == ['teacher-file.docx']
    assert (destination/'teacher-file.docx').read_bytes() == b'existing teacher work'


def test_unknown_or_already_changed_registered_version_is_not_exported(tmp_path, render_app):
    facade, p, record = bundle(tmp_path/'source')
    destination = tmp_path/'delivery'; destination.mkdir()
    with pytest.raises(ValueError, match='成品记录'):
        output.copy_primary_bundle(facade, p['lesson_design'], 'unknown', destination)
    source = output.checked_file(facade, p['lesson_design'], record['id'], 'lesson_plan.docx')
    source.write_bytes(b'external revision')
    with pytest.raises(ValueError, match='外部修改'):
        output.copy_primary_bundle(facade, p['lesson_design'], record['id'], destination)
    assert not list(destination.iterdir())


@pytest.mark.parametrize('engine', ['word', 'libreoffice'])
def test_word_preview_converts_registered_docx_and_binds_actual_pdf(tmp_path, render_app, monkeypatch, engine):
    facade, p, record = bundle(tmp_path/'source')
    source = output.checked_file(facade, p['lesson_design'], record['id'], 'lesson_plan.docx')
    raw = source.read_bytes(); calls = []
    monkeypatch.setattr(local, 'find_libreoffice', lambda: Path('local-office') if engine == 'libreoffice' else None)
    def word(src, target):
        assert src == source; calls.append('word'); target.write_bytes(b'%PDF-word-preview')
    def libre(src, target, executable, **kwargs):
        assert src == source and kwargs['export_filter'] == 'writer_pdf_Export'
        calls.append('libreoffice'); pdf = target/'lesson_plan.pdf'; pdf.write_bytes(b'%PDF-writer-preview'); return pdf
    monkeypatch.setattr(office, 'word_pdf', word); monkeypatch.setattr(office, 'libreoffice_pdf', libre)
    report = output.actual_word_preview(facade, p['lesson_design'], record['id'])
    assert calls == [engine] and source.read_bytes() == raw
    assert report['document_kind'] == 'word' and report['document_name'] == 'lesson_plan.docx'
    assert report['source_sha256'] == sha256(raw).hexdigest()
    assert report['pdf_sha256'] == sha256(Path(report['path']).read_bytes()).hexdigest()
    assert json.loads((Path(report['path']).parent/'preview.json').read_text(encoding='utf-8')) == report


@pytest.mark.parametrize('problem', ['error', 'cancel', 'source_changed'])
def test_word_preview_cannot_publish_failed_cancelled_or_changed_source(tmp_path, render_app, monkeypatch, problem):
    facade, p, record = bundle(tmp_path/'source')
    source = output.checked_file(facade, p['lesson_design'], record['id'], 'lesson_plan.docx')
    monkeypatch.setattr(local, 'find_libreoffice', lambda: None)
    def convert(src, target):
        if problem == 'error': raise office.OfficeConversionError('合成转换失败')
        if problem == 'cancel': raise ReadCancelled()
        target.write_bytes(b'%PDF-changed-source')
        src.write_bytes(b'external change')
    monkeypatch.setattr(office, 'word_pdf', convert)
    with pytest.raises(ReadCancelled if problem == 'cancel' else ValueError):
        output.actual_word_preview(facade, p['lesson_design'], record['id'])
    assert not list(source.parent.glob('actual-word-*/preview.json'))


def test_native_primary_buttons_and_word_route_keep_selected_version(desk, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench import lesson_design_dialog as ui
    d, app, page = desk
    assert not d.export_primary.isEnabled() and not d.preview_word_button.isEnabled()
    assert 'Word' in d.generate.text() and '课堂' in d.generate.text()
    d.generate.click(); settle(app, lambda: not d.busy)
    identity = d.outputs.currentData(); before = deepcopy(d.history.value)
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **k: '')
    d.export_primary.click(); settle(app)
    assert d.history.value == before
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **k: str(tmp_path))
    d.export_primary.click(); settle(app, lambda: not d.busy)
    assert '已导出到' in d.status.text() and len(list(tmp_path.glob('教案与课堂PPT-*'))) == 1
    called = []
    monkeypatch.setattr(ui, 'actual_word_preview', lambda facade, plan, selected: called.append((selected, plan)) or {'document_kind': 'word'})
    class Preview:
        def __init__(self, report, parent, **kwargs):
            assert report['document_kind'] == 'word' and 'script_save' not in kwargs
        def exec(self): pass
    monkeypatch.setattr(ui, 'ActualPptPreview', Preview)
    d.preview_word_button.click(); settle(app, lambda: not d.busy)
    assert called == [(identity, before)] and d.history.value == before


def test_native_classroom_button_prefers_student_deck(desk, tmp_path, render_app, monkeypatch):
    d, app, page = desk
    _, payload, record = bundle(tmp_path/'source', True)
    d.history.value['exports'].append(record)
    d.refresh_report(); settle(app)
    d.outputs.setCurrentIndex(d.outputs.findData(record['id'])); settle(app)
    assert d.preview_audience.currentData() == 'student_presentation.pptx'
    opened = []; monkeypatch.setattr(d, 'open_file', opened.append)
    d.open_classroom_button.click()
    assert opened == ['student_presentation.pptx']


def test_actual_word_reader_has_document_navigation_and_no_narration(qt_app, tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_workbench.actual_ppt_preview_dialog import ActualPptPreview
    from test_desktop_mixed_paper_ui import Tasks
    tasks = Tasks(); report = {**pdf_report(tmp_path, 2), 'document_kind': 'word', 'engine': 'Microsoft Word'}
    d = ActualPptPreview(report, tasks=tasks); d.show(); tasks.finish(); tasks.finish()
    assert '教案 Word' in d.windowTitle() and d.narration is None
    assert d.count == 2 and d._shown == 1
    assert '核对' not in d.caption.text() and 'Word' in d.page_selector.accessibleName()
    d.page_selector.setValue(2); tasks.finish()
    assert d._shown == 2
    d.reject(); d.deleteLater(); qt_app.processEvents()

"""Explicit library/lesson destinations, preview freshness and additive native UI."""
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog
from test_word_question_dialog import qt_app as qt_app, _Facade, _Tasks, _check, _cleanup
from test_question_explorer_ui import window as window
from test_composition_resource_titles import question
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog


def loaded(mode='both'):
    facade,tasks=_Facade(),_Tasks()
    dialog=WordQuestionDialog(facade,tasks,handoff_mode=mode)
    tasks.flush()
    return dialog,facade,tasks


def preview(dialog,tasks):
    _check(dialog,'Q1');dialog.preview_button.click();tasks.finish('预览 Word 选题')


def test_explicit_composition_click_keeps_reference_destination_separate(qt_app,monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
    dialog,facade,tasks=loaded();dialog.show();qt_app.processEvents()
    assert not dialog.composition_button.isEnabled() and dialog.composition_result is None
    preview(dialog,tasks);calls=[];result={'resources':[question()],'image_assets':[]}
    def checked(f,selections,assets,*,expected_reference):
        calls.append((f,deepcopy(selections),deepcopy(expected_reference)))
        assert expected_reference==f.word_question_reference(selections)
        return deepcopy(result)
    monkeypatch.setattr(sources,'import_questions',checked)
    assert dialog.composition_button.isVisible() and dialog.import_button.isVisible()
    QTest.mouseClick(dialog.composition_button,Qt.MouseButton.LeftButton)
    tasks.finish('核对完整题并加入课件')
    tasks.flush()
    assert dialog.result()==QDialog.DialogCode.Accepted and len(calls)==1
    assert dialog.composition_result==result and dialog.preparation_reference is None
    dialog.deleteLater()


@pytest.mark.parametrize('reason',['changed','failed','late_selection'])
def test_stale_or_failed_composition_cannot_deliver(qt_app,monkeypatch,reason):
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
    dialog,facade,tasks=loaded();preview(dialog,tasks)
    if reason=='changed':facade.reference_changed=True
    if reason=='failed':facade.reference_failed=True
    monkeypatch.setattr(sources,'import_questions',lambda *a,**k:{'resources':[question()],'image_assets':[]})
    dialog.composition_button.click()
    if reason=='late_selection':_check(dialog,'Q2')
    tasks.finish('核对完整题并加入课件')
    assert dialog.composition_result is None and dialog.preparation_reference is None
    assert dialog.result()!=QDialog.DialogCode.Accepted
    _cleanup(dialog,tasks)


def test_full_question_mode_requires_images_and_twenty_question_limit(qt_app):
    dialog,_,tasks=loaded('composition');dialog.show();qt_app.processEvents()
    assert not dialog.import_button.isVisible() and not dialog.include_images.isEnabled()
    preview(dialog,tasks);assert dialog.composition_button.isEnabled()
    dialog.include_images.setChecked(False);assert not dialog.composition_button.isEnabled()
    _cleanup(dialog,tasks)
    dialog,facade,tasks=loaded();preview(dialog,tasks)
    for i in range(21):
        key='extra-'+str(i);facade.catalog['items'].append({**facade.catalog['items'][0],'key':key})
        dialog._items[key]=deepcopy(facade.catalog['items'][-1]);dialog._selected[key]='r1'
    dialog._preview_reference=facade.word_question_reference(dialog.selections)
    dialog._preview_selection=dialog._selection_key();dialog._update_actions()
    assert not dialog.composition_button.isEnabled()
    _cleanup(dialog,tasks)


@pytest.mark.parametrize('width',[420,1000])
def test_both_handoff_actions_are_reachable_in_supported_window(qt_app,width):
    dialog,_,tasks=loaded();dialog.resize(width,900);dialog.show();qt_app.processEvents()
    preview(dialog,tasks);dialog.body_scroll.ensureWidgetVisible(dialog.composition_button)
    qt_app.processEvents()
    assert dialog.composition_button.isVisible() and dialog.import_button.isVisible()
    assert dialog.composition_button.width()<=dialog.preparation_controls.width()
    assert dialog.composition_button.isEnabled()
    _cleanup(dialog,tasks)


def test_library_signal_preserves_current_materials_and_same_question_once(window,monkeypatch):
    win,app=window;page=win.preparation_page
    page.topic.setText('已有课题');page.materials.setPlainText('既有资料全文')
    result={'resources':[question()],'image_assets':[]};opened=[]
    def inspect(dialog):
        opened.append(deepcopy(dialog.history.value))
        assert dialog.tabs.currentWidget() is dialog.composition_panel
        dialog.reject();return 0
    monkeypatch.setattr(LessonDesignDialog,'exec',inspect)
    win.library_page.word_cards_requested.emit(result)
    assert win.stack.currentWidget() is page and len(opened)==1
    assert page.topic.text()=='已有课题' and page.materials.toPlainText()=='既有资料全文'
    assert page._lesson_design['composition']['resources']==result['resources']
    win.library_page.word_cards_requested.emit(deepcopy(result));assert len(opened)==1
    before=deepcopy(page._payload());page._lesson_busy=True
    assert not page.import_word_cards(result) and page._payload()==before
    page._lesson_busy=False


def test_library_dialog_routes_complete_questions_to_canvas(window,monkeypatch):
    win,app=window;cards=[];references=[];modes=[]
    win.library_page.word_cards_requested.connect(cards.append)
    win.library_page.word_reference_requested.connect(references.append)
    monkeypatch.setattr(win.preparation_page,'import_word_cards',lambda result:True)
    result={'resources':[question()],'image_assets':[]}
    def accept(dialog):
        modes.append(dialog._handoff_mode);dialog.composition_result=deepcopy(result)
        dialog._finish_dialog(QDialog.DialogCode.Accepted);return QDialog.DialogCode.Accepted
    monkeypatch.setattr(WordQuestionDialog,'exec',accept)
    win.library_page.open_advanced()
    assert modes==['both'] and cards==[result] and references==[]


def test_default_library_has_explicit_complete_question_entry(window,monkeypatch):
    win,app=window;seen=[]
    assert win.library_page.word_composition_button.isVisible()
    def cancel(dialog):
        seen.append(dialog._handoff_mode);dialog.reject();return QDialog.DialogCode.Rejected
    monkeypatch.setattr(WordQuestionDialog,'exec',cancel)
    win.library_page.word_composition_button.click()
    assert seen==['composition']
    win.library_page.scope.setCurrentIndex(win.library_page.scope.findData('master'))
    assert not win.library_page.word_composition_button.isVisible()


def test_canvas_picker_uses_full_question_mode_and_can_cancel_without_changes(window,monkeypatch):
    win,app=window;page=win.preparation_page;dialog=LessonDesignDialog(page)
    before=deepcopy(page._payload());seen=[]
    def cancel(picker):
        seen.append(picker._handoff_mode);picker.reject();return QDialog.DialogCode.Rejected
    monkeypatch.setattr(WordQuestionDialog,'exec',cancel)
    dialog.composition_panel.add_questions()
    assert seen==['composition'] and page._payload()==before
    dialog.deleteLater()

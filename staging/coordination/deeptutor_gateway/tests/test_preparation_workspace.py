import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QPoint, QCoreApplication
from PySide6.QtWidgets import QMessageBox
from test_desktop_studio_ui import window, settle
from integrations.deeptutor_shchem_v1.desktop_workbench.app import install_chinese_translations


def test_real_actions_visible_after_scroll_at_800(window):
    win,app=window;page=win.preparation_page
    win.resize(800,700);win.navigate('preparation');settle(app)
    page.editor_scroll.verticalScrollBar().setValue(page.editor_scroll.verticalScrollBar().maximum())
    settle(app)
    for button in (page.save_button,page.generate_button,page.workspace.materials,page.workspace.results):
        point=button.mapTo(page,QPoint(0,0))
        assert button.isVisible() and 0<=point.y()<page.height()
        assert point.y()+button.height()<=page.height()
    assert page.save_button.parentWidget() is page.workspace.footer
    assert page.generate_button.parentWidget() is page.workspace.footer


def test_navigation_preserves_material_selection_and_inner_scroll(window):
    win,app=window;page=win.preparation_page;win.navigate('preparation')
    page.materials.setPlainText('\n'.join('公共材料'+str(n) for n in range(60)))
    cursor=page.materials.textCursor();cursor.setPosition(10);cursor.setPosition(25,cursor.MoveMode.KeepAnchor);page.materials.setTextCursor(cursor)
    page.materials.verticalScrollBar().setValue(10)
    before=page._payload();selection=page.materials.textCursor().selectedText();scroll=page.materials.verticalScrollBar().value()
    page.workspace.requirements.click();page.workspace.materials.click();settle(app)
    assert page._payload()==before
    assert page.materials.textCursor().selectedText()==selection
    assert page.materials.verticalScrollBar().value()==scroll


def test_failed_task_with_returned_content_can_be_located_without_retry(window):
    win,app=window;page=win.preparation_page;win.navigate('preparation')
    assert not page.workspace.results.isEnabled()
    page._render_summary({'task_id':'PREP-synthetic','status':'failed','progress_percent':30,
                          'message_zh':'合成导出失败','returned_candidate_available':True,'retryable':True})
    settle(app)
    assert page.workspace.results.isEnabled()
    assert page.recover_returned_button.isVisible()
    assert page.recover_returned_button.parentWidget() is page.workspace.footer
    page.workspace.results.click()
    assert page._generation_qt_task_id is None
    page._preparation_task_id=None;page._active_preparation_task_id=None


def test_standard_dialog_buttons_are_chinese_and_loaded_once(window):
    win,app=window
    install_chinese_translations(app)
    translator=app._workbench_chinese_translator
    assert app._workbench_chinese_translation_loaded
    install_chinese_translations(app)
    assert app._workbench_chinese_translator is translator
    dialog=QMessageBox(win)
    dialog.setStandardButtons(QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel)
    assert '确定' in dialog.button(QMessageBox.StandardButton.Ok).text()
    assert '取消' in dialog.button(QMessageBox.StandardButton.Cancel).text()
    dialog.deleteLater()

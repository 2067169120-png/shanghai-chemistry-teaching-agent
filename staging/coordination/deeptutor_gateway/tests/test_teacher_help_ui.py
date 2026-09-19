"""Real Qt scenario help and existing workbench integration, synthetic data only."""
from pathlib import Path
import os
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QRect, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget, QDialog, QApplication
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.teacher_help import TeacherHelpDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_navigation import HelpDialog, CommandPalette
from test_desktop_studio_ui import window, settle


class Owner(QWidget):
    def __init__(self):
        super().__init__()
        self.pages={key: object() for key in ('preparation','library','student','mywork')}
        self.visits=[]
    def navigate(self,route): self.visits.append(route)


@pytest.fixture
def handbook():
    app=create_application(['teacher-help-check'])
    owner=Owner(); dialog=TeacherHelpDialog(owner); dialog.show(); settle(app)
    yield dialog, owner, app
    dialog.reject(); dialog.deleteLater(); owner.deleteLater(); app.processEvents()


def screenshot(dialog,name):
    destination=os.environ.get('SHCHEM_CANDIDATE_SCREENSHOTS')
    if destination:
        path=Path(destination);path.mkdir(parents=True,exist_ok=True)
        assert dialog.grab().save(str(path/name))


def test_old_help_entry_now_exposes_searchable_teacher_tasks(handbook):
    dialog,owner,app=handbook
    assert HelpDialog is TeacherHelpDialog
    assert dialog.results.count()==6 and dialog.current_scenario.key=='lesson'
    for title in ('准备什么','怎么做','得到什么','完成前核对'):
        assert title in dialog.view.toPlainText()
    assert owner.visits==[]


def test_no_result_clears_stale_details_and_actions(handbook):
    d,owner,app=handbook;d.query.setText('no-matching-scenario');settle(app)
    assert d.current_scenario is None and d.results.count()==0
    assert not d.open_button.isEnabled() and not d.copy_button.isEnabled()
    assert '没有找到匹配场景' in d.view.toPlainText()
    assert owner.visits==[]
    d.query.clear();assert d.results.count()==6 and d.copy_button.isEnabled()


def test_clearing_search_preserves_selected_scenario_when_possible(handbook):
    d,owner,app=handbook;d.query.setText('备份');assert d.current_scenario.key=='save'
    d.query.clear();assert d.current_scenario.key=='save'
    assert owner.visits==[]


def test_typing_and_return_never_start_work(handbook):
    d,owner,app=handbook;d.query.setText('打印');d.query.setFocus()
    QTest.keyClick(d.query,Qt.Key.Key_Return);settle(app)
    assert owner.visits==[] and d.isVisible()
    assert d.current_scenario.key=='paper'


def test_copy_only_copies_instructions_after_explicit_click(handbook):
    d,owner,app=handbook;app.clipboard().setText('unchanged')
    d.query.setText('改分');assert app.clipboard().text()=='unchanged'
    d.copy_button.click();text=app.clipboard().text()
    assert '[ ]' in text and '维护源码' in text and '不是自动验收结果' in text
    assert '不是已保存' in d.status.text() and owner.visits==[]
    app.clipboard().clear()


def test_open_workspace_uses_one_allowlisted_existing_route(handbook):
    d,owner,app=handbook;d.query.setText('打印');d.open_button.click();settle(app)
    assert owner.visits==['library'] and d.result()==QDialog.DialogCode.Accepted


def test_unavailable_parent_is_read_only(handbook):
    _,owner,app=handbook;d=TeacherHelpDialog();d.show();settle(app)
    assert not d.open_button.isEnabled() and d.copy_button.isEnabled()
    d.open_workspace();assert d.isVisible()
    d.reject();d.deleteLater()


def test_failed_navigation_keeps_help_open(handbook,monkeypatch):
    d,owner,app=handbook
    def fail(route):raise RuntimeError('synthetic unavailable route')
    monkeypatch.setattr(owner,'navigate',fail)
    d.open_button.click();assert d.isVisible() and '未能打开工作区' in d.status.text()


def test_html_view_has_no_external_resource_loader(handbook):
    d,owner,app=handbook
    assert not d.view.openExternalLinks() and not d.view.openLinks()
    assert d.view.loadResource(2,QUrl('https://invalid.example/image.png')) is None


def test_real_wide_and_compact_help_geometry(handbook):
    d,owner,app=handbook
    for width,height,name in ((980,700,'teacher-help-wide.png'),(420,760,'teacher-help-compact.png')):
        d.resize(width,height);settle(app)
        assert d.width()==width
        assert d.view.height()>=150 and d.results.height()>=100
        expected=Qt.Orientation.Vertical if width<680 else Qt.Orientation.Horizontal
        assert d.splitter.orientation()==expected
        for widget in (d.query,d.results,d.view,d.copy_button,d.open_button,d.close_button):
            assert d.rect().contains(QRect(widget.mapTo(d,widget.rect().topLeft()),widget.size()))
        screenshot(d,name)
    d.query.setText('找不到的场景');settle(app);screenshot(d,'teacher-help-empty.png')


def test_escape_returns_without_navigation(handbook):
    d,owner,app=handbook;QTest.keyClick(d,Qt.Key.Key_Escape);settle(app)
    assert d.result()==QDialog.DialogCode.Rejected and owner.visits==[]


def test_workbench_help_preserves_existing_preparation_and_basket(window):
    win,app=window
    prep=win.preparation_page;prep.topic.setText('已有教师课题');prep.materials.setPlainText('已核对公共材料')
    before=prep._payload();basket=win.facade.basket()
    win.navigate('preparation')
    d=HelpDialog(win);d.show();settle(app)
    assert d.current_scenario.key=='lesson'
    d.query.setText('讲评');d.open_button.click();settle(app)
    assert win.stack.currentWidget() is win.student_page
    assert prep._payload()==before and win.facade.basket()==basket
    d.deleteLater()


@pytest.mark.parametrize('query,route',[('打印','paper'),('月考','exam-analysis'),('改分','student')])
def test_quick_commands_accept_teacher_words_without_new_handlers(handbook,query,route):
    _,owner,app=handbook;d=CommandPalette(owner);d.query.setText(query)
    assert d.results.count()==1
    d.choose();assert d.command==route
    d.deleteLater()

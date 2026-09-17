import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtWidgets import QApplication,QDialog,QMessageBox,QInputDialog
from types import SimpleNamespace
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example,FixtureStore,FixtureTransport
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.exam_dashboard import ExamDashboard
from integrations.deeptutor_shchem_v1.desktop_workbench.exam_import_dialog import ExamImportDialog
from integrations.deeptutor_shchem_v1.desktop_facade import ProviderProfileSummary

@pytest.fixture
def desk(tmp_path):
    app=QApplication.instance() or QApplication([])
    book,cfg,exam=example(tmp_path/'example.xlsx')
    f=SimpleNamespace(paths=SimpleNamespace(state_root=tmp_path/'state'),list_provider_profiles=lambda:())
    tasks=DesktopTaskBridge();d=ExamDashboard(f,tasks);d.accept_exam(exam);d.show();settle(app)
    yield d,app,(book,cfg,exam)
    tasks.wait_for_done(5000);settle(app,lambda:d._task is None);d.dirty=False;d.close();d.deleteLater();tasks.shutdown();app.processEvents()

def test_import_auto_maps_explicit_template_then_validates(desk):
    _,app,(book,cfg,exam)=desk;d=ExamImportDialog(book);d.show();settle(app)
    assert len(d.config()['questions'])==4
    d.commit();assert d.exam['students'][0]['total']==81;d.deleteLater()

def test_dashboard_native_stats_and_filter(desk):
    d,app,_=desk
    assert d.kpis[0].text()=='10 / 12'
    d.classes.setCurrentIndex(d.classes.findData('合成班A'));settle(app)
    assert d.report['overall']['n']==4 and d.kpis[0].text()=='4 / 6'

def test_heatmap_missing_is_not_zero_and_actions_use_real_item(desk):
    d,app,_=desk;d.tabs.setCurrentIndex(2);settle(app)
    m=d.student_table.model();assert m.data(m.index(4,3))=='—'
    d.select_student(m.index(1,0));assert '失' in d.student_note.toPlainText()
    assert 'S0002' in d.student_note.toPlainText()

def test_saved_history_open_does_not_overwrite_paper_or_advice(desk,monkeypatch):
    d,app,_=desk;d.paper_text.setPlainText('必须保留的试卷共同材料');d.notes.setPlainText('已教到原理');d.save_current()
    identity=d.exam['id'];before=d.store.load(identity)
    monkeypatch.setattr(QInputDialog,'getItem',lambda *a,**k:('1. '+d.exam['title'],True))
    d.open_history();settle(app)
    assert d.paper_text.toPlainText()=='必须保留的试卷共同材料'
    assert d.store.load(identity)==before

def test_editing_paper_invalidates_old_advice(desk):
    d,app,_=desk;d.result={'old':'result'};d.result_scope=None
    d.paper_text.setPlainText('修改题干条件')
    assert d.result is None and d.dirty

def test_esc_respects_unsaved_cancel(desk,monkeypatch):
    d,app,_=desk;d.notes.setPlainText('尚未保存')
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Cancel)
    d.reject();assert not d._closed and d.isVisible() and d.notes.toPlainText()=='尚未保存'

def test_current_advice_cannot_be_relabelled_as_another_class(desk):
    d,app,_=desk;d.result={'candidate':{}};d.result_scope='合成班A'
    assert d.current_result() is None
    # Do not render a fabricated object, only verify binding selector.
    d.result=None;d.classes.setCurrentIndex(d.classes.findData('合成班B'));assert d.current_result() is None

def test_small_window_keeps_toolbar_filter_and_tabs_accessible(desk):
    d,app,_=desk;d.resize(800,700);settle(app)
    assert d.width()==800
    for w in (d.import_button,d.export_button,d.classes,d.tabs,d.close_button):
        assert d.rect().contains(QRect(w.mapTo(d,w.rect().topLeft()),w.size()))
    assert d.tabs.height()>300

def test_confirmed_fake_api_via_actual_button_does_not_recompute_grades(desk):
    d,app,_=desk;profile=ProviderProfileSummary('p','Fixture','https://models.example/v1','fixture-model','chat_completions',('text',),True,'fixture-revision')
    d.facade._providers=FixtureStore();d.facade._exam_transport=FixtureTransport();d.model.clear();d.model.addItem('Fixture',profile)
    d.tabs.setCurrentIndex(3);before=d.report['overall'].copy()
    def accept():
        consent=QApplication.activeModalWidget()
        assert consent is not None and consent is not d
        consent.accept()
    QTimer.singleShot(150,accept);d.request_ai()
    settle(app,lambda:d._task is None and d.result is not None)
    assert len(d.facade._exam_transport.requests)==1 and d.report['overall']==before
    assert '待教师核对' in d.ai_result.toPlainText()

def test_no_model_keeps_dashboard_available(desk):
    d,app,_=desk;d.request_ai();assert '设置' in d.status.text()
    assert d.report['overall']['mean']==69 and d.export_button.isEnabled()

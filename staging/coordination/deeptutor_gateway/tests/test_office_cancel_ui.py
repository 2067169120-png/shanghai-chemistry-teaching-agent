"""Real Qt/task/process paths with authored inputs and a Python converter surrogate."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from threading import Event

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QRect, QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QPushButton
from test_desktop_studio_ui import settle
from test_desktop_mixed_paper_service import setup, _add_word
from test_question_explorer_ui import window
from test_lesson_design_ui import desk as lesson_desk
from test_exam_dashboard_ui import desk as exam_desk
from test_exam_practice_set_ui import task_for
from mixed_pagination_test_support import synthetic_pagination, paginate_and_read
from integrations.deeptutor_shchem_v1.desktop_independent_paper import IndependentPaperLibrary
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import _ACTIVE
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.scan_paper_page import ScanPaperPage
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.owned_process import run_owned
from integrations.deeptutor_shchem_v1.reader_cancellation import publication_guard


def capture(widget, name, controls):
    """Optional output for the explicitly guarded QA runner, never Office proof."""
    # Deliver layout requests without consuming the worker's queued terminal
    # signal; the stopping state must be captured after its label has wrapped.
    QApplication.sendPostedEvents(None,QEvent.Type.LayoutRequest)
    geometry = {}
    for label, control in controls.items():
        rect = QRect(control.mapTo(widget, control.rect().topLeft()), control.size())
        assert widget.rect().contains(rect), (name, label, rect, widget.size())
        geometry[label] = {"rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                           "visible": control.isVisible(), "enabled": control.isEnabled()}
        if isinstance(control,QLabel) and control.wordWrap():
            needed=control.fontMetrics().boundingRect(QRect(0,0,control.contentsRect().width(),10000),Qt.TextFlag.TextWordWrap,control.text()).height()
            assert control.contentsRect().height()>=needed,(name,label,needed,control.height())
    destination = os.environ.get("SHCHEM_OFFICE_CANCEL_UI_OUTPUT")
    if not destination:return
    output = Path(destination); output.mkdir(parents=True, exist_ok=True)
    path = output/(name+".png"); shot = widget.grab()
    assert shot.save(str(path))
    row = {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
           "logical_size": [widget.width(), widget.height()], "pixel_size": [shot.width(), shot.height()],
           "device_pixel_ratio": shot.devicePixelRatio(), "geometry": geometry,
           "office": False, "backend": "real owned Python process / synthetic pagination"}
    with (output/"screenshots.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False)+"\n")


def slow_process(marker):
    run_owned([sys.executable, "-B", "-c",
               "import sys,time;from pathlib import Path;Path(sys.argv[1]).touch();time.sleep(30)", str(marker)])


def lesson_controls(dialog, *, stopping=True):
    """Check the full node editor, including controls clipped by its scroll area."""
    QApplication.sendPostedEvents(None,QEvent.Type.LayoutRequest)
    content=dialog.node_scroll.widget();viewport=dialog.node_scroll.viewport()
    buttons=content.findChildren(QPushButton)
    boxes=[QRect(button.mapTo(content,button.rect().topLeft()),button.size()) for button in buttons]
    assert all(not a.intersects(b) for i,a in enumerate(boxes) for b in boxes[i+1:])
    assert all(button.height()>=button.minimumSizeHint().height() for button in buttons)
    scroll=dialog.node_scroll.verticalScrollBar();position=scroll.value()
    for button in buttons:
        dialog.node_scroll.ensureWidgetVisible(button,0,0)
        rect=QRect(button.mapTo(viewport,button.rect().topLeft()),button.size())
        assert viewport.rect().contains(rect),(button.text(),rect,viewport.rect())
    scroll.setValue(position)
    controls={"status":dialog.status,"return":dialog.return_button,"save":dialog.save,
              "node_editor":dialog.node_scroll,"properties":dialog.properties}
    if stopping:controls["stop"]=dialog.stop_button
    assert not dialog.node_scroll.geometry().intersects(dialog.properties.geometry())
    return controls


@pytest.fixture
def paper_ui(setup):
    app = create_application(["office-cancel-ui"])
    engine, store, words, *_ = setup
    facade = engine.facade
    facade._reader_stop_event = Event(); facade.basket = store.basket
    facade.independent_paper_library = lambda: IndependentPaperLibrary(facade)
    _add_word(engine, words)
    tasks = DesktopTaskBridge(); content = ScanPaperPage(facade, tasks)
    page=QDialog();box=QVBoxLayout(page);box.setContentsMargins(0,0,0,0);box.addWidget(content)
    page.resize(420,520); page.show(); shell = content._composer
    settle(app); shell.new_button.click()
    settle(app,lambda:shell._mixed_panel is not None and shell._mixed_panel._loaded_once)
    panel = shell._mixed_panel
    for name in ("_core_bundle_builder", "_docx_builder", "_word_validator", "_core_context"):
        setattr(panel.facade.service, name, getattr(engine, name))
    panel.facade.service._pagination_builder = synthetic_pagination
    yield page, panel, store, tasks, app
    page.close(); content.close(); panel.close(); tasks.shutdown(); app.processEvents()


@pytest.mark.parametrize("width", [360,420,1000])
def test_real_qt_stop_preserves_committed_paper_and_retries(paper_ui,tmp_path,width):
    page,panel,store,tasks,app = paper_ui
    page.resize(width,520 if width<700 else 760); settle(app)
    service=panel.facade.service
    old=paginate_and_read(service,service.create_preview(panel.request()))
    service.approve(old.preview_id,old.preview_hash)
    before=deepcopy(panel.facade.state_store.snapshot()["drafts"])
    marker=tmp_path/"converter-started"
    service._pagination_builder=lambda paths,output:(slow_process(marker),synthetic_pagination(paths,output))[1]
    panel.preview_button.click(); settle(app,marker.exists)
    controls={"status":panel.status,"stop":panel.cancel_button,"save":panel.save_button,"return":panel.back_button}
    assert panel.cancel_button.isVisible() and not panel.back_button.isEnabled()
    capture(page,f"paper-{width}-running",controls)
    panel.cancel_button.click()
    assert "正在停止" in panel.status.text() and not panel.cancel_button.isEnabled()
    capture(page,f"paper-{width}-stopping",controls)
    settle(app,lambda:panel._operation_task is None)
    assert "取消" in panel.status.text() and panel.preview_button.isEnabled()
    assert panel.facade.state_store.snapshot()["drafts"][_ACTIVE]==before[_ACTIVE]
    assert panel.facade.state_store.snapshot()["drafts"][old.preview_id]==before[old.preview_id]
    assert len(service.export(old.preview_id,old.preview_hash)["artifacts"])==4
    capture(page,f"paper-{width}-cancelled",{"status":panel.status,"save":panel.save_button,"return":panel.back_button})
    service._pagination_builder=synthetic_pagination
    panel.preview_button.click(); settle(app,lambda:panel._preview_dialog is not None)
    assert panel._preview.preview_id != old.preview_id
    assert panel._preview.preview_model["pagination"]["status"]=="rendered_pending_review"
    panel._preview_dialog.reject()
    capture(page,f"paper-{width}-retry",{"status":panel.status,"save":panel.save_button,"return":panel.back_button})


def test_close_during_conversion_cancels_without_late_preview(paper_ui,tmp_path):
    page,panel,store,tasks,app=paper_ui
    marker=tmp_path/"started"
    panel.facade.service._pagination_builder=lambda *a:slow_process(marker)
    panel.preview_button.click();settle(app,marker.exists)
    page.close();panel.close()
    settle(app,lambda:tasks._pool.activeThreadCount()==0)
    assert panel._preview_dialog is None
    assert _ACTIVE not in panel.facade.state_store.snapshot()["drafts"]


@pytest.mark.parametrize("width",[360,420])
def test_lesson_stop_keeps_design_and_previous_outputs(lesson_desk,tmp_path,monkeypatch,width):
    from integrations.deeptutor_shchem_v1.desktop_workbench import lesson_design_dialog as module
    dialog,app,page=lesson_desk
    dialog.resize(width,520);settle(app)
    previous=deepcopy(dialog.history.value);marker=tmp_path/"lesson-started"
    monkeypatch.setattr(module,"export_design",lambda *a,**k:slow_process(marker))
    dialog.generate.click();settle(app,marker.exists)
    assert dialog.stop_button.isVisible()
    controls=lesson_controls(dialog)
    capture(dialog,f"lesson-{width}-running",controls)
    dialog.stop_button.click();capture(dialog,f"lesson-{width}-stopping",controls)
    settle(app,lambda:dialog._task is None)
    assert dialog.history.value==previous and "取消" in dialog.status.text()
    capture(dialog,f"lesson-{width}-cancelled",lesson_controls(dialog,stopping=False))
    dialog.node_scroll.verticalScrollBar().setValue(dialog.node_scroll.verticalScrollBar().maximum())
    capture(dialog,f"lesson-{width}-node-actions",lesson_controls(dialog,stopping=False))


def test_lesson_save_has_no_stop_and_close_waits_for_commit(lesson_desk,monkeypatch):
    dialog,app,page=lesson_desk
    dialog.resize(420,520);entered,release=Event(),Event()
    original=dialog.facade.create_preparation_draft
    def save(payload):
        entered.set();assert release.wait(8)
        return original(payload)
    monkeypatch.setattr(dialog.facade,"create_preparation_draft",save)
    try:
        dialog.save.click();settle(app,entered.is_set)
        assert not dialog.stop_button.isVisible() and dialog.return_button.text()=="完成后返回"
        capture(dialog,"lesson-420-saving",lesson_controls(dialog,stopping=False))
        dialog.reject()
        assert dialog.isVisible() and dialog._close_when_finished and not dialog._stopping
    finally:release.set()
    settle(app,lambda:dialog._task is None)
    assert "已保存" in dialog.status.text() and not dialog.isVisible()
    assert page._form_baseline==page._payload()


def test_task_practice_stop_uses_real_service_and_retains_saved_set(exam_desk,setup,tmp_path,monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import exam_followup_panel as module
    dialog,app,_=exam_desk
    engine,state,words,*_=setup;key=_add_word(engine,words)
    engine.facade._reader_stop_event=Event();engine.facade.basket=state.basket
    dialog.facade=engine.facade
    from integrations.deeptutor_shchem_v1.desktop_exam_practice import freeze_selection
    task=task_for(dialog)
    task=freeze_selection(task,state.basket(),[key])
    panel=dialog.followup_panel;assert panel.store(task)
    before=deepcopy(dialog.followups);factory=module.paper_session;marker=tmp_path/"task-started"
    def session(facade,selected):
        value=factory(facade,selected)
        for name in ("_core_bundle_builder","_docx_builder","_word_validator","_core_context"):
            setattr(value.service,name,getattr(engine,name))
        value.service._pagination_builder=lambda *a:slow_process(marker)
        return value
    monkeypatch.setattr(module,"paper_session",session)
    dialog.tabs.setCurrentWidget(panel);dialog.resize(800,700);settle(app)
    panel.preview_button.click();settle(app,marker.exists)
    controls={"status":dialog.status,"stop":dialog.stop_button,"return":dialog.close_button}
    capture(dialog,"practice-800-running",controls)
    dialog.stop_button.click();capture(dialog,"practice-800-stopping",controls)
    settle(app,lambda:dialog._task is None)
    assert dialog.followups==before and panel.preview is None and panel.preview_button.isEnabled()
    assert "取消" in dialog.status.text()
    capture(dialog,"practice-800-cancelled",controls)


def test_qt_late_stop_after_commit_does_not_drop_success(exam_desk):
    dialog,app,_=exam_desk
    committed=Event();release=Event();results=[]
    def work(report,cancelled):
        with publication_guard():committed.set()
        assert release.wait(8)
        return "committed"
    dialog.run("合成提交竞争",work,results.append)
    try:
        settle(app,committed.is_set)
        assert dialog.tasks.cancel(dialog._task) is False
    finally:release.set()
    settle(app,lambda:dialog._task is None)
    assert results==["committed"]


def test_external_stop_keeps_billing_notice_without_provider_call(exam_desk,tmp_path):
    dialog,app,_=exam_desk;marker=tmp_path/"local-surrogate"
    dialog.run("合成外部任务状态",lambda *a:slow_process(marker),lambda value:pytest.fail("cancelled result published"),external_request=True)
    settle(app,marker.exists);dialog.stop_button.click()
    assert "仍可能计费" in dialog.status.text()
    settle(app,lambda:dialog._task is None)
    assert "仍可能计费" in dialog.status.text()

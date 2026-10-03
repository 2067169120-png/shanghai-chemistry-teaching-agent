import time
from pathlib import Path

import pytest
from pypdf import PdfWriter
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_teacher_workspace import (
    TeacherWorkspaceStore,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    ROUTE_ORDER,
    TeacherWorkbenchWindow,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_import_dialog import (
    TextbookImportDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import (
    TextbookSourceDialog,
)

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def settle(app, predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def test_seven_routes_preserve_editor_inputs_and_legacy_deep_links(app, tmp_path):
    facade = build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=tmp_path))
    window = TeacherWorkbenchWindow(facade)
    window.show()
    try:
        window.preparation_page.topic.setText("未保存的原课题")
        window.preparation_page.materials.setPlainText("原材料保留")
        for route in (*ROUTE_ORDER, "templates", "classroom", "mywork", "tasks"):
            window.navigate(route)
            app.processEvents()
            assert window.stack.currentWidget() is window.pages[route]
        assert window.preparation_page.topic.text() == "未保存的原课题"
        assert window.preparation_page.materials.toPlainText() == "原材料保留"
        window.workspace_store.save_context(class_label="另一班")
        assert window.preparation_page.topic.text() == "未保存的原课题"
        window.navigate("textbooks")
        window.resize(360, 560)
        settle(app, lambda: not window.textbook_page._loading)
        assert window.width() == 360
        assert window.textbook_page.width() <= window.stack.width()
        assert window.textbook_page.compact_actions.isVisible()
        assert window.textbook_page.compact_actions.height() >= 30
        assert window.textbook_page.tree.height() >= 80
        assert window.textbook_page.evidence.height() >= 80
        window.textbook_page.query.setText("周期律")
        assert window.textbook_page.tree.topLevelItemCount() >= 1
        assert facade.textbook_workspace().candidate_catalog()["imported"] is False
    finally:
        window.preparation_page.topic.clear()
        window.preparation_page.materials.clear()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_task_bridge_records_safe_origin_success_and_restart_recovery(app, tmp_path):
    store = TeacherWorkspaceStore(DesktopStateStore(tmp_path))
    store.save_context(class_label="原班级")
    bridge = DesktopTaskBridge(history_store=store, route_provider=lambda: "library")
    try:
        identity = bridge.submit("读取资料", lambda: {"api_key": "never-persist-this-result"})
        store.save_context(class_label="另一个班级")
        settle(app, lambda: bridge.records()[0]["status"] == "completed")
        row = store.snapshot()["tasks"][0]
        assert row["task_id"] == identity
        assert row["context"]["class_label"] == "原班级"
        assert row["route"] == "library"
        assert "never-persist" not in store.state.path.read_text()
        store.start("interrupted", "上次导入", route="textbooks")
    finally:
        bridge.shutdown(1000)
    reopened = DesktopTaskBridge(history_store=store)
    try:
        assert next(row for row in reopened.records() if row["task_id"] == "interrupted")["status"] == "interrupted"
    finally:
        reopened.shutdown(1000)


def test_whole_book_preview_save_reopen_actual_qt_pdf_view(app, tmp_path):
    original = tmp_path / "教材.pdf"
    writer = PdfWriter()
    writer.add_blank_page(300, 420)
    writer.add_blank_page(300, 420)
    writer.write(original)
    facade = build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=tmp_path / "state"))
    bridge = DesktopTaskBridge()
    dialog = TextbookImportDialog(facade, bridge)
    try:
        dialog.files.file_list._append_paths([str(original)])
        dialog._preview()
        settle(app, lambda: dialog.task_id is None)
        assert dialog.preview["sources"][0]["page_count"] == 2
        dialog._save()
        settle(app, lambda: dialog.task_id is None)
        assert "已保存1份" in dialog.status.text()
        book = facade.textbook_workspace().books()[0]
        reader = TextbookSourceDialog(facade, {"concept_id": book["source_id"], "revision": book["source_sha256"]},
            tasks=bridge, reading_mode="book")
        reader.show()
        try:
            settle(app, lambda: reader._loaded_pages)
            assert reader.document.pageCount() == 2
            assert reader.excerpt_button.isHidden()
            reader.next.click()
            assert reader.view.pageNavigator().currentPage() == 1
        finally:
            reader.reject()
    finally:
        dialog.reject()
        bridge.shutdown(1000)
        facade.shutdown()


def test_grading_overview_returns_the_selected_stable_batch_id(app):
    from types import SimpleNamespace

    from integrations.deeptutor_shchem_v1.desktop_workbench.grading_page import (
        GradingPage,
    )
    bridge = DesktopTaskBridge()
    page = GradingPage(SimpleNamespace(), bridge)
    page.resize(248, 410)
    page.show()
    app.processEvents()
    try:
        assert page.table.height() >= 200
        page._ready([{"batch_id": "FIRST", "title": "作业一", "class_label": "班级A", "members": []},
            {"batch_id": "SECOND", "title": "作业二", "class_label": "班级B", "members": []}])
        requests = []
        page.batch_requested.connect(requests.append)
        page.table.selectRow(1)
        page._request_batch()
        assert requests == ["SECOND"]
    finally:
        page.close()
        bridge.shutdown(1000)

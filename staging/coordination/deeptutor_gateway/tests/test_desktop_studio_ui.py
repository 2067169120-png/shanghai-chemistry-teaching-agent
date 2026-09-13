"""Native integration contracts on isolated real personal storage, no API calls."""
import os
import time
from pathlib import Path
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow, ALL_ROUTES, ROUTE_ORDER
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_navigation import CommandPalette

ROOT = Path(__file__).resolve().parents[4]


def settle(app, predicate=lambda: True):
    end = time.monotonic() + 15
    for _ in range(5):
        app.processEvents()
        time.sleep(.02)
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.02)
    assert predicate()


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    facade = build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=tmp_path))
    value = TeacherWorkbenchWindow(facade)
    value.show()
    settle(app, lambda: not value.home_page._loading)
    yield value, app
    value.close()
    value.deleteLater()
    app.processEvents()


def test_all_routes_keep_existing_primary_shortcuts(window):
    win, app = window
    assert len(ROUTE_ORDER) == 5 and len(ALL_ROUTES) == 8
    for route in ALL_ROUTES:
        win.navigate(route)
        settle(app)
        assert win.stack.currentWidget() is win.pages[route]
    assert win.primary_navigation_labels == ("首页", "题库", "组卷", "学生分析", "备课")


def test_template_ui_preserves_materials_and_blocks_concurrent_edit(window):
    win, _ = window
    prep = win.preparation_page
    prep.topic.setText("教师原课题")
    prep.materials.setPlainText("原题公共材料")
    prep.objective.setPlainText("教师自定目标")
    assert prep.apply_studio_template("unit", "另一个课题", "高二")
    assert prep.topic.text() == "教师原课题"
    assert prep.materials.toPlainText() == "原题公共材料"
    assert prep.objective.toPlainText() == "教师自定目标"
    assert "单元整体备课" in prep.template_detail.text()
    before = prep._payload()
    prep._word_import_in_flight = True
    assert not prep.apply_studio_template("concept")
    assert not prep.append_classroom_feedback("暂不追加")
    assert prep._payload() == before
    prep._word_import_in_flight = False


def test_template_gallery_search_favorite_roundtrip(window):
    win, _ = window
    page = win.template_page
    page.query.setText("错因")
    assert len(page.cards) == 1
    page._favorite("feedback", True)
    page.only_favorites.setChecked(True)
    assert len(page.cards) == 1
    assert win.facade.state_store.snapshot()["studio"]["favorites"] == ["feedback"]
    page._favorite("feedback", False)
    assert len(page.cards) == 0


def test_command_search_routes_without_running_generation(window):
    win, _ = window
    dialog = CommandPalette(win)
    dialog.query.setText("分组")
    assert dialog.results.count() == 1
    dialog.choose()
    assert dialog.command == "classroom"
    dialog.deleteLater()


def test_participation_resets_on_roster_change(window):
    win, _ = window
    panel = win.classroom_page.participation
    panel.roster.setPlainText("01\n02\n03")
    drawn = set()
    for _ in range(3):
        panel.draw()
        drawn.add(panel.draw_display.text())
    assert drawn == {"01", "02", "03"}
    panel.draw()
    assert "全部抽完" in panel.status.text()
    panel.group_count.setValue(2)
    panel.group()
    assert "第2组" in panel.groups.toPlainText()
    panel.roster.setPlainText("04\n04")
    assert not panel.groups.toPlainText()
    panel.draw()
    assert "重复" in panel.status.text()
    assert "04" not in str(win.facade.state_store.snapshot())


def test_feedback_flows_back_to_preparation_without_replacing_sources(window):
    win, _ = window
    prep = win.preparation_page
    prep.materials.setPlainText("原始教学材料")
    feedback = win.classroom_page.feedback
    with pytest.raises(ValueError):
        feedback.report()
    feedback.question.setText("合成检测：正逆速率的关系")
    feedback.counts[0][1].setValue(12)
    feedback.counts[1][1].setValue(3)
    feedback.notes.setPlainText("合成教师记录")
    feedback.send()
    assert prep.materials.toPlainText().startswith("原始教学材料")
    assert "正逆速率的关系" in prep.materials.toPlainText()
    assert "教师手动汇总" in prep.materials.toPlainText()
    assert win.stack.currentWidget() is prep
    assert not win.facade.state_store.snapshot()["drafts"]


def test_saved_template_brief_is_found_by_my_work(window):
    win, app = window
    prep = win.preparation_page
    assert prep.apply_studio_template("concept", "合成演示课", "高二")
    prep.materials.setPlainText("合成材料，不含真实题目。")
    receipt = win.facade.create_preparation_draft(prep._payload())
    win.navigate("mywork")
    settle(app, lambda: not win.my_work_page._loading)
    page = win.my_work_page
    assert page.results.count() == 1
    record = page.results.item(0).data(Qt.ItemDataRole.UserRole)
    assert record["kind"] == "draft" and record["value"]["draft_id"]
    page.query.setText("不存在的课题")
    assert page.results.count() == 0


def test_model_controls_and_timer_reset(window):
    win, app = window
    panel = win.classroom_page.equilibrium
    panel.initial.setValue(70)
    assert panel.canvas.a == 70 and panel.canvas.b == 30
    panel.toggle()
    settle(app, lambda: panel.elapsed > .1)
    assert panel.canvas.a + panel.canvas.b == pytest.approx(100)
    panel.reset()
    assert not panel.running and len(panel.canvas.trace) == 1
    timer = win.classroom_page.timer_panel
    timer.minutes.setValue(2)
    assert timer.display.text() == "02:00"
    timer.toggle()
    assert timer.clock.deadline is not None
    timer.reset(60)
    assert timer.display.text() == "01:00" and timer.clock.deadline is None

"""Regression cases discovered by inspecting actual Windows screenshots."""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog
from test_desktop_studio_ui import window, settle


def test_rebuilt_template_cards_are_hidden_immediately(window):
    win, app = window
    win.navigate("templates")
    settle(app)
    old = list(win.template_page.cards)
    assert any(card.isVisible() for card in old)
    win.template_page.rebuild()
    # Do not wait for DeferredDelete: widgets removed from a layout must
    # already be hidden during nested event processing and rapid filtering.
    assert all(card.isHidden() for card in old)
    assert len(win.template_page.cards) == 6


def test_equilibrium_chart_has_lines_not_filled_wedges(window):
    from integrations.deeptutor_shchem_v1.desktop_workbench.classroom_page import EquilibriumCanvas
    _, app = window
    canvas = EquilibriumCanvas()
    canvas.resize(800, 300)
    canvas.a, canvas.b = 25, 75
    canvas.trace = [(100, 0), (50, 50), (25, 75)]
    canvas.show()
    settle(app)
    image = canvas.grab().toImage()
    colored = 0
    left, right = int(image.width() * .56), int(image.width() * .93)
    top, bottom = int(image.height() * .2), int(image.height() * .8)
    for x in range(left, right):
        for y in range(top, bottom):
            c = image.pixelColor(x, y)
            colored += c.red() > 180 and 110 < c.green() < 200 and c.blue() < 120
    assert 0 < colored < (right - left) * (bottom - top) * .04
    canvas.close()
    canvas.deleteLater()


def test_my_work_reopens_the_requested_draft_without_rewriting_saved_state(window, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_draft_dialog import PreparationDraftDialog
    win, _ = window
    prep = win.preparation_page
    prep.apply_studio_template("concept", "较早的一份合成草稿", "高二")
    prep.materials.setPlainText("保留这份原材料。")
    win.facade.create_preparation_draft(prep._payload())
    target = win.facade.preparation_draft_options()[0]["draft_id"]
    prep.topic.setText("另一份合成草稿")
    prep.materials.setPlainText("另一份材料。")
    win.facade.create_preparation_draft(prep._payload())
    prep._form_baseline = prep._payload()
    before = win.facade.state_store.snapshot()
    def confirm(dialog):
        assert dialog.source.currentData()["draft_id"] == target
        assert dialog.selected["payload"]["topic"] == "较早的一份合成草稿"
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PreparationDraftDialog, "exec", confirm)
    prep._open_draft(target)
    assert prep.topic.text() == "较早的一份合成草稿"
    assert prep.materials.toPlainText() == "保留这份原材料。"
    assert win.facade.state_store.snapshot() == before


def test_missing_requested_draft_never_falls_back_to_another_record(window, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_draft_dialog import PreparationDraftDialog
    win, _ = window
    prep = win.preparation_page
    prep.apply_studio_template("concept", "仍然存在的合成草稿", "高二")
    prep.materials.setPlainText("当前原材料保持不变。")
    win.facade.create_preparation_draft(prep._payload())
    before_payload = prep._payload()
    before_state = win.facade.state_store.snapshot()
    def should_not_open(dialog):
        pytest.fail("A missing ID must not display an unrelated default draft")
    monkeypatch.setattr(PreparationDraftDialog, "exec", should_not_open)
    prep._open_draft("no-longer-in-current-list")
    assert "已不在当前草稿列表" in prep.status.text()
    assert prep._payload() == before_payload
    assert win.facade.state_store.snapshot() == before_state


def test_return_to_preparation_is_named_as_navigation_and_keeps_edits(window):
    win, _ = window
    prep = win.preparation_page
    prep.topic.setText("教师正在编辑的课题")
    prep.materials.setPlainText("尚未保存但应当保留的原材料")
    before = prep._payload()
    win.navigate("mywork")
    assert win.my_work_page.resume_button.text() == "返回备课继续编辑"
    win.my_work_page.resume_button.click()
    assert win.stack.currentWidget() is prep
    assert prep._payload() == before

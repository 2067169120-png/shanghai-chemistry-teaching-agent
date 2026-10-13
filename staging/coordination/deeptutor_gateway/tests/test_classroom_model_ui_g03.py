"""G03 contracts use the real Qt widget, with no personal state or network."""
from __future__ import annotations

import math

import pytest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QLabel, QScrollArea

from integrations.deeptutor_shchem_v1.desktop_workbench import classroom_page as module
from integrations.deeptutor_shchem_v1.desktop_workbench.classroom_page import (
    EquilibriumPanel,
    EquilibriumProjection,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import (
    WORKBENCH_STYLE,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import (
    install_ui_font,
)


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    install_ui_font(application)
    application.setStyleSheet(WORKBENCH_STYLE)
    return application


@pytest.fixture
def panel(app):
    value = EquilibriumPanel()
    value.timer.stop()
    yield value
    value.close()
    value.deleteLater()
    app.processEvents()


def test_default_quantities_units_true_time_and_fixed_axes(panel):
    assert panel.model.a0 == 1 and panel.model.b0 == 0
    assert panel.canvas.a + panel.canvas.b == 1
    assert "mol·L⁻¹" in panel.summary.text() and "s⁻¹" in panel.model_caption.text()
    assert "0–15 s" in panel.model_caption.text()
    panel.elapsed = 3
    panel.render()
    assert panel.canvas.trace[0].t == 0 and panel.canvas.trace[-1].t == 3
    assert panel.canvas.duration == 15
    assert panel.canvas.state.a == pytest.approx(panel.model.at(3).a)
    panel.elapsed = 7
    panel.render()
    assert panel.canvas.duration == 15 and panel.canvas.trace[-1].t == 7


def test_concentration_crossing_is_not_equilibrium_and_non_equal_equilibrium_is(panel):
    panel.elapsed = math.log(3) / .4
    panel.render()
    state = panel.canvas.state
    assert state.a == pytest.approx(state.b)
    assert state.forward_rate != pytest.approx(state.reverse_rate)
    assert not state.dynamic_equilibrium and not state.near_equilibrium
    assert "相交不代表平衡" in panel.criterion.text()
    panel.preset(.25, .75)
    assert panel.canvas.state.dynamic_equilibrium
    assert panel.canvas.a != panel.canvas.b
    assert "v正 = v逆 > 0" in panel.criterion.text()
    panel.elapsed = 10
    panel.render()
    assert panel.canvas.a == .25 and panel.canvas.b == .75
    assert panel.canvas.state.forward_rate == pytest.approx(.075)


@pytest.mark.parametrize("name,new", [("forward", .4), ("reverse", .2), ("initial", .8),
                                      ("initial_b", .2), ("duration", 30), ("speed", 2)])
def test_every_parameter_change_resets_time_and_history(panel, name, new):
    panel.running = True
    panel.elapsed = 5
    panel.render()
    getattr(panel, name).setValue(new)
    assert not panel.running and panel.elapsed == 0
    assert len(panel.canvas.trace) == 1 and panel.canvas.trace[0].t == 0
    assert panel.canvas.a == panel.initial.value() and panel.canvas.b == panel.initial_b.value()
    assert panel.start.text() == "开始"


def test_pause_continue_time_scale_and_observation_end_are_deterministic(panel, monkeypatch):
    clock = [100.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    panel.reset()
    panel.toggle()
    clock[0] += 2
    panel.tick()
    assert panel.elapsed == 2
    panel.toggle()
    before = panel.canvas.state
    clock[0] += 100
    panel.tick()
    assert panel.elapsed == 2 and panel.canvas.state == before
    panel.toggle()
    clock[0] += .25
    panel.tick()
    assert panel.elapsed == 2.25
    clock[0] += 50
    panel.tick()
    assert panel.elapsed == 15 and not panel.running and panel.start.text() == "重播"
    panel.toggle()
    assert panel.elapsed == 0 and panel.running
    panel.speed.setValue(2)
    panel.toggle()
    clock[0] += .5
    panel.tick()
    assert panel.elapsed == 1


def test_degenerate_cases_do_not_claim_dynamic_equilibrium(panel):
    panel.forward.setValue(0)
    panel.reverse.setValue(0)
    assert not panel.canvas.state.dynamic_equilibrium
    assert "组成保持初值" in panel.criterion.text()
    panel.forward.setValue(.3)
    assert "单向转化" in panel.criterion.text()
    panel.initial.setValue(0)
    assert "总浓度为零" in panel.criterion.text()
    assert not panel.canvas.state.near_equilibrium


def test_one_way_initial_terminal_is_stationary_without_dynamic_equilibrium(panel):
    panel.forward.setValue(0)
    assert panel.initial_b.value() == 0
    assert "单向终态" in panel.criterion.text() and "均为零" in panel.criterion.text()
    panel.elapsed = 15
    panel.render()
    assert panel.canvas.a == 1 and panel.canvas.b == 0
    assert not panel.canvas.state.near_equilibrium and not panel.canvas.state.dynamic_equilibrium


def test_finite_time_approach_is_explicitly_approximate(panel):
    panel.elapsed = 15
    panel.render()
    assert "接近平衡" in panel.criterion.text() and "1%" in panel.criterion.text()
    assert "渐近" in panel.criterion.text()
    assert not panel.canvas.state.dynamic_equilibrium


def test_reopening_panel_starts_from_defaults_without_state_or_profile(app):
    old = EquilibriumPanel()
    old.timer.stop()
    old.preset(.25, .75)
    old.duration.setValue(30)
    old.speed.setValue(3)
    old.elapsed = 12
    old.render()
    old.close()
    old.deleteLater()
    app.processEvents()
    fresh = EquilibriumPanel()
    fresh.timer.stop()
    assert fresh.elapsed == 0 and not fresh.running
    assert fresh.model.a0 == 1 and fresh.model.b0 == 0
    assert fresh.model.kf == .3 and fresh.model.kr == .1
    assert fresh.duration.value() == 15 and fresh.speed.value() == 1
    fresh.close()
    fresh.deleteLater()


def test_projection_is_large_readable_and_keeps_live_state_on_reopen(panel, app):
    panel.elapsed = 3
    panel.render()
    dialog = EquilibriumProjection(panel)
    dialog.resize(1600, 900)
    dialog.show()
    app.processEvents()
    assert dialog.summary.font().pointSizeF() >= 22
    assert dialog.parameters.font().pointSizeF() >= 20
    assert not dialog.findChildren(QDoubleSpinBox)
    first, second = dialog.canvas.plot_rects()
    assert first.right() < second.left()
    assert dialog.start.isVisible() and dialog.start.geometry().bottom() < dialog.height()
    panel.preset(.25, .75)
    assert dialog.canvas.state.dynamic_equilibrium and "动态平衡" in dialog.criterion.text()
    for label in dialog.findChildren(QLabel):
        assert not any(word in label.text() for word in ("非真实", "待核实", "不是实际"))
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
    again = EquilibriumProjection(panel)
    assert again.canvas.state.a == .25 and again.canvas.state.b == .75
    again.close()
    again.deleteLater()


def test_small_projection_scrolls_plots_but_controls_stay_visible(panel, app):
    dialog = EquilibriumProjection(panel)
    dialog.resize(720, 600)
    dialog.show()
    app.processEvents()
    scroll = dialog.findChild(QScrollArea)
    assert scroll.verticalScrollBar().maximum() > 0
    assert dialog.start.isVisible()
    assert dialog.start.mapTo(dialog, dialog.start.rect().bottomRight()).y() < dialog.height()
    dialog.close()
    dialog.deleteLater()


def test_narrow_parameters_reflow_instead_of_clipping(panel, app):
    panel.resize(420, 1100)
    panel.settings.toggle.setChecked(True)
    panel.settings._set_expanded(True)
    panel.show()
    app.processEvents()
    assert panel._parameter_columns == 1
    for _label, field in panel.parameter_rows:
        assert field.isVisible() and field.mapTo(panel, field.rect().bottomRight()).x() < panel.width()


def test_model_snapshot_images_are_real_qt_and_preserve_two_quantities(panel, app, tmp_path):
    panel.resize(1150, 800)
    panel.elapsed = 15
    panel.render()
    panel.show()
    app.processEvents()
    target = tmp_path / "g03-approach.png"
    assert panel.grab().save(str(target)) and target.stat().st_size > 1000
    assert panel.grab().toImage().pixelColor(0, 0).alpha() == 255
    assert len(panel.canvas.plot_rects()) == 2

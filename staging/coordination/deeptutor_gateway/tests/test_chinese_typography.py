"""Chinese UI family matching, real glyph inspection and native controls."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QLineEdit, QPlainTextEdit
from PySide6.QtGui import QFont, QFontInfo
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import (
    BODY_POINTS, HAN_SAMPLE, choose_family, install_ui_font, ui_font,
    typography_report, glyph_report,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application

@pytest.fixture
def app():
    return create_application(["typography-test"])


def test_priority_is_installed_chinese_face_not_english_default():
    available = ["Segoe UI", "Noto Sans CJK SC", "Microsoft YaHei"]
    assert choose_family(available, lambda _: True) == "Microsoft YaHei"


def test_missing_preferred_name_does_not_invent_an_installed_font():
    assert choose_family(["Segoe UI", "Arial"], lambda _: True) is None


def test_family_is_checked_for_actual_chinese_glyphs():
    calls = []
    def support(name):
        calls.append(name)
        return name == "Noto Sans CJK SC"
    assert choose_family(["Microsoft YaHei", "Noto Sans CJK SC"], support) == "Noto Sans CJK SC"
    assert calls == ["Microsoft YaHei", "Noto Sans CJK SC"]


def test_family_alias_matching_is_case_insensitive():
    assert choose_family(["microsoft yahei"], lambda _: True) == "microsoft yahei"


def test_font_installation_is_idempotent_and_does_not_reset_controls(app):
    first = install_ui_font(app)
    before = app.font().toString()
    assert install_ui_font(app) == first
    assert app.font().toString() == before
    assert app.font().pointSizeF() == BODY_POINTS


@pytest.mark.parametrize("kind", [QLabel, QPushButton, QLineEdit, QPlainTextEdit])
def test_widgets_share_verified_family_after_stylesheet_polish(app, kind):
    widget = kind()
    widget.ensurePolished()
    assert widget.font().families()[0] == app._shchem_typography["family"]
    assert widget.font().pointSizeF() >= 10.5
    record = glyph_report(widget.font(), HAN_SAMPLE)
    assert record["runs"] and record["missing_glyphs"] == 0
    widget.deleteLater()


def test_captions_are_not_eleven_pixel_text(app):
    widget = QLabel("上海高中化学")
    widget.setObjectName("BrandSub")
    widget.ensurePolished()
    assert QFontInfo(widget.font()).pixelSize() >= 12
    assert widget.font().pointSizeF() >= 9
    widget.deleteLater()


def test_custom_painters_and_reader_font_do_not_reselect_english(app):
    font = ui_font(12)
    assert font.families() == app.font().families()
    assert font.pointSizeF() == 12
    assert glyph_report(font, HAN_SAMPLE)["missing_glyphs"] == 0


def test_chemistry_symbols_remain_available_without_disabling_font_merging(app):
    details = typography_report()
    assert details["han"]["missing_glyphs"] == 0
    assert details["chemistry"]["missing_glyphs"] == 0
    assert not (ui_font().styleStrategy() & QFont.StyleStrategy.NoFontMerging)
    assert all(word not in str(details) for word in ("AppData", "site-packages", "api_key"))


def test_stylesheet_does_not_override_family_selected_by_qt():
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
    assert "font-family:" not in WORKBENCH_STYLE
    assert "font-size: 11px" not in WORKBENCH_STYLE


def test_diagnostics_reports_actual_glyphs_without_loading_credentials(tmp_path, app):
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_workbench.environment_dialog import EnvironmentDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    from test_desktop_onboarding_ui import settle
    tasks = DesktopTaskBridge()
    dialog = EnvironmentDialog(DesktopPaths.from_workspace(tmp_path, state_root=tmp_path/"state"),tasks)
    try:
        dialog.show()
        settle(app, lambda: dialog.report is not None)
        assert dialog.report["typography"]["han"]["missing_glyphs"] == 0
        assert "中文样例实际字形" in dialog.output.toPlainText()
        assert dialog.report["network_requests"] == 0
        assert str(tmp_path) not in str(dialog.report)
    finally:
        dialog.close()
        tasks.shutdown()

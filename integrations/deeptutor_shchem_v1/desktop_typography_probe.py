"""Synthetic typography evidence from real widgets; never exports font files."""
from __future__ import annotations
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
from .desktop_workbench.typography import (
    HAN_SAMPLE, CHEMISTRY_SAMPLE, glyph_report, typography_report,
)


def exercise(window, settle, capture):
    from .desktop_workbench.environment_dialog import EnvironmentDialog
    page = window.preparation_page
    original = page._payload()
    panel = QWidget(window)
    panel.setWindowTitle("中文界面显示检查")
    panel.setWindowFlag(Qt.WindowType.Dialog)
    layout = QVBoxLayout(panel)
    labels = {}
    for role, text, name in (
        ("heading", "上海高中化学 · 教师工作台", "CardTitle"),
        ("body", "保存草稿  试卷预览  教学要求  备课资料  进度与结果", ""),
        ("small", "知识点、教材章节、考试类型与原始出处分别保留。", "BrandSub"),
        ("chemistry", CHEMISTRY_SAMPLE, ""),
    ):
        label = QLabel(text)
        label.setObjectName(name)
        label.setWordWrap(True)
        layout.addWidget(label)
        labels[role] = label
    entry = QLineEdit("氢氯钠铁铜锂氧碳硫 · 第1题至第12题")
    layout.addWidget(entry)
    labels["input"] = entry
    button = QPushButton("保存草稿")
    layout.addWidget(button)
    labels["button"] = button
    panel.resize(880, 340)
    panel.show()
    settle()
    try:
        report = typography_report(panel)
        assert report["chinese_sample_supported"]
        assert report["han"]["missing_glyphs"] == 0
        assert report["chemistry"]["missing_glyphs"] == 0
        report["widgets"] = {name: glyph_report(widget.font(), CHEMISTRY_SAMPLE if name == "chemistry" else HAN_SAMPLE)
                             for name, widget in labels.items()}
        assert all(not r["missing_glyphs"] for r in report["widgets"].values())
        assert all(r["requested"][0] == report["family"] for r in report["widgets"].values())
        capture(panel, "typography-sample.png")
    finally:
        panel.close()
        panel.deleteLater()
    diagnostic = EnvironmentDialog(window.facade.paths, window.tasks, window)
    diagnostic.show()
    try:
        settle(lambda: diagnostic.report is not None)
        assert diagnostic.report["typography"]["han"]["missing_glyphs"] == 0
        capture(diagnostic, "typography-check.png")
    finally:
        diagnostic.close()
        diagnostic.deleteLater()
    for route in ("home", "preparation", "mywork"):
        window.navigate(route)
        window.resize(1360, 920)
        settle()
        capture(window, "typography-" + route + ".png")
    window.navigate("preparation")
    window.resize(800, 700)
    page.editor_scroll.verticalScrollBar().setValue(page.editor_scroll.verticalScrollBar().maximum())
    settle()
    for button in (page.save_button, page.generate_button, page.workspace.materials, page.workspace.results):
        point = button.mapTo(page, QPoint(0, 0))
        assert button.isVisible() and 0 <= point.y() and point.y() + button.height() <= page.height()
    capture(window, "typography-compact.png")
    assert page._payload() == original
    report.update(editor_unchanged=True, actions_visible_800x700=True, model_calls=0)
    return report


def run_probe(output):
    """Explicit CI entry. Isolated state, native platform supplied by the caller."""
    import hashlib
    import json
    import os
    import platform
    import sys
    import tempfile
    import time
    from pathlib import Path
    from .desktop_paths import DesktopPaths
    from .desktop_facade import build_default_facade
    from .desktop_version import DESKTOP_VERSION
    from .desktop_workbench.app import create_application
    from .desktop_workbench.main_window import TeacherWorkbenchWindow
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    app = create_application(["verify-typography"])
    errors, screenshots = [], []
    old_hook = sys.excepthook
    def failed(kind, value, tb):
        import traceback
        traceback.print_exception(kind, value, tb)
        errors.append(kind.__name__)
    sys.excepthook = failed
    def settle(condition=lambda: True):
        deadline = time.monotonic() + 20
        for _ in range(5):
            app.processEvents()
            time.sleep(.03)
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.03)
        assert condition() and not errors, errors
    def capture(widget, name):
        settle()
        path = output / name
        pixmap = widget.grab()
        assert pixmap.save(str(path))
        screenshots.append({"file": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "physical_pixels": [pixmap.width(), pixmap.height()],
                            "logical_size": [widget.width(), widget.height()],
                            "device_pixel_ratio": widget.devicePixelRatioF()})
    try:
        with tempfile.TemporaryDirectory(prefix="shchem-fonts-") as temporary:
            workspace = DesktopPaths.discover(Path(__file__)).workspace_root
            facade = build_default_facade(DesktopPaths.from_workspace(workspace, state_root=temporary))
            window = TeacherWorkbenchWindow(facade)
            window.show()
            try:
                settle(lambda: not window.home_page._loading)
                report = exercise(window, settle, capture)
            finally:
                window.close()
                app.processEvents()
        report.update(version=DESKTOP_VERSION, platform=platform.platform(),
                      frozen=bool(getattr(sys, "frozen", False)),
                      source_commit=os.environ.get("GITHUB_SHA", "local"),
                      requested_scale=os.environ.get("QT_SCALE_FACTOR", "system"),
                      screenshots=screenshots, uncaught_errors=errors,
                      scaling_scope="Qt scale-factor simulation on the recorded native backend; not OS multi-monitor acceptance.")
        (output / "typography-probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        sys.excepthook = old_hook

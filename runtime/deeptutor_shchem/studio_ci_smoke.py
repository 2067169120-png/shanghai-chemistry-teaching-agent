"""Exercise real native screens with isolated, explicitly synthetic classroom data."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    from PySide6.QtCore import qVersion
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
    from integrations.deeptutor_shchem_v1.desktop_version import DESKTOP_VERSION
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow, ALL_ROUTES
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog, SettingsDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_progress_dialog import LibraryProgressDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_templates import TemplatePreviewDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_navigation import CommandPalette, HelpDialog

    output = ROOT / "studio-qa"
    output.mkdir(exist_ok=True)
    errors, screenshots, actions = [], [], []
    def caught(kind, value, tb):
        errors.append(kind.__name__ + ": " + str(value))
        traceback.print_exception(kind, value, tb)
    sys.excepthook = caught
    app = create_application(["studio-ci-smoke"])

    def settle(predicate=lambda: True, timeout=20):
        end = time.monotonic() + timeout
        for _ in range(8):
            app.processEvents()
            time.sleep(.05)
        while not predicate() and time.monotonic() < end:
            app.processEvents()
            time.sleep(.05)
        assert predicate(), "Native UI did not settle"
        assert not errors, errors

    def capture(widget, name):
        settle()
        assert widget.isVisible() and widget.isEnabled()
        image = widget.grab()
        target = output / (name + ".png")
        assert image.save(str(target))
        screenshots.append({"file": target.name, "width": image.width(), "height": image.height(),
                            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})

    with tempfile.TemporaryDirectory(prefix="shchem-studio-ci-") as state:
        facade = build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=state))
        window = TeacherWorkbenchWindow(facade)
        window.setWindowTitle("沪上化学智研台 · 合成演示，不含用户资料")
        window.resize(1360, 940)
        window.show()
        try:
            settle(lambda: not window.home_page._loading)
            prep = window.preparation_page
            assert prep.apply_studio_template("concept", "化学平衡的建立与判据（演示）", "高二 · 合成演示")
            prep.materials.setPlainText("合成演示材料：观察封闭一级可逆A⇌B模型中相对浓度和正逆速率随时间的变化。\n本例未引用真实教材页或题库身份；正式备课请带入实际教材和完整原题。")
            facade.create_preparation_draft(prep._payload())
            facade.state_store.save_studio_favorites(["concept", "experiment"])
            window.template_page.rebuild()
            actions.append("Apply template to original preparation form and save one real local draft containing synthetic material")
            window.home_page.welcome.prompt.setText("化学平衡：为什么平衡不等于停止？")
            for route in ALL_ROUTES:
                window.navigate(route)
                if route == "mywork":
                    settle(lambda: not window.my_work_page._loading)
                    assert window.my_work_page.results.count() == 1
                settle()
                assert window.stack.currentWidget() is window.pages[route]
                capture(window, route)
            tools = window.classroom_page
            window.navigate("classroom")
            tools.tabs.setCurrentIndex(1)
            tools.participation.class_size.setValue(36)
            tools.participation.number_roster()
            tools.participation.group_count.setValue(6)
            tools.participation.group()
            tools.participation.draw()
            capture(window, "classroom-participation")
            tools.tabs.setCurrentIndex(2)
            tools.equilibrium.toggle()
            settle(lambda: tools.equilibrium.elapsed >= 4, timeout=10)
            tools.equilibrium.toggle()
            assert 25 < tools.equilibrium.canvas.a < 100
            capture(window, "classroom-equilibrium")
            tools.tabs.setCurrentIndex(3)
            feedback = tools.feedback
            feedback.question.setText("解释动态平衡时正逆反应速率的关系（合成演示）")
            for (_, spin), n in zip(feedback.counts, (26, 8, 2)):
                spin.setValue(n)
            feedback.notes.setPlainText("合成记录：下节先比较“速率相等”与“浓度相等”；这不是实际学生测评结果。")
            capture(window, "classroom-feedback")
            original = prep.materials.toPlainText()
            feedback.send()
            assert prep.materials.toPlainText().startswith(original)
            assert "教师手动汇总" in prep.materials.toPlainText()
            actions.append("Teacher-entered synthetic feedback appended to original preparation materials without model invocation")
            window.navigate("home")
            for name, factory in (
                ("import", lambda: ImportDialog(facade, window.tasks, window)),
                ("settings", lambda: SettingsDialog(facade, window.tasks, window)),
                ("progress", lambda: LibraryProgressDialog(facade, window.tasks, window)),
                ("template-preview", lambda: TemplatePreviewDialog("concept", window, topic="化学平衡（演示）")),
                ("commands", lambda: CommandPalette(window)),
                ("help", lambda: HelpDialog(window)),
            ):
                dialog = factory()
                dialog.show()
                if name == "progress":
                    settle(lambda: dialog.report is not None)
                if name == "commands":
                    dialog.query.setText("课")
                capture(dialog, name)
                dialog.close()
                settle()
                dialog.deleteLater()
            window.resize(800, 700)
            window.navigate("templates")
            capture(window, "templates-compact")
            assert not errors
            report = {"version": DESKTOP_VERSION, "platform": platform.platform(),
                      "python": platform.python_version(), "qt": qVersion(),
                      "qt_platform": os.environ["QT_QPA_PLATFORM"],
                      "source_commit": os.environ.get("GITHUB_SHA", "local"),
                      "routes_opened": list(ALL_ROUTES), "actions": actions,
                      "screenshots": screenshots, "uncaught_errors": errors,
                      "scope": "Native Windows Qt source navigation and selected offline interactions. Empty original library; one saved synthetic draft and teacher-entered synthetic classroom data. No API call, knowledge distillation, private-material import, packaged EXE or classroom-effectiveness acceptance."}
            (output / "smoke-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

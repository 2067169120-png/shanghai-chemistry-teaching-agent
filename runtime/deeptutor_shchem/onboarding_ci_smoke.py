"""Capture native settings/readiness evidence on the machine running this script.

No synthesized 'expected screenshot': the workflow must execute Qt successfully.
Temporary personal state and a fake metadata-only provider are isolated from any
teacher account. This does not test a frozen executable or a real model.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time
import traceback

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
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import SettingsDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.environment_dialog import EnvironmentDialog
    output = ROOT / "readiness-qa"
    output.mkdir(exist_ok=True)
    errors = []
    def failed(kind, value, tb):
        errors.append(kind.__name__)
        traceback.print_exception(kind, value, tb)
    sys.excepthook = failed
    app = create_application(["onboarding-ci"])
    captures = []
    def settle(condition=lambda: True):
        end = time.monotonic() + 20
        for _ in range(4):
            app.processEvents(); time.sleep(.03)
        while not condition() and time.monotonic() < end:
            app.processEvents(); time.sleep(.03)
        assert condition() and not errors, errors
    def capture(widget, name):
        settle()
        path = output / name
        assert widget.grab().save(str(path))
        captures.append({"file": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    with tempfile.TemporaryDirectory(prefix="shchem-readiness-") as temporary:
        facade = build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=temporary))
        window = TeacherWorkbenchWindow(facade)
        window.show()
        try:
            settle(lambda: not window.home_page._loading)
            for route in ALL_ROUTES:
                window.navigate(route); settle()
                assert window.stack.currentWidget() is window.pages[route]
            settings = SettingsDialog(facade, window.tasks, window)
            settings.show()
            settle(lambda: settings._active_task_id is None)
            assert settings.advanced.content.isHidden()
            capture(settings, "settings-basic.png")
            settings.advanced.toggle.click()
            settle()
            settings.scroll.ensureWidgetVisible(settings.max_output_tokens)
            capture(settings, "settings-advanced.png")
            settings.resize(480, 680)
            settle()
            capture(settings, "settings-compact.png")
            settings.close()
            settings.deleteLater()
            diagnostic = EnvironmentDialog(facade.paths, window.tasks, window)
            diagnostic.show()
            settle(lambda: diagnostic.report is not None)
            assert diagnostic.report["network_requests"] == 0
            capture(diagnostic, "environment.png")
            (output / "environment.json").write_text(json.dumps(diagnostic.report, ensure_ascii=False, indent=2), encoding="utf-8")
            diagnostic.close()
            diagnostic.deleteLater()
            settle()
            from integrations.deeptutor_shchem_v1.desktop_work_organization_probe import exercise
            window.resize(1360, 900)
            organization = exercise(window, settle, lambda widget, name: capture(widget, "source-" + name))
            from integrations.deeptutor_shchem_v1.desktop_backup_probe import exercise as backup_exercise
            # A restored personal profile, like the original one, lives outside
            # the source checkout. Only its synthetic evidence is copied to QA.
            with tempfile.TemporaryDirectory(prefix="shchem-backup-proof-") as demo:
                backup = backup_exercise(window, settle, lambda widget, name: capture(widget, "source-" + name), demo)
                shutil.copytree(demo, output / "backup-demo", dirs_exist_ok=True)
            from integrations.deeptutor_shchem_v1.desktop_teacher_desk_probe import exercise as desk_exercise
            desk = desk_exercise(window, settle, lambda widget, name: capture(widget, "source-" + name), output / "desk-demo")
            from integrations.deeptutor_shchem_v1.desktop_paper_numbering_probe import exercise as paper_exercise
            paper = paper_exercise(window, settle, lambda widget, name: capture(widget, "source-" + name), output / "numbering-demo")
            from integrations.deeptutor_shchem_v1.desktop_typography_probe import exercise as font_exercise
            typography = font_exercise(window, settle, lambda widget, name: capture(widget, "source-" + name))
        finally:
            window.close(); app.processEvents()
        report = {"version": DESKTOP_VERSION, "source_commit": os.environ.get("SHCHEM_SOURCE_SHA", os.environ.get("GITHUB_SHA", "local")),
                  "platform": platform.platform(), "python": platform.python_version(), "qt": qVersion(),
                  "routes_opened": list(ALL_ROUTES), "screenshots": captures, "uncaught_errors": errors,
                  "work_organization": organization, "lesson_backup": backup, "teacher_desk": desk, "paper_numbering": paper, "typography": typography,
                  "scope": "Native source work organization, settings and local dependency checks, isolated empty state. No real API, private teaching material, packaged EXE or classroom acceptance."}
        assert not errors
        (output / "onboarding-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

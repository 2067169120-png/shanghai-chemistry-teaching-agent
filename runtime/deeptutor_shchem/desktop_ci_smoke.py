"""Windows/Qt first-run smoke. Uses an isolated, empty personal data directory."""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
    from integrations.deeptutor_shchem_v1.desktop_version import DESKTOP_VERSION
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow, ROUTE_ORDER

    output = ROOT / "desktop-qa"
    output.mkdir(exist_ok=True)
    errors = []
    sys.excepthook = lambda kind, value, tb: errors.append(kind.__name__)
    app = create_application(["desktop-ci-smoke"])

    def settle(predicate=lambda: True, timeout=20):
        end = time.monotonic() + timeout
        for _ in range(6):
            app.processEvents()
            time.sleep(0.05)
        while not predicate() and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.05)
        assert predicate(), "Native UI did not finish within the smoke timeout"

    with tempfile.TemporaryDirectory(prefix="shchem-ci-") as state:
        paths = DesktopPaths.from_workspace(ROOT, state_root=state)
        facade = build_default_facade(paths)
        window = TeacherWorkbenchWindow(facade)
        window.resize(1280, 900)
        window.show()
        try:
            settle(lambda: not window.home_page._loading)
            for route in ROUTE_ORDER:
                window.navigate(route)
                settle()
                assert window.isVisible() and window.isEnabled()
                assert window.stack.currentWidget() is window.pages[route]
                assert window.grab().save(str(output / f"{route}-empty.png"))
            window.navigate("home")
            window.resize(800, 700)
            settle()
            assert window.grab().save(str(output / "home-compact.png"))
            try:
                from integrations.deeptutor_shchem_v1.desktop_workbench.library_progress_dialog import LibraryProgressDialog
            except ModuleNotFoundError:
                pass  # bootstrap branch before the dashboard change
            else:
                dialog = LibraryProgressDialog(facade, window.tasks, window)
                dialog.show()
                settle(lambda: dialog.report is not None)
                assert dialog.grab().save(str(output / "library-progress-empty.png"))
                (output / "library-progress-empty.json").write_text(
                    json.dumps(dialog.report, ensure_ascii=False, indent=2), encoding="utf-8")
                dialog.close()
            assert not errors, f"Uncaught Qt callback errors: {errors}"
            report = {"version": DESKTOP_VERSION, "platform": platform.platform(),
                      "python": platform.python_version(), "qt_platform": os.environ["QT_QPA_PLATFORM"],
                      "source_commit": os.environ.get("GITHUB_SHA", "local"),
                      "routes_opened": list(ROUTE_ORDER), "uncaught_errors": errors,
                      "scope": "Native Qt source startup/navigation, empty personal library; not real-material acceptance or packaged EXE."}
            (output / "smoke-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            window.close()
            app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

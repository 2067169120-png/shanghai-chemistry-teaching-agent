from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from integrations.deeptutor_shchem_v1.desktop_facade import PRIMARY_NAVIGATION

WORKSPACE = Path(__file__).resolve().parents[4]
INTEGRATION_ROOT = WORKSPACE / "integrations" / "deeptutor_shchem_v1"
RUNTIME_ROOT = WORKSPACE / "runtime" / "deeptutor_shchem"
ENTRY = RUNTIME_ROOT / "desktop_teacher_workbench.pyw"


def _desktop_sources() -> list[Path]:
    return sorted(
        [
            *INTEGRATION_ROOT.glob("desktop_*.py"),
            *(INTEGRATION_ROOT / "desktop_workbench").glob("*.py"),
            ENTRY,
        ]
    )


def test_primary_navigation_is_exactly_five_teacher_tasks() -> None:
    assert PRIMARY_NAVIGATION == ("首页", "题库", "组卷", "学生分析", "备课")
    assert len(PRIMARY_NAVIGATION) <= 7
    assert "导入资料" not in PRIMARY_NAVIGATION
    assert "设置" not in PRIMARY_NAVIGATION


def test_library_has_a_separate_personal_word_handout_scope() -> None:
    source = (INTEGRATION_ROOT / "desktop_workbench" / "library_page.py").read_text(
        encoding="utf-8"
    )
    facade = (INTEGRATION_ROOT / "desktop_facade.py").read_text(encoding="utf-8")
    assert "我的讲义（Word）" in source
    assert "visual_completion_queue" in source
    assert "search_personal_handouts" in source
    assert "PERSONAL_HANDOUT_SCOPE" in facade


def test_desktop_entry_and_sources_contain_no_browser_or_local_service_surface() -> (
    None
):
    entry_source = ENTRY.read_text(encoding="utf-8")
    assert "http" + "_app" not in entry_source
    assert "service.py" not in entry_source
    assert "server.py" not in entry_source
    assert "--windowed" not in entry_source

    forbidden = (
        "local" + "host",
        "bear" + "er",
        "pass" + "code",
        "o" + "cr fallback",
        "qweb" + "view",
        "qwebengine" + "view",
        "serve_" + "forever",
        "<ht" + "ml",
    )
    for path in _desktop_sources():
        folded = path.read_text(encoding="utf-8").casefold()
        for value in forbidden:
            assert value not in folded, f"{value!r} leaked into {path.name}"


def test_importing_desktop_facade_does_not_load_legacy_entry_modules() -> None:
    package = "integrations.deeptutor_shchem_v1."
    first = package + "http" + "_app"
    second = package + "launch" + "er"
    third = package + "serv" + "ice"
    fourth = "PySide6.QtWebEngine" + "Core"
    fifth = "PySide6.QtWebEngine" + "Widgets"
    script = (
        "import sys; "
        "import integrations.deeptutor_shchem_v1.desktop_facade; "
        f"assert {first!r} not in sys.modules; "
        f"assert {second!r} not in sys.modules; "
        f"assert {third!r} not in sys.modules; "
        f"assert {fourth!r} not in sys.modules; "
        f"assert {fifth!r} not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_background_bridge_is_qt_signal_and_thread_pool_based() -> None:
    source = (INTEGRATION_ROOT / "desktop_workbench" / "tasks.py").read_text(
        encoding="utf-8"
    )
    assert "QThreadPool" in source
    assert "QRunnable" in source
    assert "Signal" in source
    assert "threading.Event" in source


def test_windowed_entry_dependency_and_build_contract_are_present() -> None:
    requirements = (RUNTIME_ROOT / "desktop_requirements.txt").read_text(
        encoding="utf-8"
    )
    build = (RUNTIME_ROOT / "desktop_build.ps1").read_text(encoding="utf-8")
    assert "PySide6" in requirements
    assert "PyInstaller" in requirements
    assert "Pillow" in requirements
    assert "--windowed" in build
    assert "desktop_teacher_workbench.pyw" in build
    for module in (
        "integrations.deeptutor_shchem_v1.service",
        "integrations.deeptutor_shchem_v1.http_app",
        "integrations.deeptutor_shchem_v1.launcher",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ):
        assert f'"--exclude-module", "{module}"' in build


def test_desktop_import_surface_exposes_full_handout_batch_and_folder_selection() -> (
    None
):
    dialogs = (INTEGRATION_ROOT / "desktop_workbench" / "dialogs.py").read_text(
        encoding="utf-8"
    )
    components = (INTEGRATION_ROOT / "desktop_workbench" / "components.py").read_text(
        encoding="utf-8"
    )
    tasks = (INTEGRATION_ROOT / "desktop_workbench" / "tasks.py").read_text(
        encoding="utf-8"
    )
    assert "98 包 / 196 份" in dialogs
    assert "run_one_round_review_corpus_import" in dialogs
    assert "选择文件夹" in components
    assert "getExistingDirectory" in components
    assert "submit_progress" in tasks


def test_desktop_theme_is_application_wide_for_native_dialogs() -> None:
    app = (INTEGRATION_ROOT / "desktop_workbench" / "app.py").read_text(
        encoding="utf-8"
    )
    window = (INTEGRATION_ROOT / "desktop_workbench" / "main_window.py").read_text(
        encoding="utf-8"
    )
    assert "application.setStyleSheet(WORKBENCH_STYLE)" in app
    assert "QDialog" in window
    assert "QWidget#PageViewport" in window

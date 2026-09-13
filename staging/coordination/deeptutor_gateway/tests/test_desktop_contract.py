from __future__ import annotations

import ast
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


def _method(source: str, class_name: str, method_name: str) -> ast.FunctionDef:
    owner = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


def _attribute_calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        value
        for value in ast.walk(node)
        if isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == name
    ]


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
    )
    for path in _desktop_sources():
        folded = path.read_text(encoding="utf-8").casefold()
        for value in forbidden:
            assert value not in folded, f"{value!r} leaked into {path.name}"
        if "<ht" + "ml" in folded:
            # QTextDocument's limited rich text is a native Qt renderer, not a
            # web page. Only the local, escaped Word reader may own a document.
            assert path == INTEGRATION_ROOT / "desktop_workbench/word_lesson_reader.py"
            assert "class _localdocument(qtextdocument):" in folded
            assert "class _localbrowser(qtextbrowser):" in folded
            assert "self.browser.setopenlinks(false)" in folded
            assert "self.browser.setopenexternallinks(false)" in folded
            assert "def loadresource(" in folded


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
    assert "foreach ($BuildOutputPath in @($DistRoot, $WorkRoot, $SpecRoot))" in build
    assert "Test-Path -LiteralPath $BuildOutputPath" in build
    assert "Choose a fresh -BuildTag" in build
    assert '"--noconfirm"' not in build
    assert '"--clean"' not in build
    for module in (
        "integrations.deeptutor_shchem_v1.service",
        "integrations.deeptutor_shchem_v1.http_app",
        "integrations.deeptutor_shchem_v1.launcher",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ):
        assert f'"--exclude-module", "{module}"' in build


def test_desktop_import_surface_previews_analysis_handouts_and_folder_selection() -> (
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
    assert "预览指定一轮复习解析版（98 份）" in dialogs
    corpus = _method(dialogs, "ImportDialog", "_run_one_round_corpus")
    assert _attribute_calls(corpus, "teaching_pack_analysis_files")
    for method in (corpus, _method(dialogs, "ImportDialog", "_save")):
        assert _attribute_calls(method, "_start_import_preview")
        assert _attribute_calls(method, "preview_import_files")
        for direct_import in (
            "run_one_round_review_corpus_import",
            "save_visual_import_batch",
            "commit_import_preview",
        ):
            assert not _attribute_calls(method, direct_import)
    assert "选择文件夹" in components
    assert "getExistingDirectory" in components
    assert "submit_progress" in tasks


def test_desktop_import_commit_follows_current_confirmation_and_explicit_selection():
    source = (INTEGRATION_ROOT / "desktop_workbench/dialogs.py").read_text(
        encoding="utf-8"
    )
    method = _method(source, "ImportDialog", "_open_import_preview")
    commits = _attribute_calls(method, "commit_import_preview")
    assert len(commits) == 1
    commit = commits[0]
    assert [ast.unparse(value) for value in commit.args] == [
        "preview['preview_id']",
        "preview['revision']",
        "selected",
    ]
    assert _attribute_calls(method, "exec")
    assert "selected = list(dialog.selected_source_ids)" in ast.unparse(method)
    # An accepted dialog alone is insufficient: edited inputs and an empty
    # selection must also return before persistence. Behavioral UI tests cover
    # these paths; this static contract keeps their architectural placement.
    conditions = (
        "result != QDialog.DialogCode.Accepted or epoch != self._import_preview_epoch",
        "not selected",
    )
    for condition in conditions:
        guard = next(
            node
            for node in method.body
            if isinstance(node, ast.If) and ast.unparse(node.test) == condition
        )
        assert any(isinstance(node, ast.Return) for node in guard.body)
        assert _attribute_calls(guard, "_discard_import_preview")
        assert guard.end_lineno < commit.lineno


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

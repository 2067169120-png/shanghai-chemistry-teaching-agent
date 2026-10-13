"""Restored lessons retain the current user's external model metadata root."""
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from integrations.deeptutor_shchem_v1.desktop_backup import (
    create_backup, inspect_backup, manifest_revision, plan_backup, restore_backup,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPathError, DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench import app
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderSettingsError, ModelProviderSettingsStore,
)


class NoCredentials:
    """The constructor may validate paths, but must never use any credentials."""

    def read(self, *args, **kwargs):
        raise AssertionError("credential read is outside this startup test")

    def write(self, *args, **kwargs):
        raise AssertionError("credential write is outside this startup test")

    def delete(self, *args, **kwargs):
        raise AssertionError("credential delete is outside this startup test")


@pytest.fixture
def startup(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    bank = workspace / "sh-chem-db"
    bank.mkdir()
    original = bank / "original.txt"
    original.write_text("original evidence stays unchanged", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "private-user"))
    monkeypatch.setenv("SHCHEM_WORKSPACE_ROOT", str(workspace))
    normal = DesktopPaths.from_workspace(workspace)
    normal.state_root.mkdir(parents=True)
    sentinel = normal.state_root / "normal-profile-sentinel.txt"
    sentinel.write_bytes(b"do not alter the normal profile")
    seen = SimpleNamespace(
        workspace=workspace, normal=normal, paths=[], locks=[], messages=[],
        shown=[], events=[], original=original, sentinel=sentinel,
    )

    def acquire(root):
        seen.locks.append(root)
        return SimpleNamespace(unlock=lambda: seen.events.append("unlock"))

    def facade(paths):
        seen.paths.append(paths)
        # Use the production containment/reparse checks without the Windows
        # credential backend or a real profile/settings payload.
        ModelProviderSettingsStore(
            paths.settings_root, project_root=paths.workspace_root,
            credential_backend=NoCredentials(),
        )
        return SimpleNamespace(paths=paths)

    def window(facade):
        return SimpleNamespace(show=lambda: seen.shown.append(facade.paths))

    monkeypatch.setattr(app, "create_application", lambda argv: SimpleNamespace(
        exec=lambda: seen.events.append("exec") or 0,
    ))
    monkeypatch.setattr(app, "_acquire_desktop_instance_lock", acquire)
    monkeypatch.setattr(app, "build_default_facade", facade)
    monkeypatch.setattr(app, "TeacherWorkbenchWindow", window)
    for kind in ("critical", "information"):
        monkeypatch.setattr(app.QMessageBox, kind, lambda *args, _kind=kind:
                            seen.messages.append((_kind, args[1], args[2])))
    return seen


def independent_restore(startup, tmp_path, *, inside_workspace):
    source = tmp_path / "synthetic-original"
    source.mkdir()
    state = DesktopStateStore(source)
    state.add_to_basket({"key": "synthetic-question", "title_zh": "启动路径测试"})
    archive = tmp_path / "synthetic-backup.zip"
    create_backup(plan_backup(source), archive)
    report = inspect_backup(archive)
    target = (startup.workspace / "outputs" if inside_workspace else tmp_path) / "restored"
    target.parent.mkdir(parents=True, exist_ok=True)
    restore_backup(archive, target, expected_manifest=manifest_revision(report))
    return target.resolve()


@pytest.mark.parametrize("mode", ["explicit_workspace", "discovered_workspace", "sys_argv"])
def test_default_startup_keeps_external_settings_and_normal_lock(startup, monkeypatch, mode):
    arguments = ["workbench"]
    kwargs = {"workspace_root": startup.workspace} if mode == "explicit_workspace" else {}
    if mode == "sys_argv":
        monkeypatch.setattr(app.sys, "argv", arguments)
        arguments = None
    assert app.run_desktop_workbench(arguments, **kwargs) == 0
    assert startup.paths == [startup.normal]
    assert startup.locks == [startup.normal.state_root]
    assert startup.shown == startup.paths
    assert startup.events == ["exec", "unlock"]
    assert not startup.messages


@pytest.mark.parametrize("inside_workspace", [True, False])
def test_restored_startup_preserves_external_settings_and_locks_restore(
    startup, tmp_path, inside_workspace,
):
    restored = independent_restore(startup, tmp_path, inside_workspace=inside_workspace)
    assert app.run_desktop_workbench(
        ["workbench", "--personal-state", str(restored)], workspace_root=startup.workspace,
    ) == 0
    paths, = startup.paths
    assert paths.workspace_root == startup.normal.workspace_root
    assert paths.runtime_root == startup.normal.runtime_root
    assert paths.settings_root == startup.normal.settings_root
    assert paths.settings_root != restored / "model-settings"
    assert paths.state_root == restored
    assert paths.shchem_root == restored / "library" / "sh-chem-db"
    assert paths.drafts_root == restored / "drafts"
    assert paths.task_root == restored / "tasks"
    assert startup.locks == [restored]
    assert startup.shown == [paths]
    assert startup.events == ["exec", "unlock"]
    assert not startup.messages
    assert not (restored / "model-settings").exists()
    assert startup.sentinel.read_bytes() == b"do not alter the normal profile"
    assert startup.original.read_text(encoding="utf-8") == "original evidence stays unchanged"


@pytest.mark.parametrize("arguments", [[], ["unmarked"]])
def test_missing_or_unvalidated_restore_stops_before_lock_and_facade(startup, arguments):
    assert app.run_desktop_workbench(
        ["workbench", "--personal-state", *arguments], workspace_root=startup.workspace,
    ) == 2
    assert not startup.locks and not startup.paths and not startup.shown
    assert startup.messages == [(
        "critical", "无法打开恢复副本", "请选择通过本工作台校验恢复的独立目录。",
    )]


def test_workspace_resolution_error_stops_before_lock(startup, monkeypatch):
    def unavailable(*args, **kwargs):
        raise DesktopPathError("synthetic workspace is unavailable")
    monkeypatch.setattr(app.DesktopPaths, "discover", unavailable)
    assert app.run_desktop_workbench(["workbench"]) == 2
    assert not startup.locks and not startup.paths
    assert startup.messages == [("critical", "无法启动", "synthetic workspace is unavailable")]


@pytest.mark.parametrize("already_running, expected", [(True, 0), (False, 3)])
def test_restore_lock_failure_has_clear_boundary(startup, tmp_path, monkeypatch, already_running, expected):
    restored = independent_restore(startup, tmp_path, inside_workspace=True)
    def unavailable(root):
        assert root == restored
        raise app.DesktopInstanceLockError("synthetic lock failure", already_running=already_running)
    monkeypatch.setattr(app, "_acquire_desktop_instance_lock", unavailable)
    assert app.run_desktop_workbench(
        ["workbench", "--personal-state", str(restored)], workspace_root=startup.workspace,
    ) == expected
    assert not startup.paths and not startup.shown and not startup.events
    assert startup.messages == [(
        "information" if already_running else "critical",
        "已经打开" if already_running else "无法启动", "synthetic lock failure",
    )]


def test_facade_failure_reports_initialization_error_and_releases_restore_lock(startup, tmp_path, monkeypatch):
    restored = independent_restore(startup, tmp_path, inside_workspace=True)
    def unavailable(paths):
        assert paths.settings_root == startup.normal.settings_root
        raise RuntimeError("synthetic initialization failure")
    monkeypatch.setattr(app, "build_default_facade", unavailable)
    assert app.run_desktop_workbench(
        ["workbench", "--personal-state", str(restored)], workspace_root=startup.workspace,
    ) == 3
    assert startup.locks == [restored]
    assert startup.events == ["unlock"]
    assert not startup.shown
    assert startup.messages == [(
        "critical", "无法启动", "本地工作台初始化失败，请检查资料目录与个人数据目录。",
    )]


def test_project_local_settings_still_fail_production_containment_check(startup):
    metadata = startup.workspace / "outputs" / "restored" / "model-settings"
    with pytest.raises(ModelProviderSettingsError) as rejected:
        ModelProviderSettingsStore(
            metadata, project_root=startup.workspace, credential_backend=NoCredentials(),
        )
    assert rejected.value.code == "settings_root_not_external"
    assert rejected.value.status == 403
    assert not metadata.exists()

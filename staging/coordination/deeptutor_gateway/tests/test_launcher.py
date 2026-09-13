from __future__ import annotations

import copy
import json
import os
import socket
import stat
import subprocess
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

import pytest

from integrations.deeptutor_shchem_v1 import launcher as launcher_module
from integrations.deeptutor_shchem_v1.config import ConfigError
from integrations.deeptutor_shchem_v1.launcher import (
    EXPECTED_CAPABILITIES,
    EXPECTED_TABS,
    LauncherError,
    _assert_private_file,
    _atomic_private_write,
    _create_launcher_server,
    _port_available,
    _secure_directory,
    _verify_required_paths,
    build_app_config,
    default_config_path,
    generate_session_token,
    health_session,
    load_settings,
    start_session,
    stop_session,
    validate_session_token,
)

WORKSPACE = Path(__file__).resolve().parents[4]
PERSONAL_CONFIG = WORKSPACE / "runtime/deeptutor_shchem/webui.personal.json"


@pytest.fixture(autouse=True)
def _isolate_project_external_launcher_state(tmp_path, monkeypatch):
    """Unit tests must not depend on the user's selected release generation."""

    local_app_data = tmp_path / "local-app-data"
    local_app_data.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))


def _default_config() -> dict:
    return json.loads(default_config_path().read_text(encoding="utf-8"))


def _write_config(path: Path, value: dict) -> Path:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["network"].update(bind_host="0.0.0.0"), "loopback"),
        (lambda value: value["ccswitch"].update(enabled=True), "CCSwitch"),
        (
            lambda value: value["controller"].update(contract_version="1.0.0"),
            "contract drift",
        ),
        (
            lambda value: value["auth"].update(capabilities=["kb_retrieval_read"]),
            "capability configuration drift",
        ),
        (lambda value: value.update(unexpected=True), "unsupported fields"),
    ],
)
def test_launcher_config_drift_fails_closed(tmp_path, mutation, message):
    value = copy.deepcopy(_default_config())
    mutation(value)
    config_path = _write_config(tmp_path / "launcher.json", value)
    with pytest.raises(LauncherError, match=message):
        load_settings(config_path)


def test_personal_launcher_keeps_teacher_workflow_without_release_control():
    settings = load_settings(PERSONAL_CONFIG)
    assert settings.release_enabled is False
    assert settings.auto_connect is True
    assert settings.capabilities == (
        "kb_retrieval_read",
        "tag_patch_candidate_write",
            "model_provider_settings_write",
            "model_provider_synthetic_probe_execute",
            "intake_import_write",
            "intake_visual_execute",
            "review_task_write",
        "review_candidate_write",
        "review_decision_write",
    )
    assert "workbench_release_prepare_write" not in settings.capabilities
    assert "workbench_release_activate_write" not in settings.capabilities


def test_generated_token_and_app_config_preserve_security_boundaries(tmp_path):
    token = generate_session_token()
    assert len(token.encode("utf-8")) >= 32
    assert not any(character.isspace() for character in token)
    with pytest.raises(LauncherError, match="between 32"):
        validate_session_token("too-short")

    settings = load_settings()
    config = build_app_config(settings, tmp_path, token)
    assert settings.auto_connect is False
    assert config.personal_auto_auth is False
    assert config.bind_host == "127.0.0.1"
    assert config.mode == "controller"
    assert config.ccswitch_enabled is False
    assert config.overlay_root == WORKSPACE / "runtime/deeptutor_shchem/overlay"
    assert config.shchem_root == WORKSPACE / "sh-chem-db"
    assert config.controller_script == WORKSPACE / "sh-chem-db/scripts/sh_chem_agent.py"
    assert config.principals[0].capabilities == EXPECTED_CAPABILITIES
    assert config.principals[0].students == ()
    assert token not in json.dumps(config.public_summary())
    config.bind_host = "0.0.0.0"
    with pytest.raises(ConfigError, match="loopback"):
        config.validate()


def test_overlay_manifest_binds_teacher_first_ui_legacy_tabs_and_authority():
    settings = load_settings()
    manifest_hash = _verify_required_paths(settings)
    assert len(manifest_hash) == 64
    manifest = json.loads(
        (settings.overlay_root / "overlay.manifest.json").read_text(encoding="utf-8")
    )
    assert tuple(manifest["navigation"]["tabs"]) == (
        "home",
        "library",
        "students",
        "prep",
    )
    assert tuple(manifest["navigation"]["secondary_tabs"]) == (
        "materials",
        "curriculum",
        "analytics",
        "presentations",
        "hotspots",
        "templates",
        "review",
        "ai",
        "settings",
    )
    assert tuple(manifest["navigation"]["all_preserved_hash_routes"]) == (
        "home",
        "materials",
        "curriculum",
        "library",
        "students",
        "analytics",
        "prep",
        "presentations",
        "hotspots",
        "review",
        "templates",
        "ai",
        "settings",
    )
    assert tuple(manifest["navigation"]["legacy_tabs"]) == EXPECTED_TABS
    assert manifest["navigation"]["legacy_tabs_hidden"] is True
    assert EXPECTED_TABS[0] == "home"
    assert manifest["navigation"]["primary_route"] == "home"
    html = (settings.overlay_root / "index.html").read_text(encoding="utf-8")
    tab_positions = [html.index(f'id="tab-{tab}"') for tab in EXPECTED_TABS]
    assert tab_positions == sorted(tab_positions)
    candidate = manifest["candidate_review_only"]
    assert candidate["hash_verified_question_crop_serving"] is True
    assert candidate["question_crop_record_coverage_expected"] == 252
    assert candidate["answer_crop_serving"] is False
    assert candidate["original_source_serving"] is False
    assert candidate["whole_page_serving"] is False
    assert candidate["arbitrary_file_read_present"] is False
    assert manifest["claims"] == {
        "candidate_only": True,
        "human_reviewed": False,
        "official": False,
        "publication_allowed": False,
        "retrieval_ready": False,
        "generation_allowed": False,
        "teaching_use_allowed": False,
    }


def test_chinese_double_click_scripts_are_valid_windows_cmd_files():
    scripts = {
        "打开上海化学题库工作台.cmd": "launcher.py start",
        "关闭上海化学题库工作台.cmd": "launcher.py stop",
    }
    for filename, command in scripts.items():
        raw = (WORKSPACE / filename).read_bytes()
        assert raw.startswith(b"@echo off\r\n")
        assert b"\n" not in raw.replace(b"\r\n", b"")
        text = raw.decode("utf-8")
        assert "chcp 65001" in text
        assert command in text.replace("runtime\\deeptutor_shchem\\", "")
        assert "session.token" not in text
        assert "?token=" not in text.casefold()
    assert "--copy-token" not in (WORKSPACE / "打开上海化学题库工作台.cmd").read_text(encoding="utf-8")

    silent_launchers = {
        "打开沪上化学智研台（无窗口）.vbs": "打开上海化学题库工作台.cmd",
        "关闭沪上化学智研台（无窗口）.vbs": "关闭上海化学题库工作台.cmd",
    }
    for filename, target in silent_launchers.items():
        raw = (WORKSPACE / filename).read_bytes()
        assert raw.startswith(b"\xff\xfe")
        text = raw.decode("utf-16")
        assert 'shell.Run(command, 0, True)' in text
        assert target in text
        assert "/silent" in text
        assert "session.token" not in text
        assert "?token=" not in text.casefold()


def test_session_identity_binds_controller_and_overlay_hashes(monkeypatch):
    settings = load_settings()
    overlay_hash = _verify_required_paths(settings)
    baseline = launcher_module._identity(settings, overlay_hash)
    assert launcher_module._identity(settings, "f" * 64) != baseline

    original_hash = launcher_module._sha256_file

    def controller_drift(path):
        if Path(path).resolve() == settings.controller_script.resolve():
            return "e" * 64
        return original_hash(path)

    monkeypatch.setattr(launcher_module, "_sha256_file", controller_drift)
    assert launcher_module._identity(settings, overlay_hash) != baseline


def test_one_shot_stop_signal_allows_immediate_consumer_after_secure_publish(
    tmp_path, monkeypatch
):
    target = tmp_path / "stop.json"
    checked: list[Path] = []
    original_replace = os.replace

    monkeypatch.setattr(launcher_module, "_secure_file", lambda path: checked.append(Path(path)))
    monkeypatch.setattr(
        launcher_module,
        "_assert_private_file",
        lambda path: checked.append(Path(path)),
    )

    def replace_and_consume(source, destination):
        original_replace(source, destination)
        Path(destination).unlink()

    monkeypatch.setattr(launcher_module.os, "replace", replace_and_consume)
    launcher_module._write_one_shot_json(target, {"instance_id": "test-instance"})

    assert not target.exists()
    assert len(checked) == 2
    assert all(path.name.startswith(".stop.json.") for path in checked)
    assert not list(tmp_path.glob(".stop.json.*"))


def test_busy_port_and_project_local_secret_storage_fail_before_launch(tmp_path):
    value = _default_config()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = int(sock.getsockname()[1])
        assert _port_available("127.0.0.1", port) is False
        value["network"]["port"] = port
        config_path = _write_config(tmp_path / "launcher.json", value)
        session_dir = tmp_path / "session"
        with pytest.raises(LauncherError, match="already in use"):
            start_session(config_path, session_dir)
        assert not (session_dir / "session.token").exists()

    with pytest.raises(LauncherError, match="outside the project"):
        start_session(default_config_path(), WORKSPACE / "runtime/deeptutor_shchem/test")


def test_detached_child_never_receives_token_in_argv_or_environment(
    tmp_path, monkeypatch
):
    captured = {}
    session_dir = _secure_directory(tmp_path / "会话目录", WORKSPACE)
    secret = "launcher-token-must-not-cross-the-process-boundary"
    monkeypatch.setenv(launcher_module.TOKEN_ENV, secret)
    monkeypatch.setenv("SHCHEM_GATEWAY_TEACHER_TOKEN", secret)

    class FakeProcess:
        pass

    real_popen = launcher_module.subprocess.Popen

    def fake_popen(command, **kwargs):
        if "_serve" not in command:
            return real_popen(command, **kwargs)
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        captured["stdout"] = kwargs["stdout"]
        captured["stderr"] = kwargs["stderr"]
        return FakeProcess()

    monkeypatch.setattr(launcher_module.subprocess, "Popen", fake_popen)
    settings = load_settings()
    launcher_module._spawn_child(settings, session_dir, "instance-a")

    assert secret not in captured["command"]
    assert secret not in captured["environment"].values()
    assert launcher_module.TOKEN_ENV not in captured["environment"]
    assert "SHCHEM_GATEWAY_TEACHER_TOKEN" not in captured["environment"]
    presentation_environment = launcher_module._presentation_runtime_environment()
    assert set(presentation_environment) == {
        "RUNTIME_NODE",
        "RUNTIME_NODE_MODULES",
        "RUNTIME_BIN_DIR",
        "PRESENTATIONS_PYTHON",
        "PRESENTATIONS_SKILL_DIR",
    }
    assert all(Path(value).exists() for value in presentation_environment.values())
    assert all(
        captured["environment"].get(key) == value
        for key, value in presentation_environment.items()
    )
    assert captured["stdout"] is captured["stderr"]
    assert captured["stdout"] is not launcher_module.subprocess.DEVNULL
    launcher_module._assert_private_file(
        launcher_module._child_output_path(session_dir)
    )


def test_private_atomic_replace_rejects_links_and_hardlinks(tmp_path):
    directory = _secure_directory(tmp_path / "launcher-state", WORKSPACE)
    credential = directory / "credential.token"
    _atomic_private_write(credential, "a" * 48)
    _atomic_private_write(credential, "b" * 48)
    _assert_private_file(credential)
    assert credential.read_text(encoding="utf-8") == "b" * 48

    hardlink = directory / "credential-hardlink.token"
    os.link(credential, hardlink)
    with pytest.raises(LauncherError, match="hard-linked"):
        _assert_private_file(credential)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "launcher-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")
    with pytest.raises(LauncherError, match="symlinks or reparse points"):
        _secure_directory(link, WORKSPACE)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL regression")
def test_preexisting_explicit_foreign_acl_is_removed(tmp_path):
    directory = tmp_path / "broad-acl-state"
    directory.mkdir()
    subprocess.run(
        [
            "icacls",
            str(directory),
            "/inheritance:r",
            "/grant:r",
            "*S-1-1-0:(OI)(CI)(F)",
        ],
        check=True,
        capture_output=True,
    )
    secured = _secure_directory(directory, WORKSPACE)
    acl = subprocess.run(
        ["icacls", str(secured)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    ace_lines = [line for line in acl.splitlines() if ":(" in line]
    assert len(ace_lines) == 1
    assert "Everyone:" not in acl
    assert "Authenticated Users:" not in acl
    assert "BUILTIN\\Users:" not in acl


def test_spawn_exception_removes_the_private_token(tmp_path, monkeypatch):
    value = _default_config()
    value["network"]["port"] = _free_port()
    config_path = _write_config(tmp_path / "launcher.json", value)
    session_dir = tmp_path / "session"
    monkeypatch.setattr(launcher_module, "_probe_controller", lambda *_args: None)
    monkeypatch.setattr(
        launcher_module,
        "_spawn_child",
        lambda *_args: (_ for _ in ()).throw(LauncherError("spawn failed")),
    )

    with pytest.raises(LauncherError, match="spawn failed"):
        start_session(config_path, session_dir)
    for name in (
        launcher_module.SESSION_FILE,
        launcher_module.TOKEN_FILE,
        launcher_module.STOP_FILE,
        launcher_module.FAILURE_FILE,
        launcher_module.STARTUP_INTENT_FILE,
    ):
        assert not (session_dir / name).exists()


def test_port_check_to_bind_race_fails_closed_and_cleans_up(tmp_path, monkeypatch):
    value = _default_config()
    session_dir = tmp_path / "session"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        value["network"]["port"] = int(occupied.getsockname()[1])
        config_path = _write_config(tmp_path / "launcher.json", value)
        monkeypatch.setattr(launcher_module, "_port_available", lambda *_args: True)

        with pytest.raises(LauncherError, match="failed to become healthy"):
            start_session(config_path, session_dir)
        assert occupied.getsockname()[1] == value["network"]["port"]

    for name in (
        launcher_module.SESSION_FILE,
        launcher_module.TOKEN_FILE,
        launcher_module.STOP_FILE,
        launcher_module.FAILURE_FILE,
        launcher_module.STARTUP_INTENT_FILE,
    ):
        assert not (session_dir / name).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive bind regression")
def test_windows_server_bind_is_exclusive_even_against_reuseaddr(tmp_path):
    settings = load_settings()
    token = generate_session_token()
    port = _free_port()
    settings = launcher_module.LauncherSettings(
        config_path=settings.config_path,
        bind_host=settings.bind_host,
        port=port,
        controller_timeout_seconds=settings.controller_timeout_seconds,
        startup_timeout_seconds=settings.startup_timeout_seconds,
        health_timeout_seconds=settings.health_timeout_seconds,
        principal_id=settings.principal_id,
        capabilities=settings.capabilities,
        students=settings.students,
    )
    server = _create_launcher_server(build_app_config(settings, tmp_path, token))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as competing:
            competing.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                competing.bind(("127.0.0.1", port))
    finally:
        server.server_close()


def test_reused_pid_is_never_terminated_from_stale_state(tmp_path):
    directory = _secure_directory(tmp_path / "session", WORKSPACE)
    launcher_module._write_json(
        directory / launcher_module.SESSION_FILE,
        {
            "schema_version": launcher_module.SESSION_SCHEMA,
            "instance_id": "stale-instance",
            "pid": os.getpid(),
            "bind_host": "127.0.0.1",
            "port": _free_port(),
        },
    )

    with pytest.raises(LauncherError, match="did not stop cleanly"):
        stop_session(directory, timeout_seconds=0.2)
    assert launcher_module._process_alive(os.getpid()) is True
    assert (directory / launcher_module.SESSION_FILE).exists()


def test_detached_launcher_home_status_drift_and_clean_stop(tmp_path):
    value = _default_config()
    value["network"]["port"] = _free_port()
    value["network"]["startup_timeout_seconds"] = 120
    config_path = _write_config(tmp_path / "launcher.json", value)
    session_dir = tmp_path / "session"
    state = None
    try:
        state, token = start_session(config_path, session_dir)
        assert state["mode"] == "controller"
        assert state["ccswitch_enabled"] is False
        assert token not in json.dumps(state, ensure_ascii=False)
        assert (session_dir / "session.token").resolve().is_relative_to(
            session_dir.resolve()
        )
        if os.name == "nt":
            acl = subprocess.run(
                ["icacls", str(session_dir / "session.token")],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            ).stdout
            assert ":(F)" in acl
            assert "Everyone:" not in acl
            assert "Authenticated Users:" not in acl
            assert "BUILTIN\\Users:" not in acl
        elif stat.S_IMODE((session_dir / "session.token").stat().st_mode):
            assert (
                stat.S_IMODE((session_dir / "session.token").stat().st_mode)
                & (stat.S_IRWXG | stat.S_IRWXO)
            ) == 0

        healthy = health_session(session_dir)
        assert healthy["instance_id"] == state["instance_id"]

        launcher_module._write_json(
            session_dir / launcher_module.STOP_FILE,
            {"instance_id": "forged-wrong-instance"},
        )
        time.sleep(0.5)
        assert health_session(session_dir)["instance_id"] == state["instance_id"]

        root = f"http://127.0.0.1:{state['port']}"
        with urlrequest.urlopen(f"{root}/overlay/", timeout=10) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200
        for tab in EXPECTED_TABS:
            assert f'id="tab-{tab}"' in html

        with pytest.raises(urlerror.HTTPError) as unauthorized:
            urlrequest.urlopen(f"{root}/api/v1/status", timeout=10)
        assert unauthorized.value.code == 401

        original = config_path.read_bytes()
        value["network"]["health_timeout_seconds"] = 9
        _write_config(config_path, value)
        with pytest.raises(LauncherError, match="configuration drift"):
            health_session(session_dir)
        config_path.write_bytes(original)
    finally:
        if state is not None and (session_dir / "session.json").exists():
            stopped = stop_session(session_dir)
            assert stopped["instance_id"] == state["instance_id"]
            assert not (session_dir / "session.json").exists()
            assert not (session_dir / "session.token").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows clipboard helper")
def test_start_copy_token_places_secret_on_clipboard_without_printing_it(
    monkeypatch, capsys, tmp_path
):
    token = "local-teacher-token-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        launcher_module,
        "start_session",
        lambda config, session_dir: (
            {"bind_host": "127.0.0.1", "port": 8765},
            token,
        ),
    )
    monkeypatch.setattr(
        launcher_module,
        "load_settings",
        lambda _config: type("Settings", (), {"auto_connect": False})(),
    )

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(launcher_module.subprocess, "run", fake_run)
    code = launcher_module.main(
        [
            "start",
            "--copy-token",
            "--config",
            str(tmp_path / "unused.json"),
            "--session-dir",
            str(tmp_path / "session"),
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert observed == {"argv": ["clip.exe"], "input": token.encode("ascii")}
    assert token not in output
    assert "已复制到剪贴板" in output


def test_personal_start_never_prints_or_copies_the_background_token(
    monkeypatch, capsys, tmp_path
):
    token = "local-teacher-token-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    monkeypatch.setattr(
        launcher_module,
        "load_settings",
        lambda _config: type("Settings", (), {"auto_connect": True})(),
    )
    monkeypatch.setattr(
        launcher_module,
        "start_session",
        lambda config, session_dir: (
            {"bind_host": "127.0.0.1", "port": 8765},
            token,
        ),
    )
    monkeypatch.setattr(
        launcher_module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("personal auto-connect must not call a clipboard helper")
        ),
    )

    code = launcher_module.main(
        [
            "start",
            "--config",
            str(tmp_path / "unused.json"),
            "--session-dir",
            str(tmp_path / "session"),
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert token not in output
    assert "自动本机连接" in output

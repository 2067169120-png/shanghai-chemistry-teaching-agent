from __future__ import annotations

import py_compile
import re
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.desktop_version import DESKTOP_VERSION

WORKSPACE = Path(__file__).resolve().parents[4]


def test_desktop_entrypoints_exist_and_default_to_native_app() -> None:
    start = WORKSPACE / "启动沪上化学智研台桌面版.vbs"
    stop = WORKSPACE / "关闭沪上化学智研台桌面版.vbs"
    helper = WORKSPACE / "runtime" / "deeptutor_shchem" / "close_desktop_workbench.pyw"

    assert start.is_file() and stop.is_file() and helper.is_file()
    start_text = start.read_text(encoding="utf-8").casefold()
    stop_text = stop.read_text(encoding="utf-8").casefold()
    assert "desktop_teacher_workbench.pyw" in start_text
    assert 'appname & ".exe"' in start_text
    package_entries = re.findall(r"desktop_package_[0-9.]+(?:-r[0-9]+)?", start_text)
    assert package_entries == [
        f"desktop_package_{DESKTOP_VERSION}-r2",
        f"desktop_package_{DESKTOP_VERSION}",
        "desktop_package_0.1.66",
        "desktop_package_0.1.65",
        "desktop_package_0.1.64",
        "desktop_package_0.1.63",
        "desktop_package_0.1.62",
        "desktop_package_0.1.61",
        "desktop_package_0.1.60-r2",
        "desktop_package_0.1.59",
        "desktop_package_0.1.58",
        "desktop_package_0.1.57",
        "desktop_package_0.1.56",
        "desktop_package_0.1.54",
        "desktop_package_0.1.53",
    ]
    assert "for each packagename in array" in start_text
    assert "if fs.fileexists(exepath) then exit for" in start_text
    # Retained versions are optional fallbacks, not a requirement to install all
    # historical packages. Actual ZIP/EXE evidence comes from the local verifier.
    start.read_bytes().decode("ascii")
    assert "shell.run command, 1, false" in start_text
    assert "shell.run command, 0, false" not in start_text
    assert "close_desktop_workbench.pyw" in stop_text
    assert "127.0.0.1" not in start_text
    assert "127.0.0.1" not in stop_text


def test_desktop_close_helper_compiles_without_importing_web_runtime() -> None:
    helper = WORKSPACE / "runtime" / "deeptutor_shchem" / "close_desktop_workbench.pyw"
    py_compile.compile(str(helper), doraise=True)
    source = helper.read_text(encoding="utf-8").casefold()
    assert "http_app" not in source
    assert "wm_close" in source


def test_current_package_launcher_is_codepage_independent_and_shows_native_window() -> (
    None
):
    launcher = (
        WORKSPACE
        / "runtime"
        / "deeptutor_shchem"
        / f"desktop_package_{DESKTOP_VERSION}-r2"
        / "启动沪上化学智研台桌面版.vbs"
    )
    if not launcher.is_file():
        pytest.skip("Local package is not distributed in the source repository")
    source = launcher.read_bytes().decode("ascii")
    name_line = next(
        line for line in source.splitlines() if line.startswith("appName =")
    )
    app_name = "".join(
        chr(int(code, 16)) for code in re.findall(r"ChrW\(&H([0-9A-F]+)\)", name_line)
    )
    assert app_name == "沪上化学智研台"
    assert 'fs.BuildPath(fs.BuildPath(root, appName), appName & ".exe")' in source
    assert "If Not fs.FileExists(exePath) Then" in source
    assert "shell.Run Chr(34) & exePath & Chr(34), 1, False" in source
    assert "python" not in source.casefold()

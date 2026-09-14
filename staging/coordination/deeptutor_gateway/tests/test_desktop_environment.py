from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1.desktop_environment import (
    DEPENDENCIES, ICON_FILES, collect_environment_report, office_check, report_text,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_settings_view import capability_summary


def paths_at(tmp_path):
    root = tmp_path / "source"
    (root / "integrations/deeptutor_shchem_v1").mkdir(parents=True)
    paths = DesktopPaths.from_workspace(root, state_root=tmp_path / "personal")
    paths.validate_read_roots()
    return paths


def icon_root(tmp_path):
    root = tmp_path / "resources"
    assets = root / "desktop_workbench/studio_assets"
    assets.mkdir(parents=True)
    for name in ICON_FILES:
        (assets / name).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    return root


def test_report_is_local_scoped_and_contains_no_private_paths_or_materials(tmp_path, monkeypatch):
    import socket
    def network_forbidden(*args, **kwargs):
        pytest.fail("Local readiness must not use network")
    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    paths = paths_at(tmp_path)
    secret_file = paths.shchem_root / "private-school-exam-and-student.txt"
    secret_file.write_text("PRIVATE-MATERIAL-NEVER-IN-REPORT", encoding="utf-8")
    (paths.state_root / "keep.txt").write_text("untouched", encoding="utf-8")
    report = collect_environment_report(paths, importer=lambda name: object(),
        office_finder=lambda: None, word_detector=lambda: False, resource_root=icon_root(tmp_path))
    text = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in text
    assert secret_file.name not in text and "PRIVATE-MATERIAL" not in text
    assert report["network_requests"] == 0
    checks = {row["key"]: row for row in report["checks"]}
    assert checks["office"]["status"] == "missing"
    assert checks["library"]["status"] == "detected"
    assert checks["icons"]["status"] == "ready"
    assert checks["state"]["status"] == "ready"
    assert (paths.state_root / "keep.txt").read_text() == "untouched"
    assert not list(paths.state_root.glob(".readiness-*"))
    assert "不存在或" not in checks["library"]["detail"]


@pytest.mark.parametrize("failure", [ImportError, OSError, RuntimeError])
def test_dependency_failure_is_per_feature_and_sanitized(tmp_path, failure):
    paths = paths_at(tmp_path)
    def importer(name):
        if name == "pptx":
            raise failure("private-token-and-path-must-not-leak")
        return object()
    report = collect_environment_report(paths, importer=importer, office_finder=lambda: None,
        word_detector=lambda: False, resource_root=icon_root(tmp_path))
    checks = {r["key"]: r for r in report["checks"]}
    assert checks["pptx"]["status"] == "missing"
    assert checks["docx"]["status"] == "ready"
    assert "private-token" not in str(report)
    assert "PPTX" in checks["pptx"]["affects"]
    assert checks["library"]["status"] == "empty"


@pytest.mark.parametrize("word, expected", [(True, "detected"), (False, "missing"), (None, "unknown")])
def test_office_discovery_is_not_end_to_end_verification(word, expected):
    result = office_check(office_finder=lambda: None, word_detector=lambda: word)
    assert result.status == expected
    assert result.status != "ready"


def test_libreoffice_takes_priority_without_starting_word(tmp_path):
    def forbidden():
        pytest.fail("Word discovery not needed if LibreOffice found")
    result = office_check(office_finder=lambda: tmp_path / "soffice", word_detector=forbidden)
    assert result.status == "detected"
    assert "尚未执行" in result.detail


def test_missing_icon_is_identified_without_local_path(tmp_path):
    paths = paths_at(tmp_path)
    root = icon_root(tmp_path)
    (root / "desktop_workbench/studio_assets/down.svg").unlink()
    report = collect_environment_report(paths, importer=lambda name: object(),
        office_finder=lambda: None, word_detector=lambda: False, resource_root=root)
    row = next(r for r in report["checks"] if r["key"] == "icons")
    assert row["status"] == "missing" and "down.svg" in row["detail"]
    assert str(root) not in report_text(report)


def test_unwritable_state_does_not_delete_existing_file(tmp_path):
    paths = paths_at(tmp_path)
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("keep", encoding="utf-8")
    from dataclasses import replace
    report = collect_environment_report(replace(paths, state_root=blocked), importer=lambda name: object(),
        office_finder=lambda: None, word_detector=lambda: False, resource_root=icon_root(tmp_path))
    assert next(r for r in report["checks"] if r["key"] == "state")["status"] == "missing"
    assert blocked.read_text() == "keep"


def test_packaged_mode_is_explicit(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    report = collect_environment_report(paths_at(tmp_path), importer=lambda name: object(),
        office_finder=lambda: None, word_detector=lambda: False, resource_root=icon_root(tmp_path))
    assert report["execution_mode"] == "packaged"
    assert "打包版" in report_text(report)


@pytest.mark.parametrize("status", [None, "succeeded", "failed", "stale", "cancelled"])
def test_profile_summary_separates_permission_and_test_evidence(status):
    p = SimpleNamespace(last_connection_test=None if status is None else SimpleNamespace(status=status))
    text = capability_summary(p, matches_saved=True, vision_enabled=True)
    assert "不是识图测试通过" in text and "不验证" in text
    if status == "succeeded":
        assert "曾通过短文本" in text and "不是实时" in text
    elif status == "stale":
        assert "旧测试已失效" in text


def test_unsaved_configuration_does_not_inherit_success():
    p = SimpleNamespace(last_connection_test=SimpleNamespace(status="succeeded"))
    text = capability_summary(p, matches_saved=False, vision_enabled=False)
    assert "未保存修改" in text and "曾通过短文本" not in text
    assert "图片发送未允许" in text


def test_no_profile_never_claims_a_test():
    text = capability_summary(None, matches_saved=False, vision_enabled=True)
    assert "尚未保存" in text
    assert "曾通过" not in text

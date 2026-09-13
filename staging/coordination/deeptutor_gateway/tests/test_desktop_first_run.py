from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.desktop_paths import (
    DesktopPathError, DesktopPaths, discover_workspace_root,
)


def checkout(path):
    (path / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    return path


def test_clean_checkout_uses_personal_empty_library(tmp_path, monkeypatch):
    root = checkout(tmp_path / "source")
    monkeypatch.delenv("SHCHEM_WORKSPACE_ROOT", raising=False)
    monkeypatch.chdir(root)
    assert discover_workspace_root(root) == root
    paths = DesktopPaths.from_workspace(root, state_root=tmp_path / "personal")
    assert paths.uses_personal_library
    assert not (root / "sh-chem-db").exists()
    paths.validate_read_roots()
    assert paths.shchem_root.is_dir()
    assert not (root / "sh-chem-db").exists()


def test_existing_library_is_not_replaced(tmp_path):
    root = checkout(tmp_path / "source")
    bank = root / "sh-chem-db"
    bank.mkdir()
    source = bank / "original.txt"
    source.write_text("unchanged")
    paths = DesktopPaths.from_workspace(root, state_root=tmp_path / "personal")
    paths.validate_read_roots()
    assert paths.shchem_root == bank
    assert not paths.uses_personal_library
    assert source.read_text() == "unchanged"


def test_explicit_missing_workspace_does_not_fall_back(tmp_path, monkeypatch):
    root = checkout(tmp_path / "source")
    monkeypatch.chdir(root)
    monkeypatch.setenv("SHCHEM_WORKSPACE_ROOT", str(tmp_path / "missing"))
    with pytest.raises(DesktopPathError, match="SHCHEM_WORKSPACE_ROOT"):
        discover_workspace_root(root)


def test_explicit_empty_checkout_is_honored(tmp_path, monkeypatch):
    first = checkout(tmp_path / "first")
    second = checkout(tmp_path / "second")
    (second / "sh-chem-db").mkdir()
    monkeypatch.setenv("SHCHEM_WORKSPACE_ROOT", str(first))
    monkeypatch.chdir(second)
    assert discover_workspace_root(second) == first


def test_file_in_place_of_library_is_not_overwritten(tmp_path):
    root = checkout(tmp_path / "source")
    (root / "sh-chem-db").write_text("not a directory")
    paths = DesktopPaths.from_workspace(root, state_root=tmp_path / "personal")
    with pytest.raises(DesktopPathError):
        paths.validate_read_roots()
    assert (root / "sh-chem-db").read_text() == "not a directory"


def test_default_facade_can_initialize_without_private_materials(tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
    root = checkout(tmp_path / "source")
    facade = build_default_facade(
        DesktopPaths.from_workspace(root, state_root=tmp_path / "personal")
    )
    try:
        status = facade.load_desktop_registry(force_refresh=True)
        assert all(not product.loaded for product in status.products)
        assert facade.list_imported_word_batches() == ()
    finally:
        facade.shutdown()

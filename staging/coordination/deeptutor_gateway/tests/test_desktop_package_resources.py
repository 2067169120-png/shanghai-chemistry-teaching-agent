from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("package_resources_under_test", ROOT / "runtime/deeptutor_shchem/package_resources.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_declared_resources_exist_and_include_icons_schemas_and_runtime_hash_source():
    result = module.inventory()
    names = [row["path"] for row in result["files"]]
    assert len(names) == len(set(names))
    assert any(name.endswith("paper_format_contract_v1.schema.json") for name in names)
    assert any(name.endswith("down.svg") for name in names)
    assert "integrations/deeptutor_shchem_v1/desktop_local_pagination.py" in names
    assert all(len(row["sha256"]) == 64 and row["size"] > 0 for row in result["files"])
    assert all(not name.startswith(("sh-chem-db/", "runtime/private", "C:/")) for name in names)
    assert not any(name.endswith((".ttf", ".ttc", ".otf", ".docx", ".pdf")) for name in names)


def test_arguments_are_structured_not_a_shell_command():
    args = module.pyinstaller_arguments()
    assert len(args) % 2 == 0
    assert set(args[::2]) == {"--add-data"}
    assert all(":" in value for value in args[1::2])


@pytest.mark.parametrize("entry", ["../private.txt", "C:/private.txt", "/private.txt", "integrations/../private.txt", "integrations\\private.txt", "private/key.json"])
def test_manifest_rejects_non_resource_paths(tmp_path, entry):
    manifest = tmp_path / "list.json"
    manifest.write_text(json.dumps({"schema_version": "shchem.desktop-resources.v1", "files": [entry]}))
    with pytest.raises(ValueError):
        module.resource_files(tmp_path, manifest)


def test_missing_resource_fails_before_packaging(tmp_path):
    manifest = tmp_path / "list.json"
    manifest.write_text(json.dumps({"schema_version": "shchem.desktop-resources.v1", "files": ["integrations/missing.svg"]}))
    with pytest.raises(ValueError, match="missing"):
        module.resource_files(tmp_path, manifest)


def test_unlisted_teacher_file_is_not_collected(tmp_path):
    target = tmp_path / "integrations/icon.svg"
    target.parent.mkdir()
    target.write_text("<svg/>")
    (target.parent / "teacher-original.pdf").write_bytes(b"not-a-shipping-resource")
    manifest = tmp_path / "list.json"
    manifest.write_text(json.dumps({"schema_version": "shchem.desktop-resources.v1", "files": ["integrations/icon.svg"]}))
    assert len(module.resource_files(tmp_path, manifest)) == 1


def test_symlink_cannot_import_outside_file(tmp_path):
    outside = tmp_path / "outside.svg"
    outside.write_text("not-a-shipping-resource")
    root = tmp_path / "root"
    target = root / "integrations/icon.svg"
    target.parent.mkdir(parents=True)
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable in this environment")
    manifest = root / "list.json"
    manifest.write_text(json.dumps({"schema_version": "shchem.desktop-resources.v1", "files": ["integrations/icon.svg"]}))
    with pytest.raises(ValueError, match="outside"):
        module.resource_files(root, manifest)

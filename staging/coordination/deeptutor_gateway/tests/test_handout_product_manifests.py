from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/verify_handout_product_manifests.py"
)
SPEC = importlib.util.spec_from_file_location("handout_inventory_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def inventory(tmp_path):
    content = b"abc"
    (tmp_path / "data.json").write_bytes(content)
    manifest = {
        "outputs": [
            {
                "path": "data.json",
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
        "output_count_excluding_self": 1,
        "total_bytes_excluding_self": 3,
    }
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, manifest


def test_complete_inventory(inventory):
    root, _ = inventory
    assert MODULE.verify(root, "MANIFEST.json") == {
        "manifest": "MANIFEST.json",
        "valid": True,
        "files": 1,
        "bytes": 3,
    }


@pytest.mark.parametrize("change", ["missing", "extra", "same_size_content", "size"])
def test_filesystem_drift_is_rejected(inventory, change):
    root, _ = inventory
    if change == "missing":
        (root / "data.json").unlink()
    elif change == "extra":
        (root / "extra.json").write_text("{}")
    elif change == "same_size_content":
        (root / "data.json").write_bytes(b"xyz")
    else:
        (root / "data.json").write_bytes(b"longer")
    with pytest.raises(AssertionError):
        MODULE.verify(root, "MANIFEST.json")


@pytest.mark.parametrize("change", ["duplicate", "count", "bytes", "outside"])
def test_manifest_drift_is_rejected(inventory, change):
    root, manifest = inventory
    if change == "duplicate":
        manifest["outputs"].append(dict(manifest["outputs"][0]))
    elif change == "count":
        manifest["output_count_excluding_self"] = 2
    elif change == "bytes":
        manifest["total_bytes_excluding_self"] = 4
    else:
        manifest["outputs"][0]["path"] = "../data.json"
    (root / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AssertionError):
        MODULE.verify(root, "MANIFEST.json")

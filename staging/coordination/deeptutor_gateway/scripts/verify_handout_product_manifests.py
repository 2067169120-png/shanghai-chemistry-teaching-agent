"""Refresh optionally, then verify the complete local handout file inventories.

This changes only the two derived JSON manifests when explicitly requested.
It never changes source documents, candidate classifications or formal gates.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
PRODUCT = ROOT / "sh-chem-db/kb/teaching_handout_question_slices_v1_2026-08-28"


def verify(root: Path, name: str) -> dict:
    manifest_path = root / name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    listed = manifest["outputs"]
    names = [entry["path"] for entry in listed]
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    assert len(names) == len(set(names)), "Duplicate inventory paths"
    assert set(names) == actual, "Inventory paths differ from current files"
    assert len(listed) == manifest["output_count_excluding_self"]
    total = 0
    for entry in listed:
        path = (root / entry["path"]).resolve()
        assert path.is_relative_to(root.resolve())
        content = path.read_bytes()
        assert len(content) == entry["bytes"], entry["path"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], entry["path"]
        total += len(content)
    assert total == manifest["total_bytes_excluding_self"]
    return {"manifest": name, "valid": True, "files": len(listed), "bytes": total}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    # Avoid introducing bytecode files after a manifest captures its tree.
    sys.dont_write_bytecode = True
    tasks = (
        (
            PRODUCT / "native_ooxml_v2",
            "scripts/native_fastlane_pipeline.py",
            "build_version_manifest",
            "VERSION_MANIFEST.json",
        ),
        (
            PRODUCT,
            "scripts/handout_slice_pipeline.py",
            "build_product_manifest",
            "PRODUCT_MANIFEST.json",
        ),
    )
    if args.refresh:
        for index, (root, script, function, _) in enumerate(tasks):
            name = f"_handout_manifest_builder_{index}"
            spec = importlib.util.spec_from_file_location(name, root / script)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            getattr(module, function)()
    results = [verify(root, name) for root, _, _, name in tasks]
    print(
        json.dumps({"refreshed": args.refresh, "results": results}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()

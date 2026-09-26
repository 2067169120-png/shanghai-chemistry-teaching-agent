"""An explicit resource manifest for trial builds, never a teacher-folder scan.

The list records the currently tracked software resources only. Finding them
is not equivalent to accepting the complete EXE or its teaching outputs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "integrations/deeptutor_shchem_v1/desktop_resources.json"


def resource_files(root=ROOT, manifest=MANIFEST):
    root = Path(root).resolve()
    value = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if value.get("schema_version") != "shchem.desktop-resources.v1":
        raise ValueError("Unsupported resource list")
    result, seen = [], set()
    for relative in value["files"]:
        if not isinstance(relative, str) or relative in seen:
            raise ValueError("Invalid or duplicate resource entry")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative:
            raise ValueError("Resource paths must be relative POSIX paths")
        if not relative.startswith(("integrations/", "knowledge/")):
            raise ValueError("Only declared software resources may be packaged")
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("Declared resource is missing or outside source tree: " + relative)
        if target.suffix not in (".json", ".md", ".svg", ".py"):
            raise ValueError("Unsupported resource type")
        seen.add(relative)
        result.append((relative, target))
    return result


def inventory(root=ROOT, manifest=MANIFEST):
    return {"schema_version": "shchem.desktop-resource-inventory.v1",
            "files": [{"path": relative, "size": path.stat().st_size,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                      for relative, path in resource_files(root, manifest)],
            "scope": "Explicit software resources; no teaching originals, credentials or system fonts."}


def pyinstaller_arguments(root=ROOT, manifest=MANIFEST):
    arguments = []
    for relative, path in resource_files(root, manifest):
        arguments.extend(("--add-data", str(path) + ":" + Path(relative).parent.as_posix()))
    # Namespace package resource folders alone do not import dynamic modules.
    # Existing imports are still collected by PyInstaller; these text sources
    # are present solely where legacy code hashes or opens __file__.
    return arguments


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--arguments", action="store_true")
    args = parser.parse_args(argv)
    if args.arguments:
        print(json.dumps(pyinstaller_arguments(), ensure_ascii=True))
        return 0
    value = inventory()
    if args.inventory:
        args.inventory.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

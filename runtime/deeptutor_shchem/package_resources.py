"""An explicit resource manifest for trial builds, never a teacher-folder scan.

The list records the currently tracked software resources only. Finding them
is not equivalent to accepting the complete EXE or its teaching outputs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "integrations/deeptutor_shchem_v1/desktop_resources.json"



SCIENCE_JSONL = frozenset({
    "knowledge/textbook/knowledge.jsonl",
    "knowledge/lectures/part-a.jsonl",
    "knowledge/lectures/part-b.jsonl",
    "knowledge/lectures/part-c.jsonl",
})
SCIENCE_ROOTS = ("knowledge/textbook/", "knowledge/lectures/",
                 "knowledge/courseware/", "knowledge/lesson_design/")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key in resource")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite JSON constant in resource")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number in resource")
    return number


def _validate_jsonl(path, relative):
    if relative not in SCIENCE_JSONL:
        raise ValueError("Only declared science JSONL indexes may be packaged")
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Science JSONL resource exceeds size limit")
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > 512 * 1024:
            raise ValueError("Science JSONL record exceeds size limit")
        row = json.loads(line, object_pairs_hook=_unique_object,
                         parse_constant=_invalid_constant, parse_float=_finite_float)
        if not isinstance(row, dict):
            raise ValueError("Science JSONL records must be objects")
        rows.append(row)
    if not rows:
        raise ValueError("Science JSONL index is empty")


def _reject_reparse_path(root, target):
    cursor = target
    while cursor != root:
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            raise ValueError("Declared resource is a symlink or outside source tree")
        cursor = cursor.parent


def resource_files(root=ROOT, manifest=MANIFEST):
    root = Path(root).resolve()
    value = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if value.get("schema_version") != "shchem.desktop-resources.v1":
        raise ValueError("Unsupported resource list")
    if not isinstance(value.get("files"), list):
        raise ValueError("Invalid resource list")
    result, seen = [], set()
    for relative in value["files"]:
        if not isinstance(relative, str) or relative in seen:
            raise ValueError("Invalid or duplicate resource entry")
        path = PurePosixPath(relative)
        if (path.is_absolute() or ".." in path.parts or "\\" in relative
                or ":" in relative or not path.parts or path.as_posix() != relative):
            raise ValueError("Resource paths must be relative POSIX paths")
        if not relative.startswith(("integrations/", "knowledge/")):
            raise ValueError("Only declared software resources may be packaged")
        if relative.startswith("knowledge/") and not (
                relative == "knowledge/README.md" or relative.startswith(SCIENCE_ROOTS)):
            raise ValueError("Only declared science knowledge resources may be packaged")
        lexical = root / path
        _reject_reparse_path(root, lexical)
        target = lexical.resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("Declared resource is missing or outside source tree: " + relative)
        if target.suffix not in (".json", ".jsonl", ".md", ".svg", ".py"):
            raise ValueError("Unsupported resource type")
        if target.suffix == ".jsonl":
            _validate_jsonl(target, relative)
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

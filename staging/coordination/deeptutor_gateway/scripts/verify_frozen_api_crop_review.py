"""Read-only frozen-module verification; does not start the EXE or call APIs."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[4]
MODULES = (
    "desktop_facade", "desktop_personal_visual_questions", "desktop_personal_visual_attributes",
    "desktop_version", "desktop_visual_crop_review", "desktop_visual_egress",
    "desktop_visual_import_adapters", "desktop_visual_schema", "model_provider_settings",
    "visual_provider_runtime", "desktop_workbench.dialogs", "desktop_workbench.visual_import_egress_dialog",
    "desktop_preparation_provider", "desktop_word_semantic_tags",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a fresh evidence path; prior evidence was not changed.")
    spec = importlib.util.spec_from_file_location(
        "frozen_inspector", Path(__file__).with_name("verify_frozen_preparation_package.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = CArchiveReader(str(args.exe))
    pyz = archive.open_embedded_archive(next(name for name in archive.toc if name.endswith(".pyz")))
    results = [module.verify_source_matches_frozen(
        pyz, "integrations.deeptutor_shchem_v1." + name, ROOT
    ) for name in MODULES]
    report = {
        "exe": str(args.exe.resolve().relative_to(ROOT)), "bytes": args.exe.stat().st_size,
        "sha256": hashlib.sha256(args.exe.read_bytes()).hexdigest(), "modules": results,
        "provider_calls": 0, "started_exe": False, "human_ui_acceptance": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({**report, "modules": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

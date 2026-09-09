"""Compare shipped recovery bytecode with source, without importing app state."""

import argparse
import json
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[4]
MODULES = (
    "desktop_version",
    "desktop_chemistry_prompt_rules",
    "desktop_preparation_provider",
    "desktop_blueprint_generation",
    "desktop_blueprint_review",
    "intake_imports",
    "desktop_preparation",
    "desktop_preparation_visual",
    "desktop_preparation_recovery",
    "desktop_preparation_source_compare",
    "desktop_preparation_sources",
    "desktop_visual_import_v2",
    "word_handout_import",
    "word_native_math",
    "word_native_text",
    "desktop_word_question_index",
    "desktop_word_questions",
    "desktop_word_question_export",
    "desktop_workbench.word_question_dialog",
    "desktop_workbench.library_page",
    "desktop_facade",
    "desktop_workbench.dialogs",
    "desktop_workbench.main_window",
    "desktop_workbench.import_word_dialog",
    "desktop_workbench.workflow_pages",
    "desktop_workbench.paper_composer",
    "desktop_workbench.assembly_page",
    "desktop_workbench.preparation_recovery_dialog",
    "desktop_workbench.preparation_review_dialog",
    "source_crop_revision",
    "supplemental_answers",
    "answer_diagrams",
    "datong_answer_bindings",
    "datong_remaining_solutions",
    "datong_crop_revision",
    "candidate_review",
    "fengxian2025_theme2_direct_visual_scan",
    "question_visual_scan",
    "desktop_library",
    "desktop_workbench.library_detail",
    "paper_export_workbench",
    "paper_format_presets",
    "paper_export_renderer",
)


def normalized(code):
    return code.replace(
        co_filename="source-equivalence-check",
        co_consts=tuple(
            normalized(value) if isinstance(value, types.CodeType) else value
            for value in code.co_consts
        ),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise RuntimeError("Use a new report path")
    archive = CArchiveReader(str(args.exe))
    pyz = archive.open_embedded_archive(
        next(name for name in archive.toc if name.endswith(".pyz"))
    )
    results = {}
    for suffix in MODULES:
        name = "integrations.deeptutor_shchem_v1." + suffix
        source = ROOT / (name.replace(".", "/") + ".py")
        code = compile(source.read_bytes(), str(source), "exec", dont_inherit=True)
        results[name] = normalized(code) == normalized(pyz.extract(name))
    report = {
        "frozen_matches_current_source": results,
        "all_match": all(results.values()),
        "comparison": "code object equality after recursive filename normalization",
        "application_imported": False,
        "provider_called": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    if not report["all_match"]:
        raise RuntimeError("Shipped recovery code differs from tested source")


if __name__ == "__main__":
    main()

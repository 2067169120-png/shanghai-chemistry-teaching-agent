"""Read a completed local replay and inspect source/PPT comparison without calls."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation_source_compare import (
    compare_source_text,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_review_dialog import (
    PreparationReviewDialog,
)


class NoProvider:
    def __getattr__(self, name):
        raise AssertionError("Reference review must not read model settings")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)
    replay = (
        ROOT / "runtime/deeptutor_shchem/qa/returned-recovery-real-response-20260909-r2"
    )
    saved = json.loads((replay / "verification.json").read_text("utf-8"))
    paths = DesktopPaths.from_workspace(ROOT, state_root=saved["isolated_state"])
    task = saved["child"]["task_id"]
    state_files = list(paths.state_root.rglob("*.json"))
    before = {str(path): digest(path) for path in state_files}
    facade = DesktopWorkbenchFacade(paths, provider_store=NoProvider())
    report = facade.preparation_classroom_review(task)
    brief = json.loads(
        (
            ROOT
            / "runtime/deeptutor_shchem/qa/word-led-two-periods-v19-live-20260909/teacher-brief.json"
        ).read_text("utf-8")
    )
    assert report["source_reference"]["materials"] == brief["materials"]
    app = create_application([])
    install_font_fallbacks()
    view = PreparationReviewDialog(report)
    view.show()
    images, searches = [], {}
    for width, query in (
        (1060, ""),
        (1060, "氯化铵"),
        (420, "氯化铵"),
        (420, ""),
        (1060, "强电解质"),
    ):
        view.resize(width, 800)
        view.search.setText(query)
        view._compare()
        app.processEvents()
        target = output / f"compare-{width}-{len(images) + 1}.png"
        assert view.grab().save(str(target))
        images.append(str(target))
        if query:
            result = compare_source_text(report, query)
            searches[query] = {
                key: value
                for key, value in result.items()
                if key not in {"source_text", "student_text"}
            }
    assert {str(path): digest(path) for path in state_files} == before
    result = {
        "task_id": task,
        "source_materials_exactly_match_original_brief": True,
        "source_characters": len(brief["materials"]),
        "searches": searches,
        "screenshots": images,
        "registered_state_files_unchanged": True,
        "network_calls": 0,
        "visual_review": "pending",
        "semantic_source_adoption_verified": False,
        "teaching_approved": False,
    }
    (output / "verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=True))
    view.close()


if __name__ == "__main__":
    main()

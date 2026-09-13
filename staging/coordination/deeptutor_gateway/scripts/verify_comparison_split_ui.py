"""Replay the actual 40-page return offline and export PPT through native UI.

Uses an isolated PPT-only QA request, not the user's saved draft or paid task.
No provider configuration/transport is accessible. Existing teaching flaws are
not repaired here; the exported PPT is a software fixture, not a lesson delivery.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from verify_real_blueprint_preparation import BundledArtifactRenderer
from verify_returned_preparation_recovery import NoProviderSettings, RecordedResponse

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_recovery_dialog import (
    PreparationRecoveryDialog,
)


class LocalTasks:
    def submit(self, _label, operation, *, on_success, on_failure):
        on_success(operation())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-python", type=Path, required=True)
    parser.add_argument("--ui-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)
    source_dir = (
        ROOT / "runtime/deeptutor_shchem/qa/word-studied-live-generation-20260909"
    )
    raw_path = source_dir / "returned-candidate.json"
    raw_bytes = raw_path.read_bytes()
    assert (
        hashlib.sha256(raw_bytes).hexdigest()
        == "99675c2e109b299b81523525604e903e72e3a30681610865fd8af2c77ac53c76"
    )
    raw = json.loads(raw_bytes)
    brief = json.loads((source_dir / "teacher-brief.json").read_text("utf-8"))
    brief["output_kind"] = "ppt"  # explicit QA-only mode change, sources untouched
    paths = DesktopPaths.from_workspace(
        ROOT, state_root=Path(tempfile.mkdtemp(prefix="shchem-table-split-"))
    )
    facade = DesktopWorkbenchFacade(
        paths,
        provider_store=NoProviderSettings(),
        preparation_renderer=BundledArtifactRenderer(args.artifact_python),
    )
    manager = facade._preparation_manager_instance()
    asset_dir = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义精读生成材料/assets"
    assets = {
        hashlib.sha256(p.read_bytes()).hexdigest(): p
        for p in asset_dir.iterdir()
        if p.is_file()
    }
    for asset in brief["image_assets"]:
        data = assets[asset["sha256"]].read_bytes()
        assert (
            manager.image_store.import_bytes(
                data, asset["caption"], asset["source"], asset["purpose"]
            )
            == asset
        )
    parent = manager.prepare(brief, "offline-recorded", "offline-recorded-v1")
    recorded = RecordedResponse(raw)
    failed = manager.run(parent["task_id"], recorded)
    assert failed["status"] == "failed" and failed["returned_candidate_available"]
    parent_file = manager.tasks_root / (parent["task_id"] + ".json")
    parent_bytes = parent_file.read_bytes()
    source = facade.preparation_returned_source(parent["task_id"])
    app = create_application([])
    install_font_fallbacks()
    dialog = PreparationRecoveryDialog(facade, LocalTasks(), source)
    dialog.show()
    assert dialog.current_number == 17
    for number, cut in ((17, 3), (28, 2)):
        dialog.group.setCurrentIndex(dialog.group.findData(number))
        dialog.split_enabled.click()
        dialog.split_after.setValue(cut)
        dialog.first_minutes.setValue(1)
    images = []
    for width in (980, 420):
        dialog.resize(width, 820)
        for tab in (0, 2):
            dialog.tabs.setCurrentIndex(tab)
            for _ in range(4):
                app.processEvents()
            image_path = output / f"split-{width}-tab{tab}.png"
            assert dialog.grab().save(str(image_path))
            images.append(str(image_path))
    edits = dialog._edits()
    if args.ui_only:
        dialog.splits.clear()
        dialog.pending.clear()
        dialog.close()
        print(json.dumps({"screenshots": images, "export_started": False}))
        return
    summaries = []
    dialog.revision_saved.connect(summaries.append)
    dialog.save_button.click()
    assert summaries, dialog.status.text()
    child = summaries[0]
    assert child.status == "completed", child.status
    candidate_path = facade.preparation_artifact_path(child.task_id, "candidate_json")
    candidate = json.loads(candidate_path.read_text("utf-8"))
    assert len(candidate["slides"]) == 42
    for original, first_index in ((17, 16), (28, 28)):
        pair = candidate["slides"][first_index : first_index + 2]
        assert [
            r for page in pair for r in page["visual"]["comparison"]["rows"]
        ] == raw["slides"][original - 1]["visual"]["comparison"]["rows"]
    assert (
        parent_file.read_bytes() == parent_bytes and raw_path.read_bytes() == raw_bytes
    )
    report = {
        "network_calls": 0,
        "recorded_replays": recorded.calls,
        "isolated_state": str(paths.state_root),
        "parent_task_id": parent["task_id"],
        "child_task_id": child.task_id,
        "child_status": child.status,
        "slide_count": child.slide_count,
        "explicit_edits": edits,
        "source_sha256": source["source_revision"],
        "parent_and_source_unchanged": True,
        "all_ten_table_rows_preserved": True,
        "raw_slide_total_minutes": sum(s["minutes"] for s in raw["slides"]),
        "canonical_slide_total_minutes": sum(s["minutes"] for s in candidate["slides"]),
        "note": "PPT-only isolated software QA. Normalizer still reconciles original 81-minute input to requested80; content flaws remain. Not R9 replacement or teaching approval.",
        "screenshots": images,
        "visual_review": "pending",
        "pptx": str(facade.preparation_artifact_path(child.task_id, "pptx")),
    }
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True))
    dialog.close()


if __name__ == "__main__":
    main()

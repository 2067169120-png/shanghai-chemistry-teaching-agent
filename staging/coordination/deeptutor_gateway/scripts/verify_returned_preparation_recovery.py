"""Replay a saved response locally, then repair through the production facade.

No transport is constructed and no model configuration is read. The replay is
not another model result. Only the explicit table-header repair differs from
the recorded response; existing content-quality failures remain failures.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from verify_real_blueprint_preparation import BundledArtifactRenderer

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
    PreparationImageStore,
)


class NoProviderSettings:
    def __getattr__(self, name):
        raise AssertionError("Recovery must not access provider settings")


class RecordedResponse:
    def __init__(self, raw):
        self.raw, self.calls = raw, 0

    def generate(self, *args, **kwargs):
        self.calls += 1
        return deepcopy(self.raw)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-python", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)
    source_root = (
        ROOT / "runtime/deeptutor_shchem/qa/word-led-two-periods-v19-live-20260909"
    )
    raw_file, brief_file = (
        source_root / "returned-candidate.json",
        source_root / "teacher-brief.json",
    )
    before = {str(p): digest(p) for p in (raw_file, brief_file)}
    raw, brief = [json.loads(p.read_text("utf-8")) for p in (raw_file, brief_file)]
    old = json.loads((source_root / "verification.json").read_text("utf-8"))
    old_paths = DesktopPaths.from_workspace(ROOT, state_root=old["isolated_state"])
    old_images = PreparationImageStore(old_paths.task_root / "preparation-v1/images")
    state = Path(tempfile.mkdtemp(prefix="shchem-returned-recovery-"))
    paths = DesktopPaths.from_workspace(ROOT, state_root=state)
    renderer = BundledArtifactRenderer(args.artifact_python)
    facade = DesktopWorkbenchFacade(
        paths, provider_store=NoProviderSettings(), preparation_renderer=renderer
    )
    manager = facade._preparation_manager_instance()
    for asset in brief["image_assets"]:
        imported = manager.image_store.import_bytes(
            old_images.load(asset), asset["caption"], asset["source"], asset["purpose"]
        )
        assert imported == asset
    parent = manager.prepare(
        brief, "recorded-response-offline", "recorded-response-offline-v1"
    )
    recorded = RecordedResponse(raw)
    failed = manager.run(parent["task_id"], recorded)
    assert failed["status"] == "failed" and failed["returned_candidate_available"]
    assert recorded.calls == 1
    parent_task_file = manager.tasks_root / (parent["task_id"] + ".json")
    parent_bytes = parent_task_file.read_bytes()
    facade = DesktopWorkbenchFacade(
        paths, provider_store=NoProviderSettings(), preparation_renderer=renderer
    )
    assert facade.get_preparation(parent["task_id"]).returned_candidate_available
    source = facade.preparation_returned_source(parent["task_id"])
    assert source["candidate"] == raw
    table = deepcopy(raw["slides"][14]["visual"]["comparison"])
    table["columns"] = ["判断依据或条件", "典型例式"]
    edits = [{"slide_number": 15, "comparison": table}]
    (output / "explicit-edits.json").write_text(
        json.dumps(edits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    child = facade.repair_returned_preparation(
        parent["task_id"],
        source["source_revision"],
        edits,
        note="隔离QA显式修订：第15页改为判断依据或条件、典型例式两个数据列表头，保留全部行标题和单元格；不是自动生成质量通过。",
    )
    report = {
        "network_calls": 0,
        "recorded_response_replay_calls": recorded.calls,
        "source_files": before,
        "isolated_state": str(state),
        "parent_task_id": parent["task_id"],
        "returned_source_revision": source["source_revision"],
        "reopened_failed_return": True,
        "child": asdict(child),
        "artifacts": {},
        "content_audit": "failed_preexisting_not_revised",
        "classroom_ready": False,
        "visual_review": "pending",
    }
    if child.status == "completed":
        for artifact_id in child.artifact_ids:
            source_path = facade.preparation_artifact_path(child.task_id, artifact_id)
            target = output / source_path.name
            shutil.copy2(source_path, target)
            report["artifacts"][artifact_id] = {
                "path": str(target),
                "sha256": digest(target),
            }
        saved = json.loads(
            facade.preparation_artifact_path(child.task_id, "candidate_json").read_text(
                "utf-8"
            )
        )
        assert saved["slides"][14]["visual"]["comparison"] == table
        assert table["rows"] == raw["slides"][14]["visual"]["comparison"]["rows"]
    assert parent_task_file.read_bytes() == parent_bytes
    assert facade.preparation_returned_source(parent["task_id"])["candidate"] == raw
    assert before == {str(p): digest(p) for p in (raw_file, brief_file)}
    child_record = facade._preparation_manager_instance().get_task(child.task_id)
    assert (
        child_record["model_invoked"] is False
        and child.source_kind == "teacher_revision"
    )
    report.update(
        parent_unchanged=True,
        original_return_unchanged=True,
        input_files_unchanged=True,
        child_model_invoked=False,
    )
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "child_status": child.status,
                "slide_count": child.slide_count,
                "network_calls": 0,
            },
            ensure_ascii=True,
        )
    )
    return 0 if child.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Exercise source recrops and supplemental solutions through real local readers.

Read using the software environment; author/render in the bundled artifact Python.
No provider calls, central-data mutations, or human approval records are made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1 import paper_export_workbench
from integrations.deeptutor_shchem_v1.answer_diagrams import (
    answer_diagram_png,
    answer_diagram_svg,
)
from integrations.deeptutor_shchem_v1.candidate_review import Wave1CandidateReviewReader
from integrations.deeptutor_shchem_v1.desktop_library_session import (
    snapshot_reader_graph,
)
from integrations.deeptutor_shchem_v1.fengxian2025_theme2_direct_visual_scan import (
    EXPECTED_MASTER_NODE_IDS,
    Fengxian2025Theme2DirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.paper_export_workbench import (
    PaperExportJobManager,
)
from integrations.deeptutor_shchem_v1.question_visual_scan import (
    QuestionVisualScanReader,
)
from integrations.deeptutor_shchem_v1.supplemental_answers import (
    all_supplemental_answers,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader


def render_in_bundled_runtime(bundle, *, output_dir, toolchain, asset_root):
    # The application environment has source-reader dependencies; the artifact
    # runtime owns python-docx, Pillow and rendering. Use the real renderer CLI.
    bundle_path = output_dir.parent / "render_bundle.json"
    assert json.loads(bundle_path.read_text(encoding="utf-8")) == bundle
    result = subprocess.run([
        str(toolchain.python_exe), "-X", "utf8", "-m",
        "integrations.deeptutor_shchem_v1.paper_export_renderer", "render",
        "--bundle", str(bundle_path), "--output-dir", str(output_dir),
        "--asset-root", str(asset_root), "--python-exe", str(toolchain.python_exe),
        "--render-docx-script", str(toolchain.render_docx_script),
        "--pdftoppm-exe", str(toolchain.pdftoppm_exe),
        "--conversion-backend", "word_com", "--dpi", "300",
    ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=600,
       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode:
        raise RuntimeError(result.stderr[-1800:] or result.stdout[-1800:])
    return json.loads((output_dir / "render_qa_report.json").read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--datong-only", action="store_true")
    parser.add_argument("--all-datong-themes", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    image_root = output / "recrops"
    image_root.mkdir(exist_ok=True)
    db = ROOT / "sh-chem-db"
    fx, themes, scans, crops = snapshot_reader_graph((
        Fengxian2025Theme2DirectVisualScanReader(db),
        ThemeWorkbenchReader(db), QuestionVisualScanReader(db), Wave1CandidateReviewReader(db),
    ))
    views = {}
    for node_id in (() if args.datong_only else EXPECTED_MASTER_NODE_IDS):
        for descriptor in fx.detail(node_id)["evidence_descriptors"]:
            crop_id = descriptor["crop_id"]
            data = fx.question_crop(node_id, crop_id).data
            assert hashlib.sha256(data).hexdigest() == descriptor["sha256"]
            (image_root / (crop_id + ".png")).write_bytes(data)
            views[crop_id] = descriptor
    assert len(views) == (0 if args.datong_only else 12)
    datong_views = {}
    answers = all_supplemental_answers()
    for answer in answers:
        scan = scans.detail(answer["node_id"])
        assert scan["reference_answer"]["availability"] == "absent"
        assert scan["supplemental_answer"] == answer
        for descriptor in scan["evidence_descriptors"]:
            crop_id = descriptor["crop_id"]
            data = crops.question_crop(answer["node_id"], crop_id).data
            assert hashlib.sha256(data).hexdigest() == descriptor["sha256"]
            (image_root / (crop_id + ".png")).write_bytes(data)
            datong_views[crop_id] = descriptor
        if answer["diagram_key"]:
            (output / (answer["diagram_key"] + ".svg")).write_text(
                answer_diagram_svg(answer["diagram_key"]), encoding="utf-8")
            (output / (answer["diagram_key"] + ".png")).write_bytes(answer_diagram_png(answer["diagram_key"]))
    (output / "supplemental-answers.json").write_text(
        json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    catalog = themes.groups("wave1")
    snapshot_id = hashlib.sha256(paper_export_workbench._canonical_json_bytes(catalog)).hexdigest()
    paper_export_workbench.render_export_bundle = render_in_bundled_runtime
    manager = PaperExportJobManager(output / "isolated-export-state")
    request = {
        "title_zh": "高一化学综合练习" if args.all_datong_themes else "氯及其化合物专题练习",
        "subtitle_zh": "大同高一期中五主题 41 道题" if args.all_datong_themes else "大同高一期中第一主题 9 道题",
        "duration_minutes": 80 if args.all_datong_themes else 25,
        "numbering_mode": "continuous_across_paper",
        "score_per_atomic": 2,
        "answer_space_lines": 2,
        "selections": [{"scope": "wave1", "selection_unit": "theme",
                        "theme_id": f"W1-DT2025-H1-MID-T0{theme}", "target_atomic_id": None,
                        "expected_data_snapshot_id": snapshot_id}
                       for theme in (range(1, 6) if args.all_datong_themes else [1])],
    }
    job = manager.start(request, theme_catalog_loader=lambda _: catalog,
                        detail_loader=lambda _, node: scans.detail(node),
                        crop_loader=lambda _, node, crop: crops.question_crop(node, crop))
    print(json.dumps({"stage": "export_started", "job_id": job["job_id"]}), flush=True)
    manager._executor.shutdown(wait=True, cancel_futures=False)
    manager.shutdown(wait=True)
    completed = manager._read_job(job["job_id"])
    report = {
        "recrop_count": len(views) + sum("presentation_revision_id" in row for row in datong_views.values()),
        "recrops": views, "datong_views": datong_views,
        "supplemental_answer_count": len(answers),
        "diagram_count": len({answer["diagram_key"] for answer in answers if answer["diagram_key"]}),
        "original_source_answer_state_unchanged": True,
        "export_status": completed["status"], "export_error": completed["error"],
        "artifacts": completed["artifacts"], "job_id": job["job_id"],
        "visual_review_complete": False,
    }
    (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("recrop_count", "supplemental_answer_count", "diagram_count", "export_status", "export_error")}, ensure_ascii=False))
    return 0 if completed["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

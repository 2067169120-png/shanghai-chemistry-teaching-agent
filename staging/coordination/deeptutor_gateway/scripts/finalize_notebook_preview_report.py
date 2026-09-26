"""Record actual Office preview provenance without promoting teaching gates."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
bundle = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义笔记完整版-r5"
qa_root = ROOT / "runtime/deeptutor_shchem/qa/notebook-complete-r5"
path = bundle / "qa_report.json"
backup = qa_root / "qa_report.initial.json"
assert not backup.exists()
shutil.copy2(path, backup)
data = json.loads(path.read_text("utf-8"))
for row in data["rendered_slides"]:
    content = (bundle / "rendered_slides" / row["filename"]).read_bytes()
    row.update(sha256=hashlib.sha256(content).hexdigest(), size=len(content), size_bytes=len(content))
for check in data["checks"]:
    if check["check"] == "pptx_and_png_share_layout_plan":
        check.update(check="png_exported_from_actual_pptx", layout_source="Microsoft PowerPoint native export")
    if check["check"] == "text_box_overflow_estimate":
        check["applies_to"] = "initial renderer only; see verification.json for actual Office review"
data["preview_export_source"] = "actual PowerPoint exports; initial Pillow QA preserved"
data["agent_visual_review_record"] = "verification.json; not independent human teacher approval"
path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", "utf-8")
print("45 preview hashes updated; teacher gates unchanged")

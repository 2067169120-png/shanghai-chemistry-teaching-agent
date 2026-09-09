"""Render only PPT/PNG from an existing candidate, without DOCX or model calls."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    _FontBook, _make_layouts, _write_pptx, _draw_layout, _save_png,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    root.mkdir(parents=True, exist_ok=False)
    data = args.candidate.read_bytes()
    candidate = json.loads(data)
    assets = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义精读生成材料/assets"
    by_hash = {hashlib.sha256(p.read_bytes()).hexdigest(): p for p in assets.iterdir() if p.is_file()}
    images = {a["asset_id"]: by_hash[a["sha256"]].read_bytes() for a in candidate.get("image_assets", [])}
    layouts = _make_layouts(candidate, image_data=images)
    _write_pptx(root / "lesson_presentation.pptx", layouts, candidate, root=root)
    fonts = _FontBook()
    for index, layout in enumerate(layouts, 1):
        _save_png(_draw_layout(layout, fonts), root / f"slide-{index:02d}.png", root=root)
    assert args.candidate.read_bytes() == data
    report = {
        "candidate": str(args.candidate.resolve()),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_unchanged": True, "slide_count": len(layouts),
        "model_calls": 0, "docx_created": False,
        "slides": [layout.public() for layout in layouts],
        "visual_review": "pending", "office_measurement": "pending",
    }
    (root / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != "slides"}, ensure_ascii=True))


if __name__ == "__main__":
    main()

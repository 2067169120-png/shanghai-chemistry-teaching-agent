"""Replay frozen teaching content through current PPT layout, offline only.

Uses the same layout, PPT and PNG writers as the native renderer. Does not
produce or modify Word documents, normalize content, or invoke a provider.
Run with the bundled artifact Python.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    _draw_layout,
    _FontBook,
    _make_layouts,
    _write_pptx,
)

FIGURE = (
    ROOT / "outputs/备课/2026-09-09-电解质与电离方程式-修订版/textbook-figure-2-14.png"
)
FIGURE_SHA = "f375638dde10576147a56d6c71c890187ca9a3e6179236612b3077733554f3bf"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.candidate.resolve(strict=True)
    source.relative_to(ROOT)
    output = args.output.resolve()
    output.relative_to(ROOT)
    if digest(source) != args.sha256 or digest(FIGURE) != FIGURE_SHA:
        raise RuntimeError("Frozen input differs")
    candidate = json.loads(source.read_text("utf-8"))
    before = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    image_data = {candidate["image_assets"][0]["asset_id"]: FIGURE.read_bytes()}
    layouts = _make_layouts(candidate, image_data=image_data)
    output.mkdir(parents=True, exist_ok=False)
    pptx = output / "lesson_presentation.pptx"
    _write_pptx(pptx, layouts, candidate, root=output)
    fontbook = _FontBook()
    overflows = []
    intros = []
    for number, layout in enumerate(layouts, 1):
        _draw_layout(layout, fontbook).save(output / f"slide-{number:02d}.png")
        for element in layout.elements:
            if element.overflow:
                overflows.append({"page": number, "paths": list(element.source_paths)})
            if any(
                path.startswith(f"/slides/{number - 1}/content/")
                for path in element.source_paths
            ):
                intros.append(
                    {
                        "page": number,
                        "font_px": element.font_px,
                        "height": element.height,
                        "y": element.y,
                    }
                )
    assert json.dumps(candidate, ensure_ascii=False, sort_keys=True) == before
    assert digest(source) == args.sha256 and digest(FIGURE) == FIGURE_SHA
    receipt = {
        "method": "unchanged_frozen_candidate_current_PPT_layout_only",
        "input_sha256": args.sha256,
        "pptx_sha256": digest(pptx),
        "slide_count": len(layouts),
        "source_and_content_unchanged": True,
        "new_model_calls": 0,
        "docx_created": False,
        "text_overflows": overflows,
        "content_layout": intros,
        "visual_review": "pending",
        "teacher_review_required": True,
    }
    (output / "layout-replay.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()

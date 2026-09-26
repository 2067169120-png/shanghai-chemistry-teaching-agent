"""Source-window QA contact sheets; no renderer, model, or personal state.

The sheets are private review aids, not lesson materials or approval records.
Source/candidate assets remain read-only. Run from the project root.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from integrations.deeptutor_shchem_v1.fudan2026_april_theme5_zns_direct_visual_scan import (
    EXPECTED_ATOMIC_IDS,
    Fudan2026AprilTheme5ZnsDirectVisualScanReader,
)


def verify(workspace: Path) -> dict:
    reader = Fudan2026AprilTheme5ZnsDirectVisualScanReader(workspace / "sh-chem-db")
    snapshot = reader._snapshot()
    output = (
        workspace / "runtime/deeptutor_shchem/fudan_zns_native_20260912/display-review"
    )
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    paths = []
    for role in ("question", "shared_material", "answer"):
        crops = [crop for crop in snapshot.crop_by_id.values() if crop["role"] == role]
        width = max(crop["width"] for crop in crops) + 20
        height = sum(crop["height"] + 35 for crop in crops) + 10
        sheet = Image.new("RGB", (width, height), "#e9edf0")
        draw = ImageDraw.Draw(sheet)
        top = 10
        for crop in crops:
            data = snapshot.output_bytes[crop["output_path"]]
            assert hashlib.sha256(data).hexdigest() == crop["sha256"]
            with Image.open(io.BytesIO(data)) as image:
                draw.text((10, top), crop["display_key"] + " | " + role, fill="black")
                sheet.paste(image.convert("RGB"), (10, top + 20))
            top += crop["height"] + 35
            rows.append(
                {
                    key: crop[key]
                    for key in (
                        "display_key",
                        "role",
                        "sha256",
                        "source_sha256",
                        "source_page",
                        "source_crop_box",
                        "width",
                        "height",
                    )
                }
            )
        path = output / (role + ".png")
        sheet.save(path)
        paths.append(str(path))
    report = {
        "source_windows": rows,
        "atomic_units": list(EXPECTED_ATOMIC_IDS),
        "question_windows": 8,
        "shared_windows": 6,
        "answer_windows": 8,
        "contact_sheets": paths,
        "model_calls": 0,
        "human_review_complete": False,
        "word_pagination_reviewed": False,
        "publication_allowed": False,
    }
    (output / "image-bindings.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    result = verify(Path(__file__).resolve().parents[4])
    print(
        json.dumps(
            {
                "sheets": result["contact_sheets"],
                "source_windows": len(result["source_windows"]),
            },
            ensure_ascii=False,
        )
    )

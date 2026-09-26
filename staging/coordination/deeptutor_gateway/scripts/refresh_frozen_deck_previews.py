"""Draw an existing frozen deck with current glyph handling, without reflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    _draw_layout,
    _FontBook,
    _LayoutElement,
    _montage,
    _SlideLayout,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--powerpoint", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    deck = json.loads((args.bundle / "deck.json").read_text("utf-8"))
    if args.powerpoint:
        from PIL import Image

        images = []
        for slide in deck["slides"]:
            number = slide["page_no"]
            source = args.powerpoint / f"slide-{number:02d}.png"
            shutil.copy2(source, args.output / f"slide-{number}.png")
            with Image.open(source) as picture:
                images.append(picture.convert("RGB"))
        _montage(images).save(args.output / "rendered_montage.png")
        print(json.dumps({"pages": len(images), "mode": "actual_powerpoint_export"}))
        return
    with zipfile.ZipFile(args.bundle / "lesson_presentation.pptx") as archive:
        payload = {
            hashlib.sha256(data).hexdigest(): data
            for name in archive.namelist()
            if name.startswith("ppt/media/")
            for data in [archive.read(name)]
        }
    fonts = _FontBook()
    images = []
    for slide in deck["slides"]:
        elements = []
        for value in slide["elements"]:
            x, y, width, height = value["bbox_px"]
            fields = {
                key: value[key]
                for key in (
                    "fill",
                    "line",
                    "text",
                    "color",
                    "bold",
                    "align",
                    "valign",
                    "structural",
                    "overflow",
                )
                if key in value
            }
            fields["font_px"] = round(value.get("font_size_pt", 19.2) * 120 / 72)
            if value["element_type"] == "image":
                fields["image_data"] = payload[value["embedded_sha256"]]
            elements.append(
                _LayoutElement(value["element_type"], x, y, width, height, **fields)
            )
        layout = _SlideLayout(
            slide["page_no"],
            slide["slide_id"],
            slide["slide_type"],
            slide["title"],
            tuple(elements),
        )
        image = _draw_layout(layout, fonts)
        image.save(args.output / f"slide-{slide['page_no']}.png")
        images.append(image)
    _montage(images).save(args.output / "rendered_montage.png")
    print(
        json.dumps(
            {
                "pages": len(images),
                "mode": "frozen_boxes_no_reflow",
                "source_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()

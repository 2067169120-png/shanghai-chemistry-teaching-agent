"""Capture visual resume disclosure using synthetic pages only."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    from PIL import Image, ImageDraw
    from integrations.deeptutor_shchem_v1.desktop_visual_egress import DesktopVisualEgressService
    from integrations.deeptutor_shchem_v1.desktop_visual_schema import visual_import_request_policy
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.visual_import_egress_dialog import VisualImportEgressDialog

    app = create_application(["visual-resume-qa"])
    policy = visual_import_request_policy("https://example.invalid/v1", "synthetic-vision", "responses")
    pixels, base_pages = {}, []
    for index in range(1, 4):
        image = Image.new("RGB", (600, 760), "#fcfbf7")
        draw = ImageDraw.Draw(image)
        draw.text((40, 40), f"SYNTHETIC REVIEW PAGE {index}", fill="#20483f", font_size=25)
        draw.line((40, 85, 560, 85), fill="#20483f", width=3)
        for row in range(5):
            draw.text((40, 120 + row * 70), f"{row + 1}. Example content for visual resume", fill="#45544f", font_size=20)
            draw.line((60, 156 + row * 70, 500, 156 + row * 70), fill="#cbd5cf", width=2)
        draw.rectangle((70, 500, 530, 670), outline="#477d6b", width=3)
        draw.text((105, 555), "LOCAL SYNTHETIC FIXTURE", fill="#477d6b", font_size=21)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        raw = stream.getvalue()
        page_id = f"PAGE-{index}"
        pixels[page_id] = raw
        base_pages.append({
            "page_id": page_id, "source_name": "化学复习讲义（合成验收）.pdf",
            "source_role": "handout", "page_number": index,
            "sha256": hashlib.sha256(raw).hexdigest(), "width": 600, "height": 760,
            "mime_type": "image/png",
        })
    captures = []
    for mode, send_count in (("partial", 1), ("local", 0)):
        pages = [{**page, "will_send": index >= 3 - send_count} for index, page in enumerate(base_pages)]
        plan = {
            "preview_id": "SYNTHETIC-PREVIEW", "revision": "SYNTHETIC-REVISION",
            "batch_id": "SYNTHETIC-BATCH", "model_label": "合成模型 · 不调用网络",
            "request_policy": policy, "pages": pages,
            "resume": {"send_page_count": send_count, "reused_page_count": 3 - send_count},
            "confirmation_text": DesktopVisualEgressService._confirmation_text("合成模型 · 不调用网络", pages, policy),
        }
        for width, height in ((900, 820), (420, 900), (360, 900)):
            dialog = VisualImportEgressDialog(plan, pixels.__getitem__)
            dialog.resize(width, height)
            dialog.show()
            for _ in range(50):
                app.processEvents()
            assert dialog.width() == width, (mode, width, dialog.width())
            assert dialog.confirm_button.isEnabled()
            assert dialog.image_list.horizontalScrollBar().maximum() == 0
            assert dialog.rect().contains(dialog.confirm_button.mapTo(dialog, dialog.confirm_button.rect().bottomRight()))
            path = output / f"visual-resume-{mode}-{width}x{height}.png"
            assert dialog.grab().save(str(path))
            captures.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            dialog.reject()
            dialog.deleteLater()
            app.processEvents()
    (output / "manifest.json").write_text(json.dumps({
        "scope": "Synthetic UI; no personal source/state or provider access",
        "captures": captures, "provider_calls": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"captures": len(captures), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Render directory-repair states using generated pages, no provider or personal state."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")


def fixture(mode):
    from PIL import Image, ImageDraw
    from integrations.deeptutor_shchem_v1.desktop_visual_egress import DesktopVisualEgressService
    from integrations.deeptutor_shchem_v1.desktop_visual_schema import visual_import_request_policy

    pages, pixels = [], {}
    for index in range(1, 4):
        image = Image.new("RGB", (600, 760), "#fcfbf7")
        draw = ImageDraw.Draw(image)
        draw.text((35, 40), f"SYNTHETIC SOURCE PAGE {index}", fill="#20483f", font_size=26)
        for row in range(6):
            draw.text((35, 140 + row * 65), f"{row + 1}. Local recovery example content", fill="#42564b", font_size=22)
        draw.rectangle((40, 570, 555, 690), outline="#487e6b", width=3)
        draw.text((70, 615), "NO REAL DOCUMENT / NO API", fill="#487e6b", font_size=24)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        raw, page_id = stream.getvalue(), f"PAGE-{index}"
        pixels[page_id] = raw
        pages.append({"page_id": page_id, "source_name": "合成化学讲义.pdf", "source_role": "handout",
                      "page_number": index, "width": 600, "height": 760, "mime_type": "image/png",
                      "sha256": hashlib.sha256(raw).hexdigest(), "will_send": False,
                      "checkpoint_state": "repair_selected" if mode == "revised" and index < 3 else "repair_pending"})
    policy = visual_import_request_policy("https://example.invalid/v1", "synthetic-vision", "responses")
    shards = [
        {"shard_id": "group-1", "shard_index": 1, "page_ids": [p["page_id"] for p in pages[:2]]},
        {"shard_id": "group-2", "shard_index": 2, "page_ids": [pages[2]["page_id"]],
         "unavailable_reason": "记录与当前页面内容不一致。这组不能本机恢复，请先核对来源。"},
    ]
    options = [
        {"option_id": "choice-1", "shard_id": "group-1", "shard_index": 1, "version": 1,
         "summary": "已有识别记录：2 个题目片段、3 处图像依据。\n记录摘录：合成练习甲，比较两组实验现象，并补全表格。\n记录摘录：说明观察结果与实验条件的关系。"},
        {"option_id": "choice-2", "shard_id": "group-1", "shard_index": 1, "version": 2,
         "summary": "已有识别记录：2 个题目片段、3 处图像依据。\n记录摘录：合成练习甲，比较两组实验现象，填写控制变量。\n另一份旧记录，需与原页比较后选择。候选编号不代表新旧或正确程度。"},
    ]
    if mode != "unrecoverable":
        options.append({"option_id": "choice-3", "shard_id": "group-2", "shard_index": 2, "version": 1,
                        "summary": "已有识别记录：1 个题目片段、2 处图像依据。\n记录摘录：合成练习乙，请整理实验结论。"})
    plan = {"preview_id": "SYNTHETIC-PREVIEW", "revision": "SYNTHETIC-REVISION", "batch_id": "SYNTHETIC-BATCH",
            "model_label": "合成模型 · 不调用网络", "request_policy": policy, "pages": pages, "shards": shards,
            "can_confirm": mode == "revised", "repair": {
                "issues": ["当前使用的页组版本记录损坏，不能自动选择旧版本。"], "options": options,
                "selected_option_ids": ["choice-2"] if mode == "revised" else [],
                "selected_shards": int(mode == "revised"), "unavailable_shards": int(mode == "unrecoverable"),
            },
            "confirmation_text": "当前使用的页组版本记录损坏，无法自动恢复选择。\n"
                "可选记录已核对来源文件、页面像素、模型配置、处理规则与页组内容。\n"
                "同组多个候选需明确选择；候选编号不代表日期或质量。可切换原页检查内容。\n"
                + ("本次选择恢复 1 组，其余 1 组保持待处理。\n" if mode == "revised" else "尚未选择记录。\n")
                + "确认只在本机修复目录。旧记录与原目录字节保留，不调用模型。\n"
                "完成后返回普通预览；尚未恢复的页组须再次确认后才可发送。恢复内容仍待教师核对。"}
    if mode == "ordinary":
        plan.pop("repair")
        plan["can_confirm"] = True
        for index, page in enumerate(pages):
            page.update(will_send=index == 2, checkpoint_state="pending" if index == 2 else "saved")
        for index, shard in enumerate(shards):
            shard.update(reprocess=False, stored_state="pending" if index else "saved")
        plan["resume"] = {"send_page_count": 1, "reused_page_count": 2, "blocked_page_count": 0}
        plan["confirmation_text"] = DesktopVisualEgressService._confirmation_text(plan["model_label"], pages, policy)
    return plan, pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    from PySide6.QtCore import QCoreApplication, QEvent
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.visual_import_egress_dialog import VisualImportEgressDialog

    app = create_application(["visual-repair-qa"])
    captures = []
    for mode in ("damaged", "revised", "unrecoverable", "ordinary"):
        plan, pixels = fixture(mode)
        sizes = [(360, 900), (420, 900), (900, 820)]
        if mode in {"revised", "unrecoverable"}:
            sizes.append((360, 520))
        for width, height in sizes:
            dialog = VisualImportEgressDialog(plan, pixels.__getitem__)
            dialog.resize(width, height)
            dialog.show()
            if mode != "ordinary":
                dialog.repair_list.setCurrentRow(2 if mode == "unrecoverable" else 1)
            for _ in range(60):
                app.processEvents()
            assert (dialog.width(), dialog.height()) == (width, height)
            assert dialog.confirm_button.isEnabled() == (mode in {"revised", "ordinary"})
            assert dialog.rect().contains(dialog.confirm_button.mapTo(dialog, dialog.confirm_button.rect().bottomRight()))
            assert dialog.rect().contains(dialog.cancel_button.mapTo(dialog, dialog.cancel_button.rect().bottomRight()))
            if mode != "ordinary":
                assert dialog.repair_list.horizontalScrollBar().maximum() == 0
                assert dialog.repair_details.horizontalScrollBar().maximum() == 0
            for position in (("top", "bottom") if height == 520 else ("whole",)):
                if height == 520:
                    scrollbar = dialog.repair_scroll.verticalScrollBar()
                    assert scrollbar.maximum() > 0
                    scrollbar.setValue(scrollbar.maximum() if position == "bottom" else 0)
                    for _ in range(10):
                        app.processEvents()
                    assert dialog.repair_scroll.horizontalScrollBar().maximum() == 0
                for button in (dialog.confirm_button, dialog.cancel_button):
                    assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
                if mode != "ordinary" and position != "top":
                    assert dialog.rect().contains(dialog.revise_button.mapTo(dialog, dialog.revise_button.rect().bottomRight()))
                path = output / f"repair-{mode}-{width}x{height}-{position}.png"
                assert dialog.grab().save(str(path))
                captures.append({"mode": mode, "width": width, "height": height, "scroll_position": position, "path": str(path),
                                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "device_pixel_ratio": dialog.devicePixelRatioF(),
                                 "confirm_enabled": dialog.confirm_button.isEnabled()})
            dialog.reject()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    (output / "manifest.json").write_text(json.dumps({
        "scope": "Synthetic Qt rendering only; no original documents, provider, DB or personal state",
        "provider_calls": 0, "scale_factor": os.environ["QT_SCALE_FACTOR"], "captures": captures,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "captures": len(captures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Synthetic Qt gallery evidence; no source documents, personal data or API."""

import argparse
import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from integrations.deeptutor_shchem_v1.desktop_visual_egress import (
    DesktopVisualEgressService,
)
from integrations.deeptutor_shchem_v1.desktop_visual_schema import (
    visual_import_request_policy,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.visual_import_egress_dialog import (
    VisualImportEgressDialog,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=980)
    parser.add_argument("--height", type=int, default=720)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve existing screenshot evidence; choose a fresh output.")
    app = create_application([])
    install_font_fallbacks()
    pages, contents = [], {}
    for number, (role, title) in enumerate((
        ("question", "题目与共同材料"),
        ("answer", "参考答案与解析"),
        ("handout", "课堂知识与方法"),
    ), 1):
        picture = QImage(1050, 740, QImage.Format.Format_RGB32)
        picture.fill(QColor("white"))
        painter = QPainter(picture)
        painter.setPen(QColor("#173e4e"))
        painter.setFont(QFont("Microsoft YaHei UI", 25, QFont.Weight.Bold))
        painter.drawText(QRectF(48, 32, 940, 75), Qt.AlignmentFlag.AlignLeft, title)
        painter.setFont(QFont("Microsoft YaHei UI", 15))
        painter.drawText(QRectF(48, 110, 940, 60), Qt.AlignmentFlag.AlignLeft,
                         "界面合成测试材料 · 不是教材、原题或模型返回")
        painter.setFont(QFont("Microsoft YaHei UI", 22))
        lines = (
            ["共同材料：某实验记录如下，请据此作答。", "现象一：两种溶液混合后出现沉淀。",
             "现象二：过滤后获得澄清滤液。", "（1）指出需要进一步核对的实验条件。",
             "（2）说明如何保留完整共同材料与小问。"]
            if role == "question" else
            ["逐条列出作答依据，保留原答案角色。", "参考内容仅用于检查页面展示。",
             "来源答案与 AI 建议不能混为一谈。"]
            if role == "answer" else
            ["先明确知识点，再用例题组织讲练。", "知识总结、图示与课堂问题互相对应。",
             "这张图只检查讲义角色与发送预览。"]
        )
        for index, line in enumerate(lines):
            painter.drawText(QRectF(48, 220 + index * 77, 954, 66), Qt.AlignmentFlag.AlignLeft, line)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert picture.save(buffer, "PNG")
        raw = bytes(buffer.data())
        page_id = f"PAGE-{number}"
        contents[page_id] = raw
        pages.append({
            "page_id": page_id, "source_name": f"演示{title}.png", "source_role": role,
            "page_number": 1, "width": 1050, "height": 740,
            "sha256": hashlib.sha256(raw).hexdigest(), "mime_type": "image/png",
        })
    policy = visual_import_request_policy("https://example.com/v1", "synthetic-model", "responses")
    plan = {
        "preview_id": "synthetic-preview", "revision": "synthetic-revision",
        "batch_id": "synthetic-batch", "model_label": "示例视觉模型（没有调用）",
        "pages": pages,
        "request_policy": policy,
        "confirmation_text": DesktopVisualEgressService._confirmation_text(
            "示例视觉模型（合成演示，无网络调用）", pages, policy
        ),
    }
    dialog = VisualImportEgressDialog(plan, lambda page_id: contents[page_id])
    dialog.resize(args.width, args.height)
    dialog.show()
    for _ in range(100):
        app.processEvents()
        if not dialog._timer.isActive():
            break
    assert dialog._ready and dialog.image_preview.has_image
    assert dialog.tabs.currentIndex() == 0
    for button in (dialog.confirm_button, dialog.cancel_button):
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    assert dialog.grab().save(str(args.output))
    print(f"Synthetic Qt capture: {args.output}")
    dialog.reject()


if __name__ == "__main__":
    main()

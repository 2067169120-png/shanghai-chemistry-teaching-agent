"""Synthetic offscreen dialog only: no personal state, originals or network."""

import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_egress_dialog import (
    PreparationEgressDialog,
)

app = create_application([])
font = install_font_fallbacks()
assets, contents = [], {}
for index in range(28):
    picture = QImage(880, 520, QImage.Format.Format_RGB32)
    picture.fill(QColor("#ffffff"))
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#173e4e"))
    painter.setFont(QFont("Microsoft YaHei UI", 22, QFont.Weight.Bold))
    painter.drawText(QRectF(36, 30, 808, 60), Qt.AlignmentFlag.AlignLeft,
                     f"课堂讲练流程 · 示例 {index + 1:02d}")
    painter.setFont(QFont("Microsoft YaHei UI", 14))
    painter.drawText(QRectF(36, 95, 808, 45), Qt.AlignmentFlag.AlignLeft,
                     "合成预览测试图，不是教材或正式课件")
    for step, label in enumerate(("导入问题", "教材知识", "例题讲练", "课堂小结")):
        x = 36 + step * 207
        painter.setPen(QPen(QColor("#a8cbd1"), 2))
        painter.setBrush(QColor("#eef7f7" if step % 2 == 0 else "#f1f5fc"))
        painter.drawRoundedRect(QRectF(x, 195, 177, 110), 12, 12)
        painter.setPen(QColor("#20586a"))
        painter.setFont(QFont("Microsoft YaHei UI", 19, QFont.Weight.Bold))
        painter.drawText(QRectF(x, 210, 177, 70), Qt.AlignmentFlag.AlignCenter, label)
        if step < 3:
            painter.drawText(QRectF(x + 177, 215, 30, 60), Qt.AlignmentFlag.AlignCenter, "→")
    painter.setFont(QFont("Microsoft YaHei UI", 16))
    painter.drawText(QRectF(36, 360, 808, 60), Qt.AlignmentFlag.AlignLeft,
                     "核对要点：图内文字清晰 · 顺序完整 · 裁剪边界无遗漏")
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert picture.save(buffer, "PNG")
    raw = bytes(buffer.data())
    digest = hashlib.sha256(raw).hexdigest()
    asset = {
        "asset_id": "IMG-" + digest, "sha256": digest,
        "caption": f"示例教学图 {index + 1:02d} · 课堂讲练流程",
        "source": "软件界面验收合成图，无教材原图或个人信息",
        "purpose": "检查发送前预览、缩略图切换和放大查看",
        "width": 880, "height": 520, "content_type": "image/png",
    }
    assets.append(asset)
    contents[asset["asset_id"]] = raw
text = (
    "接收模型：教师选择的视觉模型（此图为合成界面，不调用模型）。\n"
    "发送内容：本次备课文字，以及28张图片、图注、来源和用途。\n"
    "图片可能含全部可见内容及文件自带元数据，请确认不含未授权的个人信息。\n\n"
    + "\n".join(f"{i}. 示例教学图 {i:02d} · 课堂结构图 / 例题配图 / 实验示意图" for i in range(1, 29))
    + "\n\n本次调用可能产生费用；重试可能再次计费。\n"
    "连接测试成功不代表已验证读图质量，生成内容仍需教师核对。"
)
target = ROOT / "docs/screenshots/preparation-egress-images.png"
assert not target.exists(), "Preserve existing screenshot evidence."
dialog = PreparationEgressDialog(
    "完整两课时 · 发送前核对", text, image_count=28, image_assets=assets,
    image_loader=lambda row: contents[row["asset_id"]],
)
dialog.resize(980, 720)
dialog.show()
for _ in range(100):
    app.processEvents()
    if not dialog._timer.isActive():
        break
assert dialog.disclosure.toPlainText() == text
assert dialog.confirm_button.isEnabled()
assert dialog.image_preview.has_image
assert all(not dialog.image_list.item(i).icon().isNull() for i in range(28))
assert dialog.cancel_button.isDefault()
assert dialog.grab().save(str(target))
print(f"Synthetic offscreen capture: {target.name}; font: {font}")
dialog.close()

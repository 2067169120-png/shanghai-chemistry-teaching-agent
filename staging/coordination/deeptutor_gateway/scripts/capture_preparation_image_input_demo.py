"""Private native UI captures using synthetic pixels and pure egress disclosure.

No user application, personal settings, credentials or network are opened.
The script captures the real image widget and native confirmation message box.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def main() -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )

    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.components import CardFrame
    from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget import (
        PreparationImagesWidget,
    )

    output_root = ROOT / "runtime/deeptutor_shchem/qa_0.1.58_image_input_ui"
    output_root.mkdir(parents=True, exist_ok=True)
    serial = 1
    while (output_root / f"capture-{serial:02d}").exists():
        serial += 1
    output = output_root / f"capture-{serial:02d}"
    output.mkdir()
    app = create_application([])
    for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"):
        if QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{name}") < 0:
            raise RuntimeError("Required Chinese demo font unavailable")
    app.setFont(QFont("Microsoft YaHei", 11))

    image_bytes = {}
    assets = []
    for index, (caption, labels, color) in enumerate(
        (
            ("课堂观察示意图（合成）", ("观察图示", "比较信息", "解释现象"), "#186b7c"),
            ("教案知识结构图（合成）", ("知识要点", "例题讲解", "课堂练习"), "#426c55"),
        )
    ):
        image = QImage(820, 270, QImage.Format.Format_RGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Microsoft YaHei", 19, QFont.Weight.Bold))
        for column, label in enumerate(labels):
            rectangle = QRect(25 + column * 275, 55, 220, 100)
            painter.setPen(QPen(QColor(color), 2))
            painter.setBrush(QColor("#f0f6f4"))
            painter.drawRoundedRect(rectangle, 12, 12)
            painter.setPen(QColor("#183238"))
            painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, label)
            if column < 2:
                painter.drawText(
                    QRect(245 + column * 275, 55, 55, 100),
                    Qt.AlignmentFlag.AlignCenter,
                    "→",
                )
        painter.setFont(QFont("Microsoft YaHei", 15))
        painter.setPen(QColor("#596c72"))
        painter.drawText(
            QRect(25, 190, 770, 45),
            Qt.AlignmentFlag.AlignCenter,
            "仅为界面演示生成，不是真实教材、试题或答案",
        )
        painter.end()
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("Synthetic image encoding failed")
        data = bytes(encoded)
        digest = hashlib.sha256(data).hexdigest()
        asset = {
            "asset_id": "IMG-" + digest,
            "sha256": digest,
            "caption": caption,
            "source": "本机合成界面演示素材",
            "purpose": "展示图片选择、预览与发送确认",
            "width": 820,
            "height": 270,
            "content_type": "image/png",
        }
        image_bytes[asset["asset_id"]] = data
        assets.append(asset)

    loads = []

    def load(asset):
        loads.append(asset["sha256"])
        return image_bytes[asset["asset_id"]]

    records = []

    def capture(widget, filename, state):
        widget.show()
        for _ in range(6):
            app.processEvents()
        pixmap = widget.grab()
        path = output / filename
        if not pixmap.save(str(path)):
            raise RuntimeError("Native widget capture failed")
        records.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "pixel_width": pixmap.width(),
                "pixel_height": pixmap.height(),
                "state": state,
            }
        )

    for mode, selected, filename in (
        ("local_only", assets, "image-input-local-only.png"),
        ("vision", assets, "image-input-vision-main.png"),
        ("vision", [], "image-input-vision-zero.png"),
    ):
        shell = QWidget()
        shell.setWindowTitle("备课图片用法")
        shell.setObjectName("PreparationImageInputDemo")
        root = QVBoxLayout(shell)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        heading = QLabel("备课图片用法与发送确认")
        heading.setObjectName("PageTitle")
        root.addWidget(heading)
        subtitle = QLabel(
            "图片用于课堂排版，或在明确确认后交给 AI 读图。以下仅使用合成演示素材。"
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("MutedLabel")
        root.addWidget(subtitle)
        columns = QHBoxLayout()
        columns.setSpacing(20)
        card = CardFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 14)
        picker = PreparationImagesWidget(
            SimpleNamespace(preparation_image_bytes=load), card
        )
        picker.set_assets_strict(selected)
        picker.set_image_input_mode(mode)
        card_layout.addWidget(picker)
        card_layout.addStretch(1)
        columns.addWidget(card, 6)
        confirmation_card = CardFrame()
        confirmation_layout = QVBoxLayout(confirmation_card)
        confirmation_layout.setContentsMargins(16, 16, 16, 16)
        title = QLabel("生成前确认")
        title.setObjectName("CardTitle")
        confirmation_layout.addWidget(title)
        payload = {"image_input_mode": mode, "image_assets": selected}
        preview = DesktopWorkbenchFacade._preparation_egress_disclosure(
            payload, "示例模型服务 / 读图模型"
        )
        dialog = QMessageBox(confirmation_card)
        dialog.setWindowFlags(Qt.WindowType.Widget)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(preview["confirmation_text"] + "\n\n是否继续？")
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.button(QMessageBox.StandardButton.Yes).setText("继续生成")
        dialog.button(QMessageBox.StandardButton.No).setText("取消")
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        dialog.setMinimumWidth(0)
        dialog.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        confirmation_layout.addWidget(dialog)
        confirmation_layout.addStretch(1)
        columns.addWidget(confirmation_card, 5)
        root.addLayout(columns, 1)
        footer = QLabel("界面演示未调用模型，也没有读取个人配置或真实教学资料。")
        footer.setObjectName("MutedLabel")
        root.addWidget(footer)
        shell.resize(1260, 830)
        capture(
            shell,
            filename,
            {
                "mode": mode,
                "selected_images": len(selected),
                "sending_images": preview["image_count"],
            },
        )
        if picker.assets() != selected or picker.image_input_mode() != mode:
            raise RuntimeError("Native image selection drifted")
        if selected and not picker.preview.has_image:
            raise RuntimeError("Synthetic selected image did not load")
        shell.close()
        app.processEvents()

    history = DesktopWorkbenchFacade._preparation_egress_disclosure(
        {"image_input_mode": "vision", "image_assets": assets},
        "不调用模型",
        local_only_operation=True,
    )
    dialog = QMessageBox()
    dialog.setWindowTitle("确认本地重新导出")
    dialog.setIcon(QMessageBox.Icon.Question)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(history["confirmation_text"] + "\n\n是否继续？")
    dialog.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    dialog.button(QMessageBox.StandardButton.Yes).setText("继续本地导出")
    dialog.button(QMessageBox.StandardButton.No).setText("取消")
    dialog.setDefaultButton(QMessageBox.StandardButton.No)
    capture(
        dialog,
        "image-input-history-local.png",
        {"mode": "vision", "local_only_operation": True, "sending_images": 0},
    )
    dialog.close()
    app.processEvents()
    report = {
        "synthetic_only": True,
        "model_calls": 0,
        "network_calls": 0,
        "personal_configuration_accesses": 0,
        "original_documents_loaded": 0,
        "native_widget": "PreparationImagesWidget",
        "confirmation_widget": "QMessageBox",
        "disclosure": "DesktopWorkbenchFacade._preparation_egress_disclosure",
        "synthetic_images": 2,
        "local_preview_load_count": len(loads),
        "screenshots": records,
        "publication_selection": "Private QA only; root chooses any public README image after viewing.",
    }
    (output / "capture-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

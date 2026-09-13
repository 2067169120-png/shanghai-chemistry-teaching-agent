"""Render the actual Qt image chooser with synthetic metadata, without app state."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget import (
    PreparationImagesWidget,
)


def main():
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    output = Path("runtime/deeptutor_shchem/qa/word-led-workflow-v18-20260909-r1")
    output.mkdir(parents=True, exist_ok=True)
    widget = PreparationImagesWidget(object())
    widget.setStyleSheet(WORKBENCH_STYLE)
    widget.set_assets(
        [
            {
                "asset_id": "IMG-" + f"{n:064x}",
                "sha256": f"{n:064x}",
                "caption": f"测试图片 {n + 1}：教材原句与知识示意图",
                "source": "合成界面测试，不是实际教材图",
                "purpose": "检查12张列表、换行和滚动",
                "width": 1600,
                "height": 900,
                "content_type": "image/png",
            }
            for n in range(12)
        ]
    )
    for width in (420, 900):
        widget.resize(width, 440)
        widget.show()
        app.processEvents()
        widget.asset_list.scrollToTop()
        app.processEvents()
        assert widget.grab().save(str(output / f"images-{width}-top.png"))
        widget.asset_list.setCurrentRow(11)
        widget.asset_list.scrollToBottom()
        app.processEvents()
        assert widget.grab().save(str(output / f"images-{width}-bottom.png"))
        assert not widget.add_button.isEnabled()
        assert widget.asset_list.horizontalScrollBar().maximum() == 0
    widget.close()
    print(
        "12 images; add disabled at capacity; last image reachable; no horizontal scroll"
    )


if __name__ == "__main__":
    main()

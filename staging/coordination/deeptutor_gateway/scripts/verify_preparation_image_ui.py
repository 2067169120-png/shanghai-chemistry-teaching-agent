"""Render an isolated, offscreen PreparationPage image-assets UI snapshot.

This is a visual smoke check only.  It uses a fixture facade and metadata for
the textbook's figure 2.14; it does not read the real app state, credentials,
or image bytes, and it does not assess the teaching material itself.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QScrollArea

from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
    DesktopTaskBridge,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


class _ScreenshotFacade:
    """Minimum reader-shaped facade; no real state or provider is accessed."""

    def __init__(self, isolated_root: Path) -> None:
        self.isolated_root = isolated_root


def _textbook_figure_214_asset() -> dict[str, object]:
    return {
        "asset_id": "IMG-" + "2" * 64,
        "sha256": "2" * 64,
        "caption": "教材图 2.14 氯化钠电离过程示意图",
        "source": "沪科技化学必修第一册，第 57 页（PDF 第 62 页）",
        "purpose": "解释氯化钠溶于水或熔融后形成自由移动离子",
        "width": 1600,
        "height": 900,
        "content_type": "image/png",
    }


def main() -> int:
    output_root = WORKSPACE / "runtime" / "deeptutor_shchem" / "qa" / "preparation-images-20260909"
    output_root.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_root / "preparation-page-images.png"
    report_path = output_root / "screenshot-report.json"

    with tempfile.TemporaryDirectory(prefix="shchem-preparation-image-ui-") as isolated:
        app = QApplication.instance() or QApplication([])
        font_family = install_font_fallbacks()
        facade = _ScreenshotFacade(Path(isolated))
        bridge = DesktopTaskBridge()
        page = PreparationPage(facade, bridge)
        page._availability_timer.stop()
        page.topic.setText("电解质与电离方程式")
        page.audience.setText("高二复习课")
        page.route.setCurrentText("复习")
        page.objective.setPlainText("解释电离与强弱电解质，并能规范书写电离方程式")
        page.materials.setPlainText(
            "复习讲义：第 04 讲·考点一；教材：沪科技化学必修第一册，第 56—58 页"
        )
        page.image_assets_widget.set_assets([_textbook_figure_214_asset()])
        page.resize(1180, 880)
        page.show()
        app.processEvents()

        scroll = page.findChild(QScrollArea, "PageScroll")
        if scroll is None:
            raise RuntimeError("PreparationPage scroll surface was not found")
        content = scroll.widget()
        if content is None:
            raise RuntimeError("PreparationPage scroll content was not found")

        target = page.image_assets_widget.mapTo(content, QPoint(0, 0))
        bar = scroll.verticalScrollBar()
        desired = max(0, target.y() - 28)
        bar.setValue(min(desired, bar.maximum()))
        app.processEvents()

        visible_rect = page.image_assets_widget.rect().translated(
            page.image_assets_widget.mapTo(scroll.viewport(), QPoint(0, 0))
        )
        viewport_rect = scroll.viewport().rect()
        intersection = visible_rect.intersected(viewport_rect)
        if intersection.height() < 120:
            raise RuntimeError(
                "image asset region is not sufficiently visible after scrolling"
            )
        if not scroll.viewport().grab().save(str(screenshot_path), "PNG"):
            raise RuntimeError(f"failed to save screenshot: {screenshot_path}")

        item = page.image_assets_widget.asset_list.item(0)
        status_text = page.image_assets_widget.status.text()
        issues: list[str] = []
        if page.image_assets_widget.asset_list.count() and "尚未添加" in status_text:
            issues.append(
                "载入图片后列表下方状态仍显示“尚未添加教学图片”，与列表内容不一致"
            )
        report = {
            "screenshot": str(screenshot_path),
            "window_size": [page.width(), page.height()],
            "font_family": font_family,
            "viewport_size": [scroll.viewport().width(), scroll.viewport().height()],
            "scroll_range": [bar.minimum(), bar.maximum()],
            "scroll_value": bar.value(),
            "image_region_visible_height": intersection.height(),
            "asset_count": page.image_assets_widget.asset_list.count(),
            "asset_row_text": item.text() if item is not None else "",
            "asset_status_text": status_text,
            "privacy_notice": page.image_assets_widget.privacy_label.text(),
            "payload_image_assets": page._payload().get("image_assets", []),
            "uses_fixture_facade": True,
            "reads_real_state_or_credentials": False,
            "visual_classroom_review": False,
            "issues": issues,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        page.close()
        bridge.shutdown(1000)
        app.processEvents()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Real-source, read-only visual acceptance for the preparation-source dialog.

The facade below wraps only ``PreparationSourcesService``.  It reads the
checked-in PKG-032 DOCX and textbook concept catalog, but it does not create a
desktop state, access credentials, call a provider, or modify/convert the
source document.  The screenshots are UI evidence, not evidence that the
Word extraction is a complete original or that the chemistry has been
reviewed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    PreparationSourcesService,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import (
    PreparationSourcesDialog,
)

SOURCE = (
    WORKSPACE / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-032/"
    "第04讲 离子反应和离子方程式（复习讲义）（上海专用）（解析版）.docx"
)
CONCEPT_IDS = (
    "TB-M1-C2-S22-C05",
    "TB-M1-C2-S22-C06",
    "TB-M1-C2-S22-C07",
)
EXPECTED_SOURCE_SHA256 = (
    "d60317b8e533b957943e98b481305b85557d030d3056bf2eb0e9273f2811162d"
)


class _ReadOnlySourceFacade:
    """Fixture facade whose only backing service is the local read-only reader."""

    def __init__(self, workspace: Path) -> None:
        self.service = PreparationSourcesService(workspace)

    def preparation_word_preview(self, path: str) -> dict[str, object]:
        return self.service.word_preview(path)

    def preparation_concept_options(self, query: str = "") -> list[dict[str, object]]:
        return self.service.concept_options(query)

    def preparation_source_reference(
        self,
        word_path: str | None,
        word_sha256: str | None,
        block_start: int,
        block_end: int,
        concepts: list[dict[str, str]],
    ) -> dict[str, object]:
        return self.service.reference(
            word_path,
            word_sha256,
            block_start,
            block_end,
            concepts,
        )


def _select_concepts(dialog: PreparationSourcesDialog) -> None:
    dialog._load_concepts("TB-M1-C2-S22-C0")
    seen: set[str] = set()
    first_selected = None
    for row in range(dialog.concept_list.count()):
        item = dialog.concept_list.item(row)
        concept_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if concept_id in CONCEPT_IDS:
            item.setCheckState(Qt.CheckState.Checked)
            seen.add(concept_id)
            first_selected = first_selected or item
    if seen != set(CONCEPT_IDS):
        raise RuntimeError(f"教材知识点选项缺失：{sorted(set(CONCEPT_IDS) - seen)}")
    if first_selected is not None:
        dialog.concept_list.scrollToItem(first_selected)


def _capture(
    dialog: PreparationSourcesDialog,
    target: Path,
    width: int,
    height: int = 420,
    *,
    scroll_to_end: bool = False,
    scroll_value: int | None = None,
) -> dict[str, object]:
    dialog.resize(width, height)
    dialog.show()
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication was not initialized")
    for _ in range(5):
        app.processEvents()
    if dialog.width() != width or dialog.height() != height:
        raise RuntimeError(
            f"dialog size was constrained: requested={width}x{height} actual={dialog.width()}x{dialog.height()}"
        )
    bar = dialog.body_scroll.verticalScrollBar()
    if scroll_value is not None:
        bar.setValue(max(bar.minimum(), min(scroll_value, bar.maximum())))
    else:
        bar.setValue(bar.maximum() if scroll_to_end else bar.minimum())
    app.processEvents()
    horizontal_ranges = {
        "body": dialog.body_scroll.horizontalScrollBar().maximum(),
        "word_list": dialog.block_list.horizontalScrollBar().maximum(),
        "concept_list": dialog.concept_list.horizontalScrollBar().maximum(),
        "preview": dialog.preview.horizontalScrollBar().maximum(),
    }
    if any(value != 0 for value in horizontal_ranges.values()):
        raise RuntimeError(f"horizontal overflow detected: {horizontal_ranges}")
    if width >= 600:
        if dialog._splitter.orientation() != Qt.Orientation.Horizontal:
            raise RuntimeError("wide dialog did not use horizontal source columns")
        panel_widths = [dialog._splitter.widget(i).width() for i in range(2)]
        if min(panel_widths) < 300:
            raise RuntimeError(f"wide source column too narrow: {panel_widths}")
    else:
        if dialog._splitter.orientation() != Qt.Orientation.Vertical:
            raise RuntimeError("narrow dialog did not switch to vertical source panels")
    if not dialog.grab().save(str(target), "PNG"):
        raise RuntimeError(f"failed to save screenshot: {target}")
    selected_block_label = ""
    for row in range(dialog.block_list.count()):
        item = dialog.block_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == dialog.block_start.value():
            selected_block_label = item.text()
            break
    return {
        "requested_size": [width, height],
        "actual_size": [dialog.width(), dialog.height()],
        "screenshot": str(target),
        "body_scroll_range": [bar.minimum(), bar.maximum()],
        "body_scroll_value": bar.value(),
        "body_viewport_height": dialog.body_scroll.viewport().height(),
        "splitter_orientation": (
            "vertical"
            if dialog._splitter.orientation() == Qt.Orientation.Vertical
            else "horizontal"
        ),
        "splitter_panel_widths": [dialog._splitter.widget(i).width() for i in range(2)],
        "horizontal_ranges": horizontal_ranges,
        "word_list_rows": dialog.block_list.count(),
        "selected_block_label": selected_block_label,
        "word_list_first_tooltip_chars": (
            len(dialog.block_list.item(0).toolTip()) if dialog.block_list.count() else 0
        ),
        "block_range": [dialog.block_start.value(), dialog.block_end.value()],
        "concept_list_rows": dialog.concept_list.count(),
        "selected_concepts": dialog.selected_concepts,
        "preview_chars": len(dialog.preview.toPlainText()),
        "preview_visible": not dialog.preview.isHidden(),
        "preview_button_enabled": dialog.preview_button.isEnabled(),
        "confirm_button_enabled": dialog.import_button.isEnabled(),
        "preview_horizontal_scroll_max": dialog.preview.horizontalScrollBar().maximum(),
        "word_path_display": dialog.word_path.text(),
    }


def main() -> int:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    output = WORKSPACE / "runtime/deeptutor_shchem/qa/preparation-sources-20260909"
    output.mkdir(parents=True, exist_ok=True)

    service = PreparationSourcesService(WORKSPACE)
    facade = _ReadOnlySourceFacade(WORKSPACE)
    word = facade.preparation_word_preview(str(SOURCE))
    if word["source_sha256"] != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("PKG-032解析版DOCX SHA-256与验收基线不一致")
    if len(word["blocks"]) < 63:
        raise RuntimeError(f"Word区块数不足：{len(word['blocks'])}")
    options = {item["concept_id"]: item for item in service.concept_options()}
    missing = [concept_id for concept_id in CONCEPT_IDS if concept_id not in options]
    if missing:
        raise RuntimeError(f"教材概念缺失：{missing}")

    app = QApplication.instance() or QApplication([])
    font_family = install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    dialog = PreparationSourcesDialog(facade)
    dialog._load_word(str(SOURCE))
    dialog.block_start.setValue(42)
    dialog.block_end.setValue(63)
    _select_concepts(dialog)
    dialog._compile_preview()
    app.processEvents()
    if dialog.reference is None:
        raise RuntimeError("真实资料预览未生成")
    reference = dialog.reference
    if len(reference["materials"]) <= 0:
        raise RuntimeError("真实资料预览为空")

    captures = [
        _capture(dialog, output / "dialog-default-900x650.png", 900, 650),
        _capture(dialog, output / "dialog-900x420.png", 900),
        _capture(
            dialog,
            output / "dialog-900x420-bottom.png",
            900,
            scroll_to_end=True,
        ),
        _capture(dialog, output / "dialog-420x420.png", 420),
        _capture(
            dialog,
            output / "dialog-420x420-middle.png",
            420,
            scroll_value=dialog.body_scroll.verticalScrollBar().maximum() // 2,
        ),
        _capture(
            dialog,
            output / "dialog-420x420-bottom.png",
            420,
            scroll_to_end=True,
        ),
    ]
    report = {
        "machine_checks_passed": True,
        "visual_review": {
            "status": "pending_manual_inspection",
            "note": "截图已生成；结构检查与人工可读性判断分开记录。",
        },
        "font_family": font_family,
        "source_path": str(SOURCE),
        "source_name": word["source_name"],
        "source_sha256": word["source_sha256"],
        "word_block_count": len(word["blocks"]),
        "selected_block_range": [42, 63],
        "selected_concept_ids": list(CONCEPT_IDS),
        "reference_material_chars": len(reference["materials"]),
        "unextracted_object_warning_count": len(reference["warnings"]),
        "unextracted_object_warnings": reference["warnings"],
        "captures": captures,
        "uses_read_only_service_facade": True,
        "builds_real_desktop_state": False,
        "reads_credentials": False,
        "calls_model": False,
        "modifies_or_converts_source_docx": False,
        "claims_complete_original_text": False,
        "claims_chemistry_review": False,
    }
    report_path = output / "verification.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    dialog.close()
    app.processEvents()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

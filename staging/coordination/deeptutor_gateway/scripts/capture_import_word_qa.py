"""Capture real imported Word widgets from an isolated state, without a model.

Only QWidget.grab() is used. The existing saved-source archive and personal
settings are not modified. Screenshots require separate visual inspection;
these checks do not constitute chemistry review or teaching approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QDialog, QScrollArea

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_visual_import_v2 import (
    DesktopImportBridgeError,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import (
    ImportWordDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


class NoModel:
    invocation_attempts = 0

    def list_metadata(self):
        return []

    def borrow_invocation_context(self, *_args, **_kwargs):
        self.invocation_attempts += 1
        raise AssertionError("This UI capture must not invoke a model")


class NoPageRendering:
    calls = 0

    def render(self, *_args, **_kwargs):
        self.calls += 1
        raise DesktopImportBridgeError(
            "page_preview_not_requested", "本次原生界面核验不渲染页面。"
        )


def _state_files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _settle() -> None:
    for _ in range(5):
        QApplication.instance().processEvents()


def _capture(
    dialog: ImportWordDialog,
    output: Path,
    width: int,
    height: int,
    position: str,
) -> dict:
    dialog.resize(width, height)
    dialog.show()
    _settle()
    scroll = dialog.findChild(QScrollArea, "PageScroll")
    if scroll is None:
        raise AssertionError("Word dialog scroll area is missing")
    bar = scroll.verticalScrollBar()
    if position == "top":
        bar.setValue(bar.minimum())
    elif position in {"reference-start", "reference-end", "reference-body"}:
        bar.setValue(bar.maximum())
        if position == "reference-body":
            if not dialog.preview_body_button.isEnabled():
                raise AssertionError("The Word body navigation button is unavailable")
            dialog.preview_body_button.click()
        else:
            reference_bar = dialog.preview.verticalScrollBar()
            reference_bar.setValue(
                reference_bar.maximum()
                if position == "reference-end"
                else reference_bar.minimum()
            )
    elif position == "image":
        scroll.ensureWidgetVisible(dialog.image_label, 0, 12)
    else:
        raise ValueError(position)
    _settle()
    if [dialog.width(), dialog.height()] != [width, height]:
        raise AssertionError("The dialog expanded beyond its requested size")
    horizontal = {
        "body": scroll.horizontalScrollBar().maximum(),
        "blocks": dialog.block_list.horizontalScrollBar().maximum(),
        "source_preview": dialog.source_preview.horizontalScrollBar().maximum(),
        "reference_preview": dialog.preview.horizontalScrollBar().maximum(),
    }
    if any(horizontal.values()):
        raise AssertionError(f"Horizontal overflow: {horizontal}")
    if not dialog.grab().save(str(output), "PNG"):
        raise AssertionError("Window image could not be saved")
    return {
        "path": str(output.resolve()),
        "size": [width, height],
        "position": position,
        "body_scroll_value": bar.value(),
        "body_scroll_maximum": bar.maximum(),
        "horizontal_ranges": horizontal,
        "reference_scroll_value": dialog.preview.verticalScrollBar().value(),
        "reference_scroll_maximum": dialog.preview.verticalScrollBar().maximum(),
        "confirm_enabled": dialog.import_button.isEnabled(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state-root",
        type=Path,
        default=ROOT
        / "runtime/deeptutor_shchem/qa/native-word-20260909/real-r1/isolated-state",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--narrow-source",
        type=int,
        help="Capture only this one-based source at 420x780, including body navigation",
    )
    args = parser.parse_args()
    if args.narrow_source is not None and args.narrow_source < 1:
        parser.error("--narrow-source must be a positive source index")
    state_root = args.state_root.resolve()
    output = args.output.resolve()
    if not state_root.is_dir():
        raise FileNotFoundError("The existing isolated import state is required")
    if output.exists():
        raise RuntimeError("Use a new screenshot output directory")
    if output == state_root or output.is_relative_to(state_root):
        raise RuntimeError("Screenshots must be outside the imported source state")
    before = _state_files(state_root)
    paths = DesktopPaths.from_workspace(ROOT, state_root=state_root)
    provider, renderer, unused = NoModel(), NoPageRendering(), object()
    facade = DesktopWorkbenchFacade(
        paths,
        theme_reader=unused,
        supplemental_reader=unused,
        curriculum_reader=unused,
        search_reader=unused,
        provider_store=provider,
        state_store=DesktopStateStore(state_root),
        paper_export_jobs=unused,
        visual_import_renderer=renderer,
    )
    app = create_application([])
    font = install_font_fallbacks()
    tasks = DesktopTaskBridge()
    output.mkdir(parents=True)
    page = PreparationPage(facade, tasks)
    # A real preparation page is used, but its optional settings/history timer
    # is stopped before processing events. No configuration reader is needed.
    page._availability_timer.stop()
    page.topic.setText("原有课题：电解质与有机结构复习")
    page.audience.setText("高二化学")
    page.lesson_count.setValue(2)
    page.lesson_minutes.setValue(45)
    page.objective.setPlainText("保留原教学目标：依据证据判断结构与性质。")
    page.materials.setPlainText("老师原有备课资料  \n请保留原来的空白和段落。")
    baseline = deepcopy(page._payload())
    rows = []
    history = ImportDialog(facade, tasks)
    batches = tuple(facade.list_imported_word_batches())
    if not batches or history.word_batch_combo.count() != len(batches):
        raise AssertionError("Imported Word history did not reopen")
    history.close()
    history.deleteLater()
    try:
        source_number = 0
        for batch in batches:
            sources = facade.imported_word_sources(batch.batch_id)
            for source in sources:
                source_number += 1
                number = source_number
                if args.narrow_source and number != args.narrow_source:
                    continue
                dialog = ImportWordDialog(facade, batch.batch_id)
                selected = dialog.source_combo.findData(source["source_id"])
                if selected < 0:
                    raise AssertionError("A saved Word source is not selectable")
                dialog.source_combo.setCurrentIndex(selected)
                if dialog.block_list.count() < 35:
                    raise AssertionError("The QA source has fewer than 35 blocks")
                dialog.block_start.setValue(1)
                dialog.block_end.setValue(35)
                dialog._compile_preview()
                if not dialog.import_button.isEnabled():
                    raise AssertionError(dialog.status.text())
                captures = []
                sizes = (
                    ((420, 780),) if args.narrow_source else ((760, 800), (420, 780))
                )
                for width, height in sizes:
                    dialog.asset_combo.setCurrentIndex(0)
                    positions = (
                        ("top", "reference-body")
                        if args.narrow_source
                        else ("top", "reference-start")
                    )
                    for position in positions:
                        captures.append(
                            _capture(
                                dialog,
                                output
                                / f"source-{number}-{width}x{height}-{position}.png",
                                width,
                                height,
                                position,
                            )
                        )
                    supported = [
                        index
                        for index in range(1, dialog.asset_combo.count())
                        if dialog.asset_combo.itemData(index).get("preview_supported")
                    ]
                    if supported and width == 760:
                        # Prefer an image tied to the selected source range.
                        selected_assets = [
                            index
                            for index in supported
                            if 1
                            <= dialog.asset_combo.itemData(index).get("block_index", 0)
                            <= 35
                        ]
                        dialog.asset_combo.setCurrentIndex(
                            (selected_assets or supported)[-1]
                        )
                        if dialog.image_label.isHidden():
                            raise AssertionError(dialog.status.text())
                        captures.append(
                            _capture(
                                dialog,
                                output / f"source-{number}-{width}x{height}-image.png",
                                width,
                                height,
                                "image",
                            )
                        )
                displayed_materials = dialog.preview.toPlainText()
                dialog._confirm()
                if (
                    dialog.result() != QDialog.DialogCode.Accepted
                    or dialog.reference is None
                ):
                    raise AssertionError("Real source confirmation failed")
                previous_payload = deepcopy(page._payload())
                if not page.import_word_reference(dialog.reference):
                    raise AssertionError("Real Word reference was not appended")
                current_payload = deepcopy(page._payload())
                current_materials = current_payload.pop("materials")
                original_materials = previous_payload.pop("materials")
                if current_payload != previous_payload:
                    raise AssertionError(
                        "Word append changed another preparation field"
                    )
                if (
                    current_materials
                    != original_materials + "\n\n" + displayed_materials
                ):
                    raise AssertionError(
                        "The appended content differs from the preview"
                    )
                (output / f"source-{number}-reference.txt").write_text(
                    displayed_materials, encoding="utf-8"
                )
                rows.append(
                    {
                        "source_name": source["source_name"],
                        "source_sha256": dialog._source["source_sha256"],
                        "preview_revision": dialog._source["revision"],
                        "blocks": dialog.block_list.count(),
                        "selected_blocks": [1, 35],
                        "source_preview_characters": len(
                            dialog.source_preview.toPlainText()
                        ),
                        "reference_characters": len(displayed_materials),
                        "warnings": dialog.reference.get("warnings", []),
                        "embedded_assets": dialog.asset_combo.count() - 1,
                        "confirmed": True,
                        "append_preserved_other_fields": True,
                        "screenshots": captures,
                    }
                )
                dialog.close()
                dialog.deleteLater()
        if not rows:
            raise AssertionError("No source matched the requested capture selection")
        after = _state_files(state_root)
        unchanged = before == after
        if not unchanged:
            raise AssertionError("The isolated saved-source state changed")
        if renderer.calls or provider.invocation_attempts:
            raise AssertionError("Unexpected renderer or model invocation")
        report = {
            "machine_checks_passed": True,
            "font_family": font,
            "state_root": str(state_root),
            "source_state_unchanged": unchanged,
            "personal_configuration_read": False,
            "external_model_invocations": provider.invocation_attempts,
            "page_renderer_calls": renderer.calls,
            "history_batches": len(batches),
            "original_materials_preserved": page.materials.toPlainText().startswith(
                baseline["materials"]
            ),
            "preserved_topic": page.topic.text(),
            "preserved_timing": [
                page.lesson_count.value(),
                page.lesson_minutes.value(),
            ],
            "sources": rows,
            "visual_review_status": "pending_agent_image_inspection",
            "chemistry_review_complete": False,
            "teaching_approval": False,
        }
        (output / "ui-verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        page.close()
        page.deleteLater()
        tasks.shutdown(1000)
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

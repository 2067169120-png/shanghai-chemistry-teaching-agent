"""Read-only real-textbook QA; no provider, OCR, source or user-state writes."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

WORKSPACE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(WORKSPACE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import CONCEPTS
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import (
    PreparationSourcesDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import (
    TextbookSourceDialog,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ForbiddenProvider:
    def __getattr__(self, name):
        raise AssertionError("Provider access forbidden during local PDF QA")


def main():
    output = (
        WORKSPACE / "runtime/deeptutor_shchem/qa/textbook-original-view-20260909-r2"
    )
    output.mkdir(parents=True, exist_ok=False)
    book = WORKSPACE / "课本/沪科技化学必修第一册【高清教材】.pdf"
    catalog = WORKSPACE / CONCEPTS
    before = {"book": digest(book), "concept_catalog": digest(catalog)}
    # Real local facade methods, without constructing/loading personal state.
    facade = object.__new__(DesktopWorkbenchFacade)
    facade.paths = SimpleNamespace(workspace_root=WORKSPACE)
    facade._providers = ForbiddenProvider()
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    selector = PreparationSourcesDialog(facade)
    selector.concept_search.setText("TB-M1-C2-S22-C06")
    assert selector.concept_list.count() == 1
    selector.concept_list.setCurrentRow(0)
    selector.concept_list.item(0).setCheckState(Qt.CheckState.Checked)
    selector.preview_button.click()
    selected_before = deepcopy(selector.selected_concepts)
    reference_before = deepcopy(selector.reference)
    assert reference_before is not None
    screenshots = []
    for width in (900, 420):
        selector.resize(width, 600)
        selector.show()
        QTest.qWait(100)
        selector.body_scroll.ensureWidgetVisible(selector.original_button)
        QTest.qWait(100)
        assert selector.width() == width
        assert selector.body_scroll.horizontalScrollBar().maximum() == 0
        name = f"source-selector-{width}.png"
        assert selector.grab().save(str(output / name))
        screenshots.append(name)

    result = {}
    errors = []

    def inspect_modal():
        viewer = None
        try:
            viewer = next(
                x for x in app.topLevelWidgets() if isinstance(x, TextbookSourceDialog)
            )
            deadline = time.monotonic() + 20
            while not viewer._loaded_pages and time.monotonic() < deadline:
                QTest.qWait(50)
            assert viewer._loaded_pages, viewer.status.text()
            assert viewer.pages.count() == 2
            assert viewer.pages.currentData() == 61
            assert viewer.view.pageNavigator().currentPage() == 60
            assert (
                hashlib.sha256(viewer._source["pdf_bytes"]).hexdigest()
                == before["book"]
            )
            result["page_count"] = viewer.document.pageCount()
            result["associated_pdf_pages"] = [61, 62]
            for width, page_index in ((900, 0), (900, 1), (420, 1)):
                viewer.resize(width, 850)
                viewer.pages.setCurrentIndex(page_index)
                viewer.zoom.setCurrentIndex(1 if width == 900 else 0)
                QTest.qWait(600)
                assert viewer.width() == width
                assert viewer.view.pageNavigator().currentPage() == 60 + page_index
                name = f"textbook-{width}-pdf-{61 + page_index}.png"
                assert viewer.grab().save(str(output / name))
                screenshots.append(name)
            viewer.zoom.setCurrentIndex(3)
            assert viewer.view.zoomFactor() == 1.5
            viewer.previous.click()
            assert viewer.view.pageNavigator().currentPage() == 60
            viewer.next.click()
            assert viewer.view.pageNavigator().currentPage() == 61
            result["navigation_and_zoom_checked"] = True
        except Exception as exc:  # noqa: BLE001 - close modal and report QA failure
            errors.append(str(exc))
        finally:
            if viewer is not None:
                viewer.reject()

    QTimer.singleShot(100, inspect_modal)
    selector.original_button.click()
    assert not errors, errors
    assert selector.selected_concepts == selected_before
    assert selector.reference == reference_before
    selector.reject()
    after = {"book": digest(book), "concept_catalog": digest(catalog)}
    assert before == after
    result.update(
        {
            "source_hashes_before": before,
            "source_hashes_after": after,
            "selection_and_preview_unchanged": True,
            "real_facade_and_modal_button_path": True,
            "screenshots": screenshots,
            "model_calls": 0,
            "human_reviewed": False,
            "teaching_use_approved": False,
            "visual_inspection": "pending_separate_image_review",
        }
    )
    (output / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

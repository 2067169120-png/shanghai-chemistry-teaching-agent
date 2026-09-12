"""UI contracts for explicit, disjoint Word lesson selections."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QDialog, QScrollArea
from test_import_word_dialog import _Facade

from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import (
    ImportWordDialog,
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


class _MultiRangeFacade(_Facade):
    """Small UI substitute; it never reads the real desktop state or calls an API."""

    def __init__(self):
        super().__init__()
        self.multirange_calls = []

    def imported_word_preview(self, batch_id, source_id):
        value = super().imported_word_preview(batch_id, source_id)
        value["blocks"].extend(
            [
                {
                    "index": 3,
                    "label": "区块 3 · 间隙",
                    "text": "间隙区块：不应因浏览游标变化而自动加入。",
                    "warnings": [],
                },
                {
                    "index": 4,
                    "label": "区块 4 · 练习",
                    "text": "练习区块：明确点击加入当前段后才带入。",
                    "warnings": [],
                },
            ]
        )
        return value

    def imported_word_multirange_reference(
        self,
        batch_id,
        source_id,
        source_sha256,
        block_ranges,
        *,
        expected_revision,
        include_images=True,
        include_guidance=True,
    ):
        self.multirange_calls.append(
            {
                "batch_id": batch_id,
                "source_id": source_id,
                "source_sha256": source_sha256,
                "block_ranges": [dict(row) for row in block_ranges],
                "expected_revision": expected_revision,
                "include_images": include_images,
                "include_guidance": include_guidance,
            }
        )
        indices = [
            index
            for bounds in block_ranges
            for index in range(bounds["start"], bounds["end"] + 1)
        ]
        return {
            "materials": "\n".join(
                f"[Word区块{index}] 内容{index}" for index in indices
            ),
            "warnings": [],
            "source_sha256": source_sha256,
            "revision": expected_revision,
            "reference_sha256": "b" * 64,
        }


def _dialog():
    facade = _MultiRangeFacade()
    return facade, ImportWordDialog(facade, "internal-batch")


def _add(dialog, start, end):
    dialog._set_range(start, end)
    dialog.range_selection.add_button.click()


def test_disjoint_ranges_require_explicit_add_and_cursor_browsing_keeps_preview(
    qt_app,
):
    facade, dialog = _dialog()
    dialog.range_selection.enabled.setChecked(True)
    assert not dialog.preview_button.isEnabled()

    _add(dialog, 1, 1)
    _add(dialog, 3, 3)
    assert dialog.range_selection.ranges() == [
        {"start": 1, "end": 1},
        {"start": 3, "end": 3},
    ]
    assert dialog.range_selection.ranges_list.count() == 2
    dialog.preview_button.click()
    assert facade.multirange_calls[-1]["block_ranges"] == [
        {"start": 1, "end": 1},
        {"start": 3, "end": 3},
    ]
    before = dialog.preview.toPlainText()
    dialog._set_range(2, 2)  # 浏览清单外的区块，不是加入动作。
    assert dialog.range_selection.ranges() == [
        {"start": 1, "end": 1},
        {"start": 3, "end": 3},
    ]
    assert dialog.preview.toPlainText() == before
    assert dialog.import_button.isEnabled()
    dialog.close()


def test_remove_clear_and_source_switch_invalidate_disjoint_preview(qt_app):
    facade, dialog = _dialog()
    dialog.range_selection.enabled.setChecked(True)
    _add(dialog, 1, 1)
    _add(dialog, 3, 3)
    dialog.preview_button.click()
    assert dialog.import_button.isEnabled()

    dialog.range_selection.ranges_list.setCurrentRow(0)
    dialog.range_selection.remove_button.click()
    assert dialog.range_selection.ranges() == [{"start": 3, "end": 3}]
    assert not dialog.preview.toPlainText()
    assert not dialog.import_button.isEnabled()

    _add(dialog, 1, 1)
    dialog.preview_button.click()
    assert dialog.import_button.isEnabled()
    dialog.range_selection.clear_button.click()
    assert dialog.range_selection.ranges() == []
    assert not dialog.preview.toPlainText()
    assert not dialog.import_button.isEnabled()

    _add(dialog, 1, 1)
    dialog.preview_button.click()
    assert dialog.import_button.isEnabled()
    dialog.source_combo.setCurrentIndex(1)
    assert dialog.range_selection.ranges() == []
    assert not dialog.range_selection.enabled.isChecked()
    assert not dialog.preview.toPlainText()
    assert not dialog.import_button.isEnabled()
    assert facade.multirange_calls
    dialog.close()


def test_confirm_uses_multirange_facade_entry_and_recompiles_same_selection(qt_app):
    facade, dialog = _dialog()
    dialog.range_selection.enabled.setChecked(True)
    _add(dialog, 1, 1)
    _add(dialog, 3, 4)
    dialog.preview_button.click()
    dialog.import_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert len(facade.multirange_calls) == 2
    assert facade.multirange_calls[-1]["block_ranges"] == [
        {"start": 1, "end": 1},
        {"start": 3, "end": 4},
    ]
    assert dialog.reference["materials"] == (
        "[Word区块1] 内容1\n[Word区块3] 内容3\n[Word区块4] 内容4"
    )
    dialog.close()


def test_preview_scrolls_to_visible_content_after_generation(qt_app):
    _facade, dialog = _dialog()
    dialog.resize(420, 560)
    dialog.show()
    qt_app.processEvents()
    dialog.range_selection.enabled.setChecked(True)
    _add(dialog, 1, 1)
    _add(dialog, 3, 4)
    dialog.preview_button.click()
    # The production dialog may schedule the final scroll on the next event;
    # process a couple of turns without sleeping or using a native desktop.
    qt_app.processEvents()
    qt_app.processEvents()

    scroll = dialog.preview
    while scroll.parentWidget() is not None and not isinstance(scroll, QScrollArea):
        scroll = scroll.parentWidget()
    assert isinstance(scroll, QScrollArea)
    top_left = dialog.preview.mapTo(scroll.viewport(), QPoint(0, 0))
    visible = (
        scroll.viewport().rect().intersected(QRect(top_left, dialog.preview.size()))
    )
    assert visible.height() >= min(40, dialog.preview.height())
    assert visible.width() > 0
    dialog.close()


def test_narrow_range_panel_has_no_horizontal_overflow(qt_app):
    _facade, dialog = _dialog()
    dialog.resize(400, 540)
    dialog.reader_tabs.setCurrentIndex(1)
    dialog.show()
    qt_app.processEvents()
    dialog.range_selection.enabled.setChecked(True)
    qt_app.processEvents()
    assert dialog.width() == 400
    page = dialog.reader_tabs.currentWidget()
    assert isinstance(page, QScrollArea)
    assert page.width() <= dialog.width()
    assert page.viewport().width() > 0
    assert page.horizontalScrollBar().maximum() == 0
    assert page.widget().width() <= page.viewport().width()
    assert dialog.range_selection.width() <= page.viewport().width()
    assert (
        dialog.range_selection.ranges_list.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert dialog.range_selection.ranges_list.horizontalScrollBar().maximum() == 0
    assert dialog.reader_tabs.width() <= dialog.width()
    for button in (
        dialog.range_selection.add_button,
        dialog.range_selection.remove_button,
        dialog.range_selection.clear_button,
    ):
        assert button.width() <= dialog.width()
    dialog.close()

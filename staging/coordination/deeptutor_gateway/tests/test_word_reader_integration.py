"""Offscreen native Word reader wiring with synthetic, local-only facade data."""

from __future__ import annotations

import hashlib
import os
from copy import deepcopy

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QTextBrowser

from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import (
    ImportWordDialog,
    _WordImageDialog,
)

LONG_BODY = "完整原教案知识点正文" * 3000
IMAGE_BLOCK = "第二段原文：请核对装置图与相应实验条件。"
LAST_BLOCK = "整份教案最后一段：课堂练习答案与总结均已保留。"
UNTRUSTED_HTML = (
    '<a href="https://example.invalid/outside">这仍是Word原文字面内容</a>'
    '<img src="https://example.invalid/picture.png">'
    '<img src="file:///private/not-a-source.png">'
    '<script>window.location="https://example.invalid/script"</script>'
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


class _ReaderFacade:
    """No file, personal settings, credentials, network, or model implementation."""

    def __init__(self):
        self.preview_calls = []
        self.asset_calls = []
        self.reference_calls = []
        self.source_failure = False
        self.asset_failure = None
        self.asset_metadata = {}
        self.asset_result = None

    def imported_word_sources(self, batch_id):
        assert batch_id == "synthetic-batch"
        return [
            {"source_id": "lesson-a", "source_name": "教案甲.docx", "role": "handout"},
            {"source_id": "lesson-b", "source_name": "教案乙.docx", "role": "handout"},
        ]

    def imported_word_preview(self, batch_id, source_id):
        self.preview_calls.append((batch_id, source_id))
        if self.source_failure:
            raise OSError("private/source-file.docx")
        if source_id == "lesson-b":
            return {
                "source_name": "教案乙.docx",
                "source_sha256": "c" * 64,
                "revision": "source-b-v1",
                "blocks": [{"index": 10, "label": "新教案正文", "text": "第二份教案独有内容", "warnings": []}],
                "sections": [],
                "assets": [],
                "warnings": [],
            }
        return {
            "source_name": "教案甲.docx",
            "source_sha256": "a" * 64,
            "revision": "source-a-v1",
            "blocks": [
                {"index": 1, "label": "原教案正文", "text": LONG_BODY, "warnings": []},
                {"index": 4, "label": "实验图示", "text": IMAGE_BLOCK + "\n" + UNTRUSTED_HTML, "warnings": []},
                {"index": 9, "label": "课末总结", "text": LAST_BLOCK, "warnings": ["公式对象须核对原文件"]},
            ],
            "sections": [{"start": 1, "end": 9, "title": "完整教案", "level": 1}],
            "assets": [
                {"asset_id": "picture-a", "label": "实验装置图", "block_index": 4, "mime_type": "image/png", "preview_supported": True, **self.asset_metadata},
                {"asset_id": "old-equation", "label": "旧公式对象", "block_index": 9, "mime_type": "application/x-ole-storage", "preview_supported": False},
            ],
            "warnings": ["教案含一个旧公式对象"],
        }

    def imported_word_asset(self, batch_id, source_id, asset_id):
        self.asset_calls.append((batch_id, source_id, asset_id))
        assert (batch_id, source_id, asset_id) == ("synthetic-batch", "lesson-a", "picture-a")
        if self.asset_failure == "exception":
            raise OSError("private/image-cache.png")
        if self.asset_failure == "corrupt":
            return {"bytes": b"not-an-image", "mime_type": "image/png"}
        if self.asset_result is not None:
            return deepcopy(self.asset_result)
        image = QImage(24 + len(self.asset_calls), 20, QImage.Format.Format_RGB32)
        image.fill(0xFF336699)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(buffer, "PNG")
        return {"bytes": bytes(data), "mime_type": "image/png", "label": "实验装置图"}

    def imported_word_reference(
        self, batch_id, source_id, source_sha256, block_start, block_end, *, expected_revision
    ):
        self.reference_calls.append((batch_id, source_id, block_start, block_end))
        return {
            "materials": f"本次选段：[Word区块{block_start}] 至 [Word区块{block_end}]",
            "warnings": [],
            "source_sha256": source_sha256,
            "revision": expected_revision,
            "reference_sha256": "b" * 64,
        }


@pytest.fixture
def reader_dialog(qt_app):
    facade = _ReaderFacade()
    dialog = ImportWordDialog(facade, "synthetic-batch")
    try:
        yield dialog, facade
    finally:
        dialog.close()
        dialog.deleteLater()
        qt_app.processEvents()


def _image_positions(browser):
    positions = []
    block = browser.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                positions.append(fragment.position())
            iterator += 1
        block = block.next()
    return positions


def _anchor_urls(browser, label):
    text_by_url = {}
    block = browser.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                href = fragment.charFormat().anchorHref()
                if href:
                    text_by_url[href] = text_by_url.get(href, "") + fragment.text()
            iterator += 1
        block = block.next()
    return [href for href, text in text_by_url.items() if label in text]


def test_reader_opens_full_original_by_default_without_expanding_preparation_range(reader_dialog):
    dialog, facade = reader_dialog
    assert dialog.reader_tabs.currentIndex() == 0
    assert "通读" in dialog.reader_tabs.tabText(0)
    assert "选段" in dialog.reader_tabs.tabText(1)
    assert isinstance(dialog.reader.browser, QTextBrowser)
    full = dialog.reader.browser.toPlainText()
    assert len(LONG_BODY) > 20_000
    assert LONG_BODY in full and IMAGE_BLOCK in full and LAST_BLOCK in full
    assert full.index(LONG_BODY) < full.index(IMAGE_BLOCK) < full.index(LAST_BLOCK)
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 1)
    assert LONG_BODY in dialog.source_preview.toPlainText()
    assert LAST_BLOCK not in dialog.source_preview.toPlainText()
    assert not facade.asset_calls and not facade.reference_calls
    assert not dialog.import_button.isEnabled()


def test_reader_scroll_and_tab_navigation_do_not_compile_or_change_selected_reference(reader_dialog):
    dialog, facade = reader_dialog
    dialog.preview_button.click()
    previous = deepcopy(dialog._preview_reference)
    previous_key = dialog._preview_key
    dialog.reader.scroll_to_block(9)
    dialog.reader_tabs.setCurrentIndex(1)
    dialog.reader_tabs.setCurrentIndex(0)
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 1)
    assert dialog._preview_reference == previous
    assert dialog._preview_key == previous_key
    assert dialog.import_button.isEnabled()
    assert len(facade.reference_calls) == 1


def test_search_return_only_finds_next_match_without_dialog_default_action(
    qt_app, reader_dialog, monkeypatch
):
    dialog, facade = reader_dialog
    path_calls, opened_urls = [], []

    def unexpected_path(*args):
        path_calls.append(args)
        raise AssertionError("search must not request an original document")

    monkeypatch.setattr(facade, "imported_word_path", unexpected_path, raising=False)
    monkeypatch.setattr(
        QDesktopServices, "openUrl", lambda url: opened_urls.append(url) or True
    )
    dialog.show()  # QT_QPA_PLATFORM=offscreen; no user-visible native window.
    qt_app.processEvents()
    reader = dialog.reader
    full_text = reader.browser.toPlainText()
    match_count = full_text.count("知识点")
    assert match_count > 1
    reader.search_edit.setText("知识点")
    dialog.open_word_button.setFocus()
    reader.search_edit.setFocus()
    qt_app.processEvents()
    assert reader.search_edit.hasFocus()
    assert f"第 1 / {match_count} 处匹配" in reader.search_status.text()
    first_position = reader.browser.textCursor().selectionStart()

    QTest.keyClick(reader.search_edit, Qt.Key.Key_Return)
    qt_app.processEvents()

    assert f"第 2 / {match_count} 处匹配" in reader.search_status.text()
    assert reader.browser.textCursor().selectionStart() > first_position
    assert reader.browser.toPlainText() == full_text
    assert dialog.reader_tabs.currentIndex() == 0
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 1)
    assert not facade.reference_calls and not facade.asset_calls
    assert not path_calls and not opened_urls
    assert dialog.reference is None and dialog._preview_reference is None
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.isVisible()


def test_reader_image_signal_loads_current_asset_after_its_source_block(reader_dialog):
    dialog, facade = reader_dialog
    assert not _image_positions(dialog.reader.browser)
    dialog.reader.image_requested.emit("picture-a")
    assert facade.asset_calls == [("synthetic-batch", "lesson-a", "picture-a")]
    assert dialog.asset_combo.currentData()["asset_id"] == "picture-a"
    assert dialog._image_pixmap is not None
    positions = _image_positions(dialog.reader.browser)
    assert len(positions) == 1
    document = dialog.reader.browser.document()
    block_end = document.find(IMAGE_BLOCK).position()
    tail_start = document.find(LAST_BLOCK).selectionStart()
    assert block_end < positions[0] < tail_start
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 1)
    assert not facade.reference_calls


def test_visible_reader_links_reach_dialog_and_old_source_link_is_rejected(reader_dialog):
    dialog, facade = reader_dialog
    browser = dialog.reader.browser
    image_links = _anchor_urls(browser, "实验装置图")
    assert len(image_links) == 1
    old_image_url = QUrl(image_links[0])
    browser.anchorClicked.emit(old_image_url)
    assert facade.asset_calls == [("synthetic-batch", "lesson-a", "picture-a")]
    assert len(_image_positions(browser)) == 1
    block_links = _anchor_urls(browser, "选此段备课")
    assert len(block_links) == 3
    browser.anchorClicked.emit(QUrl(block_links[-1]))
    assert dialog.reader_tabs.currentIndex() == 1
    assert (dialog.block_start.value(), dialog.block_end.value()) == (9, 9)
    assert LAST_BLOCK in dialog.source_preview.toPlainText()
    assert not facade.reference_calls
    dialog.source_combo.setCurrentIndex(1)
    browser.anchorClicked.emit(old_image_url)
    assert len(facade.asset_calls) == 1
    assert not _image_positions(browser)


def test_repeated_reader_image_click_reloads_even_when_combo_selection_is_unchanged(reader_dialog):
    dialog, facade = reader_dialog
    dialog.reader.image_requested.emit("picture-a")
    first_width = dialog._image_pixmap.width()
    dialog.reader.image_requested.emit("picture-a")
    assert facade.asset_calls == [("synthetic-batch", "lesson-a", "picture-a")] * 2
    assert dialog._image_pixmap.width() == first_width + 1
    assert len(_image_positions(dialog.reader.browser)) == 1


def test_unknown_reader_image_is_rejected_without_asset_or_reference_call(reader_dialog):
    dialog, facade = reader_dialog
    dialog.reader.image_requested.emit("picture-a")
    before_text = dialog.reader.browser.toPlainText()
    before_assets = list(facade.asset_calls)
    before_combo = dialog.asset_combo.currentIndex()
    dialog.reader.image_requested.emit("not-in-current-source")
    assert facade.asset_calls == before_assets
    assert dialog.asset_combo.currentIndex() == before_combo
    assert dialog.reader.browser.toPlainText() == before_text
    assert not facade.reference_calls


@pytest.mark.parametrize("failure", ["exception", "corrupt"])
def test_image_failure_clears_old_inline_picture_and_zoom(reader_dialog, failure):
    dialog, facade = reader_dialog
    dialog.reader.image_requested.emit("picture-a")
    assert _image_positions(dialog.reader.browser)
    facade.asset_failure = failure
    dialog.reader.image_requested.emit("picture-a")
    assert len(facade.asset_calls) == 2
    assert dialog._image_pixmap is None
    assert not _image_positions(dialog.reader.browser)
    assert not dialog.zoom_image_button.isEnabled()
    assert "private" not in dialog.status.text()
    assert LAST_BLOCK in dialog.reader.browser.toPlainText()


def test_unsupported_image_reports_fallback_without_loading_or_retaining_old_picture(reader_dialog):
    dialog, facade = reader_dialog
    dialog.reader.image_requested.emit("picture-a")
    dialog.reader.image_requested.emit("old-equation")
    assert len(facade.asset_calls) == 1
    assert dialog._image_pixmap is None
    assert not _image_positions(dialog.reader.browser)
    assert not dialog.zoom_image_button.isEnabled()
    assert "暂不能" in dialog.status.text()
    assert "Word" in dialog.status.text()


def test_reader_explicit_block_signal_selects_only_that_block_and_invalidates_reference(reader_dialog):
    dialog, facade = reader_dialog
    dialog.preview_button.click()
    assert dialog.import_button.isEnabled()
    dialog.reader.block_requested.emit(4)
    assert dialog.reader_tabs.currentIndex() == 1
    assert (dialog.block_start.value(), dialog.block_end.value()) == (4, 4)
    assert IMAGE_BLOCK in dialog.source_preview.toPlainText()
    assert LAST_BLOCK not in dialog.source_preview.toPlainText()
    assert dialog.reference is None and dialog._preview_reference is None
    assert not dialog.preview.toPlainText() and not dialog.import_button.isEnabled()
    assert len(facade.reference_calls) == 1
    assert not facade.asset_calls


@pytest.mark.parametrize("unknown_index", [0, 3, 100])
def test_unknown_block_signal_leaves_tab_range_and_compiled_reference_unchanged(reader_dialog, unknown_index):
    dialog, facade = reader_dialog
    dialog.preview_button.click()
    previous = deepcopy(dialog._preview_reference)
    previous_key = dialog._preview_key
    dialog.reader.block_requested.emit(unknown_index)
    assert dialog.reader_tabs.currentIndex() == 0
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 1)
    assert dialog._preview_reference == previous and dialog._preview_key == previous_key
    assert dialog.import_button.isEnabled()
    assert len(facade.reference_calls) == 1


def test_source_switch_replaces_full_text_and_clears_images_and_old_selection(reader_dialog):
    dialog, facade = reader_dialog
    dialog.reader.image_requested.emit("picture-a")
    dialog.preview_button.click()
    dialog.source_combo.setCurrentIndex(1)
    assert facade.preview_calls[-1] == ("synthetic-batch", "lesson-b")
    full = dialog.reader.browser.toPlainText()
    assert "第二份教案独有内容" in full
    assert LONG_BODY not in full and LAST_BLOCK not in full
    assert not _image_positions(dialog.reader.browser)
    assert dialog._image_pixmap is None and not dialog.zoom_image_button.isEnabled()
    assert (dialog.block_start.value(), dialog.block_end.value()) == (10, 10)
    assert dialog.reference is None and dialog._preview_reference is None
    assert not dialog.import_button.isEnabled()
    dialog.reader.image_requested.emit("picture-a")
    dialog.reader.block_requested.emit(4)
    assert len(facade.asset_calls) == 1
    assert (dialog.block_start.value(), dialog.block_end.value()) == (10, 10)
    assert len(facade.reference_calls) == 1


def test_failed_source_switch_clears_full_text_and_inline_image(reader_dialog):
    dialog, facade = reader_dialog
    dialog.reader.image_requested.emit("picture-a")
    facade.source_failure = True
    dialog.source_combo.setCurrentIndex(1)
    full = dialog.reader.browser.toPlainText()
    assert LONG_BODY not in full and IMAGE_BLOCK not in full and LAST_BLOCK not in full
    assert not _image_positions(dialog.reader.browser)
    assert dialog._source is None and dialog._image_pixmap is None
    assert not dialog.zoom_image_button.isEnabled()
    assert "private" not in dialog.status.text()


def test_word_body_html_remains_literal_and_external_links_do_not_open(reader_dialog, monkeypatch):
    dialog, facade = reader_dialog
    calls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: calls.append(url) or True)
    browser = dialog.reader.browser
    assert UNTRUSTED_HTML in browser.toPlainText()
    assert not browser.openExternalLinks()
    assert not browser.openLinks()
    assert not _image_positions(browser)
    before = browser.toPlainText()
    for target in ("https://example.invalid/outside", "file:///private/not-a-source.png"):
        browser.anchorClicked.emit(QUrl(target))
    assert not calls and not facade.asset_calls and not facade.reference_calls
    assert browser.toPlainText() == before


def test_reader_zoom_signal_uses_only_current_loaded_image(reader_dialog, monkeypatch):
    dialog, facade = reader_dialog
    opened = []
    monkeypatch.setattr(_WordImageDialog, "exec", lambda self: opened.append(self._original.size()))
    dialog.reader.image_zoom_requested.emit()
    assert not opened
    dialog.reader.image_requested.emit("picture-a")
    dialog.reader.image_zoom_requested.emit()
    assert len(opened) == 1 and opened[0] == dialog._image_pixmap.size()
    assert len(facade.asset_calls) == 1
    dialog.source_combo.setCurrentIndex(1)
    dialog.reader.image_zoom_requested.emit()
    assert len(opened) == 1


def test_asset_combo_placeholder_clears_reader_inline_picture(reader_dialog):
    dialog, _facade = reader_dialog
    dialog.reader.image_requested.emit("picture-a")
    assert _image_positions(dialog.reader.browser)
    dialog.asset_combo.setCurrentIndex(0)
    assert not _image_positions(dialog.reader.browser)
    assert dialog._image_pixmap is None
    assert not dialog.zoom_image_button.isEnabled()


@pytest.mark.parametrize("invalid_block", [None, True, 3])
def test_reader_rejects_asset_not_bound_to_an_actual_source_block(qt_app, invalid_block):
    facade = _ReaderFacade()
    facade.asset_metadata = {"block_index": invalid_block}
    dialog = ImportWordDialog(facade, "synthetic-batch")
    try:
        dialog.reader.image_requested.emit("picture-a")
        assert not facade.asset_calls
        assert not _image_positions(dialog.reader.browser)
        assert dialog._image_pixmap is None
    finally:
        dialog.close()
        dialog.deleteLater()
        qt_app.processEvents()


@pytest.mark.parametrize(
    "mode,accepted",
    [
        ("raster", True),
        ("raster_mismatch", False),
        ("derived", True),
        ("derived_original_mismatch", False),
        ("derived_preview_mismatch", False),
        ("derived_original_missing", False),
        ("derived_preview_missing", False),
    ],
)
def test_source_image_hash_binding_is_checked_before_reader_display(qt_app, mode, accepted):
    facade = _ReaderFacade()
    raw = facade.imported_word_asset("synthetic-batch", "lesson-a", "picture-a")["bytes"]
    facade.asset_calls.clear()
    digest = hashlib.sha256(raw).hexdigest()
    source_digest = "d" * 64 if mode.startswith("derived") else digest
    facade.asset_metadata = {"sha256": source_digest}
    facade.asset_result = {"bytes": raw, "mime_type": "image/png"}
    if mode.startswith("derived"):
        facade.asset_result.update(
            derived_preview=True,
            original_sha256=source_digest,
            preview_sha256=digest,
        )
    if mode == "raster_mismatch":
        facade.asset_metadata["sha256"] = "e" * 64
    if mode == "derived_original_mismatch":
        facade.asset_result["original_sha256"] = "e" * 64
    if mode == "derived_preview_mismatch":
        facade.asset_result["preview_sha256"] = "e" * 64
    if mode == "derived_original_missing":
        facade.asset_result.pop("original_sha256")
    if mode == "derived_preview_missing":
        facade.asset_result.pop("preview_sha256")
    dialog = ImportWordDialog(facade, "synthetic-batch")
    try:
        dialog.reader.image_requested.emit("picture-a")
        assert facade.asset_calls == [("synthetic-batch", "lesson-a", "picture-a")]
        assert bool(_image_positions(dialog.reader.browser)) is accepted
        assert (dialog._image_pixmap is not None) is accepted
        assert dialog.zoom_image_button.isEnabled() is accepted
        assert LAST_BLOCK in dialog.reader.browser.toPlainText()
        assert not facade.reference_calls
    finally:
        dialog.close()
        dialog.deleteLater()
        qt_app.processEvents()

"""Synthetic native blocks and in-memory Qt images; no Word or provider calls."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QPixmap, QTextDocument
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_workbench.word_lesson_reader import (
    WordLessonReader,
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def reader(qt_app):
    widget = WordLessonReader()
    widget.resize(420, 700)
    yield widget
    widget.close()
    widget.deleteLater()
    qt_app.processEvents()


def _source():
    return {
        "source_name": "演示教案.docx",
        "blocks": [
            {
                "index": 1,
                "label": "区块 1 · 开始",
                "text": "首段原文 电离",
                "warnings": [],
            },
            {
                "index": 2,
                "label": "区块 2 · 实验",
                "text": "中段原文 电离",
                "warnings": ["公式待核对"],
            },
            {
                "index": 7,
                "label": "区块 7 · 结束",
                "text": "末段原文 电离",
                "warnings": [],
            },
        ],
        "sections": [{"start": 2, "end": 7, "title": "实验与解释"}],
        "assets": [
            {
                "asset_id": "internal/png&?2",
                "label": "实验图甲",
                "block_index": 2,
                "preview_supported": True,
                "mime_type": "image/png",
            },
            {
                "asset_id": "internal/png7",
                "label": "实验图乙",
                "block_index": 7,
                "preview_supported": True,
                "mime_type": "image/png",
            },
            {
                "asset_id": "internal/emf7",
                "label": "旧式公式图",
                "block_index": 7,
                "preview_supported": False,
                "mime_type": "image/x-emf",
            },
            {
                "asset_id": "internal/unsupported",
                "label": "未支持图像",
                "block_index": 7,
                "preview_supported": False,
                "mime_type": "application/octet-stream",
            },
        ],
        "warnings": ["表格版式以原文为准"],
    }


def _href(reader, kind, value):
    return next(
        url for url, action in reader._actions.items() if action == (kind, value)
    )


def _images(reader):
    images = []
    block = reader.browser.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                images.append(
                    (fragment.position(), fragment.charFormat().toImageFormat())
                )
            iterator += 1
        block = block.next()
    return images


def _pixmap(width=1600, height=900):
    pixmap = QPixmap(width, height)
    pixmap.fill(0xFF88AA99)
    return pixmap


def test_complete_reader_keeps_more_than_20000_characters_and_last_block(reader):
    source = _source()
    source["blocks"][0]["text"] = "完整教案正文" * 5000
    selections, images = [], []
    reader.block_requested.connect(selections.append)
    reader.image_requested.connect(images.append)
    reader.set_source(source)
    text = reader.browser.toPlainText()
    assert source["blocks"][0]["text"] in text
    assert "末段原文 电离" in text
    assert "公式待核对" in text and "表格版式以原文为准" in text
    assert len(text) > 20000
    assert not selections and not images
    assert "图片 4 次" not in text


def test_native_whitespace_and_table_or_formula_text_are_preserved(reader):
    source = _source()
    original = "[表格]  左列 | 右列\n第二行\n\n空行\t制表符\n[公式待核对] H₂O + H⁺"
    source["blocks"][0]["text"] = original
    reader.set_source(source)
    assert reader._blocks[1]["text"] == original
    assert original in reader.browser.toPlainText()
    assert "<table" not in reader.browser.toHtml()


def test_untrusted_html_links_styles_and_images_are_literal_text(reader, monkeypatch):
    external = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: external.append(url))
    payload = '<a href="https://example.invalid">外链</a><img src="file:///C:/private.png"><style>body{color:red}</style>'
    source = _source()
    source["source_name"] = payload
    source["blocks"][0]["label"] = payload
    source["blocks"][0]["text"] = payload
    source["blocks"][0]["warnings"] = [payload]
    source["assets"][0]["label"] = payload
    reader.set_source(source)
    assert payload in reader.browser.toPlainText()
    assert not _images(reader)
    assert not reader.browser.openLinks() and not reader.browser.openExternalLinks()
    for target in (
        "https://example.invalid",
        "file:///C:/private.png",
        "data:image/png;base64,AAAA",
        "lesson-action:/1/99999",
        "lesson-memory:/forged",
    ):
        url = QUrl(target)
        reader.browser.anchorClicked.emit(url)
        assert (
            reader.browser.loadResource(QTextDocument.ResourceType.ImageResource, url)
            is None
        )
        assert (
            reader.browser.document().resource(
                QTextDocument.ResourceType.ImageResource, url
            )
            is None
        )
        assert (
            reader.browser.document().resource(
                QTextDocument.ResourceType.StyleSheetResource, url
            )
            is None
        )
    assert not external


def test_only_generated_current_action_mapping_emits_explicit_signals(reader):
    selected, images, zooms = [], [], []
    reader.block_requested.connect(selected.append)
    reader.image_requested.connect(images.append)
    reader.image_zoom_requested.connect(lambda: zooms.append(True))
    reader.set_source(_source())
    href = _href(reader, "image", "internal/png&?2")
    assert "internal" not in href
    reader.browser.anchorClicked.emit(QUrl(href))
    assert images == ["internal/png&?2"]
    assert not selected
    reader.scroll_to_block(7)
    reader.section_picker.setCurrentIndex(1)
    reader.search_edit.setText("末段原文")
    assert not selected
    reader.browser.anchorClicked.emit(QUrl(_href(reader, "block", 7)))
    assert selected == [7]
    assert not zooms
    reader.set_source(_source())
    reader.browser.anchorClicked.emit(QUrl(href))
    assert images == ["internal/png&?2"]


def test_images_stay_after_their_block_replace_previous_and_keep_other_actions(reader):
    reader.set_source(_source())
    assert reader.show_image("internal/png&?2", _pixmap(), "原图完整预览")
    text = reader.browser.toPlainText()
    assert text.index("中段原文") < text.index("\ufffc") < text.index("末段原文")
    assert len(_images(reader)) == 1
    assert reader.show_image("internal/png7", _pixmap(500, 1400), "另一张图")
    text = reader.browser.toPlainText()
    assert len(_images(reader)) == 1
    assert text.index("末段原文") < text.index("\ufffc")
    assert "原图完整预览" not in text
    assert _href(reader, "image", "internal/png&?2")
    assert _href(reader, "image", "internal/png7")
    assert reader.show_image("internal/emf7", None, "该旧式图形尚无法显示")
    text = reader.browser.toPlainText()
    assert not _images(reader)
    assert reader._image_pixmap is None
    assert text.index("末段原文") < text.index("该旧式图形尚无法显示")
    assert "EMF，可尝试本地转换预览" in text and "暂不支持预览" in text
    assert not any(action[0] == "zoom" for action in reader._actions.values())


def test_zoom_emits_for_current_loaded_image_only_and_derived_is_labeled(reader):
    zooms = []
    reader.image_zoom_requested.connect(lambda: zooms.append(True))
    reader.set_source(_source())
    reader.show_image("internal/emf7", _pixmap(), "转换结果待核对", derived=True)
    assert "本地转换预览" in reader.browser.toPlainText()
    href = _href(reader, "zoom", "internal/emf7")
    reader.browser.anchorClicked.emit(QUrl(href))
    assert zooms == [True]
    reader.clear_image()
    reader.browser.anchorClicked.emit(QUrl(href))
    assert zooms == [True] and not _images(reader)


def test_image_request_reveals_asset_after_long_block_and_replacement(reader, qt_app):
    source = _source()
    source["blocks"][1]["text"] = "长实验过程\n" * 350
    source["blocks"][2]["text"] = "后续长段\n" * 200
    reader.set_source(source)
    reader.show()
    qt_app.processEvents()
    reader.scroll_to_block(2)
    block_offset = reader.browser.verticalScrollBar().value()
    reader.show_image("internal/png&?2", _pixmap(), "长段之后的甲图")
    qt_app.processEvents()
    asset_offset = reader.browser.verticalScrollBar().value()
    assert asset_offset > block_offset + reader.browser.viewport().height()
    reader.show_image("internal/png7", _pixmap(), "后续长段的乙图")
    qt_app.processEvents()
    assert reader.browser.verticalScrollBar().value() > asset_offset
    assert len(_images(reader)) == 1
    reader.show_image("internal/png&?2", None, "甲图本次未能读取")
    qt_app.processEvents()
    assert reader.browser.verticalScrollBar().value() < asset_offset + 20
    assert (
        reader.browser.verticalScrollBar().value()
        > block_offset + reader.browser.viewport().height()
    )
    assert not _images(reader)


def test_clear_image_without_loaded_image_does_not_rebuild_document(reader):
    reader.set_source(_source())
    document = reader.browser.document()
    actions = dict(reader._actions)
    reader.clear_image()
    assert reader.browser.document() is document
    assert reader._actions == actions


def test_many_source_warnings_have_navigation_and_complete_text_at_end(reader, qt_app):
    source = _source()
    source["warnings"] = [f"仅全局记录的警告 {index}" for index in range(30)]
    source["blocks"][1]["text"] = "教案阅读正文\n" * 80
    reader.set_source(source)
    reader.show()
    qt_app.processEvents()
    text = reader.browser.toPlainText()
    assert "来源提醒 30 条" in text
    assert text.index("末段原文") < text.index("来源提醒 · 完整记录")
    assert all(warning in text for warning in source["warnings"])
    reader.browser.anchorClicked.emit(QUrl(_href(reader, "warnings", None)))
    assert (
        reader.browser.verticalScrollBar().value() > reader.browser.viewport().height()
    )


def test_metafile_status_distinguishes_eligible_emf_from_wmf_without_loading(reader):
    source = _source()
    source["assets"].append(
        {**source["assets"][2], "asset_id": "wmf", "mime_type": "image/x-wmf"}
    )
    requested = []
    reader.image_requested.connect(requested.append)
    reader.set_source(source)
    text = reader.browser.toPlainText()
    assert "EMF，可尝试本地转换预览" in text
    assert "WMF 旧式图形，需用 Word 核对" in text
    assert not requested and not _images(reader)


@pytest.mark.parametrize("block_index", [99, "2", True, None])
def test_assets_without_valid_block_relationship_are_rejected(reader, block_index):
    source = _source()
    source["assets"][0]["block_index"] = block_index
    reader.set_source(source)
    assert "internal/png&?2" not in reader._assets
    assert not reader.show_image("internal/png&?2", _pixmap(), "不能放错区块")
    assert "不能放错区块" not in reader.browser.toPlainText()
    assert not _images(reader)


def test_duplicate_asset_ids_are_ambiguous_and_unknown_id_clears_old_image(reader):
    source = _source()
    source["assets"].append({**source["assets"][0], "block_index": 7})
    reader.set_source(source)
    assert "internal/png&?2" not in reader._assets
    reader.show_image("internal/png7", _pixmap(), "已有图片")
    assert not reader.show_image("unknown", _pixmap(), "不要猜测归属")
    assert not _images(reader)
    assert "已有图片" not in reader.browser.toPlainText()
    assert "不要猜测归属" not in reader.browser.toPlainText()


def test_new_source_clears_image_search_and_old_memory_resource(reader):
    reader.set_source(_source())
    reader.show_image("internal/png7", _pixmap(), "旧来源图片")
    reader.search_edit.setText("电离")
    old_url = QUrl(reader._image_url)
    assert (
        reader.browser.document().resource(
            QTextDocument.ResourceType.ImageResource, old_url
        )
        is not None
    )
    reader.set_source({"blocks": [{"index": 1, "text": "新教案正文"}]})
    assert reader.search_edit.text() == "" and not reader._matches
    assert not reader._assets and not _images(reader)
    assert reader._image_asset_id is None and reader._image_pixmap is None
    assert (
        reader.browser.document().resource(
            QTextDocument.ResourceType.ImageResource, old_url
        )
        is None
    )
    assert "旧来源图片" not in reader.browser.toPlainText()
    reader.set_source(None)
    assert "在这里通读教案" in reader.browser.toPlainText()
    assert not reader._blocks and not reader._block_anchors
    assert not reader.section_picker.isEnabled()


def test_search_next_previous_wrap_and_anchor_keep_all_text(reader, qt_app):
    source = _source()
    source["blocks"][1]["text"] = "中段原文 电离\n" + "更多原文\n" * 100
    reader.set_source(source)
    reader.show()
    qt_app.processEvents()
    before = reader.browser.toPlainText()
    reader.search_edit.setText("电离")
    assert reader.search_status.text().startswith("第 1 / 3")
    reader.next_button.click()
    assert reader.search_status.text().startswith("第 2 / 3")
    reader.previous_button.click()
    reader.previous_button.click()
    assert reader.search_status.text().startswith("第 3 / 3")
    assert reader.browser.textCursor().selectedText() == "电离"
    assert reader.scroll_to_block(7)
    assert reader.browser.verticalScrollBar().value() > 0
    assert not reader.scroll_to_block(99) and not reader.scroll_to_block(True)
    reader.search_edit.setText("完全不存在的搜索")
    assert "未找到匹配" in reader.search_status.text()
    assert not reader.next_button.isEnabled()
    assert reader.browser.toPlainText() == before
    reader.search_edit.clear()
    assert not reader._matches


@pytest.mark.parametrize("width", [420, 760, 1100])
def test_narrow_layout_wraps_long_words_and_keeps_image_aspect_ratio(
    reader, qt_app, width
):
    source = _source()
    source["source_name"] = "VeryLongSourceName" * 60
    source["blocks"][0]["text"] = "NonBreakingChemicalIdentifier" * 90
    reader.set_source(source)
    reader.resize(width, 750)
    reader.show()
    qt_app.processEvents()
    original = _pixmap(2200, 3300)
    reader.show_image("internal/png&?2", original, "完整纵向图")
    qt_app.processEvents()
    assert reader.width() == width
    assert reader.browser.horizontalScrollBar().maximum() == 0
    assert (
        reader.browser.document().size().width()
        <= reader.browser.viewport().width() + 1
    )
    image = _images(reader)[0][1]
    assert image.width() <= reader.browser.viewport().width() - 40
    assert abs(image.height() / image.width() - 1.5) < 0.01
    reader.resize(420, 750)
    qt_app.processEvents()
    image = _images(reader)[0][1]
    assert image.width() <= reader.browser.viewport().width() - 40
    assert abs(image.height() / image.width() - 1.5) < 0.01
    assert original.width() == 2200 and original.height() == 3300


def test_visible_420_reader_with_616_by_155_image_and_hidden_resize_recovery(qt_app):
    from PySide6.QtTest import QTest

    from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import (
        ImportWordDialog,
    )

    source = {
        "source_name": "合成长教案.docx",
        "source_sha256": "a" * 64,
        "revision": "synthetic-long-lesson",
        "blocks": [
            {
                "index": index,
                "label": f"区块 {index}",
                "text": "合成长教案正文，保留全部区块。" * 10 + "\n下一行文字",
                "warnings": [],
            }
            for index in range(1, 598)
        ],
        "assets": [
            {
                "asset_id": "synthetic-616-by-155",
                "label": "仅供布局测试的合成图片",
                "block_index": 38,
                "preview_supported": True,
                "mime_type": "image/png",
            }
        ],
    }

    class Facade:
        def imported_word_sources(self, *_args):
            return [{"source_id": "synthetic", "source_name": source["source_name"]}]

        def imported_word_preview(self, *_args):
            return source

    def assert_visible_fit():
        # Fixed event intervals allow Qt layout timers to run. Do not poll or
        # retry until a nonzero scrollbar range disappears: either check fails.
        QTest.qWait(30)
        browser = dialog.reader.browser
        assert browser.isVisible()
        assert browser.horizontalScrollBar().maximum() == 0
        first_geometry = browser.viewport().size()
        QTest.qWait(20)
        assert browser.viewport().size() == first_geometry
        assert browser.horizontalScrollBar().maximum() == 0
        assert browser.document().size().width() <= browser.viewport().width()
        image = _images(dialog.reader)[0][1]
        assert image.width() < browser.viewport().width()
        assert abs(image.height() / image.width() - 155 / 616) < 0.01

    dialog = ImportWordDialog(Facade(), "synthetic-batch")
    try:
        dialog.resize(900, 820)
        dialog.show()
        QTest.qWait(30)
        dialog.reader.show_image(
            "synthetic-616-by-155", _pixmap(616, 155), "合成图，未读取任何原文件"
        )
        assert_visible_fit()
        dialog.resize(420, 820)
        dialog.reader.scroll_to_block(38)
        assert_visible_fit()
        assert dialog.width() == 420
        dialog.reader_tabs.setCurrentIndex(1)
        dialog.resize(900, 820)
        QTest.qWait(30)
        assert not dialog.reader.browser.isVisible()
        dialog.reader_tabs.setCurrentIndex(0)
        assert_visible_fit()
        dialog.resize(420, 820)
        assert_visible_fit()
        assert dialog.width() == 420
        assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 1)
        assert "区块 597" in dialog.reader.browser.toPlainText()
        assert dialog.reader._image_pixmap.size() == _pixmap(616, 155).size()
    finally:
        dialog.close()
        dialog.deleteLater()
        qt_app.processEvents()


def test_cjk_latin_glyph_boundary_has_real_width_gutter_after_scrollbar_appears(
    reader, qt_app
):
    from PySide6.QtGui import QFont
    from PySide6.QtTest import QTest

    # Entirely synthetic: at 16 px CJK glyphs, the 326 px line advance can
    # produce a 327 px natural line width when the following run begins with A.
    body = "中" * 4 + "HX" + "文" * 15 + "A下一行测试"
    reader.setFont(QFont("Microsoft YaHei", 10))
    reader.set_source({"blocks": [{"index": 1, "label": "字形边界", "text": body}]})
    reader.resize(390, 300)  # 366 px viewport after the vertical bar appears.
    reader.show()
    QTest.qWait(30)
    browser = reader.browser
    assert browser.verticalScrollBar().isVisible()
    assert browser.document().textWidth() <= browser.viewport().width() - 2
    assert browser.document().size().width() <= browser.viewport().width()
    assert browser.horizontalScrollBar().maximum() == 0
    assert body in browser.toPlainText()
    reader.resize(420, 700)
    QTest.qWait(30)
    assert browser.document().size().width() <= browser.viewport().width()
    assert browser.horizontalScrollBar().maximum() == 0
    assert body in browser.toPlainText()


@pytest.mark.parametrize(
    "bad_blocks",
    [
        None,
        [{"index": True, "text": "错误"}],
        [{"index": 1, "text": 42}],
        [{"index": 1, "text": "a"}, {"index": 1, "text": "b"}],
    ],
)
def test_malformed_source_fails_closed_after_clearing_previous_state(
    reader, bad_blocks
):
    reader.set_source(_source())
    reader.show_image("internal/png7", _pixmap(), "旧图")
    with pytest.raises(ValueError, match="invalid native Word blocks"):
        reader.set_source({"blocks": bad_blocks})
    assert not reader._blocks and not reader._assets and not _images(reader)
    assert "旧图" not in reader.browser.toPlainText()

"""Capture an owned offscreen Word reader without a model or personal settings.

The default fixture contains only synthetic blocks and in-memory PNGs, not a
DOCX. --source reads a supplied original DOCX without changing it. Screenshots
and metadata stay private; image generation is not visual approval.
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
PRIVATE_ROOT = ROOT / "runtime/deeptutor_shchem/word_reader_0160"
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class ReadingFacade:
    """Only the three read operations required by this dialog are available."""

    def __init__(self, preview, asset_loader):
        self.preview = deepcopy(preview)
        self.asset_loader = asset_loader
        self.calls = []

    def imported_word_sources(self, batch_id):
        assert batch_id == "READING-QA"
        self.calls.append("sources")
        return [
            {
                "source_id": "READING-SOURCE",
                "source_name": self.preview["source_name"],
                "role": "handout",
            }
        ]

    def imported_word_preview(self, batch_id, source_id):
        assert (batch_id, source_id) == ("READING-QA", "READING-SOURCE")
        self.calls.append("preview")
        return deepcopy(self.preview)

    def imported_word_asset(self, batch_id, source_id, asset_id):
        assert (batch_id, source_id) == ("READING-QA", "READING-SOURCE")
        result = self.asset_loader(asset_id)
        self.calls.append(
            {
                "operation": "asset",
                "asset_id": asset_id,
                "sha256": _sha(result["bytes"]),
            }
        )
        return result

    def imported_word_image_reference(self, *_args, **_kwargs):
        # Match the production capability flag without pretending this reader
        # QA exercised actual reference compilation, persistence or model use.
        raise AssertionError("Reference compilation is outside this read-only QA")


def _synthetic_source():
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    bodies = [
        "合成演示教案\n本资料用于检查软件阅读与选材界面，不是教材原文或真实试题。教师先完整阅读，再决定哪些内容用于本课。",
        "从观察到解释\n先写出观察到的现象，再补充发生现象时的条件。把现象和条件联系起来，说明判断依据；结论应能回应导入问题。下方配图与本区块关联，可点击对照。",
        "课堂记录要点\n观察对象：明确比较的是哪一组材料。\n控制条件：记录保持不变的条件与改变的因素。\n证据说明：说明哪一条现象支持当前解释。",
        "例题与讲评的安排\n先让学生独立完成判断，再展示分析过程。讲评时回到本节的知识总结，指出本题使用了哪项条件。配图只是合成界面示意，不构成实验装置或化学证据。",
        "选择本课需要的内容\n教师可以从完整教案中选取一个区块，再调整起止范围。选择并不等于已经追加到备课；还须预览将带入的文字与原图，确认范围。",
        "课堂小结与课后安排\n请学生用自己的话复述判断依据，并在笔记中标注仍需核对的问题。\n合成教案末块标记：全文阅读应保留这一行。",
    ]
    blocks = [
        {
            "index": i,
            "label": f"区块 {i} · {body.splitlines()[0]}",
            "text": body,
            "warnings": [],
        }
        for i, body in enumerate(bodies, 1)
    ]
    assets, payloads = [], {}
    for number, block_index in enumerate((2, 4, 5), 1):
        image = QImage(720, 220, QImage.Format.Format_ARGB32)
        image.fill(QColor("#f5f8f1"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Microsoft YaHei", 15))
        painter.setPen(QColor("#244e3d"))
        painter.drawText(
            QRect(25, 12, 670, 40),
            Qt.AlignmentFlag.AlignCenter,
            f"合成教学示意图 {number}",
        )
        for x, label in ((25, "观察现象"), (270, "记录条件"), (515, "形成解释")):
            painter.setBrush(QColor("#e2ecda"))
            painter.drawRoundedRect(x, 75, 180, 75, 10, 10)
            painter.drawText(QRect(x, 75, 180, 75), Qt.AlignmentFlag.AlignCenter, label)
        for x in (205, 450):
            painter.drawText(QRect(x, 75, 65, 75), Qt.AlignmentFlag.AlignCenter, "→")
        painter.setFont(QFont("Microsoft YaHei", 11))
        painter.drawText(
            QRect(25, 165, 670, 35),
            Qt.AlignmentFlag.AlignCenter,
            "仅用于界面演示 · 非教材、非试题、非实验装置",
        )
        painter.end()
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(buffer, "PNG")
        raw = bytes(encoded)
        asset_id = f"SYNTHETIC-IMAGE-{number}"
        payloads[asset_id] = {
            "bytes": raw,
            "mime_type": "image/png",
            "label": f"合成示意图 {number}",
        }
        assets.append(
            {
                "asset_id": asset_id,
                "label": f"区块 {block_index} 的合成示意图",
                "block_index": block_index,
                "sha256": _sha(raw),
                "mime_type": "image/png",
                "preview_supported": True,
            }
        )
    preview = {
        "source_name": "合成界面演示讲义.docx",
        "source_sha256": _sha("\n".join(bodies).encode("utf-8")),
        "revision": "synthetic-word-reader-ui-v1",
        "blocks": blocks,
        "sections": [
            {"title": "导入与知识总结", "start": 1, "end": 3},
            {"title": "例题与课堂小结", "start": 4, "end": 6},
        ],
        "assets": assets,
        "warnings": [],
    }
    return preview, lambda asset_id: deepcopy(payloads[asset_id])


def _settle(app):
    for _ in range(10):
        app.processEvents()


def _activate(reader, kind, value):
    from PySide6.QtCore import QUrl

    matches = [
        url for url, action in reader._actions.items() if action == (kind, value)
    ]
    assert len(matches) == 1, "The corresponding reader action is missing"
    reader.browser.anchorClicked.emit(QUrl(matches[0]))


def _image_fragments(reader):
    images = []
    block = reader.browser.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                form = fragment.charFormat().toImageFormat()
                images.append(
                    {
                        "position": fragment.position(),
                        "name": form.name(),
                        "size": [form.width(), form.height()],
                    }
                )
            iterator += 1
        block = block.next()
    return images


def _choose_asset(preview, loader, substantial, explicit_asset_id=None):
    import io

    from PIL import Image

    if explicit_asset_id:
        matches = [
            asset for asset in preview["assets"]
            if asset["asset_id"] == explicit_asset_id
        ]
        assert len(matches) == 1, "Explicit QA asset must have one source binding"
        asset = matches[0]
        assert asset.get("preview_supported"), "Explicit QA asset is unsupported"
        assert asset.get("mime_type") in {"image/png", "image/jpeg", "image/webp"}
        result = loader(explicit_asset_id)
        assert _sha(result["bytes"]) == asset["sha256"]
        with Image.open(io.BytesIO(result["bytes"])) as image:
            dimensions = list(image.size)
        return asset, {
            "selection": "explicit_source_bound_qa_asset",
            "asset_id": explicit_asset_id,
            "size": dimensions,
            "role_inferred": False,
        }

    candidates = []
    for asset in preview["assets"]:
        if not asset.get("preview_supported"):
            continue
        if not substantial:
            if asset.get("mime_type") == "image/png":
                return asset, {"selection": "first_supported_png"}
            continue
        if asset.get("mime_type") not in {"image/png", "image/jpeg", "image/webp"}:
            continue
        result = loader(asset["asset_id"])
        assert _sha(result["bytes"]) == asset["sha256"]
        with Image.open(io.BytesIO(result["bytes"])) as image:
            width, height = image.size
            if width < 200 or height < 100 or width * height < 30000:
                continue
            rgba = image.convert("RGBA")
            pixels = (
                rgba.get_flattened_data()
                if hasattr(rgba, "get_flattened_data")
                else rgba.getdata()
            )
            nonwhite = sum(
                1
                for red, green, blue, alpha in pixels
                if alpha > 32 and min(red, green, blue) < 225
            )
            fraction = nonwhite / (width * height)
        candidates.append(
            {
                "asset_id": asset["asset_id"],
                "size": [width, height],
                "nonwhite_fraction": fraction,
            }
        )
        if fraction >= 0.02:
            return asset, {
                "selection": "first_size_and_nonwhite_qualified_raster",
                "role_inferred": False,
                "qualified_candidate": candidates[-1],
                "criterion": "width >= 200, height >= 100, area >= 30000, nonwhite fraction >= 0.02",
            }
    raise AssertionError("No image satisfies the requested QA selection")


def _geometry(dialog):
    browser = dialog.reader.browser
    bar = browser.horizontalScrollBar()
    return {
        "dialog_size": [dialog.width(), dialog.height()],
        "browser_size": [browser.width(), browser.height()],
        "viewport_size": [browser.viewport().width(), browser.viewport().height()],
        "document_text_width": browser.document().textWidth(),
        "document_ideal_width": browser.document().idealWidth(),
        "document_size": [
            browser.document().size().width(), browser.document().size().height()
        ],
        "image_formats": _image_fragments(dialog.reader),
        "horizontal_maximum": bar.maximum(),
        "horizontal_minimum": bar.minimum(),
        "horizontal_value": bar.value(),
        "horizontal_visible": bar.isVisible(),
        "horizontal_policy": str(browser.horizontalScrollBarPolicy()),
        "vertical_visible": browser.verticalScrollBar().isVisible(),
    }


def _capture(app, dialog, output, width, height, *, block=None, focus_image=False):
    dialog.resize(width, height)
    dialog.show()
    _settle(app)
    if block is not None:
        assert dialog.reader.scroll_to_block(block)
    elif dialog.reader_tabs.currentIndex() == 0:
        dialog.reader.browser.verticalScrollBar().setValue(0)
    _settle(app)
    if focus_image:
        browser = dialog.reader.browser
        fragments = _image_fragments(dialog.reader)
        assert len(fragments) == 1
        image_block = browser.document().findBlock(fragments[0]["position"])
        bounds = browser.document().documentLayout().blockBoundingRect(image_block)
        # This is ordinary vertical navigation in our owned QA window. Keep the
        # source-image link above the complete image when it fits the viewport.
        headroom = max(0, min(36, browser.viewport().height() - bounds.height() - 8))
        browser.verticalScrollBar().setValue(round(bounds.top() - headroom))
        _settle(app)
    actual = [dialog.width(), dialog.height()]
    assert actual == [width, height], f"Window expanded: {actual}"
    horizontal = dialog.reader.browser.horizontalScrollBar().maximum()
    observed_geometry = _geometry(dialog)
    if horizontal:
        geometry = {
            "passed": False,
            "requested_size": [width, height],
            "actual_size": actual,
            **observed_geometry,
        }
        failure_image = output.with_name("failure-observation-" + output.name)
        assert dialog.grab().save(str(failure_image), "PNG")
        geometry["failure_png"] = str(failure_image)
        output.with_name("failure-observation.json").write_text(
            json.dumps(geometry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    assert horizontal == 0, f"Reader horizontal overflow: {horizontal}"
    pixmap = dialog.grab()
    assert pixmap.save(str(output), "PNG")
    return {
        "path": str(output),
        "requested_size": [width, height],
        "actual_size": actual,
        "png_size": [pixmap.width(), pixmap.height()],
        "tab_index": dialog.reader_tabs.currentIndex(),
        "reader_horizontal_maximum": horizontal,
        "reader_vertical_value": dialog.reader.browser.verticalScrollBar().value(),
        "reader_vertical_maximum": dialog.reader.browser.verticalScrollBar().maximum(),
        "visible_block_anchor": block,
        "vertical_image_focus_requested": focus_image,
        "reader_geometry": observed_geometry,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New directory under runtime/deeptutor_shchem/word_reader_0160",
    )
    parser.add_argument(
        "--source", type=Path, help="Optional existing DOCX; never edited or copied"
    )
    image_choice = parser.add_mutually_exclusive_group()
    image_choice.add_argument(
        "--substantial-image",
        action="store_true",
        help="QA-only image selection by dimensions and nonwhite pixels; no role inference",
    )
    image_choice.add_argument(
        "--asset-id", help="Explicit source asset ID for repeatable image QA"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        not output.is_relative_to(PRIVATE_ROOT.resolve())
        or output == PRIVATE_ROOT.resolve()
    ):
        parser.error(
            "Output must be a new child of the private word_reader_0160 directory"
        )
    if output.exists():
        parser.error("Output already exists; use a new private run directory")

    from PySide6.QtGui import QFont, QFontDatabase, QImage

    from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
        PreparationSourcesService,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import (
        ImportWordDialog,
    )

    app = create_application([])
    assert app.platformName() == "offscreen"
    for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"):
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    assert "Microsoft YaHei" in QFontDatabase.families(), "Chinese QA font unavailable"
    app.setFont(QFont("Microsoft YaHei", 10))
    source, before, original_bytes = None, None, None
    if args.source:
        source = args.source.resolve(strict=True)
        if source.suffix.casefold() != ".docx":
            parser.error("--source must be an existing DOCX")
        original_bytes = source.read_bytes()
        before = _sha(original_bytes)
        service = PreparationSourcesService(ROOT)
        preview = service.word_preview(source)
        assert preview["source_sha256"] == before

        def asset_loader(asset_id):
            descriptor = next(
                asset for asset in preview["assets"] if asset["asset_id"] == asset_id
            )
            return service.word_asset_bytes(
                original_bytes, asset_id, expected_sha256=descriptor["sha256"]
            )
    else:
        preview, asset_loader = _synthetic_source()
    facade = ReadingFacade(preview, asset_loader)
    output.mkdir(parents=True)
    report = {
        "synthetic_only": source is None,
        "source_path": str(source) if source else None,
        "source_sha256_before": before,
        "block_count": len(preview["blocks"]),
        "source_text_characters": sum(
            len(block["text"]) for block in preview["blocks"]
        ),
        "asset_count": len(preview.get("assets", [])),
        "ui_source_sha256": {
            name: _sha(
                (
                    ROOT / "integrations/deeptutor_shchem_v1/desktop_workbench" / name
                ).read_bytes()
            )
            for name in (
                "word_lesson_reader.py",
                "import_word_dialog.py",
                "app.py",
                "main_window.py",
            )
        },
        "font": "Microsoft YaHei",
        "platform": app.platformName(),
        "native_user_gui_operated": False,
        "provider_called": False,
        "personal_settings_read": False,
        "docx_created_or_modified": False,
        "production_image_reference_capability_declared": True,
        "image_reference_compilation_executed": False,
        "visual_inspection_performed": False,
        "captures": [],
    }
    dialog = ImportWordDialog(facade, "READING-QA")
    try:
        assert dialog._source is not None, "Preview failed to load"
        assert dialog.reader_tabs.currentIndex() == 0, (
            "Full reading must open by default"
        )
        initial_range = (dialog.block_start.value(), dialog.block_end.value())
        blocks = preview["blocks"]
        last = blocks[-1]
        assert last["index"] in dialog.reader._block_anchors
        assert dialog.reader.scroll_to_block(last["index"])
        last_nonempty = next(
            block for block in reversed(blocks) if block["text"].strip()
        )
        # Qt represents line separators differently; whitespace normalization
        # checks retained words without asserting Word's original page layout.
        assert " ".join(last_nonempty["text"].split()) in " ".join(
            dialog.reader.browser.toPlainText().split()
        )
        assert (dialog.block_start.value(), dialog.block_end.value()) == initial_range
        report["last_block_present"] = True
        report["last_block_index"] = last["index"]
        report["last_nonempty_block_index"] = last_nonempty["index"]
        report["reading_did_not_change_preparation_range"] = True
        dialog.reader.browser.verticalScrollBar().setValue(0)
        report["captures"].append(
            _capture(app, dialog, output / "reader-full-900x820.png", 900, 820)
        )
        asset, image_selection = _choose_asset(
            preview, asset_loader, args.substantial_image, args.asset_id
        )
        report["qa_image_selection"] = image_selection
        _activate(dialog.reader, "image", asset["asset_id"])
        _settle(app)
        assert dialog.reader_tabs.currentIndex() == 0
        assert dialog.reader._image_asset_id == asset["asset_id"]
        assert dialog.reader._image_pixmap is not None
        original_image = asset_loader(asset["asset_id"])
        assert _sha(original_image["bytes"]) == asset["sha256"]
        if source is not None:
            suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[
                asset["mime_type"]
            ]
            reference_path = output / ("source-asset-original" + suffix)
            reference_path.write_bytes(original_image["bytes"])
            report["source_reference_image"] = {
                "path": str(reference_path),
                "sha256": _sha(reference_path.read_bytes()),
                "unaltered_asset_bytes": True,
            }
        # QPixmap may choose an equivalent premultiplied/storage format. Compare
        # normalized decoded pixels, not that implementation-specific format.
        pixel_format = QImage.Format.Format_RGBA8888
        assert dialog.reader._image_pixmap.toImage().convertToFormat(
            pixel_format
        ) == QImage.fromData(original_image["bytes"]).convertToFormat(pixel_format)
        image_fragments = _image_fragments(dialog.reader)
        assert len(image_fragments) == 1
        current_label = next(
            block["label"] for block in blocks if block["index"] == asset["block_index"]
        )
        image_position = image_fragments[0]["position"]
        plain = dialog.reader.browser.toPlainText()
        assert plain.find(current_label) < image_position
        following = next(
            (block for block in blocks if block["index"] > asset["block_index"]), None
        )
        if following is not None:
            assert image_position < plain.find(following["label"])
        report["same_block_image"] = {
            "asset_id": asset["asset_id"],
            "block_index": asset["block_index"],
            "original_sha256": asset["sha256"],
            "original_pixel_size": [
                dialog.reader._image_pixmap.width(),
                dialog.reader._image_pixmap.height(),
            ],
            "decoded_pixels_equal": True,
            "image_between_associated_and_next_block": True,
        }
        report["captures"].append(
            _capture(
                app,
                dialog,
                output / "reader-image-900x820.png",
                900,
                820,
                block=asset["block_index"],
                focus_image=True,
            )
        )
        report["captures"].append(
            _capture(
                app,
                dialog,
                output / "reader-image-420x820.png",
                420,
                820,
                block=asset["block_index"],
                focus_image=True,
            )
        )
        _activate(dialog.reader, "block", asset["block_index"])
        assert dialog.reader_tabs.currentIndex() == 1
        assert (
            dialog.block_start.value()
            == dialog.block_end.value()
            == asset["block_index"]
        )
        assert dialog.reference is None
        report["selection_action_opened_tab_1"] = True
        report["selection_did_not_append_reference"] = True
        # Deliberately resize with the reader hidden, then show it again. A
        # hidden page can have transient geometry; the active page must recover.
        dialog.resize(900, 820)
        _settle(app)
        hidden_horizontal = dialog.reader.browser.horizontalScrollBar().maximum()
        dialog.reader_tabs.setCurrentIndex(0)
        _settle(app)
        restored_horizontal = dialog.reader.browser.horizontalScrollBar().maximum()
        report["hidden_resize_recovery"] = {
            "hidden_horizontal_maximum": hidden_horizontal,
            "visible_again_horizontal_maximum": restored_horizontal,
        }
        assert restored_horizontal == 0, (
            "Reader overflow persists after becoming visible"
        )
        dialog.reader_tabs.setCurrentIndex(1)
        report["captures"].append(
            _capture(app, dialog, output / "reader-selection-900x820.png", 900, 820)
        )
        selected_range = (dialog.block_start.value(), dialog.block_end.value())
        dialog.whole_document_button.click()
        assert dialog.reader_tabs.currentIndex() == 0
        assert (dialog.block_start.value(), dialog.block_end.value()) == selected_range
        report["whole_document_action_preserved_selection"] = True
        if source is not None:
            dialog.reader_tabs.setCurrentIndex(0)
            report["captures"].append(
                _capture(
                    app,
                    dialog,
                    output / "reader-last-block-900x820.png",
                    900,
                    820,
                    block=last["index"],
                )
            )
        report["facade_calls"] = facade.calls
        report["completed"] = True
    finally:
        dialog.close()
        dialog.deleteLater()
        _settle(app)
        if source is not None:
            after = _sha(source.read_bytes())
            report["source_sha256_after"] = after
            report["source_unchanged"] = before == after
            assert before == after, "Original DOCX changed during read-only UI QA"
        (output / "qa-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

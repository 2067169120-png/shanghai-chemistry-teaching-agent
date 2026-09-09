from __future__ import annotations

import os
from copy import deepcopy
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopVisualImportReceipt,
    DesktopVisualImportSourceSummary,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import (
    ImportWordDialog,
    _WordImageDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


class _Facade:
    def __init__(self):
        self.preview_calls = []
        self.reference_calls = []
        self.asset_calls = []
        self.changed = None
        self.fail = False

    def imported_word_sources(self, batch_id):
        return [
            {
                "source_id": "internal-handout",
                "source_name": "讲义.docx",
                "role": "handout",
                "import_state": "native_text_partial_visual_required",
            },
            {
                "source_id": "internal-answer",
                "source_name": "答案.docx",
                "role": "answer",
                "import_state": "native_text_complete",
            },
        ]

    def imported_word_preview(self, batch_id, source_id):
        self.preview_calls.append((batch_id, source_id))
        return {
            "source_name": "讲义.docx"
            if source_id == "internal-handout"
            else "答案.docx",
            "source_sha256": "a" * 64,
            "revision": "internal-revision",
            "blocks": [
                {
                    "index": 1,
                    "label": "区块 1 · 电离",
                    "text": f"{source_id == 'internal-answer' and '参考答案' or '讲义'}：电离知识点",
                    "warnings": [],
                },
                {
                    "index": 2,
                    "label": "区块 2 · 公式缺口",
                    "text": "[公式待核对]",
                    "warnings": ["区块 2 的公式未完整读取，须核对原文"],
                },
            ],
            "sections": [{"start": 1, "end": 2, "title": "电离章节", "level": 1}],
            "assets": [
                {
                    "asset_id": "internal-image",
                    "label": "区块 2 · 来源图片",
                    "block_index": 2,
                    "mime_type": "image/png",
                    "preview_supported": True,
                },
                {
                    "asset_id": "internal-unsupported",
                    "label": "区块 2 · 旧式公式对象",
                    "block_index": 2,
                    "mime_type": "image/x-emf",
                    "preview_supported": False,
                },
            ],
            "warnings": ["图片中的公式和版面关系仍需核对"],
        }

    def imported_word_reference(
        self,
        batch_id,
        source_id,
        source_sha256,
        block_start,
        block_end,
        *,
        expected_revision,
    ):
        self.reference_calls.append(
            (
                batch_id,
                source_id,
                source_sha256,
                block_start,
                block_end,
                expected_revision,
            )
        )
        if self.fail:
            raise OSError("C:/private/cache/internal-batch.docx")
        reference = {
            "materials": f"来源：{'讲义' if source_id == 'internal-handout' else '答案'}.docx\nWord 区块 {block_start}—{block_end}\n电离知识点",
            "warnings": ["公式未完整读取，须核对原文"],
            "source_sha256": source_sha256,
            "revision": expected_revision,
            "reference_sha256": "b" * 64,
        }
        if self.changed:
            reference[self.changed] += "已变化"
        return reference

    def imported_word_asset(self, batch_id, source_id, asset_id):
        self.asset_calls.append((batch_id, source_id, asset_id))
        image = QImage(20, 20, QImage.Format.Format_RGB32)
        image.fill(0xFFCCDDEE)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return {"bytes": bytes(data), "mime_type": "image/png", "label": "来源图片"}

    def list_resumable_visual_import_batches(self):
        return ()


def test_default_selection_and_source_choice_clear_previous_preview(qt_app):
    facade = _Facade()
    dialog = ImportWordDialog(facade, "internal-batch")
    assert facade.preview_calls == [("internal-batch", "internal-handout")]
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 1)
    assert "讲义" in dialog.source_preview.toPlainText()
    dialog.preview_button.click()
    assert dialog.import_button.isEnabled()
    assert dialog.reference is None  # Only confirmed references can leave the dialog.
    dialog.source_combo.setCurrentIndex(1)
    assert "参考答案" in dialog.source_preview.toPlainText()
    assert not dialog.import_button.isEnabled()
    assert not dialog.preview.toPlainText()
    assert facade.reference_calls[0][1] == "internal-handout"
    dialog.preview_button.click()
    dialog.import_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert "答案.docx" in dialog.reference["materials"]
    assert facade.reference_calls[-1][1] == "internal-answer"
    dialog.close()


def test_section_range_and_missing_formula_warnings_are_visible_and_preserved(qt_app):
    dialog = ImportWordDialog(_Facade(), "internal-batch")
    dialog.section_picker.setCurrentIndex(1)
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 2)
    assert "公式未完整读取" in dialog.source_preview.toPlainText()
    dialog.preview_button.click()
    assert "公式未完整读取" in dialog.preview.toPlainText()
    assert "1 条" in dialog.status.text()
    dialog.import_button.click()
    assert dialog.reference["warnings"] == ["公式未完整读取，须核对原文"]
    dialog.close()


def test_range_change_invalidates_preview_and_invalid_range_never_compiles(qt_app):
    facade = _Facade()
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.preview_button.click()
    dialog.block_end.setValue(2)
    assert not dialog.import_button.isEnabled()
    assert not dialog.preview.toPlainText()
    dialog.block_start.setValue(2)
    dialog.block_end.setValue(1)
    assert not dialog.preview_button.isEnabled()
    dialog._confirm()
    assert len(facade.reference_calls) == 1
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.close()


@pytest.mark.parametrize(
    "changed", ["materials", "reference_sha256", "source_sha256", "revision"]
)
def test_confirm_recompiles_and_rejects_changed_content_or_identity(qt_app, changed):
    facade = _Facade()
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.preview_button.click()
    facade.changed = changed
    dialog.import_button.click()
    assert len(facade.reference_calls) == 2
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.reference is None
    assert not dialog.import_button.isEnabled()
    assert "已变化" in dialog.status.text()
    dialog.close()


def test_confirm_failure_invalidates_preview_without_exposing_raw_error(qt_app):
    facade = _Facade()
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.preview_button.click()
    facade.fail = True
    dialog.import_button.click()
    assert dialog.reference is None
    assert not dialog.import_button.isEnabled()
    assert "核对失败" in dialog.status.text()
    assert "private" not in dialog.status.text()
    dialog.close()


@pytest.mark.parametrize("stale", [False, True])
def test_body_navigation_keeps_exact_reference_and_confirmation_hash_check(
    qt_app, stale
):
    facade = _Facade()
    compile_reference = facade.imported_word_reference

    def with_body(*args, **kwargs):
        reference = compile_reference(*args, **kwargs)
        reference["materials"] = (
            "来源说明\n" * 20 + "[Word区块1]\n" + reference["materials"]
        )
        return reference

    facade.imported_word_reference = with_body
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.resize(420, 780)
    dialog.show()
    qt_app.processEvents()
    dialog.preview_button.click()
    before_text = dialog.preview.toPlainText()
    before_reference = deepcopy(dialog._preview_reference)
    before_key = dialog._preview_key
    assert dialog.preview_body_button.isEnabled()
    dialog.preview_body_button.click()
    assert dialog.preview.textCursor().position() == before_text.index("[Word区块1]")
    assert dialog.preview.toPlainText() == before_text
    assert dialog._preview_reference == before_reference
    assert dialog._preview_key == before_key
    assert dialog.import_button.isEnabled()
    if stale:
        facade.changed = "source_sha256"
    dialog.import_button.click()
    assert len(facade.reference_calls) == 2
    assert facade.reference_calls[-1][2] == "a" * 64
    assert (dialog.result() == QDialog.DialogCode.Accepted) is not stale
    assert (dialog.reference is None) is stale
    dialog.close()


def test_body_navigation_is_disabled_without_a_word_marker(qt_app):
    dialog = ImportWordDialog(_Facade(), "internal-batch")
    assert not dialog.preview_body_button.isEnabled()
    dialog.preview_button.click()
    assert not dialog.preview_body_button.isEnabled()
    before = dialog.preview.toPlainText()
    dialog._locate_reference_body()
    assert dialog.preview.toPlainText() == before
    assert not dialog.preview_body_button.isEnabled()
    dialog.close()


def test_image_is_loaded_only_on_selection_and_narrow_dialog_hides_metadata(qt_app):
    facade = _Facade()
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.resize(420, 580)
    dialog.show()
    qt_app.processEvents()
    assert not facade.asset_calls
    visible_text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    visible_text += "\n".join(
        dialog.source_combo.itemText(i) for i in range(dialog.source_combo.count())
    )
    assert "internal-" not in visible_text
    assert "a" * 64 not in visible_text
    assert dialog.width() == 420
    dialog.asset_combo.setCurrentIndex(1)
    assert facade.asset_calls == [
        ("internal-batch", "internal-handout", "internal-image")
    ]
    assert not dialog.image_label.isHidden()
    dialog.asset_combo.setCurrentIndex(2)
    assert len(facade.asset_calls) == 1
    assert "暂不能" in dialog.status.text()
    dialog.close()


def _page():
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    bridge = DesktopTaskBridge()
    page = PreparationPage(SimpleNamespace(), bridge)
    page._availability_timer.stop()
    return page, bridge


def test_preparation_handoff_appends_only_materials_and_retains_warnings(
    qt_app, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    page, bridge = _page()
    page.topic.setText("原课题")
    page.audience.setText("高二")
    page.objective.setPlainText("原教学目标")
    page.materials.setPlainText("老师原有资料  \n保留空白")
    before = deepcopy(page._payload())
    reference = {
        "materials": "Word 所选内容",
        "warnings": ["公式待核验"],
        "topic": "不应改写课题",
    }
    assert page.import_word_reference(reference)
    after = page._payload()
    assert (
        after.pop("materials")
        == before.pop("materials")
        + "\n\nWord 所选内容\n\n原文缺口 / 待核对提醒：\n公式待核验"
    )
    assert after == before
    assert reference["materials"] == "Word 所选内容"
    assert not page.import_word_reference(reference)  # Duplicate remains atomic.
    page.close()
    bridge.shutdown(1000)


@pytest.mark.parametrize(
    "busy",
    [
        "_save_task_id",
        "_generation_qt_task_id",
        "_active_preparation_task_id",
        "_library_image_task_id",
    ],
)
def test_preparation_handoff_refuses_busy_state(qt_app, monkeypatch, busy):
    from PySide6.QtWidgets import QMessageBox

    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    page, bridge = _page()
    page.materials.setPlainText("已有内容")
    setattr(page, busy, "in-progress")
    assert not page.import_word_reference({"materials": "Word 新内容", "warnings": []})
    assert page.materials.toPlainText() == "已有内容"
    assert messages
    page.close()
    bridge.shutdown(1000)


@pytest.mark.parametrize(
    "reference",
    [
        None,
        "text",
        {},
        {"materials": []},
        {"materials": " "},
        {"materials": "正文", "warnings": "公式缺口"},
    ],
)
def test_preparation_handoff_rejects_malformed_reference_without_changes(
    qt_app, reference
):
    page, bridge = _page()
    before = page._payload()
    assert not page.import_word_reference(reference)
    assert page._payload() == before
    page.close()
    bridge.shutdown(1000)


def _receipt(filename="讲义.docx"):
    return DesktopVisualImportReceipt(
        batch_id="internal-batch",
        source_type="教师讲义",
        status="saved",
        visual_status="not_required",
        source_count=1,
        native_quick_count=1,
        visual_queue_count=0,
        sources=(
            DesktopVisualImportSourceSummary(
                "handout", 1, filename, "native_text_complete"
            ),
        ),
        message_zh="已保存",
    )


def test_import_receipt_word_action_and_child_handoff(qt_app, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog as child_module
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    calls = []
    reference = {"materials": "Word 资料", "warnings": []}

    class AcceptedDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, facade, batch_id, parent):
            calls.append((facade, batch_id, parent))
            self.reference = reference

        def exec(self):
            return self.DialogCode.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr(child_module, "ImportWordDialog", AcceptedDialog)
    facade, bridge = _Facade(), DesktopTaskBridge()
    dialog = ImportDialog(facade, bridge)
    assert dialog.word_reference_button.isHidden()
    dialog._visual_batch_saved(_receipt())
    assert not dialog.word_reference_button.isHidden()
    assert "Word 来源 1 份" in dialog.status.text()
    assert "候选 1 项" not in dialog.status.text()
    dialog._set_busy(True)
    assert not dialog.word_reference_button.isEnabled()
    dialog._set_busy(False)
    dialog.word_reference_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.preparation_reference == reference
    assert calls == [(facade, "internal-batch", dialog)]
    dialog.close()
    bridge.shutdown(1000)


@pytest.mark.parametrize(
    "accepted,appended", [(True, True), (True, False), (False, True)]
)
def test_main_window_navigates_only_after_accepted_successful_append(
    qt_app, monkeypatch, accepted, appended
):
    import integrations.deeptutor_shchem_v1.desktop_workbench.main_window as main_module

    references, routes = [], []
    reference = {"materials": "Word 资料", "warnings": []}

    class ImportStub:
        DialogCode = QDialog.DialogCode

        def __init__(self, *_args):
            self.preparation_reference = reference

        def exec(self):
            return self.DialogCode.Accepted if accepted else self.DialogCode.Rejected

        def deleteLater(self):
            pass

    monkeypatch.setattr(main_module, "ImportDialog", ImportStub)

    def append(value):
        references.append(value)
        return appended

    shell = SimpleNamespace(
        facade=object(),
        tasks=object(),
        preparation_page=SimpleNamespace(import_word_reference=append),
        navigate=routes.append,
    )
    main_module.TeacherWorkbenchWindow.open_import(shell)
    assert references == ([reference] if accepted else [])
    assert routes == (["preparation"] if accepted and appended else [])


def test_completed_word_batch_is_available_after_reopening_import(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    facade, bridge = _Facade(), DesktopTaskBridge()
    facade.list_imported_word_batches = lambda: (_receipt(),)
    dialog = ImportDialog(facade, bridge)
    assert not dialog.word_history_card.isHidden()
    assert dialog.word_batch_combo.count() == 1
    assert "讲义.docx" in dialog.word_batch_combo.currentText()
    assert "1 份 Word" in dialog.word_batch_combo.currentText()
    assert "internal-batch" not in dialog.word_batch_combo.currentText()
    assert dialog.word_batch_combo.currentData() == "internal-batch"
    assert dialog._saved_visual_receipt is None
    assert dialog.resume_card.isHidden()
    dialog.close()
    bridge.shutdown(1000)


def test_whole_lesson_reader_is_complete_without_reference_size_limit(qt_app):
    facade = _Facade()
    original_preview = facade.imported_word_preview

    def long_preview(*args):
        value = original_preview(*args)
        value["blocks"][0]["text"] = "原教案正文" * 5000
        value["blocks"][1]["text"] = "最后一个表格：完整末尾"
        return value

    facade.imported_word_preview = long_preview
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.whole_document_button.click()
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 2)
    assert "原教案正文" * 5000 in dialog.source_preview.toPlainText()
    assert "最后一个表格：完整末尾" in dialog.source_preview.toPlainText()
    assert not facade.reference_calls
    assert not dialog.import_button.isEnabled()
    assert "未按备课字数限制截断" in dialog.status.text()

    class TooLongReference(ValueError):
        message_zh = "所选参考超过 20,000 字，请缩小区块范围。"

    def reject_large_reference(*_args, **_kwargs):
        raise TooLongReference()

    facade.imported_word_reference = reject_large_reference
    before = dialog.source_preview.toPlainText()
    dialog.preview_button.click()
    assert dialog.source_preview.toPlainText() == before
    assert "20,000" in dialog.status.text()
    assert not dialog.import_button.isEnabled()
    assert dialog.reference is None
    dialog.close()


def test_whole_lesson_selection_invalidates_previously_compiled_reference(qt_app):
    facade = _Facade()
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.preview_button.click()
    assert dialog.import_button.isEnabled()
    dialog.whole_document_button.click()
    assert not dialog.import_button.isEnabled()
    assert not dialog.preview.toPlainText()
    assert len(facade.reference_calls) == 1
    dialog._confirm()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.close()


@pytest.mark.parametrize("width", [400, 760, 1200])
def test_whole_lesson_actions_fit_narrow_reader(qt_app, width):
    dialog = ImportWordDialog(_Facade(), "internal-batch")
    dialog.resize(width, 800)
    dialog.show()
    qt_app.processEvents()
    assert dialog.width() == width
    assert dialog.whole_document_button.width() <= width
    assert dialog.open_word_button.width() <= width
    assert dialog.zoom_image_button.width() <= width
    assert "完整教案原文与图片" == dialog.windowTitle()
    assert "知识摘要不替代原文" in "\n".join(
        label.text() for label in dialog.findChildren(QLabel)
    )
    dialog.close()


def test_full_size_image_is_scrollable_and_zoom_does_not_mutate_source(qt_app):
    from PySide6.QtGui import QPixmap

    parent = ImportWordDialog(_Facade(), "internal-batch")
    pixmap = QPixmap(1600, 1100)
    pixmap.fill(0xFFCCDDEE)
    dialog = _WordImageDialog(pixmap, "演示原图", parent)
    dialog.resize(400, 540)
    dialog.show()
    qt_app.processEvents()
    assert dialog.zoom.currentData() == 1.0
    assert dialog.image.pixmap().width() == 1600
    assert dialog.image.pixmap().height() == 1100
    assert dialog.scroll.horizontalScrollBar().maximum() > 0
    assert dialog.scroll.verticalScrollBar().maximum() > 0
    dialog.zoom.setCurrentIndex(1)  # 50%
    assert dialog.image.pixmap().width() == 800
    dialog.zoom.setCurrentIndex(0)  # fit width
    qt_app.processEvents()
    assert dialog.image.pixmap().width() <= dialog.scroll.viewport().width()
    assert pixmap.width() == 1600 and dialog._original.width() == 1600
    dialog.close()
    parent.close()


def test_image_zoom_requires_current_image_and_clears_on_failure(qt_app, monkeypatch):
    facade = _Facade()
    dialog = ImportWordDialog(facade, "internal-batch")
    calls = []
    monkeypatch.setattr(
        _WordImageDialog, "exec", lambda self: calls.append(self._original.size())
    )
    assert not dialog.zoom_image_button.isEnabled()
    dialog.asset_combo.setCurrentIndex(1)
    dialog.zoom_image_button.click()
    assert len(calls) == 1
    dialog.asset_combo.setCurrentIndex(2)
    assert dialog._image_pixmap is None
    assert not dialog.zoom_image_button.isEnabled()
    assert "用 Word 打开完整原文件" in dialog.status.text()
    facade.imported_word_asset = lambda *_args: {
        "bytes": b"broken",
        "mime_type": "image/png",
    }
    dialog.asset_combo.setCurrentIndex(1)
    assert dialog._image_pixmap is None
    assert not dialog.zoom_image_button.isEnabled()
    assert dialog.image_label.isHidden()
    dialog._open_image()
    assert len(calls) == 1
    dialog.close()


def test_open_original_word_is_user_initiated_and_uses_current_verified_source(
    qt_app, monkeypatch, tmp_path
):
    import integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog as module

    facade = _Facade()
    original = tmp_path / "已归档 原教案.docx"
    original.write_bytes(b"synthetic file; facade owns document validation")
    paths, urls = [], []

    def verified_path(batch_id, source_id):
        paths.append((batch_id, source_id))
        return str(original.resolve())

    facade.imported_word_path = verified_path
    monkeypatch.setattr(
        module.QDesktopServices, "openUrl", lambda url: urls.append(url) or True
    )
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.whole_document_button.click()
    assert not paths and not urls
    dialog.open_word_button.click()
    assert paths == [("internal-batch", "internal-handout")]
    assert len(urls) == 1 and urls[0].isLocalFile()
    assert urls[0].toLocalFile().replace("\\", "/") == str(original.resolve()).replace(
        "\\", "/"
    )
    dialog.source_combo.setCurrentIndex(1)
    assert len(paths) == 1
    dialog.open_word_button.click()
    assert paths[-1] == ("internal-batch", "internal-answer")
    assert "已请求" in dialog.status.text()
    assert not facade.reference_calls
    dialog.close()


@pytest.mark.parametrize("failure", ["facade", "relative", "missing", "scheme", "open"])
def test_open_original_word_failure_is_safe_and_keeps_full_reader(
    qt_app, monkeypatch, tmp_path, failure
):
    import integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog as module

    facade = _Facade()
    original = tmp_path / "demo.docx"
    original.write_bytes(b"synthetic fixture")
    urls = []

    def verified_path(*_args):
        if failure == "facade":
            raise OSError("C:/private/secret-cache.docx")
        return {
            "relative": "demo.docx",
            "missing": str(tmp_path / "missing.docx"),
            "scheme": "https://example.invalid/demo.docx",
        }.get(failure, str(original.resolve()))

    facade.imported_word_path = verified_path
    monkeypatch.setattr(
        module.QDesktopServices, "openUrl", lambda url: urls.append(url) or False
    )
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.whole_document_button.click()
    before = dialog.source_preview.toPlainText()
    dialog.open_word_button.click()
    assert "无法打开" in dialog.status.text()
    assert "private" not in dialog.status.text()
    assert dialog.source_preview.toPlainText() == before
    assert len(urls) == (1 if failure == "open" else 0)
    assert dialog.reference is None
    dialog.close()


def test_source_load_failure_clears_previous_full_text_and_image(qt_app):
    facade = _Facade()
    dialog = ImportWordDialog(facade, "internal-batch")
    dialog.whole_document_button.click()
    dialog.asset_combo.setCurrentIndex(1)
    assert dialog._image_pixmap is not None

    def fail(*_args):
        raise OSError("private/internal.docx")

    facade.imported_word_preview = fail
    dialog.source_combo.setCurrentIndex(1)
    assert not dialog.source_preview.toPlainText()
    assert dialog._source is None and dialog._image_pixmap is None
    assert dialog.image_label.isHidden()
    assert not dialog.zoom_image_button.isEnabled()
    assert not dialog.whole_document_button.isEnabled()
    assert dialog.open_word_button.isEnabled()  # Original-file fallback remains.
    assert "private" not in dialog.status.text()
    dialog.close()

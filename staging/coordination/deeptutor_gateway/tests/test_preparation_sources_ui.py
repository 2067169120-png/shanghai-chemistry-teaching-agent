from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import (
    PreparationSourcesDialog,
)


@pytest.fixture
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class _SourceFacade:
    def __init__(self) -> None:
        self.word_calls: list[str] = []
        self.reference_calls: list[
            tuple[object, object, int, int, list[dict[str, str]]]
        ] = []
        self.compile_count = 0
        self.changed_on_recompile = False

    def preparation_word_preview(self, path: str) -> dict[str, object]:
        self.word_calls.append(path)
        return {
            "source_name": Path(path).name,
            "source_sha256": "f" * 64,
            "blocks": [
                {
                    "index": 1,
                    "label": "段落 1",
                    "text": "电解质是在水溶液中或熔融状态下能导电的化合物。",
                    "warnings": [],
                },
                {
                    "index": 2,
                    "label": "表格 1",
                    "text": "强电解质与弱电解质的比较。",
                    "warnings": ["表格对象可能不是完整原文"],
                },
            ],
        }

    def preparation_concept_options(self, query: str = "") -> list[dict[str, str]]:
        values = [
            {
                "concept_id": "C06",
                "title": "电解质",
                "statement": "水溶液中或熔融状态下能导电的化合物。",
                "label": "C06 · 电解质",
                "revision": "r1",
            },
            {
                "concept_id": "C07",
                "title": "强弱电解质",
                "statement": "强电解质在水溶液中完全电离，弱电解质部分电离。",
                "label": "C07 · 强弱电解质",
                "revision": "r2",
            },
        ]
        query = query.strip()
        return [value for value in values if not query or query in value["label"]]

    def preparation_source_reference(
        self,
        word_path: str | None,
        word_sha256: str | None,
        block_start: int,
        block_end: int,
        concepts: list[dict[str, str]],
    ) -> dict[str, object]:
        self.reference_calls.append(
            (word_path, word_sha256, block_start, block_end, list(concepts))
        )
        self.compile_count += 1
        suffix = (
            "（重新编译发生变化）"
            if self.changed_on_recompile and self.compile_count > 1
            else ""
        )
        return {
            "materials": (
                f"Word区块 {block_start}-{block_end}；"
                f"教材概念：{','.join(item['concept_id'] for item in concepts)}{suffix}"
            ),
            "warnings": ["候选知识点仍需教师核验"],
        }


def _check_concept(dialog: PreparationSourcesDialog, row: int = 0) -> None:
    item = dialog.concept_list.item(row)
    item.setCheckState(Qt.CheckState.Checked)


def test_word_preview_defaults_to_first_block_and_concepts_can_be_selected(
    qt_app, tmp_path
):
    facade = _SourceFacade()
    dialog = PreparationSourcesDialog(facade)
    dialog.show()
    qt_app.processEvents()

    source = tmp_path / "第04讲.docx"
    source.write_bytes(b"fixture")
    dialog._load_word(str(source))
    assert facade.word_calls == [str(source)]
    assert dialog.block_list.count() == 2
    assert dialog.block_start.value() == 1
    assert dialog.block_end.value() == 1
    assert "段落 1" in dialog.block_list.item(0).text()
    assert "表格 1" in dialog.block_list.item(1).text()

    _check_concept(dialog)
    assert dialog.selected_concepts == [{"concept_id": "C06", "revision": "r1"}]
    assert dialog.preview_button.isEnabled()
    dialog.close()


def test_section_picker_selects_full_range_and_browsing_does_not_shrink_it(
    qt_app, tmp_path
):
    facade = _SourceFacade()
    original = facade.preparation_word_preview

    def with_sections(path):
        result = original(path)
        result["sections"] = [{"title": "考点一 电解质", "start": 1, "end": 2}]
        return result

    facade.preparation_word_preview = with_sections
    dialog = PreparationSourcesDialog(facade)
    dialog._load_word(str(tmp_path / "guide.docx"))
    assert dialog.section_picker.isEnabled()
    assert dialog.block_end.value() == 1  # No automatic scope expansion.
    dialog.section_picker.setCurrentIndex(1)
    dialog.section_picker.activated.emit(1)
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 2)
    dialog._compile_preview()
    assert facade.reference_calls[-1][2:4] == (1, 2)
    assert dialog.import_button.isEnabled()
    dialog.block_list.setCurrentRow(1)
    assert (dialog.block_start.value(), dialog.block_end.value()) == (1, 2)
    assert dialog.import_button.isEnabled()  # Browsing does not change the snapshot.
    dialog.single_block_button.click()
    assert (dialog.block_start.value(), dialog.block_end.value()) == (2, 2)
    assert dialog.section_picker.currentIndex() == 0
    assert not dialog.import_button.isEnabled()
    dialog.close()


def test_section_picker_ignores_invalid_ranges_and_clears_after_load_failure(
    qt_app, tmp_path
):
    facade = _SourceFacade()
    original = facade.preparation_word_preview

    def with_sections(path):
        result = original(path)
        result["sections"] = [
            {"title": "valid", "start": 1, "end": 2},
            {"title": "bool", "start": True, "end": 2},
            {"title": "outside", "start": 1, "end": 30},
            {"title": "reverse", "start": 2, "end": 1},
        ]
        return result

    facade.preparation_word_preview = with_sections
    dialog = PreparationSourcesDialog(facade)
    dialog._load_word(str(tmp_path / "guide.docx"))
    assert dialog.section_picker.count() == 2

    def fail(path):
        raise ValueError("read failed")

    facade.preparation_word_preview = fail
    dialog._load_word(str(tmp_path / "bad.docx"))
    assert dialog.section_picker.count() == 1
    assert not dialog.section_picker.isEnabled()
    assert not dialog.single_block_button.isEnabled()
    dialog.close()


def test_word_block_list_uses_service_label_once_and_keeps_full_text_in_tooltip(
    qt_app, tmp_path
):
    facade = _SourceFacade()
    original = facade.preparation_word_preview

    def labelled_preview(path: str) -> dict[str, object]:
        value = original(path)
        value["blocks"][0]["label"] = "42 · 电解质定义（服务摘要）"
        value["blocks"][0]["text"] = "这是完整的区块原文，不应再次拼到可读列表标签中。"
        return value

    facade.preparation_word_preview = labelled_preview  # type: ignore[method-assign]
    dialog = PreparationSourcesDialog(facade)
    dialog.show()
    qt_app.processEvents()
    dialog._load_word(str(tmp_path / "guide.docx"))
    item = dialog.block_list.item(0)
    assert item is not None
    assert item.text() == "42 · 电解质定义（服务摘要）"
    assert item.toolTip() == "这是完整的区块原文，不应再次拼到可读列表标签中。"
    assert "42 · 电解质定义（服务摘要）：" not in item.text()
    dialog.close()


def test_invalid_word_range_is_not_silently_downgraded_to_concept_only(
    qt_app, tmp_path
):
    facade = _SourceFacade()
    dialog = PreparationSourcesDialog(facade)
    dialog.show()
    qt_app.processEvents()
    dialog._load_word(str(tmp_path / "guide.docx"))
    _check_concept(dialog)
    dialog.block_start.setValue(2)
    dialog.block_end.setValue(1)
    dialog.preview_button.click()
    assert facade.reference_calls == []
    assert "范围无效" in dialog.status.text()
    assert dialog.reference is None
    dialog.close()


def test_preview_status_reports_warning_count_without_repeating_full_warnings(
    qt_app, tmp_path
):
    facade = _SourceFacade()
    original = facade.preparation_source_reference

    def many_warnings(*args, **kwargs):
        value = original(*args, **kwargs)
        value["warnings"] = [f"详细缺口 {index}" for index in range(1, 10)]
        value["materials"] += "\n" + "\n".join(value["warnings"])
        return value

    facade.preparation_source_reference = many_warnings  # type: ignore[method-assign]
    dialog = PreparationSourcesDialog(facade)
    dialog.show()
    qt_app.processEvents()
    dialog._load_word(str(tmp_path / "guide.docx"))
    _check_concept(dialog)
    dialog.preview_button.click()
    assert "包含 9 条" in dialog.status.text()
    assert "详细缺口 1" not in dialog.status.text()
    assert "详细缺口 1" in dialog.preview.toPlainText()
    dialog.close()


def test_preview_invalidates_on_selection_and_confirm_rechecks_same_reference(
    qt_app, tmp_path
):
    facade = _SourceFacade()
    dialog = PreparationSourcesDialog(facade)
    dialog.show()
    qt_app.processEvents()
    dialog._load_word(str(tmp_path / "guide.docx"))
    _check_concept(dialog)

    dialog.preview_button.click()
    assert dialog.reference is not None
    assert dialog.import_button.isEnabled()
    assert facade.compile_count == 1

    dialog.block_end.setValue(2)
    assert dialog.reference is None
    assert not dialog.import_button.isEnabled()

    dialog.preview_button.click()
    assert facade.compile_count == 2
    assert dialog.reference is not None
    dialog.import_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    # One preview compile plus one exact recompile before accepting.
    assert facade.compile_count == 3
    dialog.close()


def test_changed_reference_cannot_be_accepted_and_word_is_optional(qt_app):
    facade = _SourceFacade()
    facade.changed_on_recompile = True
    dialog = PreparationSourcesDialog(facade)
    dialog.show()
    qt_app.processEvents()
    _check_concept(dialog)
    # No Word is selected: concept-only import is still available.
    assert dialog.preview_button.isEnabled()
    dialog.preview_button.click()
    assert dialog.reference is not None
    dialog.import_button.click()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "已变化" in dialog.status.text()
    assert not dialog.import_button.isEnabled()
    dialog.close()


def test_preparation_page_source_button_appends_only_materials(qt_app, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    class _PageFacade(_SourceFacade):
        def create_preparation_draft(self, payload):
            return SimpleNamespace(message_zh="已保存")

        def preparation_availability(self):
            return SimpleNamespace(
                provider_ready=False,
                renderer_ready=False,
                message_zh="仅离线保存",
            )

        def preparation_profiles(self):
            return ()

        def list_preparations(self, *, limit=3):
            return ()

    class _AcceptedDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, _facade, _parent=None):
            self.reference = {
                "materials": "[Word+教材参考]\n电解质概念与强弱电解质对照。",
                "warnings": ["请核验教材表述"],
            }

        def exec(self):
            return self.DialogCode.Accepted

    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog as dialog_module

    monkeypatch.setattr(dialog_module, "PreparationSourcesDialog", _AcceptedDialog)
    facade = _PageFacade()
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    original_topic = page.topic.text()
    original_objective = page.objective.toPlainText()
    page.source_import_button.click()
    assert "Word+教材参考" in page.materials.toPlainText()
    assert page.topic.text() == original_topic
    assert page.objective.toPlainText() == original_objective
    assert "包含 1 条原文缺口/待核对警告" in page.status.text()
    page.close()
    bridge.shutdown(1000)

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QDialog


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _png_bytes(color: str, *, width: int = 18, height: int = 12) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


class _ImmediateTasks:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cancelled: list[str] = []
        self.serial = 0

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        self.serial += 1
        task_id = f"task-{self.serial}"
        self.calls.append(label)
        try:
            value = operation()
        except Exception as exc:  # noqa: BLE001 - deliberately exercises UI failure state
            if on_failure is not None:
                on_failure(str(exc))
        else:
            if on_success is not None:
                on_success(value)
        return task_id

    def cancel(self, task_id: str) -> None:
        self.cancelled.append(task_id)


def _row(
    batch_id: str,
    key: str,
    revision: str,
    *,
    source: str,
    book: str,
    knowledge: tuple[str, ...],
    color: str,
    image_failure: bool = False,
) -> dict[str, Any]:
    question_image = f"{key}-question"
    shared_image = f"{key}-shared"
    answer_image = f"{key}-answer"
    return {
        "batch_id": batch_id,
        "key": key,
        "revision": revision,
        "title": f"{key}完整主题",
        "theme_key": f"theme-{key}",
        "theme_title": f"主题 {key}",
        "source_name": source,
        "question_number": key,
        "question_text": f"题面 {key}：完整题目文字。",
        "shared_text": f"公共材料 {key}：完整材料文字。",
        "answer_text": f"答案 {key}：来源答案文字。",
        "warnings": ["原文提醒"] if image_failure else [],
        "images": [
            {
                "image_id": question_image,
                "role": "question",
                "caption": f"题图 {key}",
                "width": 18,
                "height": 12,
                "page_number": 2,
            },
            {
                "image_id": shared_image,
                "role": "shared_material",
                "caption": f"公共图 {key}",
                "width": 18,
                "height": 12,
                "page_number": 2,
            },
            {
                "image_id": answer_image,
                "role": "answer",
                "caption": f"答案图 {key}",
                "width": 18,
                "height": 12,
                "page_number": 3,
            },
        ],
        "facets": {
            "source": [source],
            "book": [book],
            "knowledge": list(knowledge),
        },
        "curriculum_paths": [{"volume_id": book, "chapter_id": "合成章", "section_key": "unknown"}],
        "selection_ready": True,
        "_image_failure": image_failure,
        "_color": color,
    }


class _Facade:
    def __init__(self, *, catalog_failure: bool = False, image_failure: bool = False) -> None:
        self.catalog_failure = catalog_failure
        self.image_failure = image_failure
        self.rows = [
            _row(
                "batch-a",
                "q-a",
                "rev-a",
                source="来源甲",
                book="必修一",
                knowledge=("k-redox", "k-balance"),
                color="#e64b3c",
                image_failure=image_failure,
            ),
            _row(
                "batch-b",
                "q-b",
                "rev-b",
                source="来源乙",
                book="必修二",
                knowledge=("k-redox",),
                color="#3478db",
            ),
            _row(
                "batch-a",
                "q-c",
                "rev-c",
                source="来源丙",
                book="必修一",
                knowledge=("k-organic",),
                color="#38a169",
            ),
        ]
        self.image_calls: list[tuple[str, str, str, str, bool]] = []
        self.saved: list[list[dict[str, Any]]] = []
        self.reference_calls: list[list[dict[str, Any]]] = []

    def personal_visual_questions(self, batch_id=None):
        if self.catalog_failure:
            raise OSError("private catalog path")
        rows = [row for row in self.rows if batch_id is None or row["batch_id"] == batch_id]
        return {
            "items": deepcopy(rows),
            "warnings": [],
            "filter_options": {
                "book": [
                    {"value": "必修一", "label": "必修一"},
                    {"value": "必修二", "label": "必修二"},
                ],
                "knowledge": [
                    {"value": "k-balance", "label": "守恒"},
                    {"value": "k-organic", "label": "有机"},
                    {"value": "k-redox", "label": "氧化还原"},
                ],
                "source": [
                    {"value": "来源甲", "label": "来源甲"},
                    {"value": "来源乙", "label": "来源乙"},
                    {"value": "来源丙", "label": "来源丙"},
                ],
            },
            "selection": [],
        }

    def _find(self, batch_id: str, key: str, revision: str) -> dict[str, Any]:
        for row in self.rows:
            if (row["batch_id"], row["key"], row["revision"]) == (batch_id, key, revision):
                return row
        raise KeyError(key)

    def personal_visual_question_detail(self, batch_id: str, key: str, revision: str):
        return deepcopy(self._find(batch_id, key, revision))

    def personal_visual_question_image(
        self, batch_id: str, key: str, revision: str, image_id: str, original: bool = False
    ):
        self.image_calls.append((batch_id, key, revision, image_id, original))
        row = self._find(batch_id, key, revision)
        if self.image_failure and row.get("_image_failure"):
            raise OSError("image bytes unavailable")
        color = row["_color"]
        if image_id.endswith("-shared"):
            color = "#f0a202"
        elif image_id.endswith("-answer"):
            color = "#805ad5"
        return {"bytes": _png_bytes(color), "caption": image_id}

    def save_personal_visual_selection(self, selections):
        self.saved.append(deepcopy(selections))

    def personal_visual_question_reference(self, selections):
        self.reference_calls.append(deepcopy(selections))
        return {
            "materials": "完整主题材料\n全部小题\n公共材料和来源图片引用。",
            "image_count": 2,
            "warnings": ["图片数量按实际引用统计。"],
        }


def _item(dialog, key: str):
    for index in range(dialog.question_list.count()):
        item = dialog.question_list.item(index)
        token = item.data(256)
        if isinstance(token, (tuple, list)) and token[1] == key:
            return item
    raise AssertionError(f"question {key!r} not found")


def test_batch_and_cross_group_and_filtering_keep_selection_scope(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
        PersonalVisualQuestionDialog,
    )

    facade = _Facade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    assert dialog.question_list.count() == 3

    batch_index = dialog.batch_combo.findData("batch-b")
    assert batch_index >= 0
    dialog.batch_combo.setCurrentIndex(batch_index)
    assert dialog.question_list.count() == 1
    assert _item(dialog, "q-b").isSelected() or dialog.question_list.currentItem() is not None

    dialog.batch_combo.setCurrentIndex(0)
    dialog.filter_panel.set_selection({"source": {"来源甲"}, "book": {"必修一"}})
    assert dialog.question_list.count() == 1
    assert dialog.question_list.item(0).data(256)[1] == "q-a"
    dialog.filter_panel.set_selection({"source": {"来源甲"}, "book": {"必修二"}})
    assert dialog.question_list.count() == 0
    dialog.reject()


def test_curriculum_options_keep_parents_and_dialog_rejects_cross_path(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_word_question_filters import (
        chapter_filter_id,
        section_filter_id,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
        PersonalVisualQuestionDialog,
    )

    class CurriculumFacade(_Facade):
        def personal_visual_questions(self, batch_id=None):
            result = super().personal_visual_questions(batch_id)
            result["filter_options"]["chapter"] = [
                {"value": chapter_filter_id(book, "合成章"), "label": book + " / 合成章",
                 "volume_id": book, "chapter_id": "合成章"}
                for book in ("必修一", "必修二")
            ]
            result["filter_options"]["section"] = [
                {"value": section_filter_id(book, "合成章", book + "-节"), "label": book + " / 合成节",
                 "volume_id": book, "chapter_id": "合成章", "section_key": book + "-节"}
                for book in ("必修一", "必修二")
            ]
            return result

    facade = CurriculumFacade()
    for row in facade.rows:
        path = row["curriculum_paths"][0]
        path["section_key"] = path["volume_id"] + "-节"
    facade.rows[0]["curriculum_paths"].append(deepcopy(facade.rows[1]["curriculum_paths"][0]))
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    dialog.filter_panel.set_selection({"book": {"必修一"}})
    assert [row["id"] for row in dialog.filter_panel._available("chapter")] == [
        chapter_filter_id("必修一", "合成章")
    ]
    assert [row["id"] for row in dialog.filter_panel._available("section")] == [
        section_filter_id("必修一", "合成章", "必修一-节")
    ]
    dialog.filter_panel.set_selection({
        "book": {"必修一", "必修二"},
        "chapter": {chapter_filter_id("必修二", "合成章")},
        "section": {section_filter_id("必修二", "合成章", "必修二-节")},
    })
    assert dialog.question_list.count() == 2
    assert not dialog._matches_filters(facade.rows[0], {
        "book": {"必修一"}, "chapter": {chapter_filter_id("必修二", "合成章")}
    })
    dialog.reject()


def test_legacy_facet_only_ui_still_browses_and_filters_non_curriculum(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
        PersonalVisualQuestionDialog,
    )

    facade = _Facade()
    for row in facade.rows:
        row.pop("curriculum_paths")
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    assert dialog.question_list.count() == 3
    dialog.filter_panel.set_selection({"source": {"来源甲"}, "knowledge": {"k-redox"}})
    assert dialog.question_list.count() == 1
    dialog.filter_panel.set_selection({"book": {"必修一"}})
    assert dialog.question_list.count() == 0
    dialog.reject()


def test_detail_loads_real_question_and_shared_pixels_but_isolates_answer_images(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
        PersonalVisualQuestionDialog,
    )

    facade = _Facade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    token = ("batch-a", "q-a", "rev-a")
    question_preview = dialog._image_previews[(token, "q-a-question", "question")]
    shared_preview = dialog._image_previews[(token, "q-a-shared", "shared_material")]
    assert question_preview.has_image and shared_preview.has_image
    assert facade.image_calls
    assert not any(call[3] == "q-a-answer" for call in facade.image_calls)
    assert question_preview.image.pixmap().toImage().pixelColor(2, 2) == QColor("#e64b3c")

    dialog.tabs.setCurrentIndex(2)
    assert any(call[3] == "q-a-answer" and not call[4] for call in facade.image_calls)
    answer_preview = dialog._image_previews[(token, "q-a-answer", "answer")]
    assert answer_preview.has_image
    assert answer_preview.image.pixmap().toImage().pixelColor(2, 2) == QColor("#805ad5")
    dialog.reject()


def test_switching_questions_restores_cached_pixels_and_original_has_crop_return(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
        PersonalVisualQuestionDialog,
    )

    facade = _Facade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    dialog.question_list.setCurrentRow(1)
    assert dialog._current_token == ("batch-b", "q-b", "rev-b")
    dialog.question_list.setCurrentRow(0)
    token = ("batch-a", "q-a", "rev-a")
    preview = dialog._image_previews[(token, "q-a-question", "question")]
    assert preview.has_image
    assert facade.image_calls.count(("batch-a", "q-a", "rev-a", "q-a-question", False)) == 2

    original_button = dialog._image_original_buttons[(token, "q-a-question", "question")]
    crop_button = dialog._image_crop_buttons[(token, "q-a-question", "question")]
    original_button.click()
    assert facade.image_calls[-1] == ("batch-a", "q-a", "rev-a", "q-a-question", True)
    assert dialog._is_selectable(token)
    assert crop_button.isEnabled()
    crop_button.click()
    assert dialog._is_selectable(token)
    assert preview.has_image
    dialog.reject()


def test_cancel_does_not_save_but_explicit_save_and_preparation_accept_work(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
        PersonalVisualQuestionDialog,
    )

    facade = _Facade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    _item(dialog, "q-a").setCheckState(Qt.CheckState.Checked)
    assert dialog.selections[0]["key"] == "q-a"
    dialog.reject()
    assert facade.saved == []

    facade = _Facade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    _item(dialog, "q-a").setCheckState(Qt.CheckState.Checked)
    dialog.save_button.click()
    assert facade.saved == [[{
        "batch_id": "batch-a",
        "key": "q-a",
        "revision": "rev-a",
        "points": 2,
    }]]
    dialog.preview_reference_button.click()
    assert facade.reference_calls == [facade.saved[0]]
    assert "完整主题材料" in dialog.reference_materials.toPlainText()
    assert "2 张图片" in dialog.reference_image_note.text()
    assert dialog.accept_preparation_button.isEnabled()
    dialog.accept_preparation_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.preparation_reference is not None
    assert "全部小题" in dialog.preparation_reference["materials"]


def test_explicit_empty_save_clears_previous_selection(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
        PersonalVisualQuestionDialog,
    )

    facade = _Facade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    assert facade.saved == [[]]
    assert dialog.saved
    assert "清空" in dialog.status.text()
    dialog.reject()


def test_catalog_error_and_required_image_error_fail_closed(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
        PersonalVisualQuestionDialog,
    )

    broken = PersonalVisualQuestionDialog(_Facade(catalog_failure=True), _ImmediateTasks())
    assert broken.question_list.count() == 0
    assert broken.status.objectName() == "StatusError"
    broken.reject()

    facade = _Facade(image_failure=True)
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    token = ("batch-a", "q-a", "rev-a")
    assert token in dialog._image_failures
    assert dialog.status.objectName() == "StatusAttention"
    item = _item(dialog, "q-a")
    item.setCheckState(Qt.CheckState.Checked)
    assert item.checkState() == Qt.CheckState.Unchecked
    assert not dialog.selections
    dialog.reject()

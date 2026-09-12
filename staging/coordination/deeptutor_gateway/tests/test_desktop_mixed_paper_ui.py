"""Synthetic mixed basket UI; no application, provider or real source is opened."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from test_word_question_dialog import _Facade as WordFacade
from test_word_question_dialog import _isolated_qt_app
from test_word_question_dialog import _Tasks as Tasks

from integrations.deeptutor_shchem_v1.desktop_facade import PaperPreview
from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import (
    MixedPaperPanel,
    MixedPaperPreviewDialog,
    PaperPage,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
    MixedPaperComposerModel,
    PaperComposerModel,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import (
    WordQuestionDialog,
)


def png_bytes():
    image = QImage(240, 100, QImage.Format.Format_RGB32)
    image.fill(0xFFE4F3EF)
    painter = QPainter(image)
    painter.setPen(Qt.GlobalColor.darkGray)
    painter.drawRect(30, 25, 180, 50)
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "SYNTHETIC IMAGE")
    painter.end()
    raw = QByteArray()
    buffer = QBuffer(raw)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(raw)


class FakeStore:
    def __init__(self):
        self.data = {"drafts": {}}

    def snapshot(self):
        return deepcopy(self.data)

    def save_draft(self, key, value):
        self.data["drafts"][key] = deepcopy(value)


class Facade:
    def __init__(self):
        self.rows = [
            {
                "key": "core-a",
                "scope": "master",
                "source_identity_sha256": "core-a",
                "title_zh": "合成原卷主题",
                "atomic_total": 1,
            },
            {
                "key": "word-a",
                "item_kind": "word_question",
                "title_zh": "合成 Word 完整题",
            },
        ]
        self.state_store = FakeStore()
        self.calls = []
        self.image_failure = False
        self.preview_failure = False
        self.approval_failure = False
        self.export_failure = False

    def basket(self):
        return tuple(deepcopy(self.rows))

    def paper_basket_projection(self):
        return {
            "schema_version": "shchem.desktop-mixed-basket.v1",
            "basket_sha256": hashlib.sha256(
                json.dumps(self.rows, sort_keys=True).encode()
            ).hexdigest(),
            "items": [
                {
                    "kind": "word_question"
                    if row.get("item_kind") == "word_question"
                    else "core_theme",
                    "key": row["key"],
                    "title_zh": row["title_zh"],
                    "source_zh": "合成资料 · 非真实题目",
                    "source_ref": {"revision": "synthetic-r1"},
                    "content": {},
                    "settings": {"points": 2}
                    if row.get("item_kind") == "word_question"
                    else {
                        "score_per_atomic": 2,
                        "answer_space_lines": 0,
                        "atomic_settings": {},
                    },
                }
                for row in self.rows
            ],
            "warnings": [],
        }

    def create_paper_preview(self, payload):
        self.calls.append(("preview", deepcopy(payload)))
        if self.preview_failure:
            raise RuntimeError("synthetic preview error")
        sections = []
        for key in payload["section_order"]:
            row = next(row for row in self.rows if row["key"] == key)
            settings = payload["settings_by_key"][key]
            points = settings.get("points", settings.get("score_per_atomic", 2))
            blocks = [
                {
                    "kind": "text",
                    "text": "合成共同材料：观察电子转移。\n合成题面：判断氧化剂，答题区________。",
                },
                {
                    "kind": "image",
                    "image_id": key + "-picture",
                    "caption_zh": "合成示意图占位，非原题",
                },
            ]
            sections.append(
                {
                    "kind": "word_question" if row.get("item_kind") else "core_theme",
                    "key": key,
                    "title_zh": row["title_zh"],
                    "source_zh": "合成资料",
                    "student_blocks": blocks,
                    "teacher_blocks": blocks
                    + [
                        {
                            "kind": "text",
                            "text": f"参考答案：得到电子的物质。本次练习评分：{points:g} 分。",
                        }
                    ],
                    "points": points,
                }
            )
        model = {
            "schema_version": "shchem.desktop-mixed-paper-preview.v1",
            "sections": sections,
            **{key: payload[key] for key in ("title", "show_question_scores")},
        }
        return PaperPreview(
            "synthetic-preview",
            payload["title"],
            "练习",
            len(sections),
            (),
            False,
            (),
            model,
            "f" * 64,
        )

    def paper_preview_image(self, preview_id, image_id):
        self.calls.append(("image", preview_id, image_id))
        if self.image_failure:
            raise RuntimeError("synthetic image error")
        raw = png_bytes()
        return {
            "data": raw,
            "content_type": "image/png",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    def approve_paper_preview(self, preview_id, preview_hash):
        if self.approval_failure:
            raise RuntimeError("synthetic approval error")
        self.calls.append(("approve", preview_id, preview_hash))
        return {"status": "approved"}

    def export_paper_preview(self, preview_id, preview_hash, draft):
        if self.export_failure:
            raise RuntimeError("synthetic export error")
        self.calls.append(("export", preview_id, preview_hash, deepcopy(draft)))
        return {
            "message_zh": "已生成两版 DOCX",
            "pdf_status": "not_generated",
            "artifacts": [
                {"artifact_id": "student_docx", "path": "synthetic-student.docx"},
                {"artifact_id": "teacher_docx", "path": "synthetic-teacher.docx"},
            ],
        }


@pytest.fixture
def qt_app():
    with _isolated_qt_app() as app:
        yield app


@pytest.fixture
def panel(qt_app):
    facade, tasks = Facade(), Tasks()
    widget = MixedPaperPanel(facade, tasks, PaperComposerModel())
    widget.load()
    tasks.flush()
    yield widget, facade, tasks
    widget._closed = True
    if widget._preview_dialog is not None:
        widget._preview_dialog.reject()
    widget.close()
    widget.deleteLater()
    qt_app.processEvents()


def confirm_preview(widget, tasks):
    widget.preview_button.click()
    tasks.flush()
    dialog = widget._preview_dialog
    assert dialog is not None
    assert not dialog.confirm_button.isEnabled()
    dialog.tabs.setCurrentIndex(1)
    assert dialog.confirm_button.isEnabled()
    dialog.confirm_button.click()
    tasks.flush()
    return dialog


def test_model_preserves_mixed_order_settings_and_excluded_when_appending():
    facade = Facade()
    model = MixedPaperComposerModel()
    model.merge(facade.paper_basket_projection())
    assert model.move("word-a", -1)
    model.settings["word-a"] = {"points": 4.5}
    model.remove("core-a")
    facade.rows.append({"key": "core-b", "scope": "wechat", "title_zh": "同名合成题"})
    model.merge(facade.paper_basket_projection())
    assert model.order == ["word-a", "core-b"]
    assert model.settings["word-a"] == {"points": 4.5}
    assert model.excluded == {"core-a"}
    model.restore_excluded()
    assert model.order == ["word-a", "core-b", "core-a"]


def test_ui_complete_preview_then_approval_then_docx(panel):
    widget, facade, tasks = panel
    assert not widget.export_button.isEnabled()
    dialog = confirm_preview(widget, tasks)
    labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert "合成共同材料" in labels and "参考答案：" in labels
    assert dialog._pixmaps and all(
        not picture.isNull() for picture in dialog._pixmaps.values()
    )
    assert widget.export_button.isEnabled()
    widget.export_button.click()
    tasks.flush()
    assert any(call[0] == "export" for call in facade.calls)
    assert "PDF 尚未生成" in widget.status.text()
    assert "四文件" not in widget.status.text()


def test_changed_word_points_match_frozen_preview_and_teacher_answer(panel):
    widget, _, tasks = panel
    widget.sections.setCurrentRow(1)
    widget.points.setValue(4)
    dialog = confirm_preview(widget, tasks)
    section = next(
        section
        for section in widget._preview.preview_model["sections"]
        if section["key"] == "word-a"
    )
    assert section["points"] == 4
    assert "本次练习评分：4 分" in "\n".join(
        block.get("text", "") for block in section["teacher_blocks"]
    )
    assert any(
        "本次练习分值：4" in label.text() for label in dialog.findChildren(QLabel)
    )


def test_edit_invalidates_preview_closes_dialog_and_keeps_default_extra_lines_zero(
    panel,
):
    widget, _, tasks = panel
    dialog = confirm_preview(widget, tasks)
    widget.show_scores.setChecked(True)
    assert not widget.export_button.isEnabled()
    assert dialog._closed
    assert widget.request()["show_question_scores"] is True
    assert widget.model.settings["core-a"]["answer_space_lines"] == 0


@pytest.mark.parametrize(
    "failure", ["image_failure", "preview_failure", "approval_failure"]
)
def test_failed_content_or_approval_never_unlocks_export(panel, failure):
    widget, facade, tasks = panel
    setattr(facade, failure, True)
    widget.preview_button.click()
    tasks.flush()
    if widget._preview_dialog is not None:
        widget._preview_dialog.tabs.setCurrentIndex(1)
        widget._preview_dialog.confirm_button.click()
        tasks.flush()
    assert not widget.export_button.isEnabled()


def test_stale_preview_callback_is_not_shown_after_edit(panel):
    widget, _, tasks = panel
    widget.preview_button.click()
    widget.title.setText("新的合成标题")
    tasks.flush()
    assert widget._preview_dialog is None
    assert not widget._busy


def test_projection_refresh_keeps_removed_and_reordered_sections(panel):
    widget, facade, tasks = panel
    widget.sections.setCurrentRow(1)
    widget.up_button.click()
    widget.points.setValue(5)
    widget.sections.setCurrentRow(1)
    widget.remove_button.click()
    facade.rows.append(
        {"key": "word-b", "item_kind": "word_question", "title_zh": "另一来源合成题"}
    )
    widget.load()
    tasks.flush()
    assert widget.model.order == ["word-a", "word-b"]
    assert widget.model.settings["word-a"]["points"] == 5
    assert widget.model.excluded == {"core-a"}


def test_mixed_draft_restores_without_deleting_legacy_draft(panel, qt_app):
    widget, facade, tasks = panel
    facade.state_store.save_draft("paper-current", {"payload": {"legacy": "untouched"}})
    widget.sections.setCurrentRow(1)
    widget.up_button.click()
    widget.points.setValue(3.5)
    other = MixedPaperPanel(facade, tasks, PaperComposerModel())
    other.load()
    tasks.flush()
    assert other.model.order == ["word-a", "core-a"]
    assert other.model.settings["word-a"]["points"] == 3.5
    assert facade.state_store.snapshot()["drafts"]["paper-current"]["payload"] == {
        "legacy": "untouched"
    }
    other.close()


@pytest.mark.parametrize("width", [420, 900])
def test_mixed_panel_and_real_picture_preview_fit_width(panel, qt_app, width):
    widget, _, tasks = panel
    widget.resize(width, 950)
    widget.show()
    QTest.qWait(40)
    assert widget.width() == width
    assert widget.scroll.horizontalScrollBar().maximum() == 0
    dialog = confirm_preview(widget, tasks)
    dialog.resize(width, 850)
    QTest.qWait(40)
    assert dialog.width() == width
    for index in (0, 1):
        scroll = dialog.tabs.widget(index)
        assert scroll.horizontalScrollBar().maximum() == 0
    assert all(
        label.textFormat() == Qt.TextFormat.PlainText
        for label in dialog.findChildren(QLabel)
    )


def test_real_paper_entry_switches_to_mixed_without_making_word_theme(qt_app):
    facade, tasks = Facade(), Tasks()
    widget = PaperPage(facade, tasks)
    tasks.flush()
    assert widget._mixed_panel.model.order == ["core-a", "word-a"]
    assert all(
        theme.source_identity_sha256 != "word-a" for theme in widget.model.themes
    )
    widget.close()


class BasketWordFacade(WordFacade):
    def add_word_questions_to_basket(self, selections):
        self.calls.append(("add-basket", deepcopy(selections)))
        return 3


def test_word_preview_then_explicit_add_preserves_cross_source_selection(qt_app):
    facade = BasketWordFacade(
        saved=[
            {"key": "Q1", "revision": "revision-Q1", "points": 3},
            {"key": "Q2", "revision": "revision-Q2", "points": 2},
        ]
    )
    tasks = Tasks()
    widget = WordQuestionDialog(facade, tasks)
    tasks.flush()
    counts = []
    widget.basket_changed.connect(counts.append)
    assert not widget.basket_add_button.isEnabled()
    widget.basket_preview_button.click()
    tasks.flush()
    dialog = widget._basket_preview_dialog
    assert dialog is not None
    assert len(dialog._pixmaps) == 4
    dialog.tabs.setCurrentIndex(1)
    dialog.confirm_button.click()
    assert widget.basket_add_button.isEnabled()
    assert not any(call[0] == "add-basket" for call in facade.calls)
    widget.basket_add_button.click()
    tasks.flush()
    assert counts == [3]
    assert len(widget.selections) == 2
    widget.points.setValue(4)
    assert not widget.basket_add_button.isEnabled()
    widget.close()
    tasks.flush()


def test_unconverted_word_picture_never_confirms(qt_app):
    tasks = Tasks()
    data = {
        "sections": [
            {
                "title_zh": "合成矢量题",
                "student_blocks": [{"kind": "image", "image_id": "opaque"}],
                "teacher_blocks": [{"kind": "text", "text": "合成答案"}],
            }
        ]
    }
    dialog = MixedPaperPreviewDialog(
        data, tasks, lambda _key: {"data": b"not-raster", "content_type": "image/x-wmf"}
    )
    tasks.flush()
    dialog.tabs.setCurrentIndex(1)
    assert not dialog.confirm_button.isEnabled()
    assert "不完整" in dialog.status.text()
    dialog.reject()


def test_legacy_core_refresh_and_transition_keep_exact_order_score_and_removal(qt_app):
    facade, tasks = Facade(), Tasks()
    facade.rows = [
        {
            "key": key,
            "scope": "master",
            "source_identity_sha256": key,
            "title_zh": "同名合成题",
            "atomic_total": 1,
        }
        for key in ("core-a", "core-b")
    ]
    widget = PaperPage(facade, tasks)
    assert widget._mixed_panel is None
    first, second = widget.model.themes
    second.questions[0].key = "atomic-b"
    second.questions[0].score = 7
    second.questions[0].answer_space = 0
    widget.model.themes = [second]
    widget._mark_dirty()
    facade.rows.append(
        {
            "key": "core-c",
            "scope": "master",
            "source_identity_sha256": "core-c",
            "title_zh": "新增合成题",
            "atomic_total": 1,
        }
    )
    widget.update_basket_count()
    assert [theme.source_identity_sha256 for theme in widget.model.themes] == [
        "core-b",
        "core-c",
    ]
    assert widget.model.themes[0].questions[0].score == 7
    facade.rows.append(
        {"key": "word-a", "item_kind": "word_question", "title_zh": "合成 Word"}
    )
    widget.update_basket_count()
    tasks.flush()
    assert widget._mixed_panel.model.order == ["core-b", "core-c", "word-a"]
    assert widget._mixed_panel.model.excluded == {"core-a"}
    assert widget._mixed_panel.model.settings["core-b"] == {
        "atomic_settings": {"atomic-b": {"score": 7, "answer_space_lines": 0}}
    }
    widget.close()


def test_old_hidden_object_marker_blocks_word_basket_preview(qt_app):
    facade = BasketWordFacade(
        saved=[{"key": "Q1", "revision": "revision-Q1", "points": 2}]
    )
    facade.catalog["items"][0]["question_blocks"][0]["assets"] = []
    facade.catalog["items"][0]["question_blocks"][0]["text"] += (
        "【待查看原文：嵌入对象或旧公式】"
    )
    tasks = Tasks()
    widget = WordQuestionDialog(facade, tasks)
    tasks.flush()
    assert not widget.export_button.isEnabled()
    assert widget.export_button.isHidden()
    widget._export()
    assert not any(call[0] == "export" for call in facade.calls)
    widget.basket_preview_button.click()
    tasks.flush()
    dialog = widget._basket_preview_dialog
    dialog.tabs.setCurrentIndex(1)
    assert not dialog.confirm_button.isEnabled()
    assert not widget.basket_add_button.isEnabled()
    widget.close()
    tasks.flush()


def test_frozen_image_hash_mismatch_disables_confirmation(qt_app):
    tasks = Tasks()
    blocks = [{"kind": "image", "image_id": "synthetic-image"}]
    dialog = MixedPaperPreviewDialog(
        {"sections": [{"student_blocks": blocks, "teacher_blocks": blocks}]},
        tasks,
        lambda _key: {
            "data": png_bytes(),
            "content_type": "image/png",
            "sha256": "0" * 64,
        },
    )
    tasks.flush()
    dialog.tabs.setCurrentIndex(1)
    assert not dialog.confirm_button.isEnabled()
    dialog.reject()


def test_closed_panel_rejects_late_projection_callback(panel):
    widget, _, tasks = panel
    widget.show()
    widget.load()
    before = deepcopy(widget.model.items)
    widget.close()
    tasks.flush()
    assert widget._closed
    assert widget.model.items == before


@pytest.mark.parametrize(
    "record",
    [
        "broken",
        {"payload": []},
        {"payload": {"schema_version": "unknown"}},
        {
            "payload": {
                "schema_version": "shchem.desktop-mixed-ui-draft.v1",
                "order": [],
                "excluded": [],
                "settings": {},
                "settings_ui": {"duration_minutes": "bad"},
            }
        },
    ],
)
def test_invalid_saved_mixed_draft_is_preserved_and_never_auto_overwritten(
    qt_app, record
):
    facade, tasks = Facade(), Tasks()
    facade.state_store.data["drafts"]["paper-mixed-current"] = deepcopy(record)
    before = deepcopy(facade.state_store.data)
    widget = MixedPaperPanel(facade, tasks, PaperComposerModel())
    widget.load()
    tasks.flush()
    assert widget._restore_failed and not widget._loaded_once
    assert "原稿未覆盖" in widget.status.text()
    assert (
        not widget.preview_button.isEnabled() and not widget.export_button.isEnabled()
    )
    widget._save()
    widget._preview_request()
    assert facade.state_store.data == before
    assert not any(call[0] in {"preview", "approve", "export"} for call in facade.calls)
    widget.close()
    widget.deleteLater()


def test_failed_draft_read_is_not_replaced_by_defaults_and_can_retry(
    qt_app, monkeypatch
):
    facade, tasks = Facade(), Tasks()
    before = deepcopy(facade.state_store.data)
    original = facade.state_store.snapshot

    def unavailable():
        raise OSError("synthetic draft read failure")

    monkeypatch.setattr(facade.state_store, "snapshot", unavailable)
    widget = MixedPaperPanel(facade, tasks, PaperComposerModel())
    widget.load()
    tasks.flush()
    assert widget._restore_failed and facade.state_store.data == before
    monkeypatch.setattr(facade.state_store, "snapshot", original)
    widget.load()
    tasks.flush()
    assert not widget._restore_failed and widget._loaded_once
    assert widget.preview_button.isEnabled()
    widget.close()
    widget.deleteLater()


def test_draft_save_failure_remains_visible_after_projection_load(qt_app, monkeypatch):
    facade, tasks = Facade(), Tasks()

    def unwritable(*_args):
        raise OSError("synthetic save failure")

    monkeypatch.setattr(facade.state_store, "save_draft", unwritable)
    widget = MixedPaperPanel(facade, tasks, PaperComposerModel())
    widget.load()
    tasks.flush()
    assert "草稿暂未保存" in widget.status.text()
    widget.close()
    widget.deleteLater()

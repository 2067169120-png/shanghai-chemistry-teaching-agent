from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_blueprint_drafts import DRAFT_KIND
from ..desktop_blueprint_generation import format_blueprint


class BlueprintDraftDialog(QDialog):
    """Edit content with source evidence visible; save independent local drafts."""

    def __init__(
        self,
        facade: Any,
        preview_id: str,
        parent: Any = None,
        *,
        tasks: Any = None,
        profile: Any = None,
    ):
        super().__init__(parent)
        self.facade, self.preview_id = facade, preview_id
        self.tasks, self.profile = tasks, profile
        self._review_dialog: Any = None
        self._source_zoom: Any = None
        self._page_generation = 0
        self._page_closed = False
        self._loading = True
        self._dirty = False
        self._source_index = -1
        self._source: dict | None = None
        self._candidate: dict | None = None
        self._sources: list[dict] = []
        self.setWindowTitle('编辑命题方案')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(920, 820)
        self.setMinimumSize(360, 540)
        root = QVBoxLayout(self)
        intro = QLabel(
            "直接修改共同材料、任务和解答思路，逐项对照资料。另存草稿只在本机进行；保存后可另行确认AI审校，不覆盖原稿。保存和AI审校均不等于正确性认证。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.source = self._combo()
        self.source.currentIndexChanged.connect(self._select_source)
        root.addWidget(self.source)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        root.addWidget(self.tabs, 1)

        general = QWidget()
        general_form = QFormLayout(general)
        general_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.theme = self._text(100)
        self.unknowns = self._text(140)
        self.note = self._text(80)
        general_form.addRow("主题中心", self.theme)
        general_form.addRow("待补资料与不确定性（每行一项）", self.unknowns)
        general_form.addRow("本次修订说明（可选，最多2000字）", self.note)
        self.tabs.addTab(self._scroll(general), "主题与待补")
        self.theme.textChanged.connect(
            lambda: self._general_changed("theme_center", self.theme)
        )
        self.unknowns.textChanged.connect(
            lambda: self._general_changed("unknowns", self.unknowns)
        )
        self.note.textChanged.connect(self._mark_dirty)

        materials = QWidget()
        materials_form = QFormLayout(materials)
        materials_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.material = self._combo()
        self.material.currentIndexChanged.connect(self._select_material)
        self.purpose = self._text(250)
        self.purpose.setPlaceholderText(
            "明确样品身份、已知组成、互斥假设、处理阶段和题目实际使用的条件。"
        )
        self.evidence_refs = QLineEdit()
        self.evidence_refs.setPlaceholderText("例如 E1, E24；可在资料依据页逐项核对")
        materials_form.addRow("共同材料（编号保留）", self.material)
        materials_form.addRow("材料内容与用途", self.purpose)
        materials_form.addRow("来源编号", self.evidence_refs)
        self.inspect_references = QPushButton("核对引用资料")
        self.inspect_references.clicked.connect(self._inspect_references)
        materials_form.addRow(self.inspect_references)
        self.purpose.textChanged.connect(self._material_changed)
        self.evidence_refs.textChanged.connect(self._material_changed)
        self.tabs.addTab(self._scroll(materials), "共同材料")

        questions = QWidget()
        questions_form = QFormLayout(questions)
        questions_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.question = self._combo()
        self.question.currentIndexChanged.connect(self._select_question)
        questions_form.addRow("印刷小题 / 作答单元（编号与顺序保留）", self.question)
        self.fields: dict[str, Any] = {}
        for key, label, multiline in (
            ("task_plan", "任务设计", True),
            ("answer_outline", "解答思路与成立条件", True),
            ("response_form", "作答形式", False),
            ("material_refs", "所用材料编号（例如 M1, M2）", False),
            ("depends_on", "实际使用的前序作答编号（不依赖则留空）", False),
            ("knowledge_evidence", "知识与来源依据", True),
            ("difficulty_evidence", "难度依据（未实测需注明）", True),
        ):
            widget = self._text(95) if multiline else QLineEdit()
            self.fields[key] = widget
            questions_form.addRow(label, widget)
            widget.textChanged.connect(self._question_changed)
        self.tabs.addTab(self._scroll(questions), "小题与解答")
        self.evidence = self._text(0)
        self.evidence.setReadOnly(True)
        self.evidence_panel = QWidget()
        evidence_layout = QVBoxLayout(self.evidence_panel)
        self.evidence_pick = self._combo()
        self.evidence_pick.currentIndexChanged.connect(self._select_evidence)
        evidence_layout.addWidget(self.evidence_pick)
        evidence_layout.addWidget(self.evidence, 1)
        self.source_page = self._combo()
        self.source_open = QPushButton("打开引用的讲义原页，可缩放")
        self.source_open.setEnabled(False)
        self.source_open.clicked.connect(self._open_source_page)
        self.source_page_hint = QLabel()
        self.source_page_hint.setWordWrap(True)
        evidence_layout.addWidget(self.source_page)
        evidence_layout.addWidget(self.source_open)
        evidence_layout.addWidget(self.source_page_hint)
        self.tabs.addTab(self.evidence_panel, "资料依据")
        self.original = self._text(0)
        self.original.setReadOnly(True)
        self.tabs.addTab(self.original, '原命题方案')
        self.preview = self._text(0)
        self.preview.setReadOnly(True)
        self.tabs.addTab(self.preview, "完整预览")
        self.tabs.currentChanged.connect(self._refresh_preview)
        self.status = QLabel("正在读取本地来源…")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.review_button = QPushButton("审校已保存草稿 / 查看审校记录")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self._review)
        root.addWidget(self.review_button)
        actions = QHBoxLayout()
        self.save_button = QPushButton("另存教师草稿")
        self.save_button.clicked.connect(self._save)
        self.copy_button = QPushButton("复制当前稿")
        self.copy_button.clicked.connect(self._copy)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        for button in (self.save_button, self.copy_button, close):
            actions.addWidget(button)
        root.addLayout(actions)
        self._loading = False
        self._reload_sources()

    @staticmethod
    def _combo() -> QComboBox:
        combo = QComboBox()
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        return combo

    @staticmethod
    def _text(height: int) -> QPlainTextEdit:
        widget = QPlainTextEdit()
        widget.setMinimumWidth(0)
        if height:
            widget.setMinimumHeight(height)
        return widget

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        return scroll

    @staticmethod
    def _refs(text: str) -> list[str]:
        return [value for value in re.split(r"[\s,，、;；]+", text.strip()) if value]

    def _reload_sources(self, selected_id: str | None = None):
        try:
            sources = self.facade.prompt_blueprint_draft_sources(self.preview_id)
        except Exception as exc:  # noqa: BLE001 - UI error boundary
            self.status.setText(
                getattr(exc, "message_zh", "读取草稿来源失败，请重新打开。")
            )
            self.save_button.setEnabled(False)
            self.copy_button.setEnabled(False)
            self.review_button.setEnabled(False)
            return
        self._sources = sources
        self.source.blockSignals(True)
        self.source.clear()
        index = 0
        for position, source in enumerate(sources):
            self.source.addItem(source["source_label"], source["source_id"])
            if source["source_id"] == selected_id:
                index = position
        self.source.setCurrentIndex(index)
        self.source.blockSignals(False)
        self._select_source(index)

    def _select_source(self, index: int):
        if self._loading or not 0 <= index < len(self._sources):
            return
        if self._dirty and not self._discard_ok():
            self.source.blockSignals(True)
            self.source.setCurrentIndex(self._source_index)
            self.source.blockSignals(False)
            return
        self._source_index = index
        self._source = deepcopy(self._sources[index])
        self._candidate = deepcopy(self._source["candidate"])
        self._loading = True
        self.theme.setPlainText(self._candidate["theme_center"])
        self.unknowns.setPlainText("\n".join(self._candidate["unknowns"]))
        self.note.setPlainText(self._source.get("source_note", ""))
        self.material.clear()
        self.question.clear()
        for material in self._candidate["shared_material_plan"]:
            self.material.addItem(material["material_id"])
        for part in self._candidate["question_chain"]:
            self.question.addItem(
                f"{part['printed_question_id']} / {part['atomic_part_id']} · {part['response_form']}"
            )
        self.original.setPlainText(
            format_blueprint({"candidate": self._sources[0]["candidate"]})
        )
        self.evidence_pick.clear()
        self.evidence_pick.addItem("全部资料（保留原E编号）", 0)
        for i, item in enumerate(self._source["evidence"], 1):
            category = (
                "本地规则"
                if item.get("source_type") == "local_contract"
                else "来源资料"
            )
            self.evidence_pick.addItem(
                f"E{i} · {category} · {item.get('scope', '')}", i
            )
        self._loading = False
        self._select_material(0)
        self._select_question(0)
        self._select_evidence(0)
        self._dirty = False
        self.save_button.setEnabled(True)
        self.copy_button.setEnabled(True)
        self.status.setText("已载入来源副本。编辑只影响当前稿，保存时另建记录。")
        self._refresh_preview()
        self._update_review_enabled()

    def _select_material(self, index: int):
        if self._loading or self._candidate is None or index < 0:
            return
        item = self._candidate["shared_material_plan"][index]
        self._loading = True
        self.purpose.setPlainText(item["purpose"])
        self.evidence_refs.setText(", ".join(item["evidence_refs"]))
        self._loading = False

    def _select_evidence(self, index: int):
        if self._source is None or self._loading:
            return
        evidence = self._source["evidence"]
        selected = (
            [(index, evidence[index - 1])]
            if 0 < index <= len(evidence)
            else list(enumerate(evidence, 1))
        )
        self.evidence.setPlainText(
            "\n\n".join(
                f"E{i} · {item.get('scope', '')}\n"
                + "\n".join(str(v) for v in item.get("supports", []))
                for i, item in selected
            )
        )
        self._load_source_pages(index)

    def _load_source_pages(self, index: int):
        self._page_generation += 1
        generation = self._page_generation
        self.source_page.clear()
        self.source_open.setEnabled(False)
        if self._source_zoom is not None:
            self._source_zoom.close()
        evidence_id = f"E{index}"
        if (
            self.tasks is None
            or self._source is None
            or evidence_id not in self._source.get("handout_evidence_ids", [])
        ):
            self.source_page_hint.setText(
                "选择一条带原页绑定的讲义资料，可在此对照题面。这里只读本地图片，不发送给模型。"
            )
            return
        self.source_page_hint.setText('正在核验这份命题方案引用的讲义版本…')
        self.tasks.submit(
            "读取引用讲义原页",
            lambda: self.facade.prompt_blueprint_evidence_pages(
                self.preview_id, evidence_id
            ),
            on_success=lambda pages: self._source_pages_ready(
                generation, evidence_id, pages
            ),
            on_failure=lambda message: self._source_page_failed(generation, message),
        )

    def _source_pages_ready(self, generation: int, evidence_id: str, pages: Any):
        if self._page_closed or generation != self._page_generation:
            return
        for page in pages:
            self.source_page.addItem(
                page["caption"], (evidence_id, page["role"], page["index"])
            )
        self.source_open.setEnabled(bool(pages))
        self.source_page_hint.setText(
            "原页仅用于核对已引用资料，不代表答案正确性认证；未引用的答案不在此显示。"
            if pages
            else "该讲义依据没有可用的原页定位，已保存文字仍可查看。"
        )

    def _source_page_failed(self, generation: int, message: str):
        if self._page_closed or generation != self._page_generation:
            return
        self.source_open.setEnabled(False)
        self.source_page_hint.setText(message)

    def _open_source_page(self):
        selected = self.source_page.currentData()
        if self.tasks is None or selected is None or not self.source_open.isEnabled():
            return
        evidence_id, role, index = selected
        generation, caption = self._page_generation, self.source_page.currentText()
        self.source_open.setEnabled(False)
        self.tasks.submit(
            "核验并打开讲义原页",
            lambda: self.facade.prompt_blueprint_evidence_page(
                self.preview_id, evidence_id, role, index
            ),
            on_success=lambda data: self._source_image_ready(generation, caption, data),
            on_failure=lambda message: self._source_page_failed(generation, message),
        )

    def _source_image_ready(self, generation: int, caption: str, data: Any):
        if self._page_closed or generation != self._page_generation:
            return
        pixmap = QPixmap()
        if not isinstance(data, bytes) or not pixmap.loadFromData(data):
            self._source_page_failed(
                generation, "原页图像无法读取，资料文字和当前草稿仍保留。"
            )
            return
        from .library_detail import ImageZoomDialog

        if self._source_zoom is not None:
            self._source_zoom.close()
        viewer = ImageZoomDialog(pixmap, caption, self)
        self._source_zoom = viewer
        viewer.destroyed.connect(lambda: self._source_zoom_closed(viewer))
        self.source_open.setEnabled(True)
        viewer.show()

    def _source_zoom_closed(self, viewer: Any):
        if self._source_zoom is viewer:
            self._source_zoom = None

    def _inspect_references(self):
        if self._source is None:
            return
        refs = self._refs(self.evidence_refs.text())
        first = next(
            (
                int(ref[1:])
                for ref in refs
                if re.fullmatch(r"E[1-9][0-9]*", ref)
                and int(ref[1:]) <= len(self._source["evidence"])
            ),
            0,
        )
        self.evidence_pick.setCurrentIndex(first)
        self.tabs.setCurrentWidget(self.evidence_panel)

    def _select_question(self, index: int):
        if self._loading or self._candidate is None or index < 0:
            return
        item = self._candidate["question_chain"][index]
        self._loading = True
        for key, widget in self.fields.items():
            value = item[key]
            text = ", ".join(value) if isinstance(value, list) else value
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(text)
            else:
                widget.setText(text)
        self._loading = False

    def _mark_dirty(self):
        if self._loading or self._candidate is None:
            return
        self._dirty = True
        self._update_review_enabled()
        self.status.setText("有未保存修改；切换来源或关闭前会提醒。")

    def _general_changed(self, key: str, widget: QPlainTextEdit):
        if self._loading or self._candidate is None:
            return
        text = widget.toPlainText()
        self._candidate[key] = (
            [line for line in text.splitlines() if line.strip()]
            if key == "unknowns"
            else text
        )
        self._mark_dirty()

    def _material_changed(self):
        if self._loading or self._candidate is None:
            return
        item = self._candidate["shared_material_plan"][self.material.currentIndex()]
        item.update(
            purpose=self.purpose.toPlainText(),
            evidence_refs=self._refs(self.evidence_refs.text()),
        )
        self._mark_dirty()

    def _question_changed(self):
        if self._loading or self._candidate is None:
            return
        item = self._candidate["question_chain"][self.question.currentIndex()]
        for key, widget in self.fields.items():
            text = (
                widget.toPlainText()
                if isinstance(widget, QPlainTextEdit)
                else widget.text()
            )
            item[key] = (
                self._refs(text) if key in ("material_refs", "depends_on") else text
            )
        self._mark_dirty()

    def _refresh_preview(self, *_args):
        if self._candidate is not None:
            self.preview.setPlainText(
                "教师编辑稿（"
                + ("未保存" if self._dirty else "来源副本/已保存")
                + "；不是正确性认证）\n\n"
                + format_blueprint({"candidate": self._candidate})
            )

    def _copy(self):
        self._refresh_preview()
        QGuiApplication.clipboard().setText(self.preview.toPlainText())
        self.status.setText(
            "已复制当前教师编辑稿；未保存的修改仍需另存。"
            if self._dirty
            else "已复制当前稿。"
        )

    def _save(self):
        if self._source is None or self._candidate is None:
            return
        try:
            record = self.facade.save_prompt_blueprint_draft(
                self.preview_id,
                self._source["source_id"],
                self._source["source_revision"],
                deepcopy(self._candidate),
                note=self.note.toPlainText(),
            )
        except Exception as exc:  # noqa: BLE001 - UI error boundary
            self.status.setText(
                getattr(exc, "message_zh", "保存失败，当前修改仍保留，请重试。")
            )
            return
        self._dirty = False
        self._reload_sources(record["draft_id"])
        self.status.setText("已另存教师草稿，可从上方来源列表重开；原稿未覆盖。")

    def _update_review_enabled(self):
        eligible = (
            self.tasks is not None
            and self._source is not None
            and self._source.get("source_kind") == DRAFT_KIND
            and not self._dirty
        )
        self.review_button.setEnabled(eligible)
        self.review_button.setToolTip(
            "只审校上方选中的已保存教师草稿；查看历史不调用模型。"
            if eligible
            else "请先另存教师草稿；未保存的文字不会发送给模型。"
        )

    def _review(self):
        if not self.review_button.isEnabled() or self._dirty or self._source is None:
            return
        if self._review_dialog is not None:
            self._review_dialog.show()
            self._review_dialog.raise_()
            return
        from .blueprint_review_dialog import BlueprintReviewDialog

        source_id = self._source["source_id"]
        child = BlueprintReviewDialog(
            self.facade,
            self.tasks,
            self.preview_id,
            {"candidate": deepcopy(self._source["candidate"])},
            self.profile,
            self,
            source_draft_id=source_id,
            source_label=self._source["source_label"],
        )
        self._review_dialog = child
        child.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        def finished(_result):
            self._review_dialog = None
            # Closing an inspection never switches the teacher's working source.
            self._reload_sources(source_id)

        child.finished.connect(finished)
        child.show()

    def _discard_ok(self) -> bool:
        if not self._dirty:
            return True
        return (
            QMessageBox.question(
                self,
                "保留未保存修改",
                "当前修改尚未保存。要放弃这些修改吗？",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Discard
        )

    def closeEvent(self, event: Any):
        if self._review_dialog is not None and not self._review_dialog.close():
            event.ignore()
            return
        if self._discard_ok():
            self._close_source_pages()
            super().closeEvent(event)
        else:
            event.ignore()

    def reject(self):
        if self._review_dialog is not None and not self._review_dialog.close():
            return
        if self._discard_ok():
            self._close_source_pages()
            super().reject()

    def _close_source_pages(self):
        self._page_closed = True
        self._page_generation += 1
        if self._source_zoom is not None:
            self._source_zoom.close()

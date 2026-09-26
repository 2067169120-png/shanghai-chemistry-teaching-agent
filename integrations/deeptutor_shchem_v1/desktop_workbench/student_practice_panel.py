from __future__ import annotations

"""Teacher-facing, submission-scoped practice cards and complete-theme reader."""

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .components import CardFrame, set_status
from .library_detail import LibraryDetailDialog


def _field(value: object, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _label(text: object, name: str = "MutedLabel") -> QLabel:
    label = QLabel(str(text))
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _count(value: object, name: str) -> int:
    try:
        return max(0, int(_field(value, name, 0)))
    except (TypeError, ValueError):
        return 0


class StudentPracticeCard(CardFrame):
    view_requested = Signal(str)
    add_requested = Signal(str)

    def __init__(self, recommendation: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = str(_field(recommendation, "key", ""))
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(_label(_field(recommendation, "title_zh", "推荐大题"), "CardTitle"))
        layout.addWidget(_label(_field(recommendation, "source_zh", "来源待核对")))
        layout.addWidget(_label("推荐依据：" + str(_field(recommendation, "reason_zh", "请核对本次教师确认的教材章节。"))))
        layout.addWidget(_label(
            f"命中 {_count(recommendation, 'matched_question_count')} 个单元 · "
            f"完整大题 {_count(recommendation, 'atomic_total')} 单元 · "
            f"共同材料 {_count(recommendation, 'shared_material_count')} 项"
        ))
        layout.addWidget(_label(_field(recommendation, "readiness_zh", "请先核对完整题面与共同材料。")))
        self.status = _label("先查看完整题目与共同材料，再决定是否加入题篮。")
        self.status.setAccessibleName("推荐练习操作状态")
        layout.addWidget(self.status)
        self.view_button = QPushButton("查看完整题目与材料")
        self.view_button.setObjectName("QuietButton")
        self.view_button.setAccessibleName("查看推荐练习完整题目与材料")
        self.view_button.clicked.connect(lambda: self.view_requested.emit(self.key))
        self.add_button = QPushButton("加入完整大题")
        self.add_button.setAccessibleName("将已查看的推荐完整大题加入题篮")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(lambda: self.add_requested.emit(self.key))
        layout.addWidget(self.view_button)
        layout.addWidget(self.add_button)


class StudentPracticePanel(CardFrame):
    recommend_requested = Signal()
    view_requested = Signal(str)
    add_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cards: dict[str, StudentPracticeCard] = {}
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(_label("7　本次学情 · 推荐练习", "CardTitle"))
        layout.addWidget(_label(
            "依据本次已记录的教师评分、诊断和教材章节，从本机题库寻找相关练习。"
            "不会自动形成长期学生标签；加入题篮时保留完整大题及共同材料。"
        ))
        self.status = _label("先记录教师评分并确认诊断对应的教材章节，再按本次学情推荐。")
        self.status.setAccessibleName("本次学情推荐状态")
        self.status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout.addWidget(self.status)
        self.recommend_button = QPushButton("按本次学情推荐练习")
        self.recommend_button.setObjectName("PrimaryAction")
        self.recommend_button.setAccessibleName("按本次已确认学情推荐练习")
        self.recommend_button.setEnabled(False)
        self.recommend_button.clicked.connect(self.recommend_requested)
        layout.addWidget(self.recommend_button)
        self.items = QVBoxLayout()
        self.items.setSpacing(10)
        layout.addLayout(self.items)

    def clear(self, message: str) -> None:
        self.cards.clear()
        while self.items.count():
            widget = self.items.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        set_status(self.status, "info", message)

    def show_preview(self, preview: object) -> None:
        self.clear(str(_field(preview, "message_zh", "已按本次教师确认记录查找相关练习。")))
        for notice in tuple(_field(preview, "notices_zh", ()) or ()):
            label = _label(notice)
            set_status(label, "attention")
            self.items.addWidget(label)
        for diagnosis in tuple(_field(preview, "diagnoses", ()) or ()):
            label = _label("\n".join((
                str(_field(diagnosis, "title_zh", "本次诊断依据")),
                str(_field(diagnosis, "status_zh", "")),
                str(_field(diagnosis, "evidence_zh", "")),
            )))
            label.setAccessibleName("推荐练习对应的教材章节与诊断依据")
            self.items.addWidget(label)
        for recommendation in tuple(_field(preview, "recommendations", ()) or ()):
            key = str(_field(recommendation, "key", ""))
            if not key or key in self.cards:
                continue
            card = StudentPracticeCard(recommendation)
            card.view_requested.connect(self.view_requested)
            card.add_requested.connect(self.add_requested)
            self.cards[key] = card
            self.items.addWidget(card)
        if not self.cards:
            self.items.addWidget(_label(
                "本次没有可加入的推荐。请核对已记录的评分、教材章节与诊断，"
                "也可以到题库按章节自行查找完整大题。"
            ))


class StudentPracticeDetailDialog(LibraryDetailDialog):
    """Unlock a practice card only after its question images actually display."""

    preview_ready = Signal()
    preview_failed = Signal(str)

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        # The student workflow owns preview + basket only. An unconnected
        # preparation button would otherwise appear to perform a silent action.
        for button in self._preparation_buttons:
            button.hide()
        self._required_images = tuple(self._image_widgets)
        self._preview_reported = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(75)
        self._preview_timer.timeout.connect(self._check_preview)
        self._preview_timer.start()

    def _check_preview(self) -> None:
        if self._preview_reported or not self.isVisible():
            return
        if any(widget._task_id is not None for widget in self._required_images):
            return
        self._preview_reported = True
        self._preview_timer.stop()
        if not self.detail.parts or any(not widget.has_image for widget in self._required_images):
            self.preview_failed.emit("完整题面或共同材料尚未成功显示，请关闭后重新查看；暂不能加入题篮。")
            return
        self.preview_ready.emit()

    def closeEvent(self, event: object) -> None:
        self._preview_timer.stop()
        super().closeEvent(event)


__all__ = ["StudentPracticeCard", "StudentPracticeDetailDialog", "StudentPracticePanel"]

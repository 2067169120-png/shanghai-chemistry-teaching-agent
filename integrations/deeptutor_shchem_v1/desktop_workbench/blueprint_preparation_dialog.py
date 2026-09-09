"""Offline chooser: preview saved blueprint reference before appending it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from ..desktop_blueprint_drafts import BlueprintDraftError


class BlueprintPreparationDialog(QDialog):
    def __init__(self, facade, parent=None):
        super().__init__(parent)
        self.facade = facade
        self.reference: dict | None = None
        self.setWindowTitle("从命题蓝图导入备课参考")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(820, 720)
        self.setMinimumSize(360, 480)
        root = QVBoxLayout(self)
        intro = QLabel(
            "选择最近30份已完成蓝图中的原稿、教师草稿或AI修订稿。"
            "先预览，再追加到备课资料；不会改动原蓝图或自动调用模型。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.source = QComboBox()
        self.source.setAccessibleName("选择已保存蓝图版本作为备课参考")
        self.source.setMinimumContentsLength(8)
        self.source.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        root.addWidget(self.source)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("即将导入的蓝图参考全文")
        root.addWidget(self.preview, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.import_button = QPushButton("将预览全文追加到备课资料")
        self.import_button.setObjectName("PrimaryAction")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._confirm)
        root.addWidget(self.import_button)
        close = QPushButton("取消，不导入")
        close.clicked.connect(self.reject)
        root.addWidget(close)
        self.source.currentIndexChanged.connect(self._select)
        try:
            options = facade.preparation_blueprint_options()
            for option in options:
                self.source.addItem(option["label"], option)
            if not options:
                self.status.setText(
                    "暂无可导入蓝图。请先在教材/命题蓝图入口生成并保存蓝图。"
                )
        except Exception:  # noqa: BLE001 - native boundary must not expose raw state errors
            self.status.setText("本地蓝图列表读取失败，备课内容未变。请关闭后重试。")

    def _select(self, _index: int) -> None:
        self.reference = None
        self.import_button.setEnabled(False)
        self.preview.clear()
        option = self.source.currentData()
        if not isinstance(option, dict):
            return
        try:
            reference = self.facade.preparation_blueprint_reference(
                option["preview_id"], option["source_id"], option["source_revision"]
            )
        except BlueprintDraftError as exc:
            self.status.setText(exc.message_zh)
            return
        except Exception:  # noqa: BLE001 - native boundary must not expose raw state errors
            self.status.setText("参考读取失败，未导入。请重新打开窗口选择。")
            return
        self.reference = reference
        self.preview.setPlainText(reference["materials"])
        self.status.setText(
            f"共{len(reference['materials'])}字。仅导入资料快照与设计思路，不导入图片；"
            "化学事实、答案与课堂适用性仍需教师核验。生成时将按原有流程确认文字出站。"
        )
        self.import_button.setEnabled(True)

    def _confirm(self) -> None:
        if self.reference is None:
            return
        # Recheck exact saved version before handing it to the current form.
        previous = self.reference
        self._select(self.source.currentIndex())
        if self.reference is None:
            return
        if self.reference != previous:
            self.status.setText("参考内容已变化，请重新查看全文后再次确认。")
            return
        self.accept()

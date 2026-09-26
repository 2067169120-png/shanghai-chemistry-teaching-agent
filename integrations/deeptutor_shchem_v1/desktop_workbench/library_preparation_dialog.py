"""Select library units and review the exact offline text before appending."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..desktop_blueprint_drafts import BlueprintDraftError
from ..desktop_library_preparation import library_preparation_reference


class LibraryPreparationDialog(QDialog):
    def __init__(self, detail, parent=None):
        super().__init__(parent)
        self.detail = detail
        self.reference = None
        self.setWindowTitle("将题库选题带入备课")
        self.resize(820, 760)
        self.setMinimumSize(360, 480)
        root = QVBoxLayout(self)
        intro = QLabel(
            "选择本主题中的作答单元，预览将要追加的参考文字。保留已有教材资料、课题和目标；不改变题篮或组卷，不调用模型。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        heading = QLabel(detail.title_zh + "\n" + detail.paper_title_zh)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        root.addWidget(heading)
        self.units = QListWidget()
        self.units.setAccessibleName("选择本主题内要带入备课的作答单元")
        self.units.setWordWrap(True)
        self.units.setMinimumWidth(0)
        self.units.setFixedHeight(min(180, max(64, len(detail.parts) * 34 + 12)))
        for index, part in enumerate(detail.parts, 1):
            item = QListWidgetItem(f"第{index}个作答单元 · {part.label_zh}")
            item.setData(Qt.ItemDataRole.UserRole, part.key)
            item.setToolTip(part.summary_zh)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.units.addItem(item)
        root.addWidget(self.units)
        self.include_answers = QCheckBox('包含参考答案与解题思路（使用前请核对）')
        self.include_answers.setChecked(True)
        root.addWidget(self.include_answers)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("即将追加的题库参考全文")
        root.addWidget(self.preview, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        self.import_button = QPushButton("追加到备课资料并打开备课页")
        self.import_button.setObjectName("PrimaryAction")
        self.import_button.clicked.connect(self.accept)
        root.addWidget(self.import_button)
        self.cancel_button = QPushButton("取消，不导入")
        self.cancel_button.setObjectName("QuietButton")
        self.cancel_button.clicked.connect(self.reject)
        root.addWidget(self.cancel_button)
        self.units.itemChanged.connect(self._refresh)
        self.include_answers.toggled.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_args):
        self.reference = None
        self.import_button.setEnabled(False)
        self.preview.clear()
        keys = [
            self.units.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.units.count())
            if self.units.item(i).checkState() == Qt.CheckState.Checked
        ]
        try:
            self.reference = library_preparation_reference(
                self.detail, keys, include_answers=self.include_answers.isChecked()
            )
        except BlueprintDraftError as exc:
            self.status.setText(exc.message_zh)
            return
        self.preview.setPlainText(self.reference["materials"])
        self.status.setText(
            f"已选{self.reference['question_count']}个作答单元 · {len(self.reference['materials'])}字。"
            "仅复制预览文字，摘要不等于原题全文，图片需另选；生成仍需单独确认。"
        )
        self.import_button.setEnabled(True)

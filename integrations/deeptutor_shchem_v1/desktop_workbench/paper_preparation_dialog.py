"""Preview a frozen working-copy paper before appending teaching references."""

from copy import deepcopy

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
from ..desktop_paper_preparation import paper_preparation_reference


class PaperPreparationDialog(QDialog):
    def __init__(self, snapshot, parent=None):
        super().__init__(parent)
        self.snapshot = deepcopy(snapshot)
        self.reference = None
        self.setWindowTitle("将当前组卷带入备课")
        self.resize(820, 760)
        self.setMinimumSize(360, 480)
        root = QVBoxLayout(self)
        intro = QLabel(
            "按当前组卷顺序选择例题或讲评材料。先预览全文，再追加到备课；保留原有教材资料、课题和目标，不保存组卷、不调用模型。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.themes = QListWidget()
        self.themes.setAccessibleName("选择要带入备课的大题")
        self.themes.setFixedHeight(
            min(150, max(54, len(self.snapshot.get("themes", [])) * 32 + 12))
        )
        self.themes.setWordWrap(True)
        root.addWidget(self.themes)
        for index, theme in enumerate(self.snapshot.get("themes", [])):
            item = QListWidgetItem(
                f"原编排第{index + 1}道大题 · {theme.get('title', '未命名大题')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.themes.addItem(item)
        self.include_answers = QCheckBox("包含参考答案与解析（供教师讲评，仍须核验）")
        self.include_answers.setChecked(True)
        root.addWidget(self.include_answers)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("即将追加的组卷参考全文")
        root.addWidget(self.preview, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.import_button = QPushButton("追加到备课资料并打开备课页")
        self.import_button.setObjectName("PrimaryAction")
        self.import_button.clicked.connect(self.accept)
        root.addWidget(self.import_button)
        cancel = QPushButton("取消，不导入")
        cancel.setObjectName("QuietButton")
        cancel.clicked.connect(self.reject)
        root.addWidget(cancel)
        self.themes.itemChanged.connect(self._refresh)
        self.include_answers.toggled.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_args):
        self.reference = None
        self.import_button.setEnabled(False)
        self.preview.clear()
        indices = [
            self.themes.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.themes.count())
            if self.themes.item(i).checkState() == Qt.CheckState.Checked
        ]
        try:
            reference = paper_preparation_reference(
                self.snapshot, indices, include_answers=self.include_answers.isChecked()
            )
        except BlueprintDraftError as exc:
            self.status.setText(exc.message_zh)
            return
        self.reference = reference
        self.preview.setPlainText(reference["materials"])
        self.status.setText(
            f"{reference['theme_count']}道大题 · {reference['question_count']}个作答单元 · {len(reference['materials'])}字。仅复制本页预览文字，图片不会自动传入；生成仍需单独确认。"
        )
        self.import_button.setEnabled(True)

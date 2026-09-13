"""Manual, explicitly confirmed excerpts; never extract or prefill PDF text."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TextbookExcerptWidget(QWidget):
    applied = Signal(object)
    dismissed = Signal()
    pageRequested = Signal(int)

    def __init__(self, source, revision, current_page, existing=None, parent=None):
        super().__init__(parent)
        self._identity = {
            "concept_id": source["concept_id"],
            "revision": revision,
            "source_sha256": source["source_sha256"],
        }
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        instruction = QLabel(
            "对照上方原页手工摘录短句，保留条件与符号。软件不逐字核验，不会自动填入蒸馏摘要。"
        )
        instruction.setWordWrap(True)
        root.addWidget(instruction)
        row = QHBoxLayout()
        row.addWidget(QLabel("摘录所属PDF页"))
        self.page = QComboBox()
        self.page.setAccessibleName("手工摘录对应的PDF文件页序")
        for page in source["pdf_pages"]:
            self.page.addItem(str(page), page)
        index = self.page.findData(current_page)
        self.page.setCurrentIndex(max(index, 0))
        row.addWidget(self.page, 1)
        root.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setAccessibleName("教师手工摘录的教材原句")
        self.text.setPlaceholderText(
            "在此手工输入或粘贴原句；每项最多1200字，不自动截断。"
        )
        self.text.setMaximumHeight(85)
        root.addWidget(self.text)
        self.confirmed = QCheckBox("我已对照所选原页核对文字、条件与符号")
        self.confirmed.setToolTip("这是本次摘录的教师确认，不改变题库正式审核状态。")
        root.addWidget(self.confirmed)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.use = QPushButton("采用并返回选材")
        self.use.setToolTip("将暂存摘录并勾选本知识点；还需预览、确认导入备课参考。")
        self.use.clicked.connect(self._apply)
        actions.addWidget(self.use)
        self.remove = QPushButton("移除摘录")
        self.remove.setObjectName("QuietButton")
        self.remove.setEnabled(existing is not None)
        self.remove.clicked.connect(lambda: self.applied.emit(None))
        actions.addWidget(self.remove)
        self.hide_button = QPushButton("收起")
        self.hide_button.setObjectName("QuietButton")
        self.hide_button.clicked.connect(self.dismissed.emit)
        actions.addWidget(self.hide_button)
        root.addLayout(actions)
        if existing and all(existing.get(k) == v for k, v in self._identity.items()):
            old_index = self.page.findData(existing.get("pdf_page"))
            if old_index >= 0:
                self.page.setCurrentIndex(old_index)
                self.text.setPlainText(existing.get("text", ""))
        self.text.textChanged.connect(self._edited)
        self.page.currentIndexChanged.connect(self._page_changed)
        self.confirmed.toggled.connect(self._update)
        self._update()

    def _edited(self):
        self.confirmed.setChecked(False)
        self._update()

    def _page_changed(self, *_args):
        self._edited()
        self.pageRequested.emit(self.page.currentData())

    def _update(self, *_args):
        text = self.text.toPlainText()
        valid = bool(text.strip()) and len(text) <= 1200
        self.use.setEnabled(valid and self.confirmed.isChecked())
        message = "采用后会勾选本知识点，仍需预览确认导入。"
        if len(text) > 1200:
            message = "超过1200字，未截断；请缩短摘录。"
        self.status.setText(f"{len(text)}/1200字。{message}")

    def _apply(self):
        if not self.use.isEnabled():
            return
        self.applied.emit(
            {
                **self._identity,
                "pdf_page": self.page.currentData(),
                "text": self.text.toPlainText(),
                "confirmed": True,
            }
        )

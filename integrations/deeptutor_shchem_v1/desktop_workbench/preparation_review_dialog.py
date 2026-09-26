"""Read-only classroom structure review for a completed local candidate."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_preparation_review import format_classroom_review
from ..desktop_preparation_source_compare import compare_source_text


class PreparationReviewDialog(QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("课堂结构检查")
        self.resize(1060, 800)
        self.setMinimumSize(360, 480)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "对照当时选定的讲义与教材资料，检查例题、知识总结和原句是否在学生页面中呈现。"
            "这里只检索文字线索，不判定答案或来源对应；不调用模型，不修改原课件。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        compare_page = QWidget()
        compare_layout = QVBoxLayout(compare_page)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索讲义术语、例题或原句，例如：氯化铵、NH4Cl")
        self.search.setAccessibleName("在原备课资料与学生可见正文中对照检索")
        find = QPushButton("对照查找")
        find.setObjectName("PrimaryAction")
        reset = QPushButton("显示全部")
        reset.setObjectName("QuietButton")
        find.clicked.connect(self._compare)
        self.search.returnPressed.connect(self._compare)
        reset.clicked.connect(self._reset)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(find)
        search_row.addWidget(reset)
        compare_layout.addLayout(search_row)
        self.match_status = QLabel()
        self.match_status.setWordWrap(True)
        self.match_status.setTextFormat(Qt.TextFormat.PlainText)
        compare_layout.addWidget(self.match_status)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.source_text = QPlainTextEdit()
        self.source_text.setReadOnly(True)
        self.source_text.setAccessibleName("当时选定的备课资料原文")
        self.student_text = QPlainTextEdit()
        self.student_text.setReadOnly(True)
        self.student_text.setAccessibleName("PPT学生可见正文，不含教师备注和图片像素")
        for title, editor in (
            ("当时选定的资料文字（不是模型改写的来源摘要）", self.source_text),
            ("学生可见正文（不含教师备注及图片像素）", self.student_text),
        ):
            pane = QWidget()
            pane_layout = QVBoxLayout(pane)
            pane_layout.setContentsMargins(0, 0, 0, 0)
            heading = QLabel(title)
            heading.setWordWrap(True)
            pane_layout.addWidget(heading)
            pane_layout.addWidget(editor, 1)
            self.splitter.addWidget(pane)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([500, 500])
        compare_layout.addWidget(self.splitter, 1)
        self.tabs.addTab(compare_page, "资料与课件对照")
        self.review_text = QPlainTextEdit()
        self.review_text.setReadOnly(True)
        self.review_text.setAccessibleName("课堂结构与逐页笔记来源检查")
        self.review_text.setPlainText(format_classroom_review(report))
        self.tabs.addTab(self.review_text, "课堂结构与教师备注")
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self._compare()

    def _reset(self):
        self.search.clear()
        self._compare()

    def _compare(self):
        result = compare_source_text(self.report, self.search.text().strip())
        self.source_text.setPlainText(
            result["source_text"]
            or (
                "未在资料文字中找到同一检索词。可以换用物质名称或较短的原句；不能据此认定原资料没有此知识。"
                if result["has_source"]
                else "这个检查记录未提供当时选定的资料文字，不能用模型备注代替原资料。"
            )
        )
        self.student_text.setPlainText(
            result["student_text"]
            or "未在学生可见文字中找到同一检索词；图片未参与检索，也未进行同义改写或语义判断。"
        )
        if not result["query"]:
            self.match_status.setText(
                "先查看完整资料，再用具体术语或原句对照。检索兼容上下标写法，但不会把仅出现在教师备注中的内容当成学生正文。"
            )
            return
        visible = "、".join(map(str, result["visible_pages"])) or "未找到"
        notes = "、".join(map(str, result["notes_only_pages"])) or "无"
        source = "找到文字线索" if result["source_found"] else "未找到同一文字"
        if not result["has_source"]:
            source = "没有资料快照"
        self.match_status.setText(
            f"资料：{source}；学生正文匹配页：{visible}；仅教师备注匹配页：{notes}。"
            "命中不等于例题或知识已完整采用；请核对条件、全部选项、讲评和可记录总结。"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "splitter"):
            self.splitter.setOrientation(
                Qt.Orientation.Vertical
                if self.width() < 720
                else Qt.Orientation.Horizontal
            )

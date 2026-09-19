"""Quick commands, native vector icons and task-oriented in-app help."""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout
from ..desktop_studio import TEMPLATES
from .components import section_title
from .teacher_help import TeacherHelpDialog as HelpDialog

COMMANDS = (
    ("exam-analysis", "考试分析", "Excel成绩 / 试卷 / 可视化 / 班级讲评 / 月考后先讲什么"),
    ("home", "首页", "最近备课 / 继续编辑 / 选题篮"), ("library", "题库", "教材 / 知识点 / 原题 / 找题"),
    ("paper", "组卷", "题篮 / 学生版 / 教师版 / 出一份练习卷 / 打印"),
    ("student", "学生分析", "作答 / 诊断 / 练习 / 复核作业 / 批改 / 复练 / 改分"),
    ("preparation", "备课", "PPT / 教案 / 学习单 / 明天讲一节新课"),
    ("templates", "教学模板", "场景 / 收藏 / 学习路径"),
    ("classroom", "课堂工具", '倒计时 / 点名 / 分组 / 动态平衡 / 随堂反馈'),
    ("backup", "备份与恢复", "备课作品 / 独立目录恢复 / 补回原图"),
    ("mywork", "我的备课", "草稿 / 历史 / 归档 / 回收站 / 找回作品 / 交付"),
    ("import", "导入资料", "Word / PDF / 图片"), ("settings", "模型设置", "API / 能力 / 预算"),
    ("environment", "本机检查", "版本 / 依赖 / 图标 / Office / 无网络检查"),
    ("progress", "题库进度", "标签 / 材料缺口"),
    ("help", "使用帮助", "教师使用手册 / 入门 / 快捷键 / 操作清单"),
) + tuple(("template:" + item.key, item.title, "教学模板 · " + " / ".join(item.tags)) for item in TEMPLATES)

ICON_PATHS = {
    "home": '<path d="M3 11 12 3l9 8M6 10v11h12V10M10 21v-7h4v7"/>',
    "library": '<rect x="4" y="3" width="5" height="18" rx="1"/><rect x="10" y="3" width="5" height="18" rx="1"/><path d="m17 4 4 16M5 7h3m3 0h3"/>',
    "paper": '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "student": '<circle cx="12" cy="7" r="4"/><path d="M4 21v-3a8 8 0 0 1 16 0v3M8 18h8"/>',
    "preparation": '<path d="M3 4h18v13H3zM8 22l4-5 4 5M8 9h8M8 13h5"/>',
    "templates": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "classroom": '<circle cx="12" cy="13" r="8"/><path d="M12 8v5l3 2M9 2h6M12 2v3"/>',
    "mywork": '<path d="M3 6h7l2 3h9v11H3zM3 6V4h7l2 2h7v3"/>',
    "settings": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2" fill="white"/><circle cx="16" cy="12" r="2" fill="white"/><circle cx="10" cy="18" r="2" fill="white"/>',
}


def studio_icon(key, size=22):
    drawing = ICON_PATHS.get(key, ICON_PATHS["templates"])
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="#247F58" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{drawing}</g></svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class CommandPalette(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.command = None
        self.setWindowTitle("快捷入口 · Ctrl+K")
        self.resize(640, 500)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.addWidget(section_title("搜索功能", "搜索功能或教学模板，按Enter打开；Esc返回。"))
        self.query = QLineEdit()
        self.query.setPlaceholderText("例如：出一份练习卷、讲评、改分、分组")
        self.query.setAccessibleName("快捷入口搜索")
        root.addWidget(self.query)
        self.results = QListWidget()
        self.results.setWordWrap(True)
        root.addWidget(self.results, 1)
        self.query.textChanged.connect(self.search)
        self.query.returnPressed.connect(self.choose)
        self.results.itemActivated.connect(lambda *_: self.choose())
        self.search("")
        self.query.setFocus()

    def search(self, query):
        self.results.clear()
        for key, title, tags in COMMANDS:
            if all(word in (title + " " + tags).casefold() for word in query.casefold().split()):
                item = QListWidgetItem(title + "  ·  " + tags)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setToolTip(title + '\n' + tags)
                self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def choose(self):
        item = self.results.currentItem()
        if item:
            self.command = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

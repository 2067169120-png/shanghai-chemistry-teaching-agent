"""Quick commands, native vector icons and in-app help."""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QTextBrowser, QVBoxLayout
from ..desktop_studio import TEMPLATES
from .components import section_title

COMMANDS = (
    ("home", "首页", "最近备课 / 继续编辑 / 选题篮"), ("library", "题库", "教材 / 知识点 / 原题"),
    ("paper", "组卷", "题篮 / 学生版 / 教师版"), ("student", "学生分析", "作答 / 诊断 / 练习"),
    ("preparation", "备课", "PPT / 教案 / 学习单"), ("templates", "教学模板", "场景 / 收藏 / 学习路径"),
    ("classroom", "课堂工具", "倒计时 / 点名 / 分组 / 动态平衡 / 出口检测"),
    ("backup", "备份与恢复", "备课作品 / 独立目录恢复 / 补回原图"),
    ("mywork", "我的备课", "草稿 / 历史 / 归档 / 回收站"),
    ("import", "导入资料", "Word / PDF / 图片"), ("settings", "模型设置", "API / 能力 / 预算"),
    ("environment", "本机检查", "版本 / 依赖 / 图标 / Office / 无网络检查"),
    ("progress", "题库进度", "标签 / 材料缺口"), ("help", "使用帮助", "入门 / 快捷键"),
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
        self.query.setPlaceholderText("例如：分组、PPT、原题、讲评")
        self.query.setAccessibleName("快捷入口搜索")
        root.addWidget(self.query)
        self.results = QListWidget()
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
                self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def choose(self):
        item = self.results.currentItem()
        if item:
            self.command = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用帮助 · 沪上化学智研台")
        self.resize(790, 650)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addWidget(section_title("从一次真实备课开始", "本地资料 → 选题 → 教案与课件 → 课堂反馈 → 再备课"))
        view = QTextBrowser()
        view.setOpenExternalLinks(False)
        view.setHtml('''<h3>1. 首次使用</h3><p>没有原题库也能打开。先在“设置”配置模型，或直接使用教学模板与离线课堂工具。旧资料保留在原目录；不要为升级重复导入。</p>
<h3>2. 从模板到教案与PPT</h3><p>首页“新建备课” → 填授课对象与材料 → 保存草稿 → 确认生成。需要教学结构时，从“教学模板”预览后带入。模板不会替换已填材料或原图，也不会自动调用AI。</p>
<h3>3. 选题与组卷</h3><p>题库按知识点与教材目录查找，保留完整主题及公共材料后入篮。组卷先核对学生/教师两版；Word优先原生提取，不再裁成整页图片。</p>
<h3>4. 课堂工具</h3><p>倒计时、点名、随机分组及动态平衡模型离线运行。模型是一级可逆A⇌B的示意，不是实测化学数据。出口检测由教师录入，可追加回备课资料。</p>
<h3>5. 找回工作</h3><p>“我的备课”按作品名称或原课题搜索，再按当前、归档、回收站分页显示。重命名只改显示名称；移入回收站不删文件，先还原再打开。生成中的任务暂不能整理。载入草稿前预览；查看历史结果不会自动重试收费。“题库进度”只读取本机现存目录，不使用历史统计充数。</p>
<h3>6. 恢复上次编辑</h3><p>备课表单约每2秒保存本机恢复副本，关闭时保存最后修改；重开恢复，不自动调用AI。正式“保存草稿”仍单独保留版本。新建空白备课会确认后清当前表单，不删除正式草稿和原图。恢复文件不可读时保留原件并暂停覆盖；异常退出仅恢复最后成功落盘的内容。</p>
<h3>7. 连接设置与本机检查</h3><p>设置页先填服务商、接口地址、模型、密钥与接口格式；高级预算默认收起，留空沿用各功能默认值。原预算不会因为折叠而清空。允许发送图片不代表模型已经通过识图测试；短文本测试不验证化学图或切题质量。右下角“本机检查”显示源码/打包模式、组件、图标、个人目录与Office安装发现，不调用模型。可复制或导出不含本机路径、教材与模型配置的报告。</p>
<h3>快捷键</h3><p>Ctrl+K：搜索功能与模板。Ctrl+1～5：首页、题库、组卷、学生分析、备课。F1：本帮助。Esc：关闭当前对话框或计时投屏。</p>
<h3>遇到问题</h3><p>没有题库结果：先检查实际资料目录和导入历史。扫描件无法识别：核对具体模型是否支持图片，文本联通不证明识图能力。旧VBS仍显示旧版：请改用“启动源码桌面版.cmd”。</p>''')
        root.addWidget(view, 1)
        close = QPushButton("关闭帮助")
        close.clicked.connect(self.accept)
        root.addWidget(close)

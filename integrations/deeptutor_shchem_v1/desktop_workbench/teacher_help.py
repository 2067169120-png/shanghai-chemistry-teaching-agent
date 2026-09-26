"""Searchable native task manual. Navigation is explicit and never creates work."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QBoxLayout, QDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSplitter, QTextBrowser, QVBoxLayout)
from ..desktop_teacher_scenarios import (SCENARIOS, ALLOWED_ROUTES, search_scenarios,
    checklist_text, scenario_html)


class _LocalText(QTextBrowser):
    def loadResource(self, resource_type, name):
        # The handbook has no image or external-resource dependency.
        return None


class TeacherHelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.owner = parent
        self.current_scenario = None
        self.setWindowTitle('教师使用手册 · 按教学任务查找')
        self.resize(980, 700)
        self.setMinimumSize(400, 540)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        title = QLabel('今天要完成什么？')
        title.setObjectName('PageTitle')
        root.addWidget(title)
        hint = QLabel('先看准备材料和完成检查，再打开工作区。阅读帮助不会上传资料、调用AI或改写草稿。')
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.query = QLineEdit()
        self.query.setPlaceholderText('搜索场景：备课、打印、讲评、改分、备份…')
        self.query.setAccessibleName('教师使用场景搜索')
        self.query.setClearButtonEnabled(True)
        root.addWidget(self.query)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.results = QListWidget()
        self.results.setAccessibleName('教学场景列表')
        self.results.setWordWrap(True)
        self.results.setMinimumSize(160, 100)
        self.results.setSpacing(5)
        self.view = _LocalText()
        self.view.setAccessibleName('当前场景操作说明')
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.setMinimumSize(160, 150)
        self.splitter.addWidget(self.results)
        self.splitter.addWidget(self.view)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([260, 660])
        root.addWidget(self.splitter, 1)
        self.status = QLabel('选择场景。清单只是操作提醒，不代表资料已通过核对。')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.copy_button = QPushButton('复制操作清单')
        self.copy_button.setObjectName('QuietButton')
        self.open_button = QPushButton('打开工作区')
        self.open_button.setObjectName('PrimaryAction')
        self.close_button = QPushButton('返回')
        self.close_button.setObjectName('QuietButton')
        for button in (self.copy_button, self.open_button, self.close_button):
            button.setAutoDefault(False)
            self.actions.addWidget(button)
        root.addLayout(self.actions)
        self.query.textChanged.connect(self.search)
        self.query.returnPressed.connect(self.results.setFocus)
        self.results.currentItemChanged.connect(self.show_scenario)
        self.results.itemActivated.connect(lambda *_: self.view.setFocus())
        self.copy_button.clicked.connect(self.copy_checklist)
        self.open_button.clicked.connect(self.open_workspace)
        self.close_button.clicked.connect(self.reject)
        self.search('')
        # Context selects only the explanation; it does not navigate or touch data.
        pages = getattr(parent, 'pages', {})
        stack = getattr(parent, 'stack', None)
        if stack is not None:
            active = next((key for key, page in pages.items() if page is stack.currentWidget()), None)
            for i in range(self.results.count()):
                scenario = self.results.item(i).data(Qt.ItemDataRole.UserRole)
                if scenario.route == active:
                    self.results.setCurrentRow(i)
                    break
        self.query.setFocus()

    def search(self, query):
        selected = self.current_scenario.key if self.current_scenario else None
        self.results.blockSignals(True)
        self.results.clear()
        index = 0
        for i, scenario in enumerate(search_scenarios(query)):
            item = QListWidgetItem(scenario.title + '\n' + scenario.destination)
            item.setData(Qt.ItemDataRole.UserRole, scenario)
            item.setToolTip(scenario.destination + '\n' + scenario.availability)
            self.results.addItem(item)
            if scenario.key == selected:
                index = i
        self.results.setCurrentRow(index if self.results.count() else -1)
        self.results.blockSignals(False)
        self.show_scenario(self.results.currentItem())

    def show_scenario(self, item, previous=None):
        self.current_scenario = item.data(Qt.ItemDataRole.UserRole) if item else None
        scenario = self.current_scenario
        self.copy_button.setEnabled(scenario is not None)
        can_route = bool(scenario and scenario.route in ALLOWED_ROUTES
            and scenario.route in getattr(self.owner, 'pages', {})
            and callable(getattr(self.owner, 'navigate', None)))
        self.open_button.setEnabled(can_route)
        if scenario is None:
            self.view.setPlainText('没有找到匹配场景。试试“备课”“打印”“讲评”或清空搜索；没有修改任何工作记录。')
            self.open_button.setText('打开工作区')
            self.status.setText('没有匹配项；上一条说明不会留作当前结果。')
            return
        self.view.setHtml(scenario_html(scenario))
        self.view.moveCursor(self.view.textCursor().MoveOperation.Start)
        names = {'preparation': '备课', 'library': '题库', 'student': '学生分析', 'mywork': '我的备课'}
        self.open_button.setText('打开' + names[scenario.route])
        self.status.setText(scenario.availability if can_route else
            '当前窗口仅提供说明；请按文中入口在工作台操作。')

    def copy_checklist(self):
        if self.current_scenario is not None:
            QApplication.clipboard().setText(checklist_text(self.current_scenario))
            self.status.setText('已复制操作清单；这不是已保存的草稿、正式评分或验收结果。')

    def open_workspace(self):
        scenario = self.current_scenario
        if (scenario is None or scenario.route not in ALLOWED_ROUTES
                or scenario.route not in getattr(self.owner, 'pages', {})
                or not callable(getattr(self.owner, 'navigate', None))):
            return
        try:
            self.owner.navigate(scenario.route)
        except Exception:
            self.status.setText('未能打开工作区。说明仍保留，请返回工作台按入口操作。')
            return
        self.accept()

    def resizeEvent(self, event):
        compact = event.size().width() < 680
        self.splitter.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
        self.results.setMaximumHeight(130 if compact else 16777215)
        self.actions.setDirection(QBoxLayout.Direction.TopToBottom if event.size().width() < 520
                                  else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)

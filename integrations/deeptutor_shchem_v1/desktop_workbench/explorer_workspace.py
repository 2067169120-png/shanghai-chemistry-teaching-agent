"""Resource-first controls for the existing native question explorer.

This is not another question database or basket. All selection, source reading,
numbering and export still use QuestionExplorerPage's existing services.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QInputDialog,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget)

from ..desktop_search_views import SearchViewStore
from .explorer_reader import text_label


STYLE = '''
QWidget#ResourceWorkspace { background: #f4f6f8; }
QFrame#ExplorerSidebar, QFrame#ResourceBasketRail {
  background: #ffffff; border: 1px solid #e0e6ec; border-radius: 8px;
}
QFrame#ExplorerQuestionCard { background: #ffffff; border: 1px solid #e0e6ec; border-radius: 8px; }
QLabel#ExplorerEyebrow { color: #27777c; font-weight: 600; }
QLabel#ExplorerExcerpt { color: #40515e; }
QFrame#ResourceBasketRail QListWidget { border: none; background: transparent; }
QFrame#ResourceBasketRail QListWidget::item { padding: 10px 4px; border-bottom: 1px solid #edf0f3; }
QFrame#ResourceBasketRail QListWidget::item:selected { background: #e9f4f3; color: #153f43; }
QTreeWidget#ExplorerFacetTree { border: none; background: white; }
QTreeWidget#ExplorerFacetTree::item { padding: 5px 2px; }
QTreeWidget#ExplorerFacetTree::item:selected { background: #e9f4f3; color: #153f43; }
QFrame#ExplorerBasketFooter { background: white; border: 1px solid #e0e6ec; border-radius: 8px; }
QPushButton#ResourcePrimary { background: #177c80; color: white; border: 1px solid #177c80; border-radius: 5px; padding: 7px 12px; }
QPushButton#ResourcePrimary:hover { background: #12676b; }
QPushButton#ResourcePrimary:disabled { background: #e6ecee; color: #829094; border-color: #e6ecee; }
'''


class ExplorerWorkspaceTools(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.store = SearchViewStore(page.facade.state_store)
        self._basket_signature = None
        self._records = []
        page.setObjectName('ResourceWorkspace')
        page.setStyleSheet(STYLE)
        page.query.setPlaceholderText('搜索题干、知识点或来源')
        page.query.setToolTip('仅搜索当前来源；答案不参与匹配。Ctrl+F定位搜索框。')
        page.filter_hint.setToolTip('同类标签满足任意一个，不同类须同时满足；不隐藏已选条件。')
        self.tag_search = QLineEdit()
        self.tag_search.setPlaceholderText('查找目录或标签')
        self.tag_search.setAccessibleName('查找目录或标签')
        self.tag_search.setClearButtonEnabled(True)
        self._tag_timer = QTimer(self)
        self._tag_timer.setSingleShot(True)
        self._tag_timer.setInterval(120)
        self._tag_timer.timeout.connect(self.filter_tree)
        self.tag_search.textChanged.connect(lambda: self._tag_timer.start())
        side = page.sidebar.layout()
        side.insertWidget(side.indexOf(page.tree), self.tag_search)

        self.views_bar = QWidget()
        layout = QHBoxLayout(self.views_bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.views = QComboBox()
        self.views.setMinimumWidth(0)
        self.views.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.views.setAccessibleName('常用筛选方案')
        self.views.activated.connect(self.restore_selected)
        layout.addWidget(self.views, 1)
        self.save_button = QPushButton('保存筛选')
        self.save_button.setObjectName('QuietButton')
        self.save_button.clicked.connect(self.save_current)
        self.delete_button = QPushButton('删除方案')
        self.delete_button.setObjectName('QuietButton')
        self.delete_button.clicked.connect(self.delete_selected)
        layout.addWidget(self.save_button); layout.addWidget(self.delete_button)
        page.layout().insertWidget(1, self.views_bar)

        self.rail = QFrame()
        self.rail.setObjectName('ResourceBasketRail')
        self.rail.setMinimumWidth(170)
        self.rail.setMaximumWidth(270)
        column = QVBoxLayout(self.rail)
        column.setContentsMargins(14, 16, 14, 12)
        column.setSpacing(10)
        self.heading = text_label('本次选题', 'CardTitle')
        column.addWidget(self.heading)
        self.rail_hint = text_label('按题篮顺序编排，保留题目公共材料。', 'MutedLabel')
        column.addWidget(self.rail_hint)
        self.basket_list = QListWidget()
        self.basket_list.setWordWrap(True)
        self.basket_list.setMinimumWidth(0)
        self.basket_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.basket_list.setAccessibleName('本次选题摘要')
        self.basket_list.itemDoubleClicked.connect(lambda _item: page.open_basket())
        column.addWidget(self.basket_list, 1)
        self.manage = QPushButton('调整题序与移出')
        self.manage.setObjectName('QuietButton')
        self.manage.clicked.connect(page.open_basket)
        self.preview = QPushButton('预览试卷排版')
        self.preview.setObjectName('ResourcePrimary')
        self.preview.clicked.connect(page.preview_requested)
        column.addWidget(self.manage); column.addWidget(self.preview)
        page.splitter.addWidget(self.rail)
        page.splitter.setStretchFactor(2, 0)
        page.splitter.setSizes([245, 690, 225])
        self.rail.hide()
        self.find_shortcut = QShortcut(QKeySequence.StandardKey.Find, page)
        self.find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.find_shortcut.activated.connect(self.focus_search)
        self.reload_views()
        self.refresh_basket(page.facade.basket())
        self.resize(page.width())

    def focus_search(self):
        self.page.query.setFocus()
        self.page.query.selectAll()

    def conditions(self):
        p = self.page
        return {'lane': p.scope.currentData(), 'query': p.query.text(),
                'filters': {k: sorted(v) for k, v in p.filters.items() if v},
                'curriculum': dict(p.curriculum),
                'curriculum_label': getattr(p, '_curriculum_label', '')}

    def reload_views(self, selected=None):
        try:
            records = self.store.list()
        except (ValueError, OSError, RuntimeError):
            self.page.result_summary.setText('筛选方案暂不能读取；仍可手动筛题。')
            records = []
        self._records = records
        with QSignalBlocker(self.views):
            self.views.clear()
            self.views.addItem('常用筛选 · 选择方案', None)
            for row in records:
                self.views.addItem(row['name'], row['id'])
            index = self.views.findData(selected)
            self.views.setCurrentIndex(max(0, index))
        self.delete_button.setEnabled(self.views.currentData() is not None)

    def save_current(self):
        name, ok = QInputDialog.getText(self.page, '保存筛选方案', '方案名称（例如：高三化学平衡二模题）')
        if not ok:
            return
        try:
            existing = next((r for r in self.store.list() if r['name'] == name.strip()), None)
            if existing and QMessageBox.question(self.page, '更新筛选方案',
                    '同名方案已存在。用当前条件更新它？') != QMessageBox.StandardButton.Yes:
                return
            identity = self.store.save(name, self.conditions(), replace_id=existing['id'] if existing else None)
        except (ValueError, OSError, RuntimeError) as error:
            QMessageBox.warning(self.page, '未能保存筛选方案', str(error))
            return
        self.reload_views(identity)
        self.page.result_summary.setText('筛选方案已保存；题篮和原题未改变。')

    def restore_selected(self, index):
        identity = self.views.itemData(index)
        self.delete_button.setEnabled(identity is not None)
        if identity is None:
            return
        # Re-read, so a deleted or updated view is not silently restored stale.
        try:
            row = next(r for r in self.store.list() if r['id'] == identity)
        except (StopIteration, ValueError, OSError, RuntimeError):
            self.reload_views()
            self.page.result_summary.setText('方案已变化，请重新选择。')
            return
        p, c = self.page, row['conditions']
        with QSignalBlocker(p.scope):
            p.scope.setCurrentIndex(p.scope.findData(c['lane']))
        # Reset lane-specific menu state without dispatching an intermediate query.
        started = p._started
        p._started = False
        try:
            p.scope_changed()
        finally:
            p._started = started
        p.query.setText(c['query'])
        p.filters = {k: set(v) for k, v in c['filters'].items()}
        p.curriculum = dict(c['curriculum'])
        p._curriculum_label = c['curriculum_label']
        p.search()

    def delete_selected(self):
        identity = self.views.currentData()
        if identity is None:
            return
        if QMessageBox.question(self.page, '删除筛选方案',
                '仅删除这个筛选方案，不删除题目或题篮内容。继续？') != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.remove(identity)
        except (ValueError, OSError, RuntimeError) as error:
            QMessageBox.warning(self.page, '未能删除方案', str(error))
            return
        self.reload_views()

    def filter_tree(self):
        text = self.tag_search.text().strip().casefold()
        tree = self.page.tree
        def visit(item, inherited=False):
            own = not text or text in item.text(0).casefold()
            checked = item.checkState(0) == Qt.CheckState.Checked
            children = [visit(item.child(i), inherited or own) for i in range(item.childCount())]
            visible = inherited or own or checked or any(children)
            item.setHidden(not visible)
            if text and any(children):
                item.setExpanded(True)
            return visible
        with QSignalBlocker(tree):
            for i in range(tree.topLevelItemCount()):
                visit(tree.topLevelItem(i))

    def refresh_basket(self, rows):
        signature = tuple((r.get('key'), r.get('title_zh'), r.get('source_zh')) for r in rows)
        if signature != self._basket_signature:
            self._basket_signature = signature
            self.basket_list.clear()
            for number, row in enumerate(rows, 1):
                item = QListWidgetItem(f"{number:02d}  {row.get('title_zh') or '未命名题目'}")
                item.setToolTip(row.get('source_zh') or '来源请在题篮中核对')
                self.basket_list.addItem(item)
        self.heading.setText(f'本次选题 · {len(rows)}项')
        self.rail_hint.setText('按题篮顺序编排，保留公共材料。' if rows else '展开完整题面后加入；这里显示本次已选题目。')
        self.preview.setEnabled(bool(rows))
        self.manage.setEnabled(bool(rows))

    def resize(self, width):
        self.rail.setVisible(width >= 1040)
        self.delete_button.setVisible(width >= 560)
        self.page.query.setMinimumWidth(0)

    def stop(self):
        self._tag_timer.stop()

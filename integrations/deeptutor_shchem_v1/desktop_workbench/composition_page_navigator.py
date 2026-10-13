"""Read-only navigation over the canvas pages actually visible to an audience."""
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QWidget)

from ..desktop_composition_render import _has_visible_content
from ..desktop_lesson_composition import TEACHER_ROLES


def visible_fragments(layout):
    if layout is None:
        return []
    return [fragment for fragment in layout['fragments']
            if fragment['frame']['page_id'] in layout['pages']
            and _has_visible_content(fragment['elements'])]


class CompositionPageNavigator(QWidget):
    pageRequested = Signal(str, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages = []
        self._page_items = {}
        self._fragment_items = {}
        bar = QHBoxLayout(self)
        bar.setContentsMargins(0, 0, 0, 0)
        self.audience = QLabel('学生画布')
        self.previous = QPushButton('上一页')
        self.previous.setAccessibleName('上一画布页')
        self.next = QPushButton('下一页')
        self.next.setAccessibleName('下一画布页')
        self.focus = QPushButton('专注页面')
        self.focus.setCheckable(True)
        self.focus.setAccessibleName('专注画布页面')
        self.focus.setToolTip('展开画布，便于阅读全文和图形；再次点击返回素材与来源。')
        self.pages = QComboBox()
        self.pages.setAccessibleName('画布页面导航')
        self.pages.setMinimumWidth(0)
        self.pages.setMinimumContentsLength(14)
        self.pages.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.pages.setToolTip('按本视图的页码和素材标题跳转；Word成品页码另行核对。')
        bar.addWidget(self.audience)
        bar.addWidget(self.previous)
        bar.addWidget(self.pages, 1)
        bar.addWidget(self.next)
        bar.addWidget(self.focus)
        self.tree = QTreeWidget()
        self.tree.setAccessibleName('画布页面目录')
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(0)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setToolTip('点击画布页或其中的素材；长题各续页分别列出。')
        self.pages.currentIndexChanged.connect(self._request_index)
        self.previous.clicked.connect(lambda: self._request_index(self.pages.currentIndex()-1))
        self.next.clicked.connect(lambda: self._request_index(self.pages.currentIndex()+1))
        self.tree.currentItemChanged.connect(self._request_item)
        self.set_layout(None, [], {}, student=True)

    def set_layout(self, layout, resources, kind_labels, *, student):
        self.audience.setText('学生画布' if student else '教师画布')
        self._pages = list(layout['pages']) if layout is not None else []
        self._page_items = {}
        self._fragment_items = {}
        by_id = {row['id']: row for row in resources}
        by_page = {page: [] for page in self._pages}
        fragments_visible = visible_fragments(layout)
        by_resource = {}
        for fragment in fragments_visible:
            by_page[fragment['frame']['page_id']].append(fragment)
            by_resource.setdefault(fragment['resource_id'], []).append(fragment)

        def fragment_label(fragment):
            row = by_id[fragment['resource_id']]
            parts = by_resource[row['id']]
            position = next(i for i, part in enumerate(parts) if part is fragment)+1
            suffix = f' · 本素材第{position}/{len(parts)}页' if len(parts) > 1 else ''
            if row['original']['kind'] == 'complete_question':
                visible_ids = {element.get('block_id') for element in fragment['elements']}
                roles = {block['role'] for block in row['blocks'] if block['id'] in visible_ids}
                scopes = []
                if roles-TEACHER_ROLES:
                    scopes.append('题面')
                if roles & TEACHER_ROLES:
                    scopes.append('讲评')
                if scopes:
                    suffix += ' · '+'／'.join(scopes)
            return row['title']+suffix

        with QSignalBlocker(self.pages), QSignalBlocker(self.tree):
            self.pages.clear()
            self.tree.clear()
            for index, page in enumerate(self._pages):
                fragments = by_page[page]
                titles = list(dict.fromkeys(fragment_label(f) for f in fragments))
                heading = f'第{index+1} / {len(self._pages)}页'
                summary = '、'.join(titles) if titles else '空白画布页'
                self.pages.addItem(heading+' · '+summary, page)
                self.pages.setItemData(index, summary, Qt.ItemDataRole.ToolTipRole)
                root = QTreeWidgetItem([heading])
                root.setToolTip(0, summary)
                root.setData(0, Qt.ItemDataRole.UserRole, (page, None, None))
                self.tree.addTopLevelItem(root)
                self._page_items[page] = root
                for fragment in fragments:
                    row = by_id[fragment['resource_id']]
                    frame = fragment['frame_index']
                    title = kind_labels[row['original']['kind']]+' · '+fragment_label(fragment)
                    child = QTreeWidgetItem([title])
                    child.setToolTip(0, title)
                    child.setData(0, Qt.ItemDataRole.UserRole, (page, row['id'], frame))
                    root.addChild(child)
                    self._fragment_items[(page, row['id'], frame)] = child
                if not fragments:
                    root.setText(0, heading+' · 空白画布页')
            if not self._pages:
                self.pages.addItem('无可见画布页', None)
        self.pages.setEnabled(bool(self._pages))
        self.tree.setEnabled(bool(self._pages))
        self._update_arrows()

    def set_current(self, page, resource_id=None, frame_index=None):
        with QSignalBlocker(self.pages), QSignalBlocker(self.tree):
            self.pages.setCurrentIndex(self.pages.findData(page))
            selected = self._fragment_items.get((page, resource_id, frame_index)) or self._page_items.get(page)
            self.tree.setCurrentItem(selected)
            if selected is not None:
                self._page_items[page].setExpanded(True)
                self.tree.scrollToItem(selected)
        self._update_arrows()

    def _update_arrows(self):
        index = self.pages.currentIndex()
        self.previous.setEnabled(bool(self._pages) and index > 0)
        self.next.setEnabled(bool(self._pages) and 0 <= index < len(self._pages)-1)

    def _request_index(self, index):
        if 0 <= index < len(self._pages):
            self.pageRequested.emit(self._pages[index], None, None)

    def _request_item(self, item, _previous=None):
        if item is not None:
            page, identity, frame = item.data(0, Qt.ItemDataRole.UserRole)
            if page in self._page_items:
                self.pageRequested.emit(page, identity, frame)

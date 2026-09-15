"""Native faceted question search: left taxonomy, right cards and a persistent cart."""
from __future__ import annotations

import json
from time import perf_counter

from PySide6.QtCore import Qt, QTimer, Signal, QSignalBlocker
from PySide6.QtWidgets import (QApplication, QBoxLayout, QComboBox, QFrame, QHBoxLayout, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..desktop_question_explorer import PERSONAL_LANES, core_results, entry_is_selected
from ..desktop_explorer_index import PersonalCatalogSession, check_cancelled
from .components import CardFrame
from .explorer_reader import PersonalQuestionReader, text_label
from .explorer_basket import ExplorerBasketDialog
from .library_detail import LibraryDetailDialog
from .word_question_filter_panel import _Flow


class QuestionCard(CardFrame):
    def __init__(self, entry, number, parent=None):
        super().__init__(parent)
        self.entry, self.ready = entry, False
        self.setObjectName("ExplorerQuestionCard")
        self.setMinimumWidth(0)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(20, 16, 20, 16)
        self.root.setSpacing(10)
        self.root.addWidget(text_label(f"{number:02d}  /  {entry['unit']}", "ExplorerEyebrow"))
        self.root.addWidget(text_label(entry["title"], "CardTitle"))
        self.root.addWidget(text_label(entry["subtitle"], "MutedLabel"))
        self.excerpt = text_label(entry["excerpt"] or "点击展开，查看完整题面和公共材料。", "ExplorerExcerpt")
        self.root.addWidget(self.excerpt)
        self.reader_host = QWidget()
        self.reader_layout = QVBoxLayout(self.reader_host)
        self.reader_layout.setContentsMargins(0, 0, 0, 0)
        self.reader_host.hide()
        self.root.addWidget(self.reader_host)
        self.note = text_label("", "MutedLabel")
        self.root.addWidget(self.note)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.open = QPushButton("展开题面与答案")
        self.open.setObjectName("QuietButton")
        self.add = QPushButton("＋ 加入选题篮")
        self.add.setEnabled(False)
        self.actions.addWidget(self.open)
        self.actions.addStretch(1)
        self.actions.addWidget(self.add)
        self.root.addLayout(self.actions)


class QuestionExplorerPage(QWidget):
    basket_changed = Signal(int)
    preparation_image_requested = Signal(dict)
    preparation_reference_requested = Signal(object)
    word_reference_requested = Signal(dict)
    preview_requested = Signal()
    assembly_requested = Signal()
    PAGE_SIZE = 8

    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self._loading = False
        self._epoch = 0
        self._reader_epoch = 0
        self._search_task = None
        self._detail_task = None
        self._reader = None
        self._active_card = None
        self._catalogs = {}
        self._sessions = {}
        self._started = False
        self._needs_reload = False
        self.load_metrics = {}
        self._base_facets = {}
        self.filters = {}
        self.curriculum = {}
        self._curriculum_catalog = None
        self._cursors = [None]
        self._page = 0
        self._next_cursor = None
        self._entries = []
        self.cards = []
        self._closed = False
        self._adding = set()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(160)
        self._search_timer.timeout.connect(self.search)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 12)
        root.setSpacing(12)
        heading = QHBoxLayout()
        self.page_heading = text_label("选题中心", "PageTitle")
        self.page_heading.setWordWrap(False)
        self.page_heading.setFixedHeight(40)
        heading.addWidget(self.page_heading, 1)
        self.basket_button = QPushButton("选题篮  0")
        self.basket_button.setObjectName("ExplorerBasketButton")
        self.basket_button.clicked.connect(self.open_basket)
        heading.addWidget(self.basket_button)
        root.addLayout(heading)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("ExplorerSidebar")
        self.sidebar.setMinimumWidth(0)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(12, 14, 12, 12)
        side.addWidget(text_label("标签筛选", "CardTitle"))
        self.scope = QComboBox()
        for label, lane in (("核心原卷题库", "master"), ("已细分原卷题", "wave1"),
                            ("公众号补充题", "supplemental"), ("本地 Word 题库", "word_native"),
                            ("个人图片题库", "visual_native")):
            self.scope.addItem(label, lane)
        try:
            saved_lane = facade.state_store.snapshot().get("question_explorer", {}).get("lane")
            saved_index = self.scope.findData(saved_lane)
            if saved_index >= 0:
                self.scope.setCurrentIndex(saved_index)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass  # No preference must not stop the reader opening.
        self.scope.setAccessibleName("选题来源")
        side.addWidget(self.scope)
        self.filter_hint = text_label("同类标签多选取并集，不同类同时满足。", "MutedLabel")
        side.addWidget(self.filter_hint)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setObjectName("ExplorerFacetTree")
        self.tree.setAccessibleName("知识点与教材标签菜单")
        self.tree.setIndentation(12)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setMinimumHeight(160)
        self.tree.setWordWrap(True)
        side.addWidget(self.tree, 1)
        self.advanced_button = QPushButton("Word原文与标签管理")
        self.advanced_button.setObjectName("QuietButton")
        self.advanced_button.clicked.connect(self.open_advanced)
        side.addWidget(self.advanced_button)
        self.reload_button = QPushButton("重新读取本地目录")
        self.reload_button.setObjectName("QuietButton")
        self.reload_button.clicked.connect(self.reload)
        side.addWidget(self.reload_button)
        self.diagnostics_button = QPushButton("复制加载记录")
        self.diagnostics_button.setObjectName("QuietButton")
        self.diagnostics_button.setToolTip("仅复制最近一次目录、筛选和题图耗时，不含题面、关键词或路径。")
        self.diagnostics_button.clicked.connect(self.copy_load_metrics)
        side.addWidget(self.diagnostics_button)
        self.splitter.addWidget(self.sidebar)
        right = QWidget()
        right.setMinimumWidth(0)
        work = QVBoxLayout(right)
        work.setContentsMargins(8, 0, 0, 0)
        work.setSpacing(10)
        self.search_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.filter_toggle = QPushButton("标签")
        self.filter_toggle.setObjectName("QuietButton")
        self.filter_toggle.setVisible(False)
        self.filter_toggle.clicked.connect(lambda: self.sidebar.setVisible(not self.sidebar.isVisible()))
        self.query = QLineEdit()
        self.query.setPlaceholderText("检索知识点、题面内容或来源；答案不参与匹配")
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName("题目关键词")
        self.query.returnPressed.connect(self.search)
        self.search_button = QPushButton("搜索题目")
        self.search_button.clicked.connect(self.search)
        for widget, stretch in ((self.filter_toggle, 0), (self.query, 1), (self.search_button, 0)):
            self.search_row.addWidget(widget, stretch)
        work.addLayout(self.search_row)
        chips_row = QHBoxLayout()
        self.chip_body = QWidget()
        self.chips = _Flow(self.chip_body)
        chips_row.addWidget(self.chip_body, 1)
        self.clear_button = QPushButton("清空条件")
        self.clear_button.setObjectName("QuietButton")
        self.clear_button.clicked.connect(self.clear_filters)
        chips_row.addWidget(self.clear_button)
        work.addLayout(chips_row)
        bar = QHBoxLayout()
        self.result_summary = text_label("正在连接本地目录…")
        bar.addWidget(self.result_summary, 1)
        self.previous = QPushButton("上一页")
        self.next = QPushButton("下一页")
        self.previous.setObjectName("QuietButton")
        self.next.setObjectName("QuietButton")
        self.previous.clicked.connect(lambda: self.turn_page(-1))
        self.next.clicked.connect(lambda: self.turn_page(1))
        bar.addWidget(self.previous)
        bar.addWidget(self.next)
        work.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("PageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_body = QWidget()
        self.list_layout = QVBoxLayout(self.list_body)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(12)
        self.scroll.setWidget(self.list_body)
        work.addWidget(self.scroll, 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([250, 850])
        root.addWidget(self.splitter, 1)
        self.footer = QFrame()
        self.footer.setObjectName("ExplorerBasketFooter")
        footer = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.footer)
        self.footer_layout = footer
        footer.setContentsMargins(16, 10, 16, 10)
        self.basket_label = text_label("当前未选题")
        footer.addWidget(self.basket_label, 1)
        self.view_basket = QPushButton("查看选题篮")
        self.view_basket.setObjectName("QuietButton")
        self.view_basket.clicked.connect(self.open_basket)
        self.preview_button = QPushButton("预览试卷排版")
        self.preview_button.clicked.connect(self.preview_requested)
        footer.addWidget(self.view_basket)
        footer.addWidget(self.preview_button)
        root.addWidget(self.footer)
        self.scope.currentIndexChanged.connect(self.scope_changed)
        self.tree.itemChanged.connect(self.facet_changed)
        self.tree.itemClicked.connect(self.curriculum_clicked)
        self.refresh_basket()
        self.advanced_button.setText("题图与标签管理" if self.scope.currentData() == "visual_native" else "Word原文与标签管理")

    def showEvent(self, event):
        self.refresh_basket()
        super().showEvent(event)
        if not self._started:
            self._started = True
            self.load_curriculum()
            self.search()
        elif self._needs_reload:
            self._needs_reload = False
            self.reload()

    def invalidate_catalogs(self):
        """Imports invalidate snapshots, including a cancelled cold reader."""
        self._catalogs.clear()
        self._sessions.clear()
        self._base_facets = {}
        if self.isVisible() and self._started:
            self.reload()
        else:
            self._needs_reload = True

    def copy_load_metrics(self):
        metrics = dict(self.load_metrics)
        metrics["word_images"] = list(getattr(self._reader, "load_metrics", []))
        QApplication.clipboard().setText(json.dumps(metrics, ensure_ascii=False, indent=2))
        self.diagnostics_button.setToolTip("已复制最近一次加载记录；不含题目、关键词、文件路径或密钥。")

    def _personal_source_links(self):
        row = QWidget()
        layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, row)
        layout.setContentsMargins(0, 0, 0, 0)
        for title, lane in (("切换到已导入 Word", "word_native"), ("切换到已导入图片题", "visual_native")):
            if lane == self.scope.currentData():
                continue
            button = QPushButton(title)
            button.setObjectName("QuietButton")
            button.clicked.connect(lambda _checked=False, target=lane: self.scope.setCurrentIndex(self.scope.findData(target)))
            layout.addWidget(button)
        return row

    def scope_changed(self, *_):
        lane = self.scope.currentData()
        try:
            def save(state):
                state["question_explorer"] = {"lane": lane}
            self.facade.state_store._update(save)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            self.scope.setToolTip("来源选择未能保存；当前找题仍可继续。")
        self.filters, self.curriculum, self._base_facets = {}, {}, {}
        self._page, self._cursors = 0, [None]
        self.advanced_button.setText('题图与标签管理' if self.scope.currentData() == "visual_native" else "Word原文与标签管理")
        self._render_tree()
        if self._started:
            self.search()

    def reload(self):
        self._catalogs.pop(self.scope.currentData(), None)
        self._sessions.pop(self.scope.currentData(), None)
        self._base_facets = {}
        self.load_curriculum()
        self.search()

    def load_curriculum(self):
        loader = getattr(self.facade, "curriculum_catalog", None)
        if callable(loader):
            self.tasks.submit("读取选题教材菜单", loader,
                              on_success=self.curriculum_loaded, on_failure=lambda _: None)

    def curriculum_loaded(self, value):
        if not self._closed and isinstance(value, dict):
            self._curriculum_catalog = value
            self._render_tree()

    def search(self, *_args, reset=True):
        if self._closed:
            return
        self._search_timer.stop()
        if reset:
            self._page, self._cursors = 0, [None]
        self._epoch += 1
        epoch = self._epoch
        if self._search_task:
            self.tasks.cancel(self._search_task)
        self.close_reader()
        self._clear_cards()
        self._loading = True
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.result_summary.setText("正在筛选本地题目…")
        self._render_chips()
        lane, query, page = self.scope.currentData(), self.query.text().strip(), self._page
        selection = {key: sorted(values) for key, values in self.filters.items() if values}
        selector = dict(self.curriculum)
        cursor = self._cursors[page] if lane not in PERSONAL_LANES else None
        session = self._sessions.setdefault(lane, PersonalCatalogSession(lane)) if lane in PERSONAL_LANES else None
        started = perf_counter()
        def run(report, cancelled):
            check_cancelled(cancelled)
            read_started = perf_counter()
            if lane in PERSONAL_LANES:
                loader = self.facade.word_question_catalog if lane == "word_native" else self.facade.personal_visual_questions
                index, metrics = session.get(loader, cancelled=cancelled)
                value = index.search(selection, query, page, self.PAGE_SIZE, cancelled=cancelled)
                value["performance"].update(metrics)
                return value, index.catalog
            kwargs = dict(scope=lane, query=query, limit=self.PAGE_SIZE)
            if selection:
                kwargs["filters"] = selection
            if cursor:
                kwargs["cursor"] = cursor
            kwargs.update(selector)
            value = core_results(self.facade.search_themes(**kwargs))
            check_cancelled(cancelled)
            value["performance"] = {"catalog_and_query_ms": round((perf_counter() - read_started) * 1000, 2)}
            return value, None
        def done(result):
            if self._closed or epoch != self._epoch:
                return
            value, catalog = result
            if catalog is not None:
                self._catalogs[lane] = catalog
            display_started = perf_counter()
            self._apply(value)
            self.load_metrics = {"schema": "question-page-timing-v1", "lane": lane,
                **value.get("performance", {}), "visible_cards": len(self.cards),
                "card_setup_ms": round((perf_counter() - display_started) * 1000, 2),
                "request_to_cards_ms": round((perf_counter() - started) * 1000, 2),
                "scope": "当前目录快照及卡片建立；题图读取另计，不等于全屏绘制或网络计时。"}
        self._search_task = self.tasks.submit_progress("筛选题目", run, on_success=done,
            on_failure=lambda message: self.failed(epoch, message))

    def failed(self, epoch, message):
        if self._closed or epoch != self._epoch:
            return
        self._loading = False
        self.result_summary.setText("本次目录未能读取")
        self.list_layout.addWidget(text_label(str(message) + "\n可切换左侧资料来源，或导入后点击重新读取。原题篮保持不变。", "StatusAttention"))
        self.list_layout.addWidget(self._personal_source_links())
        self.list_layout.addStretch(1)

    def _apply(self, result):
        self._loading = False
        self._entries = result["entries"]
        self._next_cursor = result["next_cursor"]
        for group, spec in result["facets"].items():
            old = self._base_facets.setdefault(group, {"label_zh": spec["label_zh"], "values": []})
            values = {row["value"]: row for row in old["values"]}
            values.update({row["value"]: row for row in spec["values"]})
            old["values"] = list(values.values())
        self._render_tree(result["facets"])
        self._render_chips()
        self.result_summary.setText(f"{result['total']} {result['count_unit']}  ·  第{self._page + 1}页")
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(bool(result["has_more"]))
        for index, entry in enumerate(self._entries, self._page * self.PAGE_SIZE + 1):
            card = QuestionCard(entry, index)
            card.open.clicked.connect(lambda _=False, c=card: self.expand(c))
            card.add.clicked.connect(lambda _=False, c=card: self.add(c))
            self.cards.append(card)
            self.list_layout.addWidget(card)
        if not self.cards:
            self.list_layout.addWidget(text_label("当前来源没有符合条件的题目\n可清空条件；刚导入资料后请重新读取，或切换到对应的个人题库。", "CardTitle"))
            self.list_layout.addWidget(self._personal_source_links())
        if result["warnings"]:
            self.list_layout.addWidget(text_label("\n".join(result["warnings"]), "MutedLabel"))
        self.list_layout.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)
        self.refresh_basket()
        if self.cards:
            self.expand(self.cards[0])

    def _clear_cards(self):
        self.cards, self._entries = [], []
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def _render_tree(self, current=None):
        # A page change with the same facets does not need hundreds of new Qt
        # items. Counts and selections remain part of the key, never just lane.
        tree_key = (self.scope.currentData(), id(self._curriculum_catalog),
                    repr(self._base_facets), repr(current),
                    tuple(sorted((k, tuple(sorted(v))) for k, v in self.filters.items())))
        if tree_key == getattr(self, "_last_tree_key", None):
            return
        self._last_tree_key = tree_key
        # Rebuilding result counts must not close the teacher's chapter path or
        # reopen a deliberately collapsed group. Branch labels are stable; leaf
        # count labels are not used as keys. Scope changes start a fresh menu.
        def branches(parent, path=()):
            for index in range(parent.childCount()):
                item = parent.child(index)
                item_path = (*path, item.text(0))
                if item.childCount():
                    yield item_path, item
                    yield from branches(item, item_path)

        lane = self.scope.currentData()
        same_lane = lane == getattr(self, "_tree_lane", None)
        branch_state = {path: item.isExpanded() for path, item in
                        branches(self.tree.invisibleRootItem())} if same_lane else {}
        scroll_position = self.tree.verticalScrollBar().value() if same_lane else 0
        expanded = {path[0] for path, opened in branch_state.items() if len(path) == 1 and opened}
        with QSignalBlocker(self.tree):
            self.tree.clear()
            if self.scope.currentData() in {"master", "supplemental"}:
                parent = QTreeWidgetItem(self.tree, ["教材章节"])
                parent.setExpanded(True)
                for volume in (self._curriculum_catalog or {}).get("volumes", []):
                    v = {"volume_id": volume["volume_id"]}
                    vi = QTreeWidgetItem(parent, [volume["volume_title"]])
                    vi.setData(0, Qt.ItemDataRole.UserRole, ("curriculum", v))
                    for chapter in volume.get("chapters", []):
                        c = {**v, "chapter_id": chapter["chapter_id"]}
                        ci = QTreeWidgetItem(vi, [chapter["chapter_title"]])
                        ci.setData(0, Qt.ItemDataRole.UserRole, ("curriculum", c))
                        for section in chapter.get("sections", []):
                            si = QTreeWidgetItem(ci, [section.get("section_title") or section.get("display_label_zh", "小节")])
                            si.setData(0, Qt.ItemDataRole.UserRole, ("curriculum", {**c, "section": section["section_key"]}))
                if not parent.childCount():
                    QTreeWidgetItem(parent, ["目录尚未连接"])
            order = ("K", "knowledge", "book", "chapter", "section", "grade", "exam", "item_type", "R", "D", "year", "region", "answer_status", "source", "teaching_use")
            for group in order:
                spec = self._base_facets.get(group)
                if not spec:
                    continue
                parent = QTreeWidgetItem(self.tree, [spec["label_zh"]])
                parent.setExpanded(parent.text(0) in expanded or group in {"K", "knowledge", "exam"})
                counts = {row["value"]: row.get("atomic_count") for row in (current or {}).get(group, {}).get("values", [])}
                for row in spec["values"]:
                    count = counts.get(row["value"], 0) if current is not None else row.get("atomic_count")
                    count = None if self.scope.currentData() in PERSONAL_LANES else count
                    label = row["label_zh"] + (f" ({count})" if count is not None else "")
                    child = QTreeWidgetItem(parent, [label])
                    child.setToolTip(0, row["label_zh"])
                    child.setData(0, Qt.ItemDataRole.UserRole, (group, row["value"]))
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.CheckState.Checked if row["value"] in self.filters.get(group, set()) else Qt.CheckState.Unchecked)
            for path, item in branches(self.tree.invisibleRootItem()):
                if path in branch_state:
                    item.setExpanded(branch_state[path])
            self.tree.doItemsLayout()
            self.tree.verticalScrollBar().setValue(scroll_position)
        self._tree_lane = lane
        self.filter_hint.setText("同类标签满足任意一个，不同类须同时满足。" + ("括号为当前匹配小问数。" if self.scope.currentData() not in PERSONAL_LANES else "使用已保存的来源标签。"))

    def facet_changed(self, item, _column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] == "curriculum":
            return
        group, value = data
        chosen = self.filters.setdefault(group, set())
        if item.checkState(0) == Qt.CheckState.Checked:
            chosen.add(value)
        else:
            chosen.discard(value)
        self._search_timer.start()

    def curriculum_clicked(self, item, _column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[0] == "curriculum":
            self.curriculum = dict(data[1])
            self._curriculum_label = item.text(0)
            self.search()

    def _render_chips(self):
        while self.chips.count():
            item = self.chips.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        for group, selected in self.filters.items():
            names = {row["value"]: row["label_zh"] for row in self._base_facets.get(group, {}).get("values", [])}
            for value in sorted(selected):
                self._chip(names.get(value, value), lambda g=group, v=value: self.remove_filter(g, v))
        if self.curriculum:
            self._chip(getattr(self, "_curriculum_label", "已限定教材章节"), self.clear_curriculum)
        if not any(self.filters.values()) and not self.curriculum:
            hint = text_label("全部标签 · 请选择左侧条件", "MutedLabel")
            hint.setWordWrap(False)
            hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            self.chips.addWidget(hint)

    def _chip(self, title, action):
        button = QPushButton(title + "  ×")
        button.setObjectName("ExplorerFilterChip")
        button.clicked.connect(lambda _=False: action())
        self.chips.addWidget(button)

    def remove_filter(self, group, value):
        self.filters[group].discard(value)
        self.search()

    def clear_curriculum(self):
        self.curriculum = {}
        self.search()

    def clear_filters(self):
        self.filters, self.curriculum = {}, {}
        self.query.clear()
        self.search()

    def turn_page(self, direction):
        if self._loading or self._page + direction < 0:
            return
        if direction > 0:
            if self.scope.currentData() not in PERSONAL_LANES and not self._next_cursor:
                return
            self._cursors = self._cursors[:self._page + 1] + [self._next_cursor]
        self._page += direction
        self.search(reset=False)

    def close_reader(self):
        self._reader_epoch += 1
        if self._detail_task:
            self.tasks.cancel(self._detail_task)
        if self._reader:
            self._reader.close()
            self._reader.deleteLater()
            self._reader = None
        if self._active_card:
            self._active_card.reader_host.hide()
            self._active_card.excerpt.show()
            self._active_card.open.setText("展开题面与答案")
            self._active_card.ready = False
        self._active_card = None

    def expand(self, card):
        same = card is self._active_card
        self.close_reader()
        if same:
            self.refresh_basket()
            return
        self._active_card = card
        self.refresh_basket()
        epoch = self._reader_epoch
        entry = card.entry
        card.note.setText("正在加载完整题面…")
        if entry["lane"] in PERSONAL_LANES:
            reader = PersonalQuestionReader(entry, self.facade, self.tasks, card.reader_host)
            self.attach_reader(card, reader)
            reader.readiness_changed.connect(lambda ready: self.reader_ready(epoch, card, ready))
            self.reader_ready(epoch, card, reader.ready_to_select)
        else:
            def ready(detail):
                if self._closed or epoch != self._reader_epoch or self._active_card is not card:
                    return
                if detail.key != entry["key"] or detail.scope != entry["lane"]:
                    failed("题目已变化，请重新检索。")
                    return
                reader = LibraryDetailDialog(detail, self.tasks, self.facade.library_image, card.reader_host,
                    answer_image_loader=getattr(self.facade, "library_answer_image", None), embedded=True)
                self.attach_reader(card, reader)
                reader.preview_readiness_changed.connect(lambda ok, _message: self.reader_ready(epoch, card, ok))
                reader.preparation_image_requested.connect(self.preparation_image_requested)
                self.reader_ready(epoch, card, reader.preview_readiness[0])
            def failed(message):
                if epoch == self._reader_epoch and not self._closed:
                    card.note.setText(str(message))
            self._detail_task = self.tasks.submit("读取完整题面", lambda: self.facade.library_theme_detail(entry["payload"]),
                                                   on_success=ready, on_failure=failed)

    def attach_reader(self, card, reader):
        self._reader = reader
        reader.setMinimumWidth(0)
        reader.setFixedHeight(220 if isinstance(reader, PersonalQuestionReader) and not reader.images else 340)
        card.reader_layout.addWidget(reader)
        card.reader_host.show()
        card.excerpt.hide()
        card.open.setText("收起题面")
        reader.show()

    def reader_ready(self, epoch, card, ready):
        if self._closed or epoch != self._reader_epoch or card is not self._active_card:
            return
        card.ready = ready
        card.note.setText("题面与公共材料已加载。" if ready else "题面仍在加载或有原图缺口，可到原文与标签入口核对。")
        self.refresh_basket()

    def add(self, card):
        if not card.ready or card is not self._active_card or card.entry["key"] in self._adding:
            return
        entry = card.entry
        if entry_is_selected(entry, self.facade.basket()):
            return
        card.add.setEnabled(False)
        self._adding.add(entry["key"])
        row = entry["payload"]
        lane = entry["lane"]
        def run():
            if lane == "word_native":
                return self.facade.add_word_questions_to_basket([{"key": row["key"], "revision": row["revision"], "points": 2}])
            if lane == "visual_native":
                return self.facade.add_personal_visual_questions_to_basket([{key: row[key] for key in ("key", "revision", "batch_id")}])
            return self.facade.add_theme_to_basket(row)
        def done(count):
            self._adding.discard(entry["key"])
            if not self._closed:
                self.refresh_basket()
                self.basket_changed.emit(count)
        def failed(message):
            self._adding.discard(entry["key"])
            if not self._closed:
                self.result_summary.setText(str(message))
                self.refresh_basket()
        self.tasks.submit("加入统一选题篮", run, on_success=done, on_failure=failed)

    def refresh_basket(self, *_):
        try:
            basket = self.facade.basket()
        except Exception:
            self.basket_label.setText("题篮暂不能读取")
            self.preview_button.setEnabled(False)
            return
        count = len(basket)
        self.basket_button.setText(f"选题篮  {count}")
        self.basket_label.setText(f"已选 {count} 项 · 完整题目/主题" if count else "尚未选题 · 展开题面后加入")
        self.preview_button.setEnabled(bool(count))
        for card in self.cards:
            selected = entry_is_selected(card.entry, basket)
            card.add.setText("已在题篮" if selected else "＋ 加入选题篮")
            card.add.setEnabled(card.ready and not selected and card.entry["key"] not in self._adding)

    def open_basket(self):
        dialog = ExplorerBasketDialog(self.facade, self.window())
        dialog.basket_changed.connect(self.refresh_basket)
        dialog.basket_changed.connect(self.basket_changed)
        dialog.preview_requested.connect(self.preview_requested)
        dialog.edit_requested.connect(self.assembly_requested)
        dialog.exec()
        dialog.deleteLater()
        self.refresh_basket()

    def open_advanced(self):
        lane = self.scope.currentData()
        if lane == "visual_native":
            from .personal_visual_question_dialog import PersonalVisualQuestionDialog
            dialog = PersonalVisualQuestionDialog(self.facade, self.tasks, self.window())
        else:
            from .word_question_dialog import WordQuestionDialog
            dialog = WordQuestionDialog(self.facade, self.tasks, self.window())
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.preparation_reference is not None:
            self.word_reference_requested.emit(dialog.preparation_reference)
        dialog.deleteLater()
        self.refresh_basket()
        self.basket_changed.emit(len(self.facade.basket()))
        if lane in PERSONAL_LANES:
            self.reload()

    def resizeEvent(self, event):
        narrow = event.size().width() < 700
        mobile = event.size().width() < 480
        self.filter_toggle.setVisible(narrow)
        if narrow != getattr(self, "_narrow", None):
            self._narrow = narrow
            self.sidebar.setVisible(not narrow)
        self.splitter.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
        self.search_row.setDirection(QBoxLayout.Direction.TopToBottom if mobile else QBoxLayout.Direction.LeftToRight)
        self.footer_layout.setDirection(QBoxLayout.Direction.TopToBottom if mobile else QBoxLayout.Direction.LeftToRight)
        for card in self.cards:
            card.actions.setDirection(QBoxLayout.Direction.TopToBottom if mobile else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)

    def closeEvent(self, event):
        self._closed = True
        self._epoch += 1
        self._search_timer.stop()
        if self._search_task:
            self.tasks.cancel(self._search_task)
        self.close_reader()
        super().closeEvent(event)

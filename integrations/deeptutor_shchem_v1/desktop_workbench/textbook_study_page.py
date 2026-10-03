"""Volume/chapter/section navigation, candidate evidence and intact local books."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..desktop_textbook_workspace import _digest
from .components import page_scroll, section_title


class _CandidateSourceFacade:
    def __init__(self, facade):
        self.preparation_textbook_source = facade.textbook_candidate_source


class TextbookStudyPage(QWidget):
    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.catalog = None
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        outer.addWidget(page_scroll(content))
        root.addWidget(section_title("教材研读", "按册、章、节查看知识候选，回到对应原页核对。知识摘要、原书是否可读与教师审核分别记录。"))
        actions = self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.query = QLineEdit()
        self.query.setPlaceholderText("查找知识点、章节或化学内容")
        self.query.setAccessibleName("教材知识候选搜索")
        self.query.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.query.textChanged.connect(self._render)
        actions.addWidget(self.query, 1)
        self.import_button = QPushButton("导入整本教材…")
        self.import_button.clicked.connect(self.open_import)
        actions.addWidget(self.import_button)
        self.sync_button = QPushButton("导入知识候选")
        self.sync_button.setObjectName("QuietButton")
        self.sync_button.clicked.connect(self._import_candidates)
        actions.addWidget(self.sync_button)
        self.compact_actions = QPushButton("教材操作…")
        menu = QMenu(self.compact_actions)
        menu.addAction("导入整本教材…", self.open_import)
        self.sync_action = menu.addAction("导入知识候选", self._import_candidates)
        menu.addAction("备份教材与候选…", self._backup)
        menu.addAction("恢复到新目录…", self._restore)
        menu.aboutToShow.connect(lambda: self.sync_action.setEnabled(self.sync_button.isEnabled()))
        self.compact_actions.setMenu(menu)
        self.compact_actions.hide()
        actions.addWidget(self.compact_actions)
        root.addLayout(actions)
        backup_actions = self.backup_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.backup_buttons = []
        for title, slot in (("备份教材与候选…", self._backup), ("恢复到新目录…", self._restore)):
            button = QPushButton(title)
            button.setObjectName("QuietButton")
            button.clicked.connect(slot)
            backup_actions.addWidget(button)
            self.backup_buttons.append(button)
        backup_actions.addStretch()
        root.addLayout(backup_actions)
        self.status = QLabel("正在读取教材知识候选…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setMinimumHeight(240)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["册 / 章 / 节 / 知识点"])
        self.tree.setAccessibleName("教材目录与知识候选")
        self.tree.setMinimumWidth(120)
        self.tree.currentItemChanged.connect(self._selected)
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_source())
        self.splitter.addWidget(self.tree)
        detail = QWidget()
        box = QVBoxLayout(detail)
        box.setContentsMargins(8, 0, 0, 0)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setAccessibleName("教材知识摘要、页码证据与审核状态")
        box.addWidget(self.evidence, 1)
        self.open_button = QPushButton("查看知识点原页")
        self.open_button.clicked.connect(self.open_source)
        self.section_button = QPushButton("阅读本节…")
        self.section_button.setObjectName("QuietButton")
        self.section_button.clicked.connect(lambda: self.open_source(section=True))
        box.addWidget(self.open_button)
        box.addWidget(self.section_button)
        self.splitter.addWidget(detail)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        root.addWidget(self.splitter, 1)
        self._selected()
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        loader = getattr(self.facade, "textbook_study_catalog", None)
        if self._loading:
            return
        if not callable(loader):
            self.status.setText("当前资料目录尚未提供教材知识候选，可从导入资料继续。")
            self.sync_button.setEnabled(False)
            return
        self._loading = True
        self.tasks.submit("读取教材研读目录", loader, on_success=self._ready, on_failure=self._failed, origin_route="textbooks")

    def _ready(self, catalog):
        self._loading = False
        self.catalog = catalog
        self.status.setText(f"{catalog['count']}条知识候选 · {len(catalog['books'])}份已导入原书 · "
            + ("已保存到个人资料" if catalog['imported'] else "当前为仓库候选，可导入个人资料")
            + "。全部待教师核对，阅读与导入不会改变审核状态。")
        self._render()

    def _failed(self, message):
        self._loading = False
        self.status.setText(message)

    def _render(self, *_args):
        current = self.tree.currentItem()
        selected = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        selected_id = (selected or {}).get("concept_id")
        self.tree.clear()
        if self.catalog is None:
            return
        query = self.query.text().casefold().split()
        if self.catalog["books"]:
            shelf = QTreeWidgetItem(self.tree, ["已导入完整原书"])
            for book in self.catalog["books"].values():
                if query and not all(word in book["source_name"].casefold() for word in query):
                    continue
                item = QTreeWidgetItem(shelf, [book["source_name"] + f" · {book['page_count']}页"])
                item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "book", **book})
            shelf.setExpanded(True)
        nodes = {}
        visible = 0
        volume_order = {key: i for i, key in enumerate(("TB-M1", "TB-M2", "TB-E1", "TB-E2", "TB-E3"))}
        rows = sorted(self.catalog["rows"], key=lambda row: (
            volume_order.get(row["curriculum"].get("volume_id"), 99),
            row["curriculum"].get("chapter_id") or "", row["curriculum"].get("section_key") or "", row["concept_id"]))
        for row in rows:
            curriculum = row["curriculum"]
            haystack = " ".join(str(value) for value in [row["title"], row["summary"], *curriculum.values()]).casefold()
            if not all(word in haystack for word in query):
                continue
            visible += 1
            keys = [curriculum.get("volume_id") or "unknown-volume",
                    curriculum.get("chapter_id") or "unknown-chapter",
                    curriculum.get("section_key") or curriculum.get("supplement_node_key") or "unknown-section"]
            labels = [curriculum.get("volume_title") or "册别待核对",
                      curriculum.get("chapter_title") or "章节待核对",
                      curriculum.get("section_title") or "其他教学单元（范围待核对）"]
            parent = self.tree
            for depth in range(3):
                key = tuple(keys[:depth + 1])
                if key not in nodes:
                    nodes[key] = QTreeWidgetItem(parent, [labels[depth]])
                    nodes[key].setExpanded(bool(query) or depth == 0)
                parent = nodes[key]
            item = QTreeWidgetItem(parent, [row["title"] + " · 待教师核验"])
            item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "candidate", "row": row,
                "concept_id": row["concept_id"], "revision": _digest(row)})
            if row["concept_id"] == selected_id:
                self.tree.setCurrentItem(item)
        self.tree.setToolTip(f"当前筛选{visible}条知识候选")
        self._selected()

    def _selection(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _selected(self, *_args):
        selected = self._selection()
        self.open_button.setEnabled(False)
        self.section_button.setEnabled(False)
        if not selected:
            self.evidence.setPlainText("选择左侧知识点查看摘要与证据；选择已导入原书可连续阅读完整PDF。")
            return
        if selected["kind"] == "book":
            self.open_button.setText("阅读整本原书")
            self.open_button.setEnabled(True)
            self.evidence.setPlainText(selected["source_name"] + f"\nPDF文件共{selected['page_count']}页"
                + "\n原文件已保存；印刷页码、目录和化学内容待核对。\n来源内容校验：" + selected["source_sha256"])
            return
        row = selected["row"]
        source = row["source"]
        pages = "、".join(map(str, source.get("pdf_pages") or [])) or "待核对"
        printed = "、".join(map(str, source.get("printed_pages") or [])) or "待核对"
        native = self.catalog.get("native_options", {}).get(row["concept_id"])
        book = self.catalog["books"].get(source["sha256"])
        self.open_button.setText("查看知识点原页")
        self.open_button.setEnabled(bool(native or book))
        self.section_button.setEnabled(bool(native))
        self.evidence.setPlainText(row["title"] + "\n\n知识摘要（候选）：\n" + row["summary"]
            + "\n\n来源：" + str(source.get("title", "待核对"))
            + "\nPDF文件页序：" + pages + "\n印刷页码：" + printed
            + "\n证据标识：" + "、".join(source.get("evidence_refs", []))
            + "\n来源内容校验：" + source["sha256"]
            + "\n\n原页：" + ("可打开并核对来源版本" if native or book else "对应原PDF尚未导入")
            + "\n审核：候选，未获教师审核\n本摘要不能代替教材原句，阅读不会提升教学或发布权限。")

    def open_source(self, *, section=False):
        selected = self._selection()
        if not selected:
            return
        from .textbook_source_dialog import TextbookSourceDialog
        if selected["kind"] == "book":
            if section:
                return
            concept = {"concept_id": selected["source_id"], "revision": selected["source_sha256"]}
            facade, mode = self.facade, "book"
        else:
            row = selected["row"]
            native = self.catalog.get("native_options", {}).get(row["concept_id"])
            if section and not native:
                return
            if native:
                concept, facade = native, self.facade
            else:
                if row["source"]["sha256"] not in self.catalog["books"]:
                    self.status.setText("请先导入该知识点对应的原PDF，再核对原页。")
                    return
                concept, facade = selected, _CandidateSourceFacade(self.facade)
            mode = "section" if section else "concept"
        dialog = TextbookSourceDialog(facade, concept, self, tasks=self.tasks, reading_mode=mode)
        if isinstance(facade, _CandidateSourceFacade):
            # A derived shelf has no activated excerpt/reference contract.
            dialog.excerpt_button.hide()
        dialog.exec()
        dialog.deleteLater()

    def open_import(self):
        from .textbook_import_dialog import TextbookImportDialog
        dialog = TextbookImportDialog(self.facade, self.tasks, self)
        dialog.imported.connect(self.refresh)
        dialog.exec()
        dialog.deleteLater()

    def _import_candidates(self):
        self.sync_button.setEnabled(False)
        def saved(_result):
            self.sync_button.setEnabled(True)
            self.refresh()
        def failed(message):
            self.sync_button.setEnabled(True)
            self.status.setText(message)
        self.tasks.submit("导入教材知识候选", lambda: self.facade.textbook_workspace().import_candidates(),
            on_success=saved, on_failure=failed, origin_route="textbooks")

    def _backup(self):
        destination, _filter = QFileDialog.getSaveFileName(self, "保存教材备份（使用新文件名）", "教材与知识候选.zip", "ZIP备份 (*.zip)")
        if destination:
            self.tasks.submit("备份教材与知识候选", lambda: self.facade.textbook_workspace().export_bundle(destination),
                on_success=lambda result: self.status.setText(f"教材备份已保存：{result['books']}份原书、{result['files']}个文件。"),
                on_failure=self.status.setText, origin_route="textbooks")

    def _restore(self):
        from pathlib import Path

        from ..desktop_textbook_workspace import TextbookWorkspaceService
        bundle, _filter = QFileDialog.getOpenFileName(self, "选择教材备份", "", "ZIP备份 (*.zip)")
        if not bundle:
            return
        parent = QFileDialog.getExistingDirectory(self, "选择恢复目录的上级文件夹")
        if not parent:
            return
        name, accepted = QInputDialog.getText(self, "新建独立恢复目录", "目录名称", text="教材恢复副本")
        if not accepted:
            return
        if not name.strip() or name in {".", ".."} or any(char in name for char in '/\\:'):
            self.status.setText("请填写不含路径分隔符的新目录名称。")
            return
        destination = Path(parent) / name.strip()
        self.tasks.submit("恢复教材到独立目录", lambda: TextbookWorkspaceService.restore_bundle(bundle, destination),
            on_success=lambda _result: self.status.setText("教材已恢复到：" + str(destination) + "。当前资料保持，可分别核对恢复副本。"),
            on_failure=self.status.setText, origin_route="textbooks")

    def resizeEvent(self, event):
        compact = event.size().width() < 720
        self.actions.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        self.import_button.setVisible(not compact)
        self.sync_button.setVisible(not compact)
        self.compact_actions.setVisible(compact)
        for button in self.backup_buttons:
            button.setVisible(not compact)
        self.splitter.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
        self.splitter.setMinimumHeight(320 if compact else 240)
        super().resizeEvent(event)

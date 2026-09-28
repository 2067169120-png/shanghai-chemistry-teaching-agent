"""Explicit new/open entry for saved papers, using the existing mixed editor."""
from __future__ import annotations

from PySide6.QtCore import Signal, QSize
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton

from .assembly_page import MixedPaperPanel, _label
from .paper_composer import PaperComposerModel


class IndependentPaperWorkspace(QWidget):
    preparation_requested = Signal(dict)

    def __init__(self, facade, tasks, parent=None, *, panel_type=MixedPaperPanel):
        super().__init__(parent)
        self.facade, self.tasks, self.panel_type = facade, tasks, panel_type
        self.library = facade.independent_paper_library()
        self._mixed_panel = None
        self._busy = False
        self._closed = False
        self._epoch = 0
        self._basket_count = 0
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        self.scope_label = _label("公共题篮与当前卷分别保存。", "MutedLabel")
        self.scope_label.setAccessibleName("公共题篮与独立当前卷范围")
        layout.addWidget(self.scope_label)
        selection = QHBoxLayout()
        self.saved_papers = QComboBox()
        self.saved_papers.setMinimumWidth(0)
        self.saved_papers.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.saved_papers.setMinimumContentsLength(3)
        self.saved_papers.setAccessibleName("选择已保存的独立卷或旧稿")
        selection.addWidget(self.saved_papers, 1)
        self.refresh_button = QPushButton("重读列表")
        self.refresh_button.clicked.connect(self.refresh)
        selection.addWidget(self.refresh_button)
        layout.addLayout(selection)
        actions = QHBoxLayout()
        self.open_button = QPushButton("继续所选卷")
        self.new_button = QPushButton("从题篮新建")
        self.new_button.setObjectName("PrimaryAction")
        self.open_button.setAccessibleName("继续所选保存卷；旧稿会保留并另存独立卷")
        self.new_button.setAccessibleName("按公共题篮顺序新建独立卷，保留已有卷")
        self.open_button.clicked.connect(self._open)
        self.new_button.clicked.connect(lambda: self._create())
        actions.addWidget(self.open_button, 1)
        actions.addWidget(self.new_button, 1)
        layout.addLayout(actions)
        self.empty = _label(
            "选择一份保存卷继续，或从公共题篮新建。\n\n"
            "新卷保存本次题目顺序与来源引用。之后增删、调序或清空题篮，都不会改动此卷。\n\n"
            "旧稿继续时另存独立卷，保留旧记录；来源缺失或变化时会提示核对。", "MutedLabel")
        layout.addWidget(self.empty, 1)
        self.status = _label("尚未打开当前卷。", "MutedLabel")
        layout.addWidget(self.status)
        self.saved_papers.currentIndexChanged.connect(self._actions)
        for button in (self.open_button, self.new_button, self.refresh_button):
            button.setAutoDefault(False)
        self.refresh()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def _message(self, text):
        (self._mixed_panel.status if self._mixed_panel is not None else self.status).setText(text)

    def refresh(self, *_args, selected=None):
        selected = selected or self.saved_papers.currentData()
        try:
            rows = self.library.list()
            self._basket_count = len(self.facade.basket())
        except Exception:
            self.saved_papers.clear()
            self._message("保存卷列表暂不能读取，原记录保留；请重读列表。")
            self._actions()
            return
        self.saved_papers.blockSignals(True)
        self.saved_papers.clear()
        self.saved_papers.addItem("选择保存卷…", None)
        for row in rows:
            kind = "旧稿·继续时另存" if row["legacy"] else "保存卷"
            count = f" · {row['count']} 段" if row["count"] is not None else ""
            self.saved_papers.addItem(f"{row['title']}{count} · {kind}", row["id"])
        if selected:
            self.saved_papers.setCurrentIndex(max(0, self.saved_papers.findData(selected)))
        self.saved_papers.blockSignals(False)
        self._scope()
        self._actions()

    def _scope(self):
        if self._mixed_panel is None:
            text = f"公共题篮 {self._basket_count} 项 · 尚未打开当前卷"
        else:
            panel = self._mixed_panel
            current = "待核对" if panel._restore_failed else f"{len(panel.model.order)} 段"
            text = f"公共题篮 {self._basket_count} 项 · 当前卷 {current}\n此卷独立保存，题篮变化不会增删本卷。"
        self.scope_label.setText(text)

    def _actions(self, *_args):
        idle = not self._busy and not self._closed
        self.open_button.setEnabled(idle and self.saved_papers.currentData() is not None)
        self.new_button.setEnabled(idle and self._basket_count > 0)
        self.refresh_button.setEnabled(idle)
        self.saved_papers.setEnabled(idle)

    def _run(self, operation, *, preview=False):
        if self._busy or self._closed:
            return
        self._busy = True
        self._epoch += 1
        epoch = self._epoch
        self._actions()
        self._message("正在核对保存卷的完整来源…")

        def ready(session):
            if self._closed or epoch != self._epoch:
                return
            self._busy = False
            old = self._mixed_panel
            if old is not None:
                old.close()
                self.layout().removeWidget(old)
                old.deleteLater()
            panel = self.panel_type(session, self.tasks, PaperComposerModel(), self)
            self._mixed_panel = panel
            self.layout().addWidget(panel, 1)
            self.empty.hide()
            self.status.hide()
            panel.load_finished.connect(lambda success: self._loaded(panel, success, preview))
            panel.scope_changed.connect(self._scope)
            panel.draft_saved.connect(lambda: self._saved_label(panel))
            panel.title.editingFinished.connect(lambda: self.refresh(selected=session.paper_id))
            panel.load()
            self.refresh(selected=session.paper_id)

        def failed(message):
            if self._closed or epoch != self._epoch:
                return
            self._busy = False
            self._message(str(message) or "保存卷未能打开，旧稿保留；请重新读取。")
            self.refresh()

        try:
            def execute():
                from ..desktop_state import DesktopStateError
                from ..desktop_facade import DesktopFacadeError
                try:
                    return operation()
                except DesktopStateError as exc:
                    raise DesktopFacadeError("independent_paper_blocked", str(exc)) from exc
            self.tasks.submit("打开独立当前卷", execute, on_success=ready, on_failure=failed)
        except Exception as exc:
            failed(str(exc))

    def _loaded(self, panel, success, preview):
        if panel is not self._mixed_panel or self._closed:
            return
        self._scope()
        if success and preview:
            panel._preview_request()

    def _saved_label(self, panel):
        """Update only the committed row, preserving selection and edit position."""
        if panel is not self._mixed_panel or panel._draft_session.current is None:
            return
        payload = panel._draft_session.current.record["payload"]
        index = self.saved_papers.findData(panel.facade.paper_id)
        if index >= 0:
            self.saved_papers.setItemText(index,
                f"{payload['settings_ui'].get('title') or '未命名卷'} · {len(payload['order'])} 段 · 保存卷")

    def _create(self, *, preview=False):
        self._run(self.library.create, preview=preview)

    def _open(self):
        identity = self.saved_papers.currentData()
        if identity is None:
            return
        from ..desktop_independent_paper import PREFIX
        self._run(lambda: self.library.open(identity) if identity.startswith(PREFIX)
                  else self.library.create(legacy_id=identity))

    def update_basket_count(self, _count=None):
        # Basket navigation never reloads or rebuilds an already opened paper.
        try:
            self._basket_count = len(self.facade.basket())
            self._scope()
        except Exception:
            self._message("公共题篮暂不能读取；当前卷保留。可继续从保存卷核对来源。")
        self._actions()

    def request_layout_preview(self):
        # This entry comes from the explicitly named public-basket preview.
        # It creates a new paper instead of resetting an existing paper order.
        self._create(preview=True)

    def request_current_details(self):
        if self._mixed_panel is None:
            self._message("请先选择保存卷继续，或从题篮新建，再查看当前卷细目。")
        else:
            self._mixed_panel._open_details()

    def closeEvent(self, event):
        self._closed = True
        self._epoch += 1
        if self._mixed_panel is not None:
            self._mixed_panel.close()
        super().closeEvent(event)

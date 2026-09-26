from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

SUPPORTED_SOURCE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".pdf",
    ".docx",
    ".pptx",
}

# The formal visual-import v2 path deliberately accepts only formats that its
# renderer and archive contract can reconstruct across an application restart.
# Keep the broader legacy default above for callers such as student-analysis
# drafts, while ImportDialog opts into this smaller set explicitly.
VISUAL_IMPORT_SOURCE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".pdf",
    ".docx",
}


def page_scroll(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("PageScroll")
    scroll.viewport().setObjectName("PageViewport")
    content.setObjectName("PageContent")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    # Ignore the content's unwrapped text size hint horizontally.  This is
    # what makes a widget-resizable scroll area truly fit a 360px window;
    # long Chinese subtitles then wrap instead of silently extending the
    # hidden horizontal edge.
    content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)
    scroll.setWidget(content)
    return scroll


def section_title(title: str, subtitle: str = "") -> QWidget:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    label = QLabel(title)
    label.setObjectName("PageTitle")
    label.setMinimumWidth(0)
    layout.addWidget(label)
    if subtitle:
        detail = QLabel(subtitle)
        detail.setObjectName("PageSubtitle")
        detail.setWordWrap(True)
        detail.setMinimumWidth(0)
        detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(detail)
    return container


def set_status(label: QLabel, state: str, text: str | None = None) -> None:
    """Apply a semantic status role and immediately refresh its QSS.

    Qt does not always repolish a widget when its object name changes after
    the application stylesheet has been installed.  Keeping this tiny helper
    next to the shared components prevents success/attention/error colors
    from becoming stale while preserving a readable text message.
    """

    normalized = state.strip().casefold()
    role = {
        "success": "StatusSuccess",
        "attention": "StatusAttention",
        "error": "StatusError",
        "info": "StatusInfo",
    }.get(normalized, "StatusInfo")
    label.setObjectName(role)
    if text is not None:
        label.setText(text)
    style = label.style()
    if style is not None:
        style.unpolish(label)
        style.polish(label)
    label.update()


class CardFrame(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class CollapsibleSection(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.toggle.setAccessibleName(title)
        self.toggle.setAccessibleDescription("展开高级设置")
        self.toggle.clicked.connect(self._set_expanded)
        root.addWidget(self.toggle)
        self.content = QFrame()
        self.content.setObjectName("CollapsibleContent")
        self.content.setFrameShape(QFrame.Shape.NoFrame)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(16, 14, 16, 14)
        self.content.setVisible(False)
        root.addWidget(self.content)

    def _set_expanded(self, expanded: bool) -> None:
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.toggle.setAccessibleDescription(
            "收起高级设置" if expanded else "展开高级设置"
        )
        self.content.setVisible(expanded)


class DropFileList(QListWidget):
    files_changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        supported_suffixes: set[str] | frozenset[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMinimumHeight(112)
        self.setObjectName("DropFileList")
        self.setWordWrap(True)
        self.setUniformItemSizes(False)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._paths: list[str] = []
        self._supported_suffixes = frozenset(
            suffix.casefold()
            for suffix in (supported_suffixes or SUPPORTED_SOURCE_SUFFIXES)
        )

    def _append_paths(self, paths: list[str]) -> None:
        changed = False
        expanded: list[Path] = []
        for raw in paths:
            selected = Path(raw)
            if selected.is_dir():
                expanded.extend(
                    path
                    for path in sorted(selected.rglob("*"))
                    if path.is_file()
                    and path.suffix.casefold() in self._supported_suffixes
                )
            else:
                expanded.append(selected)
        for path in expanded:
            if (
                path.is_file()
                and path.suffix.casefold() in self._supported_suffixes
                and str(path.resolve()) not in self._paths
            ):
                self._paths.append(str(path.resolve()))
                self.addItem(path.name)
                changed = True
        if changed:
            self.files_changed.emit()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        self._append_paths(
            [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        )
        event.acceptProposedAction()

    def paths(self) -> list[str]:
        return list(self._paths)

    def clear_paths(self) -> None:
        self._paths.clear()
        self.clear()
        self.files_changed.emit()

    def remove_selected_paths(self) -> None:
        rows = sorted({self.row(item) for item in self.selectedItems()}, reverse=True)
        if not rows:
            return
        for row in rows:
            self._paths.pop(row)
        self._refresh_items()
        self.files_changed.emit()

    def move_selected_paths(self, direction: int) -> None:
        """Move selected rows one visible step while preserving relative order."""

        if direction not in {-1, 1}:
            raise ValueError("direction must be -1 or 1")
        selected_rows = {self.row(item) for item in self.selectedItems()}
        if not selected_rows:
            return
        selected = [index in selected_rows for index in range(len(self._paths))]
        changed = False
        if direction < 0:
            indices = range(1, len(self._paths))
            for index in indices:
                if selected[index] and not selected[index - 1]:
                    self._paths[index - 1], self._paths[index] = (
                        self._paths[index],
                        self._paths[index - 1],
                    )
                    selected[index - 1], selected[index] = True, False
                    changed = True
        else:
            indices = range(len(self._paths) - 2, -1, -1)
            for index in indices:
                if selected[index] and not selected[index + 1]:
                    self._paths[index + 1], self._paths[index] = (
                        self._paths[index],
                        self._paths[index + 1],
                    )
                    selected[index + 1], selected[index] = True, False
                    changed = True
        if not changed:
            return
        self._refresh_items(selected)
        self.files_changed.emit()

    def _refresh_items(self, selected: list[bool] | None = None) -> None:
        self.clear()
        self.addItems([Path(path).name for path in self._paths])
        if selected is not None:
            for index, is_selected in enumerate(selected):
                if is_selected:
                    item = self.item(index)
                    if item is not None:
                        item.setSelected(True)


class FileSelectionPanel(CardFrame):
    files_changed = Signal()

    def __init__(
        self,
        hint: str,
        parent: QWidget | None = None,
        *,
        title: str = "",
        supported_suffixes: set[str] | frozenset[str] | None = None,
        allow_reordering: bool = False,
        accessible_name: str = "资料文件",
    ) -> None:
        super().__init__(parent)
        self._supported_suffixes = frozenset(
            suffix.casefold()
            for suffix in (supported_suffixes or SUPPORTED_SOURCE_SUFFIXES)
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("CardTitle")
            title_label.setWordWrap(True)
            root.addWidget(title_label)
        label = QLabel(hint)
        label.setWordWrap(True)
        label.setObjectName("MutedLabel")
        root.addWidget(label)
        self.selection_label = QLabel("尚未添加文件")
        self.selection_label.setObjectName("MutedLabel")
        self.selection_label.setWordWrap(True)
        root.addWidget(self.selection_label)
        self.file_list = DropFileList(supported_suffixes=self._supported_suffixes)
        self.file_list.setAccessibleName(f"{accessible_name}列表")
        self.file_list.setAccessibleDescription("列表顺序就是保存和识别时的页面顺序")
        self.file_list.setToolTip("可直接把文件拖到这里")
        self.file_list.files_changed.connect(self.files_changed)
        self.file_list.files_changed.connect(self._update_count)
        root.addWidget(self.file_list)
        self.choose_button = QPushButton("选择文件")
        self.choose_button.setAccessibleName(f"选择{accessible_name}")
        self.choose_button.clicked.connect(self._choose)
        self.folder_button = QPushButton("选择文件夹")
        self.folder_button.setAccessibleName(f"选择{accessible_name}文件夹")
        self.folder_button.setObjectName("QuietButton")
        self.folder_button.clicked.connect(self._choose_folder)
        self.clear_button = QPushButton("清空")
        self.clear_button.setAccessibleName(f"清空{accessible_name}")
        self.clear_button.setObjectName("QuietButton")
        self.clear_button.clicked.connect(self.file_list.clear_paths)
        if allow_reordering:
            # A two-column grid avoids a row of six controls forcing horizontal
            # overflow in the supported 420px-wide dialog.
            buttons = QGridLayout()
            buttons.setHorizontalSpacing(8)
            buttons.setVerticalSpacing(8)
            self.move_up_button = QPushButton("上移所选")
            self.move_up_button.setObjectName("QuietButton")
            self.move_up_button.setAccessibleName(f"上移所选{accessible_name}")
            self.move_up_button.clicked.connect(
                lambda: self.file_list.move_selected_paths(-1)
            )
            self.move_down_button = QPushButton("下移所选")
            self.move_down_button.setObjectName("QuietButton")
            self.move_down_button.setAccessibleName(f"下移所选{accessible_name}")
            self.move_down_button.clicked.connect(
                lambda: self.file_list.move_selected_paths(1)
            )
            self.remove_button = QPushButton("移除所选")
            self.remove_button.setObjectName("QuietButton")
            self.remove_button.setAccessibleName(f"移除所选{accessible_name}")
            self.remove_button.clicked.connect(self.file_list.remove_selected_paths)
            buttons.addWidget(self.choose_button, 0, 0)
            buttons.addWidget(self.folder_button, 0, 1)
            buttons.addWidget(self.move_up_button, 1, 0)
            buttons.addWidget(self.move_down_button, 1, 1)
            buttons.addWidget(self.remove_button, 2, 0)
            buttons.addWidget(self.clear_button, 2, 1)
            buttons.setColumnStretch(0, 1)
            buttons.setColumnStretch(1, 1)
            root.addLayout(buttons)
        else:
            buttons = QHBoxLayout()
            buttons.addWidget(self.choose_button)
            buttons.addWidget(self.folder_button)
            buttons.addWidget(self.clear_button)
            buttons.addStretch(1)
            root.addLayout(buttons)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def _choose(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择原始资料",
            "",
            self._dialog_filter(),
        )
        self.file_list._append_paths(files)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择资料文件夹", "")
        if folder:
            self.file_list._append_paths([folder])

    def paths(self) -> list[str]:
        return self.file_list.paths()

    def _dialog_filter(self) -> str:
        patterns = " ".join(f"*{suffix}" for suffix in sorted(self._supported_suffixes))
        return f"资料文件 ({patterns})"

    def _update_count(self) -> None:
        count = len(self.file_list.paths())
        self.selection_label.setText(
            f"已添加 {count} 个文件" if count else "尚未添加文件"
        )


__all__ = [
    "SUPPORTED_SOURCE_SUFFIXES",
    "VISUAL_IMPORT_SOURCE_SUFFIXES",
    "CardFrame",
    "CollapsibleSection",
    "FileSelectionPanel",
    "page_scroll",
    "section_title",
    "set_status",
]

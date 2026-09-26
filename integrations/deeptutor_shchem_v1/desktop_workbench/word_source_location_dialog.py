"""Read-only, occurrence-specific source context and original image viewer."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from ..desktop_word_metafile_preview import can_attempt_metafile
from .tasks import DesktopTaskBridge


def _label(text=""):
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return value


class WordSourceLocationDialog(QDialog):
    """Immutable source selection; stale image callbacks never change the view."""

    def __init__(self, load_locations, load_image, parent=None, *, tasks=None):
        super().__init__(parent)
        self.setObjectName("WordSourceLocationDialog")
        self.setWindowTitle("核对公式与对象的原文位置")
        self.setMinimumSize(400, 580)
        self.resize(1020, 790)
        self._load_locations = load_locations
        self._load_image = load_image
        self._closed = False
        self._epoch = 0
        self._jobs = {}
        self._serial = 0
        self._payload = {}
        self._locations = []
        self._current = None
        self._pixmap = None
        self._derived = False
        if tasks is None:
            # A worker may finish after this window closes. Its Qt owner must
            # outlive the dialog; never destroy a running pool on close.
            app = QApplication.instance()
            tasks = getattr(app, "_word_location_tasks", None)
            if tasks is None:
                tasks = DesktopTaskBridge(app)
                app._word_location_tasks = tasks
        self.tasks = tasks
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)
        eyebrow = _label("原文核对  /  公式与对象")
        eyebrow.setObjectName("LocationEyebrow")
        root.addWidget(eyebrow)
        title = _label("找到公式在原文中的位置")
        title.setObjectName("LocationTitle")
        root.addWidget(title)
        self.source_name = _label("正在核对来源…")
        root.addWidget(self.source_name)
        note = _label(
            "选择一处对象，查看所在段落或表格单元格，再对照该对象的原图。"
            "这里按原文件结构定位；Word 页码和屏幕排版可能不同。"
        )
        note.setObjectName("LocationNote")
        root.addWidget(note)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("查找对象、位置或附近文字…")
        self.search.setAccessibleName("筛选原文对象位置")
        self.search.setClearButtonEnabled(True)
        left_layout.addWidget(self.search)
        self.count = _label("读取中")
        left_layout.addWidget(self.count)
        self.location_list = QListWidget()
        self.location_list.setAccessibleName("原文对象的逐次出现位置")
        self.location_list.setWordWrap(True)
        self.location_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_layout.addWidget(self.location_list, 1)
        self.splitter.addWidget(left)

        right = QWidget()
        detail = QVBoxLayout(right)
        detail.setContentsMargins(0, 0, 0, 0)
        self.position = _label("选择左侧的一处对象")
        self.position.setObjectName("LocationPosition")
        detail.addWidget(self.position)
        self.notices = _label()
        self.notices.setObjectName("LocationNotice")
        detail.addWidget(self.notices)
        detail.addWidget(_label("所在段落 / 单元格原文"))
        self.context = QPlainTextEdit()
        self.context.setReadOnly(True)
        self.context.setAccessibleName("所选对象的完整原文上下文")
        self.context.setMinimumHeight(100)
        detail.addWidget(self.context, 2)
        self.asset_combo = QComboBox()
        self.asset_combo.setAccessibleName("仅属于所选对象的原图")
        self.asset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.asset_combo.setMinimumContentsLength(8)
        self.asset_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.asset_combo.hide()
        detail.addWidget(self.asset_combo)
        self.image_scroll = QScrollArea()
        self.image_scroll.setWidgetResizable(True)
        self.image_scroll.setMinimumHeight(115)
        self.image_label = _label("选择对象后显示对应原图")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image_scroll.setWidget(self.image_label)
        detail.addWidget(self.image_scroll, 2)
        self.image_note = _label()
        detail.addWidget(self.image_note)
        self.zoom_button = QPushButton("放大原图")
        self.zoom_button.setEnabled(False)
        self.zoom_button.clicked.connect(self._zoom)
        detail.addWidget(self.zoom_button)
        self.technical_button = QPushButton("展开文件内定位详情")
        self.technical_button.setCheckable(True)
        detail.addWidget(self.technical_button)
        self.technical = QPlainTextEdit()
        self.technical.setReadOnly(True)
        self.technical.setAccessibleName("文件内精确对象路径与来源指纹")
        self.technical.setMaximumHeight(110)
        self.technical.hide()
        self.technical_button.toggled.connect(self.technical.setVisible)
        detail.addWidget(self.technical)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setMinimumSize(0, 0)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_scroll.setWidget(right)
        self.splitter.addWidget(self.detail_scroll)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([340, 600])
        root.addWidget(self.splitter, 1)
        self.status = _label("正在本机核对原文件与当前位置…")
        self.status_scroll = QScrollArea()
        self.status_scroll.setWidgetResizable(True)
        self.status_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.status_scroll.setMinimumHeight(36)
        self.status_scroll.setMaximumHeight(64)
        self.status_scroll.setWidget(self.status)
        root.addWidget(self.status_scroll)
        actions = QHBoxLayout()
        self.copy_button = QPushButton("复制位置与原文")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)
        close = QPushButton("完成核对")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        root.addLayout(actions)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.setStyleSheet(
            "QDialog#WordSourceLocationDialog { background:#f5f3ed; color:#263d34; }"
            "#WordSourceLocationDialog QLabel { color:#354e42; }"
            "#WordSourceLocationDialog QLabel#LocationEyebrow { color:#708374; font-size:11px; }"
            "#WordSourceLocationDialog QLabel#LocationTitle { font-size:23px; font-weight:600; }"
            "#WordSourceLocationDialog QLabel#LocationPosition { font-size:15px; font-weight:600; }"
            "#WordSourceLocationDialog QLabel#LocationNotice { color:#886322; }"
            "#WordSourceLocationDialog QLabel#LocationNote { color:#65766b; }"
            "#WordSourceLocationDialog QPlainTextEdit, #WordSourceLocationDialog QListWidget,"
            "#WordSourceLocationDialog QScrollArea { background:#fffef9; border:1px solid #d7dfd2;"
            " border-radius:8px; padding:6px; color:#263d34; }"
            "#WordSourceLocationDialog QListWidget::item { padding:10px 5px; border-bottom:1px solid #e6eadf; }"
            "#WordSourceLocationDialog QListWidget::item:selected { background:#dfebd7; color:#214a37; }"
            "#WordSourceLocationDialog QLineEdit, #WordSourceLocationDialog QComboBox,"
            "#WordSourceLocationDialog QPushButton { min-height:28px; border:1px solid #cbd7c9;"
            " border-radius:6px; padding:4px 8px; color:#244e3d; background:#edf3e9; }"
            "#WordSourceLocationDialog QPushButton:disabled { color:#899388; }"
        )
        self.search.textChanged.connect(self._filter)
        self.location_list.currentItemChanged.connect(self._selected)
        self.asset_combo.currentIndexChanged.connect(self._asset_selected)
        QTimer.singleShot(0, self._begin)

    def _submit(self, operation, success, failure):
        self._serial += 1
        token = self._serial
        self._jobs[token] = None

        def done(value, failed=False):
            self._jobs.pop(token, None)
            if self._closed or not isValid(self):
                return
            (failure if failed else success)(value)

        try:
            task = self.tasks.submit(
                "核对原文对象位置", operation, on_success=done,
                on_failure=lambda message: done(message, True),
            )
            if token in self._jobs:
                self._jobs[token] = task
        except (RuntimeError, TypeError):
            done("本地定位暂时无法启动，请重新打开。", True)

    def _begin(self):
        if not self._closed:
            self._submit(self._load_locations, self._ready, self._failed)

    def _failed(self, message):
        self._payload = {}
        self._locations = []
        self._filter()
        self.status.setText(message or "当前位置暂时无法读取，请重新打开原文。")
        self.count.setText("未能定位")
        self.source_name.setText("来源校验未完成")
        self.position.setText("原文位置暂时无法核对")

    def _ready(self, value):
        try:
            if not isinstance(value, dict) or not isinstance(value["blocks"], list):
                raise ValueError()
            locations = [location for block in value["blocks"] for location in block["locations"]]
            if any(not isinstance(loc, dict) or not all(isinstance(loc[k], str) for k in (
                "location_id", "label", "position_text", "context_text", "xml_locator"
            )) or not isinstance(loc["assets"], list) or not isinstance(loc["notices"], list)
                for loc in locations):
                raise ValueError()
            self._payload = value
            self._locations = locations
            self.source_name.setText(str(value["source_name"]))
            self.status.setText("原文件与所选范围已核对。图像不可读时保留缺口，可复制位置后在原 Word 中对照。")
            self._filter()
            if not locations:
                self.position.setText("此范围没有可定位的公式或对象")
                self.status.setText("没有发现对应 XML 对象；文字中的缺口提示不会被当作真实对象。")
        except (KeyError, TypeError, ValueError):
            self._payload = {}
            self._locations = []
            self._failed("定位结果不完整，请重新打开原文核对。")

    def _filter(self, *_args):
        current_id = (self._current or {}).get("location_id")
        query = self.search.text().strip().casefold()
        self.location_list.blockSignals(True)
        self.location_list.clear()
        selected = 0
        for location in self._locations:
            if query and query not in " ".join(str(location.get(k, "")) for k in (
                "label", "position_text", "context_text", "kind"
            )).casefold():
                continue
            item = QListWidgetItem(location["label"] + "\n" + location["position_text"])
            item.setData(Qt.ItemDataRole.UserRole, location)
            self.location_list.addItem(item)
            if current_id == location["location_id"]:
                selected = self.location_list.count() - 1
        self.location_list.blockSignals(False)
        self.count.setText(f"显示 {self.location_list.count()} / {len(self._locations)} 处")
        if self.location_list.count():
            self.location_list.setCurrentRow(selected)
        else:
            self._selected(None)

    def _selected(self, item, *_args):
        self._epoch += 1
        self._current = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._pixmap = None
        self.zoom_button.setEnabled(False)
        self.copy_button.setEnabled(bool(item))
        self.image_label.clear()
        self.image_note.clear()
        self.asset_combo.blockSignals(True)
        self.asset_combo.clear()
        if not item:
            self.position.setText("没有匹配的对象")
            self.context.clear()
            self.notices.clear()
            self.technical.clear()
        else:
            loc = self._current
            self.position.setText(loc["position_text"])
            self.context.setPlainText(loc["context_text"])
            self.notices.setText("\n".join(loc["notices"]))
            self.technical.setPlainText(
                loc["xml_locator"] + "\n来源 SHA-256：" + str(self._payload.get("source_sha256", ""))
            )
            for asset in loc["assets"]:
                self.asset_combo.addItem(str(asset.get("label") or "对象原图"), asset)
        self.asset_combo.blockSignals(False)
        self.asset_combo.setVisible(self.asset_combo.count() > 0)
        if self.asset_combo.count():
            self._asset_selected()
        else:
            self.image_label.setText(
                "本对象没有可独立读取的原图。\n请按上方位置对照原 Word。"
                if item else "尚未选择可定位的对象。"
            )

    def _asset_selected(self, *_args):
        self._epoch += 1
        epoch = self._epoch
        self._pixmap = None
        self.zoom_button.setEnabled(False)
        self.image_label.clear()
        self.image_note.clear()
        asset = self.asset_combo.currentData()
        if not self._current or not isinstance(asset, dict):
            return
        if not (asset.get("preview_supported") is True or can_attempt_metafile(asset)):
            self.image_label.setText("此原图格式暂时无法预览，请按具体位置核对原 Word。")
            return
        location_id = self._current["location_id"]
        asset_id = asset["asset_id"]
        load_image = self._load_image
        self.image_label.setText("正在核对并读取此对象的原图…")

        def failed(message):
            if epoch == self._epoch:
                self.image_label.setText("原图暂时无法读取")
                self.image_note.setText(message or "请对照原 Word；未替换为邻近对象。")

        def ready(result):
            if epoch != self._epoch:
                return
            if not isinstance(result, dict) or not isinstance(result.get("bytes"), bytes):
                failed("原图数据无效，请重新定位。")
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(result["bytes"]) or pixmap.isNull():
                failed("此对象原图无法显示，请核对原 Word。")
                return
            self._pixmap = pixmap
            self._derived = result.get("derived_preview") is True
            self.image_note.setText("原 Word 矢量图的本地转换预览" if self._derived else "该对象的原始图片")
            self.zoom_button.setEnabled(True)
            self._fit_image()

        self._submit(lambda: load_image(location_id, asset_id), ready, failed)

    def _fit_image(self):
        if self._pixmap is not None:
            self.image_label.setPixmap(self._pixmap.scaled(
                max(1, self.image_scroll.viewport().width() - 16),
                max(1, self.image_scroll.viewport().height() - 16),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            ))

    def _zoom(self):
        if self._pixmap is not None:
            from .import_word_dialog import _WordImageDialog
            dialog = _WordImageDialog(self._pixmap, "所选对象的原图", self, derived=self._derived)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()

    def _copy(self):
        if self._current:
            QApplication.clipboard().setText("\n\n".join((
                str(self._payload.get("source_name", "")), self._current["position_text"],
                self._current["context_text"], self.technical.toPlainText(),
            )))
            self.status.setText("已复制具体位置与原文，可在 Word 中按附近文字对照。")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "splitter"):
            self.splitter.setOrientation(Qt.Orientation.Vertical if self.width() < 760 else Qt.Orientation.Horizontal)
            self._fit_image()

    def done(self, result):
        self._closed = True
        self._epoch += 1
        for task_id in tuple(self._jobs.values()):
            if task_id is not None:
                self.tasks.cancel(task_id)
        self._jobs.clear()
        self._locations.clear()
        self._payload = {}
        self._current = None
        self._pixmap = None
        super().done(result)


__all__ = ["WordSourceLocationDialog"]

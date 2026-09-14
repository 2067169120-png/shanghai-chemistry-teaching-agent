"""Choose a saved offline preparation brief and inspect it before loading."""

from datetime import datetime

from PySide6.QtCore import QTimer, Qt

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from ..desktop_preparation_drafts import PreparationDraftError


class PreparationDraftDialog(QDialog):
    def __init__(self, facade, parent=None):
        super().__init__(parent)
        self.facade = facade
        self.tasks = getattr(parent, "tasks", None)
        self._epoch = 0
        self._task_id = None
        self._offset = 0
        self._total = 0
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._search_all)
        self.selected: dict | None = None
        self.setWindowTitle("打开已保存备课草稿")
        self.resize(820, 720)
        self.setMinimumSize(360, 480)
        root = QVBoxLayout(self)
        intro = QLabel(
            "默认列出当前作品；按作品名称或原课题检索后分页。已归档草稿从“我的备课”打开，回收站草稿需先还原。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索当前草稿的作品名称或原课题")
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName("搜索全部已保存草稿")
        self.query.textChanged.connect(self._queue_search)
        root.addWidget(self.query)
        self.source = QComboBox()
        self.source.setMinimumContentsLength(8)
        self.source.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.source.setAccessibleName("选择已保存的备课草稿")
        root.addWidget(self.source)
        paging = QHBoxLayout()
        self.previous = QPushButton("上一页")
        self.next = QPushButton("下一页")
        self.page_info = QLabel("最近草稿；可输入课题检索全部")
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.previous.clicked.connect(lambda: self._page(-1))
        self.next.clicked.connect(lambda: self._page(1))
        paging.addWidget(self.previous)
        paging.addWidget(self.page_info, 1)
        paging.addWidget(self.next)
        root.addLayout(paging)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("备课草稿六字段与精细设置预览")
        root.addWidget(self.preview, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        self.load_button = QPushButton("载入到备课表单")
        self.load_button.setObjectName("PrimaryAction")
        self.load_button.setEnabled(False)
        self.load_button.clicked.connect(self._confirm)
        root.addWidget(self.load_button)
        cancel = QPushButton("取消，保留当前填写")
        cancel.setObjectName("QuietButton")
        cancel.clicked.connect(self.reject)
        root.addWidget(cancel)
        self.source.currentIndexChanged.connect(self._select)
        try:
            if callable(getattr(facade, "search_preparation_drafts", None)):
                self._populate(facade.search_preparation_drafts(limit=50))
            else:  # compatibility with existing small read-only facade clients
                options = facade.preparation_draft_options()
                self._populate({"items": options, "total": len(options), "offset": 0})
        except Exception:
            self.status.setText("草稿列表暂时无法读取，当前填写未变。")

    @staticmethod
    def _label(option):
        try:
            stamp = datetime.fromisoformat(option["created_at"].replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M:%S")
        except ValueError:
            stamp = "保存时间待核对"
        return stamp + " · " + option["title"][:100]

    def _populate(self, result, epoch=None):
        if epoch is not None and epoch != self._epoch:
            return
        self._offset, self._total = result["offset"], result["total"]
        self.source.blockSignals(True)
        self.source.clear()
        for option in result["items"]:
            self.source.addItem(self._label(option), option)
        self.source.blockSignals(False)
        self._select(self.source.currentIndex())
        self.previous.setEnabled(self._offset > 0)
        self.next.setEnabled(self._offset + 50 < self._total)
        self.page_info.setText(f"匹配{self._total}份 · 第{self._offset // 50 + 1}页")
        if not result["items"]:
            self.status.setText("暂无匹配的有效离线草稿。可修改关键词，或先在备课页保存草稿。")

    def _queue_search(self, *_):
        self._offset = 0
        self._epoch += 1
        self.selected = None
        self.load_button.setEnabled(False)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self._search_timer.start()

    def _search_all(self):
        self._epoch += 1
        epoch = self._epoch
        query, offset = self.query.text(), self._offset
        self.selected = None
        self.load_button.setEnabled(False)
        self.status.setText("正在查找全部历史草稿…")
        operation = lambda: self.facade.search_preparation_drafts(query=query, offset=offset, limit=50)
        if self.tasks is not None:
            if self._task_id:
                self.tasks.cancel(self._task_id)
            self._task_id = self.tasks.submit("查找全部草稿", operation,
                on_success=lambda result: self._populate(result, epoch),
                on_failure=lambda message: self._search_failed(message, epoch))
        else:
            try:
                self._populate(operation(), epoch)
            except Exception:
                self._search_failed("草稿检索失败，原记录未改变。", epoch)

    def _search_failed(self, message, epoch):
        if epoch == self._epoch:
            self.status.setText(message)

    def _page(self, delta):
        self._offset = max(0, self._offset + delta * 50)
        self._search_all()

    def select_draft_id(self, draft_id):
        """Find the exact ID, not the first result or only the recent page."""
        try:
            option = self.facade.preparation_draft_option(draft_id)
        except Exception:
            return False
        self._search_timer.stop()
        self._epoch += 1
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem(self._label(option), option)
        self.source.blockSignals(False)
        self._select(0)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.page_info.setText("已定位到选中的草稿，可搜索其他历史作品")
        return self.selected is not None

    def done(self, result):
        self._search_timer.stop()
        self._epoch += 1
        if self._task_id and self.tasks is not None:
            self.tasks.cancel(self._task_id)
        super().done(result)

    def _select(self, _index):
        self.selected = None
        self.load_button.setEnabled(False)
        self.preview.clear()
        option = self.source.currentData()
        if not isinstance(option, dict):
            return
        try:
            selected = self.facade.load_preparation_draft(
                option["draft_id"], option["revision"]
            )
        except PreparationDraftError as exc:
            self.status.setText(exc.message_zh)
            return
        except Exception:  # noqa: BLE001 - sanitize local read errors at the UI boundary
            self.status.setText("草稿无法载入，请关闭窗口重新选择。")
            return
        payload = selected["payload"]
        lines = [
            "输出："
            + {"ppt": "仅PPT", "lesson_plan": "仅教案", "joint": "PPT与教案"}[
                payload["output_kind"]
            ]
        ]
        for key, label in (
            ("topic", "课题/章节"),
            ("audience", "授课对象"),
            ("lesson_route", "课型"),
            ("lesson_timing", "课时"),
            ("objective", "教学目标"),
            ("materials", "资料与补充说明"),
        ):
            lines.extend(["", label + "：", payload[key]])
        for key, label in (
            ("learning_and_experiment", "学情/实验"),
            ("template_and_delivery", "模板/呈现"),
            ("homework_and_strategy", "作业/策略"),
        ):
            lines.extend(["", label + "：", payload["advanced"].get(key, "")])
        self.preview.setPlainText("\n".join(lines))
        self.selected = selected
        self.load_button.setEnabled(True)
        self.status.setText(
            "离线读取完成。载入不等于生成；再次保存会新增一份草稿，旧稿保留。"
        )

    def _confirm(self):
        if self.selected is None:
            return
        previous = self.selected
        self._select(self.source.currentIndex())
        if self.selected is not None and self.selected == previous:
            self.accept()

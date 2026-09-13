"""Choose a saved offline preparation brief and inspect it before loading."""

from datetime import datetime

from PySide6.QtWidgets import (
    QComboBox,
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
        self.selected: dict | None = None
        self.setWindowTitle("打开已保存备课草稿")
        self.resize(820, 720)
        self.setMinimumSize(360, 480)
        root = QVBoxLayout(self)
        intro = QLabel(
            "选择最近50份有效离线草稿。载入后可修改并另存一份，再按原流程确认生成；不会改写原草稿或自动调用模型。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.source = QComboBox()
        self.source.setMinimumContentsLength(8)
        self.source.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.source.setAccessibleName("选择已保存的备课草稿")
        root.addWidget(self.source)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("备课草稿六字段与精细设置预览")
        root.addWidget(self.preview, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
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
            options = facade.preparation_draft_options()
            for option in options:
                try:
                    stamp = (
                        datetime.fromisoformat(option["created_at"])
                        .astimezone()
                        .strftime("%m-%d %H:%M:%S")
                    )
                except ValueError:
                    stamp = "保存时间待核对"
                self.source.addItem(stamp + " · " + option["title"][:100], option)
            if not options:
                self.status.setText(
                    "暂无可载入的有效离线草稿。请先填写备课表单并点击“保存草稿”。"
                )
        except Exception:  # noqa: BLE001 - sanitize local read errors at the UI boundary
            self.status.setText("草稿列表暂时无法读取，当前填写未变。")

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

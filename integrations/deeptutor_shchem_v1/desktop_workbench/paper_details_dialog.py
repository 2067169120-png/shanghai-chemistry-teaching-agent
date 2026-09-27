"""Source-bound current-paper details, with scrollable text and fixed actions."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (QComboBox, QDialog, QLabel, QPushButton, QSizePolicy,
                              QTabWidget, QTextBrowser, QVBoxLayout, QWidget)


def _text(value):
    return escape(str(value), quote=True)


def _p(label, value):
    return f"<p><b>{_text(label)}</b><br>{_text(value)}</p>"


def _number(value):
    return f"{value:g}" if isinstance(value, (int, float)) else "未知"


def score_text(score):
    if score["total"] is not None:
        return f"{_number(score['total'])} 分（已知合计）"
    missing = score["missing_count"]
    if score["known"] is not None:
        return f"已知合计 {_number(score['known'])} 分；另 {missing} 项缺失，总分未知"
    return f"未知；{missing} 项缺少分值证据" if missing else "本卷无题，不计算分值"


def count_text(value):
    if value["total"] is not None:
        return str(value["total"])
    if value["known"] is not None:
        return f"已登记 {value['known']}；另 {value['unknown_sections']} 段层级未知"
    return "未登记，不能从题号推定" if value["unknown_sections"] else "本卷无题"


def _browser(name):
    browser = QTextBrowser()
    browser.setAccessibleName(name)
    browser.setOpenLinks(False)
    browser.setOpenExternalLinks(False)
    browser.setMinimumSize(0, 0)
    browser.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    option = browser.document().defaultTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    browser.document().setDefaultTextOption(option)
    return browser


class PaperDetailsDialog(QDialog):
    refresh_requested = Signal()
    edit_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("当前卷细目表")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(320, 400)
        self.resize(760, 650)
        self.details = None
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        heading = QLabel("当前卷细目表")
        heading.setObjectName("CardTitle")
        root.addWidget(heading)
        self.tabs = QTabWidget()
        self.tabs.setMinimumSize(0, 0)
        self.summary = _browser("当前卷结构、分值与证据缺失总览")
        self.tabs.addTab(self.summary, "全卷总览")
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        self.selection = QComboBox()
        self.selection.setAccessibleName("按本卷顺序选择细目")
        self.selection.setMinimumWidth(0)
        self.selection.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.selection.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.selection)
        self.item_detail = _browser("当前完整题段的来源、范围与配分细目")
        layout.addWidget(self.item_detail, 1)
        self.tabs.addTab(page, "逐项细目")
        self.trace = _browser("当前段来源版本与选中范围追溯")
        self.tabs.addTab(self.trace, "来源追溯")
        root.addWidget(self.tabs, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setAccessibleName("当前卷细目表核对状态")
        root.addWidget(self.status)
        self.refresh_button = QPushButton("重读草稿与来源")
        self.edit_button = QPushButton("返回编辑当前段")
        self.close_button = QPushButton("关闭细目表")
        self.refresh_button.setObjectName("QuietButton")
        self.close_button.setObjectName("QuietButton")
        self.edit_button.setObjectName("PrimaryAction")
        for button in (self.refresh_button, self.edit_button, self.close_button):
            button.setAutoDefault(False)
            button.setAccessibleName(button.text())
            root.addWidget(button)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.edit_button.clicked.connect(self._edit)
        self.close_button.clicked.connect(self.reject)
        self.selection.currentIndexChanged.connect(self._render_item)
        self.summary.anchorClicked.connect(self._activate_row)
        self.set_pending()

    def set_pending(self):
        self._clear("正在核对当前草稿与来源…")
        self.refresh_button.setEnabled(False)

    def _clear(self, message):
        self.details = None
        self.selection.clear()
        self.summary.setPlainText(message + "\n核对完成前不显示旧统计。")
        self.item_detail.setPlainText("当前细目尚未核对。")
        self.trace.setPlainText("当前来源尚未核对。")
        self.edit_button.setEnabled(False)
        self.status.setText(message)

    def set_error(self, message="来源或草稿待核对，请重读后再查看。"):
        self._clear(message)
        self.refresh_button.setEnabled(True)

    def set_details(self, details, *, selected=None):
        self.details = details
        self.selection.blockSignals(True)
        self.selection.clear()
        for row in details["rows"]:
            self.selection.addItem(f"本卷第 {row['number']} 段 · {row['kind_zh']}", row["key"])
        if selected is not None:
            self.selection.setCurrentIndex(max(0, self.selection.findData(selected)))
        self.selection.blockSignals(False)
        pieces = [_p("当前范围", f"本卷 {details['section_count']} 个完整题目段；已移除 {details['excluded_count']} 项不计入。")]
        for key, label in (("themes", "主题大题"), ("printed", "印刷小题"), ("atomic", "最小作答单元")):
            pieces.append(_p(label, count_text(details["counts"][key])))
        pieces.extend(('<a name="scores"></a>' + _p("本次配分", score_text(details["current_score"])),
                       _p("原来源分值", score_text(details["source_score"])),
                       _p("时间", f"本次设置 {details['duration_minutes']} 分钟；不是逐题估时。逐题用时证据缺失，无法计算预计用时。"),
                       _p("目标覆盖", "当前草稿未绑定可核验的目标清单，无法计算覆盖率。"),
                       _p("统计边界", "仅统计来源中已登记的显式层级。Word 完整题段不等于主题；图片候选未完成人工复核。来源分值不代表官方评分。")))
        if details["rows"]:
            pieces.append("<h3>按当前卷顺序</h3>")
            for index, row in enumerate(details["rows"]):
                pieces.append(f'<p><a href="section:{index}">本卷第 {row["number"]} 段</a> · {_text(row["title"])}<br>{_text(score_text(row["current_score"]))}</p>')
        else:
            pieces.append(_p("本卷暂时没有题目", "返回工作台，可撤销移出或恢复本卷移除项。全局题篮保持不变。"))
        self.summary.setHtml("".join(pieces))
        self.status.setText("已按本次读取核对；来源有变请重读。")
        self.refresh_button.setEnabled(True)
        self._render_item()

    def _activate_row(self, url):
        text = url.toString()
        if text.startswith("section:") and text[8:].isdigit() and self.details is not None:
            index = int(text[8:])
            if 0 <= index < len(self.details["rows"]):
                self.selection.setCurrentIndex(index)
                self.tabs.setCurrentIndex(1)

    def _render_item(self, *_args):
        index = self.selection.currentIndex()
        self.edit_button.setEnabled(self.details is not None and index >= 0)
        if self.details is None or index < 0:
            self.item_detail.setPlainText("本卷暂时没有题目。返回工作台可撤销移出或恢复移除项。")
            self.trace.setPlainText("本卷暂时没有题目，没有选中来源。")
            return
        row = self.details["rows"][index]
        pieces = [_p(f"本卷第 {row['number']} 段 · {row['kind_zh']}", row["title"]),
                  _p("来源", row["source"]), _p("选中范围", row["scope"])]
        for name, label in (("themes", "主题大题"), ("printed", "印刷小题"), ("atomic", "最小作答单元")):
            pieces.append(_p(label, row["counts"][name] if row["counts"][name] is not None else "未知"))
        pieces.extend((_p("结构依据", row["structure_evidence"]),
                       _p("本次配分 · " + row["score_basis"], score_text(row["current_score"])),
                       _p("原来源分值", score_text(row["source_score"]))))
        self.item_detail.setHtml("".join(pieces))
        pieces = [_p(f"本卷第 {row['number']} 段 · 来源与范围", row["title"]), _p("来源", row["source"])]
        selected = row["selected"]
        if row["kind"] == "word_question":
            for key, label in (("question_blocks", "题面区块"), ("context_blocks", "共同材料区块"), ("answer_blocks", "答案区块")):
                value = selected.get(key)
                pieces.append(_p(label + "（来源区块序号，不是印刷题号）", "、".join(map(str, value)) if value else "未登记" if value is None else "本次范围未包含"))
        else:
            for key, label in (("theme_id", "主题身份"), ("printed_ids", "印刷小题身份"), ("atomic_ids", "作答单元身份")):
                value = selected.get(key)
                pieces.append(_p(label, "、".join(value) if isinstance(value, list) else value or "未登记"))
        # Only known source identity fields; arbitrary source text is escaped,
        # never interpreted as links, images, paths, or HTML.
        for key, label in (("scope", "来源库"), ("paper_id", "原卷身份"), ("batch_id", "导入批次"),
                           ("archive_source_id", "归档来源身份"), ("source_sha256", "来源字节 SHA-256"),
                           ("data_snapshot_id", "来源目录版本"), ("revision", "题段版本"),
                           ("candidate_revision", "图片候选版本"), ("candidate_sha256", "图片候选 SHA-256"),
                           ("crop_head_sha256", "裁片版本")):
            if row["source_ref"].get(key) is not None:
                pieces.append(_p(label, row["source_ref"][key]))
        self.trace.setHtml("".join(pieces))

    def _edit(self):
        key = self.selection.currentData()
        if self.details is not None and isinstance(key, str):
            self.edit_requested.emit(key)
            self.accept()

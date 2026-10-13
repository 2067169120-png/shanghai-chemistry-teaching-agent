"""Local model-call metadata, usage and configured price estimates."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .components import page_scroll, section_title, set_status

DEFAULT_EXPORT_PATH = "E:/任务输出/上海高中化学等级考/AI用量导出.json"
CALL_LIMIT = 500
TASK_LABELS = {
    "unknown": "未知任务", "preparation": "备课", "visual_import": "视觉导入",
    "visual_crop_review": "裁片复核", "textbook_knowledge": "教材知识", "exam_advice": "考试建议",
    "student_analysis": "学生分析", "semantic_tags": "语义标签", "connection_test": "连接测试",
    "blueprint_generation": "蓝图生成", "blueprint_diagnosis": "蓝图诊断", "blueprint_revision": "蓝图修订",
}
OUTCOME_LABELS = {
    "response_received": "收到响应", "failed": "运输失败", "cancelled": "已取消", "not_sent": "未发送",
}
API_STYLE_LABELS = {"responses": "响应接口", "chat_completions": "聊天补全接口"}


def _number(value: Any) -> str:
    if value is None:
        return "未知"
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError, OverflowError):
        return "未知"


def _value(value: Any, *, missing: str = "未知") -> str:
    return missing if value is None or value == "" else str(value)


def _cost_text(call: dict[str, Any]) -> str:
    amount = call.get("estimated_cost_amount")
    currency = call.get("estimated_cost_currency")
    if amount is not None and currency:
        return f"{amount} {currency}（按配置价估算）"
    status = str(call.get("cost_status") or "")
    if status == "price_unavailable":
        return "价格暂时无法读取，无法估算"
    if status == "cache_usage_unknown":
        return "缓存用量未知，无法估算"
    if status == "not_configured":
        return "未配置价格"
    if status in {"unknown_usage", "usage_unknown", "usage_unavailable", "incomplete_usage"}:
        return "用量未知，无法估算"
    if not call.get("price_version"):
        return "未配置价格"
    return "无法估算"


def _usage_text(value: Any) -> str:
    return {
        "actual": "服务端已提供",
        "known": "服务端已提供",
        "available": "服务端已提供",
        "reported": "服务端已提供",
        "partial": "服务端部分提供",
        "unknown": "未知（服务端未提供）",
        "missing": "未知（服务端未提供）",
        "unavailable": "未知（服务端未提供）",
    }.get(str(value), _value(value))


def _table(headers: tuple[str, ...], name: str) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAccessibleName(name)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return table


def _fill_table(table: QTableWidget, rows: list[list[str]]) -> None:
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column, value in enumerate(row):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            table.setItem(row_index, column, item)


class ModelUsageDialog(QDialog):
    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self._active: str | None = None
        self._closed = False
        self._loaded_once = False
        self._prices: dict[str, dict[str, Any]] = {}
        self.setWindowTitle("用量与费用 · 本机调用记录")
        self.resize(1000, 720)
        self.setMinimumSize(520, 440)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(section_title(
            "用量与费用",
            "读取本机调用元数据。服务端未提供的用量显示未知；本地输入估算不等同于实际用量。",
        ))
        note = QLabel("费用均为按配置价估算，实际收费以服务商账单为准。时间按记录中的 UTC 显示。")
        note.setWordWrap(True)
        note.setObjectName("MutedLabel")
        root.addWidget(note)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("累计、逐次明细与价格配置")
        root.addWidget(self.tabs, 1)
        summary_page = QWidget()
        summary_layout = QFormLayout(summary_page)
        summary_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        summary_layout.setSpacing(16)
        self.request_count_label = self._summary_label()
        self.usage_counts_label = self._summary_label()
        self.known_tokens_label = self._summary_label()
        self.local_estimate_label = self._summary_label()
        self.estimated_cost_label = self._summary_label()
        self.recording_failure_label = self._summary_label()
        for title, label in (
            ("累计请求", self.request_count_label),
            ("服务端用量覆盖", self.usage_counts_label),
            ("服务端已知用量", self.known_tokens_label),
            ("本地输入估算", self.local_estimate_label),
            ("累计费用（按配置价估算）", self.estimated_cost_label),
            ("本机记录状态", self.recording_failure_label),
        ):
            summary_layout.addRow(title, label)
        coverage = QLabel("已知用量仅汇总服务端已提供的字段，含未知用量的请求无法据此推算完整实际消耗。")
        coverage.setWordWrap(True)
        coverage.setObjectName("MutedLabel")
        summary_layout.addRow(coverage)
        self.tabs.addTab(page_scroll(summary_page), "累计")

        calls_page = QWidget()
        calls_layout = QVBoxLayout(calls_page)
        self.calls_note = QLabel(f"逐次明细显示最近 {CALL_LIMIT} 条；导出包含全部本机记录。")
        self.calls_note.setWordWrap(True)
        calls_layout.addWidget(self.calls_note)
        self.calls_table = _table((
            "记录 ID", "时间（UTC）", "配置", "服务商", "模型", "接口格式", "任务", "结果",
            "已调用模型", "HTTP 状态", "耗时（毫秒）", "本地输入估算", "输入 tokens",
            "输出 tokens", "总 tokens", "缓存输入 tokens", "推理 tokens", "用量状态",
            "费用（按配置价估算）", "价格版本",
        ), "逐次模型调用元数据与用量")
        calls_layout.addWidget(self.calls_table, 1)
        self.tabs.addTab(calls_page, "逐次明细")

        prices_page = QWidget()
        prices_layout = QVBoxLayout(prices_page)
        price_note = QLabel(
            "单价单位：所选币种 / 百万 tokens。新版价格仅用于之后的调用，"
            "既有调用保留调用时的价格快照。未配置价格时仍记录用量。"
        )
        price_note.setWordWrap(True)
        price_note.setObjectName("MutedLabel")
        prices_layout.addWidget(price_note)
        price_form = QFormLayout()
        price_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName("选择要配置价格的已保存模型配置")
        self.profile_combo.currentIndexChanged.connect(self._price_selected)
        self.price_state = self._summary_label()
        self.currency_input = QLineEdit()
        self.currency_input.setPlaceholderText("例如 USD 或 CNY")
        self.currency_input.setMaxLength(3)
        self.currency_input.setAccessibleName("价格币种，三个英文字母")
        self.input_price = self._price_input("输入单价")
        self.output_price = self._price_input("输出单价")
        self.cached_input_price = self._price_input("缓存输入单价，可留空")
        self.cached_input_price.setPlaceholderText("可留空，表示未配置缓存输入单价")
        for title, field in (
            ("模型配置", self.profile_combo), ("当前价格", self.price_state),
            ("币种", self.currency_input), ("输入单价 / 百万 tokens", self.input_price),
            ("输出单价 / 百万 tokens", self.output_price),
            ("缓存输入单价 / 百万 tokens", self.cached_input_price),
        ):
            price_form.addRow(title, field)
        prices_layout.addLayout(price_form)
        price_actions = QHBoxLayout()
        self.save_price_button = QPushButton("保存价格")
        self.save_price_button.clicked.connect(self.save_price)
        self.remove_price_button = QPushButton("删除此配置价格")
        self.remove_price_button.clicked.connect(self.remove_price)
        for button in (self.save_price_button, self.remove_price_button):
            button.setAutoDefault(False)
            price_actions.addWidget(button)
        price_actions.addStretch(1)
        prices_layout.addLayout(price_actions)
        self.prices_table = _table((
            "配置", "服务商", "模型", "币种", "输入单价 / 百万", "输出单价 / 百万",
            "缓存输入单价 / 百万", "价格版本",
        ), "本机配置价格清单，费用按配置价估算")
        self.prices_table.setMinimumHeight(160)
        prices_layout.addWidget(self.prices_table, 1)
        self.prices_scroll = page_scroll(prices_page)
        self.tabs.addTab(self.prices_scroll, "价格配置")

        self.status = self._summary_label()
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button = QPushButton("导出 JSON")
        self.export_button.clicked.connect(self.export_usage)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        for button in (self.refresh_button, self.export_button, self.close_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        root.addLayout(actions)
        self.tasks.task_finished.connect(self._task_finished)
        self.refresh()

    @staticmethod
    def _summary_label() -> QLabel:
        label = QLabel("正在读取…")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    @staticmethod
    def _price_input(name: str) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText("填写大于或等于 0 的十进制单价")
        field.setAccessibleName(name + "，每百万 tokens")
        return field

    def _set_busy(self, busy: bool) -> None:
        self.refresh_button.setEnabled(not busy)
        self.export_button.setEnabled(not busy and self._loaded_once)
        for field in (self.profile_combo, self.currency_input, self.input_price,
                      self.output_price, self.cached_input_price):
            field.setEnabled(not busy and self.profile_combo.count() > 0)
        selected = self.profile_combo.currentData()
        self.save_price_button.setEnabled(not busy and selected is not None)
        self.remove_price_button.setEnabled(not busy and selected in self._prices)

    def _snapshot(self):
        return (
            self.facade.model_usage_summary(), self.facade.model_usage_calls(limit=CALL_LIMIT),
            self.facade.model_usage_prices(), self.facade.list_provider_profiles(),
        )

    def refresh(self) -> None:
        if self._active or self._closed:
            return
        self._set_busy(True)
        set_status(self.status, "info", "正在读取本机用量记录…")
        self._active = self.tasks.submit(
            "读取模型用量", self._snapshot, on_success=self._loaded, on_failure=self._failed,
        )

    def _loaded(self, snapshot) -> None:
        if self._closed:
            return
        self._active = None
        summary, calls, prices, profiles = snapshot
        self._loaded_once = True
        request_count = int(summary.get("request_count") or 0)
        actual_count = int(summary.get("actual_usage_request_count") or 0)
        unknown_count = int(summary.get("unknown_usage_request_count") or 0)
        self.request_count_label.setText(f"{request_count:,} 次")
        self.usage_counts_label.setText(f"完整用量 {actual_count:,} 次；未知或部分用量 {unknown_count:,} 次")
        token_values = [summary.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")]
        unknown_zero_stub = (
            all(value == 0 for value in token_values) and len(calls) == request_count
            and all(call.get("usage_status") == "unknown" for call in calls)
        )
        if request_count and not actual_count and (all(value is None for value in token_values) or unknown_zero_stub):
            self.known_tokens_label.setText("未知（服务端未返回可用 usage）")
        else:
            self.known_tokens_label.setText(
                f"输入 {_number(summary.get('input_tokens'))}；输出 {_number(summary.get('output_tokens'))}；"
                f"总计 {_number(summary.get('total_tokens'))} tokens"
            )
        self.local_estimate_label.setText(
            f"{_number(summary.get('local_estimated_input_tokens'))} tokens（本地估算）"
        )
        totals = summary.get("estimated_cost_totals") or []
        costs = "\n".join(f"{row['amount']} {row['currency']}（按配置价估算）" for row in totals)
        self.estimated_cost_label.setText(costs or (
            "尚无可估算费用；未配置价格或服务端用量未知" if request_count else "尚无调用记录"
        ))
        failures = int(summary.get("recording_failure_count") or 0)
        failure_text = f"记录失败 {failures:,} 次"
        if summary.get("last_recording_error"):
            # Only reveal a fixed local-storage explanation, never exception
            # text that could contain credentials, private material or paths.
            failure_text += "；最近错误：本地调用记录未能保存"
        if failures:
            failure_text += "；已发送请求不会自动重发，累计统计可能不完整。"
        self.recording_failure_label.setText(failure_text)
        _fill_table(self.calls_table, [self._call_row(call) for call in calls])
        self.calls_note.setText(f"最近 {len(calls):,} 条（最多 {CALL_LIMIT} 条）；导出包含全部本机记录。")
        selected = self.profile_combo.currentData()
        current_models = {profile.profile_id: profile.model_id for profile in profiles}
        self._prices = {
            row["profile_id"]: row for row in prices
            if current_models.get(row["profile_id"]) == row.get("model_id")
        }
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in profiles:
            self.profile_combo.addItem(
                f"{profile.provider_name} · {profile.model_id}（{profile.profile_id}）", profile.profile_id,
            )
        index = self.profile_combo.findData(selected or "desktop-default")
        if index >= 0:
            self.profile_combo.setCurrentIndex(index)
        self.profile_combo.blockSignals(False)
        _fill_table(self.prices_table, [[
            _value(row.get(key), missing="未配置" if key == "cached_input_price_per_million" else "未知")
            for key in ("profile_id", "provider_id", "model_id", "currency", "input_price_per_million",
                        "output_price_per_million", "cached_input_price_per_million", "version")
        ] for row in prices])
        self._price_selected()
        self._set_busy(False)
        set_status(self.status, "attention" if failures else "info", "本机记录已刷新。")

    @staticmethod
    def _call_row(call: dict[str, Any]) -> list[str]:
        invoked = call.get("model_invoked")
        return [
            _value(call.get(key)) for key in
            ("call_id", "occurred_at", "profile_id", "provider_id", "model_id")
        ] + [
            API_STYLE_LABELS.get(str(call.get("api_style")), "未知接口"),
            TASK_LABELS.get(str(call.get("task_type")), "未知任务"),
            OUTCOME_LABELS.get(str(call.get("outcome")), "未知结果"),
            "未知" if invoked is None else ("是" if invoked else "否"),
            _value(call.get("http_status")), _number(call.get("latency_ms")),
            *[_number(call.get(key)) for key in ("local_input_estimate", "input_tokens", "output_tokens",
                                               "total_tokens", "cached_input_tokens", "reasoning_tokens")],
            _usage_text(call.get("usage_status")), _cost_text(call),
            _value(call.get("price_version"), missing="未配置"),
        ]

    def _price_selected(self, *_args) -> None:
        row = self._prices.get(self.profile_combo.currentData())
        for field, key in ((self.currency_input, "currency"), (self.input_price, "input_price_per_million"),
                           (self.output_price, "output_price_per_million"),
                           (self.cached_input_price, "cached_input_price_per_million")):
            value = row.get(key) if row else None
            field.setText("" if value is None else str(value))
        self.price_state.setText(
            f"已配置；价格版本 {_value(row.get('version'))}（按配置价估算）" if row else
            ("未配置价格" if self.profile_combo.count() else "尚无已保存的模型配置")
        )
        self._set_busy(self._active is not None)

    @staticmethod
    def _validate_price(field: QLineEdit, name: str, *, optional: bool = False) -> str | None:
        text = field.text().strip()
        if optional and not text:
            return None
        try:
            value = Decimal(text)
        except InvalidOperation:
            value = None
        if (not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text) or value is None
                or not value.is_finite() or value < 0):
            raise ValueError(f"{name}请填写大于或等于 0 的十进制数。")
        return text

    def save_price(self) -> None:
        profile_id = self.profile_combo.currentData()
        if self._active or self._closed or profile_id is None:
            return
        try:
            currency = self.currency_input.text().strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError("币种请填写三个英文字母，例如 USD 或 CNY。")
            input_price = self._validate_price(self.input_price, "输入单价")
            output_price = self._validate_price(self.output_price, "输出单价")
            cached_price = self._validate_price(self.cached_input_price, "缓存输入单价", optional=True)
        except ValueError as error:
            set_status(self.status, "attention", str(error))
            return
        self._set_busy(True)
        set_status(self.status, "info", "正在保存此模型配置的价格…")
        self._active = self.tasks.submit(
            "保存模型价格",
            lambda: self.facade.save_model_usage_price(
                profile_id, currency=currency, input_price_per_million=input_price,
                output_price_per_million=output_price, cached_input_price_per_million=cached_price,
            ),
            on_success=self._price_changed, on_failure=self._failed,
        )

    def remove_price(self) -> None:
        profile_id = self.profile_combo.currentData()
        if self._active or self._closed or profile_id not in self._prices:
            return
        self._set_busy(True)
        set_status(self.status, "info", "正在删除此模型配置的价格…")
        self._active = self.tasks.submit(
            "删除模型价格", lambda: self.facade.remove_model_usage_price(profile_id),
            on_success=self._price_changed, on_failure=self._failed,
        )

    def _price_changed(self, _value) -> None:
        if self._closed:
            return
        self._active = None
        self.refresh()

    def export_usage(self) -> None:
        if self._active or self._closed or not self._loaded_once:
            return
        filename, _filter = QFileDialog.getSaveFileName(
            self, "导出本机模型用量记录", DEFAULT_EXPORT_PATH, "JSON (*.json)",
        )
        if not filename:
            return
        destination = Path(filename)
        if destination.suffix.casefold() != ".json":
            destination = destination.with_suffix(".json")
        self._set_busy(True)
        set_status(self.status, "info", "正在导出本机用量记录…")
        self._active = self.tasks.submit(
            "导出模型用量", lambda: self.facade.export_model_usage(destination),
            on_success=self._exported, on_failure=self._failed,
        )

    def _exported(self, _path) -> None:
        if self._closed:
            return
        self._active = None
        self._set_busy(False)
        set_status(self.status, "success", "已导出 JSON 用量记录；费用为按配置价估算。")

    def _failed(self, _message) -> None:
        if self._closed:
            return
        self._active = None
        self._set_busy(False)
        set_status(self.status, "error", "本机用量操作未完成，请检查所选目录或本机存储后重试。")

    def _task_finished(self, task_id: str) -> None:
        if self._closed or task_id != self._active:
            return
        self._active = None
        self._set_busy(False)
        set_status(self.status, "attention", "本机用量操作已结束，可刷新记录。")

    def done(self, result: int) -> None:
        self._closed = True
        if self._active:
            self.tasks.cancel(self._active)
        super().done(result)


__all__ = ["ModelUsageDialog"]

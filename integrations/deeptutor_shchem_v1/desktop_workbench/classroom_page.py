"""Native offline classroom utilities. No student upload or model execution."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_classroom_models import ReversibleFirstOrderModel
from ..desktop_studio import Countdown, NoRepeatPicker, make_groups, parse_roster
from .components import CollapsibleSection, page_scroll, section_title, set_status
from .studio_templates import text_label
from .typography import ui_font


def button(text, callback, *, quiet=False):
    value = QPushButton(text)
    value.setObjectName("QuietButton" if quiet else "PrimaryAction")
    value.clicked.connect(callback)
    return value


class TimerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.clock = Countdown()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)
        root.addWidget(text_label("给学生留出真正独立思考的时间", "CardTitle"))
        self.display = QLabel("05:00")
        self.display.setObjectName("TimerDisplay")
        self.display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display.setAccessibleName("课堂倒计时")
        root.addWidget(self.display, 1)
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 180)
        self.minutes.setValue(5)
        self.minutes.setSuffix(" 分钟")
        self.minutes.setAccessibleName("倒计时时长")
        self.minutes.valueChanged.connect(lambda n: self.reset(n * 60))
        root.addWidget(self.minutes)
        self.controls = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.start = button("开始计时", self.toggle)
        self.controls.addWidget(self.start)
        self.controls.addWidget(button("重置", lambda: self.reset(self.minutes.value() * 60), quiet=True))
        self.controls.addWidget(button("投屏大字", self.present, quiet=True))
        root.addLayout(self.controls)
        self.status = text_label("倒计时独立于AI；切换页面不会暂停，关闭应用后不继续计时。")
        root.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.projection = None
        self.timer.start()

    def toggle(self):
        if self.clock.deadline is None:
            self.clock.start()
        else:
            self.clock.pause()
        self.tick()

    def reset(self, seconds):
        self.clock.reset(seconds)
        self.tick()
        set_status(self.status, "info", "已重置，点击开始计时。切换页面不会暂停。")

    def tick(self):
        expired = self.clock.deadline is not None and self.clock.seconds == 0
        if expired:
            self.clock.pause()
        self.display.setText(self.clock.label())
        self.start.setText("暂停" if self.clock.deadline is not None else "开始 / 继续")
        if self.projection is not None:
            self.projection.setText(self.clock.label())
        if expired:
            set_status(self.status, "success", "时间到。先收集学生思路，再进入讲评。")

    def present(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("课堂倒计时 · Esc退出")
        layout = QVBoxLayout(dialog)
        display = QLabel(self.clock.label())
        display.setStyleSheet("font-size: 140px; font-weight: 700; color: #195F41;")
        display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(display, 1)
        layout.addWidget(button("返回工作台（Esc）", dialog.accept, quiet=True))
        self.projection = display
        dialog.showFullScreen()
        dialog.exec()
        self.projection = None
        dialog.deleteLater()

    def resizeEvent(self, event):
        self.controls.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 500 else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)


class ParticipationPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.picker = None
        self.picker_roster = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)
        root.addWidget(text_label("随机点名与均衡分组", "CardTitle"))
        root.addWidget(text_label("建议使用学号。名单只在当前窗口内使用，不保存、不发送给AI；关闭应用即清除。"))
        row = QHBoxLayout()
        self.class_size = QSpinBox()
        self.class_size.setRange(1, 300)
        self.class_size.setValue(40)
        self.class_size.setSuffix(" 人")
        row.addWidget(self.class_size)
        row.addWidget(button("填入顺序学号", self.number_roster, quiet=True))
        root.addLayout(row)
        self.roster = QPlainTextEdit()
        self.roster.setPlaceholderText("每行一个唯一学号或代号，例如：01\n02\n03")
        self.roster.setMaximumHeight(145)
        self.roster.setAccessibleName("课堂参与名单，仅保存在内存")
        root.addWidget(self.roster)
        self.draw_display = QLabel("准备邀请一位同学")
        self.draw_display.setObjectName("DrawDisplay")
        self.draw_display.setWordWrap(True)
        self.draw_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.draw_display)
        draw_row = QHBoxLayout()
        draw_row.addWidget(button("随机点名（本轮不重复）", self.draw))
        draw_row.addWidget(button("重开一轮", self.reset_round, quiet=True))
        root.addLayout(draw_row)
        group_row = QHBoxLayout()
        self.group_count = QSpinBox()
        self.group_count.setRange(1, 30)
        self.group_count.setValue(6)
        self.group_count.setSuffix(" 组")
        self.group_count.setAccessibleName("课堂分组数量")
        group_row.addWidget(self.group_count)
        group_row.addWidget(button("随机均衡分组", self.group, quiet=True))
        root.addLayout(group_row)
        self.groups = QPlainTextEdit()
        self.groups.setReadOnly(True)
        self.groups.setMinimumHeight(120)
        root.addWidget(self.groups)
        root.addWidget(button("复制分组结果", lambda: QApplication.clipboard().setText(self.groups.toPlainText()), quiet=True))
        self.status = text_label("请先输入名单；名单改变会开启新一轮点名。")
        root.addWidget(self.status)
        root.addStretch(1)
        self.roster.textChanged.connect(self._roster_changed)

    def _roster_changed(self):
        self.reset_round()
        self.groups.clear()

    def number_roster(self):
        self.roster.setPlainText("\n".join(f"{n:02d}" for n in range(1, self.class_size.value() + 1)))
        self.reset_round()

    def reset_round(self):
        self.picker = None
        self.picker_roster = None
        self.draw_display.setText("新一轮点名")
        set_status(self.status, "info", "新一轮已准备，点名时不重复。")

    def draw(self):
        try:
            entries = parse_roster(self.roster.toPlainText())
        except ValueError as exc:
            set_status(self.status, "attention", str(exc))
            return
        if entries != self.picker_roster:
            self.picker = NoRepeatPicker(entries)
            self.picker_roster = entries
        result = self.picker.draw()
        if result is None:
            set_status(self.status, "attention", "本轮已全部抽完，点击“重开一轮”继续。")
            return
        self.draw_display.setText(result)
        set_status(self.status, "info", f"本轮尚未抽到 {len(self.picker.remaining)} 人。")

    def group(self):
        try:
            groups = make_groups(parse_roster(self.roster.toPlainText()), self.group_count.value())
        except ValueError as exc:
            set_status(self.status, "attention", str(exc))
            return
        self.groups.setPlainText("\n\n".join(f"第{i}组  ·  {len(group)}人\n" + "、".join(group)
                                            for i, group in enumerate(groups, 1)))
        set_status(self.status, "success", "已按人数均衡随机分组；未更改点名轮次，未保存名单。")


class EquilibriumCanvas(QWidget):
    """Two quantities on a fixed, shared time axis; traces never slide or rescale time."""

    def __init__(self, parent=None, *, projection=False):
        super().__init__(parent)
        self.projection = projection
        self.model = ReversibleFirstOrderModel()
        self.elapsed, self.duration = 0., 15.
        self.state = self.model.at(0)
        self.trace = (self.state,)
        self.a, self.b = self.state.a, self.state.b
        self.setMinimumHeight(380)
        self.setAccessibleName("浓度和正逆反应速率随时间曲线，时间单位秒，浓度摩尔每升")

    def set_snapshot(self, model, elapsed, duration):
        self.model, self.duration = model, duration
        self.elapsed = min(elapsed, duration)
        self.state = model.at(self.elapsed)
        self.a, self.b = self.state.a, self.state.b
        self.trace = model.samples(self.elapsed) if self.elapsed else (self.state,)
        self.update()

    def plot_rects(self):
        margin, gap = 12., 28.
        if self.width() >= 860:
            width = (self.width() - 2 * margin - gap) / 2
            return (QRectF(margin, 4, width, self.height() - 8),
                    QRectF(margin + width + gap, 4, width, self.height() - 8))
        height = (self.height() - gap - 8) / 2
        return (QRectF(margin, 4, self.width() - 2 * margin, height),
                QRectF(margin, height + gap + 4, self.width() - 2 * margin, height))

    @staticmethod
    def _number(value):
        return f"{value:.3g}"

    def _plot(self, painter, area, *, rate=False):
        large = self.projection
        font_size = 22 if large else 12
        title_size = 24 if large else 16
        left = 105 if large else 72
        chart = area.adjusted(left, 96 if large else 73, -12, -78)
        if chart.width() < 35 or chart.height() < 35:
            return
        painter.setPen(QColor("#233A32"))
        painter.setFont(ui_font(title_size))
        title = "正逆反应速率随时间" if rate else "浓度随时间"
        painter.drawText(QRectF(area.left(), area.top(), area.width(), 38), Qt.AlignmentFlag.AlignCenter, title)
        painter.setFont(ui_font(20 if large else 12))
        legend = "正向 实线 / 逆向 虚线 · mol·L⁻¹·s⁻¹" if rate else "[A] 实线 / [B] 虚线 · mol·L⁻¹"
        painter.drawText(QRectF(area.left(), area.top() + 40, area.width(), 32), Qt.AlignmentFlag.AlignCenter, legend)
        initial = self.model.at(0)
        maximum = (max(initial.forward_rate, initial.reverse_rate) * 1.1 if rate else self.model.total)
        maximum = maximum if maximum > 0 else 1.
        painter.setFont(ui_font(font_size))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for tick in range(5):
            fraction = tick / 4
            y = chart.bottom() - fraction * chart.height()
            x = chart.left() + fraction * chart.width()
            painter.setPen(QPen(QColor("#DCE5DF"), 1))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
            painter.setPen(QColor("#233A32"))
            painter.drawText(QRectF(area.left(), y - 18, left - 9, 36),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._number(maximum * fraction))
            painter.drawText(QRectF(x - 42, chart.bottom() + 3, 84, 34),
                             Qt.AlignmentFlag.AlignCenter, self._number(self.duration * fraction))
        painter.setPen(QPen(QColor("#6C8175"), 1.5))
        painter.drawLine(chart.bottomLeft(), chart.topLeft())
        painter.drawLine(chart.bottomLeft(), chart.bottomRight())
        painter.drawText(QRectF(chart.left(), chart.bottom() + 37, chart.width(), 35),
                         Qt.AlignmentFlag.AlignCenter, "时间 t / s")
        for index, color in enumerate(("#176B43", "#995C00")):
            path = QPainterPath()
            for number, state in enumerate(self.trace):
                values = (state.forward_rate, state.reverse_rate) if rate else (state.a, state.b)
                point = QPointF(chart.left() + chart.width() * state.t / self.duration,
                                chart.bottom() - chart.height() * values[index] / maximum)
                if number:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
            pen = QPen(QColor(color), 5 if large else 3)
            if index:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)
            state = self.trace[-1]
            values = (state.forward_rate, state.reverse_rate) if rate else (state.a, state.b)
            endpoint = QPointF(chart.left() + chart.width() * state.t / self.duration,
                               chart.bottom() - chart.height() * values[index] / maximum)
            painter.setBrush(QColor(color))
            painter.drawEllipse(endpoint, 4, 4)
            painter.setBrush(Qt.BrushStyle.NoBrush)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#F8FBF9"))
        concentration, rate = self.plot_rects()
        self._plot(painter, concentration)
        self._plot(painter, rate, rate=True)

    def resizeEvent(self, event):
        self.setMinimumHeight(380 if self.width() >= 860 else 680)
        super().resizeEvent(event)


class EquilibriumProjection(QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.setWindowTitle("动态平衡 · 授课展示")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 12)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(32, 20, 32, 20)
        title = text_label("平衡时，两种物质的浓度必须相等吗？")
        title.setFont(ui_font(26))
        title.setStyleSheet("font-size: 26pt; color: #233A32; font-weight: 600;")
        root.addWidget(title)
        self.parameters = text_label("")
        self.parameters.setFont(ui_font(20))
        self.parameters.setStyleSheet("font-size: 20pt; color: #233A32;")
        root.addWidget(self.parameters)
        self.canvas = EquilibriumCanvas(projection=True)
        root.addWidget(self.canvas, 1)
        self.summary, self.rates = text_label(""), text_label("")
        for label in (self.summary, self.rates):
            label.setFont(ui_font(22))
            label.setStyleSheet("font-size: 22pt; color: #233A32;")
            root.addWidget(label)
        self.criterion = text_label("")
        self.criterion.setFont(ui_font(22))
        self.criterion.setStyleSheet("font-size: 22pt; color: #233A32;")
        root.addWidget(self.criterion)
        outer.addWidget(page_scroll(content), 1)
        row = QHBoxLayout()
        self.start = button("开始", panel.toggle)
        row.addWidget(self.start)
        row.addWidget(button("重置", panel.reset, quiet=True))
        row.addWidget(button("返回工作台（Esc）", self.reject, quiet=True))
        for index in range(row.count()):
            row.itemAt(index).widget().setFont(ui_font(18))
            row.itemAt(index).widget().setStyleSheet("font-size: 18pt;")
        outer.addLayout(row)
        panel.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        panel = self.panel
        self.parameters.setText("恒温恒容 · 一级可逆反应 A ⇌ B；v正 = k正[A]，v逆 = k逆[B]\n" + panel.model_caption.text())
        self.canvas.set_snapshot(panel.model, panel.elapsed, panel.duration.value())
        self.summary.setText(panel.summary.text())
        self.rates.setText(panel.rates.text())
        self.criterion.setText(panel.criterion.text())
        self.start.setText(panel.start.text())

    def done(self, result):
        self.panel.changed.disconnect(self.refresh)
        super().done(result)


class EquilibriumPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ClassroomEquilibriumPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#ClassroomEquilibriumPanel { background: #F8F7F2; }")
        self.running = False
        self.elapsed = 0.
        self.last_tick = time.monotonic()
        self.projection = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)
        root.addWidget(text_label("动态平衡 · 比较正逆反应速率", "CardTitle"))
        root.addWidget(text_label("恒温恒容 · 封闭体系 · 一级可逆反应 A ⇌ B（A 与 B 按 1∶1 转化）"))
        self.model_caption = text_label("")
        root.addWidget(self.model_caption)
        self.canvas = EquilibriumCanvas()
        root.addWidget(self.canvas, 1)
        self.summary, self.rates, self.criterion = text_label(""), text_label(""), text_label("")
        for label in (self.summary, self.rates, self.criterion):
            label.setFont(ui_font(13))
            label.setStyleSheet("font-size: 13pt; color: #233A32;")
            root.addWidget(label)
        self.controls = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.start = button("开始", self.toggle)
        self.controls.addWidget(self.start)
        self.controls.addWidget(button("重置", self.reset, quiet=True))
        self.controls.addWidget(button("授课大字", self.present, quiet=True))
        self.controls.addWidget(button("保存示意截图", self.save_image, quiet=True))
        root.addLayout(self.controls)
        self.settings = CollapsibleSection("模型参数与原理（修改后从初值重置）")
        form = QGridLayout()
        self.parameter_grid = form
        self.parameter_rows = []
        self._parameter_columns = None
        self.forward, self.reverse = QDoubleSpinBox(), QDoubleSpinBox()
        self.initial, self.initial_b = QDoubleSpinBox(), QDoubleSpinBox()
        for index, (box, value, title, unit) in enumerate((
                (self.forward, .3, "正向速率常数 k正", " s⁻¹"),
                (self.reverse, .1, "逆向速率常数 k逆", " s⁻¹"),
                (self.initial, 1., "初始浓度 [A]₀", " mol·L⁻¹"),
                (self.initial_b, 0., "初始浓度 [B]₀", " mol·L⁻¹"))):
            box.setRange(0, 10)
            box.setSingleStep(.05)
            box.setDecimals(3)
            box.setSuffix(unit)
            box.setValue(value)
            box.setAccessibleName(title + unit)
            label = text_label(title)
            self.parameter_rows.append((label, box))
            form.addWidget(label, index // 2, (index % 2) * 2)
            form.addWidget(box, index // 2, (index % 2) * 2 + 1)
            box.valueChanged.connect(self.reset)
        self.duration, self.speed = QDoubleSpinBox(), QDoubleSpinBox()
        self.duration.setRange(.1, 36000)
        self.duration.setValue(15)
        self.duration.setSuffix(" s")
        self.duration.setAccessibleName("模型观察时长，秒；改变后重置")
        self.speed.setRange(.1, 20)
        self.speed.setValue(1)
        self.speed.setSuffix(" 倍")
        self.speed.setAccessibleName("播放倍率，一秒播放经过的模型秒数；改变后重置")
        duration_label, speed_label = text_label("观察时间"), text_label("播放倍率")
        self.parameter_rows.extend(((duration_label, self.duration), (speed_label, self.speed)))
        form.addWidget(duration_label, 2, 0)
        form.addWidget(self.duration, 2, 1)
        form.addWidget(speed_label, 2, 2)
        form.addWidget(self.speed, 2, 3)
        self.duration.valueChanged.connect(self.reset)
        self.speed.valueChanged.connect(self.reset)
        self.settings.content_layout.addLayout(form)
        self.scale = text_label("")
        self.settings.content_layout.addWidget(self.scale)
        examples = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        examples.addWidget(button("浓度相等，速率不等", lambda: self.preset(.5, .5), quiet=True))
        examples.addWidget(button("浓度不等，已在平衡", lambda: self.preset(.25, .75), quiet=True))
        examples.addWidget(button("观察 6τ", self.observe_relaxation, quiet=True))
        self.examples = examples
        self.settings.content_layout.addLayout(examples)
        self.settings.content_layout.addWidget(text_label(
            "v正 = k正[A]；v逆 = k逆[B]；d[A]/dt = −v正 + v逆；[A]+[B] = C₀。\n"
            "k正+k逆>0 时，解析解：[A](t) = [A]平 + ([A]₀−[A]平)e⁻⁽ᵏ正⁺ᵏ逆⁾ᵗ；"
            "[A]平 = C₀k逆/(k正+k逆)，τ = 1/(k正+k逆)。\n"
            "接近平衡的显示阈值：正逆速率差 / 本次参考速率 ≤ 1%；"
            "参考速率取初始正向、初始逆向和理论平衡速率的最大值。"
        ))
        root.addWidget(self.settings)
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.reset()

    def toggle(self):
        if self.elapsed >= self.duration.value():
            self.reset()
        self.running = not self.running
        self.last_tick = time.monotonic()
        self.render()

    def reset(self, *_):
        self.running = False
        self.elapsed = 0.
        self.last_tick = time.monotonic()
        self.model = ReversibleFirstOrderModel(kf=self.forward.value(), kr=self.reverse.value(),
                                              a0=self.initial.value(), b0=self.initial_b.value())
        self.render()

    def preset(self, a0, b0):
        for box, value in ((self.forward, .3), (self.reverse, .1), (self.initial, a0), (self.initial_b, b0)):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        self.reset()

    def observe_relaxation(self):
        self.duration.setValue(min(36000, max(.1, self.model.recommended_duration())))
        self.reset()

    def tick(self):
        now = time.monotonic()
        dt = max(0., now - self.last_tick) * self.speed.value()
        self.last_tick = now
        if not self.running:
            return
        self.elapsed = min(self.duration.value(), self.elapsed + dt)
        if self.elapsed >= self.duration.value():
            self.running = False
        self.render()

    def render(self):
        state = self.model.at(self.elapsed)
        self.model_caption.setText(
            f"k正 = {self.model.kf:g} s⁻¹，k逆 = {self.model.kr:g} s⁻¹；"
            f"[A]₀ = {self.model.a0:g}，[B]₀ = {self.model.b0:g} mol·L⁻¹\n"
            f"观察 0–{self.duration.value():g} s；播放 {self.speed.value():g} 倍"
        )
        self.canvas.set_snapshot(self.model, self.elapsed, self.duration.value())
        self.summary.setText(f"t = {state.t:.2f} s  |  [A] = {state.a:.3f}，[B] = {state.b:.3f} mol·L⁻¹  |  总浓度 {self.model.total:.3f} mol·L⁻¹")
        self.rates.setText(f"v正 = {state.forward_rate:.4g}，v逆 = {state.reverse_rate:.4g} mol·L⁻¹·s⁻¹")
        if state.regime == "dynamic":
            criterion = "动态平衡：v正 = v逆 > 0；正逆反应仍在进行，浓度保持不变。"
        elif state.regime == "stationary":
            criterion = "k正 = k逆 = 0：两方向速率均为零，组成保持初值。"
        elif state.regime == "empty":
            criterion = "总浓度为零：两个方向都没有物质转化。"
        elif state.regime == "one_way":
            initial = self.model.at(0)
            if initial.forward_rate == 0 and initial.reverse_rate == 0:
                criterion = "单向终态：可转化一侧浓度已为零，两个方向速率均为零，组成保持不变。"
            elif state.forward_rate == 0 and state.reverse_rate == 0:
                criterion = "单向过程趋近终态：两个方向的速率均趋于零。"
            else:
                criterion = "单向转化极限：一个速率常数为零，物质向另一侧转化。"
        elif state.near_equilibrium:
            criterion = "接近平衡：|v正−v逆| / max(v正₀, v逆₀, v平) ≤ 1%；理论上渐近到平衡。"
        else:
            criterion = "仍在趋近平衡：v正 ≠ v逆。平衡依据是速率相等，浓度曲线相交不代表平衡。"
        self.criterion.setText(criterion)
        relaxation = self.model.relaxation_time
        self.scale.setText(f"τ = {relaxation:.3g} s；" if relaxation is not None else "k正+k逆 = 0；")
        self.scale.setText(self.scale.text() + f"横轴固定 0–{self.duration.value():g} s；每秒播放经过 {self.speed.value():g} 模型秒。")
        self.start.setText("暂停" if self.running else ("重播" if self.elapsed >= self.duration.value() else "继续" if self.elapsed else "开始"))
        self.changed.emit()

    def present(self):
        if self.projection is None:
            self.projection = EquilibriumProjection(self)
            self.projection.finished.connect(self._projection_finished)
        self.projection.showFullScreen()

    def _projection_finished(self, *_):
        dialog, self.projection = self.projection, None
        if dialog is not None:
            dialog.deleteLater()

    def save_image(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存教学模型示意图", "E:/任务输出/上海高中化学等级考/动态平衡模型.png", "PNG (*.png)")
        if path and not self.grab().save(path):
            set_status(self.summary, "error", "截图未保存，请检查目录后重试。")

    def resizeEvent(self, event):
        direction = QBoxLayout.Direction.TopToBottom if self.width() < 620 else QBoxLayout.Direction.LeftToRight
        self.controls.setDirection(direction)
        self.examples.setDirection(direction)
        columns = 1 if self.width() < 720 else 2
        if columns != self._parameter_columns:
            for label, field in self.parameter_rows:
                self.parameter_grid.removeWidget(label)
                self.parameter_grid.removeWidget(field)
            for index, (label, field) in enumerate(self.parameter_rows):
                row, column = divmod(index, columns)
                self.parameter_grid.addWidget(label, row, column * 2)
                self.parameter_grid.addWidget(field, row, column * 2 + 1)
            self._parameter_columns = columns
        super().resizeEvent(event)

    def closeEvent(self, event):
        self.timer.stop()
        self.running = False
        if self.projection is not None:
            self.projection.close()
        super().closeEvent(event)


class FeedbackPanel(QWidget):
    reference_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(text_label('随堂反馈 · 把本节反馈带回下一次备课', "CardTitle"))
        root.addWidget(text_label("由教师手动汇总，不是学生端联网投票，也不自动判定知识掌握。"))
        self.question = QLineEdit()
        self.question.setPlaceholderText("本次检测目标或问题，例如：解释平衡时正逆速率的关系")
        self.question.setAccessibleName('课堂随堂反馈问题')
        root.addWidget(self.question)
        form = QFormLayout()
        self.counts = []
        for label in ("达成当前任务", "需要再讲", "尚未完成"):
            spin = QSpinBox()
            spin.setRange(0, 300)
            spin.setAccessibleName(label + "人数")
            form.addRow(label, spin)
            self.counts.append((label, spin))
        root.addLayout(form)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("记录具体错因和下一步安排；缺少作答证据时不要猜测学生能力。")
        self.notes.setAccessibleName("教师课堂反馈备注")
        root.addWidget(self.notes, 1)
        row = QHBoxLayout()
        row.addWidget(button("追加到备课资料 →", self.send))
        row.addWidget(button("另存反馈 JSON", self.export, quiet=True))
        root.addLayout(row)
        self.status = text_label("填入检测问题及至少一项人数，便可保存或追加；不自动调用AI。")
        root.addWidget(self.status)

    def report(self):
        counts = {name: spin.value() for name, spin in self.counts}
        if not self.question.text().strip() or sum(counts.values()) == 0:
            raise ValueError("请填写检测问题与至少一项人数。")
        return {"kind": "teacher_entered_exit_ticket", "created_at": datetime.now().astimezone().isoformat(),
                "question": self.question.text().strip(), "counts": counts, "total": sum(counts.values()),
                "notes": self.notes.toPlainText(), "source": "教师手动汇总，非学生端实时采集"}

    def send(self):
        try:
            report = self.report()
        except ValueError as exc:
            set_status(self.status, "attention", str(exc))
            return
        text = ('【课堂随堂反馈 · 教师手动汇总】\n' + report["created_at"] + "\n问题：" + report["question"] +
                "\n" + "；".join(f"{k} {v}人" for k, v in report["counts"].items()) +
                "\n教师备注：" + report["notes"] + "\n仅为本次任务反馈，不直接推断长期掌握程度。")
        self.reference_requested.emit(text)

    def export(self):
        try:
            report = self.report()
        except ValueError as exc:
            set_status(self.status, "attention", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存课堂反馈", '课堂随堂反馈.json', "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            set_status(self.status, "error", "反馈未保存，请检查目录权限。")
        else:
            set_status(self.status, "success", "反馈已保存到所选文件，未上传。")


class ClassroomPage(QWidget):
    reference_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(16)
        root.addWidget(section_title("课堂工具", "不依赖网络与API，把互动、观察和即时反馈接回教学流程。"))
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.timer_panel = TimerPanel()
        self.participation = ParticipationPanel()
        self.equilibrium = EquilibriumPanel()
        self.feedback = FeedbackPanel()
        for title, panel in (("倒计时", self.timer_panel), ("点名与分组", self.participation),
                             ("动态平衡", self.equilibrium), ('随堂反馈', self.feedback)):
            self.tabs.addTab(panel, title)
        self.feedback.reference_requested.connect(self.reference_requested)
        root.addWidget(self.tabs, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(content))

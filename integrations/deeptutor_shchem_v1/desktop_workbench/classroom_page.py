"""Native offline classroom utilities. No student upload or model execution."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSlider,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)
from ..desktop_studio import Countdown, NoRepeatPicker, equilibrium_step, make_groups, parse_roster
from .components import page_scroll, section_title, set_status
from .studio_templates import text_label


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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.a, self.b, self.kf, self.kr = 100., 0., .3, .1
        self.trace = [(100., 0.)]
        self.setMinimumHeight(260)
        self.setAccessibleName("一级可逆反应模型的相对浓度与变化曲线")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#F8FBF9"))
        width, height = self.width(), self.height()
        painter.setPen(QColor("#233A32"))
        painter.setFont(QFont("Microsoft YaHei UI", 16))
        painter.drawText(QRectF(0, 6, width, 32), Qt.AlignmentFlag.AlignCenter, "A  ⇌  B")
        colors = ("#3B9567", "#DCAC50")
        bar_width = max(24, min(70, width * .13))
        for i, (value, name) in enumerate(((self.a, "A"), (self.b, "B"))):
            x = width * (.14 if i == 0 else .34)
            area = QRectF(x, 60, bar_width, height - 112)
            painter.setPen(QPen(QColor("#BED4C6"), 1))
            painter.setBrush(QColor("#EAF1ED"))
            painter.drawRoundedRect(area, 8, 8)
            level = area.height() * value / 100
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors[i]))
            painter.drawRoundedRect(QRectF(x, area.bottom() - level, bar_width, level), 6, 6)
            painter.setPen(QColor("#233A32"))
            painter.setFont(QFont("Microsoft YaHei UI", 11))
            painter.drawText(QRectF(x - 20, area.bottom() + 8, bar_width + 40, 32), Qt.AlignmentFlag.AlignCenter, f"{name}  {value:.1f}")
        chart = QRectF(width * .55, 60, max(20, width * .4 - 12), height - 112)
        # Trace paths are lines, not closed areas. The bars above used a fill.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#BECFC5"), 1))
        painter.drawLine(chart.bottomLeft(), chart.topLeft())
        painter.drawLine(chart.bottomLeft(), chart.bottomRight())
        for index, color in enumerate(colors):
            path = QPainterPath()
            for i, pair in enumerate(self.trace):
                point = QPointF(chart.left() + chart.width() * i / max(1, len(self.trace) - 1),
                                chart.bottom() - chart.height() * pair[index] / 100)
                if i:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
            painter.setPen(QPen(QColor(color), 2.5))
            painter.drawPath(path)
        painter.setPen(QColor("#60746B"))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.drawText(QRectF(chart.left(), chart.bottom() + 10, chart.width(), 25), Qt.AlignmentFlag.AlignCenter, "最近过程 · 绿色A / 金色B")


class EquilibriumPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)
        root.addWidget(text_label("动态平衡 · 看得见的双向变化", "CardTitle"))
        root.addWidget(text_label("封闭一级可逆模型 A ⇌ B；总相对浓度100，速率常数与时间均为任意模型单位。不是实际反应的实验数据。"))
        self.canvas = EquilibriumCanvas()
        root.addWidget(self.canvas, 1)
        form = QFormLayout()
        self.forward = QDoubleSpinBox()
        self.reverse = QDoubleSpinBox()
        for box, value, title in ((self.forward, .3, "A→B速率常数"), (self.reverse, .1, "B→A速率常数")):
            box.setRange(0, 2)
            box.setSingleStep(.05)
            box.setDecimals(2)
            box.setValue(value)
            box.setAccessibleName(title)
            form.addRow(title, box)
            box.valueChanged.connect(lambda *_: self.render())
        self.initial = QSlider(Qt.Orientation.Horizontal)
        self.initial.setRange(0, 100)
        self.initial.setValue(100)
        self.initial.setAccessibleName("初始A相对浓度")
        self.initial.valueChanged.connect(self.reset)
        form.addRow("初始A（改变后重置）", self.initial)
        root.addLayout(form)
        self.summary = text_label("")
        root.addWidget(self.summary)
        controls = QHBoxLayout()
        self.start = button("播放", self.toggle)
        controls.addWidget(self.start)
        controls.addWidget(button("重置", self.reset, quiet=True))
        controls.addWidget(button("保存示意截图", self.save_image, quiet=True))
        root.addLayout(controls)
        root.addWidget(text_label("观察任务：达到平衡时，A和B是否必须相等？比较正逆速率，再解释你的判断。"))
        self.running = False
        self.elapsed = 0.
        self.last_tick = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.render()

    def toggle(self):
        self.running = not self.running
        self.last_tick = time.monotonic()
        self.start.setText("暂停" if self.running else "播放")

    def reset(self, *_):
        self.running = False
        self.elapsed = 0.
        self.canvas.a = float(self.initial.value())
        self.canvas.b = 100 - self.canvas.a
        self.canvas.trace = [(self.canvas.a, self.canvas.b)]
        self.start.setText("播放")
        self.render()

    def tick(self):
        now = time.monotonic()
        dt = now - self.last_tick
        self.last_tick = now
        if not self.running:
            return
        self.elapsed += dt
        self.canvas.a, self.canvas.b = equilibrium_step(self.canvas.a, 100, self.forward.value(), self.reverse.value(), dt)
        self.canvas.trace.append((self.canvas.a, self.canvas.b))
        self.canvas.trace = self.canvas.trace[-180:]
        self.render()

    def render(self):
        forward, reverse = self.forward.value() * self.canvas.a, self.reverse.value() * self.canvas.b
        self.summary.setText(f"t = {self.elapsed:.1f}  |  A = {self.canvas.a:.2f}，B = {self.canvas.b:.2f}  |  正向速率 {forward:.2f}，逆向速率 {reverse:.2f}")
        self.canvas.update()

    def save_image(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存教学模型示意图", "动态平衡模型.png", "PNG (*.png)")
        if path and not self.grab().save(path):
            set_status(self.summary, "error", "截图未保存，请检查目录后重试。")


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

"""Page-bound, teacher-edited narration using only a local SAPI voice."""
from copy import deepcopy
from PySide6.QtCore import QLocale
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                              QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)
from ..desktop_lesson_narration import MAX_PAGE_TEXT, MAX_TOTAL_TEXT


def local_speech(parent):
    from PySide6.QtTextToSpeech import QTextToSpeech
    if "sapi" not in QTextToSpeech.availableEngines():
        raise ValueError("本机没有可用的SAPI朗读引擎；仍可整理和保存讲稿。")
    speech = QTextToSpeech("sapi", parent)
    if speech.state() == QTextToSpeech.State.Error:
        speech.deleteLater()
        raise ValueError("本机朗读引擎未就绪；仍可整理和保存讲稿。")
    return speech


class PptNarrationPanel(QWidget):
    def __init__(self, preview, save, *, speech_factory=None):
        super().__init__(preview)
        self.preview, self.save = preview, save
        self.source = None
        self.scripts = self.original_scripts = []
        self.number = None
        self.ready = self.closed = self.dirty = self.loading = False
        self.generation = 0
        self.task = self.speech = None
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel("逐页讲稿")
        root.addWidget(self.heading)
        hint = QLabel("先写适合口播的讲稿；公式、条件和图片请用物质名称说明。点击朗读才发声，翻页会停止。讲稿应用到备课后随草稿保存。")
        hint.setWordWrap(True); root.addWidget(hint)
        self.editor = QPlainTextEdit(); self.editor.setAccessibleName("当前课件页讲解稿")
        self.editor.setPlaceholderText("输入本页讲解稿，或明确追加本页可提取的文字，再核对修订。")
        self.editor.setMinimumHeight(70); root.addWidget(self.editor, 1)
        row = QHBoxLayout(); root.addLayout(row)
        self.read_button = QPushButton("追加本页文字"); self.read_button.clicked.connect(self.read_text)
        self.apply_button = QPushButton("应用讲稿到备课"); self.apply_button.clicked.connect(self.commit)
        self.discard_button = QPushButton("撤销未应用讲稿"); self.discard_button.clicked.connect(self.discard)
        for w in (self.read_button, self.apply_button, self.discard_button):row.addWidget(w)
        voice = QHBoxLayout(); root.addLayout(voice)
        self.voices = QComboBox(); self.voices.setAccessibleName("本机朗读声音")
        self.rate = QDoubleSpinBox(); self.rate.setRange(-.5, .5); self.rate.setSingleStep(.1)
        self.rate.setPrefix("语速 ")
        self.rate.setToolTip("0为默认语速；调小变慢，调大变快。")
        self.rate.setAccessibleName("本机朗读语速")
        self.play_button = QPushButton("朗读本页"); self.play_button.clicked.connect(self.play)
        self.pause_button = QPushButton("暂停"); self.pause_button.clicked.connect(self.pause)
        self.stop_button = QPushButton("停止"); self.stop_button.clicked.connect(self.stop)
        for w in (self.voices, self.rate, self.play_button, self.pause_button, self.stop_button):voice.addWidget(w)
        self.status = QLabel(""); self.status.setWordWrap(True); root.addWidget(self.status)
        self.editor.textChanged.connect(self.edited)
        self.voices.currentIndexChanged.connect(self.stop)
        self.rate.valueChanged.connect(self.stop)
        try:
            self.speech = (speech_factory or local_speech)(self)
            options = self.speech.availableVoices()
            for value in options:self.voices.addItem(value.name(), value)
            chinese = next((i for i, v in enumerate(options) if v.locale().language() == QLocale.Language.Chinese), 0)
            self.voices.setCurrentIndex(chinese)
            self.speech.stateChanged.connect(lambda *_: self.controls())
            self.speech.errorOccurred.connect(lambda *_: self.failed("本机朗读失败，请检查所选声音；讲稿保留。"))
            self.status.setText("本机朗读就绪；声音与化学术语读法仍须核听。")
        except (ImportError, RuntimeError, ValueError):
            if self.speech:self.speech.deleteLater()
            self.speech = None
            self.status.setText("本机朗读暂不可用；仍可整理、应用和保存讲稿。")
        self.controls()

    def bind(self, source, scripts):
        if len(scripts) != source.count:raise ValueError("讲稿页数与本次课件不一致。")
        self.source = source
        self.scripts = deepcopy(scripts); self.original_scripts = deepcopy(scripts)
        self.select_page(1)

    def select_page(self, number):
        if self.number == number:return
        self._store()
        self.stop(); self.number = number; self.ready = False
        self.loading = True
        self.editor.setPlainText(self.scripts[number-1] if self.scripts else "")
        self.loading = False
        audience = "学生课件" if self.preview.report.get("audience") == "student" else "教师课件（可含答案）"
        self.heading.setText(f"{audience} · 第{number}页讲稿")
        self.controls()

    def _store(self):
        if self.scripts and self.number:self.scripts[self.number-1] = self.editor.toPlainText()

    def edited(self):
        if self.loading:return
        self._store(); self.dirty = self.scripts != self.original_scripts
        self.stop(); self.controls()

    def stop(self, *_):
        self.generation += 1
        if self.task and self.task != "submitting":self.preview.tasks.cancel(self.task)
        self.task = None
        if self.speech:self.speech.stop()
        self.controls()

    def set_ready(self, ready):
        self.ready = ready
        if not ready:self.stop()
        self.controls()

    def controls(self):
        ready = self.ready and self.source is not None and not self.closed
        state = self.speech.state().name if self.speech else ""
        self.read_button.setEnabled(ready and self.task is None)
        self.editor.setEnabled(self.source is not None and not self.closed)
        self.apply_button.setEnabled(self.source is not None and not self.closed and self.dirty)
        self.discard_button.setEnabled(self.dirty and not self.closed)
        self.play_button.setEnabled(ready and self.task is None and self.speech is not None
                                    and self.voices.count() > 0 and bool(self.editor.toPlainText().strip())
                                    and state not in ("Error", "Speaking", "Paused"))
        self.pause_button.setEnabled(ready and state in ("Speaking", "Paused"))
        self.pause_button.setText("继续" if state == "Paused" else "暂停")
        self.stop_button.setEnabled(self.task is not None or state in ("Speaking", "Paused"))

    def failed(self, message):
        if self.closed:return
        self.task = None; self.status.setText(message); self.controls()

    def _checked_action(self, label, operation, finished):
        if self.closed or not self.ready or self.source is None:return
        self.stop(); token = self.generation; number = self.number; source = self.source
        def work():
            source.verify_file()
            if self.preview.verify_presentation:self.preview.verify_presentation()
            return operation(source, number)
        def done(value):
            if self.closed or token != self.generation or number != self.number:return
            self.task = None; finished(value); self.controls()
        def fail(_message):
            if self.closed or token != self.generation:return
            self.failed("原课件或预览已变化，或本页读取失败；当前讲稿保留，请返回重新核对。")
        self.task = "submitting"
        try:
            task = self.preview.tasks.submit(label, work, on_success=done, on_failure=fail)
        except (RuntimeError, ValueError, OSError):
            fail("");return
        if self.task == "submitting" and token == self.generation:self.task = task
        self.controls()

    def read_text(self):
        def append(value):
            if not value.strip():
                self.status.setText("本页没有可提取的文字；请对照图片自行编写讲稿。");return
            existing = self.editor.toPlainText()
            text = existing + ("\n\n" if existing.strip() else "") + value
            if len(text) > MAX_PAGE_TEXT:
                self.status.setText("追加后讲稿过长，未替换当前输入；请分段整理。");return
            self.editor.setPlainText(text)
            self.status.setText("已追加本页文字。请核对顺序、化学式与图片说明，再朗读。")
        self._checked_action("读取本页讲稿文字", lambda source, n:source.text(n), append)

    def play(self):
        text = self.editor.toPlainText()
        if not text.strip() or len(text) > MAX_PAGE_TEXT or not self.speech:return
        def say(_):
            voice = self.voices.currentData()
            if voice is not None:self.speech.setVoice(voice)
            self.speech.setRate(self.rate.value())
            self.speech.say(text)
            self.status.setText(f"正在用本机声音朗读第{self.number}页；翻页、修改或关闭会停止。")
        self._checked_action("核对本页朗读版本", lambda *_:None, say)

    def pause(self):
        if not self.speech:return
        if self.speech.state().name == "Paused":self.speech.resume()
        else:self.speech.pause()
        self.controls()

    def commit(self):
        self._store()
        if not self.dirty:return True
        try:
            if any(len(s) > MAX_PAGE_TEXT for s in self.scripts) or sum(map(len, self.scripts)) > MAX_TOTAL_TEXT:
                raise ValueError("讲稿文字过长，请按课时拆分；当前输入保留。")
            self.save(deepcopy(self.scripts))
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc));return False
        self.original_scripts = deepcopy(self.scripts);self.dirty = False
        self.status.setText("讲稿已应用到当前备课；保存草稿后可重开，并随备课备份恢复。")
        self.controls();return True

    def discard(self):
        self.stop();self.scripts = deepcopy(self.original_scripts);self.dirty = False
        self.loading = True;self.editor.setPlainText(self.scripts[self.number-1]);self.loading = False
        self.status.setText("已撤销未应用的讲稿修改。");self.controls()

    def finish(self):
        if not self.commit():return False
        self.closed = True;self.stop()
        return True

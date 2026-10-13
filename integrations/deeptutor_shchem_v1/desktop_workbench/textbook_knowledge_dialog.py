"""Original-page candidate review, editable knowledge and explicit batch adoption."""
from __future__ import annotations
from copy import deepcopy
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QComboBox, QDialog, QFormLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .components import set_status
from .personal_textbook_panel import label

class TextbookKnowledgeDialog(QDialog):
    notes_saved = Signal(object)

    def __init__(self, facade, tasks, selection, view, section, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.selection, self.view, self.section = deepcopy(selection), deepcopy(view), deepcopy(section)
        self._epoch = 0; self._busy = self._closed = self._rendering = False
        self._task_id = self._draft = self._reader = None
        self._edits = {}; self._row_id = None; self._draft_rows = []; self._draft_stale = False
        self.setWindowTitle('整理教材知识 · 核对原页后选用')
        self.resize(1060, 760); self.setMinimumSize(0, 0)
        root = QVBoxLayout(self)
        root.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        root.addWidget(label(section['title'] + ' · ' + view['book']['source_name']))
        root.addWidget(label('先整理所选页，再核对、修订候选。确认的条目保存为知识笔记，可勾选加入当前备课。'))
        self.settings_toggle = QToolButton(); self.settings_toggle.setCheckable(True); self.settings_toggle.setChecked(True)
        self.settings_toggle.setAccessibleName('展开或收起本批知识整理页码与模型设置')
        root.addWidget(self.settings_toggle)
        self.settings = QWidget(); settings_box = QVBoxLayout(self.settings); settings_box.setContentsMargins(0, 0, 0, 0)
        self.settings_toggle.toggled.connect(self.settings.setVisible)
        self.form = form = QFormLayout()
        self.start, self.end = QSpinBox(), QSpinBox()
        for spin in (self.start, self.end): spin.setRange(section['pdf_start'], section['pdf_end'])
        self.start.setValue(section['pdf_start']); self.end.setValue(min(section['pdf_end'], section['pdf_start'] + 7))
        self.start.setAccessibleName('知识整理起始PDF页'); self.end.setAccessibleName('知识整理结束PDF页，每批最多8页')
        self.mode = QComboBox(); self.mode.addItem('本机文字摘录', 'pdf_native_text'); self.mode.addItem('AI原页归纳', 'model_visual')
        self.mode.setAccessibleName('知识整理方式，本机文字或AI原页归纳')
        self.profiles = QComboBox(); self.profiles.setAccessibleName('知识归纳使用的已配置视觉模型')
        try:
            for row in self.facade.preparation_profiles():
                if 'vision' in row.capabilities:
                    self.profiles.addItem(row.provider_name + ' / ' + row.model_id, {'profile_id': row.profile_id, 'profile_revision': row.revision})
        except Exception:
            pass
        self.profiles.setPlaceholderText('请先在模型设置中配置视觉模型')
        form.addRow('起始PDF页', self.start); form.addRow('结束PDF页', self.end)
        form.addRow('整理方式', self.mode); form.addRow('AI模型', self.profiles)
        settings_box.addLayout(form)
        self.send_confirmed = QCheckBox('我确认将上面所选原页发送给这个模型（可能产生费用）')
        settings_box.addWidget(self.send_confirmed)
        self.generate_button = QPushButton('整理本批知识'); self.generate_button.clicked.connect(self._generate)
        settings_box.addWidget(self.generate_button); root.addWidget(self.settings)
        self.settings_toggle.toggled.connect(self._controls)
        self.saved_batches = QComboBox(); self.saved_batches.setAccessibleName('已保存知识候选批次，关闭窗口后可继续核对')
        self.saved_batches.addItem('继续已有候选批次…', None)
        self.saved_batches.currentIndexChanged.connect(self._restore)
        root.addWidget(self.saved_batches)
        self.summary = label('每批1至8页；本机摘录读取可提取文字，AI归纳读取所选原页。印刷页码保持待核对。')
        root.addWidget(self.summary)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.items = QListWidget(); self.items.setMinimumSize(0, 80)
        self.items.setAccessibleName('本批知识候选，逐条核对并勾选保存')
        self.items.currentItemChanged.connect(self._row_changed); self.items.itemChanged.connect(self._controls)
        self.splitter.addWidget(self.items)
        self.tabs = QTabWidget(); self.tabs.setMinimumSize(0, 120)
        editor = QWidget(); box = QVBoxLayout(editor)
        self.title = QLineEdit(); self.title.setMaxLength(160); self.title.setAccessibleName('候选知识名称，可编辑')
        self.text = QPlainTextEdit(); self.text.setMinimumSize(0, 90); self.text.setMaximumHeight(155)
        self.text.setAccessibleName('候选知识正文，可编辑，最多1200字')
        self.location = label('选择候选后显示原页位置。')
        self.read_button = QPushButton('定位这条知识的原页'); self.read_button.clicked.connect(self._read)
        self.quote = QPlainTextEdit(); self.quote.setReadOnly(True); self.quote.setMinimumSize(0, 65); self.quote.setMaximumHeight(95)
        self.quote.setAccessibleName('候选原页依据，仍须核对，不能当作已核验原句')
        self.limitation = label('候选适用范围和未读取内容将在这里显示。')
        self.confirmed = QCheckBox('我已核对这条知识的全部原页、条件、符号与单位')
        for widget in (self.location, self.read_button, self.title, self.text, label('候选原页依据（待核对）'), self.quote, self.limitation, self.confirmed): box.addWidget(widget)
        box.addStretch(1)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(editor); scroll.setMinimumSize(0, 0)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.tabs.addTab(scroll, '核对与修订')
        self.reading = QWidget(); self.reading_box = QVBoxLayout(self.reading)
        self.read_prompt = label('点击“定位这条知识的原页”，在这里阅读完整原页。')
        self.reading_box.addWidget(self.read_prompt, 1); self.tabs.addTab(self.reading, '教材原页')
        self.splitter.addWidget(self.tabs); root.addWidget(self.splitter, 1)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.select_button = QPushButton('勾选已核对条目'); self.select_button.clicked.connect(self._select_confirmed)
        self.save_button = QPushButton('保存勾选知识笔记'); self.save_button.clicked.connect(self._adopt)
        self.close_button = QPushButton('保存进度，稍后继续'); self.close_button.clicked.connect(self.reject)
        for widget in (self.select_button, self.save_button, self.close_button): self.actions.addWidget(widget)
        root.addLayout(self.actions)
        self.status = label('候选批次可保存和重新打开；不会自动进入备课或获得正式审核资格。'); root.addWidget(self.status)
        self.title.textChanged.connect(self._edited); self.text.textChanged.connect(self._edited)
        self.confirmed.toggled.connect(self._confirmed)
        for signal in (self.start.valueChanged, self.end.valueChanged, self.mode.currentIndexChanged, self.profiles.currentIndexChanged): signal.connect(self._range_edited)
        self.send_confirmed.toggled.connect(self._controls)
        if hasattr(self.tasks, 'task_cancelled'): self.tasks.task_cancelled.connect(self._cancelled)
        if hasattr(self.tasks, 'task_finished'): self.tasks.task_finished.connect(self._finished)
        self._controls()
        self._run('读取教材知识候选批次', lambda: self.facade.personal_textbook_knowledge().drafts(self.selection, self.section['section_id']), self._batches_loaded)

    def _run(self, label_text, operation, ready, failure=None):
        if self._busy or self._closed: return
        epoch = self._epoch; self._busy = True; self._controls()
        def done(value):
            if not self._closed and epoch == self._epoch:
                self._busy = False; self._task_id = None; ready(value); self._controls()
        def failed(message):
            if not self._closed and epoch == self._epoch:
                self._busy = False; self._task_id = None
                if failure: failure()
                self._controls(); set_status(self.status, 'error', message)
        try: self._task_id = self.tasks.submit(label_text, operation, on_success=done, on_failure=failed)
        except RuntimeError:
            self._busy = False; self._controls(); set_status(self.status, 'error', '窗口已停止接收任务，请重新打开。')

    def _cancelled(self, task_id, *_):
        if self._closed or task_id != self._task_id: return
        self._epoch += 1; self._busy = False; self._task_id = None
        self._controls(); set_status(self.status, 'attention', '本次任务已取消；已有候选与已保存修订进度仍保留，可以继续核对。')

    def _finished(self, task_id):
        if not self._closed and task_id == self._task_id:
            self._busy = False; self._task_id = None; self._controls()

    def _batches_loaded(self, rows):
        self._draft_rows = deepcopy(rows); self.saved_batches.blockSignals(True)
        self.saved_batches.clear(); self.saved_batches.addItem('继续已有候选批次…', None)
        for row in rows: self.saved_batches.addItem(f"PDF {row['pdf_pages'][0]}—{row['pdf_pages'][-1]} · {len(row['items'])}条 · {'AI归纳' if row['method'] == 'model_visual' else '文字摘录'}"
            + (' · 章节已变化' if row['section_revision'] != self.section['revision'] else ''), row)
        self.saved_batches.blockSignals(False)

    def _restore(self, *_):
        row = self.saved_batches.currentData()
        if row and not self._busy:
            row = deepcopy(row)
            def ready():
                fresh = next((r for r in self._draft_rows if r['draft_id'] == row['draft_id']), row)
                self._show_draft(fresh)
                set_status(self.status, 'attention', '该批候选的章节已变化，仍可查看原页；请展开整理设置，重新生成本批。' if self._draft_stale
                    else '已打开保存的候选；修订内容保留，逐条重新核对后再保存为知识笔记。')
            self._preserve_then(ready)

    def _review_edits(self):
        return [{key: value[key] for key in ('candidate_id', 'title', 'text')} for value in self._edits.values()]

    def _preserve_then(self, ready):
        if not self._draft or self._draft_stale:
            ready(); return
        edits = self._review_edits()
        baseline = self._draft['review_edits'] or [{key: row[key] for key in ('candidate_id', 'title', 'text')} for row in self._draft['items']]
        if edits == baseline:
            ready(); return
        draft = deepcopy(self._draft)
        def saved(value):
            self._draft = value
            self._batches_loaded([r for r in self._draft_rows if r['draft_id'] != value['draft_id']] + [value])
            ready()
        def failed():
            self.saved_batches.blockSignals(True)
            for index in range(1, self.saved_batches.count()):
                row = self.saved_batches.itemData(index)
                if row['draft_id'] == self._draft['draft_id']:
                    self.saved_batches.setCurrentIndex(index); break
            self.saved_batches.blockSignals(False)
        self._run('保存教材知识核对进度', lambda: self.facade.personal_textbook_knowledge().save_progress(self.selection,
            self.view['study_revision'], draft['draft_id'], draft['revision'], edits), saved, failed)

    def _range_edited(self, *_):
        self.send_confirmed.setChecked(False); self._controls()

    def _generate(self):
        if not self.generate_button.isEnabled(): return
        selection, revision, section_id = deepcopy(self.selection), self.view['study_revision'], self.section['section_id']
        start, end, mode = self.start.value(), self.end.value(), self.mode.currentData()
        binding = deepcopy(self.profiles.currentData())
        confirmed = self.send_confirmed.isChecked()
        def generate():
            service = self.facade.personal_textbook_knowledge()
            if mode == 'model_visual':
                return self.facade.personal_textbook_knowledge_generate(selection, revision, section_id, start, end,
                    **binding, send_confirmed=confirmed)
            return service.preview_native(selection, revision, section_id, start, end)
        def ready(draft):
            # Durable candidate save is separate from adopting teacher notes.
            self._run('保存教材知识候选批次', lambda: self.facade.personal_textbook_knowledge().save_draft(selection, revision, draft), self._candidate_saved)
        self._preserve_then(lambda: self._run('整理教材知识候选', generate, ready))

    def _candidate_saved(self, draft):
        rows = [r for r in self._draft_rows if r['draft_id'] != draft['draft_id']] + [draft]
        self._batches_loaded(rows); self._show_draft(draft)
        set_status(self.status, 'success', '候选批次已保存；选择条目核对原页、修订内容，再勾选知识笔记。')

    def _show_draft(self, draft):
        self._clear_reader(); self._draft = deepcopy(draft); self._edits.clear(); self._row_id = None
        self._draft_stale = draft['section_revision'] != self.section['revision']
        self._rendering = True; self.items.clear()
        saved = {r['candidate_id']: r for r in draft['review_edits']}
        for row in draft['items']:
            edit = saved.get(row['candidate_id'], row)
            self._edits[row['candidate_id']] = {'candidate_id': row['candidate_id'], 'title': edit['title'], 'text': edit['text'], 'confirmed': False}
            item = QListWidgetItem((edit['title'] or '待填写名称') + ' · PDF ' + '、'.join(map(str, row['source_evidence']['pdf_pages'])))
            item.setData(Qt.ItemDataRole.UserRole, deepcopy(row)); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked); self.items.addItem(item)
        self._rendering = False
        self.summary.setText(f"本批{len(draft['items'])}条候选，PDF {draft['pdf_pages'][0]}—{draft['pdf_pages'][-1]}页。" + '\n'.join(draft['warnings']))
        self.settings_toggle.setChecked(False)
        self.items.setCurrentRow(0); self.tabs.setCurrentIndex(0); self._controls()

    def _row_changed(self, item, *_):
        if self._rendering: return
        self._rendering = True
        row = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._row_id = row['candidate_id'] if row else None
        edit = self._edits.get(self._row_id, {})
        self.title.setText(edit.get('title', '')); self.text.setPlainText(edit.get('text', ''))
        self.confirmed.setChecked(edit.get('confirmed', False))
        if row:
            evidence = row['source_evidence']
            self.location.setText('原页位置：PDF ' + '、'.join(map(str, evidence['pdf_pages'])) + '；印刷页码待核对。')
            self.quote.setPlainText(evidence['source_quote']); self.limitation.setText(evidence['limitation'])
        self._rendering = False; self._controls()

    def _edited(self, *_):
        if self._rendering or self._row_id is None: return
        edit = self._edits[self._row_id]
        edit.update(title=self.title.text(), text=self.text.toPlainText(), confirmed=False)
        self.confirmed.setChecked(False)
        if self.items.currentItem():
            self.items.currentItem().setText((edit['title'] or '待填写名称') + ' · PDF ' + '、'.join(map(str, self.items.currentItem().data(Qt.ItemDataRole.UserRole)['source_evidence']['pdf_pages'])))
            self.items.currentItem().setCheckState(Qt.CheckState.Unchecked)
        self._controls()

    def _confirmed(self, checked):
        if self._rendering or self._row_id is None: return
        self._edits[self._row_id]['confirmed'] = checked; self._controls()

    def _select_confirmed(self):
        for n in range(self.items.count()):
            item = self.items.item(n); row = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(Qt.CheckState.Checked if self._edits[row['candidate_id']]['confirmed'] else Qt.CheckState.Unchecked)

    def _chosen(self):
        return [deepcopy(self._edits[item.data(Qt.ItemDataRole.UserRole)['candidate_id']]) for n in range(self.items.count())
                for item in [self.items.item(n)] if item.checkState() == Qt.CheckState.Checked]

    def _adopt(self):
        if not self.save_button.isEnabled(): return
        chosen, draft = self._chosen(), deepcopy(self._draft)
        self._run('保存勾选教材知识笔记', lambda: self.facade.personal_textbook_knowledge().adopt(self.selection,
            self.view['study_revision'], draft['draft_id'], draft['revision'], chosen), self._adopted)

    def _adopted(self, value):
        self.notes_saved.emit(value); self.accept()

    def _read(self):
        if self._row_id is None or self._busy: return
        from .textbook_source_dialog import TextbookSourceDialog
        row = next(r for r in self._draft['items'] if r['candidate_id'] == self._row_id)
        self._clear_reader()
        selection = {**self.selection, 'initial_pdf_page': row['source_evidence']['pdf_pages'][0]}
        if not self._draft_stale:
            selection.update(section_id=self.section['section_id'], study_revision=self.view['study_revision'])
        self._reader = TextbookSourceDialog(self.facade, selection, self.reading, tasks=self.tasks, reading_mode='book', embedded=True)
        self._reader.zoom.setCurrentIndex(1)  # Whole-page overview; the reader retains explicit zoom controls.
        self.read_prompt.hide(); self.reading_box.addWidget(self._reader, 1); self._reader.show(); self.tabs.setCurrentIndex(1)

    def _clear_reader(self):
        if self._reader is not None:
            old, self._reader = self._reader, None; self.reading_box.removeWidget(old); old.reject(); old.deleteLater()
        self.read_prompt.show()

    def _controls(self, *_):
        usable = not self._busy and not self._closed
        model = self.mode.currentData() == 'model_visual'
        self.form.setRowVisible(self.profiles, model); self.send_confirmed.setVisible(model)
        self.settings_toggle.setText(('收起整理设置' if self.settings_toggle.isChecked() else '调整页码与整理方式')
            + f" · PDF {self.start.value()}—{self.end.value()} · {'AI原页归纳' if model else '本机文字摘录'}")
        self.generate_button.setEnabled(usable and self.start.value() <= self.end.value()
            and self.end.value() - self.start.value() < 8
            and (not model or bool(self.profiles.currentData()) and self.send_confirmed.isChecked()))
        chosen = self._chosen()
        reviewable = usable and not self._draft_stale
        self.save_button.setEnabled(reviewable and bool(chosen) and all(r['confirmed'] and bool(r['title'].strip()) and 0 < len(r['text'].strip()) <= 1200 for r in chosen))
        self.select_button.setEnabled(reviewable and bool(self._draft))
        self.confirmed.setEnabled(reviewable and self._row_id is not None and bool(self.title.text().strip()) and 0 < len(self.text.toPlainText().strip()) <= 1200)
        self.read_button.setEnabled(usable and self._row_id is not None)
        for widget in (self.settings_toggle, self.start, self.end, self.mode, self.profiles, self.saved_batches, self.items): widget.setEnabled(usable)
        for widget in (self.title, self.text): widget.setEnabled(reviewable)

    def resizeEvent(self, event):
        narrow = event.size().width() < 720
        self.splitter.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
        self.splitter.setSizes([90, 240] if narrow else [270, 650])
        self.actions.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)

    def reject(self):
        # Closing while a task runs cancels it. An idle review retains edited
        # content but never restores checks or lesson selection next time.
        if self._closed or self._busy or not self._draft or self._draft_stale:
            super().reject(); return
        self._preserve_then(lambda: super(TextbookKnowledgeDialog, self).reject())

    def done(self, result):
        if not self._closed:
            self._closed = True; self._epoch += 1
            if self._task_id: self.tasks.cancel(self._task_id)
            self._clear_reader()
        super().done(result)

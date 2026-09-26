"""Teacher-confirmed bridge: exam evidence -> local questions -> actual retest."""
from __future__ import annotations
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QPlainTextEdit, QDialog, QListWidget, QListWidgetItem, QLineEdit,
    QDialogButtonBox, QDateEdit, QDoubleSpinBox, QFormLayout, QInputDialog)
from ..desktop_exam_data import ExamError, digest
from ..desktop_exam_followup import (create_followup, practice_request,
    record_attempt, task_summary, followup_text)
from ..desktop_question_explorer import personal_options
from ..desktop_exam_practice import (freeze_selection, selection_items, paper_session,
    request_revision, revise_selection, append_export)


def checked_rows(title, rows, parent):
    dialog = QDialog(parent); dialog.setWindowTitle(title); dialog.resize(650,480)
    box = QVBoxLayout(dialog); view = QListWidget(); box.addWidget(view,1)
    for key, text in rows:
        item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole,key)
        item.setCheckState(Qt.CheckState.Unchecked); view.addItem(item)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); box.addWidget(buttons)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    keys = [view.item(i).data(Qt.ItemDataRole.UserRole) for i in range(view.count())
            if view.item(i).checkState() == Qt.CheckState.Checked] if accepted else None
    dialog.deleteLater(); return keys


class _ExamFollowupPanelBase(QWidget):
    def __init__(self, dashboard):
        super().__init__(dashboard); self.d = dashboard; self.preview = None; self.approved = False
        self.preview_task_id = None; self.preview_revision = None
        box = QVBoxLayout(self); box.setContentsMargins(8,8,8,8)
        self.hint = QLabel('先选考试题目，确认学生与目标，再选题、排卷并记录实际复测。')
        self.hint.setWordWrap(True); box.addWidget(self.hint)
        self.question = QComboBox(); self.question.setAccessibleName('复练对应的考试题目'); box.addWidget(self.question)
        self.tasks = QComboBox(); self.tasks.setAccessibleName('已保存的复练任务'); box.addWidget(self.tasks)
        self.actions = QGridLayout(); box.addLayout(self.actions); self._action_columns = 3
        self.new = QPushButton('建立复练任务'); self.find = QPushButton('去题库按标签选题')
        self.link = QPushButton('保存本任务题集'); self.preview_button = QPushButton('预览本任务练习卷')
        self.export = QPushButton('导出已核对练习卷'); self.record = QPushButton('记录实际复测')
        self.edit_button = QPushButton('整理题集与目标')
        self._buttons = (self.new, self.find, self.link, self.edit_button, self.preview_button, self.export)
        for i, button in enumerate(self._buttons):
            button.setAutoDefault(False); self.actions.addWidget(button, i // 3, i % 3)
        # Record actions share the compact status row instead of adding a third grid row.
        from PySide6.QtWidgets import QHBoxLayout
        state_row = QHBoxLayout()
        self.selection_status = QLabel(); self.selection_status.setWordWrap(True)
        self.selection_status.setAccessibleName('本任务题集状态')
        state_row.addWidget(self.selection_status, 1); state_row.addWidget(self.record)
        box.addLayout(state_row)
        self.record.setAutoDefault(False)
        for button in (self.find, self.link, self.edit_button, self.preview_button, self.export, self.record):
            button.setObjectName('QuietButton')
        self.text = QPlainTextEdit(); self.text.setReadOnly(True); self.text.setMinimumHeight(160); box.addWidget(self.text,1)
        self.new.clicked.connect(self.new_task); self.find.clicked.connect(self.find_questions)
        self.link.clicked.connect(self.link_questions); self.preview_button.clicked.connect(self.preview_paper)
        self.export.clicked.connect(self.export_paper); self.record.clicked.connect(self.record_result)
        self.edit_button.clicked.connect(self.edit_selection)
        self.tasks.currentIndexChanged.connect(self.show_task)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns = 3 if self.width() >= 640 else 2 if self.width() >= 400 else 1
        if columns == self._action_columns:return
        self._action_columns = columns
        for button in self._buttons:self.actions.removeWidget(button)
        for index, button in enumerate(self._buttons):self.actions.addWidget(button, index // columns, index % columns)

    def current(self):
        identity = self.tasks.currentData()
        return next((t for t in self.d.followups if t['id']==identity),None)

    def current_for(self, expected):
        """Check UI identity, content revision and the on-disk exam revision."""
        current = self.current()
        if (getattr(self.d, '_closed', False) or not self.d.exam or not current
                or self.d.exam['id'] != expected['exam_id']
                or request_revision(current) != request_revision(expected)):
            raise ExamError('考试、任务题集或目标已变化，请重新预览；未改动当前记录。')
        saved = self.d._saved_revision
        if saved is not None and digest(self.d.store.load(self.d.exam['id'])) != saved:
            raise ExamError('另一个窗口已修改考试分析，请重新打开后核对；未覆盖已有记录。')
        return deepcopy(current)

    def refresh(self, selected=None):
        selected = selected if selected is not None else self.tasks.currentData()
        self.question.clear()
        if self.d.report:
            for question in self.d.report['items']:
                self.question.addItem(f"第{question['question']}题 · {question['knowledge'] or '知识点待核对'}",question['question'])
        self.tasks.blockSignals(True); self.tasks.clear()
        for task in self.d.followups:self.tasks.addItem(task['goal'][:70],task['id'])
        index = self.tasks.findData(selected)
        if index >= 0:self.tasks.setCurrentIndex(index)
        self.tasks.blockSignals(False); self.show_task()

    def show_task(self):
        task=self.current()
        if task:
            index=self.question.findData(task['question'])
            if index>=0:self.question.setCurrentIndex(index)
        for widget in (self.find,self.link,self.preview_button,self.record):widget.setEnabled(task is not None)
        valid_preview = bool(task and self.preview and task['id']==self.preview_task_id
                             and self.preview_revision == request_revision(task))
        self.export.setEnabled(bool(valid_preview and self.approved))
        self.edit_button.setEnabled(False)
        if task and 'practice_set' in task:
            try:
                count = len(selection_items(task))
                self.selection_status.setText(f'独立题集 · {count}项完整题目 · 原题文件须保留。移出题篮不影响本任务。')
                self.edit_button.setEnabled(True)
            except ExamError as error:
                self.selection_status.setText(error.message_zh)
                self.preview_button.setEnabled(False); self.export.setEnabled(False)
        else:
            self.selection_status.setText('旧题篮关联：重新勾选保存，可转为独立题集。'
                if task and task.get('links') else '尚未保存题集：先选题，再勾选本任务的完整题目。')
            if not task or not task.get('links'):self.preview_button.setEnabled(False)
        self.new.setEnabled(self.question.count()>0)
        self.text.setPlainText(followup_text([task]) if task else '尚无复练任务。缺考、缺失分数不自动认定为知识错误；仅有总分时需先补逐题映射。')

    def store(self, task, expected=None):
        """Preserve task order and restore both data and dirty state on failure."""
        if not self.d.exam or task.get('exam_id') != self.d.exam['id']:
            self.d.status.setText('任务不属于当前考试，未保存。'); return False
        before = deepcopy(self.d.followups); dirty = self.d.dirty
        indexes = [i for i, row in enumerate(before) if row['id'] == task['id']]
        if len(indexes) > 1 or (expected is not None and
                (not indexes or digest(before[indexes[0]]) != digest(expected))):
            self.d.status.setText('任务记录已变化，请重新读取；未覆盖已有修改。'); return False
        changed = deepcopy(before)
        if indexes:changed[indexes[0]] = deepcopy(task)
        else:changed.append(deepcopy(task))
        self.d.followups = changed; self.d.dirty = True
        try:
            success = self.d.save_current()
        except Exception:
            self.d.followups = before; self.d.dirty = dirty
            self.d.status.setText('任务未能保存，已有记录未修改。'); return False
        if not success:
            self.d.followups = before; self.d.dirty = dirty; return False
        self.refresh(task['id']); return True

    def new_task(self):
        number=self.question.currentData()
        if not self.d.exam or number is None:return
        eligible=[s for s in self.d.report['students'] if not s['absent'] and s['scores'].get(number) is not None]
        keys=checked_rows('选择本次复练学生（显示的是本题实得分）',[(s['id'],f"{s['local_label']} · {s['scores'][number]}") for s in eligible],self)
        if not keys:return
        goal,ok=QInputDialog.getText(self,'复练目标','写出一个可以检查的任务，例如：依据数据写出平衡常数表达式')
        if not ok:return
        due,ok=QInputDialog.getText(self,'计划日期','年-月-日',text=(date.today()+timedelta(days=7)).isoformat())
        if not ok:return
        try:self.store(create_followup(self.d.exam,number,keys,goal,due))
        except ExamError as error:self.d.status.setText(error.message_zh)

    def find_questions(self):
        task=deepcopy(self.current())
        if not task:return
        choices=['本地 Word 题库','个人图片题库']
        choice,ok=QInputDialog.getItem(self,'选择真实题库来源','当前标签来自该来源的有效目录',choices,0,False)
        if not ok:return
        lane='word_native' if choice==choices[0] else 'visual_native'
        def ready(catalog):
            current = self.current_for(task)
            options=personal_options(catalog,lane).get('knowledge',{}).get('values',[])
            options=[option for option in options if option['value']!='unknown']
            if not options:
                self.d.status.setText('此来源没有可筛选的知识点标签，请先导入并核对标签；未生成虚构命中。');return
            labels=[option['label_zh']+' ['+str(option['value'])+']' for option in options]
            suggested=[i for i,option in enumerate(options) if task['knowledge'] in (option['value'],option['label_zh'])]
            chosen,ok=QInputDialog.getItem(self,'核对题库知识点','考试标签与题库标签不自动等同，请选择确切对应',labels,suggested[0] if len(suggested)==1 else 0,False)
            if not ok:return
            option=options[labels.index(chosen)]
            revised=deepcopy(current);revised['library_filter']={'lane':lane,'knowledge':option['value'],'label':option['label_zh']}
            if not self.store(revised, expected=current):return
            self.d.pending_handoff={'exam_id':task['exam_id'],'task_id':task['id'],**revised['library_filter']}
            from PySide6.QtCore import QTimer
            def leave():
                if self.d._task:QTimer.singleShot(30,leave)
                else:self.d.close()
            QTimer.singleShot(0,leave)
        loader=self.d.facade.word_question_catalog if lane=='word_native' else self.d.facade.personal_visual_questions
        self.d.run('读取现有题库标签',lambda report,cancelled:loader(),ready)

    def link_questions(self):
        task=deepcopy(self.current())
        if not task:return
        try:
            basket=self.d.facade.basket()
            keys=checked_rows('保存所勾选题目为本任务题集（替换当前选题；整主题保留）',[(row['key'],row.get('title_zh','未命名题')+' · '+row.get('source_zh','')) for row in basket],self)
            if keys:
                current = self.current_for(task)
                if self.store(freeze_selection(current,basket,keys), expected=current):
                    self.preview=None;self.approved=False;self.show_task()
        except Exception as error:self.d.status.setText(getattr(error,'message_zh','题篮未能读取；未改动已有任务。'))

    def edit_selection(self):
        task = deepcopy(self.current())
        if not task:return
        from .exam_practice_editor import PracticeSetDialog
        def save(candidate):
            current = self.current_for(task)
            revised = revise_selection(current, [row['key'] for row in selection_items(candidate)], candidate['goal'])
            if not self.store(revised, expected=current):return False
            if request_revision(revised) != request_revision(task):
                self.preview=None; self.approved=False
            self.show_task()
            self.d.status.setText('题集与目标已保存；内容有变化时须重新预览。原题、题篮和历史复测保留。')
            return True
        try:
            self.current_for(task)
            dialog = PracticeSetDialog(task, save, self)
            self._selection_dialog = dialog
            dialog.open()
        except ExamError as error:self.d.status.setText(error.message_zh)

    def preview_paper(self):
        task=deepcopy(self.current())
        if not task:return
        try:self.current_for(task,require_fresh=True)
        except ExamError as error:self.d.status.setText(error.message_zh);return
        self.preview=None;self.approved=False;self.preview_task_id=task['id']
        self.preview_revision=request_revision(task);self.show_task()
        def prepare(report,cancelled):
            facade=paper_session(self.d.facade, task)
            request=practice_request(task,facade.basket(),facade.paper_basket_projection())
            if cancelled():raise ExamError('本次预览已取消。')
            content=facade.create_paper_preview(request)
            return facade.prepare_mixed_paper_pagination(content.preview_id,content.preview_hash)
        def ready(value):
            from .assembly_page import MixedPaperPaginationDialog
            self.current_for(task,require_fresh=True)
            session = paper_session(self.d.facade, task); self.preview=value
            dialog=MixedPaperPaginationDialog(value.preview_model,self.d.tasks,
                lambda key:session.paper_preview_image(value.preview_id,key),self.d)
            self._preview_dialog=dialog
            def approve():
                try:
                    self.current_for(task,require_fresh=True)
                    if self.preview is not value:raise ExamError('预览已更新，请核对当前版本。')
                except ExamError as error:self.d.status.setText(error.message_zh);return
                def done(result):
                    self.current_for(task,require_fresh=True)
                    if self.preview is not value:raise ExamError('旧预览的确认结果已忽略，请核对当前版本。')
                    if result.get('status')=='approved':
                        self.approved=True;dialog.mark_confirmed();self.show_task()
                self.d.run('确认本任务两版分页',lambda report,cancelled:session.approve_paper_preview(value.preview_id,value.preview_hash),done)
            dialog.preview_confirmed.connect(approve);dialog.show()
        self.d.run('生成本任务学生与教师版真实分页',prepare,ready)

    def export_paper(self):
        task=deepcopy(self.current());value=self.preview
        if not task or not value or not self.approved or task['id']!=self.preview_task_id:return
        if self.preview_revision != request_revision(task):
            self.approved=False;self.show_task()
            self.d.status.setText('任务题集或目标已变化，请重新预览并确认；旧输出仍保留。');return
        try:self.current_for(task,require_fresh=True)
        except ExamError as error:self.d.status.setText(error.message_zh);return
        def done(result):
            current = self.current_for(task,require_fresh=True)
            if self.preview is not value:raise ExamError('预览已更新，未登记旧输出；已生成文件仍保留。')
            changed = append_export(current, task, value, result)
            if self.store(changed, expected=current):
                self.d.status.setText('本任务练习卷已导出并登记；原题篮、组卷草稿和复测记录未改动。')
                self.text.appendPlainText('\n导出文件：\n'+'\n'.join(row['path'] for row in result['artifacts']))
            else:self.d.status.setText('文件已生成，但任务记录未保存；请核对保存冲突或权限后重试，文件不删除。')
        self.d.run('导出本任务练习卷',lambda report,cancelled:paper_session(self.d.facade, task).export_paper_preview(value.preview_id,value.preview_hash),done)

    def record_result(self):
        task=deepcopy(self.current())
        if not task:return
        dialog=QDialog(self);dialog.setWindowTitle('记录实际复测（不是预测提分）');dialog.resize(510,380)
        box=QVBoxLayout(dialog);form=QFormLayout();box.addLayout(form)
        student=QComboBox()
        for target in task['targets']:student.addItem(target['local_label'],target['exam_student_id'])
        when=QDateEdit();when.setCalendarPopup(True);when.setDisplayFormat('yyyy-MM-dd');when.setDate(QDate.currentDate())
        status=QComboBox()
        for text,value in [('已作答并评分','completed'),('未作答','not_attempted'),('资料不全','missing')]:status.addItem(text,value)
        score=QDoubleSpinBox();maximum=QDoubleSpinBox()
        for widget in (score,maximum):widget.setRange(0,10000);widget.setDecimals(2)
        score.lineEdit().clear();maximum.lineEdit().clear()
        note=QLineEdit();note.setPlaceholderText('依据哪次作答/哪些题，哪些地方仍需核对')
        for label,widget in [('学生',student),('实际日期',when),('情况',status),('本次实得分',score),('实际满分',maximum),('观察依据',note)]:form.addRow(label,widget)
        msg=QLabel('未作答/资料不全不记零分；本次题目与原考试不同，不直接计算“提升多少分”。');msg.setWordWrap(True);box.addWidget(msg)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject);box.addWidget(buttons)
        def save():
            try:
                if status.currentData()=='completed' and (not score.text().strip() or not maximum.text().strip()):
                    raise ExamError('请明确填写本次实得分和实际满分，空白不按0分保存。')
                current = self.current_for(task)
                record=record_attempt(current,student.currentData(),when.date().toString('yyyy-MM-dd'),status.currentData(),score.value(),maximum.value(),note.text())
                if self.store(record, expected=current):dialog.accept()
            except ExamError as error:msg.setText(error.message_zh)
        buttons.accepted.connect(save);dialog.exec();dialog.deleteLater()


from .exam_revision_ui import FollowupRevisionMixin


class ExamFollowupPanel(FollowupRevisionMixin, _ExamFollowupPanelBase):
    """Existing task editor with an explicit score-evidence review gate."""

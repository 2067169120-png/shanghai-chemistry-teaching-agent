"""Explicit score-edit and evidence-review extensions of the existing exam UI.

The exported dashboard/panel inherit these mixins; no instance monkey-patching,
parallel store, global basket replacement or automatic model calls are used.
"""
from __future__ import annotations
from copy import deepcopy
from html import escape
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QFormLayout, QComboBox, QLineEdit, QPlainTextEdit, QCheckBox, QPushButton,
    QDialogButtonBox, QFileDialog, QMessageBox, QInputDialog)
from ..desktop_exam_data import ExamError, digest
from ..desktop_exam_ai import advice_text
from ..desktop_exam_report import brief, html_report
from ..desktop_exam_revision import (correct_score, full_mapping, score_revision,
    advice_revision, task_freshness, task_review_text, review_task, now)


class ScoreCorrectionDialog(QDialog):
    def __init__(self, dashboard):
        super().__init__(dashboard); self.d = dashboard
        self.expected = digest(dashboard.bundle())
        self.exam = deepcopy(dashboard.exam)
        self.setWindowTitle('修改本机分析成绩 · 不改原Excel')
        self.resize(600, 500); self.setMinimumWidth(400)
        box = QVBoxLayout(self); box.setContentsMargins(16, 14, 16, 14)
        title = QLabel('核对后修改一项成绩'); title.setObjectName('CardTitle'); box.addWidget(title)
        notice = QLabel('只修改本机分析副本并记录原因。统计重新计算，旧讲评与受影响复练需重核；原Excel、旧成品和实际复测不改写。')
        notice.setWordWrap(True); box.addWidget(notice)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows); box.addLayout(form)
        self.student = QComboBox(); self.student.setAccessibleName('修改成绩的学生')
        for row in dashboard.report['students']:
            self.student.addItem(f"{row['class']} · {row['local_label']} ({row['id']})", row['id'])
        index = dashboard.student_table.currentIndex()
        if index.isValid():self.student.setCurrentIndex(index.row())
        self.field = QComboBox(); self.field.setAccessibleName('修改成绩的题目')
        for question in self.exam['questions']:
            self.field.addItem(f"第{question['question']}题 / 满分{question['max_score']:g}", question['question'])
        if not full_mapping(self.exam):self.field.addItem('独立总分（题目映射不完整）', '__total__')
        self.previous = QLabel(); self.previous.setWordWrap(True)
        self.value = QLineEdit(); self.value.setPlaceholderText('填写明确数字；不把空白当作0')
        self.value.setAccessibleName('修正后的得分')
        self.missing = QCheckBox('明确标记为缺失／未评分（不记0分）')
        self.reason = QPlainTextEdit(); self.reason.setMaximumHeight(85)
        self.reason.setPlaceholderText('原作答、评分依据或录入更正原因；最多2000字')
        self.reason.setAccessibleName('本次改分依据')
        for label, widget in [('学生',self.student),('得分项目',self.field),('原记录',self.previous),
                              ('新得分',self.value),('',self.missing),('修改依据',self.reason)]:
            form.addRow(label,widget)
        self.message = QLabel(); self.message.setWordWrap(True); box.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText('确认改分并保存')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.save); self.buttons.rejected.connect(self.reject); box.addWidget(self.buttons)
        self.student.currentIndexChanged.connect(self.refresh); self.field.currentIndexChanged.connect(self.refresh)
        self.missing.toggled.connect(lambda missing:self.value.setEnabled(not missing))
        self.refresh()

    def refresh(self):
        row = next((s for s in self.exam['students'] if s['id']==self.student.currentData()), None)
        if row is None:return
        field = self.field.currentData()
        old = row['total'] if field=='__total__' else row['scores'].get(field)
        self.previous.setText(f"原得分：{old if old is not None else '缺失'}；原总分：{row['total'] if row['total'] is not None else '待核对'}")
        self.value.setText('' if old is None else str(old)); self.missing.setChecked(old is None)
        self.value.setEnabled(old is not None); self.save_button.setEnabled(not row['absent'])
        self.message.setText('缺考状态不能在此直接改成成绩，请核对后重新导入。' if row['absent'] else
            '完整映射：总分由全部小题重新合计，仍有缺失则总分保持缺失。' if full_mapping(self.exam) else
            '部分映射：改小题会将旧总分标为待核对；请另行确认独立总分，不按差值猜算。')

    def save(self):
        try:
            text = self.value.text().strip()
            if not self.missing.isChecked() and not text:
                raise ExamError('请填写明确得分，或勾选缺失；空白不按0分保存。')
            try:value = None if self.missing.isChecked() else float(text)
            except ValueError:raise ExamError('新得分不是有效数字。') from None
            self.d.apply_score_change(self.student.currentData(), self.field.currentData(), value,
                                      self.reason.toPlainText(), expected=self.expected)
            self.accept()
        except (ExamError, OSError) as error:
            self.message.setText(getattr(error,'message_zh','记录未保存，输入仍保留，请检查文件权限。'))


class ExamRevisionMixin:
    def __init__(self, *args, **kwargs):
        self.advice_history = []
        super().__init__(*args, **kwargs)
        self.score_tools = QWidget(); bar = QHBoxLayout(self.score_tools); bar.setContentsMargins(0,0,0,0)
        self.correct_button = QPushButton('核对并修改成绩')
        self.recalculate_button = QPushButton('重算本地统计')
        self.revision_history_button = QPushButton('改分与旧建议记录')
        for button in (self.correct_button,self.recalculate_button,self.revision_history_button):
            button.setAutoDefault(False); button.setObjectName('QuietButton'); bar.addWidget(button)
        self.score_tools.setEnabled(bool(self.exam) and not bool(self._task))
        self.student_table.parentWidget().layout().insertWidget(1,self.score_tools)
        self.correct_button.clicked.connect(self.open_score_correction)
        self.recalculate_button.clicked.connect(self.recalculate)
        self.revision_history_button.clicked.connect(self.show_revision_history)
        self.render()

    def _busy(self, yes):
        super()._busy(yes)
        if hasattr(self,'score_tools'):self.score_tools.setEnabled(bool(self.exam) and not yes)

    def advice_input_stamp(self):
        return advice_revision(self.exam,self.paper,self.notes.toPlainText(),self.classes.currentData())

    def check_disk_current(self):
        if self._saved_revision is not None and digest(self.store.load(self.exam['id']))!=self._saved_revision:
            raise ExamError('另一个窗口已保存考试分析，请重新打开核对；未覆盖最新记录。')

    def current_result(self):
        result = self.result
        if (not self.exam or not isinstance(result,dict) or self.result_scope!=self.classes.currentData()
                or result.get('_local_evidence')!=self.advice_input_stamp()):
            return None
        return result

    def advice_display(self):
        current = self.current_result()
        if current:return advice_text(current)
        if self.result or self.advice_history:
            return ('旧AI建议已过期、范围不同或缺少版本依据，不能作为当前讲评使用。\n'
                    '本地统计仍可重算；旧建议可在“学生得分与行动 → 改分与旧建议记录”中回看。\n'
                    '重新生成必须再次确认发送资料，可能产生API费用；不会自动调用。')
        return advice_text(None)

    def freshness_text(self):
        if not self.exam:return '尚无考试数据。'
        allowed = {row['id'] for row in self.report['students']} if self.report else set()
        relevant = [task for task in self.followups if any(
            target.get('exam_student_id') in allowed for target in task.get('targets', []))]
        stale = sum(task_freshness(self.exam,task)['state']!='current' for task in relevant)
        advice = 'AI建议依据一致，仍待教师核对' if self.current_result() else (
            '旧AI建议不可作为当前建议' if self.result or self.advice_history else '尚无当前AI建议')
        return f'当前本地统计已重算；{advice}；{stale}项复练依据待核对。'

    def render(self):
        super().render()
        if not self.exam:return
        message = self.freshness_text()
        self.ai_result.setPlainText(self.advice_display())
        self.overview_note.setText(self.overview_note.text()+'\n'+message)
        self.problem_note.setText('\n'.join(self.report['warnings'])+'\n'+message)
        self.summary_label.setToolTip(message)
        if hasattr(self,'score_tools'):self.score_tools.setEnabled(not bool(self._task))

    def recalculate(self):
        if not self.exam or self._task:return
        self.render(); self.status.setText(self.freshness_text()+' 重算不等于重新确认AI或复练。')

    def bundle(self):
        bundle = super().bundle()
        bundle['advice_history'] = deepcopy(self.advice_history)
        return bundle

    def accept_exam(self, exam, *, persist=True):
        self.advice_history = []
        return super().accept_exam(exam,persist=persist)

    def open_saved(self, identity, task_id=None):
        bundle = self.store.load(identity)
        history = bundle.get('advice_history',[])
        if not isinstance(history,list) or any(not isinstance(item,dict) or 'result' not in item for item in history):
            raise ExamError('旧建议历史格式异常，请保留文件，不覆盖该记录。')
        self.accept_exam(bundle['exam'],persist=False)
        self.paper = bundle.get('paper',{'text':'','pages':[],'warnings':[]})
        self._rendering=True
        self.paper_text.setPlainText(self.paper.get('text','')); self.notes.setPlainText(bundle.get('notes',''))
        self._rendering=False
        self.result=bundle.get('advice'); self.result_scope=bundle.get('advice_scope')
        self.advice_history=deepcopy(history); self.followups=bundle.get('followups',[])
        self._saved_revision=digest(bundle); self.dirty=False
        self.classes.blockSignals(True); index=self.classes.findData(self.result_scope)
        if index>=0:self.classes.setCurrentIndex(index)
        self.classes.blockSignals(False)
        self.paper_label.setText(self.paper.get('name','未附试卷'))
        self.render(); self.followup_panel.refresh(task_id)
        if task_id:self.tabs.setCurrentWidget(self.followup_panel)
        self.status.setText(self.freshness_text())

    def open_history(self):
        if not self.flush_or_discard():return
        entries=self.store.entries()
        if not entries:self.status.setText('尚无已保存的考试分析。');return
        choices=[f'{i+1}. {title}' for i,(_,title) in enumerate(entries)]
        value,ok=QInputDialog.getItem(self,'打开历史考试分析','已保存记录',choices,0,False)
        if not ok:return
        try:self.open_saved(entries[choices.index(value)][0])
        except ExamError as error:self.status.setText(error.message_zh)

    def archive_advice(self, reason):
        if not self.result:return
        item={'at':now(),'reason':reason,'scope':self.result_scope,'result':deepcopy(self.result)}
        if not self.advice_history or self.advice_history[-1].get('result')!=self.result:
            self.advice_history.append(item)

    def input_changed(self):
        if self._rendering:return
        self.archive_advice('试卷文字或教师说明发生变化')
        super().input_changed(); self.render()

    def accept_paper(self,paper):
        self.archive_advice('试卷来源发生变化')
        super().accept_paper(paper); self.render()

    def clear_paper(self):
        self.archive_advice('清除试卷')
        super().clear_paper()
        self.result=None; self.render()

    def accept_advice_result(self,result,scope,stamp,expected_disk):
        if (self._closed or not self.exam or stamp!=self.advice_input_stamp()
                or expected_disk!=self._saved_revision):
            raise ExamError('资料或成绩版本已变化，旧API返回未采纳；请重新核对发送范围。')
        self.check_disk_current()
        self.archive_advice('新的讲评草稿替代旧草稿')
        self.result=deepcopy(result); self.result['_local_evidence']=stamp
        self.result_scope=scope; self.dirty=True
        saved=self.save_current(); self.render()
        self.status.setText('新讲评草稿已保存；统计未被模型改写，仍待教师核对。' if saved else
                            '新建议仍保留在窗口，但未保存；请核对磁盘冲突或权限，不要直接关闭。')

    def open_score_correction(self):
        if not self.exam or self._task:return
        try:
            self.check_disk_current()
            self._score_dialog=ScoreCorrectionDialog(self); self._score_dialog.open()
        except ExamError as error:self.status.setText(error.message_zh)

    def apply_score_change(self,student_id,field,value,reason,*,expected=None):
        if not self.exam or self._task or self._closed:raise ExamError('当前窗口不能改分，请结束正在进行的任务。')
        if expected is not None and digest(self.bundle())!=expected:
            raise ExamError('打开改分窗口后数据已变化，请取消并重新打开核对；本次输入未写入。')
        self.check_disk_current()
        proposed=correct_score(self.exam,student_id,field,value,reason)
        if proposed==self.exam:return False
        bundle=self.bundle(); bundle['exam']=proposed
        # Commit to disk with the original compare-and-swap token before changing UI state.
        self.store.save(bundle,expected_revision=self._saved_revision)
        self.exam=proposed; self._saved_revision=digest(bundle); self.dirty=False
        self.followup_panel.preview=None; self.followup_panel.approved=False
        self.render(); self.status.setText('改分已保存，原Excel未改写。'+self.freshness_text())
        return True

    def show_revision_history(self):
        if not self.exam:return
        dialog=QDialog(self); dialog.setWindowTitle('改分与旧建议记录 · 仅供追溯'); dialog.resize(720,570)
        box=QVBoxLayout(dialog); text=QPlainTextEdit(); text.setReadOnly(True); box.addWidget(text)
        lines=['改分只修改本机分析副本；旧建议不自动作为当前建议使用。','\n本地改分记录：']
        for row in self.exam.get('score_changes',[]):
            lines.append(f"{row['at']} · {row['student_id']} · {row['field']}\n"
                         f"{row['before']} → {row['after']}\n依据：{row['reason']}")
        history=deepcopy(self.advice_history)
        if self.result:history.append({'reason':'当前保存的建议，须按版本状态判断','result':self.result})
        for item in history:
            lines.append('\n旧建议记录：'+str(item.get('reason','')))
            try:lines.append(advice_text(item['result']))
            except (TypeError,KeyError):lines.append('该历史建议格式不完整，原记录保留，未作为当前建议。')
        text.setPlainText('\n'.join(lines)); close=QPushButton('返回'); close.clicked.connect(dialog.accept); box.addWidget(close)
        self._revision_history_dialog=dialog; dialog.open()

    def export(self):
        if not self.report:return
        self.render()
        path,_=QFileDialog.getSaveFileName(self,'导出离线可视化报告','考试分析.html','HTML报告 (*.html)')
        if not path:return
        answer=QMessageBox.question(self,'学生标识','导出中保留本机学生和班级标识吗？选择“否”则使用临时代号。自由文字仍需自行核对。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.No)
        if answer==QMessageBox.StandardButton.Cancel:return
        try:
            from ..desktop_exam_followup import followup_html
            html=html_report(self.report,self.current_result(),include_names=answer==QMessageBox.StandardButton.Yes)
            allowed={s['id'] for s in self.report['students']}; tasks=[]; states=[]
            for original in self.followups:
                task=deepcopy(original)
                task['targets']=[t for t in task['targets'] if t['exam_student_id'] in allowed]
                task['attempts']=[a for a in task['attempts'] if a['student_id'] in allowed]
                if task['targets']:
                    tasks.append(task); states.append(escape(task.get('goal',''))+'：'+escape(task_freshness(self.exam,original)['message']))
            section='<section><h2>结果版本与复练依据</h2><p>'+escape(self.freshness_text())+'</p>'
            section+=''.join('<p>'+state+'</p>' for state in states)+'</section>'
            if tasks:section+=followup_html(tasks,include_names=answer==QMessageBox.StandardButton.Yes)
            html=html.replace('</body>',section+'</body>') if '</body>' in html else html+section
            Path(path).write_text(html,encoding='utf-8')
            self.status.setText('已导出当前统计与明确版本状态；过期AI未作为当前讲评，旧文件不自动替换。')
        except OSError:self.status.setText('导出未成功，请选择可写目录。')

    def to_preparation(self):
        if self.report:
            self.render()
            self.preparation_requested.emit(brief(self.report)+'\n\n'+self.freshness_text()+'\n'+self.advice_display())
            self.status.setText('已请求追加当前统计和版本提示；过期AI正文未带入，已保存的旧备课材料不会自动重写。')


class FollowupRevisionMixin:
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.evidence_button=QPushButton('核对改分影响'); self.evidence_button.setAutoDefault(False)
        self.evidence_button.setObjectName('QuietButton')
        # Existing state row contains selection_status and record. No extra grid row.
        self.layout().itemAt(4).layout().insertWidget(1,self.evidence_button)
        self.evidence_button.clicked.connect(self.open_evidence_review); self.show_task()

    def show_task(self):
        super().show_task()
        task=self.current()
        status=task_freshness(self.d.exam,task) if task and self.d.exam else None
        stale=bool(status and status['state']!='current')
        if hasattr(self,'evidence_button'):
            self.evidence_button.setVisible(stale)
            self.evidence_button.setEnabled(bool(status and status['state']!='blocked'))
        if stale:
            self.selection_status.setText(self.selection_status.text()+'\n'+status['message'])
            self.preview_button.setEnabled(False); self.export.setEnabled(False)
            self.text.appendPlainText('\n'+task_review_text(self.d.exam,task))

    def current_for(self,expected,*,require_fresh=False):
        current=super().current_for(expected)
        if require_fresh:
            status=task_freshness(self.d.exam,current)
            if status['state']!='current':raise ExamError(status['message'])
        return current

    def open_evidence_review(self):
        task=deepcopy(self.current())
        if not task:return
        stamp=score_revision(self.d.exam)
        dialog=QDialog(self); dialog.setWindowTitle('核对成绩变化对复练的影响'); dialog.resize(680,530)
        box=QVBoxLayout(dialog); text=QPlainTextEdit(); text.setReadOnly(True)
        text.setPlainText(task_review_text(self.d.exam,task)); box.addWidget(text,1)
        reason=QLineEdit(); reason.setPlaceholderText('核对依据，以及为什么保留当前复练安排'); box.addWidget(reason)
        confirmed=QCheckBox('我已核对新的成绩与任务安排，决定保留本任务'); box.addWidget(confirmed)
        message=QLabel('确认会保存新的依据核对记录，不覆盖最初成绩、实际复测或旧成品。'); message.setWordWrap(True); box.addWidget(message)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        commit=buttons.button(QDialogButtonBox.StandardButton.Save); commit.setText('保存核对记录'); commit.setEnabled(False)
        confirmed.toggled.connect(commit.setEnabled); buttons.rejected.connect(dialog.reject); box.addWidget(buttons)
        def save():
            try:
                if not confirmed.isChecked():raise ExamError('请先明确确认核对结论。')
                if score_revision(self.d.exam)!=stamp:raise ExamError('核对期间成绩再次变化，请重新打开。')
                current=self.current_for(task)
                changed=review_task(self.d.exam,current,reason.text())
                if self.store(changed,expected=current):
                    self.preview=None; self.approved=False; self.d.render(); dialog.accept()
                else:message.setText('核对记录未保存，请检查主窗口提示；输入仍保留。')
            except ExamError as error:message.setText(error.message_zh)
        buttons.accepted.connect(save)
        dialog.reason=reason; dialog.confirmed=confirmed; dialog.commit_button=commit
        dialog.message=message; self._evidence_dialog=dialog; dialog.open()

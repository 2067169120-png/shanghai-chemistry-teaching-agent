"""Teacher-confirmed bridge: exam evidence -> local questions -> actual retest."""
from __future__ import annotations
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QPlainTextEdit, QDialog, QListWidget, QListWidgetItem, QLineEdit,
    QDialogButtonBox, QDateEdit, QDoubleSpinBox, QFormLayout, QInputDialog)
from ..desktop_exam_data import ExamError
from ..desktop_exam_followup import (create_followup, link_basket, practice_request,
    record_attempt, task_summary, followup_text)
from ..desktop_question_explorer import personal_options


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


class ExamFollowupPanel(QWidget):
    def __init__(self, dashboard):
        super().__init__(dashboard); self.d = dashboard; self.preview = None; self.approved = False
        self.preview_task_id = None
        box = QVBoxLayout(self); box.setContentsMargins(8,8,8,8)
        self.hint = QLabel('先选考试题目，确认学生与目标，再选题、排卷并记录实际复测。')
        self.hint.setWordWrap(True); box.addWidget(self.hint)
        self.question = QComboBox(); self.question.setAccessibleName('复练对应的考试题目'); box.addWidget(self.question)
        self.tasks = QComboBox(); self.tasks.setAccessibleName('已保存的复练任务'); box.addWidget(self.tasks)
        actions = QGridLayout(); box.addLayout(actions)
        self.new = QPushButton('建立复练任务'); self.find = QPushButton('去题库按标签选题')
        self.link = QPushButton('关联题篮中的题'); self.preview_button = QPushButton('预览本任务练习卷')
        self.export = QPushButton('导出已核对练习卷'); self.record = QPushButton('记录实际复测')
        for i,b in enumerate((self.new,self.find,self.link,self.preview_button,self.export,self.record)):
            b.setAutoDefault(False); actions.addWidget(b,i//3,i%3)
        for b in (self.find,self.link,self.preview_button,self.export):
            b.setObjectName('QuietButton')
        self.text = QPlainTextEdit(); self.text.setReadOnly(True); self.text.setMinimumHeight(160); box.addWidget(self.text,1)
        self.new.clicked.connect(self.new_task); self.find.clicked.connect(self.find_questions)
        self.link.clicked.connect(self.link_questions); self.preview_button.clicked.connect(self.preview_paper)
        self.export.clicked.connect(self.export_paper); self.record.clicked.connect(self.record_result)
        self.tasks.currentIndexChanged.connect(self.show_task)
        self.refresh()

    def current(self):
        identity = self.tasks.currentData()
        return next((t for t in self.d.followups if t['id']==identity),None)

    def refresh(self, selected=None):
        self.question.clear()
        if self.d.report:
            for q in self.d.report['items']:
                self.question.addItem(f"第{q['question']}题 · {q['knowledge'] or '知识点待核对'}",q['question'])
        self.tasks.blockSignals(True); self.tasks.clear()
        for task in self.d.followups:
            self.tasks.addItem(task['goal'][:70],task['id'])
        if selected:
            self.tasks.setCurrentIndex(self.tasks.findData(selected))
        self.tasks.blockSignals(False); self.show_task()

    def show_task(self):
        task=self.current()
        if task:
            index=self.question.findData(task['question'])
            if index>=0:self.question.setCurrentIndex(index)
        for w in (self.find,self.link,self.preview_button,self.record):w.setEnabled(task is not None)
        self.export.setEnabled(bool(task and self.preview and self.approved and task['id']==self.preview_task_id))
        self.new.setEnabled(self.question.count()>0)
        self.text.setPlainText(followup_text([task]) if task else '尚无复练任务。缺考、缺失分数不自动认定为知识错误；仅有总分时需先补逐题映射。')

    def store(self, task):
        before=deepcopy(self.d.followups)
        self.d.followups=[t for t in self.d.followups if t['id']!=task['id']]+[task]
        self.d.dirty=True
        if not self.d.save_current():
            self.d.followups=before
            return False
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
        except ExamError as e:self.d.status.setText(e.message_zh)

    def find_questions(self):
        task=self.current()
        if not task:return
        choices=['本地 Word 题库','个人图片题库']
        choice,ok=QInputDialog.getItem(self,'选择真实题库来源','当前标签来自该来源的有效目录',choices,0,False)
        if not ok:return
        lane='word_native' if choice==choices[0] else 'visual_native'
        identity=task['id']
        def ready(catalog):
            options=personal_options(catalog,lane).get('knowledge',{}).get('values',[])
            options=[o for o in options if o['value']!='unknown']
            if not options:
                self.d.status.setText('此来源没有可筛选的知识点标签，请先导入并核对标签；未生成虚构命中。');return
            labels=[o['label_zh']+' ['+str(o['value'])+']' for o in options]
            suggested=[i for i,o in enumerate(options) if task['knowledge'] in (o['value'],o['label_zh'])]
            chosen,ok=QInputDialog.getItem(self,'核对题库知识点','考试标签与题库标签不自动等同，请选择确切对应',labels,suggested[0] if len(suggested)==1 else 0,False)
            if not ok:return
            option=options[labels.index(chosen)]
            revised=deepcopy(task);revised['library_filter']={'lane':lane,'knowledge':option['value'],'label':option['label_zh']}
            if not self.store(revised):return
            self.d.pending_handoff={'exam_id':self.d.exam['id'],'task_id':identity,**revised['library_filter']}
            # Defer close until the task bridge has completed its own callback.
            from PySide6.QtCore import QTimer
            def leave():
                if self.d._task:QTimer.singleShot(30,leave)
                else:self.d.close()
            QTimer.singleShot(0,leave)
        loader=self.d.facade.word_question_catalog if lane=='word_native' else self.d.facade.personal_visual_questions
        self.d.run('读取现有题库标签',lambda report,cancelled:loader(),ready)

    def link_questions(self):
        task=self.current()
        if not task:return
        try:
            basket=self.d.facade.basket()
            keys=checked_rows('只勾选用于本任务的题目（整主题保留公共材料）',[(r['key'],r.get('title_zh','未命名题')+' · '+r.get('source_zh','')) for r in basket],self)
            if keys and self.store(link_basket(task,basket,keys)):
                self.preview=None;self.approved=False;self.show_task()
        except Exception as e:self.d.status.setText(getattr(e,'message_zh','题篮未能读取；未改动已有任务。'))

    def preview_paper(self):
        task=deepcopy(self.current())
        if not task:return
        self.preview=None;self.approved=False;self.preview_task_id=task['id'];self.show_task()
        def prepare(report,cancelled):
            facade=self.d.facade
            request=practice_request(task,facade.basket(),facade.paper_basket_projection())
            if cancelled():raise ExamError('本次预览已取消。')
            content=facade.create_paper_preview(request)
            return facade.prepare_mixed_paper_pagination(content.preview_id,content.preview_hash)
        def ready(value):
            from .assembly_page import MixedPaperPaginationDialog
            self.preview=value
            dialog=MixedPaperPaginationDialog(value.preview_model,self.d.tasks,
                lambda key:self.d.facade.paper_preview_image(value.preview_id,key),self.d)
            self._preview_dialog=dialog
            def approve():
                def done(result):
                    if result.get('status')=='approved':
                        self.approved=True;dialog.mark_confirmed();self.show_task()
                self.d.run('确认本任务两版分页',lambda report,cancelled:self.d.facade.approve_paper_preview(value.preview_id,value.preview_hash),done)
            dialog.preview_confirmed.connect(approve);dialog.show()
        self.d.run('生成本任务学生与教师版真实分页',prepare,ready)

    def export_paper(self):
        task=deepcopy(self.current());value=self.preview
        if not task or not value or not self.approved or task['id']!=self.preview_task_id:return
        def done(result):
            if result.get('pdf_status')!='generated':raise ExamError('两版文件未完整生成。')
            task['exports'].append({'preview_id':value.preview_id,'preview_hash':value.preview_hash,
                                   'links':deepcopy(task['links']),'artifacts':result['artifacts']})
            if self.store(task):
                self.d.status.setText('本任务练习卷已导出；文件位置见下方任务记录。原题篮和组卷草稿未改动。')
                self.text.appendPlainText('\n导出文件：\n'+'\n'.join(a['path'] for a in result['artifacts']))
        self.d.run('导出本任务练习卷',lambda report,cancelled:self.d.facade.export_paper_preview(value.preview_id,value.preview_hash),done)

    def record_result(self):
        task=self.current()
        if not task:return
        dialog=QDialog(self);dialog.setWindowTitle('记录实际复测（不是预测提分）');dialog.resize(510,380)
        box=QVBoxLayout(dialog);form=QFormLayout();box.addLayout(form)
        student=QComboBox()
        for t in task['targets']:student.addItem(t['local_label'],t['exam_student_id'])
        when=QDateEdit();when.setCalendarPopup(True);when.setDisplayFormat('yyyy-MM-dd');when.setDate(QDate.currentDate())
        status=QComboBox()
        for text,value in [('已作答并评分','completed'),('未作答','not_attempted'),('资料不全','missing')]:status.addItem(text,value)
        score=QDoubleSpinBox();maximum=QDoubleSpinBox()
        for w in (score,maximum):w.setRange(0,10000);w.setDecimals(2)
        score.lineEdit().clear();maximum.lineEdit().clear()
        note=QLineEdit();note.setPlaceholderText('依据哪次作答/哪些题，哪些地方仍需核对')
        for label,w in [('学生',student),('实际日期',when),('情况',status),('本次实得分',score),('实际满分',maximum),('观察依据',note)]:form.addRow(label,w)
        msg=QLabel('未作答/资料不全不记零分；本次题目与原考试不同，不直接计算“提升多少分”。');msg.setWordWrap(True);box.addWidget(msg)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject);box.addWidget(buttons)
        def save():
            try:
                if status.currentData()=='completed' and (not score.text().strip() or not maximum.text().strip()):
                    raise ExamError('请明确填写本次实得分和实际满分，空白不按0分保存。')
                record=record_attempt(task,student.currentData(),when.date().toString('yyyy-MM-dd'),status.currentData(),score.value(),maximum.value(),note.text())
                if self.store(record):dialog.accept()
            except ExamError as e:msg.setText(e.message_zh)
        buttons.accepted.connect(save);dialog.exec();dialog.deleteLater()

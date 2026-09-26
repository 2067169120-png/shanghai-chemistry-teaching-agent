"""A native assignment batch, embedding the existing single-submission reviewer."""
from __future__ import annotations
from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox,QDialog,QHBoxLayout,QLabel,QLineEdit,
    QListWidget,QListWidgetItem,QMessageBox,QPushButton,QVBoxLayout,QWidget)
from ..desktop_work_batches import CONDITIONS, WorkBatchStore
from ..desktop_review_evidence import value
from .components import set_status
from .student_review_desk import StudentReviewDesk


class BatchMemberDialog(QDialog):
    """Explicitly choose references; no guessed identity or class membership."""
    def __init__(self,rows,existing=None,parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择同一作业的学生作答');self.resize(720,620)
        layout=QVBoxLayout(self)
        self.title=QLineEdit((existing or {}).get('title',''));self.title.setPlaceholderText('作业名称，例如：高二化学·平衡专题第1次练习');self.title.setMaxLength(120)
        self.class_label=QLineEdit((existing or {}).get('class_label',''));self.class_label.setPlaceholderText('班级备注（仅本机，可不填）');self.class_label.setMaxLength(100)
        layout.addWidget(self.title);layout.addWidget(self.class_label)
        info=QLabel('选择本次作业对应的已有作答；每名学生只选一份。这里不上传文件、不自动识别姓名，也不启动批量模型分析。')
        info.setWordWrap(True);layout.addWidget(info)
        self.search=QLineEdit();self.search.setPlaceholderText('按学生代号、日期或状态查找');layout.addWidget(self.search)
        self.rows=QListWidget();layout.addWidget(self.rows,1)
        selected={(r['student_id'],r['submission_id']) for r in (existing or {}).get('members',[])}
        for row in rows:
            label=f"{row['label']} · {row['grade']} · {row['created_at'][:16].replace('T',' ')} · {row['status']} · {row['match_count']}项作答"
            item=QListWidgetItem(label);item.setData(Qt.ItemDataRole.UserRole,row)
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if (row['student_id'],row['submission_id']) in selected else Qt.CheckState.Unchecked)
            self.rows.addItem(item)
        self.error=QLabel();self.error.setWordWrap(True);layout.addWidget(self.error)
        actions=QHBoxLayout();ok=QPushButton('保存批次');cancel=QPushButton('取消')
        actions.addStretch(1);actions.addWidget(ok);actions.addWidget(cancel);layout.addLayout(actions)
        ok.clicked.connect(self._accept);cancel.clicked.connect(self.reject)
        self.search.textChanged.connect(self._filter)
        for button in self.findChildren(QPushButton):button.setAutoDefault(False)

    def _filter(self,text):
        for i in range(self.rows.count()):
            item=self.rows.item(i);item.setHidden(text.casefold() not in item.text().casefold())

    def selection(self):
        return [{k:self.rows.item(i).data(Qt.ItemDataRole.UserRole)[k] for k in ('student_id','submission_id')}
                for i in range(self.rows.count()) if self.rows.item(i).checkState()==Qt.CheckState.Checked]

    def _accept(self):
        members=self.selection()
        if not self.title.text().strip() or not members:
            self.error.setText('请填写作业名称并选择至少一份作答。');return
        if len({m['student_id'] for m in members})!=len(members):
            self.error.setText('同一学生有多份作答被勾选，请只保留本次要批改的一份。');return
        self.accept()


class BatchStudentReviewDesk(StudentReviewDesk):
    """Use the existing score writer; batch switches save working inputs only."""
    def __init__(self,store,tasks,summary,review,sections,pending,conditions,*,student_label,parent):
        self.store=store;self.pending_revision=pending.get('revision')
        self._pending_blocked=bool(pending.get('source_changed') and (pending.get('changes') or pending.get('observations')))
        self._condition_record=conditions
        self._condition_inputs=deepcopy(pending.get('observations',{})) if not self._pending_blocked else {}
        self._last_pending=None;self._pending_ready=False
        initial=pending.get('changes',{}) if not self._pending_blocked else {}
        super().__init__(store.facade,tasks,summary,review,sections,student_label=student_label,initial_edits=initial,parent=parent)
        self.setParent(parent,Qt.WindowType.Widget);self.setModal(False);self.setMinimumSize(0,0)
        self.layout().setContentsMargins(0,0,0,0);self.layout().setSpacing(6)
        self.layout().itemAt(0).widget().hide();self.identity.hide();self.close_button.hide()
        self.condition=QComboBox();self.condition.setAccessibleName('本题作答情况')
        for key,label in CONDITIONS.items():self.condition.addItem(label,key)
        self.condition_note=QLineEdit();self.condition_note.setPlaceholderText('简要依据，例如：缺少第2页');self.condition_note.setMaxLength(1000)
        self.condition_save=QPushButton('记录状态');self.condition_save.setObjectName('QuietButton')
        row=QHBoxLayout();row.addWidget(self.condition);row.addWidget(self.condition_note,1);row.addWidget(self.condition_save)
        self.layout().insertLayout(4,row)
        self.condition_saved=QLabel();self.condition_saved.setWordWrap(True);self.condition_saved.setObjectName('MutedLabel')
        self.layout().insertWidget(5,self.condition_saved)
        footer=QHBoxLayout();self.stash=QPushButton('暂存输入');self.discard=QPushButton('清除暂存')
        self.stash.setObjectName('QuietButton');self.discard.setObjectName('QuietButton')
        self.draft_notice=QLabel('切换学生或返回时暂存输入，不计入正式评分。');self.draft_notice.setWordWrap(True)
        footer.addWidget(self.draft_notice,1);footer.addWidget(self.stash);footer.addWidget(self.discard);self.layout().addLayout(footer)
        self.stash.clicked.connect(self.persist_pending);self.discard.clicked.connect(self.discard_pending)
        self.condition_save.clicked.connect(self.record_condition)
        self.condition.currentIndexChanged.connect(self._capture_condition)
        self.condition_note.textChanged.connect(self._capture_condition)
        self._pending_ready=True
        self._show_condition()
        if self._pending_blocked:
            self.draft_notice.setText('原题对应已变化，旧暂存未套用。请先核对并明确清除旧暂存。')
        elif initial or self._condition_inputs:
            self.draft_notice.setText('已恢复本份暂存输入；请核对后再记录评分。'+
                ('已有正式记录变化，请特别核对。' if pending.get('review_revision')!=review.revision else ''))
        self._last_pending=self.pending_payload()
        for button in self.findChildren(QPushButton):button.setAutoDefault(False)

    def _choose(self,*args):
        super()._choose(*args)
        if getattr(self,'_pending_ready',False):self._show_condition()

    def _show_condition(self):
        key=self._current
        stored=self._condition_record.get('items',{}).get(key,{}) if not self._condition_record.get('source_changed') else {}
        current=self._condition_inputs.get(key,stored)
        self.condition.blockSignals(True);self.condition_note.blockSignals(True)
        self.condition.setCurrentIndex(max(0,self.condition.findData(current.get('condition','unmarked'))))
        self.condition_note.setText(current.get('note',''))
        self.condition.blockSignals(False);self.condition_note.blockSignals(False)
        label=CONDITIONS.get(stored.get('condition'),'未标记')
        self.condition_saved.setText(f'已记录状态：{label}。状态仅说明作答情况，不改分数或自动判为错误。')

    def _capture_condition(self,*_):
        if self._current:
            self._condition_inputs[self._current]={'condition':self.condition.currentData(),'note':self.condition_note.text()}

    def pending_payload(self):
        changes={}
        for key,fields in self.edit_values().items():
            baseline=self._baselines.get(key,{})
            delta={k:v for k,v in fields.items() if v!=baseline.get(k)}
            if delta:changes[key]=delta
        observations={}
        for key,row in self._condition_inputs.items():
            old=self._condition_record.get('items',{}).get(key,{})
            old={'condition':old.get('condition','unmarked'),'note':old.get('note','')}
            if row!=old:observations[key]=deepcopy(row)
        return {'changes':changes,'observations':observations}

    def persist_pending(self,*_,force=False):
        if self._busy:return False
        payload=self.pending_payload()
        if self._pending_blocked:
            self.draft_notice.setText('旧暂存对应的原题已变化，请先核对后清除旧暂存。');return False
        if not force and payload==self._last_pending:return True
        try:
            saved=self.store.save_pending(self.summary,self.review,**payload,expected_revision=self.pending_revision)
        except Exception as e:
            self.draft_notice.setText(getattr(e,'message_zh','暂存失败，当前输入保留，请重试。'));return False
        self.pending_revision=saved['revision'];self._last_pending=deepcopy(payload)
        self.draft_notice.setText('输入已暂存；尚未记录的分数和状态不计入正式结果。')
        return True

    def _replace_review(self,review,states):
        super()._replace_review(review,states)
        if getattr(self,'_pending_ready',False):self.persist_pending()

    def record_condition(self):
        if self._busy or not self._current:return
        self._capture_condition()
        key=self._current;row=deepcopy(self._condition_inputs[key])
        try:
            self._condition_record=self.store.save_condition(self.summary,key,**row,
                expected_revision=self._condition_record.get('revision'))
        except Exception as e:
            self.condition_saved.setText(getattr(e,'message_zh','状态未保存，输入保留。'));return
        self._show_condition();self.persist_pending()

    def discard_pending(self):
        if self._busy:return
        if QMessageBox.question(self,'清除未记录输入',
            '清除本份作答所有暂存的分数、理由和状态输入；已经正式记录的评分和诊断保持。是否继续？',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:
            saved=self.store.save_pending(self.summary,self.review,{},observations={},expected_revision=self.pending_revision)
        except Exception as e:
            self.draft_notice.setText(getattr(e,'message_zh','清除未完成，输入保留。'));return
        self.pending_revision=saved['revision'];self._pending_blocked=False;self._condition_inputs={}
        self._states={};self._baselines={};self._last_pending={'changes':{},'observations':{}}
        self._replace_review(self.review,{})
        self.draft_notice.setText('未记录输入已清除；正式评分、诊断与原页未修改。')


class WorkBatchDialog(QDialog):
    def __init__(self,facade,tasks,parent=None):
        super().__init__(parent)
        self.facade=facade;self.tasks=tasks;self.store=WorkBatchStore(facade)
        self._batch=None;self._members=[];self._current_index=-1;self.desk=None
        self._busy=False;self._closed=False;self._generation=0
        self.setWindowTitle('作业批次 · 逐人复核');self.resize(1366,820);self.setMinimumSize(780,650)
        root=QVBoxLayout(self);root.setContentsMargins(12,8,12,8);root.setSpacing(6)
        top=QHBoxLayout();self.batch=QComboBox();self.batch.setMinimumWidth(0)
        self.batch.setAccessibleName('选择作业批次')
        self.batch.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.batch.setMinimumContentsLength(12)
        self.new=QPushButton('新建批次');self.edit=QPushButton('编辑批次')
        self.overview=QPushButton('正式评分统计');self.overview.clicked.connect(self.open_summary)
        top.addWidget(QLabel('作业批次'));top.addWidget(self.batch,1);top.addWidget(self.new);top.addWidget(self.edit);top.addWidget(self.overview);root.addLayout(top)
        nav=QHBoxLayout();self.previous=QPushButton('上一名');self.next=QPushButton('下一名')
        self.student=QComboBox();self.student.setMinimumWidth(0);self.student.setAccessibleName('本批次学生作答')
        nav.addWidget(self.previous);nav.addWidget(self.student,1);nav.addWidget(self.next);root.addLayout(nav)
        self.description=QLabel();self.description.setWordWrap(True);self.description.setObjectName('MutedLabel');root.addWidget(self.description)
        self.content=QVBoxLayout();root.addLayout(self.content,1)
        self.placeholder=QLabel('新建批次并选择已有作答。没有已有分析的作答，请先在学生分析中完成原流程。')
        self.placeholder.setWordWrap(True);self.content.addWidget(self.placeholder)
        bottom=QHBoxLayout();self.status=QLabel();self.status.setWordWrap(True);bottom.addWidget(self.status,1)
        self.back=QPushButton('返回学生分析');self.back.clicked.connect(self.reject);bottom.addWidget(self.back);root.addLayout(bottom)
        self.batch.activated.connect(self.choose_batch);self.student.activated.connect(self.choose_student)
        self.previous.clicked.connect(lambda:self.choose_student(self._current_index-1))
        self.next.clicked.connect(lambda:self.choose_student(self._current_index+1))
        self.new.clicked.connect(lambda:self.manage_batch(False));self.edit.clicked.connect(lambda:self.manage_batch(True))
        for b in self.findChildren(QPushButton):b.setAutoDefault(False)
        self.reload_batches()

    def open_summary(self):
        if not self._batch or not self._flush():return
        from ..desktop_batch_exam import collect_batch_exams
        from PySide6.QtWidgets import QInputDialog
        self._busy=True;self._controls()
        def ready(result):
            self._busy=False;self._controls()
            groups=result['groups']
            if not groups:
                self.status.setText('没有题目身份与满分完整的可汇总作答；未把未知项计零。');return
            labels=[f"同卷组{i+1} · {g['count']}人 · {len(g['exam']['questions'])}个已匹配评分单元" for i,g in enumerate(groups)]
            choice,ok=QInputDialog.getItem(self,'选择可比较的同卷组',
                f"批次共{len(result['batch']['members'])}人，{len(result['unavailable'])}份无法分组。不同原题组不混算。",labels,0,False)
            if not ok:return
            exam=groups[labels.index(choice)]['exam']
            # Close the batch modal before routing to the shared exam window.
            parent=self.parentWidget()
            while parent and not callable(getattr(parent,'open_exam_dialog',None)):parent=parent.parentWidget()
            if parent:
                from PySide6.QtCore import QTimer
                self.reject();QTimer.singleShot(0,lambda:parent.open_exam_dialog(exam=exam))
            else:
                from .exam_dashboard import ExamDashboard
                d=ExamDashboard(self.facade,self.tasks,self);d.accept_exam(exam);d.exec();d.deleteLater()
        self.tasks.submit('汇总教师正式评分',lambda:collect_batch_exams(self.facade,self._batch['batch_id']),
                          on_success=ready,on_failure=self._failed)

    def _controls(self):
        for w in (self.batch,self.new,self.student):w.setEnabled(not self._busy)
        self.edit.setEnabled(not self._busy and self._batch is not None)
        self.overview.setEnabled(not self._busy and self._batch is not None)
        self.previous.setEnabled(not self._busy and self._current_index>0)
        self.next.setEnabled(not self._busy and 0<=self._current_index<len(self._members)-1)

    def _flush(self):
        if self._busy:return False
        if self.desk and (self.desk._busy or not self.desk.persist_pending()):
            self.status.setText('当前记录尚未保存或暂存失败，未切换学生。');return False
        return True

    def _clear_desk(self):
        if self.desk:
            self.desk._closed=True;self.desk.viewer.stop();self.content.removeWidget(self.desk)
            self.desk.hide();self.desk.deleteLater();self.desk=None
        self.placeholder.show()

    def reload_batches(self,selected=None):
        self._busy=True;self._controls()
        def done(rows):
            if self._closed:return
            self._busy=False;self.batch.blockSignals(True);self.batch.clear()
            for row in rows:self.batch.addItem(f"{row['title']} · {len(row['members'])}份",row)
            index=next((i for i,r in enumerate(rows) if r['batch_id']==selected),0)
            self.batch.setCurrentIndex(index if rows else -1);self.batch.blockSignals(False)
            self._controls()
            if rows:self.choose_batch(index)
        self.tasks.submit('读取作业批次',self.store.list_batches,on_success=done,on_failure=self._failed)

    def choose_batch(self,index):
        if not self._flush():
            self.batch.blockSignals(True)
            for i in range(self.batch.count()):
                if self._batch and self.batch.itemData(i)['batch_id']==self._batch['batch_id']:self.batch.setCurrentIndex(i)
            self.batch.blockSignals(False);return
        batch=self.batch.itemData(index)
        if not batch:return
        self._batch=batch;self._members=self.store.members(batch);self._current_index=-1
        self.student.blockSignals(True);self.student.clear()
        for i,row in enumerate(self._members):self.student.addItem(f"{i+1}/{len(self._members)} · {row['label']}",row)
        self.student.blockSignals(False)
        self.description.setText(f"{batch['class_label'] or '未填班级备注'} · 本批次{len(self._members)}份作答。逐人复核，不按未评分计算均分。")
        self.choose_student(0)

    def choose_student(self,index):
        if not 0<=index<len(self._members):return
        if not self._flush():
            self.student.setCurrentIndex(self._current_index);return
        self._clear_desk();self._current_index=index;self.student.setCurrentIndex(index)
        self._busy=True;self._generation+=1;epoch=self._generation;self._controls()
        target=deepcopy(self._members[index]);self.placeholder.setText('正在读取该生作答…');self.status.setText('')
        def load():
            binding={k:target[k] for k in ('student_id','submission_id')}
            summary=self.facade.student_submission(**binding)
            if not summary.candidate_available:return (summary,None,None,None,None)
            review=self.facade.student_analysis_review(**binding)
            return (summary,review,self.facade.student_curriculum_sections(),
                    self.store.load_pending(summary),self.store.conditions(summary))
        def loaded(result):
            if self._closed or epoch!=self._generation:return
            self._busy=False;self._controls();summary,review,sections,pending,conditions=result
            if review is None or not review.items:
                self.placeholder.setText('该生作答尚无可复核的分析结果。返回学生分析完成上传、匹配和分析后，再打开本批次。');return
            self.desk=BatchStudentReviewDesk(self.store,self.tasks,summary,review,sections,pending,conditions,
                student_label=target['label'],parent=self)
            self.placeholder.hide();self.content.addWidget(self.desk,1);self.desk.show()
            self.status.setText('切换或返回会暂存未记录输入；正式评分和诊断仍由教师点击记录。')
        self.tasks.submit('打开本批次学生作答',load,on_success=loaded,on_failure=self._failed)

    def manage_batch(self,editing):
        if not self._flush():return
        existing=self._batch if editing else None
        self._busy=True;self._controls()
        def loaded(rows):
            if self._closed:return
            self._busy=False;self._controls()
            dialog=BatchMemberDialog(rows,existing,self)
            if dialog.exec()==QDialog.DialogCode.Accepted:
                try:
                    result=self.store.save_batch(dialog.title.text(),dialog.class_label.text(),dialog.selection(),
                        batch_id=existing['batch_id'] if existing else None,
                        expected_revision=existing['revision'] if existing else None)
                except Exception as e:self._failed(getattr(e,'message_zh','批次未保存，原批次保持。'))
                else:self.reload_batches(result['batch_id'])
            dialog.deleteLater()
        self.tasks.submit('列出已有学生作答',self.store.available_submissions,on_success=loaded,on_failure=self._failed)

    def _failed(self,message):
        if self._closed:return
        self._busy=False;self._controls();set_status(self.status,'error',message)
        if self.desk is None:self.placeholder.setText('本次读取未完成，可选择其他学生或返回。没有使用上一名学生的图片。')

    def reject(self):
        if not self._flush():return
        self._closed=True;self._generation+=1;self._clear_desk();super().reject()

    def closeEvent(self,event):
        if not self._closed:
            if not self._flush():event.ignore();return
            self._closed=True;self._generation+=1;self._clear_desk()
        super().closeEvent(event)

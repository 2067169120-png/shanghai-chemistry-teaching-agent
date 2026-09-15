"""Single-submission review desk, reusing the existing append-only decisions.

Unsaved inputs stay scoped to a match while navigating. Only explicit record
buttons call the facade. No new student store or automatic model requests.
"""
from __future__ import annotations
from copy import deepcopy
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QStackedWidget,
    QTabWidget, QVBoxLayout, QWidget)
from ..desktop_review_evidence import review_counts, value
from .components import page_scroll, set_status
from .review_page_viewer import ReviewPageViewer
from .student_page import ReviewItemCard

_TEXT_FIELDS=('score_edit','score_reason','teacher_note')
_CHOICE_FIELDS=('decision','result','primary_error','secondary_error','section')
_SCORE_FIELDS=('score_edit','score_reason')
_DIAG_FIELDS=('teacher_note',*_CHOICE_FIELDS)


def editor_values(editor):
    return {**{k:getattr(editor,k).text() for k in _TEXT_FIELDS},
            **{k:getattr(editor,k).currentData() for k in _CHOICE_FIELDS}}


def restore_values(editor, values):
    for key in _TEXT_FIELDS:
        if key in values:getattr(editor,key).setText(str(values[key]))
    # Decision and result must be restored before their dependent fields.
    for key in _CHOICE_FIELDS:
        if key in values:
            widget=getattr(editor,key);index=widget.findData(values[key])
            if index>=0:widget.setCurrentIndex(index)


class StudentReviewDesk(QDialog):
    """One existing submission. Change students/tasks in the parent workflow."""
    def __init__(self, facade, tasks, summary, review, sections=(), *,
                 student_label='匿名学生', initial_edits=None, parent=None):
        super().__init__(parent)
        if (value(summary,'student_id'),value(summary,'submission_id')) != (value(review,'student_id'),value(review,'submission_id')):
            raise ValueError('Review and original pages must identify the same submission')
        self.facade,self.tasks,self.summary,self.review=facade,tasks,summary,review
        self.sections=sections;self.student_id=value(review,'student_id');self.submission_id=value(review,'submission_id')
        self._items={value(i,'match_id'):i for i in value(review,'items',())}
        self._editors={};self._tabs={};self._states=deepcopy(initial_edits or {});self._baselines={}
        self._current=None;self._busy=False;self._closed=False;self._write_count=0
        self._refresh_task=None;self._discarded=False;self._page_states={}
        self.setWindowTitle('作答与评分 · 同屏批改');self.resize(1366,820);self.setMinimumSize(760,600)
        self.setModal(True)
        root=QVBoxLayout(self);root.setContentsMargins(16,12,16,12);root.setSpacing(10)
        header=QLabel('作答与评分');header.setObjectName('PageTitle');root.addWidget(header)
        self.identity=QLabel(f'{student_label}　·　作答保存时间 {str(value(summary,"created_at",""))[:16].replace("T"," ")}')
        self.identity.setTextFormat(Qt.TextFormat.PlainText);self.identity.setWordWrap(True);root.addWidget(self.identity)
        top=QHBoxLayout()
        self.question=QComboBox();self.question.setAccessibleName('选择待复核题目');self.question.setMinimumWidth(0)
        self.previous=QPushButton('上一题');self.next=QPushButton('下一题')
        self.reload=QPushButton('刷新已存记录');self.reload.setObjectName('QuietButton')
        self.reload.setToolTip('重新读取已保存决定；保留本窗口未记录的输入。')
        top.addWidget(self.previous);top.addWidget(self.question,1);top.addWidget(self.next);top.addWidget(self.reload)
        root.addLayout(top)
        self.summary_label=QLabel();self.summary_label.setWordWrap(True);self.summary_label.setObjectName('MutedLabel')
        root.addWidget(self.summary_label)
        self.splitter=QSplitter(Qt.Orientation.Horizontal);self.splitter.setChildrenCollapsible(False)
        self.outline=QListWidget();self.outline.setMinimumWidth(140);self.outline.setMaximumWidth(230)
        self.outline.setAccessibleName('本次作答题目与复核进度')
        self.splitter.addWidget(self.outline)
        self.viewer=ReviewPageViewer(facade,tasks,summary);self.splitter.addWidget(self.viewer)
        right=QWidget();right.setMinimumWidth(320);right_layout=QVBoxLayout(right);right_layout.setContentsMargins(0,0,0,0)
        self.editor_stack=QStackedWidget();right_layout.addWidget(self.editor_stack,1)
        self.score_button=QPushButton('记录本题评分');self.score_button.setAccessibleName('同屏记录本题教师评分')
        self.diagnosis_button=QPushButton('记录诊断');self.diagnosis_button.setObjectName('QuietButton')
        self.diagnosis_button.setAccessibleName('同屏记录本题诊断')
        actions=QHBoxLayout();actions.addWidget(self.score_button,1);actions.addWidget(self.diagnosis_button,1);right_layout.addLayout(actions)
        self.splitter.addWidget(right);self.splitter.setSizes([180,610,500]);root.addWidget(self.splitter,1)
        self.status=QLabel('原图在左，评分在右；切题保留输入，点击记录后才保存。')
        self.status.setWordWrap(True);self.status.setAccessibleName('同屏批改操作结果');root.addWidget(self.status)
        footer=QHBoxLayout()
        self.dirty_label=QLabel();self.dirty_label.setObjectName('MutedLabel');footer.addWidget(self.dirty_label,1)
        self.close_button=QPushButton('返回学生分析');self.close_button.setObjectName('QuietButton');footer.addWidget(self.close_button)
        root.addLayout(footer)
        self.question.currentIndexChanged.connect(self._choose)
        self.outline.currentRowChanged.connect(self.question.setCurrentIndex)
        self.previous.clicked.connect(lambda:self.question.setCurrentIndex(self.question.currentIndex()-1))
        self.next.clicked.connect(lambda:self.question.setCurrentIndex(self.question.currentIndex()+1))
        self.reload.clicked.connect(self.refresh_records)
        self.score_button.clicked.connect(lambda:self._emit('score'))
        self.diagnosis_button.clicked.connect(lambda:self._emit('diagnosis'))
        self.close_button.clicked.connect(self.reject)
        for button in self.findChildren(QPushButton):button.setAutoDefault(False)
        self._populate()

    def _populate(self):
        selected=self._current
        self.question.blockSignals(True);self.outline.blockSignals(True)
        self.question.clear();self.outline.clear()
        for key,item in self._items.items():
            self.question.addItem(str(value(item,'label_zh','题目')),key)
            self.outline.addItem(QListWidgetItem(str(value(item,'label_zh','题目'))))
        index=self.question.findData(selected)
        self.question.setCurrentIndex(max(0,index) if self._items else -1)
        self.question.blockSignals(False);self.outline.blockSignals(False)
        self._choose()

    def _build_editor(self,key):
        item=self._items[key]
        editor=ReviewItemCard(item,self.sections,diagnosis_available=bool(self.sections))
        editor.set_compact(True)
        editor.score_reason.setMaxLength(1000);editor.teacher_note.setMaxLength(1000)
        # Do not preset a new diagnosis to "correct / accept".
        editor.decision.setCurrentIndex(editor.decision.findData('pending'))
        editor.result.setCurrentIndex(editor.result.findData('not_scored'))
        baseline=editor_values(editor)
        self._baselines.setdefault(key,baseline)
        restore_values(editor,self._states.get(key,{}))
        # Keep the existing validators and signals, but move candidate detail out
        # of the grading form so it cannot bury the teacher's inputs.
        candidate=QWidget();candidate_layout=QVBoxLayout(candidate);candidate_layout.setContentsMargins(12,12,12,12)
        layout=editor.layout()
        while layout.count() and layout.itemAt(0).widget() is not editor.latest_score_label:
            part=layout.takeAt(0)
            if part.widget():candidate_layout.addWidget(part.widget())
        candidate_layout.addStretch(1)
        reason=value(item,'latest_score_reason_zh','')
        if reason:
            label=QLabel('上次评分理由：'+reason);label.setWordWrap(True);label.setObjectName('MutedLabel');layout.insertWidget(1,label)
        note=value(item,'latest_diagnostic_note_zh','')
        if note:
            label=QLabel('上次诊断备注：'+note);label.setWordWrap(True);layout.insertWidget(2,label)
        if value(item,'diagnostic_requires_reconfirmation',False):
            label=QLabel('评分已修改，原诊断需重新核对。');label.setWordWrap(True);set_status(label,'attention');layout.insertWidget(1,label)
        editor.record_score_button.hide();editor.record_diagnosis_button.hide()
        for widget in (editor,candidate):
            for label in widget.findChildren(QLabel):label.setTextFormat(Qt.TextFormat.PlainText)
        tabs=QTabWidget();tabs.addTab(page_scroll(editor),'教师评分');tabs.addTab(page_scroll(candidate),'AI建议与评分点')
        self.editor_stack.addWidget(tabs)
        self._editors[key]=editor;self._tabs[key]=tabs
        editor.score_requested.connect(lambda payload,k=key:self._record(k,'score',payload))
        editor.diagnosis_requested.connect(lambda payload,k=key:self._record(k,'diagnosis',payload))
        for name in _TEXT_FIELDS:getattr(editor,name).textChanged.connect(self._update_status)
        for name in _CHOICE_FIELDS:getattr(editor,name).currentIndexChanged.connect(self._update_status)
        return editor

    def _choose(self,*_):
        key=self.question.currentData()
        if key is None:
            self._current=None;self._update_status();return
        changed=key!=self._current
        if changed and self._current:self._page_states[self._current]=self.viewer.view_state()
        self._current=key
        if key not in self._editors:self._build_editor(key)
        self.editor_stack.setCurrentWidget(self._tabs[key])
        self.outline.blockSignals(True);self.outline.setCurrentRow(self.question.currentIndex());self.outline.blockSignals(False)
        if changed:self.viewer.set_item(self._items[key],self._page_states.get(key))
        self._update_status()

    def edit_values(self):
        values=deepcopy(self._states)
        values.update({k:editor_values(e) for k,e in self._editors.items()})
        return {k:v for k,v in values.items() if k in self._items}

    def dirty_keys(self):
        return [k for k,v in self.edit_values().items() if v!=self._baselines.get(k,{})]

    def _update_status(self,*_):
        if self._closed:return
        dirty=set(self.dirty_keys());counts=review_counts(self.review)
        self.summary_label.setText(f'本次 {counts["items"]} 项作答 · 已记录评分 {counts["scored"]} 项 · 未评分 {counts["unscored"]} 项。未评分不按0分计算。')
        self.dirty_label.setText(f'{len(dirty)}题有未记录输入' if dirty else '当前没有未记录输入')
        for i,(key,item) in enumerate(self._items.items()):
            score=value(item,'latest_teacher_score')
            text=f'{value(item,"label_zh","题目")}　'+('未评分' if score is None else f'{score:g}/{value(item,"maximum_score",0):g}分')
            if key in dirty:text+=' *'
            self.question.setItemText(i,text)
            self.outline.item(i).setText(text)
        editor=self._editors.get(self._current)
        self.score_button.setEnabled(bool(editor) and not self._busy)
        self.diagnosis_button.setEnabled(bool(editor and editor.record_diagnosis_button.isEnabled()) and not self._busy)
        self.previous.setEnabled(not self._busy and self.question.currentIndex()>0)
        self.next.setEnabled(not self._busy and 0<=self.question.currentIndex()<self.question.count()-1)
        for control in (self.question,self.outline,self.reload,self.editor_stack):control.setEnabled(not self._busy)

    def _emit(self,kind):
        editor=self._editors.get(self._current)
        if editor is None or self._busy:return
        self._tabs[self._current].setCurrentIndex(0)
        (editor.record_score_button if kind=='score' else editor.record_diagnosis_button).click()
        # Validation remains in the existing editor. Bring its message into view.
        target=editor.score_error if kind=='score' else editor.diagnosis_error
        if not target.isHidden():self._tabs[self._current].widget(0).ensureWidgetVisible(target)

    def _record(self,key,kind,payload):
        if self._busy or key!=self._current or payload.get('match_id')!=key:return
        self._busy=True;self._update_status();set_status(self.status,'info','正在保存教师决定…')
        binding=dict(student_id=self.student_id,submission_id=self.submission_id,
                     expected_revision=value(self.review,'revision'))
        operation=self.facade.record_student_score if kind=='score' else self.facade.record_student_diagnosis
        request=deepcopy(dict(payload))
        def success(review):
            self._busy=False
            if self._closed:return
            if (value(review,'student_id'),value(review,'submission_id'))!=(self.student_id,self.submission_id):
                self._failed('返回的记录与当前作答不一致，请刷新后核对。');return
            self._write_count+=1
            states=self.edit_values()
            if kind=='score':
                for field in _SCORE_FIELDS:states[key][field]=''
            baseline=self._baselines.setdefault(key,{})
            for field in (_SCORE_FIELDS if kind=='score' else _DIAG_FIELDS):baseline[field]=states[key][field]
            self._replace_review(review,states)
            set_status(self.status,'success','教师评分已记录；其他题的未记录输入保留。' if kind=='score' else '教师诊断已记录；模型原建议未改写。')
        self.tasks.submit('保存同屏复核决定',lambda:operation(**binding,**request),on_success=success,on_failure=self._failed)

    def _replace_review(self,review,states):
        self.review=review;self._states=states
        self._items={value(i,'match_id'):i for i in value(review,'items',())}
        for tabs in self._tabs.values():self.editor_stack.removeWidget(tabs);tabs.hide();tabs.deleteLater()
        self._tabs={};self._editors={}
        self._populate()
        # Score writes do not move the teacher away from the current source page.

    def _failed(self,message):
        self._busy=False
        if self._closed:return
        self._update_status();set_status(self.status,'error',message+' 未记录的输入仍保留；可刷新已存记录后核对再保存。')

    def refresh_records(self):
        if self._busy:return
        self._busy=True;self._update_status();set_status(self.status,'info','正在重新读取已存决定，保留未记录输入…')
        def loaded(review):
            self._busy=False
            if self._closed:return
            if (value(review,'student_id'),value(review,'submission_id'))!=(self.student_id,self.submission_id):
                self._failed('返回了另一份作答，未载入。');return
            self._replace_review(review,self.edit_values())
            set_status(self.status,'info','已刷新保存记录；未记录输入保留，请与新记录核对。')
        self._refresh_task=self.tasks.submit('刷新本次批改',lambda:self.facade.student_analysis_review(
            student_id=self.student_id,submission_id=self.submission_id),on_success=loaded,on_failure=self._failed)

    def _allow_close(self):
        if self._busy:
            set_status(self.status,'attention','当前决定正在保存，请完成后再返回。');return False
        if self.dirty_keys():
            answer=QMessageBox.question(self,'还有未记录的输入',
                '有评分或诊断尚未点击“记录”。返回将放弃这些输入，已经记录的决定保留。是否放弃未记录输入并返回？',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
            if answer!=QMessageBox.StandardButton.Yes:return False
            self._states=deepcopy(self._baselines);self._discarded=True
        return True

    def accept(self):
        self.reject()

    def reject(self):
        if not self._closed and not self._allow_close():return
        self._closed=True;self.viewer.stop();super().reject()

    def closeEvent(self,event):
        if not self._closed and not self._allow_close():event.ignore();return
        self._closed=True;self.viewer.stop();super().closeEvent(event)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.outline.setVisible(event.size().width()>=1100)

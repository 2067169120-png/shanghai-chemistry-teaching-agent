"""Native exam dashboard: exact local charts, optional explicitly confirmed API advice."""
from __future__ import annotations
import base64
from copy import deepcopy
import json
from pathlib import Path
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QTabWidget, QTableView, QPlainTextEdit, QFileDialog,
    QMessageBox, QCheckBox, QScrollArea, QSplitter, QDialogButtonBox, QInputDialog)
from ..desktop_exam_data import (read_xlsx, analyse, local_student_advice, ExamStore, ExamError, digest)
from ..desktop_exam_mapping_profiles import ExamMappingProfileStore
from ..desktop_exam_ai import load_paper, model_payload, generate_advice, advice_text
from ..desktop_exam_report import number, percent, brief, html_report
from .exam_import_dialog import ExamImportDialog
from ..desktop_student_roster import (attach_roster, bind_exam_row, set_exam_status,
    empty_roster_exam, exam_status, STATUS_LABELS)
from .student_roster_dialog import StudentRosterDialog, ExamRosterBindingDialog, ExamAttendanceDialog


class ExamTable(QAbstractTableModel):
    def __init__(self, headers, rows, rates=None, parent=None):
        super().__init__(parent);self.headers=headers;self.rows=rows;self.rates=rates or {}
    def rowCount(self, parent=QModelIndex()):return 0 if parent.isValid() else len(self.rows)
    def columnCount(self, parent=QModelIndex()):return 0 if parent.isValid() else len(self.headers)
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role==Qt.ItemDataRole.DisplayRole:
            return self.headers[section] if orientation==Qt.Orientation.Horizontal else str(section+1)
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():return None
        if role in (Qt.ItemDataRole.DisplayRole,Qt.ItemDataRole.ToolTipRole):return str(self.rows[index.row()][index.column()])
        if role==Qt.ItemDataRole.BackgroundRole and (index.row(),index.column()) in self.rates:
            v=self.rates[(index.row(),index.column())]
            if v is None:return QColor('#eef0f2')
            # Sequential tint: labels contain exact score, color is supplementary.
            return QColor.fromRgbF(.94-.36*v,.96-.16*v,.94-.24*v)


class BarChart(QWidget):
    """Native painter uses current UI font. Never execute generated HTML or JavaScript."""
    def __init__(self,title,rows=(),ceiling=100,unit='',parent=None):
        super().__init__(parent);self.title=title;self.rows=list(rows);self.ceiling=ceiling;self.unit=unit
        self.setMinimumHeight(120);self.setMinimumWidth(240)
    def set_rows(self,rows,ceiling=100,unit=''):
        self.rows=list(rows);self.ceiling=ceiling;self.unit=unit
        self.setMinimumHeight(max(145,58+len(self.rows)*32));self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor('white'));p.setPen(QColor('#263c32'))
        font=p.font();font.setBold(True);p.setFont(font)
        p.drawText(QRectF(12,5,self.width()-24,28),Qt.AlignmentFlag.AlignVCenter,self.title)
        font.setBold(False);p.setFont(font)
        if not self.rows:
            p.drawText(QRectF(12,45,self.width()-24,50),Qt.TextFlag.TextWordWrap,'暂无可计算数据；请核对成绩与题目映射。');p.end();return
        left=min(180,max(88,int(self.width()*.27)));right=72;span=max(50,self.width()-left-right)
        fm=p.fontMetrics()
        for i,(label,value) in enumerate(self.rows):
            y=45+i*32
            p.setPen(QColor('#263c32'));p.drawText(QRectF(12,y,left-20,24),Qt.AlignmentFlag.AlignVCenter,
                fm.elidedText(str(label),Qt.TextElideMode.ElideRight,left-22))
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#edf3ef'));p.drawRoundedRect(QRectF(left,y,span,23),4,4)
            if value is not None:
                p.setBrush(QColor('#3c806c'));p.drawRoundedRect(QRectF(left,y,span*max(0,value)/max(1,self.ceiling),23),4,4)
            p.setPen(QColor('#263c32'));p.drawText(QRectF(left+span+8,y,right-8,24),Qt.AlignmentFlag.AlignVCenter,number(value,self.unit))
        p.end()


def scroll_widget(widget):
    s=QScrollArea();s.setWidgetResizable(True);s.setWidget(widget);return s


class _ExamDashboardBase(QDialog):
    preparation_requested=Signal(str)
    def __init__(self,facade,tasks,parent=None):
        super().__init__(parent);self.facade=facade;self.tasks=tasks
        self.store=ExamStore(facade.paths.state_root)
        self.exam=None;self.paper={'text':'','pages':[],'warnings':[]};self.result=None;self.result_scope=None
        self.followups=[];self.pending_handoff=None;self._saved_revision=None
        self.report=None;self._task=None;self._closed=False;self.dirty=False;self._rendering=False
        self.setWindowTitle('考试分析 · 成绩、试卷与讲评');self.resize(1320,860);self.setMinimumSize(720,570)
        root=QVBoxLayout(self);root.setContentsMargins(14,12,14,12);root.setSpacing(8)
        title=QLabel('考试数据分析');title.setObjectName('PageTitle');root.addWidget(title)
        sub=QLabel('本地统计 → 逐题核对 → API讲评草稿 → 教师安排复练。数字由本地计算，缺失不记零分。');sub.setWordWrap(True);root.addWidget(sub)
        self.workflow_hint=sub
        title.setToolTip(sub.text())
        toolbar=QWidget();bar=QGridLayout(toolbar);bar.setContentsMargins(0,0,0,0)
        self.import_button=QPushButton('导入成绩Excel');self.history_button=QPushButton('打开历史分析')
        self.template_button=QPushButton('保存Excel示例');self.save_button=QPushButton('保存分析');self.export_button=QPushButton('导出可视化报告')
        for i,b in enumerate((self.import_button,self.history_button,self.template_button,self.save_button,self.export_button)):bar.addWidget(b,i//3,i%3)
        root.addWidget(toolbar);self.toolbar=toolbar;self.toolbar_layout=bar
        self._toolbar_buttons=(self.import_button,self.history_button,self.template_button,self.save_button,self.export_button);self._toolbar_columns=3
        self.roster_button=QPushButton('本地班级名册');self.empty_roster_button=QPushButton('从名册建立待录入考试')
        bar.addWidget(self.roster_button,1,2);bar.addWidget(self.empty_roster_button,2,0)
        self._toolbar_buttons += (self.roster_button,self.empty_roster_button)
        roster_available=callable(getattr(facade,'student_roster_store',None))
        self.roster_button.setEnabled(roster_available);self.empty_roster_button.setEnabled(roster_available)
        self.roster_button.clicked.connect(self.open_local_roster);self.empty_roster_button.clicked.connect(self.new_empty_roster_exam)
        self.import_button.clicked.connect(self.import_excel);self.history_button.clicked.connect(self.open_history)
        self.template_button.clicked.connect(self.template);self.save_button.clicked.connect(self.save_current);self.export_button.clicked.connect(self.export)
        filters=QHBoxLayout();filters.addWidget(QLabel('统计范围'));self.classes=QComboBox();self.classes.addItem('全部导入班级',None)
        self.classes.setMaximumWidth(360);filters.addWidget(self.classes);self.summary_label=QLabel('尚未导入成绩');self.summary_label.setWordWrap(True);filters.addWidget(self.summary_label,1);root.addLayout(filters)
        self.classes.currentIndexChanged.connect(self.render)
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        overview=QWidget();ov=QVBoxLayout(overview);cards=QWidget();grid=QGridLayout(cards);grid.setContentsMargins(0,0,0,0)
        self.kpis=[]
        for i,label in enumerate(('有效总分 / 导入人数','平均分 / 满分','中位数 / 总体标准差','达标人数 / 有效人数','高分人数 / 有效人数','缺考 / 总分缺失')):
            box=QWidget();layout=QVBoxLayout(box);layout.addWidget(QLabel(label));value=QLabel('—');value.setObjectName('CardTitle');layout.addWidget(value)
            box.setStyleSheet('background:#edf3ef;border-radius:6px;');grid.addWidget(box,i//3,i%3);self.kpis.append(value)
        ov.addWidget(cards);self.distribution=BarChart('成绩分布 · 满分百分比分段（人数）');ov.addWidget(self.distribution)
        self.class_table=QTableView();self.class_table.setMinimumHeight(160);ov.addWidget(self.class_table)
        self.overview_note=QLabel('');self.overview_note.setWordWrap(True);ov.addWidget(self.overview_note)
        self.tabs.addTab(scroll_widget(overview),'总览')
        items=QWidget();it=QVBoxLayout(items);self.item_chart=BarChart('低得分率题目 · 最多20项')
        it.addWidget(self.item_chart);self.items_table=QTableView();self.items_table.setMinimumHeight(220);it.addWidget(self.items_table)
        self.knowledge_chart=BarChart('主知识点 · 按有效作答分值加权');it.addWidget(self.knowledge_chart)
        self.tabs.addTab(scroll_widget(items),'题目与知识点')
        students=QWidget();sv=QVBoxLayout(students);notice=QLabel('点击学生行查看复核建议；表格颜色仅辅助阅读。空白为缺失，0分是明确成绩；S编号仅本次导入有效。');notice.setWordWrap(True);sv.addWidget(notice)
        self.identity_tools=QWidget();identity_bar=QGridLayout(self.identity_tools);identity_bar.setContentsMargins(0,0,0,0)
        self.attach_roster_button=QPushButton('确认本次班级名册');self.bind_student_button=QPushButton('绑定成绩行与原档案');self.attendance_button=QPushButton('核对学生／整班状态')
        self.identity_history_button=QPushButton('查看身份与状态核对记录')
        for i,button in enumerate((self.attach_roster_button,self.bind_student_button,self.attendance_button,self.identity_history_button)):
            identity_bar.addWidget(button,i//2,i%2);button.setAutoDefault(False)
        self.attach_roster_button.clicked.connect(self.attach_local_roster);self.bind_student_button.clicked.connect(self.open_identity_binding);self.attendance_button.clicked.connect(self.open_attendance)
        self.identity_history_button.clicked.connect(self.show_identity_history)
        sv.addWidget(self.identity_tools)
        self.student_table=QTableView();self.student_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows);self.student_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.student_table.clicked.connect(self.select_student);sv.addWidget(self.student_table,1)
        self.student_note=QPlainTextEdit();self.student_note.setReadOnly(True);self.student_note.setMaximumHeight(170);sv.addWidget(self.student_note)
        self.tabs.addTab(students,'学生得分与行动')
        ai=QWidget();al=QVBoxLayout(ai)
        paperbar=QHBoxLayout();self.paper_button=QPushButton('添加 / 替换试卷');self.preview_button=QPushButton('查看试卷图');self.clear_paper_button=QPushButton('清除试卷')
        for b in (self.paper_button,self.preview_button,self.clear_paper_button):paperbar.addWidget(b)
        al.addLayout(paperbar);self.paper_label=QLabel('可先分析成绩，再添加DOCX / PDF / 图片。');self.paper_label.setWordWrap(True);al.addWidget(self.paper_label)
        self.paper_text=QPlainTextEdit();self.paper_text.setPlaceholderText('试卷正文与公共材料。可粘贴已核对文字；图片内的公式/结构不凭空转写。')
        self.notes=QPlainTextEdit();self.notes.setPlaceholderText('教学进度、最近已练内容、希望解决的问题。勿填不必要的个人信息。')
        inputs=QTabWidget();inputs.addTab(self.paper_text,'试卷文字（可核对）');inputs.addTab(self.notes,'教师说明');inputs.setMaximumHeight(175);al.addWidget(inputs)
        row=QHBoxLayout();self.model=QComboBox();self.model.setMinimumWidth(120);self.refresh_models_button=QPushButton('刷新模型')
        row.addWidget(QLabel('API模型'));row.addWidget(self.model,1);row.addWidget(self.refresh_models_button);al.addLayout(row)
        choices=QHBoxLayout();self.send_images=QCheckBox('发送已选试卷图（需视觉能力）');self.send_students=QCheckBox('附匿名逐人成绩（最多100名）')
        choices.addWidget(self.send_images);choices.addWidget(self.send_students);al.addLayout(choices)
        self.ai_button=QPushButton('确认资料并生成讲评建议');self.to_prep_button=QPushButton('带入备课材料')
        actions=QHBoxLayout();actions.addWidget(self.ai_button);actions.addWidget(self.to_prep_button);al.addLayout(actions)
        self.ai_result=QPlainTextEdit();self.ai_result.setReadOnly(True);al.addWidget(self.ai_result,1)
        self.ai_result.setMinimumHeight(170)
        self.ai_scroll=scroll_widget(ai);self.tabs.addTab(self.ai_scroll,'试卷与API建议')
        problems=QWidget();pl=QVBoxLayout(problems);self.problem_note=QLabel('');self.problem_note.setWordWrap(True);pl.addWidget(self.problem_note)
        self.problem_table=QTableView();pl.addWidget(self.problem_table,1);self.tabs.addTab(problems,'数据核对')
        self.status=QLabel('从“保存Excel示例”开始，或导入已有xlsx后手动选择列。');self.status.setWordWrap(True);root.addWidget(self.status)
        foot=QHBoxLayout();self.stop_button=QPushButton('停止等待');self.stop_button.setEnabled(False)
        self.close_button=QPushButton('返回工作台');foot.addWidget(self.stop_button);foot.addStretch();foot.addWidget(self.close_button);root.addLayout(foot)
        self.close_button.clicked.connect(self.close);self.stop_button.clicked.connect(self.cancel)
        self.paper_button.clicked.connect(self.add_paper);self.preview_button.clicked.connect(self.view_paper);self.clear_paper_button.clicked.connect(self.clear_paper)
        self.refresh_models_button.clicked.connect(self.refresh_models);self.ai_button.clicked.connect(self.request_ai);self.to_prep_button.clicked.connect(self.to_preparation)
        self.paper_text.textChanged.connect(self.input_changed);self.notes.textChanged.connect(self.input_changed)
        self.tasks.task_finished.connect(self.task_finished)
        if hasattr(self.tasks, 'task_cancelled_detail'):
            self.tasks.task_cancelled_detail.connect(self.task_cancelled)
        self._close_when_finished=False
        self.refresh_models();self.render()
        for button in self.findChildren(QPushButton):button.setAutoDefault(False)
        for button in (self.history_button,self.template_button,self.save_button,self.export_button,
                       self.preview_button,self.clear_paper_button,self.refresh_models_button,self.to_prep_button,self.close_button):
            button.setObjectName('QuietButton')

        from .exam_followup_panel import ExamFollowupPanel
        self.followup_panel=ExamFollowupPanel(self)
        self.tabs.addTab(self.followup_panel,'复练与复测')

    def open_saved(self, identity, task_id=None):
        bundle=self.store.load(identity)
        self.accept_exam(bundle['exam'],persist=False)
        self.paper=bundle.get('paper',{'text':'','pages':[],'warnings':[]})
        self._rendering=True
        self.paper_text.setPlainText(self.paper.get('text',''));self.notes.setPlainText(bundle.get('notes',''))
        self._rendering=False;self.result=bundle.get('advice');self.result_scope=bundle.get('advice_scope')
        self.followups=bundle.get('followups',[]);self._saved_revision=digest(bundle);self.dirty=False
        target=self.classes.findData(self.result_scope)
        if target>=0:self.classes.setCurrentIndex(target)
        self.render();self.followup_panel.refresh(task_id)
        if task_id:self.tabs.setCurrentWidget(self.followup_panel)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        # Keep the content readable on short displays; the title tooltip retains
        # the workflow explanation without a duplicate line above every tab.
        if hasattr(self,'workflow_hint'):
            self.workflow_hint.setVisible(event.size().height()>=760)
        columns=5 if event.size().width()>=1050 else 3
        if hasattr(self,'_toolbar_buttons') and columns!=self._toolbar_columns:
            self._toolbar_columns=columns
            for button in self._toolbar_buttons:self.toolbar_layout.removeWidget(button)
            for i,button in enumerate(self._toolbar_buttons):self.toolbar_layout.addWidget(button,i//columns,i%columns)

    def set_table(self,view,headers,rows,rates=None):
        previous=view.model();view.setModel(ExamTable(headers,rows,rates,view))
        if previous:previous.deleteLater()
        view.resizeColumnsToContents()
        for i in range(len(headers)):view.setColumnWidth(i,min(340,max(72,view.columnWidth(i))))

    def _busy(self,yes):
        for w in (self.toolbar,self.classes,self.paper_button,self.clear_paper_button,self.paper_text,self.notes,self.model,
                  self.refresh_models_button,self.send_images,self.send_students,self.ai_button,self.to_prep_button):w.setEnabled(not yes)
        self.stop_button.setEnabled(yes and getattr(self,'_can_cancel',True))
        if hasattr(self,'followup_panel'):self.followup_panel.setEnabled(not yes)

    def run(self,label,fn,callback,*,cancellable=True,external_request=False):
        if self._task:return
        self._can_cancel=cancellable
        self._external_request=external_request
        self._busy(True);self.status.setText(label+'…')
        def success(value):
            if not self._closed:
                try:callback(value)
                except Exception as e:self.status.setText(getattr(e,'message_zh','操作未完成，请核对文件与数据。'))
        def failure(message):
            if not self._closed:self.status.setText(message)
        def operation(report,cancelled):
            if cancellable:return fn(report,cancelled)
            from ..reader_cancellation import publication_guard
            with publication_guard():return fn(report,cancelled)
        self._task=self.tasks.submit_progress(label,operation,on_success=success,on_failure=failure)

    def task_finished(self,identity):
        if identity==self._task:
            self._task=None
            if not self._closed:self._busy(False)
            if self._close_when_finished:
                self._close_when_finished=False
                self.close()

    def task_cancelled(self,identity,_label,message):
        if identity==self._task and not self._closed:
            self.status.setText(message+(' 已发给服务商的调用仍可能计费。' if self._external_request else ''))

    def cancel(self):
        if self._task:
            if not self._can_cancel:
                if self._close_when_finished:self.status.setText('正在保存确认，完成后返回。')
                return
            if self.tasks.cancel(self._task) is False:return
            self.stop_button.setEnabled(False)
            self.status.setText('正在停止本次任务；已有题集与输出保留，等待清理状态…'+
                (' 已发给服务商的调用仍可能计费。' if self._external_request else ''))

    def import_excel(self):
        path,_=QFileDialog.getOpenFileName(self,'选择成绩Excel','','Excel工作簿 (*.xlsx)')
        if not path:return
        if not self.flush_or_discard():return
        self.run('读取成绩Excel',lambda report,cancelled:read_xlsx(path),self.map_excel)

    def map_excel(self,book):
        dialog=ExamImportDialog(book,self,mapping_profiles=ExamMappingProfileStore(self.facade.paths.state_root))
        if dialog.exec()==QDialog.DialogCode.Accepted:self.accept_exam(dialog.exam)
        dialog.deleteLater()

    def accept_exam(self,exam,*,persist=True):
        self.followups=[];self.pending_handoff=None;self._saved_revision=None
        if hasattr(self,'followup_panel'):
            self.followup_panel.preview=None;self.followup_panel.approved=False
        self.exam=exam;self.result=None;self.result_scope=None;self.dirty=True
        self._rendering=True;self.paper={'text':'','pages':[],'warnings':[]};self.paper_text.clear();self.notes.clear();self.paper_label.setText('尚未添加试卷；题号与知识点来自成绩导入时的映射。');self._rendering=False
        self.classes.blockSignals(True);self.classes.clear();self.classes.addItem('全部导入班级',None)
        for c in sorted({s['class'] for s in exam['students']}):self.classes.addItem(c,c)
        self.classes.blockSignals(False);self.render()
        if persist:self.save_current()
        self.status.setText(f"已导入{len(exam['students'])}条学生记录，{len(exam['issues'])}项需核对。可先用本地图表，后续API只生成讲评草稿。")

    def template(self):
        from ..desktop_exam_template import template_bytes
        path,_=QFileDialog.getSaveFileName(self,'保存合成成绩示例','成绩导入示例.xlsx','Excel (*.xlsx)')
        if path:
            try:Path(path).write_bytes(template_bytes());self.status.setText('示例已保存，含成绩、题目映射和使用说明；不是实际学生成绩。')
            except OSError:self.status.setText('文件未能保存，请选择可写位置。')

    def render(self):
        ready=self.exam is not None
        if hasattr(self,'identity_tools'):
            roster_available=callable(getattr(self.facade,'student_roster_store',None))
            self.identity_tools.setEnabled(ready and not self._task and roster_available)
            self.roster_button.setEnabled(roster_available and not self._task);self.empty_roster_button.setEnabled(roster_available and not self._task)
        for w in (self.save_button,self.export_button,self.paper_button,self.ai_button,self.to_prep_button):w.setEnabled(ready and not self._task)
        if not ready:
            self.ai_result.setPlainText(advice_text(None));return
        self.report=analyse(self.exam,self.classes.currentData());s=self.report['overall'];n=s['n']
        self.summary_label.setText(f"{self.exam['title']} · {self.report['scope']}")
        self.summary_label.setToolTip(f"应到{s['expected']}，实到{s['present']}，缺考{s['absent']}，免考{s['exempt']}，未录入{s['not_entered']}；未绑定导入行{s['unbound']}。")
        values=[f"{n} / {s['enrolled']}",f"{number(s['mean'])} / {number(self.exam['maximum'])}",f"{number(s['median'])} / {number(s['sd'])}",
                f"{s['pass_n']} / {n} · {percent(s['pass_rate'])}",f"{s['high_n']} / {n} · {percent(s['high_rate'])}",f"{s['absent']} / {s['unavailable']}"]
        for label,v in zip(self.kpis,values):label.setText(v)
        self.distribution.set_rows([(r['label'],r['n']) for r in self.report['distribution']],max((r['n'] for r in self.report['distribution']),default=1),'人')
        self.set_table(self.class_table,['班级','有效总分人数','均分','中位数','最低分','最高分'],[(c['class'],c['n'],number(c['mean']),number(c['median']),number(c['min']),number(c['max'])) for c in self.report['classes']])
        self.overview_note.setText(f"应到{s['expected']}人，实到{s['present']}人，缺考{s['absent']}人，免考{s['exempt']}人，未录入{s['not_entered']}人；未绑定导入行{s['unbound']}条。\n达标线{number(self.exam['pass_score'])}，高分线{number(self.exam['excellent_score'])}，均为导入时自定阈值。分布区间左闭右开，最后一区间含满分。\n统计排除缺考、免考、未录入及无有效总分者；附名册后未绑定导入行保留原分数但须先确认身份。单次数据不代表已进步。")
        qs=self.report['items'];ordered=sorted(qs,key=lambda q:(q['rate'] is None,q['rate'] or 0))
        self.item_chart.set_rows([(q['question'],None if q['rate'] is None else q['rate']*100) for q in ordered[:20]],100,'%')
        self.set_table(self.items_table,['题号','满分','有效人数','缺失','均分','得分率','主知识点','章节'],[(q['question'],number(q['max_score']),q['n'],q['missing'],number(q['mean']),percent(q['rate']),q['knowledge'] or '待映射',q['chapter'] or '待映射') for q in qs])
        self.knowledge_chart.set_rows([(k['label'],None if k['rate'] is None else k['rate']*100) for k in self.report['knowledge']],100,'%')
        rows=[];rates={}
        for i,st in enumerate(self.report['students']):
            rows.append([st.get('local_alias',st['local_label']),st['id'],st['class'],number(st['total']),
                '未绑定名册' if st.get('excluded_unbound') else STATUS_LABELS[exam_status(st)] + ('／总分待核对' if exam_status(st)=='present' and st['total'] is None else '')]+[number(st['scores'].get(q['question'])) for q in qs])
            for j,q in enumerate(qs,5):
                val=st['scores'].get(q['question']);rates[(i,j)]=val/q['max_score'] if val is not None else None
        self.set_table(self.student_table,['学生（本机）','API代号','班级','总分','状态']+[q['question'] for q in qs],rows,rates)
        self.student_note.setPlainText('点击任一学生行：查看需优先回看的题目、失分与练习安排。没有原作答时不自动判定错因。')
        self.problem_note.setText('\n'.join(self.report['warnings']))
        self.set_table(self.problem_table,['Excel行','字段','需核对内容'],[(i['row'],i['field'],i['detail']) for i in self.report['issues']])
        self.ai_result.setPlainText(advice_text(self.current_result()))
        if hasattr(self,'followup_panel'):self.followup_panel.refresh()

    def select_student(self,index):
        if self.report and 0<=index.row()<len(self.report['students']):
            s=self.report['students'][index.row()]
            binding=s.get('identity_binding') or {}
            detail=(f"\n本地成员：{binding['member_id']}；原档案：{binding.get('profile_student_id') or '未绑定'}；原作答：{binding.get('submission_id') or '未关联'}" if binding else '\n尚未明确绑定本地名册与原档案。')
            original=next(r for r in self.exam['students'] if r['id']==s['id'])
            self.student_note.setPlainText(s.get('local_alias',s['local_label'])+' · '+s['id']+detail+
                f"\n原导入标识：{original['local_label']}；原记录总分：{number(original['total'])}\n"+local_student_advice(s,self.report))

    def open_local_roster(self):
        if self._task:return
        try:
            dialog=StudentRosterDialog(self.facade,self);dialog.exec();dialog.deleteLater()
            self.status.setText('本地名册仅用于教师识别；已保存考试快照与旧绑定不会自动替换。')
        except Exception as error:self.status.setText(getattr(error,'message_zh','本地名册暂时无法读取。'))

    def _choose_roster(self):
        rosters=self.facade.student_roster_store().entries()
        if not rosters:raise ExamError('请先在“本地班级名册”创建成员、明确绑定匿名档案并保存。')
        choices=[f"{r['label']} · {len(r['members'])}人 · {r['id'][:8]}" for r in rosters]
        value,ok=QInputDialog.getItem(self,'明确选择本地名册','本次采用的名册（不按班级名匹配）',choices,0,False)
        return rosters[choices.index(value)] if ok else None

    def attach_local_roster(self):
        if not self.exam or self._task:return
        try:
            expected=digest(self.bundle());roster=self._choose_roster()
            if roster is None:return
            choices=sorted({s['class'] for s in self.exam['students']})
            exam_class,ok=QInputDialog.getItem(self,'确认名册对应范围','明确选择本次Excel班级',choices,0,False)
            if not ok:return
            reason,ok=QInputDialog.getText(self,'确认本次名册','核对名册版本与本次考试班级的依据')
            if not ok:return
            proposed=attach_roster(self.exam,roster,exam_class=exam_class,reason=reason)
            self.commit_identity_change(proposed,expected=expected)
        except Exception as error:self.status.setText(getattr(error,'message_zh','名册未附入考试，请核对当前输入。'))

    def new_empty_roster_exam(self):
        if self._task or not self.flush_or_discard():return
        try:
            roster=self._choose_roster()
            if roster is None:return
            dialog=QDialog(self);dialog.setWindowTitle('建立整班待录入考试');box=QVBoxLayout(dialog)
            from PySide6.QtWidgets import QFormLayout,QLineEdit,QDoubleSpinBox
            form=QFormLayout();title=QLineEdit();maximum=QDoubleSpinBox();passed=QDoubleSpinBox();high=QDoubleSpinBox();reason=QLineEdit()
            for widget,value in ((maximum,100),(passed,60),(high,85)):widget.setRange(0,10000);widget.setValue(value)
            for label,widget in [('考试名称',title),('试卷满分',maximum),('自定达标线',passed),('自定高分线',high),('确认名册依据',reason)]:form.addRow(label,widget)
            box.addLayout(form);message=QLabel('全班初始为未录入；尚无Excel来源或成绩，不记零分。');message.setWordWrap(True);box.addWidget(message)
            buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);box.addWidget(buttons);buttons.rejected.connect(dialog.reject)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText('建立并保存本次考试');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
            def create():
                try:
                    exam=empty_roster_exam(roster,title=title.text(),maximum=maximum.value(),pass_score=passed.value(),excellent_score=high.value(),reason=reason.text())
                    bundle={'exam':exam,'paper':{'text':'','pages':[],'warnings':[]},'notes':'','advice':None,'advice_scope':None,'followups':[],'advice_history':[]}
                    self.store.save(bundle,expected_revision=None);self.open_saved(exam['id']);dialog.accept()
                except ExamError as error:message.setText(error.message_zh)
            buttons.accepted.connect(create);self._empty_roster_dialog=dialog;dialog.open()
        except Exception as error:self.status.setText(getattr(error,'message_zh','待录入考试未能创建。'))

    def open_identity_binding(self):
        if not self.exam or self._task:return
        try:
            if not self.exam.get('roster_snapshots'):raise ExamError('请先明确确认本次班级名册。')
            self.check_disk_current();self._identity_dialog=ExamRosterBindingDialog(self);self._identity_dialog.open()
        except ExamError as error:self.status.setText(error.message_zh)

    def open_attendance(self):
        if not self.exam or self._task:return
        try:self.check_disk_current();self._attendance_dialog=ExamAttendanceDialog(self);self._attendance_dialog.open()
        except ExamError as error:self.status.setText(error.message_zh)

    def show_identity_history(self):
        if not self.exam:return
        dialog=QDialog(self);dialog.setWindowTitle('本机身份与状态核对记录');dialog.resize(760,560)
        box=QVBoxLayout(dialog);notice=QLabel('包含本机身份信息，仅供教师追溯；不是匿名导出。原Excel、原分数与历史复测不改写。');notice.setWordWrap(True);box.addWidget(notice)
        view=QPlainTextEdit();view.setReadOnly(True)
        view.setPlainText(json.dumps({'roster_snapshots':self.exam.get('roster_snapshots',[]),'identity_history':self.exam.get('identity_history',[])},ensure_ascii=False,indent=2))
        box.addWidget(view,1);close=QPushButton('返回');close.clicked.connect(dialog.accept);box.addWidget(close)
        self._identity_history_dialog=dialog;dialog.open()

    def commit_identity_change(self, proposed, *, expected):
        if not self.exam or self._task or self._closed:raise ExamError('当前窗口不能修改学生身份或状态。')
        if digest(self.bundle())!=expected:raise ExamError('确认期间考试数据已变化，请重新读取后核对；输入未写入。')
        self.check_disk_current();bundle=self.bundle();bundle['exam']=proposed
        self.store.save(bundle,expected_revision=self._saved_revision)
        self.exam=proposed;self._saved_revision=digest(bundle);self.dirty=False
        if hasattr(self,'followup_panel'):self.followup_panel.preview=None;self.followup_panel.approved=False
        self.render();self.status.setText('身份／状态核对已保存，原分数与历史复测保留；旧建议和受影响复练须按新依据核对。')

    def apply_identity_binding(self, student_id, member_id, submission_id, reason, *, expected):
        proposed=bind_exam_row(self.exam,student_id,member_id,store=self.facade.student_roster_store(),submission_id=submission_id,reason=reason)
        self.commit_identity_change(proposed,expected=expected)

    def apply_attendance_status(self, target, status, reason, *, expected):
        rows=[target] if target is not None else [r['id'] for r in self.report['students'] if r.get('identity_binding')]
        if not rows:raise ExamError('当前范围没有已确认的名册成员，请先附名册并核对。')
        proposed=self.exam
        for identity in rows:proposed=set_exam_status(proposed,identity,status,reason=reason)
        self.commit_identity_change(proposed,expected=expected)

    def input_changed(self):
        if self._rendering:return
        self.paper['text']=self.paper_text.toPlainText();self.result=None;self.dirty=True
        self.ai_result.setPlainText(advice_text(None))

    def current_result(self):
        return self.result if self.result_scope==self.classes.currentData() else None

    def add_paper(self):
        path,_=QFileDialog.getOpenFileName(self,'选择完整试卷或相关完整主题','','试卷 (*.docx *.pdf *.png *.jpg *.jpeg *.bmp)')
        if path:self.run('读取试卷',lambda report,cancelled:load_paper(path),self.accept_paper)

    def accept_paper(self,paper):
        self.paper=paper;self._rendering=True;self.paper_text.setPlainText(paper['text']);self._rendering=False
        self.paper_label.setText(paper['name']+f" · {len(paper['pages'])}幅图；文字/图片可先查看。")
        self.result=None;self.dirty=True;self.ai_result.setPlainText(advice_text(None));self.status.setText('\n'.join(paper['warnings']) or '试卷文字已读取，原文件不改写。')

    def clear_paper(self):
        self.paper={'text':'','pages':[],'warnings':[]};self.paper_text.clear();self.paper_label.setText('试卷已清除，成绩保留。');self.dirty=True

    def view_paper(self):
        pages=self.paper.get('pages',[])
        if not pages:self.status.setText('没有可查看的试卷图片；DOCX正文可在文字框核对。');return
        d=QDialog(self);d.setWindowTitle('试卷图片 · 发送前核对隐私及公式');d.resize(950,760);layout=QVBoxLayout(d)
        picker=QComboBox();layout.addWidget(picker);label=QLabel();label.setAlignment(Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignHCenter)
        sc=QScrollArea();sc.setWidget(label);layout.addWidget(sc,1)
        for p in pages:picker.addItem(p['label'])
        def show(i):
            pix=QPixmap();pix.loadFromData(base64.b64decode(pages[i]['data']));label.setPixmap(pix);label.resize(pix.size())
        picker.currentIndexChanged.connect(show);show(0)
        close=QPushButton('返回');close.clicked.connect(d.accept);layout.addWidget(close);d.exec();d.deleteLater()

    def refresh_models(self):
        self.model.clear()
        try:
            for p in self.facade.list_provider_profiles():
                if p.key_saved and 'text' in p.capabilities:self.model.addItem(p.provider_name+' / '+p.model_id,p)
        except Exception:pass
        if not self.model.count():self.model.addItem('先在工作台设置中配置API',None)

    def request_ai(self):
        if not self.exam or self._task:return
        try:
            self.check_disk_current(); self.render()
        except ExamError as error:self.status.setText(error.message_zh);return
        request_stamp=self.advice_input_stamp(); expected_disk=self._saved_revision
        profile=self.model.currentData()
        if profile is None:self.status.setText('请先在工作台“设置”保存API连接，再点击刷新模型。');return
        try:
            payload=model_payload(self.report,self.paper,self.notes.toPlainText(),self.send_students.isChecked())
            pages=deepcopy(self.paper.get('pages',[])) if self.send_images.isChecked() else []
            if self.send_images.isChecked() and not pages:raise ExamError('没有试卷图片可发送，请先添加试卷，或取消发图。')
        except ExamError as e:self.status.setText(e.message_zh);return
        d=QDialog(self);d.setWindowTitle('确认发送给API的资料');d.resize(800,670);box=QVBoxLayout(d)
        msg=QLabel(f"接收方：{profile.provider_name} / {profile.model_id}\n本次发送匿名成绩汇总、教师已确认的题目映射/试卷文字/说明，及{len(pages)}幅试卷图。"
                   f"\n匿名逐人成绩：{len(payload['students'])}名；不发送Excel文件、姓名、班级名称或学号列。"
                   '\n自由文字和图片内的个人信息不会自动擦除，请先核对。API可能收费；结果仅为讲评草稿，不是成绩判定。')
        msg.setWordWrap(True);box.addWidget(msg)
        preview=QPlainTextEdit();preview.setReadOnly(True);preview.setPlainText(json.dumps(payload,ensure_ascii=False,indent=2)+'\n试卷图：\n'+'\n'.join(p['label'] for p in pages));box.addWidget(preview,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认发送并生成')
        buttons.accepted.connect(d.accept);buttons.rejected.connect(d.reject);box.addWidget(buttons)
        accepted=d.exec()==QDialog.DialogCode.Accepted;d.deleteLater()
        if not accepted:return
        scope=self.classes.currentData()
        if request_stamp!=self.advice_input_stamp():
            self.status.setText('确认期间资料已变化，请重新核对发送内容。');return
        def done(result):
            self.accept_advice_result(result,scope,request_stamp,expected_disk)
        self.run('API生成讲评草稿',lambda report,cancelled:generate_advice(self.facade,profile.profile_id,profile.revision,payload,pages,confirmed=True,cancelled=cancelled,transport=getattr(self.facade,"_exam_transport",None)),done,external_request=True)

    def bundle(self):
        return {'exam':self.exam,'paper':self.paper,'notes':self.notes.toPlainText(),'advice':self.result,'advice_scope':self.result_scope,'followups':deepcopy(self.followups)}

    def save_current(self):
        if not self.exam:return True
        try:
            bundle=self.bundle();self.store.save(bundle,expected_revision=self._saved_revision);self._saved_revision=digest(bundle);self.dirty=False;self.status.setText('已保存本机分析快照；原Excel/试卷不变，未调用API。');return True
        except ExamError as e:self.status.setText(e.message_zh);return False

    def open_history(self):
        if not self.flush_or_discard():return
        entries=self.store.entries()
        if not entries:self.status.setText('尚无已保存的考试分析。');return
        choices=[f'{i+1}. {title}' for i,(_,title) in enumerate(entries)]
        value,ok=QInputDialog.getItem(self,'打开历史考试分析','已保存记录',choices,0,False)
        if not ok:return
        try:
            b=self.store.load(entries[choices.index(value)][0]);self.accept_exam(b['exam'],persist=False)
            self.paper=b.get('paper',{'text':'','pages':[],'warnings':[]});self._rendering=True
            self.paper_text.setPlainText(self.paper.get('text',''));self.notes.setPlainText(b.get('notes',''));self._rendering=False
            self.result=b.get('advice');self.result_scope=b.get('advice_scope');self.followups=b.get('followups',[]);self._saved_revision=digest(b);self.dirty=False
            target=self.classes.findData(self.result_scope)
            if target>=0:self.classes.setCurrentIndex(target)
            self.paper_label.setText(self.paper.get('name','未附试卷'));self.render()
        except ExamError as e:self.status.setText(e.message_zh)

    def export(self):
        if not self.report:return
        path,_=QFileDialog.getSaveFileName(self,'导出离线可视化报告','考试分析.html','HTML报告 (*.html)')
        if not path:return
        answer=QMessageBox.question(self,'学生标识','导出中保留本机学生和班级标识吗？选择“否”则使用临时代号。自由文字仍需自行核对。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.No)
        if answer==QMessageBox.StandardButton.Cancel:return
        try:
            from ..desktop_exam_followup import followup_html
            include_names=answer==QMessageBox.StandardButton.Yes
            html=html_report(self.report,self.current_result(),include_names=include_names)
            if self.followups:
                # Limit task observations to the currently displayed class.
                allowed={s['id'] for s in self.report['students']}
                tasks=deepcopy(self.followups)
                for task in tasks:
                    task['targets']=[t for t in task['targets'] if t['exam_student_id'] in allowed]
                    task['attempts']=[a for a in task['attempts'] if a['student_id'] in allowed]
                tasks=[t for t in tasks if t['targets']]
                section=followup_html(tasks,include_names=include_names)
                html=html.replace('</body>',section+'</body>') if '</body>' in html else html+section
            Path(path).write_text(html,encoding='utf-8')
            self.status.setText('已导出离线图表及当前范围的复测记录；自由文字请核对，不是题库或学生原件备份。')
        except OSError:self.status.setText('导出未成功，请选择可写目录。')

    def to_preparation(self):
        if self.report:self.preparation_requested.emit(brief(self.report)+'\n\n'+advice_text(self.current_result()));self.status.setText('已请求追加讲评材料；未自动生成教案/PPT，请到备课页核对并保存。')

    def flush_or_discard(self):
        if not self.dirty or not self.exam:return True
        answer=QMessageBox.question(self,'保存当前考试分析','保存当前试卷说明与分析结果后再继续？',QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
        if answer==QMessageBox.StandardButton.Cancel:return False
        return self.save_current() if answer==QMessageBox.StandardButton.Save else True

    def reject(self):
        self.close()

    def closeEvent(self,event):
        if self._task:
            self._close_when_finished=True;self.cancel();event.ignore();return
        if not self.flush_or_discard():event.ignore();return
        self._closed=True;event.accept()


from .exam_revision_ui import ExamRevisionMixin


class ExamDashboard(ExamRevisionMixin, _ExamDashboardBase):
    """Existing exam dashboard with explicit local evidence-version support."""

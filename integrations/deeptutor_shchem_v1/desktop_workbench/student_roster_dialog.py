"""Native local roster editor and explicit exam identity/attendance dialogs."""
from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QDialogButtonBox, QInputDialog, QMessageBox)
from ..desktop_exam_data import ExamError, digest
from ..desktop_student_roster import new_roster, new_member, STATUS_LABELS, exam_status


def field(value, name, default=None):
    return value.get(name,default) if isinstance(value,dict) else getattr(value,name,default)


class StudentRosterDialog(QDialog):
    def __init__(self, facade, parent=None):
        super().__init__(parent); self.facade=facade; self.store=facade.student_roster_store()
        self.draft=None; self.dirty=False; self.saved=False
        self.setWindowTitle('本地班级名册 · 稳定身份与别名');self.resize(760,600)
        box=QVBoxLayout(self)
        note=QLabel('班级与学生别名只保存在本机私有学生目录。每个成员有独立稳定ID；同名可以并存，不按姓名合并。名册不进入API或备课ZIP。');note.setWordWrap(True);box.addWidget(note)
        bar=QHBoxLayout();self.rosters=QComboBox();bar.addWidget(self.rosters,1)
        self.new_button=QPushButton('新建班级名册');bar.addWidget(self.new_button);box.addLayout(bar)
        self.class_label=QLineEdit();self.class_label.setAccessibleName('班级本地别名');box.addWidget(self.class_label)
        self.members=QTableWidget(0,3);self.members.setHorizontalHeaderLabels(['学生本地别名','稳定成员ID','关联匿名档案'])
        self.members.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);self.members.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.members.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);box.addWidget(self.members,1)
        form=QFormLayout();self.alias=QLineEdit();self.alias.setAccessibleName('成员本地别名')
        self.profile=QComboBox();self.profile.addItem('不绑定匿名档案',None)
        for profile in facade.student_profiles():
            sid=field(profile,'student_id');self.profile.addItem(f"{field(profile,'label_zh','匿名学生')} · {sid[:8]}",sid)
        form.addRow('成员别名',self.alias);form.addRow('明确选择匿名档案',self.profile);box.addLayout(form)
        actions=QHBoxLayout();self.add_button=QPushButton('新增成员（生成新ID）');self.edit_button=QPushButton('应用成员别名／档案选择')
        actions.addWidget(self.add_button);actions.addWidget(self.edit_button);box.addLayout(actions)
        self.status=QLabel();self.status.setWordWrap(True);box.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Close)
        self.save_button=buttons.button(QDialogButtonBox.StandardButton.Save);self.save_button.setText('保存本地名册')
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('返回')
        buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);box.addWidget(buttons)
        self.new_button.clicked.connect(self.new_class);self.add_button.clicked.connect(self.add_member);self.edit_button.clicked.connect(self.edit_member)
        self.rosters.currentIndexChanged.connect(self.choose_class);self.members.itemSelectionChanged.connect(self.choose_member)
        self.class_label.textEdited.connect(self.class_edited);self.refresh()

    def refresh(self, identity=None):
        self.rosters.blockSignals(True);self.rosters.clear()
        for roster in self.store.entries():self.rosters.addItem(roster['label']+' · '+roster['id'][:8],roster['id'])
        index=self.rosters.findData(identity)
        if index>=0:self.rosters.setCurrentIndex(index)
        self.rosters.blockSignals(False);self.choose_class()

    def choose_class(self):
        identity=self.rosters.currentData()
        if self.dirty and QMessageBox.question(self,'未保存名册','放弃当前未保存名册输入并切换？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:
            target=self.rosters.findData(self.draft['id']) if self.draft else -1
            self.rosters.blockSignals(True);self.rosters.setCurrentIndex(target);self.rosters.blockSignals(False);return
        self.draft=self.store.load(identity) if identity else None;self.dirty=False;self.render()

    def new_class(self):
        if self.dirty:self.status.setText('请先保存当前名册，再新建班级。');return
        label,ok=QInputDialog.getText(self,'新建本地班级','班级本地别名')
        if not ok:return
        try:self.draft=new_roster(label);self.dirty=True;self.render()
        except ExamError as error:self.status.setText(error.message_zh)

    def class_edited(self, text):
        if self.draft:self.draft['label']=text;self.dirty=True

    def render(self):
        self.class_label.setText(self.draft['label'] if self.draft else '')
        self.members.setRowCount(0)
        for member in self.draft['members'] if self.draft else []:
            i=self.members.rowCount();self.members.insertRow(i)
            for j,text in enumerate((member['local_alias'],member['id'],member['profile_student_id'] or '未绑定')):
                item=QTableWidgetItem(text);item.setData(Qt.ItemDataRole.UserRole,member['id']);self.members.setItem(i,j,item)
        self.members.resizeColumnsToContents()
        for button in (self.add_button,self.edit_button,self.save_button):button.setEnabled(self.draft is not None)
        self.alias.clear();self.profile.setCurrentIndex(0)

    def choose_member(self):
        index=self.members.currentRow()
        if self.draft and 0<=index<len(self.draft['members']):
            member=self.draft['members'][index];self.alias.setText(member['local_alias'])
            self.profile.setCurrentIndex(max(0,self.profile.findData(member['profile_student_id'])))

    def add_member(self):
        if not self.draft:return
        try:
            self.draft['members'].append(new_member(self.alias.text()));self.dirty=True;self.render()
            self.members.selectRow(len(self.draft['members'])-1)
            self.status.setText('已生成独立成员ID；请明确绑定档案并保存。同名不会合并。')
        except ExamError as error:self.status.setText(error.message_zh)

    def edit_member(self):
        index=self.members.currentRow()
        if not self.draft or not 0<=index<len(self.draft['members']):self.status.setText('请先选择名册成员。');return
        try:
            candidate=new_member(self.alias.text())
            self.store.check_profile(self.profile.currentData())
            member=self.draft['members'][index];member['local_alias']=candidate['local_alias'];member['profile_student_id']=self.profile.currentData()
            self.dirty=True;self.render();self.members.selectRow(index);self.status.setText('已应用本地输入，稳定成员ID保留；请保存名册。')
        except ExamError as error:self.status.setText(error.message_zh)

    def save(self):
        if not self.draft:return
        try:
            candidate=deepcopy(self.draft);candidate['label']=self.class_label.text()
            saved=self.store.save(candidate,expected_revision=self.draft['revision']);self.dirty=False;self.saved=True
            self.refresh(saved['id']);self.status.setText('本地名册已保存；匿名档案与考试历史未改写。')
        except ExamError as error:self.status.setText(error.message_zh)

    def reject(self):
        if self.dirty and QMessageBox.question(self,'未保存名册','放弃本次未保存输入并关闭？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        super().reject()


class ExamRosterBindingDialog(QDialog):
    def __init__(self, dashboard):
        super().__init__(dashboard);self.d=dashboard;self.expected=digest(dashboard.bundle())
        self.setWindowTitle('显式绑定成绩行、名册与原作答');self.resize(680,460)
        box=QVBoxLayout(self);notice=QLabel('逐项选择，不按同名、学号或S编号自动关联。S代号仅属于这场考试；改绑会使旧复练身份依据待核对。');notice.setWordWrap(True);box.addWidget(notice)
        form=QFormLayout();self.row=QComboBox();self.member=QComboBox();self.submission=QComboBox();self.reason=QPlainTextEdit();self.reason.setMaximumHeight(90)
        self.reason.setPlaceholderText('教师核对依据，例如按原作答封面与本地名册确认；不要填不必要的个人信息。')
        for row in dashboard.report['students']:self.row.addItem(f"{row['class']} · {row['local_label']} ({row['id']}) · Excel行{row['excel_row'] or '无'}",row['id'])
        selected=dashboard.student_table.currentIndex()
        if selected.isValid():self.row.setCurrentIndex(selected.row())
        for label,widget in [('本场成绩行',self.row),('明确名册成员',self.member),('可选：档案中的原作答',self.submission),('本次核对依据',self.reason)]:form.addRow(label,widget)
        box.addLayout(form);self.status=QLabel();self.status.setWordWrap(True);box.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);box.addWidget(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('确认绑定并保存');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.row.currentIndexChanged.connect(self.row_changed);self.member.currentIndexChanged.connect(self.member_changed);self.row_changed()

    def row_changed(self):
        row=next((r for r in self.d.exam['students'] if r['id']==self.row.currentData()),None)
        self.member.blockSignals(True);self.member.clear();self.member.addItem('请明确选择成员',None)
        if row:
            for snapshot in self.d.exam.get('roster_snapshots',[]):
                if snapshot['exam_class']!=row['class']:continue
                for member in snapshot['roster']['members']:
                    self.member.addItem(member['local_alias']+' · '+member['id'][:8],member)
            previous=(row.get('identity_binding') or {}).get('member_id')
            for i in range(1,self.member.count()):
                if self.member.itemData(i)['id']==previous:self.member.setCurrentIndex(i);break
        self.member.blockSignals(False);self.member_changed()

    def member_changed(self):
        self.submission.clear();self.submission.addItem('不关联原作答',None)
        member=self.member.currentData()
        if not member or not member['profile_student_id']:return
        try:
            for value in self.d.facade.student_submissions(student_id=member['profile_student_id'],limit=100):
                sid=field(value,'submission_id');self.submission.addItem(f"{field(value,'created_at','')} · {sid[:8]}",sid)
            row=next((r for r in self.d.exam['students'] if r['id']==self.row.currentData()),{})
            binding=row.get('identity_binding') or {}
            if binding.get('member_id')==member['id']:
                self.submission.setCurrentIndex(max(0,self.submission.findData(binding.get('submission_id'))))
        except Exception:self.status.setText('该档案作答暂时无法读取；不会从其他学生猜测原作答。')

    def save(self):
        try:
            member=self.member.currentData()
            if not member:raise ExamError('请明确选择名册成员。')
            self.d.apply_identity_binding(self.row.currentData(),member['id'],self.submission.currentData(),self.reason.toPlainText(),expected=self.expected)
            self.accept()
        except ExamError as error:self.status.setText(error.message_zh)


class ExamAttendanceDialog(QDialog):
    def __init__(self,dashboard):
        super().__init__(dashboard);self.d=dashboard;self.expected=digest(dashboard.bundle());self.setWindowTitle('核对学生或整班应考状态')
        box=QVBoxLayout(self);note=QLabel('缺考、免考与未录入分别统计；原始分数保留在本机记录中，只有实到状态的有效分数进入统计。');note.setWordWrap(True);box.addWidget(note)
        form=QFormLayout();self.target=QComboBox();self.target.addItem('请明确选择范围','__choose__')
        self.target.addItem('当前范围的整班名册成员',None)
        for row in dashboard.report['students']:self.target.addItem(f"{row.get('local_alias',row['local_label'])} · {row['id']} · {STATUS_LABELS[exam_status(row)]}",row['id'])
        self.status_choice=QComboBox()
        for key,label in STATUS_LABELS.items():self.status_choice.addItem(label,key)
        self.reason=QPlainTextEdit();self.reason.setMaximumHeight(90)
        for label,widget in [('学生／整班',self.target),('确认状态',self.status_choice),('核对依据',self.reason)]:form.addRow(label,widget)
        box.addLayout(form);self.message=QLabel();self.message.setWordWrap(True);box.addWidget(self.message)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);box.addWidget(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存核对状态');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')

    def save(self):
        try:
            target=self.target.currentData()
            if target=='__choose__':raise ExamError('请明确选择学生或整班范围。')
            self.d.apply_attendance_status(target,self.status_choice.currentData(),self.reason.toPlainText(),expected=self.expected)
            self.accept()
        except ExamError as error:self.message.setText(error.message_zh)

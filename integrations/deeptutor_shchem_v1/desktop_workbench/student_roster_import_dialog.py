"""Local file preview, explicit columns and per-record roster decisions."""
from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QSpinBox, QTableWidget, QTableWidgetItem,
    QPlainTextEdit, QCheckBox, QDialogButtonBox, QFileDialog)

from ..desktop_exam_data import ExamError, column_name
from ..desktop_student_roster_import import read_roster_file, roster_headers, preview_roster_import, commit_roster_import, import_profiles


class StudentRosterImportDialog(QDialog):
    def __init__(self, facade, roster, parent=None):
        super().__init__(parent);self.facade=facade;self.store=facade.student_roster_store()
        self.roster=deepcopy(roster);self.book=None;self.plan=None;self.saved_roster=None;self._rendering=False
        self._actions={};self._targets={};self._choices={};self.setWindowTitle('导入外部整班名册 · 本机核对');self.resize(1080,760);self.setMinimumSize(680,540)
        box=QVBoxLayout(self)
        notice=QLabel('名单只保存在当前本机私有学生目录。先确认工作表与列，再逐行选择新建、更新到稳定成员或排除；同名不合并，原名单与历史考试快照不改写。')
        notice.setWordWrap(True);box.addWidget(notice)
        row=QHBoxLayout();self.path=QLineEdit();self.path.setReadOnly(True);self.path.setAccessibleName('待导入的本地名单文件')
        self.file_button=QPushButton('选择XLSX／CSV／TSV名单');row.addWidget(self.path,1);row.addWidget(self.file_button);box.addLayout(row)
        settings=QGridLayout();self.encoding=QComboBox();self.delimiter=QComboBox();self.sheet=QComboBox();self.header_row=QSpinBox();self.header_row.setRange(1,9999)
        for label,value in [('UTF／BOM自动识别','auto'),('UTF-8','utf-8-sig'),('UTF-16','utf-16'),('GB18030（明确选择）','gb18030')]:self.encoding.addItem(label,value)
        for label,value in [('按CSV／TSV类型',None),('逗号',','),('制表符','\t'),('分号',';')]:self.delimiter.addItem(label,value)
        self.reload_button=QPushButton('按当前编码重读原文件')
        for i,(label,widget) in enumerate([('文字编码',self.encoding),('分隔符',self.delimiter),('工作表',self.sheet),('表头记录行',self.header_row)]):
            settings.addWidget(QLabel(label),i//2,(i%2)*2);settings.addWidget(widget,i//2,(i%2)*2+1)
        settings.addWidget(self.reload_button,2,0,1,4);box.addLayout(settings)
        mapping=QGridLayout();self.columns={}
        for i,(key,label) in enumerate([('alias_col','学生本地别名（必选）'),('member_col','已有稳定成员UUID（可选）'),('profile_col','匿名档案ID（可选）'),('external_col','原表学号／标识（仅追溯）')]):
            combo=QComboBox();combo.setAccessibleName(label);self.columns[key]=combo
            mapping.addWidget(QLabel(label),i//2,(i%2)*2);mapping.addWidget(combo,i//2,(i%2)*2+1)
            combo.currentIndexChanged.connect(self.invalidate)
        box.addLayout(mapping);self.preview_button=QPushButton('生成本地名单核对预览')
        self.recheck_button=QPushButton('重新核对当前名册版本')
        checks=QHBoxLayout();checks.addWidget(self.preview_button);checks.addWidget(self.recheck_button);box.addLayout(checks)
        batches=QHBoxLayout();self.new_rows_button=QPushButton('明确将无ID可用行设为新建');self.update_ids_button=QPushButton('明确按源稳定UUID更新');self.page=QSpinBox();self.page.setRange(1,1)
        batches.addWidget(self.new_rows_button);batches.addWidget(self.update_ids_button);batches.addWidget(QLabel('每页100条／页码'));batches.addWidget(self.page);box.addLayout(batches)
        self.table=QTableWidget(0,8);self.table.setHorizontalHeaderLabels(['源记录／状态','本地别名','本行处理','更新到稳定成员','错误／重复／重名核对','源已有成员ID','匿名档案','原表标识'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);box.addWidget(self.table,1)
        self.confirm=QCheckBox('我已确认列映射、逐行处理和仅保存到本机的范围')
        self.acknowledge=QCheckBox('我已核对重名／重复项及排除行，不按名称自动合并')
        box.addWidget(self.confirm);box.addWidget(self.acknowledge)
        self.reason=QPlainTextEdit();self.reason.setMaximumHeight(65);self.reason.setPlaceholderText('本次导入核对依据；只需记录必要信息。');box.addWidget(self.reason)
        self.status=QLabel('选择名单后明确选择列；学校学号不等于本机稳定成员UUID。');self.status.setWordWrap(True);box.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.save_button=buttons.button(QDialogButtonBox.StandardButton.Save);self.save_button.setText('确认处理并保存名单')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);box.addWidget(buttons)
        self.file_button.clicked.connect(self.choose_file);self.reload_button.clicked.connect(self.reload)
        self.preview_button.clicked.connect(self.preview);self.sheet.currentIndexChanged.connect(self.headers_changed);self.header_row.valueChanged.connect(self.headers_changed)
        self.recheck_button.clicked.connect(self.recheck_roster)
        self.new_rows_button.clicked.connect(self.choose_new_rows);self.update_ids_button.clicked.connect(self.choose_existing_ids);self.page.valueChanged.connect(self.render_page)
        self.encoding.currentIndexChanged.connect(self.read_settings_changed);self.delimiter.currentIndexChanged.connect(self.read_settings_changed)
        self.preview_button.setEnabled(False);self.save_button.setEnabled(False)

    def choose_file(self):
        path,_=QFileDialog.getOpenFileName(self,'选择本地整班名册','','名单 (*.xlsx *.csv *.tsv)')
        if path:self.load_file(path)

    def load_file(self,path):
        self.path.setText(str(path));self.reload()

    def reload(self):
        if not self.path.text():return
        self.invalidate();self.book=None;self.preview_button.setEnabled(False)
        try:
            book=read_roster_file(self.path.text(),encoding=self.encoding.currentData(),delimiter=self.delimiter.currentData())
            self._rendering=True;self.book=book;self.sheet.clear()
            for sheet in book['sheets']:self.sheet.addItem(sheet['name'],sheet['name'])
            self.header_row.setValue(1);self._rendering=False;self.headers_changed()
            self.preview_button.setEnabled(True);self.status.setText('已读取名单原文件；请选择确切列并生成预览。尚未保存成员。')
        except ExamError as error:self._rendering=False;self.status.setText(error.message_zh)

    def invalidate(self,*_args):
        if self._rendering:return
        self.plan=None;self._actions={};self._targets={};self._choices={};self.table.setRowCount(0)
        self.confirm.setChecked(False);self.acknowledge.setChecked(False);self.save_button.setEnabled(False)

    def read_settings_changed(self,*_args):
        self.invalidate();self.book=None;self.preview_button.setEnabled(False)
        self.status.setText('编码或分隔符已变化，请按当前设置重读原文件，再确认列。')

    def headers_changed(self,*_args):
        if self._rendering or self.book is None:return
        self.invalidate()
        try:
            headers=roster_headers(self.book,self.sheet.currentData(),self.header_row.value())
            self._rendering=True
            for key,combo in self.columns.items():
                combo.clear();combo.addItem('请明确选择' if key=='alias_col' else '不使用',None)
                for i,label in enumerate(headers):combo.addItem(f'{column_name(i)} · {label or "空表头"}',i)
            self._rendering=False
        except ExamError as error:self._rendering=False;self.status.setText(error.message_zh)

    def config(self):
        return {'sheet':self.sheet.currentData(),'header_row':self.header_row.value(),
                **{key:combo.currentData() for key,combo in self.columns.items()}}

    def preview(self):
        if self.book is None:return
        try:
            profiles=import_profiles(self.store)
            plan=preview_roster_import(self.book,self.config(),self.roster,profiles=profiles)
            self.plan=plan;self.confirm.setChecked(False);self.acknowledge.setChecked(False)
            self._choices={row['source_row']:{'action':None} for row in plan['rows']}
            self.page.blockSignals(True);self.page.setRange(1,max(1,(len(plan['rows'])+99)//100));self.page.setValue(1);self.page.blockSignals(False)
            self.render_page()
            self.save_button.setEnabled(True)
            errors=sum(bool(row['errors']) for row in plan['rows']);conflicts=sum(bool(row['conflicts']) for row in plan['rows']);warnings=sum(bool(row['warnings']) for row in plan['rows'])
            self.status.setText(f"本地预览{len(plan['rows'])}条非空记录；错误{errors}条、重复冲突{conflicts}条、重名警示{warnings}条。\n"+'\n'.join(plan['notices']))
        except ExamError as error:self.status.setText(error.message_zh)

    def render_page(self,*_args):
        if self.plan is None:return
        self._rendering=True;self.table.setRowCount(0);self._actions={};self._targets={}
        try:
            offset=(self.page.value()-1)*100
            for row in self.plan['rows'][offset:offset+100]:
                i=self.table.rowCount();self.table.insertRow(i);number=row['source_row'];lines=row['source_lines']
                state='错误' if row['errors'] else '重复待核' if row['conflicts'] else '重名待核' if row['warnings'] else '可核对'
                location=str(number)+' · '+state+(f"（文字行{lines['start']}—{lines['end']}）" if lines else '')
                issues='\n'.join(row['errors']+row['conflicts']+row['warnings']) or '内容可核对，仍需明确处理'
                for j,text in [(0,location),(1,row['alias']),(4,issues),(5,row['member_id'] or '新行，无稳定ID'),(6,row['profile_student_id'] or '空白不清原档案'),(7,row['external_id'] or '')]:
                    item=QTableWidgetItem(text);item.setData(Qt.ItemDataRole.UserRole,number);item.setToolTip(issues);self.table.setItem(i,j,item)
                action=QComboBox();action.addItem('请明确选择',None)
                for label,value in [('新建独立成员','new'),('更新已有稳定成员','update'),('排除此源记录','exclude')]:action.addItem(label,value)
                target=QComboBox();target.addItem('请明确选择成员',None)
                self._actions[number]=action;self._targets[number]=target
                self.table.setCellWidget(i,2,action);self.table.setCellWidget(i,3,target)
                choice=self._choices.get(number,{'action':None});action.setCurrentIndex(max(0,action.findData(choice['action'])))
                self.populate_target(number,choice.get('member_id'));target.setEnabled(choice['action']=='update')
                action.currentIndexChanged.connect(lambda _index,n=number:self.decision_changed(n))
                target.currentIndexChanged.connect(lambda _index,n=number:self.target_changed(n))
            self.table.resizeColumnsToContents()
            for i,width in enumerate((100,120,170,220,240,240,180,150)):self.table.setColumnWidth(i,width)
        finally:self._rendering=False

    def recheck_roster(self):
        if self.plan is None:self.status.setText('请先按当前文件与明确列映射生成预览，再重核名册版本。');return
        try:
            book=read_roster_file(self.path.text(),**self.plan['source']['read_options'])
            if book['source_sha256']!=self.plan['source']['source_sha256']:
                raise ExamError('名单原文件已变化；原选择与原因保留，请重读原文件并逐行核对，不能迁移旧行选择。')
            current=self.store.load(self.roster['id']) if self.roster['revision'] is not None else deepcopy(self.roster)
            profiles=import_profiles(self.store)
            fresh=preview_roster_import(book,self.plan['config'],current,profiles=profiles)
            self.roster=current;self.book=book;self.plan=fresh
            self.confirm.setChecked(False);self.acknowledge.setChecked(False);self.render_page()
            self.status.setText('已重核当前名册版本与档案归属；原逐行选择和原因保留作为待核输入，请重新核对错误／重复提示并明确确认。')
        except ExamError as error:self.status.setText(error.message_zh)

    def populate_target(self,number,selected=None):
        row=next(row for row in self.plan['rows'] if row['source_row']==number);target=self._targets[number]
        if self._actions[number].currentData()!='update':return
        identity=selected or row['member_id']
        options=[m for m in self.roster['members'] if not row['member_id'] or m['id']==row['member_id']]
        for member in options:
            if target.findData(member['id'])<0:target.addItem(member['local_alias']+' · '+member['id'],member['id'])
        target.setCurrentIndex(max(0,target.findData(identity)))

    def clear_confirmation(self,*_args):
        if self._rendering:return
        self.confirm.setChecked(False)

    def decision_changed(self,number):
        self.clear_confirmation();action=self._actions[number].currentData();target=self._targets[number]
        target.setEnabled(action=='update')
        if action=='update':
            self.populate_target(number)
        else:target.setCurrentIndex(0)
        self._choices[number]={'action':action,**({'member_id':target.currentData()} if action=='update' else {})}

    def target_changed(self,number):
        if self._rendering:return
        self.clear_confirmation()
        if self._actions[number].currentData()=='update':self._choices[number]={'action':'update','member_id':self._targets[number].currentData()}

    def choose_new_rows(self):
        if self.plan is None:return
        for row in self.plan['rows']:
            if not row['member_id'] and not row['errors'] and not row['conflicts']:
                self._choices[row['source_row']]={'action':'new'}
        self.clear_confirmation();self.render_page();self.status.setText('已按明确操作将无成员ID、无错误／重复归属冲突的行设为独立新成员；重名不合并，错误行仍须处理。')

    def choose_existing_ids(self):
        if self.plan is None:return
        for row in self.plan['rows']:
            if row['member_id'] and not row['errors']:
                self._choices[row['source_row']]={'action':'update','member_id':row['member_id']}
        self.clear_confirmation();self.render_page();self.status.setText('已按明确操作选择源名单中确属本名册的稳定UUID更新；重复UUID仍须排除到唯一，不按别名匹配。')

    def decisions(self):
        return deepcopy(self._choices)

    def save(self):
        if self.plan is None:self.status.setText('请先按当前名单与列生成核对预览。');return
        try:
            saved=commit_roster_import(self.store,self.path.text(),self.plan,self.decisions(),
                confirmed=self.confirm.isChecked(),acknowledge_warnings=self.acknowledge.isChecked(),reason=self.reason.toPlainText())
            self.saved_roster=saved;self.accept()
        except ExamError as error:self.status.setText(error.message_zh)

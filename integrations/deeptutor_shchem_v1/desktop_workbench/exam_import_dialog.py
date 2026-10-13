"""Explicit Excel column and non-overlapping question mapping before local statistics."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QPushButton, QDialogButtonBox, QHeaderView, QWidget, QScrollArea,
    QInputDialog, QMessageBox, QCheckBox)
from ..desktop_exam_data import display, column_name, build_exam, ExamError, digest
from ..desktop_exam_mapping_profiles import review_profile, resolve_profile, profile_from_config, ROLES, QUESTION_FIELDS, LIMITS


class MappingProfileReviewDialog(QDialog):
    """Nothing is applied to the import draft until an explicit teacher confirmation."""
    def __init__(self, profile, book, current_config, parent=None):
        super().__init__(parent)
        self.profile=profile;self.book=book;self.current_config=current_config;self.resolved_config=None
        self.setWindowTitle('复用映射方案 · 核对本次变化');self.resize(1050,740)
        root=QVBoxLayout(self)
        intro=QLabel(f'方案：{profile["name"]}\n按列标题重新匹配本次工作表。请核对改名、重复表头及题号、满分、知识点；必要时在下表明确选择当前列。')
        intro.setWordWrap(True);root.addWidget(intro)
        review=review_profile(profile,book,current_config)
        self.changes=QLabel('\n'.join(review['changes']));self.changes.setWordWrap(True)
        self.changes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(self.changes);root.addWidget(scroll,1)
        self.mapping=QTableWidget(len(review['bindings']),4)
        self.mapping.setHorizontalHeaderLabels(['用途','原方案列标题','本次成绩列（明确选择）','将使用的题目配置'])
        self.mapping.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.mapping.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.mapping.horizontalHeader().setStretchLastSection(True)
        self.selectors={}
        for row,b in enumerate(review['bindings']):
            self.mapping.setItem(row,0,QTableWidgetItem(b['label']))
            self.mapping.setItem(row,1,QTableWidgetItem(b['header'] or '不选择'))
            combo=QComboBox();combo.addItem('尚未明确绑定','unresolved')
            if b['key']!='student_col':
                combo.addItem('本次不映射此题' if b['key'].startswith('question:') else '本次不选择',None)
            for index,label in enumerate(review['headers']):
                combo.addItem(f'{column_name(index)} · {label or "空表头（不可使用）"}',index)
            if b['column'] is not None:
                combo.setCurrentIndex(combo.findData(b['column']))
            elif b['header'] is None:
                combo.setCurrentIndex(combo.findData(None))
            self.mapping.setCellWidget(row,2,combo);self.selectors[b['key']]=combo
            text=''
            if b['key'].startswith('question:'):
                q=profile['questions'][int(b['key'].split(':')[1])]
                text=f'题号 {q["question"]} · 满分 {q["max_score"]:g}\n{q["knowledge"]} / {q["chapter"]}\n{q["context"]}'
            self.mapping.setItem(row,3,QTableWidgetItem(text))
        self.mapping.resizeRowsToContents();root.addWidget(self.mapping,2)
        self.confirm=QCheckBox('我已核对本次列标题、上述差异和题目配置，确认将方案应用到当前导入草稿')
        root.addWidget(self.confirm)
        self.message=QLabel('取消会保留当前草稿；重新绑定或本次排除仅影响本次导入，更新方案需另行确认。')
        self.message.setWordWrap(True);root.addWidget(self.message)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.apply_button=buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText('确认应用方案');self.apply_button.setEnabled(False)
        self.confirm.toggled.connect(self.apply_button.setEnabled)
        buttons.accepted.connect(self.accept_mapping);buttons.rejected.connect(self.reject);root.addWidget(buttons)

    def accept_mapping(self):
        if not self.confirm.isChecked():
            self.message.setText('请先核对本次变化并勾选确认。');return
        try:
            self.resolved_config=resolve_profile(self.profile,self.book,self.current_config,
                {key:combo.currentData() for key,combo in self.selectors.items()})
        except ExamError as e:
            self.message.setText(e.message_zh);return
        self.accept()


class ExamImportDialog(QDialog):
    def __init__(self, book, parent=None, *, mapping_profiles=None):
        super().__init__(parent)
        self.book=book;self.exam=None;self.mapping_profiles=mapping_profiles;self._selected_profile=None
        self.setWindowTitle('导入成绩 · 核对列与题号');self.resize(1050,780)
        root=QVBoxLayout(self)
        hint=QLabel('一张工作表只放同一场考试；一行一名学生。支持总分或逐题分数，空白/缺考不计0分。')
        hint.setWordWrap(True);root.addWidget(hint)
        profile_bar=QHBoxLayout();profile_bar.addWidget(QLabel('列映射方案'))
        self.profile_choice=QComboBox();self.profile_choice.setMinimumWidth(180);profile_bar.addWidget(self.profile_choice,1)
        self.profile_reuse=QPushButton('选择并核对复用');self.profile_save=QPushButton('命名保存新方案')
        self.profile_update=QPushButton('更新选中方案');self.profile_delete=QPushButton('删除选中方案')
        for button in (self.profile_reuse,self.profile_save,self.profile_update,self.profile_delete):profile_bar.addWidget(button)
        root.addLayout(profile_bar)
        profile_hint=QLabel('方案仅保存列标题、满分阈值和教师题目配置；不保存学生行、成绩、考试名称或源文件位置。')
        profile_hint.setWordWrap(True);root.addWidget(profile_hint)
        self.title=QLineEdit(book['source_name'].rsplit('.',1)[0]);self.sheet=QComboBox()
        for s in book['sheets']:self.sheet.addItem(s['name']+('（隐藏表）' if s['hidden'] else ''),s['name'])
        self.header=QSpinBox();self.header.setRange(1,100);self.header.setValue(1)
        self.student=QComboBox();self.class_col=QComboBox();self.total=QComboBox();self.status_col=QComboBox()
        self.maximum=QDoubleSpinBox();self.maximum.setRange(.01,10000);self.maximum.setValue(100)
        self.passing=QDoubleSpinBox();self.passing.setRange(0,10000);self.passing.setValue(60)
        self.high=QDoubleSpinBox();self.high.setRange(.01,10000);self.high.setValue(85)
        fields=QWidget();form=QFormLayout(fields)
        for label,w in [('考试名称',self.title),('成绩工作表',self.sheet),('表头所在行',self.header),('学生标识列（姓名或学号，仅本机）',self.student),
                        ('班级列（可不选）',self.class_col),('总分列（可按完整小题相加）',self.total),('缺考状态列（可不选）',self.status_col),
                        ('试卷满分',self.maximum),('达标线（自定分析阈值）',self.passing),('高分线（自定分析阈值）',self.high)]:form.addRow(label,w)
        tabs=QTabWidget();root.addWidget(tabs,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(fields);tabs.addTab(scroll,'1. 基本映射')
        self.mapping=QTableWidget();self.mapping.setColumnCount(7)
        self.mapping.setHorizontalHeaderLabels(['计入','成绩列','本卷题号','满分','主知识点','章节','公共材料/备注'])
        self.mapping.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        page=QWidget();layout=QVBoxLayout(page)
        tip=QLabel('可不选小题，先分析总分。勾选后必须填真实满分；同一得分列不重复使用，不同时选择大题总分和其小问。\n识别“题目映射”工作表的同名列；自动填入后仍请核对。')
        tip.setWordWrap(True);layout.addWidget(tip);layout.addWidget(self.mapping,1);tabs.addTab(page,'2. 小题与知识点')
        self.preview=QTableWidget();self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);tabs.addTab(self.preview,'3. 原表预览（前8行）')
        self.message=QLabel('');self.message.setWordWrap(True);root.addWidget(self.message)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('核对并生成本地面板')
        buttons.accepted.connect(self.commit);buttons.rejected.connect(self.reject);root.addWidget(buttons)
        self.sheet.currentIndexChanged.connect(self.populate);self.header.valueChanged.connect(self.populate)
        for w in (self.student,self.class_col,self.total,self.status_col):w.currentIndexChanged.connect(self.choices_changed)
        self.populate()
        self.profile_choice.currentIndexChanged.connect(self.profile_selected)
        self.profile_save.clicked.connect(self.save_profile);self.profile_update.clicked.connect(self.update_profile)
        self.profile_delete.clicked.connect(self.delete_profile);self.profile_reuse.clicked.connect(self.review_mapping_profile)
        self.refresh_profiles()

    def refresh_profiles(self, identity=None):
        self.profile_choice.blockSignals(True);self.profile_choice.clear()
        self.profile_choice.addItem('选择已保存方案',None)
        if self.mapping_profiles is not None:
            for key,name in self.mapping_profiles.entries():self.profile_choice.addItem(name,key)
        if identity is not None:self.profile_choice.setCurrentIndex(self.profile_choice.findData(identity))
        self.profile_choice.blockSignals(False);self.profile_selected()
        self.profile_save.setEnabled(self.mapping_profiles is not None)
        if self.mapping_profiles is None:self.profile_save.setToolTip('此窗口未配置本地方案存储；可继续完成本次成绩导入。')

    def profile_selected(self):
        self._selected_profile=None
        if self.mapping_profiles is not None and self.profile_choice.currentData() is not None:
            try:self._selected_profile=self.mapping_profiles.load(self.profile_choice.currentData())
            except ExamError as e:self.message.setText(e.message_zh)
        for button in (self.profile_reuse,self.profile_update,self.profile_delete):button.setEnabled(self._selected_profile is not None)

    def save_profile(self):
        if self.mapping_profiles is None:return
        name,ok=QInputDialog.getText(self,'保存列映射方案','方案名称（按表格格式或教学用途命名）')
        if not ok:return
        try:profile=self.mapping_profiles.save(name,self.book,self.config())
        except ExamError as e:self.message.setText(e.message_zh);return
        self.refresh_profiles(profile['id']);self.message.setText(f'已保存方案“{profile["name"]}”；学生行与成绩未写入方案。')

    def update_profile(self):
        profile=self._selected_profile
        if profile is None:return
        try:candidate=profile_from_config(profile['name'],self.book,self.config(),identity=profile['id'])
        except ExamError as e:self.message.setText(e.message_zh);return
        changes=[]
        if profile['headers']!=candidate['headers']:
            changes.append('表头及顺序：\n原：'+' / '.join(profile['headers'])+'\n新：'+' / '.join(candidate['headers']))
        for key,label in ROLES.items():
            if profile['roles'][key]!=candidate['roles'][key]:
                changes.append(f'{label}：{profile["roles"][key] or "不选择"} → {candidate["roles"][key] or "不选择"}')
        for key,label in zip(LIMITS,('试卷满分','达标线','高分线')):
            if profile[key]!=candidate[key]:changes.append(f'{label}：{profile[key]} → {candidate[key]}')
        old_questions={q['header']:q for q in profile['questions']};new_questions={q['header']:q for q in candidate['questions']}
        for header in dict.fromkeys([*old_questions,*new_questions]):
            old=old_questions.get(header);new=new_questions.get(header)
            if old!=new:
                def describe(q):
                    if q is None:return '不映射'
                    return f'题号 {q["question"]} / 满分 {q["max_score"]:g} / {q["knowledge"]} / {q["chapter"]} / {q["context"]}'
                changes.append(f'题目列 {header}\n原：{describe(old)}\n新：{describe(new)}')
        summary='\n\n'.join(changes) if changes else '结构与题目配置没有变化。'
        if QMessageBox.question(self,'更新列映射方案',f'将当前配置替换方案“{profile["name"]}”？\n学生行及成绩不会写入方案。\n\n'+summary,
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:profile=self.mapping_profiles.save(profile['name'],self.book,self.config(),profile['id'],expected_revision=digest(profile))
        except ExamError as e:self.message.setText(e.message_zh);return
        self.refresh_profiles(profile['id']);self.message.setText(f'已更新方案“{profile["name"]}”。')

    def delete_profile(self):
        profile=self._selected_profile
        if profile is None:return
        if QMessageBox.question(self,'删除列映射方案',f'删除方案“{profile["name"]}”？\n当前导入草稿将保留。',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:self.mapping_profiles.delete(profile['id'],expected_revision=digest(profile))
        except ExamError as e:self.message.setText(e.message_zh);return
        self.refresh_profiles();self.message.setText('已删除选中方案；当前导入草稿保留。')

    def review_mapping_profile(self):
        if self._selected_profile is None:return
        try:dialog=MappingProfileReviewDialog(self._selected_profile,self.book,self.config(),self)
        except ExamError as e:self.message.setText(e.message_zh);return
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.apply_config(dialog.resolved_config)
            self.message.setText('已按本次明确选择应用方案；请继续核对原表与本卷题目，再生成本地面板。')
        dialog.deleteLater()

    def apply_config(self, config):
        """Apply only the review's validated result, preserving the current exam title."""
        self.sheet.blockSignals(True);self.header.blockSignals(True)
        self.sheet.setCurrentIndex(self.sheet.findData(config['sheet']));self.header.setValue(config['header_row'])
        self.sheet.blockSignals(False);self.header.blockSignals(False);self.populate()
        for key,combo in (('student_col',self.student),('class_col',self.class_col),('total_col',self.total),('status_col',self.status_col)):
            combo.blockSignals(True);combo.setCurrentIndex(combo.findData(config[key]));combo.blockSignals(False)
        for key,spin in (('max_score',self.maximum),('pass_score',self.passing),('excellent_score',self.high)):
            spin.setValue(config[key])
        for row in range(self.mapping.rowCount()):self.mapping.item(row,0).setCheckState(Qt.CheckState.Unchecked)
        for q in config['questions']:
            row=q['column'];self.mapping.item(row,0).setCheckState(Qt.CheckState.Checked)
            for column,key in enumerate(QUESTION_FIELDS,2):self.mapping.item(row,column).setText(str(q.get(key,'')))
        self.choices_changed()

    def populate(self):
        sheet=next(s for s in self.book['sheets'] if s['name']==self.sheet.currentData())
        grid=sheet['rows'];h=self.header.value()-1
        if h>=len(grid):return
        labels=[display(v).strip() for v in grid[h]]
        def fill(combo, aliases, optional=True):
            combo.blockSignals(True);combo.clear()
            if optional:combo.addItem('不选择'+('（按完整小题相加）' if combo is self.total else ''),None)
            for i,x in enumerate(labels):combo.addItem(f'{column_name(i)} · {x or "空表头"}',i)
            match=next((i for alias in aliases for i,x in enumerate(labels) if x==alias),None)
            if match is not None:combo.setCurrentIndex(combo.findData(match))
            combo.blockSignals(False)
        fill(self.student,['学号','学生编号','姓名','学生'],False);fill(self.class_col,['班级','班级名称'])
        fill(self.total,['总分','合计分','成绩']);fill(self.status_col,['状态','考试状态','缺考状态'])
        self.mapping.setRowCount(len(labels))
        metadata={}
        for meta in self.book['sheets']:
            if meta['name']=='题目映射' and meta['rows']:
                heads=[display(v) for v in meta['rows'][0]]
                for row in meta['rows'][1:]:
                    d=dict(zip(heads,map(display,row)))
                    metadata[d.get('成绩列','')]=d
        for i,label in enumerate(labels):
            d=metadata.get(label,{})
            check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if d else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole,i);self.mapping.setItem(i,0,check)
            for c,text in enumerate([label,d.get('本卷题号',label),d.get('满分',''),d.get('知识点',''),d.get('章节',''),d.get('公共材料/备注','')],1):
                item=QTableWidgetItem(text)
                if c==1:item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.mapping.setItem(i,c,item)
        shown=grid[h:h+9];self.preview.setColumnCount(len(labels));self.preview.setRowCount(len(shown))
        self.preview.setHorizontalHeaderLabels([column_name(i) for i in range(len(labels))])
        for r,row in enumerate(shown):
            for c,v in enumerate(row):self.preview.setItem(r,c,QTableWidgetItem(display(v)))
        self.preview.resizeColumnsToContents();self.choices_changed()
        self.message.setText('公式只读已保存结果，请先在Excel重算保存。隐藏行及Excel筛选不会排除数据。')

    def choices_changed(self):
        used={c.currentData() for c in (self.student,self.class_col,self.total,self.status_col)}
        for r in range(self.mapping.rowCount()):
            check=self.mapping.item(r,0)
            if check is None:continue
            self.mapping.setRowHidden(r, r in used)
            if r in used:check.setCheckState(Qt.CheckState.Unchecked)

    def config(self):
        questions=[]
        for r in range(self.mapping.rowCount()):
            if self.mapping.item(r,0).checkState()!=Qt.CheckState.Checked:continue
            vals=[self.mapping.item(r,c).text().strip() for c in range(2,7)]
            questions.append(dict(zip(('question','max_score','knowledge','chapter','context'),vals),column=r))
        return {'title':self.title.text(),'sheet':self.sheet.currentData(),'header_row':self.header.value(),
                'student_col':self.student.currentData(),'class_col':self.class_col.currentData(),
                'total_col':self.total.currentData(),'status_col':self.status_col.currentData(),
                'max_score':self.maximum.value(),'pass_score':self.passing.value(),'excellent_score':self.high.value(),'questions':questions}

    def commit(self):
        try:self.exam=build_exam(self.book,self.config())
        except ExamError as e:self.message.setText(e.message_zh);return
        self.accept()

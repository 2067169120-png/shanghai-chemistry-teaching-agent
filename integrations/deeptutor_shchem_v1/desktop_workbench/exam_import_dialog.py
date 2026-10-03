"""Explicit Excel column and non-overlapping question mapping before local statistics."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QPushButton, QDialogButtonBox, QHeaderView, QWidget, QScrollArea, QInputDialog)
from ..desktop_exam_data import display, column_name, build_exam, ExamError


class ExamImportDialog(QDialog):
    def __init__(self, book, parent=None, *, state=None):
        super().__init__(parent)
        self.book=book;self.exam=None
        self.setWindowTitle('导入成绩 · 核对列与题号');self.resize(1050,780)
        root=QVBoxLayout(self)
        hint=QLabel('一张工作表只放同一场考试；一行一名学生。支持总分或逐题分数，空白/缺考不计0分。')
        hint.setWordWrap(True);root.addWidget(hint)
        self.profile_store=None
        if state is not None:
            from ..desktop_exam_mapping_profiles import ExamMappingProfiles
            self.profile_store=ExamMappingProfiles(state)
            preset=QWidget();row=QHBoxLayout(preset);row.setContentsMargins(0,0,0,0)
            self.profiles=QComboBox();row.addWidget(self.profiles,1)
            apply=QPushButton('核对并复用方案…');apply.clicked.connect(self.apply_profile);row.addWidget(apply)
            save=QPushButton('存为新方案…');save.clicked.connect(lambda:self.save_profile(False));row.addWidget(save)
            update=QPushButton('更新选中方案');update.clicked.connect(lambda:self.save_profile(True));row.addWidget(update)
            root.addWidget(preset)
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
        if self.profile_store:self.reload_profiles()
        for button in self.findChildren(QPushButton):button.setAutoDefault(False)

    def reload_profiles(self, selected=None):
        self.profiles.clear();self.profiles.addItem('手动映射 / 选择已保存方案',None)
        try:
            for profile in self.profile_store.profiles():self.profiles.addItem(profile['label'],profile)
            for i in range(self.profiles.count()):
                if (self.profiles.itemData(i) or {}).get('id')==selected:self.profiles.setCurrentIndex(i);break
        except ExamError as exc:self.message.setText(exc.message_zh)

    def apply_profile(self):
        profile=self.profiles.currentData()
        if not profile:self.message.setText('请先选择已保存的列映射方案。');return
        try:check=self.profile_store.preview(profile['id'],profile['revision'],self.book)
        except ExamError as exc:self.message.setText(exc.message_zh);return
        dialog=QDialog(self);dialog.setWindowTitle('核对方案与本次成绩表');dialog.resize(760,560)
        layout=QVBoxLayout(dialog)
        hint=QLabel('按列顺序核对表头。表头完全一致时可复用；成绩内容仍按本次文件重新读取。'+
                    ('\n表头或工作表有差异，请返回手动映射。' if not check['exact'] else ''))
        hint.setWordWrap(True);layout.addWidget(hint)
        expected=check['expected']['headers'];current=(check['current'] or {}).get('headers',[])
        table=QTableWidget(max(len(expected),len(current)),3)
        table.setHorizontalHeaderLabels(['列','方案表头','本次表头']);table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for r in range(table.rowCount()):
            for c,text in enumerate((column_name(r),expected[r] if r<len(expected) else '（不存在）',current[r] if r<len(current) else '（不存在）')):
                table.setItem(r,c,QTableWidgetItem(text))
        layout.addWidget(table,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('复用已核对方案')
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(check['exact'])
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                current_check=self.profile_store.preview(profile['id'],profile['revision'],self.book)
                if current_check['exact']:
                    self.apply_config(current_check['config'])
                    self.message.setText('已复用核对后的列映射；请核对本次满分、阈值和小题，再生成本地面板。')
            except ExamError as exc:self.message.setText(exc.message_zh)
        dialog.deleteLater()

    def apply_config(self, config):
        self.sheet.blockSignals(True);self.header.blockSignals(True)
        self.sheet.setCurrentIndex(self.sheet.findData(config['sheet']));self.header.setValue(config['header_row'])
        self.sheet.blockSignals(False);self.header.blockSignals(False);self.populate()
        for combo,key in ((self.student,'student_col'),(self.class_col,'class_col'),(self.total,'total_col'),(self.status_col,'status_col')):
            combo.setCurrentIndex(combo.findData(config[key]))
        for spin,key in ((self.maximum,'max_score'),(self.passing,'pass_score'),(self.high,'excellent_score')):spin.setValue(config[key])
        for r in range(self.mapping.rowCount()):self.mapping.item(r,0).setCheckState(Qt.CheckState.Unchecked)
        for question in config['questions']:
            r=question['column'];self.mapping.item(r,0).setCheckState(Qt.CheckState.Checked)
            for c,key in enumerate(('question','max_score','knowledge','chapter','context'),2):
                self.mapping.item(r,c).setText(str(question.get(key,'')))
        self.choices_changed()

    def save_profile(self, update):
        profile=self.profiles.currentData() if update else None
        if update and not profile:self.message.setText('请先选择需要更新的方案。');return
        label,ok=QInputDialog.getText(self,'保存成绩列映射方案','方案名称',text=(profile or {}).get('label',''))
        if not ok:return
        try:
            result=self.profile_store.save(label,self.book,self.config(),profile_id=(profile or {}).get('id'),
                                           expected_revision=(profile or {}).get('revision'))
            self.reload_profiles(result['id']);self.message.setText('映射方案已保存，仅包含列、满分与题目标签；学生成绩仍来自每次导入的文件。')
        except ExamError as exc:self.message.setText(exc.message_zh)

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

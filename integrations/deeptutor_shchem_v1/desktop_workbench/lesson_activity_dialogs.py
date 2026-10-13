"""Select local lesson activities and explicitly review source refreshes."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QVBoxLayout, QWidget)

from ..desktop_composition_activities import (activity_title, refresh_activity,
    keep_activity_snapshot, work_copy_changed)
from ..desktop_lesson_composition import digest


def label(text):
    widget = QLabel(text); widget.setTextFormat(Qt.TextFormat.PlainText); widget.setWordWrap(True)
    return widget


def readable_snapshot(snapshot):
    n = snapshot['node']; timing = '尚未估时' if n['minutes'] is None else f"{n['minutes']}分钟"
    lines = [activity_title(snapshot), n['kind'] + ' · ' + timing,
             '教学目标：' + ('；'.join(g['text'] for g in snapshot['objectives']) or '尚未关联目标'), '',
             '学生可见：', '学生任务：' + (n['student_task'] or '尚未填写')]
    if n['student_material']:
        lines += ['材料：' + (n['material_text'] or '无文字材料')]
        lines += ['图片：' + a['caption'] + ' · 来源：' + a['source'] for a in snapshot['image_assets']]
    else:
        lines.append('材料和图片仅供教师查看。')
    lines += ['', '教师可见：']
    for key, name in (('teacher_action', '教师活动'), ('expected_output', '预期产出'),
                      ('criteria', '评价依据'), ('teacher_answer', '教师答案'), ('notes', '教师备注')):
        if n[key]: lines.append(name + '：' + n[key])
    if not n['student_material']:
        if n['material_text']: lines.append('材料：' + n['material_text'])
        lines += ['图片：' + a['caption'] + ' · 来源：' + a['source'] for a in snapshot['image_assets']]
    return '\n'.join(lines)


class ActivityPicker(QDialog):
    def __init__(self, rows, parent):
        super().__init__(parent); self.rows = deepcopy(rows); self.chosen = []; self.checked = set(); self.loading = False
        self.setWindowTitle('选择教学环节加入课件画布'); self.resize(960, 700)
        root = QVBoxLayout(self)
        root.addWidget(label('勾选本课环节。学生任务与已勾选的材料进入学生页；教师活动、评价依据、答案和备注保留在教师版。'))
        self.search = QLineEdit(); self.search.setPlaceholderText('查找环节、学生任务或材料'); root.addWidget(self.search)
        split = QSplitter(); root.addWidget(split, 1)
        self.list = QListWidget(); self.list.setAccessibleName('待加入课件的教学环节'); split.addWidget(self.list)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); split.addWidget(self.details); split.setSizes([290, 610])
        self.count = label('尚未勾选环节'); root.addWidget(self.count)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok); self.apply_button.setText('加入所选环节')
        buttons.accepted.connect(self.choose); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.search.textChanged.connect(self.filter); self.list.itemChanged.connect(self.check)
        self.list.currentItemChanged.connect(self.show_row); self.filter()

    def filter(self, *_):
        self.loading = True; query = self.search.text().casefold().strip(); self.list.clear()
        for row in self.rows:
            snapshot = row['snapshot']
            if query and query not in readable_snapshot(snapshot).casefold(): continue
            item = QListWidgetItem(activity_title(snapshot) + ' · ' + snapshot['node']['kind'])
            item.setData(Qt.ItemDataRole.UserRole, row['node_id'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row['node_id'] in self.checked else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.loading = False
        if self.list.count(): self.list.setCurrentRow(0)
        else: self.details.clear()
        self.update_count()

    def check(self, item):
        if self.loading: return
        identity = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked: self.checked.add(identity)
        else: self.checked.discard(identity)
        self.update_count()

    def update_count(self):
        self.count.setText(f'已勾选 {len(self.checked)} 个环节，按教学设计中的顺序加入。')
        self.apply_button.setEnabled(bool(self.checked))

    def show_row(self, item, *_):
        row = next((r for r in self.rows if item and r['node_id'] == item.data(Qt.ItemDataRole.UserRole)), None)
        self.details.setPlainText(readable_snapshot(row['snapshot']) if row else '')

    def choose(self):
        self.chosen = [r['node_id'] for r in self.rows if r['node_id'] in self.checked]
        if self.chosen: self.accept()


class ActivityUpdateDialog(QDialog):
    def __init__(self, composition, identity, plan, assets, parent):
        super().__init__(parent)
        from ..desktop_composition_activities import activity_snapshot
        self.composition = deepcopy(composition); self.identity = identity; self.plan = deepcopy(plan); self.assets = deepcopy(assets)
        self.result_value = None; self.item = next(r for r in composition['resources'] if r['id'] == identity)
        self.setWindowTitle('核对教学环节关联'); self.resize(960, 750)
        root = QVBoxLayout(self)
        root.addWidget(label('更新将采用当前教学环节，保留素材的阅读顺序和所有外框位置；文字变长时需要重新检查续页。也可保留现用副本，停止跟随环节更新。'))
        tabs = QTabWidget(); root.addWidget(tabs, 1)
        for title, text in (
                ('现用副本', self.item['title'] + '\n\n' + '\n\n'.join(b['value'] if b['kind'] == 'text' else '【本地图片】' for b in self.item['blocks'])),
                ('原环节快照', readable_snapshot(self.item['original']['evidence']['activity_snapshot']))):
            view = QPlainTextEdit(); view.setReadOnly(True); view.setPlainText(text); tabs.addTab(view, title)
        self.source_available = True
        try:
            if plan['id'] != self.item['activity_link']['design_id']:
                raise ValueError('关联的教学设计已不存在。')
            source = readable_snapshot(activity_snapshot(plan, self.item['activity_link']['node_id'], assets))
        except ValueError as exc:
            source = str(exc); self.source_available = False
        self.current = QPlainTextEdit(); self.current.setReadOnly(True); self.current.setPlainText(source); tabs.addTab(self.current, '当前关联环节')
        tabs.setCurrentIndex(2)
        self.allow_replace = QCheckBox('已核对现用副本，允许用当前环节替换我在画布中编辑过的标题和文字')
        self.allow_replace.setVisible(work_copy_changed(self.item)); root.addWidget(self.allow_replace)
        self.message = label(''); root.addWidget(self.message)
        actions = QHBoxLayout(); root.addLayout(actions)
        self.keep_button = QPushButton('保留独立副本'); self.keep_button.clicked.connect(self.keep); actions.addWidget(self.keep_button)
        actions.addStretch(1)
        self.update_button = QPushButton('用当前环节更新'); self.update_button.clicked.connect(self.update); actions.addWidget(self.update_button)
        cancel = QPushButton('返回'); cancel.clicked.connect(self.reject); actions.addWidget(cancel)
        self.allow_replace.toggled.connect(self.toggle); self.toggle()

    def toggle(self, *_):
        self.update_button.setEnabled(self.source_available and
            (not work_copy_changed(self.item) or self.allow_replace.isChecked()))

    def update(self):
        try:
            self.result_value = refresh_activity(self.composition, self.identity, self.plan, self.assets,
                expected=digest(self.composition), replace_edited=self.allow_replace.isChecked())
            self.accept()
        except ValueError as exc: self.message.setText(str(exc))

    def keep(self):
        self.result_value = keep_activity_snapshot(self.composition, self.identity); self.accept()

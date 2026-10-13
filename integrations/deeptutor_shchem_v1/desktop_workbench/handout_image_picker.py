"""Find embedded lecture pictures and knowledge maps without reimporting Word."""
from copy import deepcopy
import json

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QSplitter, QVBoxLayout, QWidget)
from ..desktop_handout_images import handout_image_preview, handout_image_context, handout_image_roles, image_key
from ..desktop_handout_sources import validate_handout_source
from ..desktop_composition_images import teacher_image
from ..desktop_word_metafile_preview import can_attempt_metafile
from .preparation_images_widget import _LocalImagePreview
from .tasks import DesktopTaskBridge
from .components import page_scroll


def _label(text=''):
    item = QLabel(text); item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True); item.setMinimumWidth(0); return item


def _identity(row):
    return (row['batch_id'], row['source_id'], row['asset_id']) if row else None


class HandoutImagePicker(QDialog):
    def __init__(self, facade, snapshot, resources, parent=None, *, tasks=None):
        super().__init__(parent)
        self.facade = facade; self.rows = deepcopy(snapshot['images']); self.resources = deepcopy(resources)
        self.document_source = None
        if snapshot.get('document_source') is not None:
            self.document_source = validate_handout_source(snapshot['document_source'])
            if 'asset_id' in self.document_source:
                raise ValueError('讲义知识的来源记录不是完整文件，请重新选择。')
            for row in self.rows:
                checked = validate_handout_source(row)
                if ('asset_id' not in checked or any(checked[key] != self.document_source[key]
                        for key in ('batch_id','source_id','source_name','source_path','source_sha256','source_revision'))):
                    raise ValueError('讲义图目录与当前来源不一致，请重新打开。')
        self.contexts = deepcopy(snapshot.get('contexts', {}))
        self.tasks = tasks or DesktopTaskBridge(self); self._own_tasks = tasks is None
        self._task = None; self._context_task = None; self._epoch = 0; self._closed = False; self.current = None
        self._previewed = None; self.edits = {}; self.selection = None
        self.setWindowTitle('选择讲义原图与知识结构网'); self.resize(1080, 800); self.setMinimumSize(360, 520)
        root = QVBoxLayout(self)
        root.addWidget(_label('按讲义找原图，完整预览后加入本课。知识结构网可放在开场、概念之间或小结，串起讲解内容。'))
        self.split = QSplitter(); root.addWidget(self.split, 1)
        left = QWidget(); navigation = QVBoxLayout(left); navigation.setContentsMargins(0, 0, 0, 0)
        self.source = QComboBox(); self.source.setMinimumWidth(0)
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source.setAccessibleName('选择已导入讲义'); self.source.addItem('全部讲义', None)
        sources = {}
        for row in self.rows: sources.setdefault((row['batch_id'], row['source_id']), [row['source_name'], 0])[1] += 1
        if self.document_source is not None:
            key = (self.document_source['batch_id'], self.document_source['source_id'])
            sources.setdefault(key, [self.document_source['source_name'], 0])
        for key, (name, count) in sources.items(): self.source.addItem(f'{name}（{count}图）', key)
        navigation.addWidget(self.source)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜索讲义名称、栏目或原文附近文字'); navigation.addWidget(self.search)
        self.small = QCheckBox('同时显示小图与符号'); self.small.setToolTip('默认略去小于4KB的小图；可本地转换的矢量图保留。勾选后可查看全部原图。'); navigation.addWidget(self.small)
        self.count = _label(); navigation.addWidget(self.count)
        self.list = QListWidget(); self.list.setWordWrap(True); self.list.setMinimumSize(0, 72)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setAccessibleName('所选讲义中的原图'); navigation.addWidget(self.list, 1); self.split.addWidget(left)
        right = QWidget(); editor = QVBoxLayout(right); editor.setContentsMargins(0, 0, 0, 0)
        self.heading = _label(); editor.addWidget(self.heading)
        self.context = QPlainTextEdit(); self.context.setReadOnly(True); self.context.setFixedHeight(68)
        self.context.setAccessibleName('所选原图的原文栏目与附近文字'); editor.addWidget(self.context)
        self.preview = _LocalImagePreview(self); self.preview.image.setFixedHeight(230); editor.addWidget(self.preview)
        self.dimensions = _label(); editor.addWidget(self.dimensions)
        self.read_button = QPushButton('通读原文并继续选图…'); self.read_button.setEnabled(False)
        self.read_button.clicked.connect(self.read_original); editor.addWidget(self.read_button)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.title = QLineEdit(); self.title.setMaxLength(160)
        self.title.setPlaceholderText('例如：这些知识点怎样联系起来？'); self.title.setAccessibleName('本页引导问题或图题')
        self.purpose = QPlainTextEdit(); self.purpose.setFixedHeight(90); self.purpose.setPlaceholderText('学生看哪条分支、联系哪些概念、记下什么解释')
        self.teacher_only = QCheckBox('仅教师可见')
        form.addRow('引导问题／图题', self.title); form.addRow('讲解用途', self.purpose); form.addRow(self.teacher_only); editor.addLayout(form)
        self.details_button = QPushButton('查看原文位置与来源记录'); self.details_button.setCheckable(True); editor.addWidget(self.details_button)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setFixedHeight(120); self.details.hide(); editor.addWidget(self.details)
        self.details_button.toggled.connect(self.details.setVisible)
        self.editor_scroll = page_scroll(right); self.split.addWidget(self.editor_scroll); self.split.setSizes([350, 710])
        self.status = _label(); root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm = buttons.button(QDialogButtonBox.StandardButton.Ok); self.confirm.setText('加入当前课件'); self.confirm.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.choose); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.source.currentIndexChanged.connect(self.filter_rows); self.search.textChanged.connect(self.filter_rows)
        self.small.toggled.connect(self.filter_rows); self.list.currentItemChanged.connect(self.show_row); self.filter_rows()
        if snapshot.get('issues'): self.status.setText(f"{len(snapshot['issues'])}份讲义暂未读取；当前已读取的原图可继续选用。")
        if snapshot.get('source'): self.focus_image(snapshot['source'])
        elif self.document_source is not None: self.focus_document(self.document_source)

    def save_edits(self):
        if self.current is not None:
            self.edits[_identity(self.current)] = (self.title.text(), self.purpose.toPlainText(), self.teacher_only.isChecked())

    def filter_rows(self, *_):
        self.save_edits(); old = _identity(self.current); source = self.source.currentData(); query = self.search.text().strip().casefold(); selected = 0
        with QSignalBlocker(self.list):
            self.list.clear()
            for row in self.rows:
                if source is not None and (row['batch_id'], row['source_id']) != tuple(source): continue
                context = self.contexts.get(image_key(row), {})
                searchable = '\n'.join((row['source_name'], context.get('section', ''), context.get('excerpt', '')))
                if query and query not in searchable.casefold(): continue
                if not self.small.isChecked() and row['bytes_count'] < 4096 and not can_attempt_metafile(row): continue
                number = row['asset_id'].rsplit('image', 1)[-1]
                text = f"原图{number} · 区块{row['block_index']}"
                if context.get('section'): text += '\n' + ' '.join(context['section'].split())[:70]
                elif context.get('excerpt'): text += '\n' + ' '.join(context['excerpt'].split())[:70]
                if source is None: text += '\n' + row['source_name']
                item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole, row); self.list.addItem(item)
                if _identity(row) == old: selected = self.list.count() - 1
            self.list.setCurrentRow(selected if self.list.count() else -1)
        self.count.setText(f'当前 {self.list.count()} 张原图'); self.show_row(self.list.currentItem())

    def show_row(self, item, *_):
        self.save_edits(); self._epoch += 1; self._previewed = None; self.confirm.setEnabled(False); self.read_button.setEnabled(False)
        if self._task is not None: self.tasks.cancel(self._task); self._task = None
        if self._context_task is not None: self.tasks.cancel(self._context_task); self._context_task = None
        self.current = deepcopy(item.data(Qt.ItemDataRole.UserRole)) if item else None
        self.preview.clear('正在读取完整原图…' if item else '没有匹配的原图。可调整讲义或显示小图。'); self.dimensions.clear()
        if self.current is None: self.heading.clear(); self.context.clear(); self.title.clear(); self.purpose.clear(); self.details.clear(); return
        row = deepcopy(self.current); key = _identity(row); epoch = self._epoch
        roles = handout_image_roles(self.resources, row); restricted = teacher_image(roles)
        title, purpose, scope = self.edits.get(key, ('观察这张图，能发现什么？', '', restricted))
        self.title.setText(title); self.purpose.setPlainText(purpose); self.teacher_only.setChecked(scope or restricted); self.teacher_only.setEnabled(not restricted)
        self.heading.setText(row['source_name'] + f"\n原文区块{row['block_index']}")
        context = self.contexts.get(image_key(row), {})
        self.context.setPlainText(('原文栏目：' + context['section'] + '\n' if context.get('section') else '')
                             + ('原文附近：' + context['excerpt'] if context.get('excerpt') else '当前图片区块没有文字说明，可通读原文查看上下文。'))
        self.editor_scroll.verticalScrollBar().setValue(0)
        self.details.setPlainText(json.dumps(row, ensure_ascii=False, indent=2)); self.status.setText('正在读取讲义原图…')
        self._task = self.tasks.submit('读取讲义原图', lambda: handout_image_preview(self.facade, row),
            on_success=lambda value: self.ready(epoch, key, value), on_failure=lambda message: self.failed(epoch, key, message))

    def ready(self, epoch, key, value):
        if self._closed or epoch != self._epoch or key != _identity(self.current): return
        self._task = None
        if value['source'] != self.current: self.failed(epoch, key, '原图记录已变化，请重新选择。'); return
        self._previewed = deepcopy(value['source']); info = value['info']
        self.preview.set_bytes(value['bytes'], self.current['source_name'])
        display = ' · 本地转换显示' if value.get('derived_preview') is True else ''
        self.dimensions.setText(f"完整原图{display} · {info['width']} × {info['height']} 像素")
        self.confirm.setEnabled(True); self.read_button.setEnabled(True); self.status.setText('填写本页的问题和讲解用途，再加入当前课件。')

    def failed(self, epoch, key, message):
        if self._closed or epoch != self._epoch or key != _identity(self.current): return
        self._task = None; self._previewed = None; self.confirm.setEnabled(False); self.read_button.setEnabled(False); self.preview.clear('原图暂未显示'); self.status.setText(message)

    def focus_image(self, row):
        if row not in self.rows:
            self.status.setText('原图或来源版本已变化，请重新打开讲义图。'); return
        self.search.clear()
        key = (row['batch_id'], row['source_id'])
        source_index = next((i for i in range(self.source.count())
                             if self.source.itemData(i) is not None and tuple(self.source.itemData(i)) == key), None)
        if source_index is None:
            self.status.setText('这份讲义不在当前目录，请重新打开讲义图。'); return
        self.source.setCurrentIndex(source_index)
        if row['bytes_count'] < 4096 and not can_attempt_metafile(row): self.small.setChecked(True)
        for index in range(self.list.count()):
            if self.list.item(index).data(Qt.ItemDataRole.UserRole) == row:
                self.list.setCurrentRow(index); return

    def focus_document(self, row):
        self.search.clear()
        key = (row['batch_id'], row['source_id'])
        source_index = next((i for i in range(self.source.count())
            if self.source.itemData(i) is not None and tuple(self.source.itemData(i)) == key), None)
        if source_index is None:
            self.status.setText('这份讲义不在当前目录，请重新打开讲义图。'); return
        self.source.setCurrentIndex(source_index)
        if not self.rows:
            self.status.setText('这份讲义没有可选原图；可返回知识段继续选择完整题。')

    def read_original(self):
        if self._closed or self._previewed is None or self._context_task is not None: return
        row = deepcopy(self._previewed); epoch = self._epoch
        self.read_button.setEnabled(False); self.status.setText('正在打开这份讲义的完整原文…')
        def ready(value):
            if self._closed or epoch != self._epoch or row != self.current: return
            self._context_task = None; self.read_button.setEnabled(True)
            from .handout_image_reader import HandoutImageReader
            dialog = HandoutImageReader(self.facade, value, self)
            try:
                if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selection is not None:
                    if not self._closed and epoch == self._epoch and row == self.current:
                        self.focus_image(dialog.selection)
                if not self._closed: self.status.setText('填写本页的问题和讲解用途，再加入当前课件。')
            finally: dialog.deleteLater()
        def failed(message):
            if self._closed or epoch != self._epoch or row != self.current: return
            self._context_task = None; self.read_button.setEnabled(True); self.status.setText(message)
        self._context_task = self.tasks.submit('打开讲义原图的完整原文', lambda: handout_image_context(self.facade, row),
            on_success=ready, on_failure=failed)

    def choose(self):
        if self._previewed is None or self._closed or not self.confirm.isEnabled(): return
        title = self.title.text().strip(); purpose = self.purpose.toPlainText().strip()
        if not title or len(purpose) > 500: self.status.setText('请填写本页问题或图题；讲解用途最多500字。'); return
        self.selection = {'source': deepcopy(self._previewed), 'title': title, 'purpose': purpose, 'teacher_only': self.teacher_only.isChecked()}; self.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'split'): return
        narrow = self.width() < 750; self.split.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
        self.preview.image.setFixedHeight(160 if narrow else 230)

    def done(self, result):
        if not self._closed:
            self._closed = True; self._epoch += 1
            if self._task is not None: self.tasks.cancel(self._task); self._task = None
            if self._context_task is not None: self.tasks.cancel(self._context_task); self._context_task = None
            self.preview.close_zoom()
            if self._own_tasks: self.tasks.shutdown(1000)
        super().done(result)

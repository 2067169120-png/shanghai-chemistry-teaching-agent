"""Collect textbook crops and real local photographs in teaching order."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json

from PySide6.QtCore import Qt, QSize, QSignalBlocker
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from ..desktop_composition_image_batch import (prepare_image_batch, validate_image_batch_requests,
    _image_source, _read_image)
from ..desktop_composition_images import read_local_image
from ..desktop_composition_sources import textbook_image_options
from ..desktop_lesson_composition import digest, new_composition, TEACHER_ROLES
from ..desktop_preparation_images import MAX_IMAGES, verify_image_bytes
from ..desktop_saved_preparation_images import target_image_scope
from ..desktop_textbook_preparation_crops import MAX_BATCH_IMAGE_BYTES
from .components import page_scroll
from .preparation_images_widget import _LocalImagePreview
from .teaching_image_picker import TeachingImagePicker
from .textbook_preparation_crop_dialog import TextbookPreparationCropDialog


def _label(text):
    value = QLabel(text); value.setWordWrap(True); value.setMinimumWidth(0)
    value.setTextFormat(Qt.TextFormat.PlainText)
    return value


class _ReadOnlyImages:
    def __init__(self, facade): self.facade = facade
    def __getattr__(self, name): return getattr(self.facade, name)
    def preparation_image_bytes(self, asset): return _read_image(self.facade, asset)


class _QueueCropDialog(TextbookPreparationCropDialog):
    """Return the already displayed pixels beside the sealed request only."""
    def __init__(self, *args, **kwargs):
        self.confirmed_preview = None
        super().__init__(*args, **kwargs)
        self.setWindowTitle('选取教材裁片，加入本批教学顺序')
        self.confirm.setText('将这张裁片放入本批')
        self.cancel.setText('返回本批')

    def _confirm(self):
        if self.confirm.isEnabled(): self.confirmed_preview = deepcopy(self._preview)
        super()._confirm()


class _FullPage(QLabel):
    def __init__(self):
        super().__init__(); self.original = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(240, 160)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setStyleSheet('background: #dce1e8; border: 1px solid #aab3c0;')
        self.setAccessibleName('完整页面预览，保持全页比例且不裁切')

    def set_page(self, image):
        self.original = QPixmap.fromImage(image); self._fit()

    def _fit(self):
        if not self.original.isNull():
            self.setPixmap(self.original.scaled(self.contentsRect().size(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event); self._fit()


class ImageBatchPreview(QDialog):
    """A complete, measured page preview before the one course handoff."""
    def __init__(self, result, image_data, parent=None):
        super().__init__(parent)
        self.result_data, self.image_data = deepcopy(result), dict(image_data)
        self._layout = None; self._seen = set()
        self.setWindowTitle('本批教学图片 · 完整排版预览'); self.resize(1120, 760)
        self.setMinimumSize(420, 480)
        root = QVBoxLayout(self)
        root.addWidget(_label('按本批教学顺序预览完整页面。导学问题显示在左上；教师用途和教师图片按既有范围保留。'))
        bar = QHBoxLayout(); self.format = QComboBox(); self.format.addItems(['16:9', 'A4'])
        self.format.setAccessibleName('本批预览画幅')
        self.previous = QPushButton('上一页'); self.next = QPushButton('下一页')
        self.pages = QComboBox(); self.pages.setMinimumWidth(0)
        self.pages.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.pages.setMinimumContentsLength(12); self.pages.setAccessibleName('本批完整预览页码')
        for control in (self.format, self.previous, self.pages, self.next): bar.addWidget(control)
        bar.setStretch(2, 1); root.addLayout(bar)
        self.page = _FullPage(); root.addWidget(self.page, 1)
        self.status = _label(''); root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm.setText('已查看本批完整预览')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('返回修改')
        buttons.accepted.connect(self._accept_preview); buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.format.currentTextChanged.connect(self._render)
        self.pages.currentIndexChanged.connect(self.show_page)
        self.previous.clicked.connect(lambda: self.pages.setCurrentIndex(self.pages.currentIndex()-1))
        self.next.clicked.connect(lambda: self.pages.setCurrentIndex(self.pages.currentIndex()+1))
        self._render()

    def _render(self, *_):
        from ..desktop_composition_render import arrange_composition, layout_composition
        self._seen.clear(); self.confirm.setEnabled(False)
        try:
            value = new_composition(self.format.currentText()); value['resources'] = deepcopy(self.result_data['resources'])
            for row in value['resources']:
                value['placements'][row['id']] = {'page_id': value['pages'][0], 'x': 12, 'y': 12,
                    'width': 296 if value['format'] == '16:9' else 186,
                    'height': 156 if value['format'] == '16:9' else 273}
            value = arrange_composition(value, self.result_data['image_assets'], self.image_data)
            self._layout = layout_composition(value, self.result_data['image_assets'], self.image_data, student=False)
            if self._layout['errors']: raise ValueError('；'.join(self._layout['errors']))
        except (ValueError, OSError) as exc:
            self._layout = None; self.page.original = QPixmap(); self.page.clear()
            self.status.setText(str(exc)); self.previous.setEnabled(False); self.next.setEnabled(False)
            with QSignalBlocker(self.pages): self.pages.clear()
            return
        with QSignalBlocker(self.pages):
            self.pages.clear()
            for index, page in enumerate(self._layout['pages']): self.pages.addItem(f'第{index+1}页／共{len(self._layout["pages"])}页', page)
        self.show_page(0)

    def show_page(self, index):
        if self._layout is None or not 0 <= index < self.pages.count(): return
        from ..desktop_composition_render import page_image
        page = self.pages.itemData(index)
        try: self.page.set_page(page_image(self._layout, page, scale=3))
        except (ValueError, OSError) as exc:
            self.confirm.setEnabled(False); self.status.setText(str(exc)); return
        self._seen.add(page)
        self.previous.setEnabled(index > 0); self.next.setEnabled(index+1 < self.pages.count())
        self.confirm.setEnabled(len(self._seen) == self.pages.count())
        self.status.setText(f'已查看{len(self._seen)}／{self.pages.count()}页 · {self.format.currentText()}完整页面 · 可返回继续调整问题与顺序。')

    def _accept_preview(self):
        if self.confirm.isEnabled(): self.accept()


class ImageBatchWorkspace(QDialog):
    def __init__(self, facade, tasks, assets, resources, parent=None, *, initial_source='images'):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.assets, self.resources = deepcopy(assets), deepcopy(resources)
        self.selection_requests = None
        self.entries = []; self._index = -1; self._editing = False
        self._closed = self._busy = False; self._epoch = 0; self._task_id = None
        self._child = None; self._preview_stamp = None
        if hasattr(tasks, 'task_cancelled'): tasks.task_cancelled.connect(self._cancelled)
        self.setWindowTitle('本批教学图片'); self.resize(1120, 820); self.setMinimumSize(410, 590)
        root = QVBoxLayout(self)
        root.addWidget(_label('把教材原裁片与真实情境照片放在同一批里，按讲课顺序安排，集中修改左上导学问题与用途，最后一次加入当前课。'))
        self.source_bar = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.textbook = QPushButton('继续选教材原裁片…')
        self.local = QPushButton('多选本机情境照片…')
        self.existing = QPushButton('从本课／已有备课选图…')
        for button in (self.textbook, self.local, self.existing):
            button.setAutoDefault(False); self.source_bar.addWidget(button)
        root.addLayout(self.source_bar)
        self.split = QSplitter(Qt.Orientation.Horizontal); root.addWidget(self.split, 1)
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(_label('本批教学顺序'))
        self.list = QListWidget(); self.list.setIconSize(QSize(96, 68)); self.list.setWordWrap(True)
        self.list.setMinimumWidth(0); self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setAccessibleName('本批教学图片缩略总览与教学顺序')
        left_layout.addWidget(self.list, 1)
        order = QHBoxLayout(); self.up = QPushButton('上移'); self.down = QPushButton('下移'); self.remove = QPushButton('移除')
        for button in (self.up, self.down, self.remove): button.setAutoDefault(False); order.addWidget(button)
        left_layout.addLayout(order)
        self.same_page = QPushButton('从这张原页再裁一片…'); self.same_page.setAutoDefault(False)
        left_layout.addWidget(self.same_page); self.split.addWidget(left)
        editor = QWidget(); fields = QVBoxLayout(editor); fields.setContentsMargins(0, 0, 0, 0)
        self.preview = _LocalImagePreview(self); self.preview.image.setFixedHeight(280); fields.addWidget(self.preview)
        self.context = _label('本批尚无图片。可先选教材裁片或多选本机照片。'); fields.addWidget(self.context)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.title = QLineEdit(); self.title.setMaxLength(160); self.title.setPlaceholderText('例如：观察到的现象怎样解释？')
        self.title.setAccessibleName('本批当前图片左上导学问题或图题')
        self.purpose = QPlainTextEdit(); self.purpose.setFixedHeight(95)
        self.purpose.setPlaceholderText('学生观察哪里、比较什么、用它解释什么')
        self.purpose.setAccessibleName('本批当前图片教学用途')
        self.teacher_only = QCheckBox('仅教师可见')
        form.addRow('左上导学问题／图题', self.title); form.addRow('本次课堂用途', self.purpose); form.addRow(self.teacher_only)
        fields.addLayout(form)
        self.source_button = QPushButton('查看原路径、页码与来源记录'); self.source_button.setCheckable(True)
        self.source_details = QPlainTextEdit(); self.source_details.setReadOnly(True); self.source_details.setFixedHeight(150)
        self.source_details.hide(); fields.addWidget(self.source_button); fields.addWidget(self.source_details)
        self.source_button.toggled.connect(self.source_details.setVisible)
        fields.addStretch(1); self.split.addWidget(page_scroll(editor)); self.split.setSizes([360, 700])
        self.status = _label('本批暂存在工作区；完成预览后，一次加入当前课。'); root.addWidget(self.status)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight); self.preview_button = QPushButton('预览整批 16:9／A4…')
        self.confirm = QPushButton('将本批加入当前课'); self.confirm.setObjectName('PrimaryAction')
        self.cancel = QPushButton('取消本批')
        for button in (self.preview_button, self.confirm, self.cancel): button.setAutoDefault(False); self.actions.addWidget(button)
        root.addLayout(self.actions)
        self.textbook.clicked.connect(self.choose_textbook); self.local.clicked.connect(self.choose_local)
        self.existing.clicked.connect(self.choose_existing)
        self.list.currentRowChanged.connect(self.select_entry)
        self.title.textChanged.connect(self.edit_current); self.purpose.textChanged.connect(self.edit_current)
        self.teacher_only.toggled.connect(self.edit_current)
        self.up.clicked.connect(lambda: self.move_current(-1)); self.down.clicked.connect(lambda: self.move_current(1))
        self.remove.clicked.connect(self.remove_current); self.same_page.clicked.connect(self.crop_same_page)
        self.preview_button.clicked.connect(self.preview_batch); self.confirm.clicked.connect(self.accept_batch)
        self.cancel.clicked.connect(self.reject)
        self._refresh()
        (self.textbook if initial_source == 'textbook' else self.local).setFocus()

    def requests(self): return [deepcopy(entry['request']) for entry in self.entries]

    @staticmethod
    def _context(entry):
        row = entry['request']; source = row.get('selection', row.get('request'))
        text = entry['context'].removesuffix(' · 仅教师')
        return text + (' · 仅教师' if entry['restricted'] or row.get('teacher_only', source.get('teacher_only', False)) else '')

    def _invalidate(self):
        self._preview_stamp = None
        self.status.setText(f'本批{len(self.entries)}项 · 已保留逐图核对，修改顺序或问题后请重看整批预览。')

    def _refresh(self):
        restricted_pixels = {sha256(entry['data']).hexdigest() for entry in self.entries if entry['source_restricted']}
        for entry in self.entries: entry['restricted'] = sha256(entry['data']).hexdigest() in restricted_pixels
        selected = min(max(self._index, 0), len(self.entries)-1)
        with QSignalBlocker(self.list):
            self.list.clear()
            for index, entry in enumerate(self.entries):
                request = entry['request']; source = request.get('selection', request.get('request'))
                title = request.get('title', source.get('title', source.get('caption', '')))
                item = QListWidgetItem(f'{index+1}. {title}\n{self._context(entry)}')
                pixmap = QPixmap(); pixmap.loadFromData(entry['data'])
                item.setIcon(QIcon(pixmap.scaled(96, 68, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
                item.setToolTip(title+'\n'+request.get('purpose', source.get('purpose', '')))
                self.list.addItem(item)
            self.list.setCurrentRow(selected)
        self.select_entry(selected); self._controls()

    def _controls(self):
        active = not self._busy and not self._closed
        selected = active and 0 <= self._index < len(self.entries)
        for button in (self.textbook, self.local, self.existing): button.setEnabled(active and len(self.entries) < MAX_IMAGES)
        for control in (self.list, self.title, self.purpose): control.setEnabled(active if control is self.list else selected)
        self.teacher_only.setEnabled(selected and not self.entries[self._index]['restricted'] if selected else False)
        self.up.setEnabled(selected and self._index > 0); self.down.setEnabled(selected and self._index+1 < len(self.entries))
        self.remove.setEnabled(selected)
        self.same_page.setEnabled(selected and self.entries[self._index]['request']['kind'] == 'textbook' if selected else False)
        self.preview_button.setEnabled(active and bool(self.entries))
        self.confirm.setText(f'将本批{len(self.entries)}项加入当前课')
        self.confirm.setEnabled(active and bool(self.entries) and self._preview_stamp == digest(self.requests()))

    def select_entry(self, index):
        self._index = index; self._editing = True
        try:
            if not 0 <= index < len(self.entries):
                self.preview.clear('先添加本批要用的教材裁片或照片。')
                self.title.clear(); self.purpose.clear(); self.source_details.clear(); self.teacher_only.setChecked(False)
                self.context.setText('本批尚无图片。可先选教材裁片或多选本机照片。')
                return
            entry = self.entries[index]; request = entry['request']; source = request.get('selection', request.get('request'))
            title = request.get('title', source.get('title', source.get('caption', '')))
            self.title.setText(title); self.purpose.setPlainText(request.get('purpose', source.get('purpose', '')))
            self.teacher_only.setChecked(entry['restricted'] or request.get('teacher_only', source.get('teacher_only', False)))
            self.preview.set_bytes(entry['data'], title); self.context.setText(self._context(entry)+' · 已核对图片，回看与排序保留确认。')
            self.source_details.setPlainText(json.dumps(entry['evidence'], ensure_ascii=False, indent=2))
        finally:
            self._editing = False; self._controls()

    def edit_current(self, *_):
        if self._editing or not 0 <= self._index < len(self.entries): return
        row = self.entries[self._index]['request']
        row.update(title=self.title.text(), purpose=self.purpose.toPlainText(), teacher_only=self.teacher_only.isChecked())
        item = self.list.item(self._index)
        item.setText(f'{self._index+1}. {self.title.text()}\n{self._context(self.entries[self._index])}')
        self.context.setText(self._context(self.entries[self._index])+' · 已核对图片，回看与排序保留确认。')
        self._invalidate(); self._controls()

    def _append(self, additions):
        if len(self.entries)+len(additions) > MAX_IMAGES: raise ValueError(f'本批最多{MAX_IMAGES}个图片用途。')
        # Multiple teaching uses retain their own source records while sharing
        # immutable pixels. Capacity is a pixel budget, not a use-count budget.
        pixels = {sha256(row['data']).hexdigest(): row['data'] for row in [*self.entries, *additions]}
        if sum(map(len, pixels.values())) > MAX_BATCH_IMAGE_BYTES:
            raise ValueError('本批图片总量过大，请减少图片或缩小裁片。')
        for row in [*self.entries, *additions]: row['data'] = pixels[sha256(row['data']).hexdigest()]
        self.entries.extend(additions); self._index = len(self.entries)-len(additions)
        self._invalidate(); self._refresh()

    def enqueue_image(self, selection, *, append=True):
        asset, data, roles, binding = _image_source(self.facade, selection, self.assets, self.resources)
        target_roles, question = target_image_scope(self.resources, asset)
        if question: raise ValueError('原题图片请从“完整题…”加入，保留题面与答案范围。')
        restricted = bool(set([*roles, *target_roles]) & TEACHER_ROLES)
        request = {'kind': 'image', 'selection': deepcopy(selection)}
        context = ('已有备课' if binding else '本机照片' if 'local' in selection else '当前课图片')
        if restricted or selection['teacher_only']: context += ' · 仅教师'
        entry = {'request': request, 'data': data, 'restricted': restricted, 'source_restricted': restricted, 'context': context,
            'evidence': {'image_asset': asset, 'selection': deepcopy(selection)}}
        if append: self._append([entry])
        return entry

    def enqueue_textbook(self, request, preview):
        validate_image_batch_requests([{'kind': 'textbook', 'request': request}])
        verify_image_bytes(preview['image_asset'], preview['image_bytes'])
        if (preview['preview_revision'] != request['expected_preview_revision']
                or preview['image_asset']['sha256'] != request['expected_image_sha256']):
            raise ValueError('裁片与所确认原页不一致，请重新预览。')
        origin = preview['provenance']
        roles, question = target_image_scope(self.resources, preview['image_asset'])
        if question: raise ValueError('原题图片请从“完整题…”加入，保留题面与答案范围。')
        restricted = bool(set(roles) & TEACHER_ROLES)
        context = f'{origin["source_title"]} · PDF第{origin["pdf_page"]}页'
        self._append([{'request': {'kind': 'textbook', 'request': deepcopy(request)},
            'data': preview['image_bytes'], 'restricted': restricted, 'source_restricted': restricted,
            'context': context, 'evidence': deepcopy(origin)}])

    def choose_local(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '多选本机情境照片', '', '图像文件 (*.png *.jpg *.jpeg *.webp)')
        if not paths: return
        try: self.add_local_paths(paths)
        except (ValueError, OSError) as exc: self.status.setText(str(exc))

    def add_local_paths(self, paths):
        additions = []
        for name in paths:
            data = read_local_image(name); title = Path(name).stem[:160]
            request = {'file_path': str(name), 'sha256': sha256(data).hexdigest(), 'caption': title,
                'source': str(Path(name).absolute()), 'purpose': '观察图片并说明与本节内容的联系。'}
            choice = {'local': request, 'title': title, 'purpose': request['purpose'], 'teacher_only': False}
            additions.append(self.enqueue_image(choice, append=False))
        self._append(additions)

    def choose_existing(self):
        dialog = TeachingImagePicker(_ReadOnlyImages(self.facade), self.assets, self.resources, self, tasks=self.tasks)
        dialog.setWindowTitle('选图片放入本批教学顺序'); dialog.confirm_button.setText('将此图片放入本批')
        self._child = dialog
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selection: self.enqueue_image(dialog.selection)
        except (ValueError, OSError) as exc: self.status.setText(str(exc))
        finally: self._child = None; dialog.deleteLater()

    def _run(self, label, operation, ready):
        if self._busy or self._closed: return
        self._busy = True; self._epoch += 1; epoch = self._epoch; self._controls(); self.status.setText(label)
        def succeeded(value):
            if self._closed or epoch != self._epoch:
                if hasattr(value, 'discard'): value.discard()
                return
            self._task_id = None; self._busy = False
            try: ready(value)
            except (ValueError, OSError) as exc: self.status.setText(str(exc))
            self._controls()
        def failed(message):
            if self._closed or epoch != self._epoch: return
            self._task_id = None; self._busy = False; self.status.setText(message); self._controls()
        try: self._task_id = self.tasks.submit(label, operation, on_success=succeeded, on_failure=failed)
        except RuntimeError as exc: failed(str(exc))

    def choose_textbook(self):
        def choose(snapshot):
            from .lesson_composition_panel import ResourcePicker
            rows = snapshot['assets'] + [{**book, 'origin': 'personal', 'label': '选择PDF原页'} for book in snapshot['local_books']]
            dialog = ResourcePicker(rows, '选择教材图或原页', lambda row:
                row['source_name']+' · 个人教材' if row.get('origin') == 'personal'
                else row['volume_title']+' · '+row['label']+f' · PDF {row["pdf_page"]}', self, visual_facade=self.facade)
            self._child = dialog
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted: return
                row = deepcopy(dialog.chosen)
            finally: self._child = None; dialog.deleteLater()
            if row.get('origin') == 'personal':
                from ..desktop_personal_textbook_crops import book_page_selection
                page, ok = QInputDialog.getInt(self, '选择教材原页', 'PDF文件页码', 1, 1, row['page_count'])
                if not ok: return
                selection = book_page_selection(row, page)
            else: selection = {key: row[key] for key in ('visual_asset_id', 'revision')}
            self._crop(selection)
        self._run('正在读取教材目录…', lambda: textbook_image_options(self.facade), choose)

    def _crop(self, selection):
        dialog = _QueueCropDialog(self.facade, self.tasks, selection, self); self._child = dialog
        try:
            if (dialog.exec() == QDialog.DialogCode.Accepted and dialog.selection_request
                    and dialog.confirmed_preview is not None): self.enqueue_textbook(dialog.selection_request, dialog.confirmed_preview)
        except (ValueError, OSError) as exc: self.status.setText(str(exc))
        finally: self._child = None; dialog.deleteLater()

    def crop_same_page(self):
        if 0 <= self._index < len(self.entries) and self.entries[self._index]['request']['kind'] == 'textbook':
            self._crop(deepcopy(self.entries[self._index]['request']['request']['selection']))

    def move_current(self, step):
        target = self._index + step
        if not 0 <= target < len(self.entries): return
        self.entries[self._index], self.entries[target] = self.entries[target], self.entries[self._index]
        self._index = target; self._invalidate(); self._refresh()

    def remove_current(self):
        if not 0 <= self._index < len(self.entries): return
        self.entries.pop(self._index); self._index = min(self._index, len(self.entries)-1)
        self._invalidate(); self._refresh()

    def preview_batch(self):
        try: requests = validate_image_batch_requests(self.requests())
        except ValueError as exc: self.status.setText(str(exc)); return
        stamp = digest(requests)
        def ready(publication):
            dialog = None
            try:
                if not publication.result['resources']:
                    self.status.setText('这些图片与课堂用途已在本课中，请调整问题或用途后再加入。'); return
                dialog = ImageBatchPreview(publication.result, publication.image_data, self); self._child = dialog
                if dialog.exec() == QDialog.DialogCode.Accepted and stamp == digest(self.requests()):
                    self._preview_stamp = stamp
                    self.status.setText(f'已完整预览本批{len(self.entries)}项。确认后一次加入当前课，可一次撤销。')
            finally:
                self._child = None
                if dialog is not None: dialog.deleteLater()
                publication.discard()
        self._run('正在逐张核对本批原图与排版…',
            lambda: prepare_image_batch(self.facade, self.assets, self.resources, requests), ready)

    def accept_batch(self):
        if not self.confirm.isEnabled() or self._preview_stamp != digest(self.requests()): return
        self.selection_requests = validate_image_batch_requests(self.requests()); self.accept()

    def _cancelled(self, task_id, _label):
        if task_id != self._task_id or self._closed: return
        self._task_id = None; self._busy = False; self._epoch += 1
        self.status.setText('已停止本次读取，本批选择仍保留。'); self._controls()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'split'):
            narrow = event.size().width() < 760
            self.split.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
            self.source_bar.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
            self.actions.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
            self.preview.image.setFixedHeight(160 if narrow else 280)

    def done(self, result):
        if not self._closed:
            self._closed = True; self._epoch += 1
            if self._task_id is not None: self.tasks.cancel(self._task_id); self._task_id = None
            if self._child is not None: self._child.reject()
            self.preview.close_zoom()
            if result != QDialog.DialogCode.Accepted: self.selection_requests = None
        super().done(result)

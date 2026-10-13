"""Preview an existing or local picture, then explicitly add a classroom use."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from datetime import datetime
import json

from PySide6.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QStyledItemDelegate, QVBoxLayout, QWidget)
from ..desktop_composition_images import image_options, read_local_image, image_roles, teacher_image
from ..desktop_preparation_images import image_info, verify_image_bytes
from .preparation_images_widget import _LocalImagePreview, PreparationImageMetadataDialog
from .components import page_scroll
from .tasks import DesktopTaskBridge
from .preview_navigation import PixmapCache
from ..reader_cancellation import check_read_cancelled


THUMBNAIL_SIZE = QSize(96, 64)
THUMBNAIL_CACHE_BYTES = 2 * 1024 * 1024
THUMBNAIL_CACHE_ENTRIES = 64


class _ThumbnailItemDelegate(QStyledItemDelegate):
    """Keep the wrapped question inside the viewport beside its fixed icon."""
    def sizeHint(self, option, index):
        width = max(1, self.parent().viewport().width())
        text_width = max(1, width - THUMBNAIL_SIZE.width() - 28)
        height = option.fontMetrics.boundingRect(QRect(0, 0, text_width, 0),
            int(Qt.TextFlag.TextWordWrap), str(index.data(Qt.ItemDataRole.DisplayRole) or '')).height()
        return QSize(width, max(THUMBNAIL_SIZE.height(), height)+12)


def _read_thumbnail(facade, row):
    """Decode one verified visible picture off the GUI thread; return QImage."""
    check_read_cancelled()
    data = read_local_image(row['local']['file_path']) if 'local' in row else facade.preparation_image_bytes(row['asset'])
    verify_image_bytes(row['asset'], data); check_read_cancelled()
    buffer = QBuffer(); buffer.setData(QByteArray(data)); buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setScaledSize(reader.size().scaled(THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read(); check_read_cancelled()
    if image.isNull(): raise ValueError('图片无法解码。')
    return image.scaled(THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _saved_time(row):
    value = row.get('created_at', '')
    try: return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, AttributeError): return str(value)


class TeachingImagePicker(QDialog):
    def __init__(self, facade, assets, resources, parent=None, *, tasks=None, thumbnail_tasks=None):
        super().__init__(parent)
        self.facade = facade; self.resources = deepcopy(resources)
        self.choices = [{**r, 'identity': r['asset']['asset_id']} for r in image_options(assets, resources)]
        self.current = None; self.edits = {}; self.selection = None; self.loading = False
        self.tasks = tasks; self._own_tasks = tasks is None
        self._saved_task = None; self._saved_epoch = 0; self._closed = False; self._saved_dialog = None
        self._thumbnail_tasks = thumbnail_tasks; self._own_thumbnail_tasks = thumbnail_tasks is None
        self._thumbnail_task = None; self._thumbnail_epoch = 0; self._thumbnail_failed = set()
        self._thumbnail_view = None; self._thumbnail_attempted = set()
        self._thumbnails = PixmapCache(THUMBNAIL_CACHE_BYTES, THUMBNAIL_CACHE_ENTRIES)
        placeholder = QPixmap(THUMBNAIL_SIZE); placeholder.fill(Qt.GlobalColor.transparent)
        self._thumbnail_placeholder = QIcon(placeholder)
        self._thumbnail_timer = QTimer(self); self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.timeout.connect(self._load_visible_thumbnail)
        self.setWindowTitle('选择课外／本机配图'); self.resize(1000, 760)
        self.setMinimumSize(360, 480)
        root = QVBoxLayout(self)
        heading = QLabel('选图、预览，再安排课堂用途'); heading.setObjectName('CardTitle'); root.addWidget(heading)
        hint = QLabel('选择教材、讲义或课外图片，作为新的素材页加入课件。加入后可调整位置、用途及点击显示。')
        hint.setWordWrap(True); root.addWidget(hint)
        self.split = QSplitter(Qt.Orientation.Horizontal); root.addWidget(self.split, 1)
        left = QWidget(); side = QVBoxLayout(left); side.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜索图题或用途'); self.search.setAccessibleName('搜索本课教学图片')
        self.scope = QComboBox(); self.scope.addItems(['全部图片', '课堂可见', '仅教师可见'])
        side.addWidget(self.search); side.addWidget(self.scope)
        self.list = QListWidget(); self.list.setAccessibleName('本课教学图片列表'); self.list.setMinimumSize(0, 72)
        self.list.setWordWrap(True); self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setIconSize(THUMBNAIL_SIZE)
        self.list.setItemDelegate(_ThumbnailItemDelegate(self.list))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); side.addWidget(self.list, 1)
        self.list.viewport().installEventFilter(self)
        self.list.verticalScrollBar().valueChanged.connect(self._schedule_thumbnails)
        self.import_button = QPushButton('从本机导入图片…'); side.addWidget(self.import_button)
        self.saved_button = QPushButton('从已有备课选图片…'); side.addWidget(self.saved_button)
        self.saved_button.setEnabled((callable(getattr(facade, 'search_preparation_drafts', None))
            or callable(getattr(facade, 'preparation_draft_options', None))) and callable(getattr(facade, 'load_preparation_draft', None)))
        self.split.addWidget(left)
        editor = QWidget(); form_root = QVBoxLayout(editor); form_root.setContentsMargins(0, 0, 0, 0)
        self.preview = _LocalImagePreview(self); self.preview.image.setFixedHeight(250); form_root.addWidget(self.preview)
        self.dimensions = QLabel(''); self.dimensions.setWordWrap(True); form_root.addWidget(self.dimensions)
        fields = QFormLayout(); fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.title = QLineEdit(); self.title.setPlaceholderText('例如：这张图里的现象怎样解释？'); self.title.setMaxLength(160)
        self.title.setAccessibleName('本次课堂导学问题或图题')
        self.title.setToolTip('显示在课页左上的导学问题或图题；可用“为什么”“怎样”等问题引导观察。')
        self.purpose = QPlainTextEdit(); self.purpose.setPlaceholderText('学生看哪里、比较什么、用它解释什么'); self.purpose.setFixedHeight(85)
        self.purpose.setAccessibleName('本次图片课堂用途')
        self.teacher_only = QCheckBox('仅教师可见')
        fields.addRow('导学问题／图题', self.title); fields.addRow('课堂用途', self.purpose); fields.addRow(self.teacher_only)
        form_root.addLayout(fields)
        self.source_button = QPushButton('查看图片来源记录'); self.source_button.setCheckable(True); form_root.addWidget(self.source_button)
        self.source_details = QPlainTextEdit(); self.source_details.setReadOnly(True); self.source_details.setFixedHeight(110)
        self.source_details.setVisible(False); form_root.addWidget(self.source_details)
        self.source_button.toggled.connect(self.source_details.setVisible)
        self.split.addWidget(page_scroll(editor)); self.split.setSizes([300, 680])
        self.status = QLabel(''); self.status.setWordWrap(True); root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm_button = buttons.button(QDialogButtonBox.StandardButton.Ok); self.confirm_button.setText('加入课件')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept_choice); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.search.textChanged.connect(self.filter_rows); self.scope.currentIndexChanged.connect(self.filter_rows)
        self.list.currentItemChanged.connect(self.show_choice); self.import_button.clicked.connect(self.choose_local)
        self.saved_button.clicked.connect(self.choose_saved)
        self.filter_rows()

    def save_edits(self):
        if self.current is not None:
            self.edits[self.current['identity']] = {'title': self.title.text(), 'purpose': self.purpose.toPlainText(),
                'teacher_only': self.teacher_only.isChecked()}

    def filter_rows(self, *_):
        self.save_edits(); identity = self.current['identity'] if self.current else None
        self._invalidate_thumbnail_read()
        term = self.search.text().strip().casefold(); scope = self.scope.currentIndex()
        self.loading = True; self.list.clear(); self.current = None
        selected = 0
        for row in self.choices:
            if (scope == 1 and (row['teacher_only'] or row.get('via_question'))) or (scope == 2 and not row['teacher_only']): continue
            values = self.edits.get(row['identity'], row)
            searchable = ' '.join((row['asset']['caption'], row['title'], row['purpose'],
                                   row.get('saved_title', ''), values['title'], values['purpose']))
            if term and term not in searchable.casefold(): continue
            title = values['title'].strip() or row['title'] or row['asset']['caption']
            context = []
            if row['teacher_only']: context.append('仅教师')
            if row.get('via_question'): context.append('完整题入口')
            if 'local' in row: context.append('本机')
            if 'saved_preparation' in row: context.append('已有备课')
            label = title + ('\n' + ' · '.join(context) if context else '')
            item = QListWidgetItem(label); item.setData(Qt.ItemDataRole.UserRole, row['identity'])
            item.setData(Qt.ItemDataRole.UserRole+1, row['asset']['sha256']); item.setIcon(self._thumbnail_placeholder)
            details = [title]
            if row.get('saved_title'): details.append('来源课程：' + row['saved_title'])
            details.extend(('原图题：' + row['asset']['caption'], '课堂用途：' + values['purpose']))
            item.setToolTip('\n'.join(details)); self.list.addItem(item)
            if row['identity'] == identity: selected = self.list.count()-1
        self.loading = False
        if self.list.count(): self.list.setCurrentRow(selected)
        else:
            self.preview.clear('暂无符合条件的图片。可调整搜索或从本机导入。')
            self.dimensions.clear(); self.confirm_button.setEnabled(False)
        self._schedule_thumbnails()

    def _invalidate_thumbnail_read(self):
        self._thumbnail_epoch += 1; self._thumbnail_timer.stop()
        self._thumbnail_view = None; self._thumbnail_attempted.clear()
        if self._thumbnail_task is not None:
            self._thumbnail_tasks.cancel(self._thumbnail_task); self._thumbnail_task = None

    def _schedule_thumbnails(self, *_):
        if not self._closed: self._thumbnail_timer.start(0)

    def eventFilter(self, watched, event):
        if watched is self.list.viewport() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._schedule_thumbnails()
        return super().eventFilter(watched, event)

    def _visible_thumbnail_items(self):
        if not self.list.isVisible(): return []
        area = self.list.viewport().rect()
        return [self.list.item(i) for i in range(self.list.count())
                if self.list.visualItemRect(self.list.item(i)).intersects(area)]

    def _load_visible_thumbnail(self):
        if self._closed: return
        visible = self._visible_thumbnail_items(); identities = {i.data(Qt.ItemDataRole.UserRole) for i in visible}
        if identities != self._thumbnail_view:
            self._thumbnail_view = identities; self._thumbnail_attempted.clear()
        # List icons also retain pixmaps. Keep non-visible icons empty so the
        # cache budget covers all decoded thumbnail copies owned by this dialog.
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) not in identities: item.setIcon(self._thumbnail_placeholder)
        missing = None
        for item in visible:
            key = item.data(Qt.ItemDataRole.UserRole+1); pixmap = self._thumbnails.get(key)
            if pixmap is not None: item.setIcon(QIcon(pixmap))
            elif (item.data(Qt.ItemDataRole.UserRole) not in self._thumbnail_failed
                    and item.data(Qt.ItemDataRole.UserRole) not in self._thumbnail_attempted and missing is None): missing = item
        if missing is None or self._thumbnail_task is not None: return
        identity = missing.data(Qt.ItemDataRole.UserRole); key = missing.data(Qt.ItemDataRole.UserRole+1)
        row = deepcopy(next(r for r in self.choices if r['identity'] == identity)); epoch = self._thumbnail_epoch
        self._thumbnail_attempted.add(identity)
        if self._thumbnail_tasks is None: self._thumbnail_tasks = DesktopTaskBridge(self)
        def ready(image):
            if self._closed or epoch != self._thumbnail_epoch: return
            self._thumbnail_task = None
            # Scrolling can hide a picture while it is decoded. Discard it.
            if identity not in {i.data(Qt.ItemDataRole.UserRole) for i in self._visible_thumbnail_items()}:
                self._schedule_thumbnails(); return
            pixmap = QPixmap.fromImage(image); evicted = self._thumbnails.put(key, pixmap)
            for i in range(self.list.count()):
                item = self.list.item(i)
                if item.data(Qt.ItemDataRole.UserRole+1) in evicted: item.setIcon(self._thumbnail_placeholder)
            self._schedule_thumbnails()
        def failed(_message):
            if self._closed or epoch != self._thumbnail_epoch: return
            # Failure belongs to this read source, not its pixel SHA. Another
            # alias can have valid stored bytes even if a local path vanished.
            self._thumbnail_task = None; self._thumbnail_failed.add(identity); self._schedule_thumbnails()
        self._thumbnail_task = self._thumbnail_tasks.submit('读取可见图片缩略图', lambda:_read_thumbnail(self.facade, row),
            on_success=ready, on_failure=failed)

    def show_choice(self, item, _previous=None):
        if self.loading or item is None: return
        self.save_edits()
        identity = item.data(Qt.ItemDataRole.UserRole)
        row = next(r for r in self.choices if r['identity'] == identity); self.current = row
        values = self.edits.get(identity, row)
        self.title.setText(values['title']); self.purpose.setPlainText(values['purpose'])
        self.teacher_only.setChecked(row['teacher_only'] or values['teacher_only'])
        self.teacher_only.setEnabled(not row['teacher_only'])
        self.status.setText('原素材图片仅教师可见，本次保持这一范围。' if row['teacher_only'] else '')
        details = {'image_asset': row['asset'], 'saved_preparation': row['saved_preparation']} if 'saved_preparation' in row else row['asset']
        self.source_details.setPlainText(json.dumps(details, ensure_ascii=False, indent=2))
        try:
            if 'local' in row:
                data = read_local_image(row['local']['file_path'])
                if sha256(data).hexdigest() != row['asset']['sha256']: raise ValueError('图片已变化，请重新选择。')
            else: data = self.facade.preparation_image_bytes(row['asset'])
            verify_image_bytes(row['asset'], data); self.preview.set_bytes(data, row['asset']['caption'])
            self.dimensions.setText(f"完整原图 · {row['asset']['width']} × {row['asset']['height']} 像素")
            self.confirm_button.setEnabled(not row.get('via_question', False))
            if row.get('via_question'):
                self.status.setText('原题图片请从“完整题…”加入，保留题面与答案范围。')
        except Exception:
            self.preview.clear('图片暂时无法读取。请重新选择。'); self.confirm_button.setEnabled(False)

    def choose_local(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择本机教学图片', '', '图像文件 (*.png *.jpg *.jpeg *.webp)')
        if not path: return
        try:
            data = read_local_image(path)
            dialog = PreparationImageMetadataDialog(path, self, caption=Path(path).stem[:160],
                source='本机图片：'+Path(path).name, preview_bytes=data, classroom_only=True)
            if dialog.exec() != QDialog.DialogCode.Accepted: dialog.deleteLater(); return
            request = {'file_path':path, 'sha256':sha256(data).hexdigest(), 'caption':dialog.caption,
                       'source':dialog.source, 'purpose':dialog.purpose}
            dialog.deleteLater()
            asset = {k:request[k] for k in ('sha256', 'caption', 'source', 'purpose')}
            asset.update({'asset_id':'IMG-'+asset['sha256'], **image_info(data)})
            roles = image_roles(self.resources, asset['asset_id'])
            row = {'identity':'LOCAL-'+asset['sha256'], 'asset':asset, 'local':request,
                   'source_roles':roles, 'teacher_only':teacher_image(roles), 'title':asset['caption'], 'purpose':asset['purpose']}
            self.save_edits(); self.choices = [r for r in self.choices if r['identity'] != row['identity']]+[row]
            self.current = row; self.title.setText(row['title']); self.purpose.setPlainText(row['purpose'])
            self.teacher_only.setChecked(row['teacher_only'])
            self.search.blockSignals(True); self.scope.blockSignals(True)
            self.search.clear(); self.scope.setCurrentIndex(0)
            self.search.blockSignals(False); self.scope.blockSignals(False); self.filter_rows()
        except (ValueError, OSError) as exc: self.status.setText(str(exc))

    def choose_saved(self):
        if self._closed or self._saved_task is not None: return
        from ..desktop_saved_preparation_images import (saved_preparation_image_options, saved_preparation_images,
                                                       saved_preparation_image_catalog)
        from .lesson_composition_panel import ResourcePicker
        class SavedCoursePicker(ResourcePicker):
            def show_row(self, item, *_):
                if item is None: self.detail.clear(); return
                row = item.data(Qt.ItemDataRole.UserRole)
                self.detail.setPlainText(row['title']+'\n\n保存时间：'+_saved_time(row)
                    +f"\n\n图片：{row.get('image_count', 0)}张\n\n选择后预览原图，填写本课的问题与讲解用途。")
        if self.tasks is None: self.tasks = DesktopTaskBridge(self)
        self._saved_epoch += 1; epoch = self._saved_epoch
        self.saved_button.setEnabled(False); self.status.setText('正在读取已有备课…')
        def failed(message):
            if self._closed or epoch != self._saved_epoch: return
            self._saved_task = None; self.saved_button.setEnabled(True); self.status.setText(message)
        def images_ready(rows):
            if self._closed or epoch != self._saved_epoch: return
            self._saved_task = None; self.saved_button.setEnabled(True)
            self.save_edits(); imported = []
            for source in rows:
                row = deepcopy(source); binding = row['saved_preparation']
                row['identity'] = 'SAVED-'+binding['draft_id']+'-'+binding['draft_revision']+'-'+row['asset']['asset_id']
                aliases = [r for r in self.choices if r['asset']['sha256'] == row['asset']['sha256']]
                if any(r['teacher_only'] for r in aliases): row['teacher_only'] = True
                if any(r.get('via_question') for r in aliases): row['via_question'] = True
                self.choices = [r for r in self.choices if r['identity'] != row['identity']]+[row]; imported.append(row)
            if not imported:
                self.status.setText('这份备课没有可用图片。可选择其他备课。'); return
            self.search.blockSignals(True); self.scope.blockSignals(True)
            self.search.clear(); self.scope.setCurrentIndex(0)
            self.search.blockSignals(False); self.scope.blockSignals(False); self.filter_rows()
            for index in range(self.list.count()):
                if self.list.item(index).data(Qt.ItemDataRole.UserRole) == imported[0]['identity']:
                    self.list.setCurrentRow(index); break
            if not self.current.get('via_question') and not self.current['teacher_only']:
                self.status.setText('填写本课的引导问题和讲解用途，再加入课件。')
        def courses_ready(courses):
            if self._closed or epoch != self._saved_epoch: return
            self._saved_task = None
            if not courses:
                self.saved_button.setEnabled(True); self.status.setText('暂无已保存的备课。'); return
            dialog = SavedCoursePicker(courses, '从最近备课选图片',
                lambda r:r['title']+' · '+_saved_time(r)+f" · {r.get('image_count', 0)}图", self,
                intro='选择一份已有备课，再预览其中的图片，安排本课的问题与讲解用途。')
            dialog.list.setWordWrap(True)
            dialog.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._saved_dialog = dialog
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    if not self._closed: self.saved_button.setEnabled(True); self.status.setText('可继续选择本课图片。')
                    return
                if self._closed or epoch != self._saved_epoch: return
                course = deepcopy(dialog.chosen); self.status.setText('正在读取所选备课的图片…')
                self._saved_task = self.tasks.submit('读取已有备课图片',
                    lambda: saved_preparation_images(self.facade, course['draft_id'], course['revision']),
                    on_success=images_ready, on_failure=failed)
            finally:
                self._saved_dialog = None; dialog.deleteLater()
        if callable(getattr(self.facade, 'search_preparation_drafts', None)):
            from .saved_image_course_picker import SavedImageCoursePicker
            dialog = SavedImageCoursePicker(self.facade, self, tasks=self.tasks); self._saved_dialog = dialog
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    if not self._closed: self.saved_button.setEnabled(True); self.status.setText('可继续选择本课图片。')
                    return
                if self._closed or epoch != self._saved_epoch: return
                course = deepcopy(dialog.chosen); self.status.setText('正在读取所选备课的图片…')
                self._saved_task = self.tasks.submit('读取已有备课图片',
                    lambda:saved_preparation_image_catalog(self.facade, course['draft_id'], course['revision']),
                    on_success=images_ready, on_failure=failed)
            finally:
                self._saved_dialog = None; dialog.deleteLater()
            return
        self._saved_task = self.tasks.submit('读取最近备课', lambda:saved_preparation_image_options(self.facade),
            on_success=courses_ready, on_failure=failed)

    def accept_choice(self):
        if self.current is None or not self.confirm_button.isEnabled(): return
        title = self.title.text().strip(); purpose = self.purpose.toPlainText().strip()
        if not title or len(title) > 160 or len(purpose) > 500:
            self.status.setText('请填写课堂图题；课堂用途最多500字。'); return
        choice = {'title': title, 'purpose': purpose, 'teacher_only': self.teacher_only.isChecked()}
        if 'local' in self.current: choice['local'] = deepcopy(self.current['local'])
        elif 'saved_preparation' in self.current: choice['saved_preparation'] = deepcopy(self.current['saved_preparation'])
        else: choice.update({'asset':deepcopy(self.current['asset']), 'source_roles':deepcopy(self.current['source_roles'])})
        self.selection = choice; self.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'split'): return
        narrow = self.width() < 700
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if self.split.orientation() != orientation:
            self.split.setOrientation(orientation); self.split.setSizes([160, 520] if narrow else [300, 680])
        self.preview.image.setFixedHeight(160 if narrow else 250)
        self._schedule_thumbnails()

    def done(self, result):
        if not self._closed:
            self._closed = True; self._saved_epoch += 1
            self._invalidate_thumbnail_read(); self._thumbnails.clear()
            for i in range(self.list.count()): self.list.item(i).setIcon(QIcon())
            if self._saved_task is not None and self.tasks is not None: self.tasks.cancel(self._saved_task); self._saved_task = None
            if self._saved_dialog is not None: self._saved_dialog.reject()
            self.preview.close_zoom()
            if self._own_tasks and self.tasks is not None: self.tasks.shutdown(1000)
            if self._own_thumbnail_tasks and self._thumbnail_tasks is not None: self._thumbnail_tasks.shutdown(1000)
        super().done(result)

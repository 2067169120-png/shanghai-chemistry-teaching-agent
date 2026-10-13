"""Choose a lecture once, read it, then hand off an explicit mixed selection."""
from copy import deepcopy

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QComboBox, QDialog,
    QFormLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget)
from shiboken6 import isValid

from ..desktop_handout_knowledge import handout_knowledge_sources, handout_knowledge_outline
from ..desktop_handout_sources import handout_source_context, validate_handout_source
from ..desktop_handout_images import handout_image_preview
from ..desktop_handout_questions import handout_question_facade
from ..desktop_question_classroom_copy import printed_source_span
from .components import page_scroll
from .handout_knowledge_picker import HandoutKnowledgePicker, _source_valid
from .handout_image_picker import HandoutImagePicker
from .tasks import DesktopTaskBridge
from .word_lesson_reader import WordLessonReader
from .word_source_display import section_display_title, section_display_summary
from .word_question_dialog import WordQuestionDialog


SCHEMA = 'shchem.handout-material-request.v1'


def _label(text=''):
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    return value


def _identity(source):
    return tuple((source or {}).get(key) for key in
        ('batch_id', 'source_id', 'source_name', 'source_sha256', 'source_role'))


def _document_matches(source, document):
    if not isinstance(source, dict) or not isinstance(document, dict):
        return False
    keys = ('batch_id', 'source_id', 'source_name', 'source_path', 'source_sha256', 'source_revision')
    return (all(source.get(key) == document.get(key) for key in keys)
        and ('source_role' not in source or source['source_role'] == document['source_role']))


class _MaterialMetadataDialog(QDialog):
    """Edit classroom choices already in memory, without reading the source again."""
    def __init__(self, entry, *, answer_source=False, parent=None):
        super().__init__(parent)
        self.metadata = None
        self.kind = entry['kind']
        if self.kind not in ('knowledge', 'image', 'question'):
            raise ValueError('请在知识栏目或原图选材中修改本课问题与用途。')
        self.setWindowTitle('编辑所选素材')
        self.resize(540, 400)
        self.setMinimumSize(360, 300)
        root = QVBoxLayout(self)
        root.addWidget(_label(entry['label']))
        body = QWidget()
        form = QFormLayout(body)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        title = entry['value'].get('title', '')
        if self.kind == 'question' and 'title' not in entry['value']:
            title = entry['label'].removeprefix('完整题与答案 · ')
            span = printed_source_span(title)
            if span is not None:
                title = title[:span[0]] + title[span[1]:]
        self.title = QLineEdit(title)
        self.title.setMaxLength(160)
        self.title.setMinimumWidth(0)
        self.title.setPlaceholderText('例如：怎样根据结构判断材料的性能？' if self.kind == 'question'
                                      else '例如：这张图能解释哪一个现象？')
        self.title.setAccessibleName('所选素材的本课引导问题')
        form.addRow('本课引导问题', self.title)
        if self.kind == 'image':
            self.purpose = QPlainTextEdit(entry['value']['purpose'])
            self.purpose.setMinimumWidth(0)
            self.purpose.setMinimumHeight(90)
            self.purpose.setPlaceholderText('学生观察什么、联系哪些知识、记下哪些解释')
            self.purpose.setAccessibleName('所选原图的讲解用途')
            form.addRow('讲解用途', self.purpose)
        self.teacher_only = None
        if self.kind != 'question':
            self.teacher_only = QCheckBox('仅教师可见')
            self.teacher_only.setChecked(answer_source or entry['value']['teacher_only'])
            self.teacher_only.setEnabled(not answer_source)
            form.addRow('适用范围', self.teacher_only)
        if answer_source and self.kind != 'question':
            form.addRow(_label('答案讲义中的素材用于教师讲解。'))
        self.content_scroll = page_scroll(body)
        root.addWidget(self.content_scroll, 1)
        self.status = _label()
        root.addWidget(self.status)
        self.buttons = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.cancel_button = QPushButton('取消')
        self.confirm = QPushButton('保存本次修改')
        self.confirm.setObjectName('PrimaryAction')
        for button in (self.cancel_button, self.confirm):
            button.setMinimumWidth(0)
            button.setAutoDefault(False)
            self.buttons.addWidget(button)
        root.addLayout(self.buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm.clicked.connect(self.choose)

    def choose(self):
        title = self.title.text().strip()
        if not title:
            self.status.setText('请填写本课引导问题。')
            return
        metadata = {'title': title}
        if self.kind != 'question':
            metadata['teacher_only'] = self.teacher_only.isChecked()
        if self.kind == 'image':
            purpose = self.purpose.toPlainText().strip()
            if len(purpose) > 500:
                self.status.setText('讲解用途最多500字，请缩短后保存。')
                return
            metadata['purpose'] = purpose
        self.metadata = metadata
        self.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'buttons'):
            self.buttons.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 460
                                      else QBoxLayout.Direction.LeftToRight)


class _BoundImagePicker(HandoutImagePicker):
    """An answer document remains teacher-only while selecting original figures."""
    def show_row(self, item, *_):
        super().show_row(item)
        if (self.document_source or {}).get('source_role') == 'answer':
            self.teacher_only.setChecked(True)
            self.teacher_only.setEnabled(False)


class HandoutMaterialWorkspace(QDialog):
    def __init__(self, facade, tasks=None, parent=None, *, initial_source=None, resume_source=False,
                 questions_only=False, question_context=None):
        super().__init__(parent)
        if initial_source is not None and not _source_valid(initial_source, bound=True):
            raise ValueError('讲义来源记录不完整，请重新选择这份讲义。')
        self.initial_source = (validate_handout_source(initial_source)
                               if initial_source is not None else None)
        self._resume_source = bool(resume_source and initial_source is not None)
        self._continuation_source = None
        self._catalog_sources = []
        self._questions_only = bool(questions_only)
        self._question_context = deepcopy(question_context) if question_context is not None else {}
        if not isinstance(self._question_context, dict):
            raise ValueError('本课选题范围不完整，请重新打开选题。')
        self.all_question_library_requested = False
        self.facade = facade
        self.tasks = tasks or DesktopTaskBridge(self)
        self._own_tasks = tasks is None
        self.request = None
        self._closed = False
        self._epoch = 0
        self._image_epoch = 0
        self._serial = 0
        self._jobs = {}
        self._sources = []
        self._outline = None
        self._document = None
        self._picture_rows = {}
        self._pixmap = None
        self._zoom = None
        self._child = None
        self._child_epoch = 0
        self._selections = []
        self._checking = False
        self._source_stale = False
        self.setWindowTitle('从一份讲义连续选材')
        self.resize(1120, 850)
        self.setMinimumSize(360, 500)
        root = QVBoxLayout(self)
        root.addWidget(_label('先选择一份已导入的Word讲义，再阅读其中的完整题与答案；也可明确浏览全部题库。'
                             if self._questions_only else
                             '先选择一份已导入的Word讲义，查看原文，再选完整知识栏目、原图或完整题。'
                             '本次选材会一起加入当前备课，可继续制作Word教案和课堂PPT。'))
        self.all_questions_button = QPushButton('浏览全部 Word 题库…')
        self.all_questions_button.setAutoDefault(False)
        self.all_questions_button.setVisible(self._questions_only)
        self.all_questions_button.setToolTip('切换会清空本次尚未加入备课的选择；首次读取全部讲义可能较慢。')
        self.all_questions_button.clicked.connect(self.choose_all_questions)
        root.addWidget(self.all_questions_button)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        if self._questions_only:
            content.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.source_search = QLineEdit()
        self.source_search.setPlaceholderText('查找已导入的讲义名称')
        self.source_search.setAccessibleName('查找讲义选材来源')
        content.addWidget(self.source_search)
        self.source = QComboBox()
        self.source.setMinimumWidth(0)
        self.source.setMinimumContentsLength(12)
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source.setAccessibleName('本次选材的一份原Word讲义')
        self.source.addItem('选择一份已导入的Word…', None)
        content.addWidget(self.source)
        self.source_summary = _label('选择后在这里查看完整原文。')
        content.addWidget(self.source_summary)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.knowledge_button = QPushButton('选知识栏目与知识网…')
        self.images_button = QPushButton('选原图与知识网…')
        self.questions_button = QPushButton('选完整题与答案…')
        self.reread_button = QPushButton('重新读取这份讲义')
        for button in (self.knowledge_button, self.images_button, self.questions_button, self.reread_button):
            button.setAutoDefault(False)
            button.setMinimumWidth(0)
            self.actions.addWidget(button)
        self.knowledge_button.setVisible(not self._questions_only)
        self.images_button.setVisible(not self._questions_only)
        content.addLayout(self.actions)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.navigation = QListWidget()
        self.navigation.setWordWrap(True)
        self.navigation.setMinimumSize(0, 120)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setAccessibleName('原讲义知识栏目，仅点击阅读')
        self.navigation.setVisible(not self._questions_only)
        self.splitter.addWidget(self.navigation)
        reading = QWidget()
        reader_layout = QVBoxLayout(reading)
        reader_layout.setContentsMargins(0, 0, 0, 0)
        self.reading_heading = _label('原文阅读')
        reader_layout.addWidget(self.reading_heading)
        self.reader = WordLessonReader(self, image_selection_only=True)
        self.reader.setMinimumHeight(300)
        self.reader.setAccessibleName('本次选材讲义的完整原文')
        reader_layout.addWidget(self.reader, 1)
        self.splitter.addWidget(reading)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([270, 750])
        content.addWidget(self.splitter, 1)
        content.addWidget(_label('本次授课顺序 · 按这个顺序加入备课'))
        self.selected = QListWidget()
        self.selected.setWordWrap(True)
        self.selected.setMinimumSize(0, 100)
        self.selected.setMaximumHeight(190)
        self.selected.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.selected.setAccessibleName('本次知识、原图与完整题的混合选材清单')
        content.addWidget(self.selected)
        self.selection_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.edit_button = QPushButton('编辑所选素材…')
        self.move_up_button = QPushButton('上移')
        self.move_down_button = QPushButton('下移')
        for button in (self.edit_button, self.move_up_button, self.move_down_button):
            button.setAutoDefault(False)
            button.setMinimumWidth(0)
            self.selection_actions.addWidget(button)
        self.remove_button = QPushButton('移除所选素材')
        self.clear_button = QPushButton('清空本次选材')
        self.remove_button.setAutoDefault(False)
        self.clear_button.setAutoDefault(False)
        self.selection_actions.addWidget(self.remove_button)
        self.selection_actions.addWidget(self.clear_button)
        content.addLayout(self.selection_actions)
        self.content_scroll = page_scroll(body)
        root.addWidget(self.content_scroll, 1)
        self.status = _label('正在读取已导入讲义目录…')
        root.addWidget(self.status)
        self.footer = QWidget()
        self.footer_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.footer)
        self.footer_layout.setContentsMargins(0, 0, 0, 0)
        self.count = _label('已选 0 份素材')
        self.footer_layout.addWidget(self.count, 1)
        self.stop_button = QPushButton('停止读取')
        self.cancel_button = QPushButton('取消')
        self.confirm = QPushButton('加入当前备课')
        self.confirm.setObjectName('PrimaryAction')
        for button in (self.stop_button, self.cancel_button, self.confirm):
            button.setAutoDefault(False)
            self.footer_layout.addWidget(button)
        root.addWidget(self.footer)
        self.source_search.textChanged.connect(self.filter_sources)
        self.source.currentIndexChanged.connect(self.read_source)
        self.navigation.currentItemChanged.connect(self.read_section)
        self.reader.image_requested.connect(self.show_image)
        self.reader.image_zoom_requested.connect(self.zoom_image)
        self.knowledge_button.clicked.connect(self.choose_knowledge)
        self.images_button.clicked.connect(self.choose_images)
        self.questions_button.clicked.connect(self.choose_questions)
        self.reread_button.clicked.connect(self.read_source)
        self.edit_button.clicked.connect(self.edit_selection)
        self.move_up_button.clicked.connect(lambda: self.move_selection(-1))
        self.move_down_button.clicked.connect(lambda: self.move_selection(1))
        self.remove_button.clicked.connect(self.remove_selection)
        self.clear_button.clicked.connect(self.clear_selections)
        self.selected.currentRowChanged.connect(self._update_actions)
        self.stop_button.clicked.connect(self.stop_reading)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm.clicked.connect(self.confirm_selection)
        self._layout_for_width()
        self._update_actions()
        self._submit('catalog', '读取讲义选材资料目录', lambda: handout_knowledge_sources(facade),
                     self._catalog_ready)

    def _alive(self, epoch=None):
        return not self._closed and isValid(self) and (epoch is None or epoch == self._epoch)

    def _submit(self, name, label, operation, success):
        self._cancel(name)
        self._serial += 1
        token, epoch = self._serial, self._epoch
        self._jobs[name] = (token, None)
        def done(value, failed=False):
            if not self._alive(epoch) or self._jobs.get(name, (None,))[0] != token:
                return
            self._jobs.pop(name, None)
            if failed:
                self._checking = False
                if name in {'catalog', 'outline', 'confirm'}:
                    self._continuation_source = None
                if not self._resume_failed(name, str(value)):
                    self.status.setText(str(value))
                self._update_actions()
            else:
                try:
                    success(value)
                except (ValueError, KeyError, TypeError) as exc:
                    self._checking = False
                    if name in {'catalog', 'outline', 'confirm'}:
                        self._continuation_source = None
                    if not self._resume_failed(name, str(exc)):
                        self.status.setText(str(exc))
                    self._update_actions()
            self._update_actions()
        task = self.tasks.submit(label, operation, on_success=lambda value: done(value),
                                 on_failure=lambda message: done(message, True))
        if self._jobs.get(name, (None,))[0] == token:
            self._jobs[name] = (token, task)
        self._update_actions()

    def _cancel(self, name):
        job = self._jobs.pop(name, None)
        if job and job[1] is not None:
            self.tasks.cancel(job[1])

    def continuation_source(self):
        """Return only a fully read binding, never pending selections or a catalog guess."""
        source = self._continuation_source
        if (source is None or self._source_stale or source != self._document
                or not isinstance(self._outline, dict) or self._outline.get('source') != source
                or _identity(self.source.currentData()) != _identity(source)):
            return None
        return validate_handout_source(deepcopy(source))

    def _resume_failed(self, name, message):
        if (name not in {'catalog', 'outline'} or not self._resume_source
                or self.initial_source is None or not self._alive()):
            return False
        # Ordinary repeat selection may fall back to manual choice. A sealed
        # section preset never enables this path and keeps its exact rejection.
        self.initial_source = None
        self._resume_source = False
        self._sources = deepcopy(self._catalog_sources)
        with QSignalBlocker(self.source):
            self.source.setCurrentIndex(0)
        with QSignalBlocker(self.source_search):
            self.source_search.clear()
        self.read_source()
        self.filter_sources()
        self.status.setText('上次讲义暂不能续读，请重新选择。' + message)
        self._update_actions()
        return True

    def _catalog_ready(self, value):
        rows = value.get('sources') if isinstance(value, dict) else None
        if not isinstance(rows, list) or any(not _source_valid(row) for row in rows):
            raise ValueError('讲义目录不完整，请重新打开讲义选材。')
        self._sources = deepcopy(rows)
        self._catalog_sources = deepcopy(rows)
        if self.initial_source is not None:
            # Catalog metadata has only six fields. Keep the complete sealed
            # binding for extraction rather than losing its path and revision.
            matches = [row for row in rows if all(self.initial_source.get(key) == item
                                                 for key, item in row.items())]
            self._sources = [deepcopy(self.initial_source)] if len(matches) == 1 else []
            self.filter_sources()
            if not self._sources:
                if self._resume_failed('catalog', '这份讲义的来源或版本已变化。'):
                    return
                self._source_stale = True
                self.status.setText('这份讲义的来源或版本已变化，请重新选择。')
                return
            self.source.setCurrentIndex(1)
            return
        self.filter_sources()
        self.status.setText('选择一份Word讲义，先查看原文。' if rows else '当前没有已导入的Word讲义。')

    def filter_sources(self, *_):
        if not self._alive():
            return
        old = _identity(self.source.currentData())
        query = '' if self.initial_source is not None else self.source_search.text().strip().casefold()
        current = 0
        with QSignalBlocker(self.source):
            self.source.clear()
            self.source.addItem('选择一份已导入的Word…', None)
            for row in self._sources:
                if query and query not in row['source_name'].casefold():
                    continue
                self.source.addItem(row['source_name'], deepcopy(row))
                if _identity(row) == old:
                    current = self.source.count() - 1
            self.source.setCurrentIndex(current)
        if _identity(self.source.currentData()) != old:
            self.read_source()

    def _close_child(self):
        self._child_epoch += 1
        child, self._child = self._child, None
        if child is not None and isValid(child):
            child.reject()
            child.deleteLater()

    def read_source(self, *_):
        if not self._alive():
            return
        self._epoch += 1
        self._image_epoch += 1
        for name in tuple(self._jobs):
            self._cancel(name)
        self._close_child()
        self._checking = False
        self._source_stale = False
        self._continuation_source = None
        self._outline = self._document = None
        self._picture_rows.clear()
        self._pixmap = None
        self.close_zoom()
        self.reader.set_source(None)
        self.navigation.clear()
        self.clear_selections()
        self.request = None
        source = deepcopy(self.source.currentData())
        self._update_actions()
        if self.initial_source is not None and source != self.initial_source:
            self._source_stale = True
            self.status.setText('本次选材的讲义来源已变化，请重新选择。')
            self._update_actions()
            return
        if source is None:
            self.source_summary.setText('选择后在这里查看完整原文。')
            self.status.setText('切换讲义会清空本次选材；原有备课与题篮保持。')
            return
        self.source_summary.setText(source['source_name'])
        self.status.setText('正在读取这份讲义的原栏目和完整原文…')
        self._submit('outline', '读取当前讲义选材原文',
            lambda: handout_knowledge_outline(self.facade, source),
            lambda value: self._outline_ready(source, value))

    def _outline_ready(self, source, value):
        if _identity(source) != _identity(self.source.currentData()):
            return
        if not isinstance(value, dict):
            raise ValueError('讲义原文不完整，请重新读取。')
        bound = validate_handout_source(value.get('source'))
        preview = value.get('preview')
        if ('asset_id' in bound or _identity(bound) != _identity(source)
                or (self.initial_source is not None and
                    (source != self.initial_source or bound != self.initial_source))
                or not isinstance(preview, dict)
                or preview.get('source_sha256') != bound['source_sha256']
                or preview.get('revision') != bound['source_revision']
                or not isinstance(value.get('sections'), list)):
            raise ValueError('讲义原文和当前来源不一致，请重新读取。')
        self._outline = deepcopy(value)
        self._document = bound
        self.reader.set_source(preview, navigation_sections=value['sections'])
        self._picture_rows = {}
        for asset in preview.get('assets', []):
            row = {'schema': 'shchem.handout-picture.v1',
                **{key: bound[key] for key in ('batch_id', 'source_id', 'source_name', 'source_path',
                    'source_sha256', 'source_revision')},
                **{key: asset[key] for key in ('asset_id', 'block_index', 'mime_type', 'sha256', 'bytes_count')}}
            self._picture_rows[asset['asset_id']] = row
        with QSignalBlocker(self.navigation):
            for section in value['sections']:
                item = QListWidgetItem(section_display_title(section, preview) + '\n'
                                       + section_display_summary(section, preview))
                item.setData(Qt.ItemDataRole.UserRole, deepcopy(section))
                self.navigation.addItem(item)
        self.source_summary.setText(bound['source_name'] + f"\n{len(preview.get('blocks', []))}个原文区块 · "
                                    f"{len(value['sections'])}个原栏目 · {len(preview.get('assets', []))}张原图")
        self.status.setText('点击栏目只阅读。使用上方选材按钮，把需要的知识、原图或完整题放入本次清单。')
        self._continuation_source = deepcopy(bound)
        if self._resume_source:
            # The fresh outline has now proved the full previous binding. Keep
            # ordinary source browsing available without silently rebinding it.
            self.initial_source = None
            self._resume_source = False
            self._sources = deepcopy(self._catalog_sources)
            self.filter_sources()
            self.status.setText('已续读上次讲义；本次清单为空，可继续选材或改选其他讲义。')

    def read_section(self, item, *_):
        if self._alive() and self._document is not None and item is not None:
            section = item.data(Qt.ItemDataRole.UserRole)
            self.reading_heading.setText(section_display_title(section, self._outline['preview']))
            self.reader.scroll_to_block(section['start'])

    def _open_child(self, child, collect, *, success_message=None):
        if not self._alive() or self._document is None:
            child.deleteLater()
            return
        self._close_child()
        self._child = child
        epoch, child_epoch = self._epoch, self._child_epoch
        document = deepcopy(self._document)
        def finished(result):
            if (self._alive(epoch) and self._child is child and child_epoch == self._child_epoch
                    and self._document == document):
                self._child = None
                if result == QDialog.DialogCode.Accepted:
                    try:
                        collect(child)
                        self.status.setText(success_message or '已加入本次选材清单，可继续选同一份讲义的其他素材。')
                    except (ValueError, KeyError, TypeError) as exc:
                        self.status.setText(str(exc))
                self._update_actions()
            child.deleteLater()
        child.finished.connect(finished)
        child.open()
        self._update_actions()

    def choose_knowledge(self):
        if self._questions_only or self._document is None or self._child is not None or self._checking:
            return
        picker = HandoutKnowledgePicker(self.facade, self, initial_source=deepcopy(self._document))
        def collect(child):
            if not isinstance(child.requests, list):
                raise ValueError('请明确勾选本次知识栏目。')
            for request in child.requests:
                if not _document_matches(request.get('source'), self._document):
                    raise ValueError('知识栏目不属于当前讲义，请重新选择。')
            self._add_selections('knowledge', child.requests)
        self._open_child(picker, collect)

    def choose_images(self):
        if self._questions_only or self._document is None or self._child is not None or self._checking or 'picker' in self._jobs:
            return
        document = deepcopy(self._document)
        self.status.setText('正在读取这份讲义的原图目录…')
        def ready(snapshot):
            if snapshot.get('document_source') != document:
                raise ValueError('原图与当前讲义版本不一致，请重新读取。')
            picker = _BoundImagePicker(self.facade, snapshot, [], self, tasks=self.tasks)
            picker.confirm.setText('加入本次讲义选材')
            def collect(child):
                selection = deepcopy(child.selection)
                if not isinstance(selection, dict) or not _document_matches(selection.get('source'), document):
                    raise ValueError('原图不属于当前讲义，请重新选择。')
                if document['source_role'] == 'answer':
                    selection['teacher_only'] = True
                self._add_selections('image', [selection])
            self._open_child(picker, collect)
        self._submit('picker', '读取当前讲义选材原图目录',
                     lambda: handout_source_context(self.facade, document), ready)

    def choose_questions(self):
        if self._document is None or self._child is not None or self._checking or 'picker' in self._jobs:
            return
        document = deepcopy(self._document)
        self.status.setText('正在读取这份讲义的完整题与答案…')
        def ready(scoped):
            browser = self._question_context if self._questions_only else {}
            picker = WordQuestionDialog(scoped, self.tasks, self,
                required_source_id=document['source_sha256'], handoff_mode='composition',
                persist_selection=False, allow_knowledge_links=False, selection_only=True,
                initial_curriculum_context=deepcopy(browser.get('curriculum_context')),
                initial_search_query=browser.get('search_query', ''))
            picker.setWindowTitle('从这份讲义选择完整题与答案')
            def collect(child):
                result = child.selection_result
                if not isinstance(result, dict) or not isinstance(result.get('selections'), list):
                    raise ValueError('请阅读并确认本次完整题与答案。')
                rows = result['selections']
                for row in rows:
                    if row.get('scoped_source_document') != document or row.get('source_sha256') != document['source_sha256']:
                        raise ValueError('完整题不属于当前讲义，请重新选择。')
                labels = {key: value.get('title_zh') or value.get('title') or key
                          for key, value in child._items.items()}
                self._add_selections('question', rows, labels)
            self._open_child(picker, collect)
        self._submit('picker', '读取当前讲义选材完整题目录',
                     lambda: handout_question_facade(self.facade, document), ready)

    def choose_all_questions(self):
        if (not self._questions_only or not self._alive() or self._checking
                or self._child is not None or 'picker' in self._jobs):
            return
        self.request = None
        self.all_question_library_requested = True
        self.accept()

    def _add_selections(self, kind, rows, labels=None):
        if self._questions_only and kind != 'question':
            raise ValueError('本次只选择完整例题；知识栏目与原图请从讲义选材入口加入。')
        merged = deepcopy(self._selections)
        for row in rows:
            if kind == 'knowledge':
                key = (kind, row['section']['id'])
                label = '知识栏目 · ' + row['title']
            elif kind == 'image':
                key = (kind, row['source']['asset_id'], row['purpose'], row['teacher_only'])
                label = '原图 · ' + row['title']
            else:
                key = (kind, row['key'], row['revision'])
                label = '完整题与答案 · ' + row.get('title', (labels or {}).get(row['key'], row['key']))
            entry = {'kind': kind, 'key': key, 'value': deepcopy(row), 'label': label}
            old = next((i for i, item in enumerate(merged) if item['key'] == key), None)
            if old is None:
                merged.append(entry)
            else:
                previous = merged[old]['value']
                if (kind == 'question' and 'title' in previous
                        and all(previous.get(k) == row.get(k) for k in
                                ('key', 'revision', 'source_sha256', 'scoped_source_document'))):
                    # A fresh native selection can update points without losing
                    # the teacher's title for this same, still-pending source.
                    entry['value']['title'] = previous['title']
                    entry['label'] = '完整题与答案 · ' + previous['title']
                merged[old] = entry
        if sum(item['kind'] == kind for item in merged) > 20:
            raise ValueError('每类素材本次最多20份，请先移除不需要的素材再继续选择。')
        self._selections = merged
        self._render_selections()

    def _render_selections(self, selected_index=None):
        with QSignalBlocker(self.selected):
            self.selected.clear()
            for index, entry in enumerate(self._selections, 1):
                detail = ''
                if entry['kind'] in ('knowledge', 'image'):
                    scope = entry['value']['teacher_only'] or (self._document or {}).get('source_role') == 'answer'
                    detail = '\n' + ('仅教师可见' if scope else '课堂与学生可见')
                    if entry['kind'] == 'image' and entry['value']['purpose']:
                        purpose = entry['value']['purpose']
                        detail += ' · 用途：' + (purpose if len(purpose) <= 100 else purpose[:100] + '…')
                elif entry['kind'] == 'question':
                    detail = f"\n本次练习 {entry['value']['points']} 分"
                self.selected.addItem(f"{index}. {entry['label']}{detail}")
            if selected_index is not None and self._selections:
                self.selected.setCurrentRow(min(max(0, selected_index), len(self._selections)-1))
        counts = {kind: sum(row['kind'] == kind for row in self._selections)
                  for kind in ('knowledge', 'image', 'question')}
        self.count.setText(f"已选 {len(self._selections)} 份：知识{counts['knowledge']} · "
                           f"原图{counts['image']} · 完整题{counts['question']}")
        self._update_actions()

    def edit_selection(self):
        index = self.selected.currentRow()
        if (not self._alive() or self._document is None or self._checking or self._source_stale
                or self._child is not None or 'picker' in self._jobs
                or not 0 <= index < len(self._selections)):
            return
        before = deepcopy(self._selections)
        entry = before[index]
        if entry['kind'] not in ('knowledge', 'image', 'question'):
            return
        document = deepcopy(self._document)
        outline_revision = (self._outline or {}).get('outline_revision')
        child = _MaterialMetadataDialog(entry, answer_source=document['source_role'] == 'answer', parent=self)
        def collect(editor):
            if (self._checking or self._source_stale or self._document != document
                    or (self._outline or {}).get('outline_revision') != outline_revision
                    or self._selections != before):
                raise ValueError('讲义版本或所选素材已变化，本次修改未保存，请重新选择。')
            metadata = editor.metadata
            expected = ({'title'} if entry['kind'] == 'question' else
                        {'title', 'purpose', 'teacher_only'} if entry['kind'] == 'image'
                        else {'title', 'teacher_only'})
            if not isinstance(metadata, dict) or set(metadata) != expected:
                raise ValueError('本次修改不完整，请重新编辑所选素材。')
            value = deepcopy(entry['value'])
            if (not isinstance(metadata['title'], str) or not metadata['title'].strip()
                    or len(metadata['title']) > 160):
                raise ValueError('请填写不超过160字的引导问题。')
            if entry['kind'] != 'question' and type(metadata['teacher_only']) is not bool:
                raise ValueError('请明确本课素材的适用范围。')
            if (entry['kind'] == 'image' and (not isinstance(metadata['purpose'], str)
                                            or len(metadata['purpose']) > 500)):
                raise ValueError('讲解用途最多500字。')
            source = value.get('scoped_source_document') if entry['kind'] == 'question' else value.get('source')
            if (not _document_matches(source, document) or (entry['kind'] == 'question'
                    and value.get('source_sha256') != document['source_sha256'])):
                raise ValueError('所选素材不属于当前讲义版本，请重新选择。')
            if document['source_role'] == 'answer' and entry['kind'] != 'question':
                metadata = {**metadata, 'teacher_only': True}
            value.update(deepcopy(metadata))
            replacement = deepcopy(entry)
            replacement['value'] = value
            if entry['kind'] == 'image':
                replacement['key'] = ('image', value['source']['asset_id'], value['purpose'], value['teacher_only'])
                replacement['label'] = '原图 · ' + value['title']
            elif entry['kind'] == 'knowledge':
                replacement['label'] = '知识栏目 · ' + value['title']
            else:
                replacement['label'] = '完整题与答案 · ' + value['title']
            if any(i != index and other['key'] == replacement['key'] for i, other in enumerate(before)):
                raise ValueError('同一原图已有相同用途和适用范围的选材；本次修改未保存，请调整用途或先移除另一项。')
            self._selections = [replacement if i == index else row for i, row in enumerate(before)]
            self._render_selections(index)
        self._open_child(child, collect, success_message='已保存本次修改，素材仍在原授课位置。')

    def move_selection(self, offset):
        if (not self._alive() or self._checking or self._child is not None
                or 'picker' in self._jobs or type(offset) is not int or offset not in (-1, 1)):
            return
        index = self.selected.currentRow()
        target = index + offset
        if not 0 <= index < len(self._selections) or not 0 <= target < len(self._selections):
            return
        self._selections[index], self._selections[target] = self._selections[target], self._selections[index]
        self._render_selections(target)

    def remove_selection(self):
        index = self.selected.currentRow()
        if (self._alive() and not self._checking and self._child is None and 'picker' not in self._jobs
                and 0 <= index < len(self._selections)):
            self._selections.pop(index)
            self._render_selections(index)

    def clear_selections(self):
        if self._checking or self._child is not None or 'picker' in self._jobs:
            return
        self._selections.clear()
        self._render_selections()

    def confirm_selection(self):
        if not self.confirm.isEnabled():
            return
        if self._questions_only and any(row['kind'] != 'question' for row in self._selections):
            self.status.setText('本次只选择完整例题，请重新选择。')
            return
        document = deepcopy(self._document)
        entries = deepcopy(self._selections)
        self._checking = True
        self.status.setText('正在核对本次选材的讲义原文版本…')
        def ready(context):
            self._checking = False
            if context.get('document_source') != document:
                self._source_stale = True
                raise ValueError('讲义来源版本已变化，请重新读取这份讲义后选材。')
            if entries != self._selections:
                raise ValueError('本次选材已变化，请重新确认。')
            self.request = {'schema': SCHEMA, 'source': document,
                'knowledge_requests': [deepcopy(row['value']) for row in entries if row['kind'] == 'knowledge'],
                'image_selections': [deepcopy(row['value']) for row in entries if row['kind'] == 'image'],
                'question_selections': [deepcopy(row['value']) for row in entries if row['kind'] == 'question']}
            counts = {'knowledge': 0, 'image': 0, 'question': 0}
            order = []
            for row in entries:
                kind = row['kind']
                order.append({'kind': kind, 'index': counts[kind]})
                counts[kind] += 1
            self.request['selection_order'] = order
            self.accept()
        self._submit('confirm', '核对本次讲义选材来源',
                     lambda: handout_source_context(self.facade, document), ready)

    def show_image(self, asset_id):
        if not self._alive() or self._document is None:
            return
        row = self._picture_rows.get(asset_id)
        if row is None:
            return
        self._image_epoch += 1
        image_epoch = self._image_epoch
        self.close_zoom()
        self._pixmap = None
        self.reader.show_image(asset_id, None, '正在读取完整原图…')
        def ready(value):
            if image_epoch != self._image_epoch:
                return
            picture = QPixmap()
            if value.get('source') != row or not picture.loadFromData(value.get('bytes', b'')):
                raise ValueError('原图暂未显示，请重新选择。')
            self._pixmap = picture
            self.reader.show_image(asset_id, picture, '', derived=value.get('derived_preview') is True)
        self._submit('image', '读取当前讲义选材原图',
                     lambda: handout_image_preview(self.facade, deepcopy(row)), ready)

    def zoom_image(self):
        if self._alive() and self._pixmap is not None:
            from .library_detail import ImageZoomDialog
            self.close_zoom()
            self._zoom = ImageZoomDialog(self._pixmap, self.reading_heading.text(), self)
            self._zoom.show()

    def close_zoom(self):
        if self._zoom is not None:
            self._zoom.close()
            self._zoom = None

    def stop_reading(self):
        if not self._alive():
            return
        self._epoch += 1
        self._image_epoch += 1
        for name in tuple(self._jobs):
            self._cancel(name)
        self._checking = False
        self._close_child()
        self.status.setText('读取已停止；可继续选材或重新读取这份讲义。')
        self._update_actions()

    def _update_actions(self, *_):
        if not hasattr(self, 'confirm'):
            return
        ready = self._alive() and self._document is not None and not self._checking and not self._source_stale
        choosing = self._child is not None or 'picker' in self._jobs
        for button in (self.knowledge_button, self.images_button, self.questions_button):
            button.setEnabled(ready and not choosing)
        self.knowledge_button.setEnabled(not self._questions_only and ready and not choosing)
        self.images_button.setEnabled(not self._questions_only and ready and not choosing)
        self.all_questions_button.setEnabled(self._questions_only and self._alive()
            and not self._checking and not choosing)
        # A source choice has no original text yet. Keep that empty pane out
        # of the question-only entry; the complete reader appears after a read.
        self.splitter.setVisible(not self._questions_only or self._document is not None)
        self.reread_button.setEnabled(self._alive() and self.source.currentData() is not None and not self._checking)
        self.source.setEnabled(not self._checking and self._alive() and self.initial_source is None)
        self.source_search.setEnabled(not self._checking and self._alive() and self.initial_source is None)
        index = self.selected.currentRow()
        selected_kind = self._selections[index]['kind'] if 0 <= index < len(self._selections) else None
        self.edit_button.setEnabled(ready and not choosing and selected_kind in ('knowledge', 'image', 'question'))
        self.edit_button.setToolTip('修改完整题在本课的引导问题；题面与答案保持原设置。'
                                   if selected_kind == 'question' else '修改本课引导问题、原图用途与适用范围')
        self.remove_button.setEnabled(ready and not choosing and 0 <= self.selected.currentRow() < len(self._selections))
        self.move_up_button.setEnabled(ready and not choosing and 0 < index < len(self._selections))
        self.move_down_button.setEnabled(ready and not choosing and 0 <= index < len(self._selections)-1)
        self.clear_button.setEnabled(ready and not choosing and bool(self._selections))
        self.confirm.setEnabled(ready and bool(self._selections) and not choosing)
        self.stop_button.setEnabled(self._alive() and (bool(self._jobs) or self._child is not None))

    def _layout_for_width(self):
        narrow = self.width() < 750
        mobile = self.width() < 600
        self.actions.setDirection(QBoxLayout.Direction.TopToBottom if mobile else QBoxLayout.Direction.LeftToRight)
        self.selection_actions.setDirection(QBoxLayout.Direction.TopToBottom if mobile else QBoxLayout.Direction.LeftToRight)
        self.footer_layout.setDirection(QBoxLayout.Direction.TopToBottom if mobile else QBoxLayout.Direction.LeftToRight)
        self.splitter.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
        self.splitter.setMinimumHeight(650 if narrow else 390)
        self.splitter.setSizes([200, 450] if narrow else [270, 750])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'footer_layout'):
            self._layout_for_width()

    def done(self, result):
        if not self._closed:
            if result != QDialog.DialogCode.Accepted:
                self.request = None
            self._closed = True
            self._epoch += 1
            self._image_epoch += 1
            for name in tuple(self._jobs):
                self._cancel(name)
            self._close_child()
            self.close_zoom()
            self.reader.set_source(None)
            if self._own_tasks:
                self.tasks.shutdown(1000)
        super().done(result)

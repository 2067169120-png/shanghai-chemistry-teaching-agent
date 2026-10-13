"""Read one original Word and choose source-bound knowledge sections, without writes."""
from copy import deepcopy
import json
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from ..desktop_handout_knowledge import handout_knowledge_sources, handout_knowledge_outline
from ..desktop_handout_images import handout_image_preview, handout_image_context, validate_source as validate_picture_source
from .components import page_scroll
from .tasks import DesktopTaskBridge
from .word_lesson_reader import WordLessonReader
from .word_source_display import section_display_title, section_display_summary


_PLACEHOLDER = '【待查看原文：图片或图形】'
_SOURCE_SCHEMA = 'shchem.handout-knowledge-source.v1'
_SOURCE_KEYS = {'schema', 'batch_id', 'source_id', 'source_name', 'source_sha256', 'source_role'}
_BOUND_KEYS = _SOURCE_KEYS | {'source_path', 'source_revision'}
_SECTION_KEYS = {'id', 'level', 'title', 'start', 'end'}
_HASH = re.compile(r'[a-f0-9]{64}')
MAX_SECTIONS = 20


def _label(text=''):
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    return value


def _identity(source):
    source = source if isinstance(source, dict) else {}
    return tuple(source.get(k) for k in ('batch_id', 'source_id', 'source_sha256'))


def _source_valid(source, *, bound=False):
    keys = _BOUND_KEYS if bound else _SOURCE_KEYS
    return (isinstance(source, dict) and set(source) == keys
        and source.get('schema') == _SOURCE_SCHEMA
        and all(isinstance(source.get(k), str) and source[k].strip()
                for k in ('batch_id', 'source_id', 'source_name'))
        and isinstance(source.get('source_sha256'), str)
        and _HASH.fullmatch(source['source_sha256']) is not None
        and source.get('source_role') in {'handout', 'question', 'answer'}
        and (not bound or (isinstance(source.get('source_path'), str) and source['source_path']
            and isinstance(source.get('source_revision'), str)
            and _HASH.fullmatch(source['source_revision']) is not None)))


def _section_valid(section, preview):
    if (not isinstance(section, dict) or set(section) != _SECTION_KEYS
        or not isinstance(section.get('id'), str) or not section['id']
        or type(section.get('level')) is not int or not 1 <= section['level'] <= 9
        or not isinstance(section.get('title'), str) or not section['title'].strip()
        or type(section.get('start')) is not int or type(section.get('end')) is not int):
        return False
    blocks = preview.get('blocks', [])
    indices = {b['index'] for b in blocks}
    start, end = section['start'], section['end']
    if start not in indices or end not in indices or start > end:
        return False
    has_text = any(start < b['index'] <= end and b['text'].strip()
                   and b['text'].strip() != _PLACEHOLDER for b in blocks)
    has_image = any(type(a.get('block_index')) is int and start <= a['block_index'] <= end
                    for a in preview.get('assets', []) if isinstance(a, dict))
    return has_text or has_image


class HandoutKnowledgePicker(QDialog):
    def __init__(self, facade, parent=None, initial_source=None):
        super().__init__(parent)
        self.facade = facade
        self.initial_source = deepcopy(initial_source)
        self._legacy_source = None; self._initial_error = None
        if initial_source is not None:
            try:
                if isinstance(initial_source, dict) and initial_source.get('schema') == 'shchem.handout-picture.v1':
                    self._legacy_source = validate_picture_source(initial_source)
                elif not (_source_valid(initial_source) or _source_valid(initial_source, bound=True)):
                    raise ValueError('讲义来源记录不完整，请从讲义知识入口重新选择。')
            except ValueError as exc:
                self._initial_error = str(exc)
        self.tasks = DesktopTaskBridge(self)
        self.requests = None
        self._closed = False; self._epoch = 0; self._image_epoch = 0
        self._jobs = {}; self._sources = []; self._outline = None
        self._sections = []; self._checked = {}; self.edits = {}
        self._current_section = None; self._picture_rows = {}; self._pixmap = None; self._zoom = None
        self._updating = False; self._checking = False; self._stale = False
        self.setWindowTitle('选择讲义知识段落与知识结构网')
        self.resize(1100, 860); self.setMinimumSize(360, 560)
        root = QVBoxLayout(self)
        root.addWidget(_label('选一份讲义，阅读原栏目并勾选本课所需知识；为每段填写引导问题。切换讲义会清空本次选择。'))
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.navigation = QWidget(); nav = QVBoxLayout(self.navigation)
        nav.setContentsMargins(0, 0, 8, 0)
        self.source_search = QLineEdit(); self.source_search.setPlaceholderText('查找讲义名称')
        self.source_search.setAccessibleName('查找原Word讲义'); nav.addWidget(self.source_search)
        self.source = QComboBox(); self.source.setMinimumWidth(0)
        self.source.setMinimumContentsLength(12)
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source.setAccessibleName('选择一份原Word'); nav.addWidget(self.source)
        self.section_search = QLineEdit(); self.section_search.setPlaceholderText('查找原栏目或附近原文')
        self.section_search.setAccessibleName('查找原知识栏目'); nav.addWidget(self.section_search)
        self.sections = QListWidget(); self.sections.setWordWrap(True)
        self.sections.setMinimumHeight(140)
        self.sections.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sections.setAccessibleName('勾选本课知识栏目'); nav.addWidget(self.sections, 1)
        self.count = _label('已选 0 / 20 段'); nav.addWidget(self.count)
        self.split.addWidget(self.navigation)
        self.reading_panel = QWidget(); reading = QVBoxLayout(self.reading_panel)
        reading.setContentsMargins(0, 0, 0, 0)
        self.heading = _label('先选择一份原Word'); reading.addWidget(self.heading)
        self.reader = WordLessonReader(self, image_selection_only=True)
        self.reader.setMinimumHeight(340)
        self.reader.setAccessibleName('完整讲义原文与知识原图')
        reading.addWidget(self.reader, 1)
        form = QFormLayout(); form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.title = QLineEdit(); self.title.setMinimumWidth(0); self.title.setMaxLength(160)
        self.title.setPlaceholderText('例如：这些知识点怎样联系起来？')
        self.title.setAccessibleName('所选知识段的本课引导问题')
        self.teacher_only = QCheckBox('仅教师讲解'); self.teacher_only.setEnabled(False)
        form.addRow('本课引导问题', self.title); form.addRow(self.teacher_only); reading.addLayout(form)
        self.details_button = QPushButton('查看原栏目与来源版本'); self.details_button.setCheckable(True)
        self.details_button.setAutoDefault(False); reading.addWidget(self.details_button)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setFixedHeight(120)
        self.details.setMinimumWidth(0); self.details.hide(); reading.addWidget(self.details)
        self.split.addWidget(self.reading_panel); self.split.setStretchFactor(1, 2)
        body = QWidget(); body_layout = QVBoxLayout(body); body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(self.split)
        self.content_scroll = page_scroll(body); root.addWidget(self.content_scroll, 1)
        self.status = _label('正在读取讲义目录…'); root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm.setText('确认本次知识段选择'); self.confirm.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('返回课件')
        for button in buttons.buttons(): button.setAutoDefault(False)
        buttons.accepted.connect(self.choose); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.source_search.textChanged.connect(self.filter_sources)
        self.source.currentIndexChanged.connect(self.read_source)
        self.section_search.textChanged.connect(self.filter_sections)
        self.sections.currentItemChanged.connect(self.show_section)
        self.sections.itemChanged.connect(self.check_section)
        self.title.textChanged.connect(self._edit_changed)
        self.teacher_only.toggled.connect(self._edit_changed)
        self.details_button.toggled.connect(self.details.setVisible)
        self.reader.image_requested.connect(self.show_image)
        self.reader.image_zoom_requested.connect(self.zoom_image)
        self._layout_for_width()
        self._jobs['catalog'] = self.tasks.submit('读取讲义知识资料目录',
            lambda: handout_knowledge_sources(self.facade),
            on_success=self._catalog_ready, on_failure=lambda message: self._failed('catalog', None, message))

    def _alive(self, epoch=None):
        return not self._closed and isValid(self) and (epoch is None or epoch == self._epoch)

    def _cancel(self, name):
        task = self._jobs.pop(name, None)
        if task is not None: self.tasks.cancel(task)

    def _catalog_ready(self, value):
        if not self._alive(): return
        self._jobs.pop('catalog', None)
        rows = value.get('sources') if isinstance(value, dict) else None
        if not isinstance(rows, list) or any(not _source_valid(row) for row in rows):
            self._failed('catalog', None, '讲义目录不完整，请重新打开。'); return
        self._sources = deepcopy(rows)
        if self.initial_source is not None:
            if self._initial_error is not None:
                self._sources = []; self.filter_sources(); self.status.setText(self._initial_error); return
            self._sources = [row for row in self._sources
                if _identity(row) == _identity(self.initial_source)
                and row['source_name'] == self.initial_source['source_name']
                and (self._legacy_source is not None or row['source_role'] == self.initial_source['source_role'])]
            if len(self._sources) != 1:
                self.status.setText('这份讲义的来源或版本已变化，请从讲义知识入口重新选择。')
                self.filter_sources(); return
        self.filter_sources()
        if self.initial_source is not None and self.source.count() == 2:
            self.source.setCurrentIndex(1)
        else:
            self.status.setText('选择一份原Word，再阅读和勾选知识栏目。' if self._sources else '当前没有可选的Word讲义。')

    def filter_sources(self, *_):
        if not self._alive(): return
        old = _identity(self.source.currentData()); query = self.source_search.text().strip().casefold()
        self.source.blockSignals(True); self.source.clear(); self.source.addItem('选择一份原Word…', None)
        current = 0
        for row in self._sources:
            if query and query not in row['source_name'].casefold(): continue
            self.source.addItem(row['source_name'], deepcopy(row))
            if _identity(row) == old: current = self.source.count() - 1
        self.source.setCurrentIndex(current); self.source.blockSignals(False)
        if _identity(self.source.currentData()) != old: self.read_source()

    def read_source(self, *_):
        if not self._alive(): return
        self._epoch += 1; epoch = self._epoch; self._image_epoch += 1
        for name in ('outline', 'image', 'confirm'): self._cancel(name)
        self._checking = False; self._stale = False; self._outline = None; self._sections = []
        self._checked.clear(); self.edits.clear(); self._current_section = None
        self._picture_rows.clear(); self._pixmap = None; self.close_zoom()
        self.reader.set_source(None); self.heading.setText('先选择一份原Word')
        self.title.clear(); self.title.setEnabled(False); self.teacher_only.setEnabled(False)
        self.details.clear(); self.section_search.clear(); self.filter_sections()
        source = deepcopy(self.source.currentData())
        if source is None:
            self.status.setText('选择一份原Word，再阅读和勾选知识栏目。'); return
        self.heading.setText(source['source_name']); self.status.setText('正在读取这份讲义的原栏目和完整原文…')
        self._jobs['outline'] = self.tasks.submit('读取讲义知识栏目',
            lambda: self._read_outline(source),
            on_success=lambda value: self._outline_ready(epoch, source, value),
            on_failure=lambda message: self._failed('outline', epoch, message))

    def _read_outline(self, source):
        if self._legacy_source is not None:
            handout_image_context(self.facade, deepcopy(self._legacy_source))
        return handout_knowledge_outline(self.facade, source)

    def _validate_outline(self, source, value):
        if (not isinstance(value, dict) or not _source_valid(value.get('source'), bound=True)
            or _identity(value['source']) != _identity(source)
            or any(value['source'][key] != source[key] for key in _SOURCE_KEYS)
            or not isinstance(value.get('outline_revision'), str)
            or _HASH.fullmatch(value['outline_revision']) is None
            or not isinstance(value.get('sections'), list) or not isinstance(value.get('preview'), dict)):
            raise ValueError('讲义栏目或来源版本不完整，请重新选择。')
        preview = value['preview']; blocks = preview.get('blocks')
        if (preview.get('source_sha256') != value['source']['source_sha256']
            or preview.get('revision') != value['source']['source_revision']
            or not isinstance(blocks, list)
            or any(not isinstance(b, dict) or type(b.get('index')) is not int or b['index'] < 1
                   or not isinstance(b.get('text'), str) for b in blocks)
            or [b['index'] for b in blocks] != sorted({b['index'] for b in blocks})
            or not isinstance(preview.get('assets', []), list)):
            raise ValueError('讲义原文或来源版本已变化，请重新选择。')
        if self.initial_source is not None and self.initial_source.get('source_revision') is not None:
            if value['source']['source_revision'] != self.initial_source['source_revision']:
                raise ValueError('这份讲义的来源版本已变化，请从讲义知识入口重新选择。')
        ids = [s.get('id') for s in value['sections'] if isinstance(s, dict)]
        if len(ids) != len(value['sections']) or len(ids) != len(set(ids)):
            raise ValueError('讲义栏目范围不完整，请重新选择。')
        return deepcopy(value)

    def _outline_ready(self, epoch, source, value):
        if not self._alive(epoch) or _identity(self.source.currentData()) != _identity(source): return
        self._jobs.pop('outline', None)
        try:
            value = self._validate_outline(source, value)
            valid = [s for s in value['sections'] if _section_valid(s, value['preview'])]
            self._outline = value; self._sections = valid
            display = value['preview']
            self.reader.set_source(display, navigation_sections=valid)
            self._picture_rows = {}
            bound = value['source']
            for asset in display.get('assets', []):
                if not isinstance(asset, dict) or not isinstance(asset.get('asset_id'), str): continue
                row = {'schema': 'shchem.handout-picture.v1',
                    **{k: bound[k] for k in ('batch_id', 'source_id', 'source_name', 'source_path', 'source_sha256', 'source_revision')},
                    **{k: asset.get(k) for k in ('asset_id', 'block_index', 'mime_type', 'sha256', 'bytes_count')}}
                if asset['asset_id'] in self._picture_rows:
                    self._picture_rows[asset['asset_id']] = None
                else: self._picture_rows[asset['asset_id']] = row
            self.details.setPlainText(json.dumps(bound, ensure_ascii=False, indent=2))
            self.filter_sections()
            self.status.setText('勾选所需栏目，为每段填写本课引导问题。' if valid else '这份讲义没有含正文或知识图的可选原栏目。')
        except (ValueError, TypeError, KeyError) as exc:
            self._outline = None; self._sections = []; self.reader.set_source(None); self.filter_sections()
            self.status.setText(str(exc)); self.confirm.setEnabled(False)

    def _save_edit(self):
        if self._current_section is not None and not self._updating:
            self.edits[self._current_section['id']] = (self.title.text().strip(), self.teacher_only.isChecked())

    def _edit_changed(self, *_):
        if self._updating or not self._alive(): return
        self._save_edit(); self._update_confirm()

    def filter_sections(self, *_):
        if not self._alive(): return
        self._save_edit(); old = (self._current_section or {}).get('id')
        query = self.section_search.text().strip().casefold(); selected = 0
        self.sections.blockSignals(True); self.sections.clear()
        blocks = (self._outline or {}).get('preview', {}).get('blocks', [])
        for section in self._sections:
            if query:
                nearby = '\n'.join(b['text'] for b in blocks if section['start'] <= b['index'] <= section['end'])
                if query not in (section_display_title(section, self._outline['preview']) + '\n' + nearby).casefold(): continue
            item = QListWidgetItem(f"H{section['level']} · "
                + section_display_title(section, self._outline['preview']) + '\n'
                + section_display_summary(section, self._outline['preview']))
            item.setData(Qt.ItemDataRole.UserRole, deepcopy(section))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if section['id'] in self._checked else Qt.CheckState.Unchecked)
            self.sections.addItem(item)
            if section['id'] == old: selected = self.sections.count() - 1
        self.sections.setCurrentRow(selected if self.sections.count() else -1)
        self.sections.blockSignals(False); self.show_section(self.sections.currentItem()); self._update_confirm()

    def show_section(self, item, *_):
        if not self._alive(): return
        self._save_edit(); self._current_section = deepcopy(item.data(Qt.ItemDataRole.UserRole)) if item else None
        self._updating = True
        try:
            section = self._current_section
            if section is None:
                self.title.clear(); self.title.setEnabled(False); self.teacher_only.setChecked(False)
                self.teacher_only.setEnabled(False); return
            restricted = self._outline['source']['source_role'] == 'answer'
            title, teacher = self.edits.get(section['id'], ('', restricted))
            self.title.setText(title); self.title.setEnabled(True)
            self.teacher_only.setChecked(restricted or teacher); self.teacher_only.setEnabled(not restricted)
            self.heading.setText(self._outline['source']['source_name'] + '\n'
                + section_display_title(section, self._outline['preview']))
            self.reader.scroll_to_block(section['start'])
            self.details.setPlainText(json.dumps({'source': self._outline['source'], 'section': section}, ensure_ascii=False, indent=2))
        finally:
            self._updating = False

    def check_section(self, item):
        if not self._alive() or self._outline is None: return
        section = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            if section['id'] not in self._checked and len(self._checked) >= MAX_SECTIONS:
                self.sections.blockSignals(True); item.setCheckState(Qt.CheckState.Unchecked); self.sections.blockSignals(False)
                self.status.setText('每批最多20段，请先移除不需要的栏目。'); return
            self._checked[section['id']] = deepcopy(section)
            self.sections.setCurrentItem(item)
        else: self._checked.pop(section['id'], None)
        self._save_edit(); self._update_confirm()

    def _current_requests(self):
        self._save_edit()
        if self._outline is None: return []
        result = []
        for section in sorted(self._checked.values(), key=lambda s: (s['start'], s['end'], s['id'])):
            title, teacher = self.edits.get(section['id'], ('', self._outline['source']['source_role'] == 'answer'))
            result.append({'source': deepcopy(self._outline['source']),
                'outline_revision': self._outline['outline_revision'], 'section': deepcopy(section),
                'title': title, 'teacher_only': self._outline['source']['source_role'] == 'answer' or teacher})
        return result

    def _update_confirm(self):
        requests = self._current_requests()
        ready = (self._alive() and not self._checking and not self._stale and 1 <= len(requests) <= MAX_SECTIONS
            and all(r['title'] and len(r['title']) <= 160
                    and _section_valid(r['section'], self._outline['preview']) for r in requests))
        self.confirm.setEnabled(bool(ready)); self.count.setText(f'已选 {len(requests)} / 20 段')

    def choose(self):
        if not self._alive() or not self.confirm.isEnabled() or self._checking: return
        requests = self._current_requests(); epoch = self._epoch
        source = deepcopy(self._outline['source']); outline_revision = self._outline['outline_revision']
        self._checking = True; self.confirm.setEnabled(False); self.status.setText('正在核对本次原栏目与来源版本…')
        def ready(value):
            if not self._alive(epoch): return
            self._jobs.pop('confirm', None); self._checking = False
            try:
                value = self._validate_outline(source, value)
                current = {s['id']: s for s in value['sections']}
                if (value['outline_revision'] != outline_revision
                    or value['source']['source_revision'] != source['source_revision']
                    or any(current.get(r['section']['id']) != r['section'] for r in requests)):
                    raise ValueError('讲义内容或栏目已变化，请重新读取并选择。')
                if any(not _section_valid(r['section'], value['preview']) for r in requests):
                    raise ValueError('所选栏目没有完整正文或范围已变化，请重新选择。')
            except (ValueError, TypeError, KeyError) as exc:
                self._stale = True; self.status.setText(str(exc)); self.confirm.setEnabled(False); return
            if self._current_requests() != requests:
                self.status.setText('本次选择已变化，请再次确认。'); self._update_confirm(); return
            self.requests = deepcopy(requests); self.accept()
        self._jobs['confirm'] = self.tasks.submit('核对所选讲义知识栏目',
            lambda: self._read_outline(source),
            on_success=ready, on_failure=lambda message: self._failed('confirm', epoch, message))

    def show_image(self, asset_id):
        if not self._alive() or self._outline is None: return
        row = self._picture_rows.get(asset_id)
        if row is None: return
        epoch = self._epoch; self._image_epoch += 1; image_epoch = self._image_epoch
        self._cancel('image'); self.close_zoom(); self._pixmap = None
        self.reader.show_image(asset_id, None, '正在读取完整原图…')
        def ready(value):
            if not self._alive(epoch) or image_epoch != self._image_epoch: return
            self._jobs.pop('image', None)
            pixmap = QPixmap()
            if (not isinstance(value, dict) or value.get('source') != row
                or not isinstance(value.get('bytes'), bytes) or not pixmap.loadFromData(value['bytes'])):
                failed('原图暂未显示，请重新选择。'); return
            self._pixmap = pixmap
            self.reader.show_image(asset_id, pixmap, '', derived=value.get('derived_preview') is True)
        def failed(message):
            if not self._alive(epoch) or image_epoch != self._image_epoch: return
            self._jobs.pop('image', None); self._pixmap = None
            self.reader.show_image(asset_id, None, message); self.status.setText(message)
        self._jobs['image'] = self.tasks.submit('读取讲义知识原图',
            lambda: handout_image_preview(self.facade, deepcopy(row)), on_success=ready, on_failure=failed)

    def zoom_image(self):
        if not self._alive() or self._pixmap is None: return
        from .library_detail import ImageZoomDialog
        self.close_zoom(); self._zoom = ImageZoomDialog(self._pixmap, self.heading.text(), self); self._zoom.show()

    def close_zoom(self):
        if self._zoom is not None: self._zoom.close(); self._zoom = None

    def _failed(self, name, epoch, message):
        if not self._alive(epoch): return
        self._jobs.pop(name, None)
        if name == 'confirm': self._checking = False; self._stale = True
        self.confirm.setEnabled(False); self.status.setText(message)

    def _layout_for_width(self):
        narrow = self.width() < 750
        self.split.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
        self.split.setMinimumHeight(830 if narrow else 650)
        self.navigation.setMinimumHeight(260 if narrow else 0)
        self.reading_panel.setMinimumHeight(520)
        self.split.setSizes([280, 550] if narrow else [330, 730])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'split'): self._layout_for_width()

    def release(self):
        if not self._closed:
            self._closed = True; self._epoch += 1; self._image_epoch += 1
            for name in tuple(self._jobs): self._cancel(name)
            self.close_zoom(); self._pixmap = None; self.confirm.setEnabled(False)
            self.reader.set_source(None); self._outline = None; self._picture_rows.clear()
            shutdown = getattr(self.tasks, 'shutdown', None)
            if callable(shutdown): shutdown(1000)

    def done(self, result):
        if not self._closed and result != QDialog.DialogCode.Accepted: self.requests = None
        self.release()
        super().done(result)

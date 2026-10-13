"""Source-grid table editing with fixed, verified image occurrences.

The widget only consumes detached models and explicitly supplied image bytes.
It neither discovers a profile nor persists a resource or image.
"""
from copy import deepcopy
from hashlib import sha256

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..desktop_handout_knowledge_tables import classroom_cell_text
from ..desktop_preparation_images import verify_image_bytes


def _label(text):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    result.setMinimumWidth(0)
    return result


def _source_offset(source, document_offset):
    """Map a Qt UTF-16 cursor boundary to an exact Python source boundary."""
    index = units = 0
    while index < len(source) and units < document_offset:
        units += 2 if ord(source[index]) > 0xffff else 1
        index += 2 if source[index:index + 2] == '\r\n' else 1
    return index


def _source_breaks(source):
    """Record only breaks that differ from the editor's ordinary new LF."""
    result, index, displayed = [], 0, 0
    while index < len(source):
        width = 2 if source[index:index + 2] == '\r\n' else 1
        value = source[index:index + width]
        if value in {'\r\n', '\r', '\u2029'}:
            result.append((displayed, value))
        index += width; displayed += 1
    return tuple(result)


class SourceTextEdit(QPlainTextEdit):
    """A plain editor that preserves unchanged source whitespace exactly."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = self._rendered = ''
        self._break_states = {}
        self._undo_steps = 0
        self._new_undo_command = False
        self._loading_source = False
        self.document().undoCommandAdded.connect(self._record_new_command)
        self.document().contentsChange.connect(self._capture_source_delta)

    def set_source_text(self, text):
        self._loading_source = True
        self.setPlainText(text)
        self._source = text
        self._rendered = self.document().toRawText()
        self._undo_steps = self.document().availableUndoSteps()
        self._break_states = {self._undo_steps: (self._version(self._rendered), _source_breaks(text))}
        self._new_undo_command = False
        self._loading_source = False

    @staticmethod
    def _version(text):
        return sha256(text.encode('utf-8', errors='surrogatepass')).digest()

    def _record_new_command(self):
        if not self._loading_source:
            self._new_undo_command = True
            self._break_states = {steps: state for steps, state in self._break_states.items()
                if steps <= self._undo_steps}

    def _capture_source_delta(self, position, removed, added):
        if self._loading_source:
            return
        current = self.document().toRawText()
        version = self._version(current)
        steps = self.document().availableUndoSteps()
        state = self._break_states.get(steps)
        if (not self._new_undo_command and steps != self._undo_steps
                and state is not None and state[0] == version):
            # Restore only the actual native undo/redo state. Equal displayed
            # text on another edit branch may have different source breaks.
            # Store sparse break metadata, not full per-keystroke text copies.
            breaks = dict(state[1])
            self._source = ''.join(breaks.get(i, '\n') if c == '\u2029' else c
                for i, c in enumerate(current))
        elif current != self._rendered:
            start = _source_offset(self._source, position)
            end = _source_offset(self._source, position + removed)
            raw = current.encode('utf-16-le', errors='surrogatepass')
            inserted = raw[2 * position:2 * (position + added)].decode('utf-16-le', errors='surrogatepass')
            self._source = self._source[:start] + inserted.replace('\u2029', '\n') + self._source[end:]
        self._rendered = current
        self._undo_steps = steps
        self._break_states[steps] = (version, _source_breaks(self._source))
        self._new_undo_command = False

    def source_text(self):
        return self._source


class VerifiedImagePreview(QLabel):
    """A resizable thumbnail of exactly the caller-supplied verified bytes."""
    def __init__(self, asset, data, parent=None):
        super().__init__(parent)
        self.setObjectName('NativeTableImagePreview')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumSize(0, 56)
        self.setMaximumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.source_pixmap = QPixmap()
        try:
            if asset is None or data is None:
                raise ValueError('原图未载入')
            verified = verify_image_bytes(asset, data)
            if not self.source_pixmap.loadFromData(verified):
                raise ValueError('原图无法显示')
        except (ValueError, TypeError, OSError):
            self.source_pixmap = QPixmap()
            self.setText('原图暂时无法显示')
        self.setAccessibleName('单元格中的原图，只读')
        self._fit()

    def _fit(self):
        if not self.source_pixmap.isNull():
            self.setPixmap(self.source_pixmap.scaled(max(1, self.width() - 8), 170,
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class NativeTableCellEditor(QWidget):
    """Edit only a source cell's ordered text segments, never its image slots.

    ``select_cell`` uses the source's one-based grid coordinates. A coordinate
    covered by a merge selects its visible restart cell. ``cells_value`` always
    returns every native cell, including unchanged continuation cells.
    """
    changed = Signal()

    def __init__(self, model, assets=(), image_data=None, parent=None):
        super().__init__(parent)
        self.setObjectName('NativeTableCellEditor')
        self.model = deepcopy(model)
        self._cells = self.model['cells']
        self._by_position = {(c['row'], c['column']): c for c in self._cells}
        self._assets = {a['asset_id']: deepcopy(a) for a in assets}
        self._image_data = dict(image_data or {})
        self._read_only = True
        self._loading = False
        self._current = None
        self.segment_edits = []
        self.image_previews = []
        self._anchors = {}
        self._continuations = {}
        self._pixmaps = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(_label('选择单元格编辑文字；图片按原位置显示。下标用 _{3}，上标用 ^{2+}。'))
        split = QSplitter(Qt.Orientation.Vertical)
        self.grid = QTableWidget()
        self.grid.setObjectName('NativeTableGrid')
        self.grid.setAccessibleName('讲义原表格，使用方向键选择单元格')
        self.grid.setMinimumSize(0, 80)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.grid.setIconSize(QSize(52, 32))
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.grid.horizontalHeader().setMinimumSectionSize(48)
        self.grid.verticalHeader().setDefaultSectionSize(54)
        split.addWidget(self.grid)

        self.cell_scroll = QScrollArea()
        self.cell_scroll.setObjectName('NativeTableCellScroll')
        self.cell_scroll.setWidgetResizable(True)
        self.cell_scroll.setMinimumSize(0, 90)
        self.cell_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        split.addWidget(self.cell_scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)
        navigation = QHBoxLayout()
        self.previous_button = QPushButton('上一格')
        self.next_button = QPushButton('下一格')
        self.previous_button.setObjectName('NativeTablePreviousCell')
        self.next_button.setObjectName('NativeTableNextCell')
        self.cell_position = _label('')
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.cell_position, 1)
        navigation.addWidget(self.next_button)
        root.addLayout(navigation)
        self.previous_button.clicked.connect(lambda: self._step(-1))
        self.next_button.clicked.connect(lambda: self._step(1))
        self._previous_shortcut = QShortcut(QKeySequence('Alt+Left'), self)
        self._next_shortcut = QShortcut(QKeySequence('Alt+Right'), self)
        for shortcut in (self._previous_shortcut, self._next_shortcut):
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._previous_shortcut.activated.connect(lambda: self._step(-1))
        self._next_shortcut.activated.connect(lambda: self._step(1))
        self._build_grid()
        self.grid.currentCellChanged.connect(self._grid_selected)
        if self._cells:
            self.select_cell(self._cells[0]['row'], self._cells[0]['column'])

    def _build_grid(self):
        rows = self.model['rows']
        columns = max((r['grid_before'] + r['grid_after'] +
            sum(c['column_span'] for c in r['cells']) for r in rows), default=1)
        self.grid.setRowCount(len(rows))
        self.grid.setColumnCount(columns)
        for ri in range(len(rows)):
            for ci in range(columns):
                blank = QTableWidgetItem('')
                blank.setFlags(Qt.ItemFlag.NoItemFlags)
                blank.setBackground(QColor('#edf0ee'))
                self.grid.setItem(ri, ci, blank)
        # Build merge coverage from the exact source starts and spans. No
        # omitted columns or synthetic continuation cells are introduced.
        for cell in self._cells:
            position = cell['row'], cell['column']
            if position in self._anchors:
                continue
            span, height = cell['column_span'], 1
            if cell['vertical_merge'] == 'restart':
                while True:
                    below = self._by_position.get((cell['row'] + height, cell['column']))
                    if (below is None or below['vertical_merge'] != 'continue'
                            or below['column_span'] != span):
                        break
                    self._continuations.setdefault(position, []).append(below)
                    height += 1
            for ri in range(cell['row'], cell['row'] + height):
                for ci in range(cell['column'], cell['column'] + span):
                    self._anchors[ri, ci] = position
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            item.setBackground(QColor('#ffffff' if cell['editable'] else '#edf0ee'))
            self.grid.setItem(cell['row'] - 1, cell['column'] - 1, item)
            if height > 1 or span > 1:
                self.grid.setSpan(cell['row'] - 1, cell['column'] - 1, height, span)
            self._refresh_grid_cell(cell)
        self._visible_positions = list(dict.fromkeys(self._anchors.values()))

    def _preview_text(self, cell):
        parts = []
        for index, segment in enumerate(cell['segments']):
            parts.append(classroom_cell_text(segment))
            if index < len(cell['slots']):
                parts.append('〔原图〕')
        return ''.join(parts)

    def _refresh_grid_cell(self, cell):
        item = self.grid.item(cell['row'] - 1, cell['column'] - 1)
        text = self._preview_text(cell)
        for continuation in self._continuations.get((cell['row'], cell['column']), []):
            if any(continuation['segments']) or continuation['slots']:
                text += '\n' + self._preview_text(continuation)
        item.setText(text)
        item.setToolTip(text)
        if cell['slots']:
            identity = cell['slots'][0]['asset_id']
            if identity not in self._pixmaps:
                preview = VerifiedImagePreview(self._assets.get(identity), self._image_data.get(identity))
                self._pixmaps[identity] = preview.source_pixmap
                preview.deleteLater()
            if not self._pixmaps[identity].isNull():
                item.setIcon(QIcon(self._pixmaps[identity]))

    def select_cell(self, row, column):
        """Select a one-based source position, resolving merged coverage."""
        anchor = self._anchors.get((row, column))
        if anchor is None:
            return False
        self.grid.setCurrentCell(anchor[0] - 1, anchor[1] - 1)
        if self._current != anchor:
            self._show_cell(anchor)
        self.grid.scrollToItem(self.grid.item(anchor[0] - 1, anchor[1] - 1))
        return True

    def _grid_selected(self, row, column, *_):
        anchor = self._anchors.get((row + 1, column + 1))
        if anchor is not None:
            self._show_cell(anchor)

    def _show_cell(self, position):
        self.store_current()
        self._loading = True
        self._current = position
        self.segment_edits = []
        self.image_previews = []
        cell = self._by_position[position]
        content = QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(content)
        heading = f'第{position[0]}行，第{position[1]}列'
        if cell['column_span'] > 1:
            heading += f'（横向合并{cell["column_span"]}列）'
        if not cell['editable']:
            heading += ' · 合并续接内容，只读'
        layout.addWidget(_label(heading))
        self._add_segments(layout, cell, editable=True)
        for continuation in self._continuations.get(position, []):
            if any(continuation['segments']) or continuation['slots']:
                layout.addWidget(_label('原合并单元格的续接内容（只读）'))
                self._add_segments(layout, continuation, editable=False)
        layout.addStretch()
        previous = self.cell_scroll.takeWidget()
        if previous is not None:
            previous.deleteLater()
        self.cell_scroll.setWidget(content)
        self.cell_position.setText(f'第{position[0]}行 · 第{position[1]}列')
        index = self._visible_positions.index(position)
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(index + 1 < len(self._visible_positions))
        self._loading = False

    def _add_segments(self, layout, cell, *, editable):
        for index, segment in enumerate(cell['segments']):
            edit = SourceTextEdit()
            edit.setMinimumWidth(0)
            edit.setFixedHeight(72)
            edit.set_source_text(segment)
            edit.setReadOnly(self._read_only or not cell['editable'] or not editable)
            edit.setAccessibleName(f'第{cell["row"]}行第{cell["column"]}列，文字片段{index + 1}')
            if cell['slots']:
                title = ('第一张图前的文字' if index == 0 else
                    '最后一张图后的文字' if index == len(cell['slots']) else f'第{index}与第{index + 1}张图之间的文字')
                edit.setPlaceholderText(title)
                layout.addWidget(_label(title))
            layout.addWidget(edit)
            if editable:
                edit.setObjectName(f'NativeTableSegment{index}')
                self.segment_edits.append(edit)
                edit.textChanged.connect(self._segment_changed)
            if index < len(cell['slots']):
                slot = cell['slots'][index]
                preview = VerifiedImagePreview(self._assets.get(slot['asset_id']),
                    self._image_data.get(slot['asset_id']))
                preview.setProperty('image_block_id', slot['image_block_id'])
                preview.setAccessibleName(f'第{index + 1}张原图，位置固定')
                layout.addWidget(preview)
                self.image_previews.append(preview)

    def _segment_changed(self):
        if self._loading or self._read_only:
            return
        self.store_current()
        self.changed.emit()

    def store_current(self):
        if self._loading or self._current is None or self._read_only:
            return
        cell = self._by_position[self._current]
        if cell['editable']:
            cell['segments'] = [edit.source_text() for edit in self.segment_edits]
            self._refresh_grid_cell(cell)

    def cells_value(self):
        self.store_current()
        return [{key: deepcopy(cell[key]) for key in ('row', 'column', 'segments')}
            for cell in self._cells]

    @property
    def segment_editors(self):
        return self.segment_edits

    def set_read_only(self, value):
        self.store_current()
        self._read_only = bool(value)
        if self._current is not None:
            cell = self._by_position[self._current]
            for edit in self.segment_edits:
                edit.setReadOnly(self._read_only or not cell['editable'])

    def _step(self, delta):
        if self._current is None:
            return
        index = self._visible_positions.index(self._current) + delta
        if 0 <= index < len(self._visible_positions):
            self.select_cell(*self._visible_positions[index])

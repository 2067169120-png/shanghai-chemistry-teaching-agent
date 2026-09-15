"""Pixel-coordinate selection of number regions; source pixels remain available."""
from __future__ import annotations
from copy import deepcopy
import math

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QImage, QPainter, QPen, QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QSpinBox, QCheckBox, QWidget, QComboBox)

from ..desktop_raster_numbers import validate_edits, render_numbered_image, RasterNumberError


class NumberCanvas(QWidget):
    region_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 160)
        self.image = QImage()
        self.selection = None
        self._anchor = None
        self._view = QRectF()
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName('题图：用鼠标框选旧题号，不框入公式或题干')

    def set_image(self, data):
        image = QImage.fromData(data)
        if image.isNull():
            raise RasterNumberError('题图无法显示，请重新准备预览。')
        self.image = image
        self.update()

    def view_rect(self):
        if self.image.isNull():
            return QRectF()
        scale = min(max(1, self.width()-16)/self.image.width(), max(1, self.height()-16)/self.image.height())
        w, h = self.image.width()*scale, self.image.height()*scale
        return QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('white'))
        self._view = self.view_rect()
        if self._view.isEmpty():
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(self._view, self.image)
        if self.selection:
            x0,y0,x1,y1 = self.selection
            sx, sy = self._view.width()/self.image.width(), self._view.height()/self.image.height()
            painter.setPen(QPen(QColor('#247F58'), 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(self._view.left()+x0*sx, self._view.top()+y0*sy, (x1-x0)*sx, (y1-y0)*sy))

    def _point(self, position):
        rect = self.view_rect()
        if rect.isEmpty():
            return None
        return (min(self.image.width(), max(0, (position.x()-rect.left())*self.image.width()/rect.width())),
                min(self.image.height(), max(0, (position.y()-rect.top())*self.image.height()/rect.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.view_rect().contains(event.position()):
            self._anchor = self._point(event.position())
            self.selection = None

    def mouseMoveEvent(self, event):
        if self._anchor is not None:
            point = self._point(event.position())
            if point:
                self.selection = [math.floor(min(self._anchor[0],point[0])), math.floor(min(self._anchor[1],point[1])),
                                  math.ceil(max(self._anchor[0],point[0])), math.ceil(max(self._anchor[1],point[1]))]
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._anchor is not None:
            self.mouseMoveEvent(event)
            self._anchor = None
            if self.selection:
                self.region_selected.emit(list(self.selection))


class ScanNumberDialog(QDialog):
    """Edits apply to this preview only; same-image copies use the same region."""
    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.setWindowTitle('调整图片题号 · 仅修改本次试卷')
        self.resize(1050, 760)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.images = catalog['images']
        self.edits = []
        self._history = []
        root = QVBoxLayout(self)
        hint = QLabel('选题图 → 只框旧号 → 填本卷号 → 添加替换。原图不变；同图在学生、教师两版中的副本同步替换。')
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.picker = QComboBox()
        for i, image in enumerate(self.images):
            roles = '、'.join('学生版' if x=='student' else '教师版' for x in image['audiences'])
            self.picker.addItem(f"图{i+1} · {image['width']}×{image['height']} · {roles}")
        root.addWidget(self.picker)
        self.location = QLabel(); self.location.setWordWrap(True); root.addWidget(self.location)
        self.canvas = NumberCanvas()
        root.addWidget(self.canvas, 1)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('改为本卷第'))
        self.number = QSpinBox(); self.number.setRange(1,999)
        self.number.setAccessibleName('替换后的本卷题号')
        controls.addWidget(self.number)
        self.dot = QCheckBox('数字后加点'); self.dot.setChecked(True)
        controls.addWidget(self.dot)
        self.add = QPushButton('添加替换'); self.add.setEnabled(False)
        controls.addWidget(self.add)
        self.original = QCheckBox('对照原图')
        controls.addWidget(self.original)
        root.addLayout(controls)
        self.regions = QListWidget(); self.regions.setMaximumHeight(105)
        self.regions.setAccessibleName('本次已添加的题号替换，可移除或撤销')
        root.addWidget(self.regions)
        edit_actions = QHBoxLayout()
        self.remove = QPushButton('移除选中区域')
        self.undo = QPushButton('撤销上一步')
        self.clear = QPushButton('清除全部替换')
        for button in (self.remove,self.undo,self.clear): edit_actions.addWidget(button)
        root.addLayout(edit_actions)
        self.status = QLabel('图片内的“第几题”引用需单独框选数字；未框选处不会自动识别或修改。')
        self.status.setWordWrap(True); root.addWidget(self.status)
        if catalog.get('unsupported_parts'):
            self.status.setText('部分图片格式或方向暂不支持调整；这些原图会保留。请在最终卷面中逐项核对。')
        actions = QHBoxLayout()
        self.cancel = QPushButton('取消'); self.cancel.clicked.connect(self.reject)
        self.apply = QPushButton('生成两版排版预览'); self.apply.clicked.connect(self.accept)
        actions.addWidget(self.cancel); actions.addWidget(self.apply); root.addLayout(actions)
        self.picker.currentIndexChanged.connect(self._select_image)
        self.canvas.region_selected.connect(lambda _: self.add.setEnabled(True))
        self.add.clicked.connect(self._add_region)
        self.remove.clicked.connect(self._remove_region)
        self.undo.clicked.connect(self._undo)
        self.clear.clicked.connect(self._clear)
        self.original.toggled.connect(self._refresh_image)
        self.regions.currentRowChanged.connect(self._show_region)
        self.undo.setEnabled(False)
        self._select_image()

    def _snapshot(self):
        self._history.append(deepcopy(self.edits))
        self._history = self._history[-100:]

    def _select_image(self, *_):
        self.canvas.selection = None
        self.add.setEnabled(False)
        if self.images:
            image = self.images[self.picker.currentIndex()]
            locations = image.get('contexts', [])
            self.location.setText('位置：' + ('；'.join(locations[:2]) or '请对照当前卷题序填写新号。'))
        self._refresh_image()

    def _refresh_image(self, *_):
        if not self.images: return
        image = self.images[self.picker.currentIndex()]
        edits = [e for e in self.edits if e['image_sha256']==image['image_sha256']]
        data = image['data'] if self.original.isChecked() or not edits else render_numbered_image(image['data'], edits)
        self.canvas.set_image(data)

    def _add_region(self):
        image = self.images[self.picker.currentIndex()]
        edit = {'image_sha256':image['image_sha256'], 'box':self.canvas.selection,
                'number':self.number.value(), 'punctuation':'.' if self.dot.isChecked() else ''}
        try:
            checked = validate_edits(self.edits+[edit], self.images)
        except RasterNumberError as exc:
            self.status.setText(str(exc)); return
        self._snapshot(); self.edits = checked
        self.canvas.selection = None; self.add.setEnabled(False)
        self.original.setChecked(False)
        self._render_list()

    def _render_list(self):
        self.regions.clear()
        ids = {r['image_sha256']:i+1 for i,r in enumerate(self.images)}
        for edit in self.edits:
            self.regions.addItem(f"图{ids[edit['image_sha256']]} → {edit['number']}{edit['punctuation']} · 区域 {edit['box']}")
        self.undo.setEnabled(bool(self._history))
        self.status.setText(f'已添加{len(self.edits)}个区域。只用于当前题序；重新排序或重新生成时需要重新核对。')
        self._refresh_image()

    def _show_region(self, index):
        if not 0 <= index < len(self.edits): return
        edit = self.edits[index]
        self.picker.setCurrentIndex(next(i for i,r in enumerate(self.images) if r['image_sha256']==edit['image_sha256']))
        self.canvas.selection = list(edit['box']); self.canvas.update()
        self.add.setEnabled(False)

    def _remove_region(self):
        index = self.regions.currentRow()
        if 0 <= index < len(self.edits):
            self._snapshot(); self.edits.pop(index); self.canvas.selection=None; self._render_list()

    def _undo(self):
        if self._history:
            self.edits=self._history.pop(); self.canvas.selection=None; self._render_list()

    def _clear(self):
        if self.edits:
            self._snapshot(); self.edits=[]; self.canvas.selection=None; self._render_list()

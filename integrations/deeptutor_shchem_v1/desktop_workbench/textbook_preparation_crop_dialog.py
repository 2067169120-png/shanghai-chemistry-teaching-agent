"""Native original-page selection and crop preview before lesson handoff."""
from __future__ import annotations

from copy import deepcopy
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from ..desktop_preparation_images import verify_image_bytes
from .components import page_scroll, set_status
from .personal_visual_crop_dialog import CropCanvas, _CropImagePreview, _bounds


def _label(text):
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True); value.setMinimumWidth(0)
    return value


class TextbookPreparationCropDialog(QDialog):
    def __init__(self, facade, tasks, selection, parent=None, *, allow_composition_handoff=False):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.selection = deepcopy(selection)
        self.selection_request = None
        self.handoff_mode = 'assets'
        self.allow_composition_handoff = bool(allow_composition_handoff)
        self._page = self._preview = self._chosen_bounds = None
        self._busy = self._painting = self._closed = False
        self._ticket = 0
        self._task_id = None
        if hasattr(tasks,'task_cancelled'):
            tasks.task_cancelled.connect(self._task_cancelled)
        if hasattr(tasks,'task_finished'):
            tasks.task_finished.connect(self._task_finished)
        self.setWindowTitle('选取教材图表，加入本次备课')
        self.resize(1080, 820); self.setMinimumSize(390, 600)
        root = QVBoxLayout(self); root.setContentsMargins(12, 12, 12, 12)
        root.addWidget(_label('在原页拖动选框，或使用边界输入框微调；核对裁片、图题与用途后勾选确认，再加入当前备课。'))
        self.origin = _label('正在核对素材来源与原页…'); root.addWidget(self.origin)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        left, right = QWidget(), QWidget()
        source_layout, preview_layout = QVBoxLayout(left), QVBoxLayout(right)
        for layout in (source_layout, preview_layout): layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = CropCanvas(); source_layout.addWidget(self.canvas, 1)
        source_tools = QHBoxLayout()
        self.fit_source, self.actual_source = QPushButton('适应原页'), QPushButton('原始大小')
        self.fit_source.clicked.connect(self.canvas.fit_page)
        self.actual_source.clicked.connect(lambda:self.canvas.set_zoom(100))
        for button in (self.fit_source,self.actual_source):
            button.setAutoDefault(False); source_tools.addWidget(button)
        source_layout.addLayout(source_tools)
        self.whole_page = QPushButton('使用整页'); self.whole_page.setObjectName('QuietButton')
        self.whole_page.clicked.connect(self._choose_whole_page)
        source_layout.addWidget(self.whole_page)
        self.edge_layout = QGridLayout(); self.edges = {}; self.edge_columns = []
        for index,(key, title) in enumerate((('left','左'),('top','上'),('right','右'),('bottom','下'))):
            spin = QSpinBox(); spin.setRange(0, 6000); spin.setMinimumWidth(95)
            spin.setAccessibleName('原页选框'+title+'边界像素')
            spin.valueChanged.connect(self._edges_changed)
            self.edges[key] = spin
            column = QWidget(); layout = QVBoxLayout(column); layout.setContentsMargins(0,0,0,0)
            layout.addWidget(_label(title)); layout.addWidget(spin)
            self.edge_columns.append(column); self.edge_layout.addWidget(column,0,index)
        source_layout.addLayout(self.edge_layout)
        self.canvas.bounds_changed.connect(self._canvas_changed)
        self.crop_preview = _CropImagePreview(); preview_layout.addWidget(self.crop_preview, 1)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.caption, self.purpose = QLineEdit(), QLineEdit()
        self.caption.setMaxLength(160); self.purpose.setMaxLength(500)
        self.caption.setPlaceholderText('例如：结构怎样影响材料的性质？')
        self.caption.setToolTip('导学问题或图题显示在课页左上；可用“为什么”“怎样”等问题引导观察。')
        self.purpose.setPlaceholderText('填写这张图在本次课中的观察或讲解用途')
        for control in (self.caption, self.purpose):
            control.setMinimumWidth(0); control.textChanged.connect(self._invalidate_preview)
        form.addRow('导学问题／图题',self.caption); form.addRow('本次用途',self.purpose)
        preview_layout.addLayout(form)
        self.checked = QCheckBox('已查看并确认当前裁片')
        self.checked.toggled.connect(self._update_controls); preview_layout.addWidget(self.checked)
        preview_layout.addWidget(_label('核对文字、公式与图例完整清晰，边缘没有截断。'))
        preview_layout.addWidget(_label('确认后在备课中核对图片用途与显示位置；生成课件或学习单后，打开实际文件检查。来源裁选记录会保留。'))
        self.splitter.addWidget(left); self.splitter.addWidget(right); self.splitter.setSizes([550,470])
        self.splitter.setChildrenCollapsible(False)
        self.scroll = page_scroll(self.splitter)
        root.addWidget(self.scroll,1)
        self.status = _label('尚未框选。原页读取不会保存图片或调用模型。'); root.addWidget(self.status)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.reload = QPushButton('重读原页'); self.reload.clicked.connect(self._load_page)
        self.preview_button = QPushButton('预览裁片'); self.preview_button.clicked.connect(self._preview_crop)
        self.confirm = QPushButton('加入当前备课'); self.confirm.clicked.connect(self._confirm)
        self.compose = QPushButton('加入并编排')
        self.compose.setObjectName('PrimaryAction')
        self.compose.setToolTip('直接加入当前课件，打开编排画布；不用再从图库选择。')
        self.compose.clicked.connect(self._confirm_composition)
        self.compose.setVisible(self.allow_composition_handoff)
        if self.allow_composition_handoff: self.confirm.setText('只入备课图库')
        self.cancel = QPushButton('返回教材'); self.cancel.clicked.connect(self.reject)
        for button in (self.reload,self.preview_button,self.confirm,self.cancel):
            button.setAutoDefault(False); self.actions.addWidget(button)
        self.compose.setAutoDefault(False)
        if self.allow_composition_handoff: self.actions.insertWidget(2, self.compose)
        root.addLayout(self.actions)
        self._update_controls(); self._load_page()

    def _invalidate_preview(self, *_):
        self._ticket += 1
        self._preview = None
        with QSignalBlocker(self.checked): self.checked.setChecked(False)
        self.crop_preview.clear('选框、图题或用途变化后，需要重新预览。')
        self._update_controls()

    def _update_controls(self, *_):
        available = self._page is not None and not self._busy and not self._closed
        for control in (self.whole_page,self.fit_source,self.actual_source,self.canvas,self.caption,self.purpose,*self.edges.values()):
            control.setEnabled(available)
        self.reload.setEnabled(not self._busy and not self._closed)
        self.preview_button.setEnabled(available and self._chosen_bounds is not None
            and bool(self.caption.text().strip()) and bool(self.purpose.text().strip()))
        self.checked.setEnabled(available and self._preview is not None)
        self.confirm.setEnabled(available and self._preview is not None and self.checked.isChecked())
        self.compose.setEnabled(self.allow_composition_handoff and self.confirm.isEnabled())

    def _load_page(self):
        if self._closed or self._busy:return
        self._invalidate_preview(); self._page = self._chosen_bounds = None
        self.canvas.scene().clear(); self._busy = True
        self._ticket += 1; ticket = self._ticket
        self._update_controls()
        def ready(page):
            if self._closed or ticket != self._ticket:return
            self._busy = False; self._task_id = None
            try:
                if (page['visual_asset_id'] != self.selection['visual_asset_id']
                    or page['revision'] != self.selection['revision']):
                    raise ValueError('原页身份与所选教材素材不一致。')
                pixmap = QPixmap()
                if not pixmap.loadFromData(page['page_data']) or (pixmap.width(),pixmap.height()) != (page['width'],page['height']):
                    raise ValueError('教材原页图像无法完整显示。')
                self._page = page
                self.canvas.set_source(pixmap,(0,0,page['width'],page['height']))
                for key,spin in self.edges.items():
                    with QSignalBlocker(spin):
                        spin.setMaximum(page['width'] if key in ('left','right') else page['height'])
                self._set_edges((0,0,page['width'],page['height']))
                with QSignalBlocker(self.caption): self.caption.setText(page['label'][:160])
                printed = page['printed_page'] if type(page['printed_page']) is int else '待核对'
                self.origin.setText(f"{page['source_title']} · PDF第{page['pdf_page']}页／印刷{printed}")
                set_status(self.status,'info','原页已打开。请拖动框选，或明确选择使用整页，再填写本次用途。')
            except (KeyError,ValueError,TypeError) as exc:
                self._page = None; self.canvas.scene().clear()
                set_status(self.status,'error',str(exc))
            self._update_controls()
        def failed(message):
            if self._closed or ticket != self._ticket:return
            self._busy = False; self._task_id = None; self._page = None; self._chosen_bounds = None
            self.canvas.scene().clear(); self._update_controls()
            set_status(self.status,'error',message)
        try:
            self._task_id = self.tasks.submit('读取教材框选原页',lambda:self.facade.preparation_textbook_crop_page(self.selection),
                              on_success=ready,on_failure=failed)
        except RuntimeError as exc: failed(str(exc))

    def _set_edges(self, edges):
        self._painting = True
        try:
            for key,value in zip(('left','top','right','bottom'),edges,strict=True):
                self.edges[key].setValue(value)
        finally:self._painting = False

    def _canvas_changed(self, edges):
        if self._page is None or self._busy:return
        self.canvas.set_bounds(edges)
        self._set_edges(edges); self._chosen_bounds = self._bounds_dict(edges)
        self._invalidate_preview()

    @staticmethod
    def _bounds_dict(edges):
        return dict(zip(('left','top','right','bottom'),edges,strict=True))

    def _edges_changed(self, *_):
        if self._painting or self._page is None or self._busy:return
        value = {key:spin.value() for key,spin in self.edges.items()}
        try:
            edges = _bounds(value,self._page['width'],self._page['height'])
            self.canvas.set_bounds(edges); self._chosen_bounds = value
        except (ValueError,TypeError): self._chosen_bounds = None
        self._invalidate_preview()

    def _choose_whole_page(self):
        if self._page is None:return
        edges = (0,0,self._page['width'],self._page['height'])
        self.canvas.set_bounds(edges); self._canvas_changed(edges)

    def _preview_crop(self):
        if not self.preview_button.isEnabled():return
        self._invalidate_preview(); self._busy = True
        self._ticket += 1; ticket = self._ticket
        arguments = (deepcopy(self.selection),self._page['page_revision'],deepcopy(self._chosen_bounds),
                     self.caption.text().strip(),self.purpose.text().strip())
        self._update_controls()
        def ready(value):
            if self._closed or ticket != self._ticket:return
            self._busy = False; self._task_id = None
            try:
                verify_image_bytes(value['image_asset'],value['image_bytes'])
                self.crop_preview.set_bytes(value['image_bytes'],value['image_asset']['caption'])
                self._preview = value
                self.scroll.ensureWidgetVisible(self.crop_preview)
                asset = value['image_asset']
                set_status(self.status,'info',f"裁片{asset['width']}×{asset['height']}像素。请核对后勾选确认；内容可向下滚动。")
            except (ValueError,KeyError,TypeError):
                self._preview = None; self.crop_preview.clear('裁片无法核对，请重新预览。')
                set_status(self.status,'error','裁片内容或完整性无法核对，未加入备课。')
            self._update_controls()
        def failed(message):
            if self._closed or ticket != self._ticket:return
            self._busy = False; self._task_id = None; self._preview = None; self._update_controls()
            set_status(self.status,'error',message)
        try:
            self._task_id = self.tasks.submit('核对教材裁片',lambda:self.facade.preparation_textbook_crop_preview(*arguments),
                              on_success=ready,on_failure=failed)
        except RuntimeError as exc: failed(str(exc))

    def _task_cancelled(self,task_id,_label):
        if self._closed or task_id != self._task_id:return
        self._task_id = None; self._busy = False
        self._invalidate_preview()
        set_status(self.status,'attention','本次读取已取消，可以重读原页或重新预览裁片。')

    def _task_finished(self,task_id):
        if self._closed or task_id != self._task_id:return
        self._task_id = None; self._busy = False
        self._update_controls()

    def _confirm(self):
        if not self.confirm.isEnabled():return
        self.handoff_mode = 'assets'
        self.selection_request = self._current_request()
        self.accept()

    def _confirm_composition(self):
        if not self.compose.isEnabled(): return
        self.handoff_mode = 'composition'
        self.selection_request = self._current_request()
        self.accept()

    def _current_request(self):
        return {'selection':deepcopy(self.selection),'page_revision':self._page['page_revision'],
            'bounds':deepcopy(self._chosen_bounds),'caption':self.caption.text().strip(),
            'purpose':self.purpose.text().strip(),'expected_preview_revision':self._preview['preview_revision'],
            'expected_image_sha256':self._preview['image_asset']['sha256'],'selection_confirmed':True}

    def resizeEvent(self,event):
        if hasattr(self,'splitter'):
            narrow = event.size().width()<800
            self.splitter.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
            self.actions.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
            for widget in self.edge_columns:self.edge_layout.removeWidget(widget)
            for index,widget in enumerate(self.edge_columns):
                self.edge_layout.addWidget(widget,index//2 if narrow else 0,index%2 if narrow else index)
        super().resizeEvent(event)

    def done(self,result):
        self._closed = True; self._ticket += 1
        if self._task_id is not None:
            self.tasks.cancel(self._task_id); self._task_id = None
        self._page = self._preview = None
        self.crop_preview.clear('')
        self.canvas.scene().clear()
        super().done(result)

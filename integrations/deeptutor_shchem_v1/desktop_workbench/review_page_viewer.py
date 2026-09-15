"""Read one exact submission page at a time, with reversible visual evidence overlay."""
from __future__ import annotations
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QGraphicsScene, QGraphicsView, QHBoxLayout,
                              QLabel, QPushButton, QVBoxLayout, QWidget)
from ..desktop_review_evidence import matched_page, value


class ReviewPageViewer(QWidget):
    def __init__(self, facade, tasks, summary, parent=None):
        super().__init__(parent)
        self.facade, self.tasks, self.summary = facade, tasks, summary
        self.student_id = value(summary, 'student_id')
        self.submission_id = value(summary, 'submission_id')
        self._epoch, self._task, self._closed = 0, None, False
        self._item = None
        self._pixmap_item = self._box_item = None
        self._fit, self._zoom = True, 1.0
        self.loaded_identity = None
        self._active_region = None
        root = QVBoxLayout(self);root.setContentsMargins(0,0,0,0);root.setSpacing(8)
        self.role = QComboBox();self.role.setAccessibleName('查看材料种类')
        for title, key in (('学生作答', 'student_work_pages'), ('原题与公共材料', 'question_pages'), ('参考答案', 'reference_answer_pages')):
            self.role.addItem(title, key)
        self.pages = QComboBox();self.pages.setAccessibleName('查看本次材料页面')
        root.addWidget(self.role);root.addWidget(self.pages)
        self.location = QComboBox();self.location.setAccessibleName('评分点证据定位')
        root.addWidget(self.location)
        self.notice = QLabel('请选择待复核题目。');self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText);self.notice.setObjectName('MutedLabel')
        root.addWidget(self.notice)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene);self.view.setAccessibleName('原作答与评分证据画布')
        self.view.setMinimumSize(240,200)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        root.addWidget(self.view,1)
        toolbar=QHBoxLayout()
        for text, action in (('−',lambda:self.zoom(.8)), ('适应页面',self.fit_page),
                             ('+',lambda:self.zoom(1.25)), ('重新读图',self.load_page)):
            button=QPushButton(text);button.setObjectName('QuietButton')
            button.clicked.connect(action);toolbar.addWidget(button)
        root.addLayout(toolbar)
        self.role.currentIndexChanged.connect(self._role_changed)
        self.pages.currentIndexChanged.connect(self.load_page)
        self.location.currentIndexChanged.connect(self._locate)

    def view_state(self):
        page=self.pages.currentData()
        return {"role":self.role.currentData(), "file_id":value(page,"file_id"), "page_sha256":value(page,"sha256")}

    def set_item(self, item, saved=None):
        self._item=item;self._active_region=None
        self.location.blockSignals(True);self.location.clear()
        self.location.addItem('整页查看（不裁去公共材料）',None)
        for region in value(item,'evidence_regions',()):
            self.location.addItem(value(region,'label_zh','AI证据定位'),region)
        self.location.blockSignals(False)
        self.role.blockSignals(True)
        self.role.setCurrentIndex(max(0,self.role.findData(saved.get('role'))) if saved else 0)
        self.role.blockSignals(False)
        self._role_changed()
        if saved:
            for i in range(self.pages.count()):
                page=self.pages.itemData(i)
                if (value(page,'file_id'),value(page,'sha256'))==(saved.get('file_id'),saved.get('page_sha256')):
                    self.pages.setCurrentIndex(i);break

    def _role_changed(self,*_):
        self._active_region=None
        self.location.blockSignals(True);self.location.setCurrentIndex(0);self.location.blockSignals(False)
        pages=[p for p in value(self.summary,'pages',()) if value(p,'role')==self.role.currentData()]
        expected=matched_page(self.summary,value(self._item,'match_id'),self.role.currentData())
        self.pages.blockSignals(True);self.pages.clear()
        selected=-1
        for i,page in enumerate(pages):
            exact=(value(expected,'sha256')==value(page,'sha256') and value(expected,'file_id')==value(page,'file_id'))
            self.pages.addItem(str(value(page,'label_zh','材料页'))+(' · 本题对应页' if exact else ''),page)
            if exact:selected=i
        # An unknown pairing never silently falls back to another page.
        self.pages.setCurrentIndex(selected)
        self.pages.blockSignals(False)
        self.load_page()

    def _locate(self,*_):
        region=self.location.currentData();self._active_region=region
        if region is None:
            self._draw_region();self.fit_page();return
        role=value(region,'role');digest=value(region,'page_sha256')
        self.role.blockSignals(True);self.role.setCurrentIndex(self.role.findData(role));self.role.blockSignals(False)
        self.pages.blockSignals(True);self.pages.clear();selected=-1
        for page in value(self.summary,'pages',()):
            if value(page,'role')!=role:continue
            self.pages.addItem(str(value(page,'label_zh','材料页')),page)
            if value(page,'sha256')==digest:selected=self.pages.count()-1
        self.pages.setCurrentIndex(selected);self.pages.blockSignals(False)
        self.load_page()

    def load_page(self,*_):
        self._epoch+=1;epoch=self._epoch
        if self._task:self.tasks.cancel(self._task)
        self._task=None;self.loaded_identity=None
        self.scene.clear();self._pixmap_item=self._box_item=None
        page=self.pages.currentData()
        if page is None:
            self.notice.setText('本题没有对应的这类页面；可在页面列表中手动查看已上传材料。')
            return
        identity=(value(page,'file_id'),value(page,'sha256'))
        region=self._active_region
        if region is not None and value(region,'page_sha256')!=identity[1]:
            self._active_region=None
            self.location.blockSignals(True);self.location.setCurrentIndex(0);self.location.blockSignals(False)
        self.notice.setText('正在读取本机原页…')
        def read():
            data,mime=self.facade.student_submission_page(student_id=self.student_id,
                submission_id=self.submission_id,file_id=identity[0],page_sha256=identity[1])
            image=QImage.fromData(data) if mime.startswith('image/') else QImage()
            if image.isNull():raise ValueError('Page is not a readable image')
            return image
        def loaded(image):
            if self._closed or epoch!=self._epoch:return
            self._task=None;self.loaded_identity=identity
            self._pixmap_item=self.scene.addPixmap(QPixmap.fromImage(image))
            self.scene.setSceneRect(QRectF(0,0,image.width(),image.height()))
            self.notice.setText('原始整页，可放大并拖动。框线为AI定位，不是教师批注，也不改原图。')
            self._draw_region();self.fit_page()
        def failed(message):
            if self._closed or epoch!=self._epoch:return
            self._task=None
            self.notice.setText('原页暂时无法显示，请重新读图或回到材料匹配页检查。不会用上一题的图代替。')
        self._task=self.tasks.submit('读取本题原作答',read,on_success=loaded,on_failure=failed)

    def _draw_region(self):
        if self._box_item is not None:
            self.scene.removeItem(self._box_item);self._box_item=None
        region=self._active_region
        if self._pixmap_item is None or region is None:return
        if self.loaded_identity is None or self.loaded_identity[1]!=value(region,'page_sha256'):return
        x,y,w,h=value(region,'box');rect=self.scene.sceneRect()
        pen=QPen(QColor('#b56c12'),2,Qt.PenStyle.DashLine);pen.setCosmetic(True)
        self._box_item=self.scene.addRect(x*rect.width(),y*rect.height(),w*rect.width(),h*rect.height(),pen)

    def fit_page(self,*_):
        self._fit=True
        if self._pixmap_item is not None:
            self.view.fitInView(self.scene.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
            self._zoom=self.view.transform().m11()

    def zoom(self,multiplier):
        if self._pixmap_item is None:return
        self._fit=False
        target=max(.05,min(6,self._zoom*multiplier))
        self.view.scale(target/self._zoom,target/self._zoom);self._zoom=target

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self._fit:QTimer.singleShot(0,self.fit_page)

    def stop(self):
        self._closed=True;self._epoch+=1
        if self._task:self.tasks.cancel(self._task)
        self._task=None

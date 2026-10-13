"""A lesson crop's read-only original page in the existing PDF reader."""
from copy import deepcopy

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton
from .textbook_source_dialog import TextbookSourceDialog


class _CropSourcePort:
    def __init__(self, facade, item, assets, context):
        self.facade, self.item, self.assets = facade, deepcopy(item), deepcopy(assets)
        self.context = context

    def preparation_textbook_asset_source(self, visual_asset_id, revision):
        if {'visual_asset_id': visual_asset_id, 'revision': revision} != self.context['selection']:
            raise ValueError('教材裁片原页身份已变化，请返回课件后重新打开。')
        return self.facade.preparation_textbook_crop_original(
            self.item, self.assets, expected_binding=self.context['binding_sha256'])['source']


class TextbookCropSourceDialog(TextbookSourceDialog):
    """No crop/import/edit controls, reading positions or history transactions."""
    def __init__(self, facade, item, assets, context, parent=None, *, current=None):
        self._current = current or (lambda: None)
        self._crop_title = item['title']
        self.action = None
        self.section_materials_requested = False
        super().__init__(_CropSourcePort(facade, item, assets, context), context['selection'],
                         parent, reading_mode='asset')
        self.setWindowTitle('对照教材原页')
        self.setMinimumSize(360, 500)
        # Generic asset readers expose candidate indexes and study hints. A
        # saved crop already has a precise source binding; show that scope only.
        while self.summary_tabs.count() > 1:
            self.summary_tabs.removeTab(self.summary_tabs.count()-1)
        self.reading_hints.hide();self.asset_list.hide();self.asset_details.hide()
        self.summary_tabs.setTabText(0,'原页定位')
        self.summary_tabs.setTabBarAutoHide(True)
        self.summary_tabs.setAccessibleName('当前课件教材裁片的原页定位')
        self.summary.setAccessibleName('当前教材图题、册名、PDF文件页与印刷页码')
        self.summary_tabs.setMaximumHeight(85)
        self.summary_tabs.setMinimumHeight(70)
        self.heading.hide()
        self.status.setText('对照课件裁片对应的完整教材页，可缩放查看；关闭后返回当前课件。')
        self.excerpt_button.hide()
        self.current_page_crop_button.hide()
        self.close_button = next(button for button in self.findChildren(QPushButton)
            if button.text() == '关闭，返回选材')
        self.close_button.setText('返回课件')
        self.close_button.setAccessibleName('关闭教材原页并返回当前课件')
        self.section_materials_button = QPushButton('本节按知识点选材…')
        self.section_materials_button.setObjectName('PrimaryAction')
        self.section_materials_button.setEnabled(False)
        self.section_materials_button.clicked.connect(self._choose_section_materials)
        self.layout().insertWidget(self.layout().indexOf(self.close_button), self.section_materials_button)
        self.reading_state_changed.connect(self._reading_ready)
        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(100)
        self._stale_timer.timeout.connect(self._guard_current)
        self._stale_timer.start()

    def _guard_current(self):
        if self._closed:
            return False
        try:
            self._current()
            if hasattr(self, 'section_materials_button'):
                self.section_materials_button.setEnabled(self._loaded_pages and self._source is not None)
            return True
        except (ValueError, OSError) as exc:
            self._stale_timer.stop()
            self.section_materials_button.setEnabled(False)
            self._set_controls(False)
            self.status.setText(str(exc))
            self.reject()
            return False

    def _reading_ready(self):
        ready = self._guard_current() and self._loaded_pages and self._source is not None
        self.section_materials_button.setEnabled(bool(ready))

    def _choose_section_materials(self):
        self._reading_ready()
        if not self.section_materials_button.isEnabled():
            return
        self.action = 'section_materials'
        self.section_materials_requested = True
        self.accept()

    def _source_ready(self, source):
        if self._guard_current():
            super()._source_ready(source)
            if not self._closed and self._source is not None:
                printed = source.get('printed_page')
                page_label = f"PDF第{source['pdf_pages'][0]}页"
                if type(printed) is int:page_label += f' · 印刷第{printed}页'
                self.summary.setPlainText(self._crop_title + '\n' + source['source_name'] + '\n' + page_label)
                self.summary_tabs.setCurrentIndex(0)

    def _document_status(self,status):
        super()._document_status(status)
        if not self._closed:
            self.summary_tabs.setCurrentIndex(0)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        # The generic reader adjusts its detail height on every resize. Apply
        # this crop reader's compact, scrollable scope after that adjustment.
        self.summary_tabs.setMinimumHeight(70)
        self.summary_tabs.setMaximumHeight(110 if event.size().width()<600 else 85)

    def reject(self):
        self.action = None
        self.section_materials_requested = False
        if hasattr(self, '_stale_timer'):
            self._stale_timer.stop()
        super().reject()

    def done(self, result):
        if not self._closed:
            self._release()
        if hasattr(self, '_stale_timer'):
            self._stale_timer.stop()
        if hasattr(self, 'section_materials_button'):
            self.section_materials_button.setEnabled(False)
        if result != self.DialogCode.Accepted:
            self.action = None
            self.section_materials_requested = False
        super().done(result)

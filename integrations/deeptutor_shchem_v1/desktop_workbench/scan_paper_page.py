"""Reviewed image numbers inside the original responsive paper page shell."""
from copy import deepcopy
from PySide6.QtWidgets import QPushButton, QDialog, QWidget, QVBoxLayout
from .assembly_page import PaperPage as ComposerPage, MixedPaperPanel, MixedPaperPaginationDialog
from .workflow_pages import PaperPage as WorkflowPaperPage
from .components import page_scroll
from .scan_number_dialog import ScanNumberDialog
from ..desktop_raster_paper_service import RasterPaperService


class ScanNumberPanel(MixedPaperPanel):
    def __init__(self, *args, **kwargs):
        self._number_dialog = None
        super().__init__(*args, **kwargs)
        self.number_button = QPushButton('调整图片题号后预览…')
        self.number_button.setObjectName('QuietButton')
        self.number_button.setToolTip('框选扫描图片中的原题号，只修改本次导出的副本。原题库不变。')
        layout = self.scroll.widget().layout()
        layout.insertWidget(layout.indexOf(self.preview_button), self.number_button)
        self.number_button.clicked.connect(self._start_numbering)
        self._update_actions()

    def _update_actions(self):
        super()._update_actions()
        if hasattr(self, 'number_button'):
            self.number_button.setEnabled(not self._busy and not self._restore_failed and bool(self.model.order))

    def _start_numbering(self):
        if self._busy or not self.model.order:
            return
        self._edited()
        request, generation = self.request(), self._generation
        self._busy = True
        self._update_actions()
        self.status.setText('正在读取本次试卷题图，尚未改动原图…')

        def prepare():
            preview = self.facade.create_paper_preview(request)
            catalog = RasterPaperService(self.facade).inspect_images(preview.preview_id, preview.preview_hash)
            return preview, catalog

        def ready(value):
            if self._closed or generation != self._generation:
                return
            preview, catalog = value
            if not catalog['images']:
                self._number_failed(generation, '本次没有可调整的 PNG/JPEG/BMP 题图；可直接使用普通排版预览。')
                return
            from ..desktop_number_regions import NumberRegionStore
            dialog = ScanNumberDialog(catalog, self, region_store=NumberRegionStore(self.facade.state_store))
            self._number_dialog = dialog

            def finished(code):
                if self._closed or generation != self._generation:
                    return
                self._number_dialog = None
                edits = deepcopy(dialog.edits)
                dialog.deleteLater()
                if code != QDialog.DialogCode.Accepted:
                    self._number_failed(generation, '已取消图片题号调整，原图和选题顺序不变。')
                    return
                self.status.setText('正在生成调整题号后的实际分页…')
                self.tasks.submit('生成图片换号后的试卷',
                    lambda: RasterPaperService(self.facade).prepare_corrected(preview.preview_id, preview.preview_hash, edits),
                    on_success=lambda result: self._number_ready(generation, result),
                    on_failure=lambda message: self._number_failed(generation, message))

            dialog.finished.connect(finished)
            dialog.show()

        self.tasks.submit('准备图片题号', prepare, on_success=ready,
                          on_failure=lambda message: self._number_failed(generation, message))

    def _number_failed(self, generation, message):
        if self._closed or generation != self._generation:
            return
        self._busy = False
        self.status.setText(str(message))
        self._update_actions()

    def _number_ready(self, generation, preview):
        if self._closed or generation != self._generation:
            return
        self._busy = False
        self._preview = preview
        dialog = MixedPaperPaginationDialog(preview.preview_model, self.tasks,
            lambda key: self.facade.paper_preview_image(preview.preview_id, key), self)
        self._preview_dialog = dialog
        dialog.preview_confirmed.connect(lambda: self._approve(dialog, generation, preview))
        dialog.show()
        self._update_actions()

    def closeEvent(self, event):
        if self._number_dialog is not None:
            self._number_dialog.reject()
        super().closeEvent(event)


class ScanComposer(ComposerPage):
    def _activate_mixed(self, basket, *, force=False):
        scopes = {row.get('scope', 'master') for row in basket
                  if row.get('item_kind') not in {'word_question', 'personal_visual_theme'}}
        required = any(row.get('item_kind') in {'word_question', 'personal_visual_theme'} for row in basket)
        required = required or len(scopes) > 1 or (force and bool(basket))
        if required and self._mixed_panel is None and callable(getattr(self.facade, 'paper_basket_projection', None)):
            self._mixed_panel = ScanNumberPanel(self.facade, self.tasks, self.model, self)
            self._mixed_panel.load_finished.connect(self._basket_preview_loaded)
            self.layout().addWidget(self._mixed_panel)
        return super()._activate_mixed(basket, force=force)


class ScanPaperPage(WorkflowPaperPage):
    """Retain the existing shell, forwarding and scrolling; only swap its composer."""
    def __init__(self, facade, tasks, parent=None):
        QWidget.__init__(self, parent)
        self.facade = facade
        self.tasks = tasks
        self._composer = ScanComposer(facade, tasks)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(self._composer))

"""An explicit, cancellable working copy of an uncommitted material batch."""
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QPushButton, QVBoxLayout


class PendingMaterialEditor(QDialog):
    def __init__(self, composition, assets, image_data, error, parent=None, *, guard=None):
        super().__init__(parent)
        self.setObjectName('PendingMaterialBatchEditor')
        self.setWindowTitle('编辑这批课堂素材')
        self.resize(700, 440)
        self.value = deepcopy(composition)
        self._original_resources = deepcopy(composition['resources'])
        self.assets, self.image_data = deepcopy(list(assets)), dict(image_data)
        self.guard = guard
        self.result_value = None
        self.active_resource_editor = None
        self.guard_error = None
        root = QVBoxLayout(self)
        explanation = QLabel('这批素材暂时不能完整排版。可明确修改课堂副本，再核对来源并整批加入；原资源快照和原图保留。')
        explanation.setWordWrap(True); explanation.setTextFormat(Qt.TextFormat.PlainText); root.addWidget(explanation)
        self.status = QLabel(str(error)); self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText); root.addWidget(self.status)
        self.resources = QListWidget(); self.resources.setObjectName('PendingMaterialBatchResources')
        self.resources.addItems([row['title'] for row in self.value['resources']]); self.resources.setCurrentRow(0)
        root.addWidget(self.resources, 1)
        self.edit_button = QPushButton('编辑这批课堂素材')
        self.edit_button.setObjectName('PendingMaterialBatchEdit')
        self.edit_button.clicked.connect(self.edit_selected); root.addWidget(self.edit_button)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('核对并加入本课')
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName('PendingMaterialBatchApply')
        self.buttons.accepted.connect(self.apply); self.buttons.rejected.connect(self.reject); root.addWidget(self.buttons)
        self._timer = QTimer(self); self._timer.setInterval(150); self._timer.timeout.connect(self._check_current)
        self.finished.connect(self._timer.stop)
        if guard is not None: self._timer.start()

    def _check_current(self):
        try:
            if self.guard is not None: self.guard()
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            self.guard_error = str(exc)
            if self.active_resource_editor is not None: self.active_resource_editor.reject()
            self.reject()
            return False

    def edit_selected(self):
        if not self._check_current(): return
        index = self.resources.currentRow()
        if not 0 <= index < len(self.value['resources']): return
        from .lesson_composition_panel import ResourceEditor
        from ..desktop_handout_material_handoff import checked_material_work_copy
        editor = ResourceEditor(self.value, self.value['resources'][index]['id'], self,
                                assets=self.assets, image_data=self.image_data)
        self.active_resource_editor = editor
        try:
            if editor.exec() != QDialog.DialogCode.Accepted or editor.result_value is None: return
            if not self._check_current(): return
            checked_material_work_copy({'resources': self._original_resources}, editor.result_value['resources'])
            self.value = deepcopy(editor.result_value)
            self.resources.item(index).setText(self.value['resources'][index]['title'])
            self.status.setText('课堂副本已暂存。核对并加入时会重新检查原来源与完整排版；取消则不加入本批。')
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
        finally:
            self.active_resource_editor = None
            editor.deleteLater()

    def apply(self):
        if not self._check_current(): return
        from ..desktop_handout_material_handoff import checked_material_work_copy
        try:
            checked_material_work_copy({'resources': self._original_resources}, self.value['resources'])
            self.result_value = deepcopy(self.value)
            self.accept()
        except ValueError as exc:
            self.status.setText(str(exc))

    def reject(self):
        self.result_value = None
        if self.active_resource_editor is not None: self.active_resource_editor.reject()
        super().reject()

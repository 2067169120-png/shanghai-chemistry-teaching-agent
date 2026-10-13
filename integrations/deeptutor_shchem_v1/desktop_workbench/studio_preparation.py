"""Additive studio entry points on the existing preparation generation workflow."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QComboBox, QHBoxLayout, QLabel, QSizePolicy, QWidget
from PySide6.QtCore import Signal
from .workflow_pages import PreparationPage
from .components import set_status
from ..desktop_composition_insertion import INSERTION_LABELS


class MaterialInsertionTarget(QComboBox):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    def showPopup(self):
        self.owner.refresh_material_insertion_targets()
        super().showPopup()


class StudioPreparationPage(PreparationPage):
    textbook_requested = Signal()
    handout_requested = Signal()
    material_handoff_finished = Signal(bool, str)
    preparation_target_changed = Signal()
    def __init__(self, facade, tasks, parent=None):
        super().__init__(facade, tasks, parent)
        self._editor_work_title = ''
        self._editor_draft_id = None
        from .preparation_recovery import PreparationRecoveryController
        state_root = getattr(getattr(facade, "paths", None), "state_root", None)
        self.recovery = PreparationRecoveryController(self) if state_root is not None else None
        from .preparation_workspace import PreparationWorkspace, WrappingControlLayout
        self.workspace = PreparationWorkspace(self)
        from PySide6.QtWidgets import QPushButton
        self.lesson_design_button = QPushButton("教学设计")
        self.lesson_design_button.setObjectName("QuietButton")
        self.lesson_design_button.setToolTip("编辑目标、活动和评价，本地生成同源教案/PPT/学习单。")
        self.lesson_design_button.clicked.connect(self.open_lesson_design)
        self.personal_template_button = QPushButton("我的模板…")
        self.personal_template_button.setObjectName("QuietButton")
        self.personal_template_button.clicked.connect(self.open_personal_teaching_templates)
        self.personal_template_button.setToolTip('选择、预览和管理自己的教学模板。')
        self.textbook_button = QPushButton("从教材选材")
        self.textbook_button.setObjectName("QuietButton")
        self.textbook_button.setToolTip("按教材章节阅读原页，勾选知识并预览后追加到当前备课。")
        self.textbook_button.clicked.connect(self.open_textbooks)
        self.handout_button = QPushButton('从讲义选材')
        self.handout_button.setObjectName('QuietButton')
        self.handout_button.setToolTip('先选一份讲义，连续选完整知识、原图和完整题，一批加入本课。')
        self.handout_button.clicked.connect(self.open_handout_materials)
        self.workspace.register_studio_controls(textbook=self.textbook_button,
            handout=self.handout_button, lesson_design=self.lesson_design_button,
            personal_template=self.personal_template_button)
        self._material_position_epoch = 0
        self._source_material_insertion = None
        self.material_insertion_bar = QWidget(self)
        insertion = WrappingControlLayout(self.material_insertion_bar)
        insertion.setContentsMargins(16, 0, 16, 6)
        position_group = QWidget(self.material_insertion_bar)
        position_layout = QHBoxLayout(position_group)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(6)
        position_layout.addWidget(QLabel('加入位置'))
        self.material_insertion_position = QComboBox(self)
        for mode, title in INSERTION_LABELS: self.material_insertion_position.addItem(title, mode)
        self.material_insertion_position.setAccessibleName('从原资料加入课件的位置')
        position_layout.addWidget(self.material_insertion_position)
        insertion.addWidget(position_group)
        target_group = QWidget(self.material_insertion_bar)
        target_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        target_layout = QHBoxLayout(target_group)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(6)
        target_layout.addWidget(QLabel('目标素材'))
        self.material_insertion_target = MaterialInsertionTarget(self)
        self.material_insertion_target.setAccessibleName('新批素材之前或之后的目标素材')
        self.material_insertion_target.setMinimumWidth(180)
        self.material_insertion_target.setMinimumContentsLength(12)
        self.material_insertion_target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        target_layout.addWidget(self.material_insertion_target, 1)
        insertion.addWidget(target_group)
        self.material_insertion_bar.setToolTip('进入教材或讲义选材前确定位置；整批完整素材及续页一起加入，已有页面保持。')
        self.layout().insertWidget(1, self.material_insertion_bar)
        self.material_insertion_position.currentIndexChanged.connect(self._material_position_changed)
        self.material_insertion_target.currentIndexChanged.connect(self._material_position_changed)
        self.refresh_material_insertion_targets()
        self._material_handoff_dialog = None
        self._material_handoff_epoch = 0
        self.last_material_handoff = None
        self._textbook_handoff_closed = False

    def _material_position_changed(self, *_):
        self._material_position_epoch += 1
        self.material_insertion_target.setEnabled(self.material_insertion_position.currentData() != 'end')
        self.preparation_target_changed.emit()

    def refresh_material_insertion_targets(self, *, prefer_session=False):
        rows = (self._lesson_design or {}).get('composition', {}).get('resources', [])
        chosen = self.material_insertion_target.currentData()
        state = getattr(self, '_lesson_editor_session', None)
        if (chosen is None or prefer_session) and isinstance(state, dict) and state.get('design_id') == (self._lesson_design or {}).get('id'):
            chosen = state.get('resource_id')
        self.material_insertion_target.blockSignals(True)
        self.material_insertion_target.clear()
        for index, row in enumerate(rows):
            title = ' '.join(row['title'].split())
            self.material_insertion_target.addItem(f'{index+1} · {title}', row['id'])
        if chosen is not None:
            self.material_insertion_target.setCurrentIndex(self.material_insertion_target.findData(chosen))
        self.material_insertion_target.blockSignals(False)
        for index in (1, 2): self.material_insertion_position.model().item(index).setEnabled(bool(rows))
        if not rows: self.material_insertion_position.setCurrentIndex(0)
        self.material_insertion_target.setEnabled(bool(rows) and self.material_insertion_position.currentData() != 'end')

    def showEvent(self, event):
        self.refresh_material_insertion_targets()
        super().showEvent(event)

    def freeze_material_insertion(self):
        from ..desktop_lesson_design import digest
        self.refresh_material_insertion_targets()
        mode = self.material_insertion_position.currentData()
        anchor = self.material_insertion_target.currentData()
        if mode != 'end' and anchor is None:
            raise ValueError('请先选择加入位置的目标素材。')
        return {'mode': mode, 'anchor_resource_id': anchor,
                'epoch': self._material_position_epoch, 'draft_id': self._editor_draft_id,
                'design_id': (self._lesson_design or {}).get('id'),
                'composition': digest((self._lesson_design or {}).get('composition'))}

    def check_material_insertion(self, insertion, *, before_open=False):
        from ..desktop_lesson_design import digest
        if (insertion['epoch'] != self._material_position_epoch
                or insertion['draft_id'] != self._editor_draft_id
                or (before_open and (insertion['design_id'] != (self._lesson_design or {}).get('id')
                    or insertion['composition'] != digest((self._lesson_design or {}).get('composition'))))):
            raise ValueError('加入位置或目标备课在选材期间变化，本批未加入；请重新选择素材。')
        if insertion['mode'] != 'end' and not any(
                row['id'] == insertion['anchor_resource_id']
                for row in (self._lesson_design or {}).get('composition', {}).get('resources', [])):
            raise ValueError('加入位置所选的素材已移除，本批未加入；请重新选择位置。')

    def take_material_insertion(self):
        insertion = self._source_material_insertion
        self._source_material_insertion = None
        if insertion is None: insertion = self.freeze_material_insertion()
        self.check_material_insertion(insertion, before_open=True)
        return insertion

    def panel_material_insertion(self, insertion):
        return {key: insertion[key] for key in ('mode', 'anchor_resource_id')}

    def open_handout_materials(self):
        if self.studio_busy():
            set_status(self.status, 'attention', '请先完成当前保存、导入或生成，再选择讲义素材。')
            return
        self.preparation_target_changed.emit()
        try: self._source_material_insertion = self.freeze_material_insertion()
        except ValueError as exc:
            set_status(self.status, 'attention', str(exc)); return
        self.handout_requested.emit()

    def import_handout_materials(self, request):
        """Start a source-bound mixed batch; final receipt follows asynchronously."""
        from ..desktop_handout_material_handoff import (prepare_handout_materials,
            validate_material_request)
        try: request = validate_material_request(request)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            set_status(self.status, 'error', str(exc)); return False
        return self._import_material_batch(request, prepare_handout_materials, '正在核对讲义选材并加入本课…')

    def open_textbook_section_materials(self, selection, *, on_handoff=None):
        from .forward_section_entry import open_textbook_section_materials
        return open_textbook_section_materials(self, selection, on_handoff=on_handoff)

    def import_linked_question(self, request):
        from ..desktop_handout_material_handoff import prepare_linked_question
        return self._import_material_batch(request, prepare_linked_question, '正在核对知识关联原题并加入本课…')

    def import_textbook_crops_for_composition(self, requests):
        """Receive the reading window's explicit crop-to-canvas action."""
        from copy import deepcopy
        from threading import Event
        from PySide6.QtWidgets import QDialog
        from ..desktop_composition_sources import (import_textbook_crops_for_composition,
                                                   validate_textbook_crop_requests)
        from ..desktop_lesson_composition import digest
        from ..desktop_preparation import DesktopPreparationError
        from ..desktop_facade import DesktopFacadeError
        from ..reader_cancellation import ReadCancelled
        from .lesson_design_dialog import LessonDesignDialog
        if self.studio_busy() or self._textbook_handoff_closed:
            set_status(self.status, 'attention', '请先完成当前备课操作，再加入教材裁片。')
            return False
        dialog = None
        try:
            insertion = self.take_material_insertion()
            frozen = validate_textbook_crop_requests(requests)
            original_design = deepcopy(self._lesson_design)
            assets = deepcopy(self.image_assets_widget.assets())
            resources = deepcopy((original_design or {}).get('composition', {}).get('resources', []))
            target = self._editor_draft_id
            payload_before_open = deepcopy(self._payload())
            dialog = LessonDesignDialog(self)
            history_before = deepcopy(dialog.history.value)
            history_back, history_forward = deepcopy(dialog.history.back), deepcopy(dialog.history.forward)
            expected_history = digest(history_before)
            expected_payload = deepcopy(self._payload())
            self._material_handoff_epoch += 1
            epoch = self._material_handoff_epoch
            revoked = Event(); connections = []; box = {'delivered': False, 'cleaned': False,
                                                        'reported': False, 'applying': False}
            self._lesson_busy = True; self._material_handoff_dialog = dialog
            self.last_material_handoff = {'status': 'reading', 'epoch': epoch}

            def revoke(*_): revoked.set()
            for widget in (self.topic, self.audience, self.objective, self.materials,
                           self.learning_detail, self.template_detail, self.strategy_detail,
                           self.output_kind, self.route, self.lesson_count, self.lesson_minutes):
                signal = next((getattr(widget, name) for name in
                    ('textChanged', 'currentIndexChanged', 'valueChanged') if hasattr(widget, name)), None)
                if signal is not None:
                    signal.connect(revoke); connections.append((signal, revoke))
            for signal in (self.image_assets_widget.mode_changed, self.preparation_target_changed):
                signal.connect(revoke); connections.append((signal, revoke))
            def assets_changed(*_):
                # add_result sets the complete image form at its commit. The
                # immutable snapshots still catch foreign edits while it reads.
                if not box['applying']: revoke()
            self.image_assets_widget.assets_changed.connect(assets_changed)
            connections.append((self.image_assets_widget.assets_changed, assets_changed))

            def worker_guard():
                if revoked.is_set(): raise ReadCancelled('备课在裁图期间变化，本批未加入。')

            def current():
                self.check_material_insertion(insertion)
                if (revoked.is_set() or self._textbook_handoff_closed
                        or epoch != self._material_handoff_epoch
                        or self._editor_draft_id != target
                        or getattr(self.tasks, 'is_shutting_down', False)
                        or dialog._stopping or dialog._close_when_finished
                        or self._payload() != expected_payload
                        or digest(dialog.history.value) != expected_history
                        or self.image_assets_widget.assets() != assets):
                    raise ValueError('当前备课、图片或裁图窗口已变化，本批未加入；请重新选图。')

            def receipt(ok, message, count=0):
                box['reported'] = True
                self.last_material_handoff = {'status': 'added' if ok and count else 'duplicate' if ok else 'not_added',
                    'epoch': epoch, 'count': count, 'message': message}
                set_status(self.status, 'success' if ok else 'attention', message)
                dialog.status.setText(message)
                self.refresh_material_insertion_targets()
                self.material_handoff_finished.emit(ok, message)

            def cleanup(*_):
                if box['cleaned']: return
                box['cleaned'] = True; revoke()
                if not box['delivered'] and not box['reported']:
                    receipt(False, '本次裁图已停止或未完成；原有备课内容保留。')
                if (not box['delivered'] and self._editor_draft_id == target
                        and digest(dialog.history.value) == expected_history
                        and self._lesson_design == history_before):
                    self._lesson_design = deepcopy(original_design)
                    self.setWindowModified(self._payload() != self._form_baseline)
                for signal, callback in connections:
                    try: signal.disconnect(callback)
                    except (RuntimeError, TypeError): pass
                if self._material_handoff_dialog is dialog:
                    self._material_handoff_dialog = None; self._lesson_busy = False
                dialog.deleteLater()

            def abort(message):
                if box['delivered'] or box['cleaned']: return
                dialog._stopping = True; dialog.set_busy(False)
                receipt(False, message)
                # Aborting a source handoff bypasses the normal Return save.
                QDialog.reject(dialog)

            class CheckedCanvasFacade:
                def __getattr__(_self, name): return getattr(self.facade, name)
                def preparation_image_bytes(_self, asset):
                    current()
                    data = self.facade.preparation_image_bytes(asset)
                    current()
                    return data

            def ready(result):
                try:
                    current()
                    if not result['resources']:
                        box['delivered'] = True; dialog.set_busy(False)
                        receipt(True, '这些裁片及相同图题、用途已在本课中；可继续编排。')
                        return
                    dialog.facade = CheckedCanvasFacade()
                    box['applying'] = True
                    committed_history = None
                    try:
                        dialog.composition_panel.add_result(result, expected_history, expected_assets=assets,
                            insertion=self.panel_material_insertion(insertion))
                        committed_history = deepcopy(dialog.history.value)
                        dialog.facade = self.facade
                        box['applying'] = False
                        after_payload = deepcopy(expected_payload)
                        after_payload['lesson_design'] = deepcopy(committed_history)
                        after_payload['image_assets'] = deepcopy(result['image_assets'])
                        def committed_current():
                            self.check_material_insertion(insertion)
                            if (revoked.is_set() or self._textbook_handoff_closed
                                    or epoch != self._material_handoff_epoch or self._editor_draft_id != target
                                    or getattr(self.tasks, 'is_shutting_down', False)
                                    or dialog._stopping or dialog._close_when_finished
                                    or dialog.history.value != committed_history or self._payload() != after_payload
                                    or self.image_assets_widget.assets() != result['image_assets']):
                                raise ValueError('备课在裁片编排期间变化，本批未加入；请重新选图。')
                        committed_current()
                        # The pre-commit facade guards only the source transaction.
                        # Repaint the committed canvas with the ordinary facade.
                        dialog.composition_panel.refresh(); dialog.composition_panel.fit_view()
                        committed_current()
                        if dialog.composition_panel.layout_result is None:
                            raise ValueError('教材裁片无法完整显示，本批未加入；请重新选图。')
                    except BaseException:
                        added_rows = (self._lesson_design or {}).get('composition', {}).get('resources', [])
                        owned_design = (self._lesson_design == committed_history if committed_history is not None
                            else self._lesson_design == history_before or added_rows == resources + result['resources'])
                        owned_assets = self.image_assets_widget.assets() == result.get('image_assets')
                        dialog.history.value = deepcopy(history_before)
                        dialog.history.back, dialog.history.forward = deepcopy(history_back), deepcopy(history_forward)
                        if self._editor_draft_id == target:
                            if owned_design: self._lesson_design = deepcopy(original_design)
                            if owned_assets: self.image_assets_widget.set_assets(assets)
                        raise
                    finally:
                        box['applying'] = False
                        dialog.facade = self.facade
                    box['delivered'] = True; dialog.set_busy(False)
                    dialog.tabs.setCurrentWidget(dialog.composition_panel)
                    receipt(True, f'已将 {len(result["resources"])} 张教材裁片加入课件；可一次撤销并继续编排。', len(result['resources']))
                except (ValueError, TypeError, KeyError, OSError, RuntimeError, DesktopFacadeError) as exc:
                    abort(str(exc))

            def work():
                try:
                    worker_guard()
                    result = import_textbook_crops_for_composition(self.facade, frozen, assets, resources,
                                                                   guard=worker_guard)
                    worker_guard()
                    return result
                except ReadCancelled: raise
                except (ValueError, TypeError, KeyError, OSError, DesktopFacadeError) as exc:
                    raise DesktopPreparationError('textbook_canvas_handoff', str(exc)) from exc

            def finished(identity):
                if identity == box.get('task_id') and not box['delivered'] and not box['cleaned']:
                    abort('本次裁图已停止或未完成；原有备课内容保留。')

            self.tasks.task_finished.connect(finished); connections.append((self.tasks.task_finished, finished))
            dialog.finished.connect(cleanup); dialog.destroyed.connect(revoke)
            dialog.tabs.setCurrentWidget(dialog.composition_panel)
            dialog._cancellable = True; dialog.set_busy(True)
            label = '正在核对教材裁片并加入课件…'; dialog.status.setText(label)
            dialog._task = self.tasks.submit(label, work, on_success=ready, on_failure=abort)
            box['task_id'] = dialog._task
            dialog.open(); set_status(self.status, 'info', label)
            return True
        except (ValueError, TypeError, KeyError, OSError, RuntimeError, DesktopFacadeError) as exc:
            if 'abort' in locals() and dialog is not None:
                abort(str(exc))
                return False
            if dialog is not None:
                if self._lesson_design == dialog.history.value: self._lesson_design = original_design
                self._lesson_busy = False; self._material_handoff_dialog = None
                dialog.deleteLater()
            elif ('payload_before_open' in locals() and self._editor_draft_id == target
                    and self.image_assets_widget.assets() == assets):
                # Constructor failure can occur after sync_page initializes an
                # empty design. Revert only that incidental in-memory change.
                opened = deepcopy(self._payload()); previous = deepcopy(payload_before_open)
                opened.pop('lesson_design', None); previous.pop('lesson_design', None)
                if opened == previous:
                    self._lesson_design = original_design
                    self.setWindowModified(self._payload() != self._form_baseline)
            set_status(self.status, 'error', str(exc)); return False

    def _import_material_batch(self, request, prepare, label):
        from copy import deepcopy
        from threading import Event
        from PySide6.QtWidgets import QDialog
        from ..desktop_lesson_composition import digest
        from ..desktop_preparation import DesktopPreparationError
        from ..reader_cancellation import ReadCancelled
        from .lesson_design_dialog import LessonDesignDialog
        if self.studio_busy():
            set_status(self.status, 'attention', '请先完成当前备课操作，再加入选材。')
            return False
        try:
            insertion = self.take_material_insertion()
            if not isinstance(request, dict):
                raise ValueError('选材交接记录不完整，请重新选择。')
            # The linked request also contains a live scoped facade. The backend
            # deliberately rebuilds that scope from the saved exact endpoints.
            frozen = {k: deepcopy(v) if k != 'scoped_facade' else v for k, v in request.items()}
            original_design = deepcopy(self._lesson_design)
            assets = deepcopy(self.image_assets_widget.assets())
            resources = deepcopy((original_design or {}).get('composition', {}).get('resources', []))
            dialog = LessonDesignDialog(self)
            self._material_handoff_epoch += 1
            epoch = self._material_handoff_epoch
            expected_payload = deepcopy(self._payload())
            expected_history = digest(dialog.history.value)
            history_before = deepcopy(dialog.history.value)
            history_back, history_forward = deepcopy(dialog.history.back), deepcopy(dialog.history.forward)
            revoked = Event(); connections = []; box = {'publication': None, 'delivered': False, 'cleaned': False, 'reported': False, 'phase': 'reading'}
            self._lesson_busy = True
            self._material_handoff_dialog = dialog
            self.last_material_handoff = {'status': 'reading', 'epoch': epoch}

            def revoke(*_): revoked.set()
            for widget in (self.topic, self.audience, self.objective, self.materials,
                           self.learning_detail, self.template_detail, self.strategy_detail,
                           self.output_kind, self.route, self.lesson_count, self.lesson_minutes):
                signal = next((getattr(widget, name) for name in
                    ('textChanged', 'currentIndexChanged', 'valueChanged') if hasattr(widget, name)), None)
                if signal is not None:
                    signal.connect(revoke); connections.append((signal, revoke))
            for signal in (self.image_assets_widget.assets_changed, self.image_assets_widget.mode_changed,
                           self.preparation_target_changed):
                signal.connect(revoke); connections.append((signal, revoke))

            def worker_guard():
                # Only thread-safe state here; Word reconstruction stays in the
                # worker and no Qt getter is called by the backend.
                if revoked.is_set(): raise ReadCancelled('备课在选材期间变化，本批未加入。')

            def current():
                self.check_material_insertion(insertion)
                if (revoked.is_set() or epoch != self._material_handoff_epoch
                        or getattr(self.tasks, 'is_shutting_down', False)
                        or dialog._stopping or dialog._close_when_finished
                        or self._payload() != expected_payload
                        or digest(dialog.history.value) != expected_history
                        or self.image_assets_widget.assets() != assets):
                    raise ValueError('备课、图片或选材窗口在读取期间变化，本批未加入；请重新选择。')

            def receipt(ok, message, count=0):
                box['reported'] = True
                self.last_material_handoff = {'status': 'added' if ok and count else 'duplicate' if ok else 'not_added',
                    'epoch': epoch, 'count': count, 'message': message}
                set_status(self.status, 'success' if ok else 'attention', message)
                dialog.status.setText(message)
                self.refresh_material_insertion_targets()
                self.material_handoff_finished.emit(ok, message)

            def cleanup(*_):
                if box['cleaned']: return
                box['cleaned'] = True; revoke()
                publication = box['publication']
                if publication is not None and not box['delivered'] and box['phase'] != 'rechecking':
                    publication.discard()
                if not box['delivered'] and not box['reported']:
                    receipt(False, '本次选材已停止或未完成；原有备课内容保留。')
                # Opening the existing design dialog initializes an empty
                # design. An aborted handoff must not persist that initializer.
                if (not box['delivered'] and digest(dialog.history.value) == expected_history
                        and self._lesson_design == history_before):
                    self._lesson_design = deepcopy(original_design)
                    self.setWindowModified(self._payload() != self._form_baseline)
                for signal, callback in connections:
                    try: signal.disconnect(callback)
                    except (RuntimeError, TypeError): pass
                if self._material_handoff_dialog is dialog:
                    self._material_handoff_dialog = None; self._lesson_busy = False
                dialog.deleteLater()

            def abort(message):
                if box['delivered'] or box['cleaned']: return
                publication = box['publication']
                if publication is not None: publication.discard()
                dialog._stopping = True
                dialog.set_busy(False)
                receipt(False, message)
                # Skip the normal Return action's framework/recovery save for
                # an aborted source transaction. Teacher form fields stay put.
                QDialog.reject(dialog)

            def ready(result):
                try:
                    box['phase'] = 'published'
                    current()
                    publication = box['publication']
                    if not result['resources']:
                        publication.accept(); box['delivered'] = True
                        receipt(True, '这些原内容及用途已在本课中；已有标题、讲解和使用范围保留。')
                        dialog.set_busy(False)
                        return
                    def restore_owned():
                        from ..desktop_handout_material_handoff import material_batch_is_referenced
                        present = dialog.history.value
                        history_state = (present, dialog.history.back, dialog.history.forward)
                        owned = (history_state == (history_before, history_back, history_forward)
                            or history_state == box.get('committed_history'))
                        if owned:
                            if self._lesson_design == present or self._lesson_design == history_before:
                                self._lesson_design = deepcopy(original_design)
                            dialog.history.value = deepcopy(history_before)
                            dialog.history.back, dialog.history.forward = deepcopy(history_back), deepcopy(history_forward)
                        referenced = material_batch_is_referenced(result, dialog.history.value,
                            *dialog.history.back, *dialog.history.forward, self._lesson_design, existing_assets=assets)
                        # A synchronous refresh may have made a later edit. It
                        # owns its full state and any pictures it still uses.
                        if referenced:
                            publication.accept()
                        elif self.image_assets_widget.assets() == result.get('image_assets'):
                            self.image_assets_widget.set_assets(assets)
                    def committed(value, back, forward):
                        box['committed_history'] = (value, back, forward)
                    def accepted(_result):
                        publication.accept(); box['delivered'] = True; box['phase'] = 'committed'
                        dialog.set_busy(False)
                        dialog.tabs.setCurrentWidget(dialog.composition_panel)
                        receipt(True, f'已整批加入 {len(result["resources"])} 项素材；可一次撤销并继续编排。', len(result['resources']))
                    def recheck_failed(message):
                        restore_owned(); abort(message)
                    def recheck(working):
                        box['phase'] = 'rechecking'
                        box['recheck_task_id'] = dialog.composition_panel.recheck_pending_materials(
                            publication, working, expected_history, expected_assets=assets,
                            insertion=self.panel_material_insertion(insertion), guard=current,
                            worker_guard=worker_guard, commit_receipt=committed,
                            success=accepted, failure=recheck_failed)
                    try:
                        # The worker has finished source publication. A modal
                        # detached editor may now process task_finished events.
                        box['phase'] = 'editing_or_layout'
                        dialog.set_busy(False)
                        added = dialog.composition_panel.add_published_result(publication, result, expected_history,
                            expected_assets=assets, insertion=self.panel_material_insertion(insertion), guard=current,
                            on_recheck=recheck, commit_receipt=committed)
                        if not added: return
                    except BaseException:
                        restore_owned()
                        raise
                    accepted(result)
                except Exception as exc:
                    abort(str(exc))

            def work():
                try:
                    worker_guard()
                    publication = prepare(self.facade, frozen, assets, resources)
                    box['publication'] = publication
                    worker_guard()
                    return publication.publish(worker_guard)
                except ReadCancelled: raise
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    publication = box['publication']
                    if publication is not None: publication.discard()
                    raise DesktopPreparationError('material_handoff', str(exc)) from exc

            def finished(identity):
                if (identity == box.get('task_id') and box['phase'] == 'reading'
                        and not box['delivered'] and not box['cleaned']):
                    abort('本次选材已停止或未完成；原有备课内容保留。')

            self.tasks.task_finished.connect(finished)
            connections.append((self.tasks.task_finished, finished))
            dialog.finished.connect(cleanup)
            dialog.destroyed.connect(revoke)
            dialog.tabs.setCurrentWidget(dialog.composition_panel)
            dialog._cancellable = True; dialog.set_busy(True); dialog.status.setText(label)
            dialog._task = self.tasks.submit(label, work, on_success=ready, on_failure=abort)
            box['task_id'] = dialog._task
            dialog.open()
            set_status(self.status, 'info', label)
            return True
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            if 'dialog' in locals():
                self._lesson_design = original_design
                self._lesson_busy = False; self._material_handoff_dialog = None
                dialog.deleteLater()
            set_status(self.status, 'error', str(exc)); return False

    def closeEvent(self, event):
        self._textbook_handoff_closed = True
        dialog = self._material_handoff_dialog
        if dialog is not None:
            self._material_handoff_epoch += 1
            dialog.reject()
        super().closeEvent(event)

    def open_textbooks(self):
        if self.studio_busy():
            set_status(self.status, "attention", "请先完成当前保存、导入或生成，再选择教材参考。")
            return
        self.preparation_target_changed.emit()
        try: self._source_material_insertion = self.freeze_material_insertion()
        except ValueError as exc:
            set_status(self.status, 'attention', str(exc)); return
        self.textbook_requested.emit()

    def open_lesson_design(self):
        if self.studio_busy():
            set_status(self.status, "attention", "请先完成当前保存、导入或生成。")
            return
        from .lesson_design_dialog import LessonDesignDialog
        dialog = LessonDesignDialog(self)
        dialog.exec()
        dialog.deleteLater()
        self.refresh_material_insertion_targets(prefer_session=True)

    def open_personal_teaching_templates(self):
        if self.studio_busy():
            set_status(self.status, "attention", "请先完成当前备课操作，再选教学结构模板。")
            return
        from .teaching_template_dialogs import TeachingTemplateLibraryDialog
        dialog = TeachingTemplateLibraryDialog(self.facade, self, apply_callback=self.apply_personal_teaching_template)
        dialog.exec(); dialog.deleteLater()

    def apply_personal_teaching_template(self, record):
        if self.studio_busy():
            set_status(self.status, "attention", "请先完成当前备课操作，再应用教学结构模板。")
            return False
        from .lesson_design_dialog import LessonDesignDialog
        from copy import deepcopy
        original_design = deepcopy(self._lesson_design)
        dialog = LessonDesignDialog(self)
        opened_design = deepcopy(dialog.history.value)
        self._lesson_busy = True
        try:
            if not dialog.apply_personal_teaching_template(record):
                # Opening an empty lesson creates its first design in memory.
                # A cancelled template preview should not save that incidental
                # change; concurrent teacher edits still take precedence.
                if dialog.history.value == opened_design and self._lesson_design == opened_design:
                    self._lesson_design = original_design
                    self.setWindowModified(self._payload() != self._form_baseline)
                set_status(self.status, "info", dialog.status.text())
                return False
            dialog.exec()
            return True
        finally:
            self._lesson_busy = False
            dialog.deleteLater()

    def import_textbook_cards(self, result):
        """Receive an explicit, checked bookshelf batch in the current canvas."""
        from copy import deepcopy
        from ..desktop_lesson_composition import digest
        from ..desktop_composition_sources import merge_knowledge_result
        from ..desktop_facade import DesktopFacadeError
        if self.studio_busy():
            set_status(self.status,'attention','请先完成当前备课操作，再加入教材知识卡。')
            return False
        try:
            insertion = self.take_material_insertion()
            existing=(self._lesson_design or {}).get('composition',{}).get('resources',[])
            rows=merge_knowledge_result(result, existing)['resources']
            if not rows:
                set_status(self.status,'info','这些版本的知识卡已在当前课件素材中；可打开“教学环节”继续编排。')
                return True
            # Check existing classroom pictures before constructing a dialog.
            # A missing local copy should leave this lesson ready to retry.
            for asset in self.image_assets_widget.assets():
                self.facade.preparation_image_bytes(asset)
            from .lesson_design_dialog import LessonDesignDialog
            dialog=LessonDesignDialog(self)
            self._lesson_busy=True
            try:
                panel=dialog.composition_panel
                self.check_material_insertion(insertion)
                panel.add_result({'resources':deepcopy(rows)},digest(dialog.history.value),
                    insertion=self.panel_material_insertion(insertion))
                dialog.tabs.setCurrentWidget(panel)
                dialog.exec()
            finally:
                self._lesson_busy=False
                dialog.deleteLater()
                self.refresh_material_insertion_targets()
        except (ValueError,TypeError,KeyError,OSError,DesktopFacadeError) as exc:
            set_status(self.status,'error',str(exc))
            return False
        return True


    def import_word_cards(self, result):
        """Receive complete native questions from the library in this lesson."""
        from ..desktop_composition_sources import merge_question_result
        from ..desktop_lesson_composition import digest
        if self.studio_busy():
            set_status(self.status,'attention','请先完成当前备课操作，再加入完整题。')
            return False
        try:
            insertion = self.take_material_insertion()
            existing=(self._lesson_design or {}).get('composition',{}).get('resources',[])
            merged=merge_question_result(result,existing,self.image_assets_widget.assets())
            if not merged['resources']:
                set_status(self.status,'info','这些版本的完整题已在当前课件中；原作品副本保持，可继续编排。')
                return True
            from .lesson_design_dialog import LessonDesignDialog
            dialog=LessonDesignDialog(self)
            self._lesson_busy=True
            try:
                panel=dialog.composition_panel
                self.check_material_insertion(insertion)
                panel.add_result(merged,digest(dialog.history.value),
                    insertion=self.panel_material_insertion(insertion))
                dialog.tabs.setCurrentWidget(panel)
                dialog.exec()
            finally:
                self._lesson_busy=False
                dialog.deleteLater()
                self.refresh_material_insertion_targets()
        except (ValueError,TypeError,KeyError,OSError) as exc:
            set_status(self.status,'error',str(exc))
            return False
        return True

    def studio_busy(self) -> bool:
        return bool(self._save_task_id or self._generation_qt_task_id
                    or self._active_preparation_task_id or self._word_import_in_flight
                    or self._library_image_task_id or getattr(self,'_lesson_busy',False)
                    or getattr(self, '_section_materials_job', None))

    def apply_studio_template(self, key: str, topic: str = "", audience: str = "") -> bool:
        from ..desktop_studio import template_brief
        if self.studio_busy():
            set_status(self.status, "attention", "请先等待当前保存、导入或生成结束。")
            return False
        value = template_brief(self._payload(), key, topic=topic, audience=audience)
        self.topic.setText(value["topic"])
        self.audience.setText(value["audience"])
        self.objective.setPlainText(value["objective"])
        self.template_detail.setText(value["advanced"]["template_and_delivery"])
        self.route.setCurrentText(value["lesson_route"])
        if "timing" in value:
            self.lesson_count.setValue(value["timing"]["periods"])
            self.lesson_minutes.setValue(value["timing"]["minutes_per_period"])
        self.setWindowModified(self._payload() != self._form_baseline)
        set_status(self.status, "success", "已加入教学模板，可继续编辑。原资料、原图和已有课题保留；尚未保存或调用模型。")
        return True

    def append_classroom_feedback(self, text: str) -> bool:
        if self.studio_busy():
            set_status(self.status, "attention", "请等待当前备课操作结束，再带入课堂反馈。")
            return False
        old = self.materials.toPlainText()
        if text not in old:
            self.materials.setPlainText("\n\n".join(filter(None, (old, text))))
        self.setWindowModified(self._payload() != self._form_baseline)
        set_status(self.status, "success", "教师录入的课堂反馈已追加；未改变题目、原图或评分，未调用模型。请保存草稿。")
        return True

    def _open_draft(self, draft_id: str | None = None) -> None:
        from ..desktop_preparation import normalize_preparation_payload
        from .preparation_draft_dialog import PreparationDraftDialog

        if self.studio_busy():
            set_status(self.status, "attention", "请先等待保存、导入或生成结束，再打开其他草稿。")
            return
        dialog = PreparationDraftDialog(self.facade, self)
        if isinstance(draft_id, str) and not dialog.select_draft_id(draft_id):
            dialog.deleteLater()
            set_status(self.status, "attention", "这份草稿已不在当前草稿列表或已无法读取。请刷新后重选；当前填写未改变，也没有改开其他草稿。")
            return
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.selected is None:
            dialog.deleteLater()
            return
        selected = dialog.selected
        option = dialog.source.currentData()
        selected_title = (option.get('title', '') if isinstance(option, dict)
                          and option.get('draft_id') == selected['draft_id'] else '')
        dialog.deleteLater()
        if self._payload() != self._form_baseline:
            decision = QMessageBox.question(
                self, "确认载入另一份草稿",
                "当前表单有尚未保存的修改。载入所选草稿会替换当前表单，已保存的草稿不会改变。是否放弃这些未保存修改并载入？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if decision != QMessageBox.StandardButton.Yes:
                return
        payload = selected["payload"]
        normalized = normalize_preparation_payload(payload)
        from copy import deepcopy
        self._lesson_design = deepcopy(payload.get("lesson_design"))
        self.output_kind.setCurrentIndex(self.output_kind.findData(payload["output_kind"]))
        self.topic.setText(payload["topic"])
        self.audience.setText(payload["audience"])
        if self.route.findText(payload["lesson_route"]) < 0:
            self.route.addItem(payload["lesson_route"])
        self.route.setCurrentText(payload["lesson_route"])
        self.lesson_count.setValue(normalized["timing"]["periods"])
        self.lesson_minutes.setValue(normalized["timing"]["minutes_per_period"])
        self.objective.setPlainText(payload["objective"])
        self.materials.setPlainText(payload["materials"])
        self.image_assets_widget.set_assets(payload.get("image_assets", ()))
        self.image_assets_widget.set_image_input_mode(payload.get("image_input_mode"))
        for key, widget in (
            ("learning_and_experiment", self.learning_detail),
            ("template_and_delivery", self.template_detail),
            ("homework_and_strategy", self.strategy_detail),
        ):
            widget.setText(payload["advanced"].get(key, ""))
        self._form_baseline = self._payload()
        self._editor_work_title = selected_title
        self._editor_draft_id = selected['draft_id']
        self.setWindowModified(False)
        self._preparation_task_id = None
        self._current_task_status = ""
        self._current_task_retryable = False
        self.progress_card.hide()
        self._hide_result_artifacts()
        self.result_card.hide()
        set_status(self.status, "success", "备课草稿已载入，可继续修改并另存；生成仍需确认。原草稿未改写，尚未调用模型。")
        self.topic.setFocus()
        self.preparation_target_changed.emit()

    def _saved(self, receipt):
        super()._saved(receipt)
        # Saving creates a new immutable draft rather than updating the loaded
        # work. Do not keep the previous work's display name on that new copy.
        self._editor_work_title = ''
        self._editor_draft_id = receipt.draft_id

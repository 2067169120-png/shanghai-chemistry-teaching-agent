"""One frozen course destination for a native, detached image batch."""
from copy import deepcopy
from threading import Event

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QDialog

from ..desktop_lesson_design import digest
from ..reader_cancellation import ReadCancelled


class FrozenCompositionScope(QObject):
    """Observe GUI state here; workers receive only the revocation Event."""
    def __init__(self, panel, insertion=None):
        super().__init__(panel)
        self.panel, self.owner, self.insertion = panel, panel.owner, deepcopy(insertion)
        self.revoked = Event()
        self.committed = self.closed = self.owner_closed = False
        self.dialog = None
        self.history = deepcopy((self.owner.history.value, self.owner.history.back, self.owner.history.forward))
        self.expected = digest(self.history[0])
        self.assets = deepcopy(self.owner.page.image_assets_widget.assets())
        self.resources = deepcopy(panel.composition()['resources'])
        self.payload = self.owner.page._payload() if callable(getattr(self.owner.page, '_payload', None)) else None
        self.payload = deepcopy(self.payload)
        self.target = getattr(self.owner.page, '_editor_draft_id', None)
        self.connections = []
        for name in ('topic', 'audience', 'objective', 'materials', 'learning_detail',
                     'template_detail', 'strategy_detail', 'output_kind', 'route',
                     'lesson_count', 'lesson_minutes'):
            widget = getattr(self.owner.page, name, None)
            signal = next((getattr(widget, key) for key in
                ('textChanged', 'currentIndexChanged', 'valueChanged') if hasattr(widget, key)), None)
            self._connect(signal, self.invalidate)
        for source, name in ((self.owner.page, 'preparation_target_changed'),
                             (self.owner.page.image_assets_widget, 'assets_changed'),
                             (self.owner.page.image_assets_widget, 'mode_changed'),
                             (panel.insertion_position, 'currentIndexChanged')):
            self._connect(getattr(source, name, None), self.invalidate)
        for name in ('finished', 'destroyed'):
            self._connect(getattr(self.owner, name, None), self._owner_closed)
        self._connect(getattr(getattr(self.owner, 'stop_button', None), 'clicked', None), self.invalidate)
        self.timer = QTimer(self); self.timer.setInterval(80)
        self.timer.timeout.connect(self.observe); self.timer.start()
        panel._transaction_scopes.add(self)

    def _connect(self, signal, callback):
        if signal is not None:
            signal.connect(callback); self.connections.append((signal, callback))

    def _owner_closed(self, *_):
        self.owner_closed = True; self.invalidate()

    def invalidate(self, *_):
        if self.closed or self.committed: return
        self.revoked.set()
        if self.dialog is not None:
            try: self.dialog.reject()
            except RuntimeError: pass

    def check(self):
        if self.committed: return
        if self.closed or self.revoked.is_set():
            raise ValueError('备课或选材窗口在读取期间变化，本批未加入；请重新选择。')
        try:
            self.panel.check_insertion(self.insertion)
            payload = getattr(self.owner.page, '_payload', None)
            if (self.history != (self.owner.history.value, self.owner.history.back, self.owner.history.forward)
                    or self.owner.page.image_assets_widget.assets() != self.assets
                    or getattr(self.owner.page, '_editor_draft_id', None) != self.target
                    or (callable(payload) and payload() != self.payload)):
                raise ValueError('备课、图片或编辑历史在选材期间变化，本批未加入；请重新选择。')
        except (ValueError, RuntimeError):
            self.invalidate(); raise

    def observe(self):
        if self.closed or self.committed: return
        try: self.check()
        except (ValueError, RuntimeError): pass

    def mark_committed(self, *_):
        self.committed = True
        self.close()

    def close(self):
        if self.closed: return
        self.closed = True; self.timer.stop(); self.dialog = None
        for signal, callback in self.connections:
            try: signal.disconnect(callback)
            except (TypeError, RuntimeError): pass
        self.connections.clear(); self.panel._transaction_scopes.discard(self)
        self.deleteLater()


class CompositionImageBatchEntry:
    """Own publication files until the single history commit has a receipt."""
    def __init__(self, panel):
        self.panel, self.owner = panel, panel.owner
        self.scope = None
        self.workspace = None

    def open(self, initial_source='images'):
        from .image_batch_workspace import ImageBatchWorkspace
        if self.workspace is not None:
            self.workspace.raise_(); self.workspace.activateWindow(); return
        if getattr(self.owner, 'busy', False):
            self.owner.status.setText('请先完成当前操作，再选本批教学图片。'); return
        insertion = self.panel.freeze_insertion()
        scope = FrozenCompositionScope(self.panel, insertion); self.scope = scope
        dialog = None; queued = False
        try:
            scope.check()
            dialog = ImageBatchWorkspace(self.owner.facade, self.owner.tasks, scope.assets,
                scope.resources, self.panel, initial_source=initial_source)
            self.workspace = scope.dialog = dialog
            outcome = dialog.exec()
            scope.check()
            if outcome != QDialog.DialogCode.Accepted or not dialog.selection_requests: return
            requests = deepcopy(dialog.selection_requests)
            scope.dialog = None
            self._queue(scope, requests); queued = True
        except (ValueError, OSError, RuntimeError) as exc:
            if not scope.owner_closed: self.owner.status.setText(str(exc))
        finally:
            self.workspace = None; scope.dialog = None
            if dialog is not None: dialog.deleteLater()
            if not queued:
                scope.close()
                if self.scope is scope: self.scope = None

    def _queue(self, scope, requests):
        from ..desktop_composition_image_batch import prepare_image_batch, image_batch_is_referenced
        panel, owner, facade, tasks = self.panel, self.owner, self.owner.facade, self.owner.tasks
        if getattr(owner, 'busy', False):
            raise ValueError('当前备课已有读取任务，本批未加入；完成后请重新选择。')
        # Workers capture immutable snapshots and the Event-only guard. The
        # GUI owns the rest of this state and all history/form comparisons.
        assets, resources = deepcopy(scope.assets), deepcopy(scope.resources)
        revoked = scope.revoked
        def worker_guard():
            if revoked.is_set():
                raise ReadCancelled('备课或选材窗口在读取期间变化，本批未加入；请重新选择。')
        state = {'publication': None, 'task': None, 'phase': 'reading', 'done': False}
        connections = []

        def referenced(publication):
            return image_batch_is_referenced(publication.result, owner.history.value,
                *owner.history.back, *owner.history.forward,
                getattr(owner.page, '_lesson_design', None), existing_assets=assets)

        def release():
            state['done'] = True; state['phase'] = 'finished'
            for signal, callback in connections:
                try: signal.disconnect(callback)
                except (RuntimeError, TypeError): pass
            connections.clear(); scope.close()
            if self.scope is scope: self.scope = None

        def abort(message=None):
            publication = state['publication']
            try:
                if publication is not None:
                    if scope.committed or referenced(publication):
                        publication.accept()
                    else:
                        if (not scope.owner_closed and
                                owner.page.image_assets_widget.assets() == publication.result['image_assets']):
                            owner.page.image_assets_widget.set_assets(deepcopy(assets))
                        publication.discard(referenced_assets=assets)
                if message and not scope.owner_closed: owner.status.setText(str(message))
            finally: release()

        def receipt(*history):
            # sync_page may emit stale-scope signals or even fail after put().
            # The history reference already owns these files at this point.
            scope.mark_committed(*history)
            state['publication'].accept()

        def finished(identity):
            if identity == state['task'] and state['phase'] in {'reading', 'rechecking'} and not state['done']:
                scope.invalidate(); abort()

        def cancelled(identity, *_):
            if identity == state['task'] and not state['done']: scope.invalidate()

        tasks.task_finished.connect(finished); connections.append((tasks.task_finished, finished))
        for name in ('task_cancelled', 'task_cancelled_detail'):
            signal = getattr(tasks, name, None)
            if signal is not None: signal.connect(cancelled); connections.append((signal, cancelled))

        def recheck(result, *, repaired=False):
            scope.check(); state['phase'] = 'rechecking'
            publication = state['publication']
            def work():
                worker_guard(); publication.recheck_published(worker_guard); return result
            def ready(checked):
                state['phase'] = 'layout'
                try:
                    scope.check()
                    if not checked['resources']:
                        publication.accept()
                        owner.status.setText('相同原图、导学问题与用途已在本课；已有编辑保持。')
                        release(); return
                    if repaired:
                        panel.add_result(checked, scope.expected, expected_assets=assets,
                            insertion=scope.insertion, commit_guard=scope.check, commit_receipt=receipt)
                        added = True
                    else:
                        added = panel.add_published_result(publication, checked, scope.expected,
                            expected_assets=assets, insertion=scope.insertion, guard=scope.check,
                            commit_receipt=receipt, on_recheck=lambda working: recheck(working, repaired=True))
                    if added:
                        owner.status.setText(f'本批 {len(checked["resources"])} 项教学图片已按顺序加入指定位置，可一次撤销。')
                        release()
                except Exception as exc: abort(str(exc))
            owner.run('正在最后核对本批原图与来源…', work, ready, cancellable=True)
            state['task'] = owner._task

        def prepare():
            publication = None
            try:
                worker_guard()
                publication = prepare_image_batch(facade, assets, resources, requests, guard=worker_guard)
                state['publication'] = publication
                return publication.publish(worker_guard)
            except BaseException:
                if publication is not None: publication.discard()
                raise

        def prepared(result):
            state['phase'] = 'layout'
            try: recheck(result)
            except Exception as exc: abort(str(exc))

        try:
            owner.run('正在核对并交接本批教学图片…', prepare, prepared, cancellable=True)
            state['task'] = owner._task
        except BaseException:
            abort(); raise

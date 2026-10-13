"""Open section-led selection before a classroom resource has been inserted."""
from copy import deepcopy
from threading import Event

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QLineEdit, QPlainTextEdit, QSpinBox,
)

from .components import set_status


def open_textbook_section_materials(page, selection, *, on_handoff=None, before_handoff=None):
    """Keep source reading detached, then use the studio's batch transaction.

    GUI changes revoke the scope permanently. Worker checks read only Events;
    form, assets and destination are compared on the GUI before handoff.
    """
    if page.studio_busy():
        set_status(page.status, 'attention', '请先完成当前备课操作，再从本节选材。')
        return False
    if (not isinstance(selection, dict)
            or set(selection) != {'book_key', 'section_filter_key', 'selection'}):
        set_status(page.status, 'error', '请选择一节已保存的教材章节。')
        return False
    frozen = deepcopy(selection)
    try:
        insertion = page.freeze_material_insertion()
        original_payload = deepcopy(page._payload())
        original_design = deepcopy(page._lesson_design)
        assets = deepcopy(page.image_assets_widget.assets())
        resources = deepcopy((original_design or {}).get('composition', {}).get('resources', []))
        target = page._editor_draft_id
    except (ValueError, RuntimeError) as exc:
        set_status(page.status, 'attention', str(exc))
        return False

    revoked, destroyed = Event(), Event()
    connections = []
    box = {'identity': None, 'dialog': None, 'cleaned': False}
    page._section_materials_job = box

    def report(state, message):
        if destroyed.is_set():
            return
        try:
            set_status(page.status, state, message)
        except RuntimeError:
            pass

    def revoke(*_):
        revoked.set()

    def owner_destroyed(*_):
        destroyed.set()
        revoke()

    def connect(signal, callback=revoke):
        signal.connect(callback)
        connections.append((signal, callback))

    # Track transitions, including A -> B -> A, rather than equality alone.
    for kind, names in ((QLineEdit, ('textChanged',)),
                        (QPlainTextEdit, ('textChanged',)),
                        (QComboBox, ('currentIndexChanged',)),
                        (QSpinBox, ('valueChanged',)),
                        (QDoubleSpinBox, ('valueChanged',))):
        for widget in page.findChildren(kind):
            for name in names:
                connect(getattr(widget, name))
    for source, name in ((page, 'preparation_target_changed'),
                         (page.image_assets_widget, 'assets_changed'),
                         (page.image_assets_widget, 'mode_changed')):
        signal = getattr(source, name, None)
        if signal is not None:
            connect(signal)
    connect(page.destroyed, owner_destroyed)

    def worker_guard():
        if revoked.is_set() or destroyed.is_set() or getattr(page.tasks, 'is_shutting_down', False):
            raise ValueError('当前备课在选材期间变化，请重新从教材打开。')

    def current():
        worker_guard()
        page.check_material_insertion(insertion, before_open=True)
        if (page._editor_draft_id != target or page._payload() != original_payload
                or page._lesson_design != original_design
                or page.image_assets_widget.assets() != assets):
            revoke()
            raise ValueError('目标备课、图片或加入位置已经变化，请重新选材。')

    def cleanup():
        if box['cleaned']:
            return
        box['cleaned'] = True
        revoke()
        for signal, callback in connections:
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        if getattr(page, '_section_materials_job', None) is box:
            page._section_materials_job = None

    def cancelled(identity, *_):
        if identity != box['identity']:
            return
        revoke()
        if box['dialog'] is not None:
            box['dialog'].reject()
        else:
            cleanup()
            report('info', '本次章节读取已停止。')

    cancel_signal = getattr(page.tasks, 'task_cancelled', None)
    if cancel_signal is not None:
        connect(cancel_signal, cancelled)

    def read():
        from ..desktop_section_materials import textbook_section_material_context
        worker_guard()
        context = textbook_section_material_context(page.facade,
            frozen['book_key'], frozen['section_filter_key'], frozen['selection'])
        worker_guard()
        return context

    def prepare(facade, request, image_assets, course_resources):
        from ..desktop_section_material_handoff import prepare_section_material_requests
        if (set(request) != {'context', 'requests'} or not request['requests']
                or any(row.get('context') != request['context'] for row in request['requests'])):
            raise ValueError('本次授课清单与已选教材章节不一致，请重新选择。')
        return prepare_section_material_requests(facade, None, course_resources,
                                                 image_assets, request['requests'])

    def ready(context):
        from .section_materials_dialog import SectionMaterialsDialog
        dialog = None
        try:
            current()
            dialog = SectionMaterialsDialog(page.facade, page.tasks, context, page,
                guard=worker_guard, course_resources=resources, image_assets=assets,
                batch_mode=True)
            box['dialog'] = dialog
            outcome = dialog.exec()
            current()
            if outcome != QDialog.DialogCode.Accepted or not dialog.requests:
                report('info', '已返回教材；授课清单可重新选取。')
                return
            if before_handoff is not None and not before_handoff():
                report('attention', '组卷修改尚未保存，本次选材未加入；请处理后重新选择。')
                return
            request = {'context': deepcopy(context), 'requests': deepcopy(dialog.requests)}
            # The existing transaction owns all publication and the one undo.
            cleanup()
            page._source_material_insertion = deepcopy(insertion)
            handoff_options = {'before_handoff': before_handoff} if before_handoff is not None else {}
            accepted = page._import_material_batch(request, prepare,
                '正在核对本节授课清单并加入当前备课…', **handoff_options)
            if accepted and on_handoff is not None:
                on_handoff()
        except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
            report('attention', str(exc))
        finally:
            cleanup()
            if dialog is not None:
                dialog.deleteLater()

    def failed(message):
        cleanup()
        report('attention', str(message))

    try:
        report('info', '正在打开本节备课选材…')
        box['identity'] = page.tasks.submit('读取教材本节备课选材', read,
            on_success=ready, on_failure=failed)
    except (ValueError, RuntimeError, TypeError) as exc:
        failed(exc)
        return False
    return True

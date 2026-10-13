"""Explicitly link a sealed crop to the same book's verified catalog location."""
from copy import deepcopy

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from shiboken6 import isValid


def _text(value):
    label = QLabel(value)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


class TextbookSourceLocationDialog(QDialog):
    def __init__(self, facade, item, assets, preview, parent=None, *, tasks, current):
        super().__init__(parent)
        if (not isinstance(preview, dict) or any(not isinstance(preview.get(key), str)
                or not preview[key] for key in ('preview_id', 'old_path', 'current_path', 'title'))):
            raise ValueError('原书位置核对信息不完整，请返回课件后重新打开。')
        self.facade, self.tasks, self._current = facade, tasks, current
        self._item, self._assets, self._preview = deepcopy(item), deepcopy(assets), deepcopy(preview)
        self._closed = False
        self._busy = False
        self._serial = 0
        self._task = None
        self.linked = False
        self.setWindowTitle('关联同一原书的新位置')
        self.resize(640, 390)
        self.setMinimumSize(360, 310)
        layout = QVBoxLayout(self)
        self.title = _text(preview['title'])
        self.title.setObjectName('CardTitle')
        layout.addWidget(self.title)
        layout.addWidget(_text('资料库已找到这本原书的当前位置。确认后重新核对并打开教材原页。'))
        layout.addWidget(_text('保存时的位置'))
        self.old_path = _text(preview['old_path'])
        self.old_path.setAccessibleName('教材原书保存时的位置')
        layout.addWidget(self.old_path)
        layout.addWidget(_text('当前资料库位置'))
        self.current_path = _text(preview['current_path'])
        self.current_path.setAccessibleName('同一教材原书在当前资料库中的位置')
        layout.addWidget(self.current_path)
        self.status = _text('确认关联后继续对照原页。')
        layout.addWidget(self.status)
        layout.addStretch(1)
        actions = QHBoxLayout()
        self.cancel_button = QPushButton('取消')
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button = QPushButton('关联并打开原页')
        self.confirm_button.setObjectName('PrimaryAction')
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.confirm_link)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)
        self._guard_timer = QTimer(self)
        self._guard_timer.setInterval(100)
        self._guard_timer.timeout.connect(self._guard_current)
        self._guard_timer.start()
        if hasattr(tasks, 'task_finished'):
            tasks.task_finished.connect(self._task_finished)

    def _alive(self, serial=None):
        return not self._closed and isValid(self) and (serial is None or serial == self._serial)

    def _guard_current(self):
        if not self._alive():
            return False
        try:
            self._current()
            return True
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            self.reject()
            return False

    def _set_busy(self, value):
        self._busy = value
        self.confirm_button.setEnabled(not value and not self._closed)

    def confirm_link(self):
        if self._busy or not self._guard_current():
            return
        from ..desktop_textbook_source_locations import stage_textbook_source_relocation
        self._serial += 1
        serial = self._serial
        self._set_busy(True)
        self.status.setText('正在核对当前原书位置…')
        item, assets = deepcopy(self._item), deepcopy(self._assets)
        preview_id = self._preview['preview_id']

        def failed(message):
            if not self._alive(serial):
                return
            self._serial += 1
            self._task = None
            self._set_busy(False)
            self.status.setText(str(message))

        def ready(publication):
            if not self._alive(serial):
                return
            self._task = None
            try:
                self._current()
                receipt = publication.publish()
                if (not isinstance(receipt, dict) or receipt.get('linked') is not True
                        or receipt.get('original_unchanged') is not True
                        or receipt.get('current_path') != self._preview['current_path']):
                    raise ValueError('原书位置关联结果不完整，请返回课件后重新打开原页。')
            except Exception as exc:
                failed(str(exc))
                return
            self.linked = True
            self.status.setText('已关联当前原书位置，正在打开原页')
            self.accept()

        def work():
            try:
                return stage_textbook_source_relocation(self.facade, item, assets, expected_preview=preview_id)
            except (ValueError, OSError) as exc:
                from ..desktop_preparation import DesktopPreparationError
                raise DesktopPreparationError('textbook_source_relocation', str(exc)) from exc

        try:
            task = self.tasks.submit('核对并关联当前原书位置', work,
                on_success=ready, on_failure=failed)
        except Exception as exc:
            failed(str(exc))
            return
        if self._alive(serial) and self._busy:
            self._task = task

    def _task_finished(self, identity):
        if self._alive() and self._busy and identity == self._task:
            self._serial += 1
            self._task = None
            self._set_busy(False)
            self.status.setText('本次核对未完成，可重新关联或返回课件。')

    def done(self, result):
        if not self._closed:
            self._closed = True
            self._serial += 1
            self._guard_timer.stop()
            task, self._task = self._task, None
            if task is not None:
                self.tasks.cancel(task)
            self.confirm_button.setEnabled(False)
        super().done(result)

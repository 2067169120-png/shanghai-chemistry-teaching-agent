"""One original-page editor for an ordered, explicitly confirmed crop batch."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QBoxLayout, QComboBox, QLabel, QPushButton

from ..desktop_preparation_images import MAX_IMAGES
from .components import set_status
from .textbook_preparation_crop_dialog import TextbookPreparationCropDialog


class TextbookBatchCropDialog(TextbookPreparationCropDialog):
    def __init__(self, facade, tasks, choices, parent=None, *, allow_composition_handoff=False):
        if not isinstance(choices, list) or not 1 <= len(choices) <= MAX_IMAGES:
            raise ValueError(f'每批请选择1至{MAX_IMAGES}张图表。')
        self.choices = deepcopy(choices)
        for row in self.choices:
            if (not isinstance(row, dict) or not isinstance(row.get('selection'), dict)
                    or set(row['selection']) != {'visual_asset_id', 'revision'}
                    or not isinstance(row.get('label'), str)):
                raise ValueError('请从当前教材目录或个人原书页重新选择图表。')
        self._index = 0
        self._confirmed = {}
        self._drafts = {}
        self._switching = True
        self._restore_request = None
        self.selection_requests = None
        super().__init__(facade, tasks, self.choices[0]['selection'], parent,
                         allow_composition_handoff=allow_composition_handoff)
        self.setWindowTitle('批量选取教材图表')
        self.steps = QLabel()
        self.steps.setWordWrap(True)
        self.steps.setMinimumWidth(0)
        self.steps.setAccessibleName('本批图表核对进度与提交状态')
        self.choice = QComboBox()
        self.choice.setMinimumWidth(0)
        self.choice.setMinimumContentsLength(10)
        self.choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.choice.setAccessibleName('选择本批中需要重新核对的图表')
        self.choice.currentIndexChanged.connect(self._choose)
        self.previous = QPushButton('上一张')
        self.next = QPushButton('下一张')
        self.previous.setAutoDefault(False); self.next.setAutoDefault(False)
        self.previous.clicked.connect(lambda: self._choose(self._index - 1))
        self.next.clicked.connect(lambda: self._choose(self._index + 1))
        self.navigation = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.navigation.addWidget(self.choice, 1)
        self.navigation.addWidget(self.previous)
        self.navigation.addWidget(self.next)
        self.layout().insertLayout(0, self.navigation)
        self.layout().insertWidget(0, self.steps)
        self.cancel.setText('返回教材，暂不加入')
        self.reload.setText('重读本图')
        self._switching = False
        self._update_controls()

    def _invalidate_preview(self, *_):
        if not self._switching:
            self._confirmed.pop(self._index, None)
        super()._invalidate_preview()

    def _choose(self, index):
        if self._closed or self._busy or not 0 <= index < len(self.choices) or index == self._index:
            if hasattr(self, 'choice'):
                with QSignalBlocker(self.choice):
                    self.choice.setCurrentIndex(self._index)
            return
        self._switching = True
        try:
            if self._page is not None:
                self._drafts[self._index] = {'page_revision': self._page['page_revision'],
                    'bounds': deepcopy(self._chosen_bounds), 'caption': self.caption.text(),
                    'purpose': self.purpose.text()}
            self._index = index
            # Reopening a confirmed image is an explicit edit, so the teacher
            # must preview and confirm it again before the batch can be sent.
            self._restore_request = self._confirmed.pop(index, None) or self._drafts.get(index)
            self.selection = deepcopy(self.choices[index]['selection'])
            with QSignalBlocker(self.purpose):
                self.purpose.clear()
            self._load_page()
        finally:
            self._switching = False
        self._update_controls()

    def _refresh_navigation(self):
        if not hasattr(self, 'choice'):
            return
        with QSignalBlocker(self.choice):
            self.choice.clear()
            for index, row in enumerate(self.choices):
                mark = '已核对' if index in self._confirmed else '待核对'
                title = self._confirmed.get(index, {}).get('caption', row['label'])
                self.choice.addItem(f'{index + 1} · {mark} · {title}')
            self.choice.setCurrentIndex(self._index)
        count, total = len(self._confirmed), len(self.choices)
        self.steps.setText(f'本批 {total} 张 · 已核对 {count} 张。'
            + ('全部核对后可一次加入当前备课。' if count < total
               else '本批图题、用途与裁片已逐张核对；点击下方按钮加入。'))
        usable = not self._busy and not self._closed
        self.choice.setEnabled(usable)
        self.previous.setEnabled(usable and self._index > 0)
        self.next.setEnabled(usable and self._index + 1 < total)
        final_label = '只入备课图库' if self.allow_composition_handoff else '加入当前备课'
        self.confirm.setText(f'{final_label}（{total}张）' if count == total else '确认本图并继续')
        self.compose.setText(f'加入并编排（{total}张）')
        self.compose.setEnabled(self.allow_composition_handoff and usable and count == total
                                and self._preview is not None and self.checked.isChecked())

    def _update_controls(self, *_):
        super()._update_controls()
        if not hasattr(self, 'choice'):
            return
        if self._page is not None and not self._busy and not self._switching and self._restore_request is not None:
            saved, self._restore_request = self._restore_request, None
            if saved['page_revision'] == self._page['page_revision']:
                self._switching = True
                try:
                    with QSignalBlocker(self.caption), QSignalBlocker(self.purpose):
                        self.caption.setText(saved['caption']); self.purpose.setText(saved['purpose'])
                    if saved['bounds'] is not None:
                        self._chosen_bounds = deepcopy(saved['bounds'])
                        edges = tuple(saved['bounds'][k] for k in ('left', 'top', 'right', 'bottom'))
                        self.canvas.set_bounds(edges); self._set_edges(edges)
                finally:
                    self._switching = False
            set_status(self.status, 'info', '正在重新核对这一图。请预览裁片，再勾选确认。')
            super()._update_controls()
        if (self._index in self._confirmed and self._preview is not None and not self._busy
                and not self.checked.isChecked()):
            self._confirmed.pop(self._index)
        self._refresh_navigation()

    def _confirm(self):
        if not self.confirm.isEnabled():
            return
        if len(self._confirmed) == len(self.choices):
            self.handoff_mode = 'assets'
            self.selection_requests = [deepcopy(self._confirmed[i]) for i in range(len(self.choices))]
            self.accept()
            return
        self._confirmed[self._index] = self._current_request()
        unconfirmed = [i for i in range(len(self.choices)) if i not in self._confirmed]
        if unconfirmed:
            self._choose(unconfirmed[0])
        else:
            self._update_controls()
            set_status(self.status, 'success', '本批裁片已逐张核对。核对图题与用途后，一次加入当前备课。')

    def _confirm_composition(self):
        if not self.compose.isEnabled() or len(self._confirmed) != len(self.choices): return
        self.handoff_mode = 'composition'
        self.selection_requests = [deepcopy(self._confirmed[i]) for i in range(len(self.choices))]
        self.accept()

    def resizeEvent(self, event):
        if hasattr(self, 'navigation'):
            self.navigation.setDirection(QBoxLayout.Direction.TopToBottom if event.size().width() < 650
                                         else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)

    def done(self, result):
        if result != self.DialogCode.Accepted:
            self.selection_requests = None
        self._confirmed.clear()
        self._drafts.clear()
        super().done(result)

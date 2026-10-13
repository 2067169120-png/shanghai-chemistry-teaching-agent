"""Choose an exact original answer and inspect the real classroom page."""
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter,
    QVBoxLayout, QWidget)

from ..desktop_composition_render import layout_composition, page_image
from ..desktop_lesson_composition import resource_frames, validate_composition
from ..desktop_question_answer_reveal import (FIELD, answer_reveal_options,
    clear_question_answer_reveal, configure_question_answer_reveal,
    question_answer_reveal_errors)


def _label(text):
    value = QLabel(text); value.setWordWrap(True); value.setMinimumWidth(0)
    value.setTextFormat(Qt.TextFormat.PlainText)
    return value


def _combo():
    value = QComboBox(); value.setMinimumWidth(0); value.setMinimumContentsLength(10)
    value.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return value


class QuestionAnswerRevealDialog(QDialog):
    def __init__(self, composition, resource_id, assets=(), image_data=None, parent=None, *, guard=None):
        super().__init__(parent)
        self.original = validate_composition(composition)
        self.resource_id = resource_id
        self.item = next(row for row in self.original['resources'] if row['id'] == resource_id)
        self.rows = answer_reveal_options(self.item)
        self.assets, self.image_data = deepcopy(list(assets)), dict(image_data or {})
        self.guard = guard; self.guard_error = None
        self.result_value = self._candidate = None
        self._layouts = {}; self._loading = True
        self.setWindowTitle('题面同页／点击原答案'); self.resize(1220, 850); self.setMinimumSize(430, 590)
        root = QVBoxLayout(self)
        root.addWidget(_label('把完整题面放在一张课堂页上，保留原图与24磅正文。需要点击答案时，请逐块核对原文并明确选择；静态学生 Word／PDF 隐藏答案与解析。'))
        self.split = QSplitter(); root.addWidget(self.split, 1)
        left = QWidget(); fields = QVBoxLayout(left)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.addWidget(_label(self.item['title']))
        self.same_page = QCheckBox('启用完整题面同页')
        self.same_page.setAccessibleName('启用或清除这道完整题的课堂同页设置')
        self.same_page.setChecked(FIELD in self.item or self.original['format'] == '16:9')
        self.same_page.setEnabled(FIELD in self.item or self.original['format'] == '16:9')
        fields.addWidget(self.same_page)
        fields.addWidget(_label('标题旁点击显示哪块原答案（可不选；最多一块短文字）'))
        self.answers = QListWidget(); self.answers.setWordWrap(True); self.answers.setMinimumWidth(0)
        self.answers.setAccessibleName('按原文区块逐项选择点击答案，默认不选')
        self.answers.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        fields.addWidget(self.answers, 1)
        for row in self.rows:
            item = QListWidgetItem(f'原文区块 {row["source_index"]} · {row["text"]}')
            item.setData(Qt.ItemDataRole.UserRole, row['block_id'])
            if row['supported']:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if row['selected'] else Qt.CheckState.Unchecked)
            item.setToolTip(row['reason'] or '只显示此块当前作品文字；其他答案与解析保留在教师范围。')
            self.answers.addItem(item)
        self.no_answer = QPushButton('不显示答案，仅保留题面同页')
        self.no_answer.setAutoDefault(False); fields.addWidget(self.no_answer)
        self.source = QPlainTextEdit(); self.source.setReadOnly(True); self.source.setMinimumWidth(0)
        self.source.setMinimumHeight(80); self.source.setMaximumHeight(210)
        self.source.setAccessibleName('所选答案的原文、当前作品副本与区块标识')
        fields.addWidget(self.source)
        self.scope_note = _label(''); fields.addWidget(self.scope_note)
        self.split.addWidget(left)
        right = QWidget(); preview = QVBoxLayout(right); preview.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout(); self.audience = _combo()
        for name, key in (('课堂点击前', 'before'), ('课堂点击后', 'after'),
                          ('静态学生页', 'student'), ('教师完整页', 'teacher')):
            self.audience.addItem(name, key)
        self.audience.setAccessibleName('原答案点击前后与静态学生实际预览')
        self.click_preview = QPushButton('预览一次点击'); self.click_preview.setAutoDefault(False)
        bar.addWidget(self.audience, 1); bar.addWidget(self.click_preview); preview.addLayout(bar)
        navigation = QHBoxLayout(); self.previous = QPushButton('上一页'); self.next = QPushButton('下一页')
        self.pages = _combo(); self.pages.setAccessibleName('完整题预览页码')
        for button in (self.previous, self.next): button.setAutoDefault(False)
        navigation.addWidget(self.previous); navigation.addWidget(self.pages, 1); navigation.addWidget(self.next)
        preview.addLayout(navigation)
        self.scene = QGraphicsScene(self); self.view = QGraphicsView(self.scene)
        self.view.setBackgroundBrush(QColor('#e8eeeb')); self.view.setMinimumHeight(180)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setAccessibleName('保持整页比例的题面与原答案原生排版预览')
        preview.addWidget(self.view, 1); self.split.addWidget(right); self.split.setSizes([340, 820])
        self.status = _label(''); root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.apply_button.setText('应用到这道完整题')
        self.apply_button.setObjectName('QuestionAnswerRevealApply')
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel); self.cancel_button.setText('取消')
        root.addWidget(buttons); buttons.accepted.connect(self.apply); buttons.rejected.connect(self.reject)
        self.answers.itemChanged.connect(self.selection_changed)
        self.answers.currentItemChanged.connect(self.show_source)
        self.same_page.toggled.connect(self.update_preview)
        self.no_answer.clicked.connect(self.clear_answers)
        self.audience.currentIndexChanged.connect(self.show_layout)
        self.click_preview.clicked.connect(self.preview_click)
        self.pages.currentIndexChanged.connect(self.show_page)
        self.previous.clicked.connect(lambda: self.pages.setCurrentIndex(self.pages.currentIndex()-1))
        self.next.clicked.connect(lambda: self.pages.setCurrentIndex(self.pages.currentIndex()+1))
        self._guard_timer = QTimer(self); self._guard_timer.setInterval(100)
        self._guard_timer.timeout.connect(self.check_guard)
        if guard is not None: self._guard_timer.start()
        self._loading = False; self.answers.setCurrentRow(0); self.update_preview()

    def block_ids(self):
        return [self.answers.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.answers.count())
                if self.answers.item(i).checkState() == Qt.CheckState.Checked]

    def selection_changed(self, selected):
        if self._loading: return
        if selected.checkState() == Qt.CheckState.Checked:
            with QSignalBlocker(self.answers):
                for i in range(self.answers.count()):
                    item = self.answers.item(i)
                    if item is not selected and item.checkState() == Qt.CheckState.Checked:
                        item.setCheckState(Qt.CheckState.Unchecked)
            self.answers.setCurrentItem(selected)
        self.update_preview()

    def clear_answers(self):
        with QSignalBlocker(self.answers):
            for i in range(self.answers.count()):
                item = self.answers.item(i)
                if item.flags() & Qt.ItemFlag.ItemIsUserCheckable: item.setCheckState(Qt.CheckState.Unchecked)
        self.update_preview()

    def show_source(self, item, *_):
        if item is None: self.source.clear(); return
        row = next(row for row in self.rows if row['block_id'] == item.data(Qt.ItemDataRole.UserRole))
        text = f'原文区块 {row["source_index"]}\n原文：\n{row["original_text"]}'
        if row['working_value_changed']: text += '\n\n本作品已修改，课堂显示当前副本：\n' + row['text']
        if row['reason']: text += '\n\n' + row['reason']
        self.source.setPlainText(text + '\n\n区块标识：' + row['block_id'])

    def check_guard(self):
        try:
            if self.guard is not None: self.guard()
            return True
        except (ValueError, RuntimeError) as exc:
            self.guard_error = str(exc); self.status.setText(str(exc)); self.apply_button.setEnabled(False)
            self._guard_timer.stop(); return False

    def _selected_composition(self, value):
        result = deepcopy(value)
        result['resources'] = [deepcopy(next(r for r in value['resources'] if r['id'] == self.resource_id))]
        result['placements'] = {self.resource_id: deepcopy(value['placements'][self.resource_id])}
        used = {f['page_id'] for f in resource_frames(result['placements'][self.resource_id])}
        result['pages'] = [p for p in result['pages'] if p in used]
        if 'click_reveals' in result:
            result['click_reveals'] = [identity for identity in result['click_reveals'] if identity == self.resource_id]
        return validate_composition(result)

    def update_preview(self, *_):
        if self._loading: return
        self._candidate = None; self._layouts = {}; self.apply_button.setEnabled(False)
        projection = self.original['format'] == '16:9'
        editable = projection and self.same_page.isChecked()
        self.answers.setEnabled(editable); self.no_answer.setEnabled(editable)
        self.scope_note.setText('取消同页设置后保留当前外框与分页。' if not self.same_page.isChecked() else
            '当前为打印画幅，已保存的课堂设置暂不启用；切回16:9后恢复。' if not projection else
            '其他答案、解析与教师备注仅在教师完整页显示。')
        if not self.check_guard(): return
        try:
            if editable:
                value = configure_question_answer_reveal(self.original, self.resource_id,
                    self.block_ids(), self.assets, self.image_data)
            elif not self.same_page.isChecked(): value = clear_question_answer_reveal(self.original, self.resource_id)
            else: value = deepcopy(self.original)
            errors = question_answer_reveal_errors(value, self.resource_id, self.assets, self.image_data)
            if errors: raise ValueError('\n'.join(errors))
            selected = self._selected_composition(value)
            for key, student, reveals in (('before', True, False), ('after', True, True),
                                          ('student', True, False), ('teacher', False, False)):
                result = layout_composition(selected, self.assets, self.image_data,
                    student=student, include_reveals=reveals)
                if result['errors']: raise ValueError('\n'.join(result['errors']))
                self._layouts[key] = result
            self._candidate = value; self.apply_button.setEnabled(True)
            self.status.setText(f'完整题面 {len(self._layouts["student"]["pages"])} 页 · '
                + ('已明确选择一块原答案，课堂点击后显示。' if editable and self.block_ids() else '当前未启用课堂答案点击。'))
        except (ValueError, OSError, KeyError) as exc:
            self.status.setText(str(exc)); self._layouts = {}
        self.show_layout()

    def preview_click(self):
        self.audience.setCurrentIndex(self.audience.findData('before' if self.audience.currentData() == 'after' else 'after'))

    def show_layout(self, *_):
        previous = self.pages.currentData()
        with QSignalBlocker(self.pages):
            self.pages.clear(); result = self._layouts.get(self.audience.currentData())
            if result is not None:
                for index, page in enumerate(result['pages']): self.pages.addItem(f'第{index+1}／{len(result["pages"])}页', page)
                self.pages.setCurrentIndex(max(0, self.pages.findData(previous)))
        self.click_preview.setText('回到点击前' if self.audience.currentData() == 'after' else '预览一次点击')
        self.show_page()

    def show_page(self, *_):
        self.scene.clear(); result = self._layouts.get(self.audience.currentData()); page = self.pages.currentData()
        if result is not None and page in result['pages']:
            self.scene.addPixmap(QPixmap.fromImage(page_image(result, page)))
            self.scene.setSceneRect(self.scene.itemsBoundingRect()); self.fit_view()
        self.previous.setEnabled(self.pages.currentIndex() > 0)
        self.next.setEnabled(0 <= self.pages.currentIndex() < self.pages.count()-1)

    def fit_view(self):
        if self.scene.items(): self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'split'):
            self.split.setOrientation(Qt.Orientation.Vertical if event.size().width() < 800 else Qt.Orientation.Horizontal)
            QTimer.singleShot(0, self.fit_view)

    def apply(self):
        self.update_preview()
        if self._candidate is not None and self.check_guard():
            self.result_value = deepcopy(self._candidate); self.accept()

    def done(self, result):
        self._guard_timer.stop()
        if result != QDialog.DialogCode.Accepted: self.result_value = None
        super().done(result)

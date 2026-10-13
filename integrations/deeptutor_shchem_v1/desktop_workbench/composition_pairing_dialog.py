"""Choose two existing classroom resources and preview their authored page layout."""
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from ..desktop_lesson_composition import (edit_resource, resource_frames,
    set_click_reveal, validate_composition)
from ..desktop_composition_render import layout_composition, page_image
from .components import page_scroll


def _label(text):
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    return value


class CompositionPairingDialog(QDialog):
    def __init__(self, composition, assets, image_data, parent=None, *, selected=None):
        super().__init__(parent)
        self.setWindowTitle('组成图文讲解页')
        self.resize(1120, 820)
        self.setMinimumSize(400, 500)
        self.original = validate_composition(composition)
        self.assets = deepcopy(assets)
        self.image_data = dict(image_data)
        self.result_value = None
        self._candidate = None
        self._layouts = {}
        self._loading = True
        root = QVBoxLayout(self)
        root.addWidget(_label('选择本课两项素材，用原图、问题和知识讲解组成课堂页。先查看实际排版，再应用到当前备课。'))
        controls = QWidget()
        form = QFormLayout(controls)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.first = self._combo()
        self.second = self._combo()
        for item in self.original['resources']:
            for combo in (self.first, self.second):
                combo.addItem(item['title'], item['id'])
                combo.setItemData(combo.count()-1, item['title'], Qt.ItemDataRole.ToolTipRole)
        first, second = self._defaults(selected)
        self.first.setCurrentIndex(self.first.findData(first))
        self.second.setCurrentIndex(self.second.findData(second))
        form.addRow('原图或问题（第一项）', self.first)
        form.addRow('知识讲解（第二项）', self.second)
        self.question_title = QLineEdit()
        self.question_title.setPlaceholderText('用一个问题引导学生观察和思考')
        self.question_title.setMaxLength(2000)
        form.addRow('左上导学问题', self.question_title)
        self.explanation_title = QLineEdit()
        self.explanation_title.setMaxLength(2000)
        form.addRow('讲解标题', self.explanation_title)
        self.mode = self._combo()
        self.mode.addItem('左图／问题，右侧讲解', 'side_by_side')
        self.mode.addItem('上方原图／问题，下方讲解', 'stacked')
        form.addRow('课堂页布局', self.mode)
        self.classroom_uses_pair = QCheckBox('课堂PPT与静态学生页沿用组成页的位置')
        self.classroom_uses_pair.setChecked(True)
        form.addRow(self.classroom_uses_pair)
        self.arrangement_note = _label('')
        form.addRow(self.arrangement_note)
        self.click_second = QCheckBox('第二项内容在课堂PPT中点击后显示')
        form.addRow(self.click_second)
        self.click_note = _label('点击讲解用于课堂放映；静态学生页先呈现问题和材料。')
        form.addRow(self.click_note)
        self.controls_scroll = page_scroll(controls)
        self.controls_scroll.setMaximumHeight(330)
        self.controls_scroll.setMinimumHeight(150)
        root.addWidget(self.controls_scroll)
        toolbar = QHBoxLayout()
        self.audience = self._combo()
        self.audience.addItem('教师完整页', 'teacher')
        self.audience.addItem('课堂PPT完成态', 'classroom')
        self.audience.addItem('静态学生页', 'student')
        self.audience.setAccessibleName('图文页预览范围')
        toolbar.addWidget(self.audience, 1)
        self.focus = QPushButton('放大预览')
        self.focus.setCheckable(True)
        toolbar.addWidget(self.focus)
        root.addLayout(toolbar)
        navigation = QHBoxLayout()
        self.previous = QPushButton('上一页')
        navigation.addWidget(self.previous)
        self.pages = self._combo()
        self.pages.setAccessibleName('图文讲解页预览页码')
        navigation.addWidget(self.pages, 1)
        self.next = QPushButton('下一页')
        navigation.addWidget(self.next)
        root.addLayout(navigation)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setBackgroundBrush(QColor('#e8eeeb'))
        self.view.setMinimumHeight(130)
        self.view.setAccessibleName('图文讲解页实际排版预览')
        root.addWidget(self.view, 1)
        self.status = _label('')
        root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText('应用图文讲解页')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        root.addWidget(buttons)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.update_preview)
        self.first.currentIndexChanged.connect(lambda *_: self.resource_changed('first'))
        self.second.currentIndexChanged.connect(lambda *_: self.resource_changed('second'))
        self.mode.currentIndexChanged.connect(self.schedule_preview)
        self.question_title.textChanged.connect(self.schedule_preview)
        self.explanation_title.textChanged.connect(self.schedule_preview)
        self.classroom_uses_pair.toggled.connect(self.schedule_preview)
        self.click_second.toggled.connect(self.schedule_preview)
        self.audience.currentIndexChanged.connect(self.show_layout)
        self.pages.currentIndexChanged.connect(self.show_page)
        self.previous.clicked.connect(lambda: self.pages.setCurrentIndex(self.pages.currentIndex()-1))
        self.next.clicked.connect(lambda: self.pages.setCurrentIndex(self.pages.currentIndex()+1))
        self.focus.toggled.connect(self.focus_preview)
        self.resource_changed('first')
        self.resource_changed('second')
        self._loading = False
        self.update_preview()

    @staticmethod
    def _combo():
        value = QComboBox()
        value.setMinimumWidth(0)
        value.setMinimumContentsLength(12)
        value.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return value

    def _defaults(self, selected):
        rows = self.original['resources']
        if len(rows) < 2:
            return None, None
        selected_row = next((r for r in rows if r['id'] == selected), rows[0])
        images = {'textbook_image', 'handout_image', 'preparation_image'}
        if selected_row['original']['kind'] in images:
            first = selected_row
            binding = first['original'].get('selected_from_knowledge', {})
            second = next((r for r in rows if r['id'] == binding.get('resource_id')
                           and r['original_sha256'] == binding.get('original_sha256')), None)
            if second is None:
                candidates = [r for r in rows if r['id'] != first['id']]
                index = rows.index(first)
                second = min(candidates, key=lambda r: (
                    r['original']['kind'] not in {'knowledge_note', 'handout_knowledge', 'personal_text'},
                    abs(rows.index(r)-index)))
        elif selected_row['original']['kind'] == 'complete_question':
            first = selected_row
            candidates = [r for r in rows if r['id'] != first['id']]
            second = min(candidates, key=lambda r: (
                r['original']['kind'] not in {'knowledge_note', 'personal_text'},
                abs(rows.index(r)-rows.index(first))))
        else:
            second = selected_row
            candidates = [r for r in rows if r['id'] != second['id']]
            first = min(candidates, key=lambda r: (
                r['original']['kind'] not in images,
                abs(rows.index(r)-rows.index(second))))
        return first['id'], second['id']

    def resource_changed(self, which):
        combo = self.first if which == 'first' else self.second
        item = next((r for r in self.original['resources'] if r['id'] == combo.currentData()), None)
        field = self.question_title if which == 'first' else self.explanation_title
        field.blockSignals(True)
        field.setText(item['title'] if item is not None else '')
        field.blockSignals(False)
        if which == 'second':
            self.click_second.blockSignals(True)
            self.click_second.setChecked(item is not None and item['id'] in self.original.get('click_reveals', []))
            self.click_second.blockSignals(False)
        self.schedule_preview()

    def schedule_preview(self, *_):
        if not self._loading:
            self.apply_button.setEnabled(False)
            self._preview_timer.start(180)

    def update_preview(self):
        from ..desktop_composition_pairing import compose_teaching_pair
        self._preview_timer.stop()
        self._candidate = None
        self._layouts = {}
        try:
            value = deepcopy(self.original)
            for identity, field in ((self.first.currentData(), self.question_title),
                                    (self.second.currentData(), self.explanation_title)):
                title = field.text().strip()
                if not title:
                    raise ValueError('请填写导学问题和讲解标题。')
                value = edit_resource(value, identity, title=title)
            value = set_click_reveal(value, self.second.currentData(), self.click_second.isChecked())
            value = compose_teaching_pair(value, self.assets, self.image_data,
                self.first.currentData(), self.second.currentData(), mode=self.mode.currentData(),
                classroom_uses_pair=self.classroom_uses_pair.isChecked())
            for key, student, reveals in (('teacher', False, False), ('classroom', True, True), ('student', True, False)):
                rendered = layout_composition(value, self.assets, self.image_data,
                    student=student, include_reveals=reveals)
                if rendered['errors']:
                    raise ValueError('\n'.join(rendered['errors']))
                self._layouts[key] = rendered
            self._candidate = value
            self.apply_button.setEnabled(True)
            paired = self.classroom_uses_pair.isChecked()
            self.arrangement_note.setText('课堂使用这次组成的页面；原有学生单独排版会一并切换，可整步撤销。'
                if paired and 'student_arrangement' in self.original else
                '课堂使用这次组成的页面。' if paired else
                '保留现有学生页面编排；若采用单独排版，组成页仅用于教师版本。')
            self.status.setText(f"教师 {len(self._layouts['teacher']['pages'])} 页 · 课堂PPT {len(self._layouts['classroom']['pages'])} 页 · 静态学生 {len(self._layouts['student']['pages'])} 页；长内容完整续页。")
        except (ValueError, KeyError, OSError) as exc:
            self.apply_button.setEnabled(False)
            self.status.setText(str(exc))
        self.show_layout()

    def show_layout(self, *_):
        previous = self.pages.currentData()
        self.pages.blockSignals(True)
        self.pages.clear()
        result = self._layouts.get(self.audience.currentData())
        if result is not None:
            for index, identity in enumerate(result['pages']):
                self.pages.addItem(f'第 {index+1} / {len(result["pages"])} 页', identity)
            preferred = previous
            if preferred not in result['pages'] and self._candidate is not None:
                preferred = resource_frames(self._candidate['placements'][self.first.currentData()])[0]['page_id']
            index = self.pages.findData(preferred)
            self.pages.setCurrentIndex(max(0, index))
        self.pages.blockSignals(False)
        self.show_page()

    def show_page(self, *_):
        self.scene.clear()
        result = self._layouts.get(self.audience.currentData())
        identity = self.pages.currentData()
        if result is not None and identity in result['pages']:
            self.scene.addPixmap(QPixmap.fromImage(page_image(result, identity)))
            self.scene.setSceneRect(self.scene.itemsBoundingRect())
            self.fit_view()
        self.previous.setEnabled(self.pages.currentIndex() > 0)
        self.next.setEnabled(0 <= self.pages.currentIndex() < self.pages.count()-1)

    def fit_view(self):
        if self.scene.items():
            self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def focus_preview(self, focused):
        self.controls_scroll.setVisible(not focused)
        self.focus.setText('返回选材' if focused else '放大预览')
        QTimer.singleShot(0, self.fit_view)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'view'):
            QTimer.singleShot(0, self.fit_view)

    def apply(self):
        self.update_preview()
        if self._candidate is not None:
            self.result_value = deepcopy(self._candidate)
            self.accept()

"""A cancellable, block-bound image display and local pagination work copy."""
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from ..desktop_composition_image_display import (arrange_image_resource,
    image_display_rows, set_image_display)
from ..desktop_composition_render import layout_composition, page_image
from ..desktop_lesson_composition import (TEACHER_ROLES, resource_frames,
    validate_composition)
from ..desktop_preparation_images import verify_image_bytes
from .components import page_scroll


_ROLES = {'question': '题面', 'shared_material': '公共条件', 'answer': '教师答案',
    'analysis': '教师解析', 'rubric': '教师评分说明', 'teacher_note': '教师备注',
    'material': '教材素材', 'knowledge': '知识内容', 'personal': '个人图片',
    'student_task': '学生任务'}
_EMPTY_STUDENT = '学生版没有可见内容，请加入学生任务或材料后再生成。'


def _label(text):
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    return value


def _combo():
    value = QComboBox()
    value.setMinimumWidth(0)
    value.setMinimumContentsLength(12)
    value.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return value


class ImageDisplayDialog(QDialog):
    def __init__(self, composition, resource_id, assets, image_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle('图片显示与分页')
        self.resize(1120, 850)
        self.setMinimumSize(400, 540)
        self.original = validate_composition(composition)
        self.resource_id = resource_id
        self.item = next((r for r in self.original['resources'] if r['id'] == resource_id), None)
        if self.item is None:
            raise ValueError('请选择作品中的素材。')
        self.rows = image_display_rows(self.item)
        if not self.rows:
            raise ValueError('这项素材没有可设置的原图。')
        self.assets = deepcopy(assets)
        self.image_data = dict(image_data)
        self._limits = {row['image_block_id']: deepcopy(row['limits']) for row in self.rows if row['limits']}
        self.result_value = None
        self._candidate = None
        self._layouts = {}
        self._loading = True
        self._current = None
        self._thumbnail = QPixmap()
        root = QVBoxLayout(self)
        root.addWidget(_label('按原图区块设置显示宽、高上限，图片等比缩小。原图、正文和答案范围保留；先查看这项素材的完整分页，再应用到本课。'))
        controls = QWidget()
        form = QFormLayout(controls)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow('当前素材', _label(self.item['title']))
        self.images = _combo()
        self.images.setAccessibleName('按原图区块选择图片显示设置')
        for index, row in enumerate(self.rows):
            location = f"原文区块 {row['source_index']}" if row['source_index'] is not None else '作品图片区块'
            self.images.addItem(f"第 {index+1} 张 · {_ROLES[row['role']]} · {location}", row['image_block_id'])
            self.images.setItemData(index, row['image_block_id'] + '\n' + row['asset_id'], Qt.ItemDataRole.ToolTipRole)
        form.addRow('设置哪张图', self.images)
        self.thumbnail = QLabel()
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setMinimumSize(0, 150)
        self.thumbnail.setMaximumHeight(180)
        self.thumbnail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.thumbnail.setAccessibleName('当前原图缩略预览')
        form.addRow(self.thumbnail)
        self.binding = QPlainTextEdit()
        self.binding.setReadOnly(True)
        self.binding.setMaximumHeight(78)
        self.binding.setMinimumWidth(0)
        self.binding.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.binding.setAccessibleName('当前原图区块与图片标识')
        form.addRow(self.binding)
        self.max_width = self._spin('图片显示宽度上限毫米')
        self.max_height = self._spin('图片显示高度上限毫米')
        form.addRow('宽度上限 mm', self.max_width)
        form.addRow('高度上限 mm', self.max_height)
        form.addRow(_label('0 为自动；只填写宽度或高度即可。上限保留原图比例，实际大小还受页面与原图尺寸约束。'))
        self.reset = QPushButton('恢复这张图的自动大小')
        form.addRow(self.reset)
        self.rearrange = QCheckBox('重新排这项素材')
        self.rearrange.setChecked(True)
        form.addRow(self.rearrange)
        form.addRow(_label('勾选后，这项素材排成独立页面并完整续页，其他素材的位置保持。取消勾选则保留现有外框和分页。'))
        self.controls_scroll = page_scroll(controls)
        self.controls_scroll.setMinimumHeight(160)
        self.controls_scroll.setMaximumHeight(380)
        root.addWidget(self.controls_scroll)
        toolbar = QHBoxLayout()
        self.audience = _combo()
        self.audience.addItem('教师完整页', 'teacher')
        self.audience.addItem('课堂PPT完成态', 'classroom')
        self.audience.addItem('静态学生页', 'student')
        self.audience.setAccessibleName('图片素材完整预览范围')
        toolbar.addWidget(self.audience, 1)
        self.focus = QPushButton('放大预览')
        self.focus.setCheckable(True)
        toolbar.addWidget(self.focus)
        root.addLayout(toolbar)
        navigation = QHBoxLayout()
        self.previous = QPushButton('上一页')
        self.pages = _combo()
        self.pages.setAccessibleName('完整所选素材预览页码')
        self.next = QPushButton('下一页')
        navigation.addWidget(self.previous)
        navigation.addWidget(self.pages, 1)
        navigation.addWidget(self.next)
        root.addLayout(navigation)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setBackgroundBrush(QColor('#e8eeeb'))
        self.view.setMinimumHeight(130)
        self.view.setAccessibleName('完整所选素材实际排版预览')
        root.addWidget(self.view, 1)
        self.status = _label('')
        self.status_scroll = page_scroll(self.status)
        self.status_scroll.setMinimumHeight(30)
        self.status_scroll.setMaximumHeight(84)
        root.addWidget(self.status_scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.apply_button.setText('应用图片显示与分页')
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setText('取消')
        root.addWidget(buttons)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.update_preview)
        self.images.currentIndexChanged.connect(self.select_image)
        self.max_width.valueChanged.connect(self.limits_changed)
        self.max_height.valueChanged.connect(self.limits_changed)
        self.reset.clicked.connect(self.reset_current)
        self.rearrange.toggled.connect(self.schedule_preview)
        self.audience.currentIndexChanged.connect(self.show_layout)
        self.pages.currentIndexChanged.connect(self.show_page)
        self.previous.clicked.connect(lambda: self.pages.setCurrentIndex(self.pages.currentIndex()-1))
        self.next.clicked.connect(lambda: self.pages.setCurrentIndex(self.pages.currentIndex()+1))
        self.focus.toggled.connect(self.focus_preview)
        self.select_image()
        self._loading = False
        self.update_preview()

    @staticmethod
    def _spin(name):
        spin = QDoubleSpinBox()
        spin.setRange(0, 400)
        spin.setDecimals(2)
        spin.setSpecialValueText('自动')
        spin.setAccessibleName(name)
        return spin

    def store_current(self):
        if self._current is None:
            return
        values = {key: spin.value() for key, spin in (
            ('max_width_mm', self.max_width), ('max_height_mm', self.max_height)) if spin.value() > 0}
        if values:
            self._limits[self._current] = values
        else:
            self._limits.pop(self._current, None)

    def select_image(self, *_):
        self.store_current()
        self._loading = True
        self._current = self.images.currentData()
        row = next(r for r in self.rows if r['image_block_id'] == self._current)
        limits = self._limits.get(self._current, {})
        self.max_width.setValue(limits.get('max_width_mm', 0))
        self.max_height.setValue(limits.get('max_height_mm', 0))
        location = f"原文区块 {row['source_index']}" if row['source_index'] is not None else '作品图片区块'
        self.binding.setPlainText(f"{_ROLES[row['role']]} · {location}\n图片区块：{row['image_block_id']}\n原图：{row['asset_id']}")
        assets = {a['asset_id']: a for a in self.assets}
        self._thumbnail = QPixmap()
        try:
            data = verify_image_bytes(assets[row['asset_id']], self.image_data[row['asset_id']])
            if not self._thumbnail.loadFromData(data):
                raise ValueError('原图暂时无法显示。')
            self.thumbnail.setPixmap(self._thumbnail.scaled(340, 170, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        except (ValueError, KeyError, OSError):
            self.thumbnail.clear()
            self.thumbnail.setText('原图暂时无法显示。')
        self._loading = False
        self.show_thumbnail()
        self.schedule_preview()

    def limits_changed(self, *_):
        if not self._loading:
            self.store_current()
            self.schedule_preview()

    def reset_current(self):
        self.max_width.setValue(0)
        self.max_height.setValue(0)
        self.store_current()
        self.schedule_preview()

    def schedule_preview(self, *_):
        if not self._loading:
            self.apply_button.setEnabled(False)
            self._preview_timer.start(180)

    def preview_composition(self, value):
        """Measure every block of this resource, preserving its audience policy."""
        selected = deepcopy(value)
        selected['resources'] = [deepcopy(next(r for r in value['resources'] if r['id'] == self.resource_id))]
        selected['placements'] = {self.resource_id: deepcopy(value['placements'][self.resource_id])}
        used = {frame['page_id'] for frame in resource_frames(selected['placements'][self.resource_id])}
        selected['pages'] = [page for page in value['pages'] if page in used]
        if 'click_reveals' in selected:
            selected['click_reveals'] = [identity for identity in selected['click_reveals'] if identity == self.resource_id]
        return validate_composition(selected)

    def update_preview(self):
        self._preview_timer.stop()
        self.store_current()
        self._candidate = None
        self._layouts = {}
        try:
            value = set_image_display(self.original, self.resource_id, self._limits)
            if self.rearrange.isChecked():
                value = arrange_image_resource(value, self.resource_id, self.assets, self.image_data)
            selected = self.preview_composition(value)
            for key, student, reveals in (('teacher', False, False), ('classroom', True, True), ('student', True, False)):
                rendered = layout_composition(selected, self.assets, self.image_data,
                    student=student, include_reveals=reveals)
                if student and rendered['errors'] == [_EMPTY_STUDENT] and not rendered['pages']:
                    rendered['errors'] = []
                if rendered['errors']:
                    raise ValueError('\n'.join(rendered['errors']))
                self._layouts[key] = rendered
            self._candidate = value
            self.apply_button.setEnabled(True)
            self.status.setText(f"完整所选素材：教师 {len(self._layouts['teacher']['pages'])} 页 · 课堂PPT {len(self._layouts['classroom']['pages'])} 页 · 静态学生 {len(self._layouts['student']['pages'])} 页。")
        except (ValueError, KeyError, OSError) as exc:
            self.apply_button.setEnabled(False)
            self._layouts = {}
            self.status.setText(str(exc) + ('\n请勾选“重新排这项素材”，或返回画布使用“教师一栏／两栏排满并续页”。'
                if not self.rearrange.isChecked() else ''))
        self.show_layout()

    def show_thumbnail(self):
        row = next((r for r in self.rows if r['image_block_id'] == self._current), None)
        self.thumbnail.setVisible(row is not None and
            (self.audience.currentData() == 'teacher' or row['role'] not in TEACHER_ROLES))

    def show_layout(self, *_):
        self.show_thumbnail()
        previous = self.pages.currentData()
        self.pages.blockSignals(True)
        self.pages.clear()
        result = self._layouts.get(self.audience.currentData())
        if result is not None:
            for index, identity in enumerate(result['pages']):
                self.pages.addItem(f'第 {index+1} / {len(result["pages"])} 页', identity)
            self.pages.setCurrentIndex(max(0, self.pages.findData(previous)))
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
        elif result is not None:
            self.scene.addText('这项素材在当前范围中不显示。')
            self.scene.setSceneRect(self.scene.itemsBoundingRect())
            self.fit_view()
        self.previous.setEnabled(self.pages.currentIndex() > 0)
        self.next.setEnabled(0 <= self.pages.currentIndex() < self.pages.count()-1)

    def fit_view(self):
        if self.scene.items():
            self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def focus_preview(self, focused):
        self.controls_scroll.setVisible(not focused)
        self.focus.setText('返回设置' if focused else '放大预览')
        QTimer.singleShot(0, self.fit_view)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'view'):
            QTimer.singleShot(0, self.fit_view)

    def done(self, result):
        self._preview_timer.stop()
        super().done(result)

    def apply(self):
        self.update_preview()
        if self._candidate is not None:
            self.result_value = deepcopy(self._candidate)
            self.accept()

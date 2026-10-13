"""Read-only content view of the current, teacher-owned lesson node."""
from __future__ import annotations

from collections import OrderedDict
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from ..desktop_lesson_design import validate_design
from ..desktop_preparation_images import normalize_image_assets, verify_image_bytes


def node_content(payload, identity, student=True):
    """Use the same visibility rules as the existing DOCX output, without truncation."""
    plan = validate_design(payload['lesson_design'])
    node = next((n for n in plan['nodes'] if n['id'] == identity), None)
    if node is None:
        return None
    assets = {a['asset_id']: a for a in normalize_image_assets(payload.get('image_assets', []))}
    blocks = []
    if not student:
        goals = [g['text'] for g in plan['objectives'] if g['id'] in node['objective_ids']]
        timing = '尚未估时' if node['minutes'] is None else f"{node['minutes']}分钟"
        blocks.append({'text': '目标：' + '；'.join(goals) + '　用时：' + timing})
    if not student or node['student_material']:
        if node['material_text']:
            blocks.append({'text': node['material_text']})
        for key in node['image_ids']:
            blocks.append({'image': assets.get(key), 'image_id': key})
    for key, title in (('teacher_action', '教师活动'), ('student_task', '学生任务'),
                       ('expected_output', '预期产出'), ('criteria', '评价依据'),
                       ('teacher_answer', '教师答案'), ('notes', '教师备注')):
        if node[key] and (not student or key == 'student_task'):
            blocks.append({'text': title + '：' + node[key]})
    return {'title': node['title'], 'order': plan['nodes'].index(node) + 1,
            'blocks': blocks, 'student': student}


class _ContentBrowser(QTextBrowser):
    def __init__(self, parent):
        super().__init__(parent)
        self.images = {}
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    def loadResource(self, kind, url):
        # Only this widget's verified, local thumbnails may become resources.
        if kind == QTextDocument.ResourceType.ImageResource:
            return self.images.get(url.toString(), QImage())
        return None


class LessonNodePreview(QWidget):
    def __init__(self, facade, parent=None):
        super().__init__(parent)
        self.facade = facade
        self._payload, self._identity = None, None
        self._cache = OrderedDict()
        self._render_identity = None
        layout = QVBoxLayout(self)
        heading = QLabel('所选环节 · 内容预览')
        heading.setObjectName('CardTitle')
        layout.addWidget(heading)
        self.role = QComboBox()
        self.role.addItem('学生学习单', True)
        self.role.addItem('教师教案', False)
        self.role.setAccessibleName('环节预览的可见范围')
        layout.addWidget(self.role)
        self.browser = _ContentBrowser(self)
        self.browser.setAccessibleName('所选教学环节的完整内容预览')
        layout.addWidget(self.browser, 1)
        self.notice = QLabel('内容随编辑更新；实际分页、PPT投影及公式版式请核对生成文件。')
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.notice)
        self.reload = QPushButton('重新核对所选图片')
        self.reload.setObjectName('QuietButton')
        layout.addWidget(self.reload)
        self.role.currentIndexChanged.connect(self.render)
        self.reload.clicked.connect(self.reload_images)

    def set_payload(self, payload, identity):
        self._payload, self._identity = payload, identity
        self.render()

    def reload_images(self):
        self._cache.clear()
        self.render()

    def clear(self):
        self._payload, self._identity = None, None
        self._cache.clear()
        self.browser.images.clear()
        self.browser.clear()

    def _image(self, asset):
        key = asset['asset_id']
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        data = verify_image_bytes(asset, self.facade.preparation_image_bytes(asset))
        image = QImage.fromData(data)
        if image.isNull():
            raise ValueError('图片无法显示，请重新关联本地素材。')
        thumbnail = image.scaled(700, 900, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self._cache[key] = thumbnail
        while len(self._cache) > 8:
            self._cache.popitem(last=False)
        return thumbnail

    def render(self, *_):
        old_position = self.browser.verticalScrollBar().value()
        render_identity = (self._identity, self.role.currentData())
        keep_position = render_identity == self._render_identity
        self._render_identity = render_identity
        self.browser.images = {}
        parts = []
        errors = []
        if self._payload is None:
            parts.append('<p>选择一个教学环节后查看内容。</p>')
        else:
            try:
                content = node_content(self._payload, self._identity, bool(self.role.currentData()))
            except (ValueError, TypeError, KeyError) as exc:
                content = None
                errors.append('内容未能核对：' + str(exc))
            if content is None:
                parts.append('<p>选择一个教学环节后查看内容。</p>')
            else:
                parts.append('<h2>' + escape(f"{content['order']}. {content['title']}") + '</h2>')
                for block in content['blocks']:
                    if 'text' in block:
                        parts.append('<p>' + escape(block['text']).replace('\n', '<br>') + '</p>')
                        continue
                    asset = block['image']
                    if asset is None:
                        errors.append('本环节引用的图片已移除，请重新关联。')
                        parts.append('<p>图片缺失</p>')
                        continue
                    try:
                        image = self._image(asset)
                    except (ValueError, OSError, KeyError) as exc:
                        errors.append('图片未能核对：' + str(exc))
                        parts.append('<p>图片未能显示</p>')
                    else:
                        url = 'lessonimage:' + asset['asset_id']
                        self.browser.images[url] = image
                        width = min(image.width(), max(100, self.browser.viewport().width() - 36))
                        parts.append(f'<p><img src="{url}" width="{width}" '
                                     f'height="{round(width * image.height() / image.width())}"></p>')
                    parts.append('<p>' + escape(asset['caption'] + '　来源：' + asset['source']) + '</p>')
                if not content['blocks']:
                    parts.append('<p>本环节尚无学生可见内容。可填写学生任务，或核对后勾选学生材料。</p>')
        # A new document drops old resources when switching teacher/student scope.
        document = QTextDocument(self.browser)
        self.browser.setDocument(document)
        document.setDefaultStyleSheet('body {color:#26352f;} p {line-height:140%;} h2 {color:#214b3b;}')
        self.browser.setHtml('<html><body>' + ''.join(parts) + '</body></html>')
        if keep_position:
            self.browser.verticalScrollBar().setValue(old_position)
        self.notice.setText('\n'.join(errors) if errors else
            '内容随编辑更新；实际分页、PPT投影及公式版式请核对生成文件。')
        self.reload.setEnabled(self._payload is not None)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'browser') and event.oldSize().width() != event.size().width():
            self.render()

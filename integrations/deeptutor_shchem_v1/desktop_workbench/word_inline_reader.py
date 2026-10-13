"""Selectable local paragraph flow, with source images kept at their positions."""
from copy import deepcopy
from hashlib import sha256
from html import escape
import math

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QFont, QImage, QTextDocument
from PySide6.QtWidgets import QFrame, QSizePolicy, QTextBrowser


class WordInlineReader(QTextBrowser):
    def __init__(self, reading, parent=None):
        super().__init__(parent)
        self.reading = deepcopy(reading)
        self.setObjectName('WordInlineReader')
        self.setAccessibleName('按原文顺序显示的题面' if reading['scope'] == 'question' else '按原文顺序显示的答案与解析')
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setStyleSheet('QTextBrowser { background: white; color: #263b36; border: 0; padding: 0; }')
        font = QFont(self.font()); font.setPixelSize(18)
        self.document().setDefaultFont(font)
        self.document().setDocumentMargin(0)
        self._images = {}
        for asset_id, asset in reading['images'].items():
            raw = asset['bytes']
            if not isinstance(raw, bytes) or sha256(raw).hexdigest() != asset['display']['sha256']:
                raise ValueError('原图内容与来源位置不一致。')
            image = QImage.fromData(raw)
            if image.isNull() or (image.width(), image.height()) != (asset['display']['width'], asset['display']['height']):
                raise ValueError('原图无法完整显示。')
            self._images[asset_id] = image
        self._render_width = None
        QTimer.singleShot(0, self._reflow)

    def loadResource(self, _kind, _url):
        # HTML is generated from escaped tokens only, with explicit in-memory
        # pictures. No relative file, remote URL or arbitrary source HTML loads.
        return None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self):
        width = max(1, self.viewport().width())
        if width == self._render_width:
            return
        self._render_width = width
        paragraphs = []
        for paragraph in self.reading['paragraphs']:
            runs = []
            for token in paragraph['tokens']:
                if token['kind'] == 'text':
                    value = escape(token['text']).replace('\n', '<br/>')
                    tag = {'subscript': 'sub', 'superscript': 'sup'}.get(token.get('script'))
                    runs.append(f'<{tag}>{value}</{tag}>' if tag else value)
                elif token['kind'] == 'image':
                    image = self._images[token['asset_id']]
                    natural = token['width_mm'] * (96 / 25.4) * (18 / 16) if isinstance(token['width_mm'], (int, float)) else image.width()
                    picture_width = min(max(1, natural), max(1, width - 24))
                    picture_height = picture_width * image.height() / image.width()
                    url = QUrl('word-source-image:' + token['asset_id'])
                    self.document().addResource(QTextDocument.ResourceType.ImageResource, url, image)
                    runs.append(f'<img src="{escape(url.toString(), quote=True)}" width="{picture_width:.2f}" height="{picture_height:.2f}"/>')
                elif token['kind']=='eq':
                    from ..word_static_eq import layout_eq
                    from ..desktop_preparation_images import image_info
                    assets={identity:{'asset_id':identity,'sha256':sha256(asset['bytes']).hexdigest(),
                        **image_info(asset['bytes']),'caption':'原图','source':'本地Word','purpose':'原生条件式'}
                        for identity,asset in self.reading['images'].items()}
                    data={identity:asset['bytes'] for identity,asset in self.reading['images'].items()}
                    picture=layout_eq(token,assets,data,font_px=18)
                    if picture['width']*96/25.4>width-24:
                        raise ValueError('完整条件式超出阅读区，请扩大窗口。')
                    url=QUrl('word-source-eq:'+token['field']['field_sha256'])
                    self.document().addResource(QTextDocument.ResourceType.ImageResource,url,picture['_image'])
                    runs.append(f'<img src="{url.toString()}" width="{picture["width"]*96/25.4:.2f}" height="{picture["height"]*96/25.4:.2f}"/>')
                else:
                    raise ValueError('原文行内内容不完整。')
            paragraphs.append('<p style="margin-top:0; margin-bottom:12px;">' + ''.join(runs) + '</p>')
        self.setHtml(''.join(paragraphs))
        self.document().setTextWidth(width)
        self.setFixedHeight(max(30, math.ceil(self.document().size().height()) + 4))

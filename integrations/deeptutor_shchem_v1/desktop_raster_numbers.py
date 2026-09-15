"""Teacher-selected number regions on generated DOCX image copies.

No OCR, network, original-file writes, or source-identity changes. Coordinates
are integer pixels in the decoded source image, with an exclusive right/bottom.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import re
import posixpath
from functools import lru_cache
import xml.etree.ElementTree as ET
from lxml import etree as LET
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


class RasterNumberError(ValueError):
    pass


_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
_MEDIA = re.compile(r'^word/media/[^/]+$')
_DIGEST = re.compile(r'^[0-9a-f]{64}$')
MAX_IMAGE_PIXELS = 25_000_000


def _image(data: bytes):
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {'PNG', 'JPEG', 'BMP'} or getattr(source, 'n_frames', 1) != 1:
                raise RasterNumberError('本次只支持静态 PNG、JPEG 或 BMP 题图。')
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise RasterNumberError('题图过大，请先在原资料中检查图片尺寸。')
            if source.getexif().get(274, 1) != 1:
                raise RasterNumberError('带旋转标记的图片请先核对方向，当前不自动调整。')
            source.load()
            return source.convert('RGBA')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise RasterNumberError('无法读取这张题图，请检查原资料。') from exc


def _package(data):
    try:
        package = ZipFile(BytesIO(data))
        names = package.namelist()
        if len(names) != len(set(names)) or sum(i.file_size for i in package.infolist()) > 256 * 1024 * 1024:
            raise RasterNumberError('文档图片包重复或过大，无法调整题号。')
        if '[Content_Types].xml' not in names or 'word/document.xml' not in names:
            raise RasterNumberError('不是可读取的试卷 DOCX。')
        return package
    except BadZipFile as exc:
        raise RasterNumberError('试卷 DOCX 损坏，请重新生成。') from exc


def _contexts(package):
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    a = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    rels = ET.fromstring(package.read('word/_rels/document.xml.rels'))
    targets = {n.get('Id'): posixpath.normpath(posixpath.join('word', n.get('Target', ''))).lstrip('/')
               for n in rels if n.get('TargetMode') != 'External'}
    result, preceding = {}, ''
    body = ET.fromstring(package.read('word/document.xml')).find('{'+w+'}body')
    for block in body:
        text = ''.join(n.text or '' for n in block.iter('{'+w+'}t')).strip()
        if text:
            preceding = text[:120]
        for blip in block.iter('{'+a+'}blip'):
            target = targets.get(blip.get('{'+r+'}embed'))
            if target and preceding:
                result.setdefault(target, [])
                if preceding not in result[target]: result[target].append(preceding)
    return result


def image_catalog(documents: dict[str, bytes]) -> dict:
    """Return each identical image once, with explicit audience/occurrence scope."""
    records, unsupported = {}, 0
    for audience in ('student', 'teacher'):
        with _package(documents[audience + '_bytes']) as package:
            contexts = _contexts(package)
            for name in package.namelist():
                if not _MEDIA.fullmatch(name):
                    continue
                data = package.read(name)
                key = sha256(data).hexdigest()
                if key not in records:
                    try:
                        image = _image(data)
                    except RasterNumberError:
                        unsupported += 1
                        continue
                    records[key] = {'image_sha256': key, 'data': data,
                                    'width': image.width, 'height': image.height,
                                    'audiences': [], 'parts': [], 'contexts': []}
                record = records[key]
                if audience not in record['audiences']:
                    record['audiences'].append(audience)
                for text in contexts.get(name, []):
                    location = ('学生版' if audience == 'student' else '教师版') + ' · ' + text
                    if location not in record['contexts']: record['contexts'].append(location)
                record['parts'].append({'audience': audience, 'part': name})
    return {'images': list(records.values()), 'unsupported_parts': unsupported}


def validate_edits(edits, images):
    if not isinstance(edits, list) or len(edits) > 200:
        raise RasterNumberError('每次最多调整200个题号区域。')
    by_hash = {row['image_sha256']: row for row in images}
    checked = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'image_sha256', 'box', 'number', 'punctuation'}:
            raise RasterNumberError('题号区域记录不完整，请重新框选。')
        key, box = edit['image_sha256'], edit['box']
        if not isinstance(key, str) or not _DIGEST.fullmatch(key) or key not in by_hash:
            raise RasterNumberError('本次试卷没有这张原图，请按当前题序重新框选。')
        row = by_hash[key]
        if (not isinstance(box, list) or len(box) != 4 or any(type(x) is not int for x in box)
                or not (0 <= box[0] < box[2] <= row['width'] and 0 <= box[1] < box[3] <= row['height'])
                or box[2] - box[0] < 4 or box[3] - box[1] < 6):
            raise RasterNumberError('请在原图范围内框选完整题号，区域不能过小。')
        if type(edit['number']) is not int or not 1 <= edit['number'] <= 999 or edit['punctuation'] not in ('', '.'):
            raise RasterNumberError('本卷题号须为1至999的整数。')
        for previous in checked:
            a = previous['box']
            if previous['image_sha256'] == key and min(a[2], box[2]) > max(a[0], box[0]) and min(a[3], box[3]) > max(a[1], box[1]):
                raise RasterNumberError('题号区域重叠，请先移除旧框再重新选择。')
        checked.append(deepcopy(edit))
    return checked


@lru_cache(maxsize=128)
def _number_font(size):
    # Only numeric glyphs are painted. Fonts are used locally, never copied.
    for family in ('arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(family, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def render_numbered_image(data: bytes, edits: list[dict]) -> bytes:
    original = _image(data)
    key = sha256(data).hexdigest()
    checked = validate_edits(edits, [{'image_sha256': key, 'width': original.width, 'height': original.height}])
    result = original.copy()
    for edit in checked:
        left, top, right, bottom = edit['box']
        patch = Image.new('RGBA', (right - left, bottom - top), (255, 255, 255, 255))
        draw = ImageDraw.Draw(patch)
        text = str(edit['number']) + edit['punctuation']
        # Fit into the selected rectangle; never paint past it into chemistry.
        for size in range(min(patch.height, 512), 0, -1):
            font = _number_font(size)
            bounds = draw.textbbox((0, 0), text, font=font)
            width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
            if width <= max(1, patch.width - 2) and height <= max(1, patch.height - 2):
                break
        x = 1 - bounds[0]
        y = max(0, (patch.height - height) // 2) - bounds[1]
        draw.text((x, y), text, font=font, fill=(0, 0, 0, 255))
        result.paste(patch, (left, top))
    output = BytesIO()
    result.save(output, format='PNG')
    return output.getvalue()


def apply_to_documents(documents: dict, edits: list[dict]) -> dict:
    """Patch raster media only. XML text, formula and drawing extents stay intact."""
    catalog = image_catalog(documents)
    checked = validate_edits(edits, catalog['images'])
    if not checked:
        return dict(documents)
    converted = {}
    for image in catalog['images']:
        regions = [e for e in checked if e['image_sha256'] == image['image_sha256']]
        if regions:
            converted[image['image_sha256']] = render_numbered_image(image['data'], regions)
    outputs = dict(documents)
    from .desktop_paper_numbering import RASTER_NOTICE
    reviewed_notice = "已按教师框选调整部分图片题号；未框选的旧号与图内引用仍需对照本卷核对。"
    for audience in ('student', 'teacher'):
        with _package(documents[audience + '_bytes']) as package:
            replacements = {name: converted[sha256(package.read(name)).hexdigest()]
                            for name in package.namelist() if _MEDIA.fullmatch(name)
                            and sha256(package.read(name)).hexdigest() in converted}
            if not replacements:
                continue
            types = LET.fromstring(package.read('[Content_Types].xml'))
            for name in replacements:
                part = '/' + name
                node = next((n for n in types if n.tag == '{'+_CT+'}Override' and n.get('PartName') == part), None)
                if node is None:
                    node = LET.SubElement(types, '{'+_CT+'}Override', {'PartName': part})
                node.set('ContentType', 'image/png')
            output = BytesIO()
            with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
                for info in package.infolist():
                    data = (LET.tostring(types, encoding='UTF-8', xml_declaration=True, standalone=True) if info.filename == '[Content_Types].xml'
                            else replacements.get(info.filename))
                    data = package.read(info.filename) if data is None else data
                    if info.filename == 'word/document.xml':
                        data = data.replace(RASTER_NOTICE.encode('utf-8'), reviewed_notice.encode('utf-8'))
                    archive.writestr(info, data)
            outputs[audience + '_bytes'] = output.getvalue()
    outputs['warnings'] = [w for w in documents.get('warnings', []) if w != RASTER_NOTICE] + [
        f'本次已按教师框选替换{len(checked)}个图片题号区域；未框选的旧号及图内引用仍须逐项核对。'
    ]
    return outputs

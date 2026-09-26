"""Reusable pixel locations, never the question number from an earlier paper.

Stores only image digests, dimensions, boxes and punctuation in existing personal
state. Opening the editor is read-only; remembering/forgetting is explicit.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from collections.abc import Mapping

from .desktop_raster_numbers import RasterNumberError, validate_edits

_KEY = 'scan_number_regions'
_SCHEMA = 'shchem.number-regions.v1'


class NumberRegionStore:
    def __init__(self, state):
        self.state = state

    @staticmethod
    def _library(value):
        library = value.get(_KEY, {'schema_version': _SCHEMA, 'images': {}})
        if (not isinstance(library, dict) or library.get('schema_version') != _SCHEMA
                or not isinstance(library.get('images'), dict)):
            raise RasterNumberError('已存题号位置暂时无法读取；仍可手工框选，原记录未改写。')
        return library

    def load(self, image):
        row = self._library(self.state.snapshot())['images'].get(image['image_sha256'])
        if row is None:
            return []
        if (not isinstance(row, dict) or row.get('width') != image['width']
                or row.get('height') != image['height'] or not isinstance(row.get('regions'), list)):
            raise RasterNumberError('此图的已存位置与尺寸不一致，请重新框选并记住位置。')
        regions = row['regions']
        if any(not isinstance(r, dict) or set(r) != {'box', 'punctuation'} for r in regions):
            raise RasterNumberError('此图的已存位置不完整，请重新框选并记住位置。')
        # Existing box validation enforces bounds and non-overlap. Number 1 is
        # used solely to validate geometry, and is never stored or returned.
        validate_edits([dict(r, image_sha256=image['image_sha256'], number=1)
                        for r in regions], [image])
        return deepcopy(regions)

    def remember(self, image, edits):
        checked = validate_edits(edits, [image])
        if not checked:
            raise RasterNumberError('请先为此图添加至少一个替换区域，再记住框选位置。')
        row = {'width': image['width'], 'height': image['height'],
               'regions': [{'box': r['box'], 'punctuation': r['punctuation']} for r in checked]}
        def update(value):
            library = deepcopy(self._library(value))
            library['images'][image['image_sha256']] = row
            value[_KEY] = library
        self.state._update(update)

    def forget(self, image):
        def update(value):
            library = deepcopy(self._library(value))
            library['images'].pop(image['image_sha256'], None)
            value[_KEY] = library
        self.state._update(update)



def validated_region_library(value):
    """Validate the complete optional backup field without trusting extra keys."""
    import re
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'images'}
            or value.get('schema_version') != _SCHEMA or not isinstance(value.get('images'), dict)):
        raise RasterNumberError('备份中的题号位置格式不正确。')
    for digest, row in value['images'].items():
        if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or not isinstance(row, dict) or set(row) != {'width', 'height', 'regions'}
                or type(row['width']) is not int or type(row['height']) is not int
                or not 1 <= row['width'] <= 50000 or not 1 <= row['height'] <= 50000
                or not isinstance(row['regions'], list)
                or any(not isinstance(r, dict) or set(r) != {'box', 'punctuation'} for r in row['regions'])):
            raise RasterNumberError('题号位置记录含无效尺寸、身份或多余字段。')
        validate_edits([dict(r, image_sha256=digest, number=1) for r in row['regions']],
                       [{'image_sha256': digest, 'width': row['width'], 'height': row['height']}])
    return deepcopy(value)


def region_totals(value):
    library = validated_region_library(value)
    return {'number_region_images': len(library['images']),
            'number_regions': sum(len(r['regions']) for r in library['images'].values())}


def record_image_numbers(document, blocks, number, hints):
    """Record the known owning printed question of generated body images.

    Only called by the composer, using its numbering plan, never inferred from
    a filename, text snippet, or model output. None marks shared/multiple or
    otherwise unresolved printed-question ownership. A shared occurrence vetoes
    automatic suggestions even when the same image also appears in one question.
    """
    if hints is None:
        return
    a = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    v = 'urn:schemas-microsoft-com:vml'
    for block in blocks:
        for node in block.iter():
            if node.tag == '{'+a+'}blip':
                rid = node.get('{'+r+'}embed')
            elif node.tag == '{'+v+'}imagedata':
                rid = node.get('{'+r+'}id')
            else:
                continue
            rel = document.part.rels.get(rid)
            if rel is None or rel.is_external:
                continue
            key = sha256(rel.target_part.blob).hexdigest()
            values = hints.setdefault(key, [])
            if number not in values:
                values.append(number)


def suggest_number(image, hints):
    values = hints.get(image['image_sha256'], []) if isinstance(hints, Mapping) else []
    if (len(values) == 1 and type(values[0]) is int and 1 <= values[0] <= 999):
        return values[0]
    return None

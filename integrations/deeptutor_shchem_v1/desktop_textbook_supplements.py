"""Read the declared supplement registry without changing numbered sections."""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from . import desktop_textbook_asset_catalog as catalog
from . import desktop_textbook_assets as metadata
from .desktop_preparation_sources import CONCEPTS
from .desktop_textbook_section_reader import _page_range

PRODUCT_PATH = Path(CONCEPTS).with_name('textbook_directory.json')
MAX_NODES = 1024


class TextbookSupplementError(ValueError):
    def __init__(self, message):
        self.message_zh = message
        super().__init__(message)


def _closed(value):
    return (isinstance(value, dict) and value.get('candidate_only') is True
            and all(value.get(key) is False for key in metadata._CLOSED_FLAGS))


def _node(row, registry, parents, registry_revision, product_revision):
    if not _closed(row) or row.get('counted_in_60') is not False:
        raise ValueError('invalid supplement boundary')
    key = metadata._text(row['node_key'])
    if key in {'@unmapped', '@supplement'} or key in {node['node_key'] for node in registry['nodes']}:
        raise ValueError('numbered section registered as supplement')
    volume = next((v for v in registry['volumes'] if v['volume_id'] == row['volume_id']), None)
    if volume is None or any(row.get(k) != volume[k] for k in ('source_path', 'source_sha256')):
        raise ValueError('source identity mismatch')
    catalog._source_relative(row['source_path'])
    chapter = row.get('chapter_id')
    if chapter is not None:
        metadata._text(chapter)
        if parents.get(chapter) != volume['volume_id'] or row.get('parent_node_id') != chapter:
            raise ValueError('chapter parent mismatch')
    elif row.get('parent_node_id') != volume['volume_id']:
        raise ValueError('volume parent mismatch')
    pages = row['pdf_page_list']
    metadata._pages(pages)
    if not isinstance(pages, list) or pages != sorted(pages):
        raise ValueError('invalid explicit page list')
    if row.get('pdf_pages') != [pages[0], pages[-1]]:
        raise ValueError('PDF endpoints disagree with explicit list')
    # This product explicitly stores endpoint pairs plus an enumerated PDF
    # list. Keep absent printed ranges unknown; never guess from PDF offsets.
    printed = _page_range(row['printed_pages']) if row.get('printed_pages') else []
    if printed and len(printed) != len(pages):
        raise ValueError('printed range mismatch')
    chapter_nodes = [node for node in registry['nodes'] if node['chapter_id'] == chapter]
    revision = catalog._canonical_sha256({'registry': registry_revision,
                                          'product': product_revision, 'node': row})
    return {'supplement_node_key': key, 'title': metadata._text(row['title'], 500),
            'node_type': metadata._text(row['node_type'], 80),
            'volume_id': volume['volume_id'], 'volume_title': volume['volume_title'],
            'chapter_id': chapter,
            'chapter_title': chapter_nodes[0]['chapter_title'] if chapter_nodes else None,
            'source_name': Path(row['source_path']).name,
            'source_path': row['source_path'], 'source_sha256': row['source_sha256'],
            'pdf_pages': list(pages), 'printed_pages': printed,
            'revision': revision, 'registry_sha256': registry_revision,
            'product_sha256': product_revision, 'counted_in_60': False,
            'candidate_only': True, **{key: False for key in metadata._CLOSED_FLAGS}}


def read_supplements(workspace, registry, registry_revision, parents):
    """Optional for old installations; invalid supplements never change sections."""
    try:
        try:
            path, before = catalog._checked_file(workspace, PRODUCT_PATH,
                                                catalog.MAX_DIRECTORY_BYTES, '教材补充栏目目录')
        except FileNotFoundError:
            return [], None, []
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError('registry replaced')
            raw = stream.read(catalog.MAX_DIRECTORY_BYTES + 1)
        _, after = catalog._checked_file(workspace, PRODUCT_PATH,
                                         catalog.MAX_DIRECTORY_BYTES, '教材补充栏目目录')
        if (len(raw) > catalog.MAX_DIRECTORY_BYTES
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError('registry changed')
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=metadata._unique_object,
                           parse_constant=metadata._invalid_constant, parse_float=metadata._finite_float)
        rows = value.get('supplement_nodes') if isinstance(value, dict) else None
        if (not _closed(value) or value.get('source_registry_sha256') != registry_revision
                or not isinstance(rows, list) or len(rows) > MAX_NODES
                or any(type(value.get(k)) is not int for k in ('volume_count', 'chapter_count', 'section_count'))
                or [value.get(k) for k in ('volume_count', 'chapter_count', 'section_count')]
                != [len(registry['volumes']), len(parents), len(registry['nodes'])]):
            raise ValueError('registry contract mismatch')
        revision = hashlib.sha256(raw).hexdigest()
        keys = Counter(row.get('node_key') for row in rows
                       if isinstance(row, dict) and isinstance(row.get('node_key'), str))
        accepted, rejected = [], 0
        for row in rows:
            try:
                if not isinstance(row, dict) or keys.get(row.get('node_key')) != 1:
                    raise ValueError('conflicting identity')
                accepted.append(_node(row, registry, parents, registry_revision, revision))
            except (ValueError, TypeError, KeyError):
                rejected += 1
        notices = [f'有 {rejected} 个补充栏目暂无法核对归属或页码，未作为阅读入口。'] if rejected else []
        return accepted, revision, notices
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise TextbookSupplementError('补充栏目目录暂未通过来源或版本核对；正式章节仍可浏览。') from exc


def binding(row, nodes, *, asset=False):
    """Literal declared identities only; do not rewrite a concept or asset."""
    node = nodes.get(row.get('supplement_node_key'))
    pages = [row['pdf_page']] if asset else row['pdf_pages']
    if (node is not None and row.get('section_key') is None
            and all(row.get(k) == node[k] for k in ('volume_id', 'chapter_id'))
            and (not asset or row['source_sha256'] == node['source_sha256'])
            and set(pages).issubset(node['pdf_pages'])):
        return node['supplement_node_key']
    return None

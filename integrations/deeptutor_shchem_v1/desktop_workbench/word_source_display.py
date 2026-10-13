"""Readable Word labels, independent of source text, revisions and evidence."""
from collections import Counter
import re


_IMAGE_MARKERS = (
    '【待查看原文：图片或图形】',
    '【待查看原文：图片或旧式图形】',
)
_IMAGE_MARKER = re.compile('|'.join(re.escape(value) for value in _IMAGE_MARKERS))
_ANY_MARKER = re.compile(r'【待查看原文：[^】]*】')


def located_images(preview, start, end):
    """Count selectable, unambiguous source assets, not XML occurrences."""
    if not isinstance(preview, dict):
        return []
    blocks, assets = preview.get('blocks', ()), preview.get('assets', ())
    if not isinstance(blocks, (list, tuple)) or not isinstance(assets, (list, tuple)):
        return []
    indices = {b.get('index') for b in blocks if isinstance(b, dict)
               and type(b.get('index')) is int}
    counts = Counter(a.get('asset_id') for a in assets if isinstance(a, dict)
                     and isinstance(a.get('asset_id'), str))
    return [a for a in assets if isinstance(a, dict)
            and isinstance(a.get('asset_id'), str) and a['asset_id']
            and counts[a['asset_id']] == 1
            and type(a.get('block_index')) is int and a['block_index'] in indices
            and start <= a['block_index'] <= end
            and isinstance(a.get('mime_type'), str) and a['mime_type'].startswith('image/')]


def _image_label_text(block, images):
    """Only remove known picture tokens with a matching source image binding.

    A literal token alongside a real picture adds an unmatched token, so the
    conservative count check retains it. Reused relationships can similarly
    retain tokens; this display layer never invents occurrence information.
    """
    text = block.get('text', '') if isinstance(block, dict) else ''
    if not isinstance(text, str):
        return None
    known = _IMAGE_MARKER.findall(text)
    if not known or len(known) != len(images) or _ANY_MARKER.findall(text) != known:
        return None
    return _IMAGE_MARKER.sub('', text)


def section_display_title(section, preview):
    title = section['title'].strip()
    start = section['start']
    blocks = preview.get('blocks', ()) if isinstance(preview, dict) else ()
    heading = next((b for b in blocks if isinstance(b, dict) and b.get('index') == start), {})
    images = located_images(preview, start, start)
    if _image_label_text(heading, images) is not None:
        title = ' '.join(_IMAGE_MARKER.sub('', title).split())
        if not title:
            return f'原图栏目 · 区块{start}'
    return title


def section_display_summary(section, preview):
    start, end = section['start'], section['end']
    count = len(located_images(preview, start, end))
    return f'原区块 {start}—{end} · 可查看{count}张原图'


def block_display_label(block, images):
    """Clean a derived label only; the native body stays byte-for-byte intact."""
    label = block.get('label') or f"区块 {block['index']}"
    if not isinstance(label, str):
        label = f"区块 {block['index']}"
    valid = [a for a in images if isinstance(a, dict)
             and a.get('block_index') == block['index']
             and isinstance(a.get('mime_type'), str) and a['mime_type'].startswith('image/')]
    cleaned = _image_label_text(block, valid)
    if cleaned is None:
        return label
    if not cleaned.strip():
        return f"区块 {block['index']} · 原图"
    return _IMAGE_MARKER.sub('', label)

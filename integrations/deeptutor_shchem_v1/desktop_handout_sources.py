"""Read-only navigation from either bound lecture evidence to its original Word.

A document binding has no selected picture occurrence. Picture rows are built
only from verified original preview assets; legacy picture bindings keep their
existing occurrence validator and never pass through the document route.
"""
from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
import json
from threading import RLock

from .desktop_handout_images import (SCHEMA as PICTURE_SCHEMA, _IMAGE_FIELDS,
    _context_labels, handout_image_context, validate_source)
from .desktop_handout_knowledge import (SOURCE_SCHEMA as DOCUMENT_SCHEMA,
    validate_handout_knowledge_source)
from .desktop_preparation_sources import PreparationSourcesService, _digest
from .reader_cancellation import check_read_cancelled
from .word_native_text import NATIVE_WORD_TEXT_REVISION
from .word_static_eq import REVISION as STATIC_EQ_REVISION

_PREVIEW_LIMIT = 4
_PREVIEW_BYTES_LIMIT = 16 * 1024 * 1024
_PREVIEWS = OrderedDict()
_PREVIEW_LOCK = RLock()


def _preview_key(source):
    return tuple(source[key] for key in ('batch_id', 'source_id', 'source_name',
        'source_role', 'source_sha256')) + (NATIVE_WORD_TEXT_REVISION, STATIC_EQ_REVISION)


def _valid_preview(preview, source):
    """Memory is an extraction optimization, never source identity authority."""
    try:
        return (isinstance(preview, dict)
            and preview.get('source_name') == source['source_name']
            and preview.get('source_sha256') == source['source_sha256']
            and preview.get('extraction_revision') in {NATIVE_WORD_TEXT_REVISION,
                NATIVE_WORD_TEXT_REVISION+'+'+STATIC_EQ_REVISION}
            and preview.get('revision') == source['source_revision']
            and all(isinstance(preview.get(key), list)
                for key in ('blocks', 'assets', 'sections', 'warnings'))
            and preview['revision'] == _digest({key: value for key, value in preview.items()
                if key != 'revision'}))
    except (ValueError, TypeError, RecursionError):
        return False


def _cached_preview(source):
    with _PREVIEW_LOCK:
        key = _preview_key(source)
        stored = _PREVIEWS.get(key)
        if stored is None:
            return None
        if not _valid_preview(stored[0], source):
            del _PREVIEWS[key]
            return None
        _PREVIEWS.move_to_end(key)
        return deepcopy(stored[0])


def _remember_preview(source, preview):
    size = len(json.dumps(preview, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    if size > _PREVIEW_BYTES_LIMIT:
        return
    with _PREVIEW_LOCK:
        check_read_cancelled()
        key = _preview_key(source)
        _PREVIEWS[key] = (deepcopy(preview), size)
        _PREVIEWS.move_to_end(key)
        while (len(_PREVIEWS) > _PREVIEW_LIMIT
                or sum(row[1] for row in _PREVIEWS.values()) > _PREVIEW_BYTES_LIMIT):
            _PREVIEWS.popitem(last=False)


def _current_document(facade, source, *, final=False):
    """Re-read only the selected archive; never substitute another same-byte file."""
    check_read_cancelled()
    current = facade._imported_word_source(source['batch_id'], source['source_id'])
    check_read_cancelled()
    content = getattr(current, 'content', None)
    if (not isinstance(content, bytes)
            or getattr(current, 'effective_source_file_id', None) != source['source_id']
            or getattr(current, 'filename', None) != source['source_name']
            or getattr(current, 'role', None) != source['source_role']
            or getattr(current, 'source_sha256', None) != source['source_sha256']
            or sha256(content).hexdigest() != source['source_sha256']):
        message = ('讲义在读取期间变化，请重新选择。' if final else
            '讲义原文或来源版本已变化，请重新选择。')
        raise ValueError(message)
    check_read_cancelled()
    return content


def validate_handout_source(binding):
    """Accept exactly an existing twelve-field picture or eight-field document."""
    if isinstance(binding, dict):
        if binding.get('schema') == PICTURE_SCHEMA:
            return validate_source(binding)
        if binding.get('schema') == DOCUMENT_SCHEMA:
            # The document validator also accepts hashes through ``str`` for
            # older metadata callers. Persisted handoff bindings require the
            # same concrete string hash types as original picture bindings.
            if any(not isinstance(binding.get(k), str)
                   for k in ('source_sha256', 'source_revision')):
                raise ValueError('讲义的来源版本不完整，请重新选择。')
            return validate_handout_knowledge_source(binding)
    raise ValueError('讲义的来源记录不完整，请重新选择。')


def handout_source_context(facade, binding):
    """Return the exact archive's original preview and real picture occurrences.

    Document snapshots use ``document_source`` so image readers cannot treat
    the whole Word document as a selected picture. An image-free Word returns
    an empty image list and remains a valid source for complete questions.
    """
    checked = validate_handout_source(binding)
    check_read_cancelled()
    if checked['schema'] == PICTURE_SCHEMA:
        return handout_image_context(facade, checked)
    content = _current_document(facade, checked)
    preview = _cached_preview(checked)
    cold = preview is None
    if cold:
        preview = PreparationSourcesService(facade.paths.workspace_root).word_preview_bytes(
            content, checked['source_name'])
    check_read_cancelled()
    if not _valid_preview(preview, checked):
        raise ValueError('讲义原文或提取版本已变化，请重新选择。')
    # As with legacy pictures, a restored physical location must not rewrite
    # the immutable old provenance path. The resolver reads and verifies the
    # facade's current exact archive while navigation keeps the input binding.
    source = checked
    document = {key: source[key] for key in ('batch_id', 'source_id',
        'source_name', 'source_path', 'source_sha256', 'source_revision')}
    images = []
    for asset in preview['assets']:
        check_read_cancelled()
        images.append(validate_source({'schema': PICTURE_SCHEMA, **document,
            **{key: asset[key] for key in _IMAGE_FIELDS}}))
    contexts = _context_labels(preview, source['batch_id'], source['source_id'])
    result = {'document_source': deepcopy(source), 'preview': deepcopy(preview),
        'images': images, 'contexts': contexts}
    _current_document(facade, checked, final=True)
    check_read_cancelled()
    if cold:
        _remember_preview(checked, preview)
    check_read_cancelled()
    return result

"""Explicit Word heading sections, with original text and picture occurrences.

The heading index is independent of the established Word preview revision.
Reading never creates a cache. Import validates the whole batch before the
existing transactional image store publishes any new content-addressed files.
"""
from contextlib import nullcontext
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import math
import re

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from .desktop_composition_images import teacher_image
from .desktop_composition_inline import NativeParagraphDisplay, _dimension
from .desktop_lesson_composition import block, digest, resource, validate_resource, ROLES
from .desktop_preparation_images import normalize_image_assets, verify_image_bytes
from .desktop_preparation_sources import (PreparationSourcesService, _body_blocks,
    _local, _table_rows, _readable_table)
from .desktop_teaching_framework import classroom_text
from .desktop_word_question_index import index_word_questions, _ANSWER
from .desktop_word_source_reference import WordSourceReferenceService, _commit_images
from .reader_cancellation import check_read_cancelled

SOURCE_SCHEMA = 'shchem.handout-knowledge-source.v1'
SECTION_SCHEMA = 'shchem.handout-knowledge-section.v1'
SOURCE_ROLES_SCHEMA = 'shchem.handout-knowledge-source-roles.v2'
OUTLINE_REVISION = 'shchem.word-explicit-headings.v1'
_METADATA = {'schema', 'batch_id', 'source_id', 'source_name', 'source_sha256', 'source_role'}
_BOUND = _METADATA | {'source_path', 'source_revision'}
_SECTION = {'id', 'level', 'title', 'start', 'end'}
_EVIDENCE = {'source_path', 'source_sha256', 'source_revision', 'handout_source',
    'section_snapshot', 'image_assets', 'source_roles'}
_REF = {'block_index', 'position', 'source_asset_id', 'source_sha256', 'original_sha256',
    'original_mime_type', 'asset_id', 'preview_sha256', 'derived_preview',
    'renderer_revision', 'source_role', 'source_branch', 'status', 'container_xml_sha256',
    'width_mm', 'height_mm'}
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_MARKER = re.compile(r'【待查看原文：[^】]*】')
_METHOD_TITLE = re.compile(r'思维建模(?:[ \t\u3000]+|[：:][ \t\u3000]*)([^\r\n。！？?；;：:【】]{1,160})\Z')
_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
_WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
_PIC = 'http://schemas.openxmlformats.org/drawingml/2006/picture'
_V = 'urn:schemas-microsoft-com:vml'
_O = 'urn:schemas-microsoft-com:office:office'
_W10 = 'urn:schemas-microsoft-com:office:word'
_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
OBJECT_SCHEMA = 'shchem.handout-object-preview.v1'
_OBJECT_FIELDS = {'schema', 'prog_id', 'shape_id', 'coverage', 'binary_interpreted',
    'container_xml_sha256', 'image', 'ole'}
_RELATION_FIELDS = {'relationship_id', 'relationship_type', 'package_part',
    'mime_type', 'sha256', 'bytes_count', 'is_external'}


def _text(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and not any(ord(c) < 32 for c in value)


def _source(row, bound=False):
    if not isinstance(row, dict) or set(row) not in ((_BOUND,) if bound else (_METADATA, _BOUND)):
        raise ValueError('讲义栏目来源记录不完整，请重新选择。')
    if (row['schema'] != SOURCE_SCHEMA or not isinstance(row['source_role'], str)
            or row['source_role'] not in {'handout', 'question', 'answer'}
            or any(not _text(row[k], 256) for k in ('batch_id', 'source_id'))
            or not _text(row['source_name'], 2000) or not _SHA.fullmatch(str(row['source_sha256']))):
        raise ValueError('讲义栏目来源标识或文件摘要无效。')
    if set(row) == _BOUND and (not _text(row['source_path'], 2000)
            or not _SHA.fullmatch(str(row['source_revision']))):
        raise ValueError('讲义栏目必须保留原Word位置和提取版本。')
    return deepcopy(row)


def validate_handout_knowledge_source(row):
    """Validate the eight-field source binding stored in a course resource."""
    return _source(row, bound=True)


def _section(row):
    if (not isinstance(row, dict) or set(row) != _SECTION or type(row['level']) is not int
            or not 1 <= row['level'] <= 9 or type(row['start']) is not int
            or type(row['end']) is not int or not 1 <= row['start'] <= row['end']
            or row['id'] not in {f"word-heading-b{row['start']}", f"word-method-b{row['start']}"}
            or not _text(row['title'], 2000)):
        raise ValueError('请选择Word中明确的完整栏目，不能改写原栏目范围。')
    return deepcopy(row)


def handout_knowledge_sources(facade):
    """List saved metadata only: do not preview every imported Word file."""
    rows, issues = [], []
    for batch in facade.list_imported_word_batches():
        check_read_cancelled()
        try:
            for row in facade.imported_word_sources(batch.batch_id):
                rows.append(_source({'schema': SOURCE_SCHEMA, 'batch_id': batch.batch_id,
                    'source_id': row['source_id'], 'source_name': row['source_name'],
                    'source_sha256': row['source_sha256'], 'source_role': row['role']}))
        except (ValueError, OSError) as exc:
            issues.append({'batch_id': batch.batch_id, 'message': str(exc)})
    check_read_cancelled()
    return {'sources': rows, 'issues': issues}


def _heading_rows(document, preview):
    """Use actual heading/outlining properties, including inherited styles."""
    elements = list(_body_blocks(document._element.body))
    if len(elements) != len(preview['blocks']):
        raise ValueError('Word栏目与原文区块定位不一致，请重新读取。')
    headings = []
    for index, element in enumerate(elements, 1):
        if _local(element) != 'p':
            continue
        paragraph = Paragraph(element, document._body)
        style = paragraph.style
        names, styles = [], []
        while style is not None:
            if style.style_id in [s.style_id for s in styles]:
                raise ValueError('Word标题样式继承循环，无法完整读取栏目。')
            styles.append(style)
            names.append((style.name or '').casefold().replace(' ', ''))
            style = style.base_style
        if any(n.startswith('toc') or '目录' in n for n in names):
            continue
        levels = element.xpath('./w:pPr/w:outlineLvl')
        for inherited in styles:
            if levels:
                break
            levels = inherited.element.xpath('./w:pPr/w:outlineLvl')
        level = None
        if levels:
            try:
                level = int(levels[0].get(qn('w:val'))) + 1
            except (TypeError, ValueError):
                continue
        else:
            for name in names:
                match = re.fullmatch(r'(?:heading|标题)([1-9])', name)
                if match:
                    level = int(match[1]); break
        if level not in range(1, 10):
            continue
        raw = preview['blocks'][index - 1]['text']
        title = ' '.join(_MARKER.sub('', raw).split())
        if title.replace(' ', '').casefold() in {'目录', 'contents', 'tableofcontents'}:
            continue
        # Empty headings still delimit the previous section. A picture-only
        # heading retains its literal source marker rather than an invented name.
        if not title:
            title = ' '.join(raw.split())
        headings.append({'id': f'word-heading-b{index}', 'level': level, 'title': title, 'start': index})
    return headings


def _headings(document, preview):
    """Keep established H1–H9 IDs and ranges, including empty delimiters."""
    headings = _heading_rows(document, preview)
    result = []
    for position, heading in enumerate(headings):
        end = next((h['start'] - 1 for h in headings[position + 1:] if h['level'] <= heading['level']), len(preview['blocks']))
        if heading['title']:
            result.append(_section({**heading, 'end': end}))
    return result


def _explicit_method_sections(document, preview, occupied):
    """Whole literal method units outside every known question/answer/context.

    The source paragraph must itself be the explicit title. Sentence mentions,
    picture labels and titles inside an exercise never become knowledge units.
    Keep every intervening source block, including tables and picture-only
    paragraphs, until the next actual heading, literal title or question scope.
    """
    elements = list(_body_blocks(document._element.body))
    headings = _heading_rows(document, preview)
    styled_starts = {h['start'] for h in headings}
    occupied = set(occupied)
    indexed = index_word_questions(preview)
    # The existing question recognizer treats the literal label as a heading.
    # An unfinished preceding question must not become "complete" merely by
    # that cut: protect it through the next real heading or printed question.
    question_boundaries = styled_starts | {r['block_start'] for r in indexed}
    for row in indexed:
        if not row['answer_blocks']:
            end = min((n for n in question_boundaries if n > row['block_start']),
                      default=len(elements) + 1)
            occupied.update(range(row['block_start'], end))
    assets = {a['block_index'] for a in preview['assets']}
    titles = []
    for native, element in zip(preview['blocks'], elements, strict=True):
        check_read_cancelled()
        index, raw = native['index'], native['text'].strip()
        if (_local(element) != 'p' or index in styled_starts or index in occupied
                or index in assets or not _METHOD_TITLE.fullmatch(raw)):
            continue
        titles.append({'start': index, 'title': ' '.join(raw.split())})
    boundaries = sorted(styled_starts | occupied | {t['start'] for t in titles})
    result = []
    for title in titles:
        start = title['start']
        end = next((n - 1 for n in boundaries if n > start), len(elements))
        # Adjacent titles and empty units cannot claim a complete knowledge body.
        if not any(b['text'].strip() or b['index'] in assets
                   for b in preview['blocks'][start:end]):
            continue
        parent_level = next((h['level'] for h in reversed(headings) if h['start'] < start), 0)
        result.append(_section({'id': f'word-method-b{start}',
            'level': min(parent_level + 1, 9), 'title': title['title'], 'start': start, 'end': end}))
    return result


def _outline(facade, chosen):
    chosen = _source(chosen); check_read_cancelled()
    source = facade._imported_word_source(chosen['batch_id'], chosen['source_id'])
    preview = PreparationSourcesService(facade.paths.workspace_root).word_preview_bytes(source.content, source.filename)
    if (getattr(source,'effective_source_file_id',None) != chosen['source_id']
            or sha256(source.content).hexdigest() != chosen['source_sha256']
            or preview['source_sha256'] != chosen['source_sha256']
            or source.filename != chosen['source_name'] or source.role != chosen['source_role']
            or ('source_revision' in chosen and chosen['source_revision'] != preview['revision'])):
        raise ValueError('讲义原文或提取版本已变化，请重新选择栏目。')
    bound = {**{k: chosen[k] for k in _METADATA},
        'source_path': str(facade.imported_word_path(chosen['batch_id'], chosen['source_id'])),
        'source_revision': preview['revision']}
    bound = validate_handout_knowledge_source(bound)
    document = Document(BytesIO(source.content))
    sections = _headings(document, preview)
    # Sources without explicit method paragraphs keep their exact old outline.
    if any(_METHOD_TITLE.fullmatch(b['text'].strip()) for b in preview['blocks']):
        occupied = _question_ranges(facade, bound, preview)
        methods = _explicit_method_sections(document, preview, occupied)
        sections = sorted(sections + methods, key=lambda s: (s['start'], s['level'], s['id']))
    revision = digest({'index_revision': OUTLINE_REVISION, 'source_sha256': bound['source_sha256'],
        'source_revision': bound['source_revision'], 'sections': sections})
    current = facade._imported_word_source(chosen['batch_id'], chosen['source_id'])
    if (getattr(current,'effective_source_file_id',None) != bound['source_id']
            or sha256(current.content).hexdigest() != bound['source_sha256']
            or current.filename != bound['source_name'] or current.role != bound['source_role']):
        raise ValueError('讲义在读取栏目期间变化，请重新选择。')
    if set(chosen)==_BOUND:
        # The exact current archive was verified above. Old physical paths are
        # immutable provenance, never a read locator. New metadata selections
        # still get imported_word_path; actual opens use that native resolver.
        fresh=PreparationSourcesService(facade.paths.workspace_root).word_preview_bytes(current.content,current.filename)
        if fresh!=preview or fresh['revision']!=chosen['source_revision']:
            raise ValueError('讲义的完整提取版本在读取期间变化，请重新选择。')
        bound=validate_handout_knowledge_source(deepcopy(chosen))
    check_read_cancelled()
    return {'source': bound, 'outline_revision': revision, 'sections': sections, 'preview': deepcopy(preview)}, source


def handout_knowledge_outline(facade, source):
    """Read only the selected original Word and its separate heading index."""
    return _outline(facade, source)[0]


def _request(row):
    if not isinstance(row, dict) or set(row) != {'source', 'outline_revision', 'section', 'title', 'teacher_only'}:
        raise ValueError('讲义栏目选择记录不完整，请重新预览。')
    source = validate_handout_knowledge_source(row['source'])
    section = _section(row['section'])
    title = classroom_text(row['title']) if isinstance(row['title'], str) else ''
    if (not title or len(title) > 160 or type(row['teacher_only']) is not bool
            or not _SHA.fullmatch(str(row['outline_revision']))):
        raise ValueError('请填写不超过160字的问题标题，并明确课堂使用范围。')
    return {**row, 'source': source, 'section': section, 'title': title}


def _request_matches_outline(request, outline, source):
    """Accept an unchanged legal legacy styled scope after adding new units.

    Only the exact historical styled-directory digest is compatible. New
    method IDs, changed styled ranges and invented/stale hashes still fail;
    the caller separately checks every source identity and question role.
    """
    section = request['section']
    if section not in outline['sections']:
        return False
    if request['outline_revision'] == outline['outline_revision']:
        return True
    if section['id'] != f"word-heading-b{section['start']}":
        return False
    legacy = _headings(Document(BytesIO(source.content)), outline['preview'])
    legacy_revision = digest({'index_revision': OUTLINE_REVISION,
        'source_sha256': outline['source']['source_sha256'],
        'source_revision': outline['source']['source_revision'], 'sections': legacy})
    return section in legacy and request['outline_revision'] == legacy_revision


def _question_ranges(facade, source, preview):
    # Keep automatic ranges as well: a saved edit cannot make a known answer
    # paragraph available as ordinary classroom knowledge.
    rows = index_word_questions(preview)
    if hasattr(facade, '_word_questions'):
        words = facade._word_questions()
        with words._lock:
            catalog, inventory = words._catalog({source['batch_id']: {source['source_id']}}, read_only=True)
        found = inventory.get(source['source_sha256'])
        if found is None or len(inventory) != 1:
            raise ValueError('讲义题答来源已变化，请重新读取栏目。')
        native, batch_id, current, _indexed = found
        if (batch_id != source['batch_id'] or native.effective_source_file_id != source['source_id']
                or sha256(native.content).hexdigest() != source['source_sha256']
                or native.source_sha256 != source['source_sha256']
                or current['source_sha256'] != source['source_sha256'] or current['revision'] != source['source_revision']):
            raise ValueError('讲义题答与栏目不属于同一原文版本。')
        for row in catalog['items']:
            if (row.get('batch_id') != source['batch_id'] or row.get('archive_source_id') != source['source_id']
                    or row.get('source_sha256') != source['source_sha256']
                    or row.get('source_revision') != source['source_revision']):
                raise ValueError('讲义题答范围来源失配。')
        rows += catalog['items']
    return {b['index'] for row in rows for group in ('context_blocks', 'question_blocks', 'answer_blocks') for b in row[group]}


def _check_roles(facade, source, preview, section):
    indices = set(range(section['start'], section['end'] + 1))
    if indices & _question_ranges(facade, source, preview):
        raise ValueError('该栏目包含已识别的题目或答案，请从这份讲义的完整题入口加入。')
    if any(_ANSWER.search(b['text']) for b in preview['blocks'] if b['index'] in indices):
        raise ValueError('该栏目包含答案或解析，请从完整题入口保留题答范围。')


def _chemwindow_preview(node, document, ref):
    """Bind the one embedded display image; never open or interpret the OLE binary."""
    from .offline_word_image_label_review import _xml_sha
    allowed = {'{' + _W + '}object', '{' + _V + '}shape', '{' + _V + '}imagedata', '{' + _V + '}stroke',
        '{' + _O + '}lock', '{' + _W10 + '}anchorlock', '{' + _O + '}OLEObject'}
    if (node.attrib or any(n.tag not in allowed for n in node.iter())
            or any(_local(n) in {'drawing', 'pict', 'object', 'AlternateContent'} for n in node.iterancestors())):
        raise ValueError('ChemWindow显示对象含尚不能完整绑定的结构。')
    shapes = node.findall('.//{' + _V + '}shape')
    images = node.findall('.//{' + _V + '}imagedata')
    oles = node.findall('.//{' + _O + '}OLEObject')
    if (len(shapes) != 1 or len(images) != 1 or len(oles) != 1
            or shapes[0].getparent() is not node or images[0].getparent() is not shapes[0]
            or oles[0].getparent() is not node):
        raise ValueError('ChemWindow对象必须有唯一的同容器原图和内嵌对象。')
    shape, image, ole = shapes[0], images[0], oles[0]
    if (shape.get('type') != '#_x0000_t75' or not _text(shape.get('id'), 256)
            or set(shape.attrib) - {'id', 'type', 'alt', 'style', '{' + _O + '}ole',
                'coordsize', '{' + _O + '}preferrelative', 'filled', 'stroked'}
            or shape.get('filled') not in {None, 'f', 'false', '0'}
            or shape.get('stroked') not in {None, 'f', 'false', '0'}
            or any(part.split(':', 1)[0].strip() not in {'width', 'height'}
                for part in shape.get('style', '').split(';') if part.strip())
            or set(image.attrib) - {qn('r:id'), '{' + _O + '}title'} or list(image)):
        raise ValueError('ChemWindow原图含裁剪、效果或外链，未导入栏目。')
    for child in shape:
        tag = _local(child)
        if child is image:
            continue
        if child.getparent() is not shape or list(child):
            raise ValueError('ChemWindow原图的形状元数据不完整。')
        if tag == 'stroke':
            valid = shape.get('stroked') in {'f', 'false', '0'} and dict(child.attrib) == {'joinstyle': 'miter'}
        elif tag == 'lock':
            valid = dict(child.attrib) == {'{' + _V + '}ext': 'edit', 'aspectratio': 't'}
        elif tag == 'anchorlock':
            valid = not child.attrib
        else:
            valid = False
        if not valid:
            raise ValueError('ChemWindow原图的效果尚不能完整绑定。')
    if (set(ole.attrib) - {'Type', 'ProgID', 'ShapeID', 'DrawAspect', 'ObjectID', qn('r:id')}
            or list(ole) or ole.get('Type') != 'Embed' or ole.get('DrawAspect') != 'Content'
            or ole.get('ProgID') != 'ChemWindow.Document' or ole.get('ShapeID') != shape.get('id')):
        raise ValueError('ChemWindow显示预览与内嵌对象并非同一对象。')
    relations = {}
    for key, part_node, expected_type in (('image', image, _R + '/image'), ('ole', ole, _R + '/oleObject')):
        rid = part_node.get(qn('r:id')); relation = document.part.rels.get(rid)
        if relation is None or relation.is_external or relation.reltype != expected_type:
            raise ValueError('ChemWindow对象缺少完整的本地内嵌关系。')
        part = relation.target_part
        relations[key] = {'relationship_id': rid, 'relationship_type': relation.reltype,
            'package_part': str(part.partname), 'mime_type': part.content_type,
            'sha256': sha256(part.blob).hexdigest(), 'bytes_count': len(part.blob), 'is_external': False}
    source_object = {'schema': OBJECT_SCHEMA, 'prog_id': 'ChemWindow.Document',
        'shape_id': shape.get('id'), 'coverage': 'same_object_visible_preview_only',
        'binary_interpreted': False, 'container_xml_sha256': _xml_sha(node), **relations}
    _validate_source_object(source_object, {**ref, 'container_xml_sha256': _xml_sha(node)})
    return source_object


def _validate_source_object(value, ref):
    if isinstance(value, dict) and value.get('prog_id') == 'PBrush':
        from .desktop_handout_paint_preview import validate_paint_preview
        return validate_paint_preview(value, ref)
    if (not isinstance(value, dict) or set(value) != _OBJECT_FIELDS or value['schema'] != OBJECT_SCHEMA
            or value['prog_id'] != 'ChemWindow.Document' or not _text(value['shape_id'], 256)
            or value['coverage'] != 'same_object_visible_preview_only' or value['binary_interpreted'] is not False
            or value['container_xml_sha256'] != ref['container_xml_sha256']):
        raise ValueError('ChemWindow同对象显示证据不完整。')
    for key in ('image', 'ole'):
        relation = value[key]
        if (not isinstance(relation, dict) or set(relation) != _RELATION_FIELDS
                or not _text(relation['relationship_id'], 256)
                or relation['relationship_type'] != _R + ('/image' if key == 'image' else '/oleObject')
                or not _text(relation['package_part'], 2000) or not relation['package_part'].startswith('/word/')
                or '..' in relation['package_part'].split('/') or '\\' in relation['package_part']
                or not _SHA.fullmatch(str(relation['sha256'])) or type(relation['bytes_count']) is not int
                or not 0 < relation['bytes_count'] <= 40 * 1024 * 1024 or relation['is_external'] is not False):
            raise ValueError('ChemWindow原图或对象的内部关系证据不完整。')
    image, ole = value['image'], value['ole']
    if (image['relationship_id'] == ole['relationship_id'] or image['package_part'] == ole['package_part']
            or image['sha256'] != ref['original_sha256'] or image['mime_type'] != ref['original_mime_type']
            or image['mime_type'] not in {'image/wmf', 'image/x-wmf', 'image/emf', 'image/x-emf'}
            or ole['mime_type'] != 'application/vnd.openxmlformats-officedocument.oleObject'):
        raise ValueError('ChemWindow原图与对象的来源摘要不一致。')


def _occurrence_objects(display, native, references):
    """Bind complete embedded pixels, not a reconstruction of Word effects.

    Cropping/brightness in the Word container remains source XML evidence;
    the classroom material deliberately uses the complete original image.
    Unsupported shapes, floating/nested/ambiguous objects cannot masquerade
    as one original picture. OLE previews retain the existing strict check.
    """
    from .offline_word_image_label_review import _objects, _xml_sha
    element = display.elements[native['index'] - 1]
    scene = display.source_scene(native['index'])
    containers = [n for n in element.iter() if _local(n) in {'drawing', 'pict', 'object'}]
    if len(containers) != len(references):
        raise ValueError('原图出现次数与Word对象定位不一致，未导入本批栏目。')
    result = []
    for node, ref in zip(containers, references):
        if (ref['source_branch'] != 'direct'
                or any(_local(a) in {'drawing', 'pict', 'object', 'AlternateContent'} for a in node.iterancestors())):
            raise ValueError('嵌套或兼容分支图形尚不能完整定位，请使用原Word。')
        if _local(node) == 'object':
            oles = node.findall('.//{' + _O + '}OLEObject')
            if len(oles) == 1 and oles[0].get('ProgID') == 'ChemWindow.Document':
                ref['source_object'] = _chemwindow_preview(node, display.doc, ref)
                placeholder = '【待查看原文：嵌入对象或旧公式】'
            elif len(oles) == 1 and oles[0].get('ProgID') == 'PBrush':
                from .desktop_handout_paint_preview import paint_preview
                ref['source_object'] = paint_preview(node, display.doc, ref)
                placeholder = '【待查看原文：嵌入对象或旧公式】'
            else:
                originals = [a for a in display.preview_assets if a['block_index'] == native['index']]
                checked = _objects(element, display.doc, native['index'], originals)
                match = [o for o in checked if o['container_xml_sha256'] == _xml_sha(node)]
                if len(match) != 1:
                    raise ValueError('旧公式对象与显示预览不能对应。')
                placeholder = match[0]['placeholder']
        else:
            if _local(node) == 'drawing' and scene is None and (len(node.findall('.//{' + _WP + '}inline')) != 1
                    or len(node.findall('.//{' + _PIC + '}pic')) != 1
                    or node.findall('.//{' + _WP + '}anchor')):
                raise ValueError('浮动图形或形状尚不能作为完整栏目原图加入。')
            if _local(node) == 'drawing':
                graphics = [n for n in node.iter() if _local(n) == 'graphicData']
                if len(graphics) != 1 or graphics[0].get('uri') != _PIC or any(
                        _local(n) in {'wsp', 'wgp', 'sp', 'txbxContent', 'textbox'} for n in node.iter()):
                    raise ValueError('原图包含其他形状或文本框，未导入本批栏目。')
            else:
                shapes = [n for n in node.iter() if _local(n) == 'shape']
                if len(shapes) != 1 or shapes[0].get('type') != '#_x0000_t75' or any(
                        _local(n) not in {'pict', 'shape', 'imagedata', 'stroke', 'lock', 'anchorlock'} for n in node.iter()):
                    raise ValueError('旧式图形并非单一完整原图，未导入本批栏目。')
            image_nodes = [n for n in node.iter() if _local(n) in {'blip', 'imagedata'}]
            if len(image_nodes) != 1:
                raise ValueError('原生图形没有唯一完整的内嵌图片。')
            image = image_nodes[0]
            rid = image.get(qn('r:embed')) or image.get(qn('r:id'))
            relation = display.doc.part.rels.get(rid)
            if (image.get(qn('r:link')) or relation is None or relation.is_external
                    or not relation.target_part.content_type.startswith('image/')
                    or sha256(relation.target_part.blob).hexdigest() != ref['original_sha256']):
                raise ValueError('原图的内嵌关系或原始字节已变化。')
            placeholder = '【待查看原文：图片或图形】' if _local(node) == 'drawing' else '【待查看原文：图片或旧式图形】'
        ref['container_xml_sha256'] = _xml_sha(node)
        ref['width_mm'], ref['height_mm'] = _dimension(node)
        from .desktop_handout_paint_preview import picture_transform
        transform = picture_transform(node, ref)
        if transform is not None:
            ref['transform'] = transform
        result.append({'container_xml_sha256': ref['container_xml_sha256'], 'placeholder': placeholder,
                       **({'transform': deepcopy(transform)} if transform is not None else {})})
    if scene is not None:
        from .word_static_quote import validate_scene
        validate_scene(scene, native['text'], refs=references)
    return result


def _table_snapshot(element, document, text):
    rows = _table_rows(element, document, set())
    if _readable_table(rows) != text:
        raise ValueError('原Word表格行列与完整原文不一致。')
    return rows


def _source_scope_role(teacher_only, document_role):
    return 'teacher_note' if teacher_only or document_role == 'answer' else 'knowledge'


def _knowledge_scope_role(scope):
    role = _source_scope_role(scope['teacher_only'], scope['document_role'])
    # Unversioned saved evidence used globally shared image roles. Keep that
    # frozen contract, without migrating or unlocking existing teacher blocks.
    if 'schema' not in scope and any(teacher_image(row['roles']) for row in scope['images']):
        return 'teacher_note'
    return role


def validate_handout_knowledge_evidence(evidence):
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE:
        raise ValueError('讲义知识栏目来源字段不完整。')
    source = validate_handout_knowledge_source(evidence['handout_source'])
    if any(evidence[k] != source[k] for k in ('source_path', 'source_sha256', 'source_revision')):
        raise ValueError('讲义栏目来源与封存原文件不一致。')
    snapshot = evidence['section_snapshot']
    if (not isinstance(snapshot, dict) or set(snapshot) != {'schema', 'outline_revision', 'section', 'blocks'}
            or snapshot['schema'] != SECTION_SCHEMA or not _SHA.fullmatch(str(snapshot['outline_revision']))):
        raise ValueError('完整讲义栏目须保留独立栏目版本和原始区块。')
    section = _section(snapshot['section']); native = snapshot['blocks']
    if (not isinstance(native, list) or not 1 <= len(native) <= 256
            or any(not isinstance(b, dict) or type(b.get('index')) is not int for b in native)
            or [b.get('index') for b in native if isinstance(b, dict)] != list(range(section['start'], section['end'] + 1))):
        raise ValueError('封存栏目必须保留每个连续原始区块，不能截断。')
    assets = normalize_image_assets(evidence['image_assets'])
    by_id = {a['asset_id']: a for a in assets}; used = set()
    for native_block in native:
        if (set(native_block) not in ({'index', 'text', 'images'}, {'index', 'text', 'images', 'table_rows'},
                                    {'index','text','images','static_eqs'}, {'index','text','images','source_scene'})
                or not isinstance(native_block['text'], str) or len(native_block['text']) > 120000
                or not isinstance(native_block['images'], list) or len(native_block['images']) > 48):
            raise ValueError('原文字、原图与表格快照不完整。')
        if 'table_rows' in native_block:
            _validate_table_rows(native_block['table_rows'])
            if _readable_table(native_block['table_rows']) != native_block['text']:
                raise ValueError('表格原行列与封存原文不一致。')
        for position, ref in enumerate(native_block['images'], 1):
            if (not isinstance(ref, dict) or not _REF <= set(ref) <= _REF | {'source_object', 'transform'} or ref['block_index'] != native_block['index']
                    or type(ref['block_index']) is not int or type(ref['position']) is not int
                    or ref['position'] != position or ref['source_sha256'] != source['source_sha256']
                    or ref['source_role'] != source['source_role'] or ref['status'] != 'included'
                    or ref['source_branch'] != 'direct' or not _text(ref['source_asset_id'], 256)
                    or not _text(ref['original_mime_type'], 256) or type(ref['derived_preview']) is not bool
                    or any(not _SHA.fullmatch(str(ref[k])) for k in ('original_sha256', 'preview_sha256', 'container_xml_sha256'))
                    or ref['asset_id'] != 'IMG-' + ref['preview_sha256'] or ref['asset_id'] not in by_id):
                raise ValueError('完整原图出现位置与来源摘要失配。')
            if (ref['derived_preview'] and not _text(ref['renderer_revision'], 256)) or (not ref['derived_preview'] and (
                    ref['renderer_revision'] is not None or ref['original_sha256'] != ref['preview_sha256']
                    or ref['original_mime_type'] != by_id[ref['asset_id']]['content_type'])):
                raise ValueError('原图与派生预览身份不一致。')
            if 'source_object' in ref:
                _validate_source_object(ref['source_object'], ref)
                if (by_id[ref['asset_id']]['content_type'] != 'image/png'
                        or (ref['source_object']['prog_id'] == 'ChemWindow.Document' and not ref['derived_preview'])):
                    raise ValueError('ChemWindow对象须保留原矢量图与独立派生PNG。')
            if 'transform' in ref:
                from .desktop_handout_paint_preview import validate_picture_transform
                validate_picture_transform(ref['transform'], ref)
            for size in (ref['width_mm'], ref['height_mm']):
                if size != 'unknown' and (type(size) not in (int, float) or not math.isfinite(size) or not 0 < size <= 10000):
                    raise ValueError('原图对象尺寸须保留来源值，缺失填写unknown。')
            used.add(ref['asset_id'])
        if 'source_scene' in native_block:
            from .word_static_quote import validate_scene
            validate_scene(native_block['source_scene'], native_block['text'], refs=native_block['images'])
    if used != set(by_id):
        raise ValueError('栏目原图记录存在缺失或无关图片。')
    roles = evidence['source_roles']
    if (not isinstance(roles, dict) or set(roles) not in (
                {'teacher_only', 'document_role', 'images'},
                {'schema', 'teacher_only', 'document_role', 'images'})
            or ('schema' in roles and roles['schema'] != SOURCE_ROLES_SCHEMA)
            or type(roles['teacher_only']) is not bool or roles['document_role'] != source['source_role']
            or not isinstance(roles['images'], list) or len(roles['images']) != len(assets)):
        raise ValueError('讲义栏目须保留原资料及图片来源的使用范围。')
    seen = set()
    for row in roles['images']:
        if (not isinstance(row, dict) or set(row) != {'asset_id', 'roles'} or not isinstance(row['asset_id'], str)
                or row['asset_id'] not in by_id
                or row['asset_id'] in seen or not isinstance(row['roles'], list)
                or any(not isinstance(r, str) or r not in ROLES for r in row['roles'])
                or len(row['roles']) != len(set(row['roles']))):
            raise ValueError('原图既有教师范围记录无效。')
        seen.add(row['asset_id'])
        # Each asset above is bound to the current source's exact occurrences.
        # Its other uses share pixels, not the audience of this verified scope.
        if 'schema' in roles and row['roles'] != [_source_scope_role(roles['teacher_only'], roles['document_role'])]:
            raise ValueError('原图使用范围与当前讲义来源及教师选择不一致。')
    return source, snapshot, assets


def _validate_table_rows(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError('原表格行记录无效。')
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'grid_before', 'grid_after', 'cells'}
                or any(type(row[k]) is not int or not 0 <= row[k] <= 10000 for k in ('grid_before', 'grid_after'))
                or not isinstance(row['cells'], list) or len(row['cells']) > 10000):
            raise ValueError('原表格网格记录无效。')
        for cell in row['cells']:
            if (not isinstance(cell, dict) or set(cell) != {'column_span', 'vertical_merge', 'text'}
                    or type(cell['column_span']) is not int or not 1 <= cell['column_span'] <= 10000
                    or not isinstance(cell['vertical_merge'], str) or cell['vertical_merge'] not in {'none', 'restart', 'continue'}
                    or not isinstance(cell['text'], str) or len(cell['text']) > 120000):
                raise ValueError('原表格单元格或合并记录无效。')


def validate_handout_knowledge_resource(value):
    original = value['original']
    source, snapshot, _assets = validate_handout_knowledge_evidence(original['evidence'])
    scope = original['evidence']['source_roles']
    role = _knowledge_scope_role(scope)
    expected = [(kind, role, b['index'], content) for b in snapshot['blocks']
        for kind, content in [('text', b['text']), *[('image', r['asset_id']) for r in b['images']]]]
    actual = [(b['kind'], b['role'], b['source_index'], b['value']) for b in original['blocks']]
    if original['kind'] != 'handout_knowledge' or actual != expected or original['title'] != source['source_name'] + ' · ' + snapshot['section']['title']:
        raise ValueError('讲义栏目必须完整绑定封存原文字、每次原图位置与教师使用范围。')
    for native in snapshot['blocks']:
        text=next(b for b in original['blocks'] if b['kind']=='text' and b['source_index']==native['index'])
        presentations = text.get('presentation', {}).get('images', [])
        pictures = [b for b in original['blocks'] if b['kind']=='image' and b['source_index']==native['index']]
        if any('transform' in r for r in native['images']) and len(presentations) != len(native['images']):
            raise ValueError('原图翻转缺少原段落的逐次出现绑定。')
        for ref, picture, presentation in zip(native['images'], pictures, presentations):
            if presentation['image_block_id'] != picture['id'] or presentation.get('transform') != ref.get('transform'):
                raise ValueError('原图显示变换与栏目来源出现位置失配。')
        if native.get('static_eqs',[])!=text.get('presentation',{}).get('eqs',[]):
            raise ValueError('静态EQ原字段与完整栏目快照失配。')
        if native.get('source_scene') != text.get('presentation', {}).get('scene'):
            raise ValueError('原Word图文布局与完整栏目快照失配。')
        for span in native.get('static_eqs',[]):
            base=span['field']['base']
            if base['kind']=='image':
                picture=next(b for b in original['blocks'] if b['id']==base['image_block_id'])
                refs=[r for r in native['images'] if r['asset_id']==picture['value']
                      and r['container_xml_sha256']==base['container_xml_sha256']]
                if len(refs)!=1:raise ValueError('静态EQ原图与栏目来源出现位置失配。')


def _same_section(first, second):
    a, b = first['original']['evidence'], second['original']['evidence']
    # A directory gains independent units without changing this frozen scope.
    # Keep the exact section, full blocks, every image reference and source
    # revision in the comparison; only its encompassing directory digest varies.
    def stable_snapshot(item):
        snapshot = deepcopy(item['original']['evidence']['section_snapshot'])
        snapshot.pop('outline_revision')
        # Static image EQ bases reference fresh composition block UUIDs. Compare
        # their exact source occurrence instead; never normalize source text,
        # field evidence, image pixels, crop, or another same-pixel occurrence.
        positions = {block['id']: index for index, block in enumerate(item['original']['blocks'])}
        for native in snapshot['blocks']:
            for span in native.get('static_eqs', []):
                base = span['field']['base']
                if base['kind'] == 'image':
                    base['image_block_id'] = positions[base['image_block_id']]
        return snapshot
    a_snapshot, b_snapshot = stable_snapshot(first), stable_snapshot(second)
    return (all(a[k] == b[k] for k in ('source_sha256', 'source_revision'))
        and a_snapshot == b_snapshot)


def import_handout_knowledge(facade, requests, existing_assets, existing_resources):
    """Return additions once; preserve existing edits and commit images atomically."""
    if not isinstance(requests, list) or not 1 <= len(requests) <= 20:
        raise ValueError('每次请选择1至20个完整讲义栏目。')
    requests = [_request(row) for row in requests]
    current_assets = normalize_image_assets(existing_assets)
    if not isinstance(existing_resources, list):
        raise ValueError('当前课件资源记录无效。')
    current_resources = [validate_resource(r) for r in existing_resources]
    words = facade._word_questions() if hasattr(facade, '_word_questions') else None
    with words._lock if words is not None else nullcontext():
        service = WordSourceReferenceService(facade)
        contexts, compiled_rows, items, assets, payloads = {}, [], [], {}, {}
        for request in requests:
            check_read_cancelled()
            source = request['source']; key = digest({k: source[k] for k in _BOUND - {'source_path'}})
            if key not in contexts:
                contexts[key] = _outline(facade, source)
            outline, original = contexts[key]
            if not _request_matches_outline(request, outline, original):
                raise ValueError('原Word栏目或范围已变化，请重新预览。')
            source = outline['source']
            section = request['section']
            _check_roles(facade, outline['source'], outline['preview'], section)
            native_blocks = outline['preview']['blocks'][section['start'] - 1:section['end']]
            # Preserve the established whole-Word preview. Only a separately
            # verified complete source scene can explain its field dependency;
            # unknown, nested and cross-paragraph fields still reject the batch.
            display = NativeParagraphDisplay(original.content)
            native_readings=[(display.elements[b['index']-1],display.reader.read(display.elements[b['index']-1])) for b in native_blocks]
            if any(('field_code_dependency' in reading.features and display.source_scene(native['index']) is None) or
                   (_local(element)!='p' and reading.static_eqs)
                   for native, (element, reading) in zip(native_blocks, native_readings, strict=True)):
                raise ValueError('所选讲义栏目含尚不能完整读取的Word公式域或字段，请使用原Word；未导入本批栏目。')
            selection = {k: source[k] for k in ('batch_id', 'source_id', 'source_sha256')}
            selection.update(revision=source['source_revision'], block_start=section['start'], block_end=section['end'])
            compiled, data, quality_hold = service._compile(selection, include_images=True, include_guidance=False)
            if quality_hold or compiled['image_issues'] or compiled['reference_issues']:
                raise ValueError('所选讲义栏目有已记录的来源问题或原图缺口，未导入本批栏目。')
            display.preview_assets = outline['preview']['assets']
            # The current source and complete section have passed the role
            # guard. An identical image elsewhere is not this occurrence.
            role = _source_scope_role(request['teacher_only'], source['source_role'])
            role_records = [{'asset_id': a['asset_id'], 'roles': [role]}
                for a in compiled['image_assets']]
            blocks, snapshot_blocks = [], []
            for native in native_blocks:
                element = display.elements[native['index'] - 1]
                if _local(element) not in {'p', 'tbl'}:
                    raise ValueError('该栏目含尚不能完整读取的正文容器，未导入本批栏目。')
                refs = deepcopy([r for r in compiled['image_references'] if r['block_index'] == native['index']])
                objects = _occurrence_objects(display, native, refs)
                if _MARKER.findall(native['text']) != [o['placeholder'] for o in objects]:
                    raise ValueError('栏目含尚不能完整读取的图形或公式，未导入本批栏目。')
                text = block('text', role, native['text'], native['index']); blocks.append(text)
                pictures = [block('image', role, r['asset_id'], native['index']) for r in refs]
                blocks.extend(pictures)
                if _local(display.elements[native['index'] - 1]) == 'p':
                    display.bind(text, pictures, objects)
                snap = {'index': native['index'], 'text': native['text'], 'images': refs}
                if text.get('presentation',{}).get('eqs'):
                    snap['static_eqs']=deepcopy(text['presentation']['eqs'])
                if text.get('presentation', {}).get('scene') is not None:
                    snap['source_scene'] = deepcopy(text['presentation']['scene'])
                if _local(display.elements[native['index'] - 1]) == 'tbl':
                    snap['table_rows'] = _table_snapshot(display.elements[native['index'] - 1], display.doc, native['text'])
                snapshot_blocks.append(snap)
            snapshot = {'schema': SECTION_SCHEMA, 'outline_revision': request['outline_revision'],
                'section': deepcopy(section), 'blocks': snapshot_blocks}
            section_assets = []
            for a in compiled['image_assets']:
                old = next((x for x in current_assets if x['asset_id'] == a['asset_id']), None)
                chosen = deepcopy(old or {**a, 'caption': request['title'],
                    'source': source['source_name'], 'purpose': '讲义完整栏目原图'})
                verify_image_bytes(chosen, data[chosen['sha256']]); section_assets.append(chosen)
            evidence = {k: source[k] for k in ('source_path', 'source_sha256', 'source_revision')}
            evidence.update(handout_source=deepcopy(source), section_snapshot=snapshot,
                image_assets=section_assets, source_roles={'schema': SOURCE_ROLES_SCHEMA,
                    'teacher_only': request['teacher_only'],
                    'document_role': source['source_role'], 'images': role_records})
            item = resource('handout_knowledge', source['source_name'] + ' · ' + section['title'], blocks, evidence)
            item['title'] = request['title']; item = validate_resource(item)
            if not any(r['original']['kind'] == 'handout_knowledge' and _same_section(r, item)
                    for r in [*current_resources, *items]):
                items.append(item)
                for a in section_assets:
                    assets.setdefault(a['asset_id'], a); payloads[a['sha256']] = data[a['sha256']]
            compiled_rows.append((request, selection, digest(compiled)))
        # Final source/heading/role/quality checks cover changes during a batch.
        for request, selection, expected in compiled_rows:
            outline, _original = _outline(facade, request['source'])
            if not _request_matches_outline(request, outline, _original):
                raise ValueError('讲义栏目在确认期间变化，未导入本批栏目。')
            _check_roles(facade, outline['source'], outline['preview'], request['section'])
            checked, _data, hold = service._compile(selection, include_images=True, include_guidance=False)
            if hold or digest(checked) != expected:
                raise ValueError('讲义原文或图片在确认期间变化，未导入本批栏目。')
        normalize_image_assets([*current_assets, *[a for a in assets.values()
            if a['asset_id'] not in {c['asset_id'] for c in current_assets}]])
        check_read_cancelled()
        merged = _commit_images(Path(facade.paths.task_root) / 'preparation-v1' / 'images',
            list(assets.values()), payloads, current_assets) if items else deepcopy(current_assets)
        return {'resources': items, 'image_assets': merged}

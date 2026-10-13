"""Source-preserving resource cards and independent physical placement.

The composition lives inside the existing lesson draft. Original ordered blocks
remain separate from explicitly unlocked edits. This module performs no reads,
model calls, authority checks, or publication; rendering must measure overflow.
"""
from copy import deepcopy
from hashlib import sha256
import json
import math
import re
from uuid import uuid4

SCHEMA = 'shchem.lesson-composition.v1'
DESIGN_SCHEMA = 'shchem.lesson-design.v3'
RENDER_REVISION = 'shchem.composition-classroom-exposition.v41'
FORMATS = {'A4': (210, 297), 'A3': (297, 420), '16:9': (320, 180)}
MAX_COMPOSITION_RESOURCES = 128
KINDS = {'complete_question', 'textbook_image', 'knowledge_note', 'personal_text', 'lesson_activity', 'lesson_framework', 'preparation_image', 'handout_image', 'handout_knowledge'}
TEACHER_ROLES = {'answer', 'analysis', 'rubric', 'teacher_note'}
ROLES = TEACHER_ROLES | {'shared_material', 'question', 'material', 'knowledge', 'personal', 'student_task'}
FLAGS = {'candidate_only': True, 'human_reviewed': False, 'generation_allowed': False,
         'publication_allowed': False, 'official': False}


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _id(value, prefix):
    return isinstance(value, str) and bool(re.fullmatch(prefix + r'-[a-f0-9]{32}', value))


def _sha(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-f0-9]{64}', value))


def _text(value, limit=120000):
    return isinstance(value, str) and len(value) <= limit


def _blocks(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
        raise ValueError('资源须保留完整的有序文字和图片块。')
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) not in (
                {'id', 'kind', 'role', 'source_index', 'value'},
                {'id', 'kind', 'role', 'source_index', 'value', 'presentation'})
                or not _id(row['id'], 'CB') or row['id'] in seen
                or not isinstance(row['kind'], str) or row['kind'] not in {'text', 'image'}
                or not isinstance(row['role'], str) or row['role'] not in ROLES
                or (row['source_index'] is not None and (type(row['source_index']) is not int or row['source_index'] < 0))):
            raise ValueError('资源内容块的标识、类型或原文位置无效。')
        seen.add(row['id'])
        if row['kind'] == 'text' and not _text(row['value']):
            raise ValueError('资源文字超出草稿容量。')
        if row['kind'] == 'image' and (not isinstance(row['value'], str)
                or not re.fullmatch(r'IMG-[a-f0-9]{64}', row['value'])):
            raise ValueError('资源图片须引用本地素材。')
        if 'presentation' in row:
            p=row['presentation']
            if (row['kind']!='text' or row['source_index'] is None or not isinstance(p,dict)
                    or set(p) not in ({'schema','images'},{'schema','images','scripts'},
                        {'schema','images','scripts','eqs'}, {'schema','images','scripts','scene'}) or p['schema']!='shchem.native-word-paragraph.v1'
                    or not isinstance(p['images'],list) or len(p['images'])>48):
                raise ValueError('原生段落的行内显示依据不完整。')
            scripts=p.get('scripts',[]);end=0
            if not isinstance(scripts,list) or len(scripts)>10000:raise ValueError('原生上下标依据超出容量。')
            for span in scripts:
                if (not isinstance(span,dict) or set(span)!={'start','end','script'}
                        or type(span['start']) is not int or type(span['end']) is not int
                        or not end<=span['start']<span['end']<=120000
                        or span['script'] not in {'subscript','superscript'}):
                    raise ValueError('原生上下标必须保留完整的来源位置。')
                end=span['end']
            for image in p['images']:
                if (not isinstance(image,dict) or set(image) not in ({'image_block_id','placeholder','width_mm','height_mm'},
                        {'image_block_id','placeholder','width_mm','height_mm','transform'})
                        or not _id(image['image_block_id'],'CB')
                        or image['placeholder'] not in {'【待查看原文：图片或图形】','【待查看原文：图片或旧式图形】','【待查看原文：嵌入对象或旧公式】'}):
                    raise ValueError('行内图片必须保留已验证的对象与原文对应关系。')
                if 'transform' in image:
                    from .desktop_handout_paint_preview import validate_picture_transform
                    validate_picture_transform(image['transform'])
                for name in ('width_mm','height_mm'):
                    size=image[name]
                    if size!='unknown' and (type(size) not in (int,float) or not math.isfinite(size) or not 0<size<=10000):
                        raise ValueError('原图显示尺寸缺失时标记unknown，不补猜。')
            markers=re.findall(r'【待查看原文：[^】]*】',row['value'])
            if markers!=[image['placeholder'] for image in p['images']]:
                raise ValueError('文字中的行内图标记须保留；请修改标记前后的文字，不删去原图。')
            if 'eqs' in p:
                from .word_static_eq import validate_spans
                validate_spans(p['eqs'],row['value'],images=p['images'])
            if 'scene' in p:
                from .word_static_quote import validate_scene
                validate_scene(p['scene'], p['scene'].get('text') if isinstance(p['scene'], dict) else None, images=p['images'])


def _inline_bindings(rows):
    by_id={row['id']:row for row in rows};seen=set()
    for row in rows:
        for ref in row.get('presentation',{}).get('images',[]):
            image=by_id.get(ref['image_block_id'])
            if (image is None or image['kind']!='image' or image['role']!=row['role']
                    or image['source_index']!=row['source_index'] or image['id'] in seen):
                raise ValueError('行内图不得跨题块、跨答案范围或重复绑定。')
            seen.add(image['id'])


def block(kind, role, value, source_index=None):
    row = {'id': 'CB-' + uuid4().hex, 'kind': kind, 'role': role,
           'source_index': source_index, 'value': value}
    _blocks([row])
    return row


def _evidence(kind, evidence):
    base = {'source_path', 'source_sha256', 'source_revision'}
    extra = {'complete_question': {'question_key', 'scope_review_sha256', 'scope_status', 'hierarchy'},
             'textbook_image': {'pdf_pages', 'printed_pages'},
             'knowledge_note': {'record_id', 'pdf_pages', 'printed_pages'}, 'personal_text': set(),
             'lesson_activity': {'design_id', 'node_id', 'activity_snapshot'},
             'lesson_framework': {'design_id', 'section', 'framework_snapshot'},
             'preparation_image': {'image_asset', 'source_roles'},
             'handout_image': {'handout_source', 'image_asset', 'source_roles'},
             'handout_knowledge': {'handout_source', 'section_snapshot', 'image_assets', 'source_roles'}}[kind]
    allowed=[base|extra]
    if kind=='knowledge_note':allowed.append(base|extra|{'concept_source'})
    if kind=='textbook_image':allowed.append(base|extra|{'crop_id'})
    if kind=='handout_image':allowed.append(base|extra|{'image_derivation'})
    if not isinstance(evidence, dict) or set(evidence) not in allowed:
        raise ValueError('资源来源字段不完整，不能从文件名补猜。')
    if not _text(evidence['source_path'], 2000) or not evidence['source_path'].strip():
        raise ValueError('资源来源路径或说明缺失。')
    if kind == 'personal_text':
        if evidence['source_sha256'] != 'unknown' or evidence['source_revision'] != 'unknown':
            raise ValueError('手工文字不能伪装成已经核验的来源文件。')
    elif kind == 'lesson_activity':
        from .desktop_composition_activities import validate_activity_snapshot
        snapshot = validate_activity_snapshot(evidence['activity_snapshot'])
        if (evidence['source_sha256'] != 'unknown' or evidence['source_revision'] != digest(snapshot)
                or evidence['design_id'] != snapshot['design_id'] or evidence['node_id'] != snapshot['node']['id']):
            raise ValueError('教学环节须保留实际草稿版本，不能伪造来源文件摘要。')
    elif kind == 'lesson_framework':
        from .desktop_framework_composition import validate_snapshot
        snapshot = validate_snapshot(evidence['framework_snapshot'])
        if (evidence['source_sha256'] != 'unknown' or evidence['source_revision'] != digest(snapshot)
                or evidence['design_id'] != snapshot['design_id'] or evidence['section'] != snapshot['section']):
            raise ValueError('教案栏目须保留当前框架的实际内容和版本。')
    elif not _sha(evidence['source_sha256']) or not _sha(evidence['source_revision']):
        raise ValueError('资源来源摘要和版本缺失。')
    if kind == 'preparation_image':
        from .desktop_composition_images import validate_image_evidence
        validate_image_evidence(evidence)
    if kind == 'handout_image':
        from .desktop_handout_images import validate_handout_evidence
        validate_handout_evidence(evidence)
    if kind == 'handout_knowledge':
        from .desktop_handout_knowledge import validate_handout_knowledge_evidence
        validate_handout_knowledge_evidence(evidence)
    if kind == 'complete_question':
        if (not _text(evidence['question_key'], 300) or not evidence['question_key'].strip()
                or not _sha(evidence['scope_review_sha256']) or evidence['scope_status'] != 'complete_native_scope'):
            raise ValueError('完整题卡须绑定具体题目、来源版本和完整范围复核。')
        fields = {'paper_id', 'theme_big_question_id', 'printed_question_id', 'atomic_part_id'}
        hierarchy = evidence['hierarchy']
        if (not isinstance(hierarchy, dict) or set(hierarchy) != fields
                or any(not _text(v, 300) or not v.strip() for v in hierarchy.values())):
            raise ValueError('题目层级字段须保留；缺失项填写unknown。')
    if kind in {'textbook_image', 'knowledge_note'}:
        if kind == 'textbook_image' and 'crop_id' in evidence and not _sha(evidence['crop_id']):
            raise ValueError('教材裁图标识须保留确切的64位小写摘要。')
        pages = evidence['pdf_pages']
        if not isinstance(pages, list) or not pages or any(type(p) is not int or p <= 0 for p in pages):
            raise ValueError('教材资源须有PDF页码。')
        printed = evidence['printed_pages']
        if printed != 'unknown' and (not isinstance(printed, list) or not printed
                or any(type(p) is not int or p <= 0 for p in printed)):
            raise ValueError('教材印刷页码须核验，缺失填写unknown。')
        if kind == 'knowledge_note' and (not _text(evidence['record_id'], 300) or not evidence['record_id'].strip()):
            raise ValueError('知识卡须绑定具体笔记。')
        if kind=='knowledge_note' and 'concept_source' in evidence:
            from .desktop_textbook_concept_cards import validate_concept_source
            validate_concept_source(evidence)


def resource(kind, title, blocks, evidence):
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError('资源类型无法识别。')
    _blocks(blocks); _evidence(kind, evidence)
    original = {'kind': kind, 'title': title, 'blocks': deepcopy(blocks), 'evidence': deepcopy(evidence)}
    value = {'id': 'CR-' + uuid4().hex, 'original': original, 'original_sha256': digest(original),
             'title': title, 'blocks': deepcopy(blocks), 'locked': kind != 'personal_text', **FLAGS}
    if kind == 'lesson_activity':
        value['activity_link'] = {'design_id': evidence['design_id'], 'node_id': evidence['node_id'], 'mode': 'linked'}
    if kind == 'lesson_framework':
        value['framework_link'] = {'design_id': evidence['design_id'], 'section': evidence['section'], 'mode': 'linked'}
    validate_resource(value)
    return value


def validate_resource(value):
    fields = {'id', 'original', 'original_sha256', 'title', 'blocks', 'locked', *FLAGS}
    if isinstance(value, dict) and 'exposition' in value:
        fields.add('exposition')
    if isinstance(value, dict) and 'image_display' in value:
        fields.add('image_display')
    if isinstance(value, dict) and 'working_tables' in value:
        fields.add('working_tables')
    if isinstance(value, dict) and 'question_answer_reveal' in value:
        fields.add('question_answer_reveal')
    if isinstance(value, dict) and isinstance(value.get('original'), dict) and value['original'].get('kind') == 'lesson_activity':
        fields.add('activity_link')
    if isinstance(value, dict) and isinstance(value.get('original'), dict) and value['original'].get('kind') == 'lesson_framework':
        fields.add('framework_link')
    if not isinstance(value, dict) or set(value) != fields or not _id(value['id'], 'CR'):
        raise ValueError('编排资源字段不完整。')
    original = value['original']
    original_fields = {'kind', 'title', 'blocks', 'evidence'}
    if isinstance(original, dict) and 'selected_from_knowledge' in original:
        original_fields.add('selected_from_knowledge')
    if isinstance(original, dict) and 'selected_from_preparation' in original:
        original_fields.add('selected_from_preparation')
    if (not isinstance(original, dict) or set(original) != original_fields
            or not isinstance(original['kind'], str) or original['kind'] not in KINDS
            or not _text(original['title'], 2000)
            or value['original_sha256'] != digest(original)):
        raise ValueError('原资源快照摘要失配，请核对来源。')
    _evidence(original['kind'], original['evidence']); _blocks(original['blocks']); _blocks(value['blocks'])
    if original['kind'] != 'handout_knowledge' and any('transform' in r
            for b in original['blocks'] for r in b.get('presentation', {}).get('images', [])):
        raise ValueError('原图显示变换须由完整讲义栏目的来源证据绑定。')
    if original['kind'] != 'handout_knowledge' and any('scene' in b.get('presentation', {}) for b in original['blocks']):
        raise ValueError('原Word图片域场景须由完整讲义栏目的原始来源绑定。')
    from .word_static_eq import validate_spans
    for row in original['blocks']:
        if 'eqs' in row.get('presentation',{}):
            validate_spans(row['presentation']['eqs'],row['value'],images=row['presentation']['images'],original=True)
        if 'scene' in row.get('presentation', {}):
            from .word_static_quote import validate_scene
            validate_scene(row['presentation']['scene'], row['value'], images=row['presentation']['images'])
    if 'exposition' in value:
        from .desktop_composition_exposition import validate_exposition
        validate_exposition(value)
    if 'image_display' in value:
        from .desktop_composition_image_display import validate_image_display
        validate_image_display(value)
    if original['kind']=='knowledge_note' and 'concept_source' in original['evidence']:
        record=original['evidence']['concept_source']['record']
        if (original['title']!=record['title'] or
                [b['value'] for b in original['blocks'] if b['role']=='knowledge']!=[record['statement']]):
            raise ValueError('教材摘要卡的原内容须与来源记录一致；修改请编辑作品副本。')
    if 'selected_from_knowledge' in original:
        _validate_knowledge_selection(original)
    if 'selected_from_preparation' in original:
        from .desktop_saved_preparation_images import validate_saved_preparation_resource
        validate_saved_preparation_resource(original)
    _inline_bindings(original['blocks']); _inline_bindings(value['blocks'])
    allowed_roles = {'complete_question': {'shared_material', 'question', *TEACHER_ROLES},
                     'textbook_image': {'material', 'teacher_note'},
                     'knowledge_note': {'knowledge', 'teacher_note'},
                     'personal_text': {'personal', 'teacher_note'},
                     'lesson_activity': {'student_task', 'material', 'teacher_note', 'answer'},
                     'lesson_framework': {'material', 'teacher_note'},
                     'preparation_image': {'material', 'teacher_note'},
                     'handout_image': {'material', 'teacher_note'},
                     'handout_knowledge': {'knowledge', 'teacher_note'}}[original['kind']]
    if any(row['role'] not in allowed_roles for row in original['blocks']):
        raise ValueError('资源内容块与资料类型不对应。')
    if original['kind'] == 'complete_question' and (
            not any(row['role'] == 'question' and row['value'] for row in original['blocks'])
            or any(row['source_index'] is None for row in original['blocks'])):
        raise ValueError('完整题卡须保留题面及原始区块位置。')
    if not _text(value['title'], 2000) or type(value['locked']) is not bool:
        raise ValueError('资源标题或锁定状态无效。')
    # Edits cannot drop conditions or move answer blocks into student scope.
    identity = lambda row: (row['id'], row['kind'], row['role'], row['source_index'], row.get('presentation'),
                            row['value'] if row['kind']=='image' else None)
    if [identity(row) for row in original['blocks']] != [identity(row) for row in value['blocks']]:
        raise ValueError('资源原有块的顺序、范围和作答可见类型不能被静默替换。')
    if any(type(value[k]) is not bool or value[k] is not v for k, v in FLAGS.items()):
        raise ValueError('本地资源编排不解锁正式、人审或发布资格。')
    if original['kind'] == 'lesson_activity':
        from .desktop_composition_activities import validate_activity_resource
        validate_activity_resource(value)
    if original['kind'] == 'lesson_framework':
        from .desktop_framework_composition import validate_framework_resource
        validate_framework_resource(value)
    if original['kind'] == 'preparation_image':
        from .desktop_composition_images import validate_image_resource
        validate_image_resource(value)
    if original['kind'] == 'handout_image':
        from .desktop_handout_images import validate_handout_resource
        validate_handout_resource(value)
    if original['kind'] == 'handout_knowledge':
        from .desktop_handout_knowledge import validate_handout_knowledge_resource
        validate_handout_knowledge_resource(value)
    if 'working_tables' in value:
        from .desktop_handout_working_tables import validate_working_tables
        validate_working_tables(value)
    if 'question_answer_reveal' in value:
        from .desktop_question_answer_reveal import validate_question_answer_reveal
        validate_question_answer_reveal(value)
    return deepcopy(value)


def _validate_knowledge_selection(original):
    choice = original['selected_from_knowledge']
    fields = {'resource_id', 'title', 'record_id', 'original_sha256', 'source_sha256', 'source_revision'}
    if (original['kind'] != 'textbook_image' or not isinstance(choice, dict) or set(choice) != fields
            or not _id(choice['resource_id'], 'CR') or not _text(choice['title'], 2000)
            or not _text(choice['record_id'], 300) or not choice['record_id'].strip()
            or not all(_sha(choice[k]) for k in ('original_sha256', 'source_sha256', 'source_revision'))
            or choice['source_sha256'] != original['evidence']['source_sha256']):
        raise ValueError('选图所用知识卡的来源快照失配，请重新从知识卡选图。')


def bind_knowledge_selection(image, card):
    """Record the explicit entry-point choice, without asserting chemical relevance."""
    image = validate_resource(image); card = validate_resource(card)
    if image['original']['kind'] != 'textbook_image' or card['original']['kind'] != 'knowledge_note':
        raise ValueError('选图来源须为知识卡，目标须为教材裁片。')
    evidence = card['original']['evidence']
    image['original']['selected_from_knowledge'] = {
        'resource_id': card['id'], 'title': card['original']['title'],
        'record_id': evidence['record_id'], 'original_sha256': card['original_sha256'],
        'source_sha256': evidence['source_sha256'], 'source_revision': evidence['source_revision']}
    image['original_sha256'] = digest(image['original'])
    return validate_resource(image)


def new_composition(format='A4'):
    if not isinstance(format, str) or format not in FORMATS:
        raise ValueError('画幅须为A4、A3或16:9。')
    return {'schema_version': SCHEMA, 'format': format,
            'pages': ['CP-' + uuid4().hex], 'resources': [], 'placements': {}}


def validate_composition(value):
    required = {'schema_version', 'format', 'pages', 'resources', 'placements'}
    if (not isinstance(value, dict) or not required <= set(value) <= required | {'student_arrangement', 'click_reveals'}
            or value['schema_version'] != SCHEMA or not isinstance(value['format'], str)
            or value['format'] not in FORMATS):
        raise ValueError('页面编排字段或画幅无效。')
    if 'student_arrangement' in value:
        policy = value['student_arrangement']
        if (not isinstance(policy, dict) or set(policy) != {'columns'}
                or type(policy['columns']) is not int or policy['columns'] not in (1, 2)):
            raise ValueError('学生版单独编排须选择一栏或两栏。')
    pages = value['pages']
    if (not isinstance(pages, list) or not 1 <= len(pages) <= 160
            or any(not _id(p, 'CP') for p in pages) or len(set(pages)) != len(pages)):
        raise ValueError('页面标识无效。')
    rows = value['resources']
    if not isinstance(rows, list) or len(rows) > MAX_COMPOSITION_RESOURCES:
        raise ValueError(f'每份课件最多{MAX_COMPOSITION_RESOURCES}项素材，请按课时拆分；原内容须保留。')
    for row in rows: validate_resource(row)
    links = [(r['activity_link']['design_id'], r['activity_link']['node_id']) for r in rows
             if r['original']['kind'] == 'lesson_activity' and r['activity_link']['mode'] == 'linked']
    if len(links) != len(set(links)):
        raise ValueError('同一教学环节已经关联画布；请更新已有素材，或明确保留独立副本。')
    frameworks = [(r['framework_link']['design_id'], r['framework_link']['section']) for r in rows
                  if r['original']['kind'] == 'lesson_framework']
    if len(frameworks) != len(set(frameworks)):
        raise ValueError('同一教案栏目已经加入课件，请同步已有栏目。')
    ids = [row['id'] for row in rows]
    if len(set(ids)) != len(ids) or not isinstance(value['placements'], dict) or set(value['placements']) != set(ids):
        raise ValueError('资源位置须与资源标识逐一对应。')
    if 'click_reveals' in value:
        reveals = value['click_reveals']
        if (value['format'] != '16:9' or not isinstance(reveals, list)
                or any(not isinstance(identity, str) for identity in reveals)
                or len(reveals) != len(set(reveals)) or not set(reveals) <= set(ids)):
            raise ValueError('课堂点击显示须引用当前16:9课件中的素材。')
        if any(not any(b['role'] not in TEACHER_ROLES and b['value'] for b in r['blocks'])
               for r in rows if r['id'] in reveals):
            raise ValueError('点击显示需要课堂可见的讲解素材；教师原答案仍保留在教师文档。')
        if any('question_answer_reveal' in r for r in rows if r['id'] in reveals):
            raise ValueError('整题同页的题面须直接可见，不能同时设置整份素材点击显示。')
    width, height = FORMATS[value['format']]
    for placement in value['placements'].values():
        if not isinstance(placement, dict) or set(placement) not in (
                {'page_id', 'x', 'y', 'width', 'height'},
                {'page_id', 'x', 'y', 'width', 'height', 'continuations'}):
            raise ValueError('资源位置或尺寸无效。')
        tails = placement.get('continuations', [])
        if not isinstance(tails, list) or len(tails) > 159:
            raise ValueError('续页外框无效。')
        frames = [{k: v for k, v in placement.items() if k != 'continuations'}, *tails]
        frame_pages = set()
        for frame in frames:
            _validate_frame(frame, pages, width, height)
            if frame['page_id'] in frame_pages:
                raise ValueError('同一资源的续页须分配在不同页。')
            frame_pages.add(frame['page_id'])
    return deepcopy(value)


def _validate_frame(placement, pages, width, height):
        if (not isinstance(placement, dict) or set(placement) != {'page_id', 'x', 'y', 'width', 'height'}
                or placement['page_id'] not in pages
                or any(type(placement[k]) not in (int, float) or not math.isfinite(placement[k])
                       for k in ('x', 'y', 'width', 'height'))):
            raise ValueError('资源位置或尺寸无效。')
        x, y, w, h = (placement[k] for k in ('x', 'y', 'width', 'height'))
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
            raise ValueError('资源外框超出纸面；完整内容仍须由排版核对是否溢出。')


def resource_frames(placement):
    return [{k: v for k, v in placement.items() if k != 'continuations'},
            *deepcopy(placement.get('continuations', []))]


def add_resource(value, item, placement):
    result = validate_composition(value)
    item = validate_resource(item)
    result['resources'].append(item); result['placements'][item['id']] = deepcopy(placement)
    return validate_composition(result)


def edit_resource(value, identity, *, title=None, values=None, table_cells=None, locked=None, expected=None):
    result = validate_composition(value)
    if expected is not None and digest(result) != expected:
        raise ValueError('编排已修改，请重新核对当前版本。')
    item = next((row for row in result['resources'] if row['id'] == identity), None)
    if item is None: raise ValueError('资源已移除。')
    if (values or table_cells) and item['locked']:
        raise ValueError('原资源已锁定，请先明确解锁作品副本。')
    if table_cells is not None:
        if not isinstance(table_cells, dict):
            raise ValueError('工作表格编辑须按原表文字区块标识提交。')
        if isinstance(values, dict) and set(values) & set(table_cells):
            raise ValueError('同一表格不能同时提交整段文字和单元格编辑。')
    if title is not None: item['title'] = title
    if values is not None:
        if not isinstance(values, dict) or not set(values) <= {row['id'] for row in item['blocks']}:
            raise ValueError('编辑引用了不存在的原内容块。')
        for row in item['blocks']:
            if row['id'] in values: row['value'] = values[row['id']]
    if table_cells is not None:
        from .desktop_handout_working_tables import _apply_table_cells
        _apply_table_cells(item, table_cells)
    if locked is not None: item['locked'] = locked
    return validate_composition(result)


def suggested_resource_title(item):
    """Give a new source question short navigation; retain authored work titles."""
    item = validate_resource(item)
    original = item['original']
    if original['kind'] != 'complete_question' or item['title'] != original['title']:
        return item['title']
    from .desktop_question_classroom_copy import _working_title
    return _working_title(item)


def place_resource(value, identity, placement, *, expected=None):
    result = validate_composition(value)
    if expected is not None and digest(result) != expected:
        raise ValueError('编排已修改，请重新核对当前版本。')
    if identity not in result['placements']: raise ValueError('资源已移除。')
    result['placements'][identity] = deepcopy(placement)
    item = next(r for r in result['resources'] if r['id'] == identity)
    if (result['format'] == '16:9' and 'question_answer_reveal' in item
            and isinstance(placement, dict) and {'x', 'y', 'width', 'height'} <= set(placement)):
        item['question_answer_reveal']['classroom_frame'] = {
            k: placement[k] for k in ('x', 'y', 'width', 'height')}
    return validate_composition(result)


def reorder_resources(value, identities):
    result = validate_composition(value)
    rows = {row['id']: row for row in result['resources']}
    if not isinstance(identities, list) or len(identities) != len(rows) or set(identities) != set(rows):
        raise ValueError('资源顺序必须保留每一项。')
    result['resources'] = [rows[key] for key in identities]
    return result


def visible_blocks(item, *, student=True):
    item = validate_resource(item)
    from .desktop_teaching_framework import classroom_blocks
    rows = [deepcopy(row) for row in item['blocks'] if not student or row['role'] not in TEACHER_ROLES]
    return classroom_blocks(item, rows)


def set_student_arrangement(value, columns=None):
    """Save an audience layout choice without replacing teacher frames or source."""
    result = validate_composition(value)
    if columns is None:
        result.pop('student_arrangement', None)
    else:
        result['student_arrangement'] = {'columns': columns}
    return validate_composition(result)


def set_click_reveal(value, identity, enabled):
    """Explicit classroom display choice; never changes source or answer roles."""
    result = validate_composition(value)
    if type(enabled) is not bool or identity not in result['placements']:
        raise ValueError('请选择当前作品中的素材。')
    reveals = list(result.get('click_reveals', []))
    if enabled and identity not in reveals:
        reveals.append(identity)
    if not enabled and identity in reveals:
        reveals.remove(identity)
    if reveals:
        result['click_reveals'] = reveals
    else:
        result.pop('click_reveals', None)
    return validate_composition(result)


def enable_composition(plan):
    from .desktop_lesson_design import validate_design
    result = validate_design(plan)
    if result['schema_version'] == DESIGN_SCHEMA: return result
    result.update(schema_version=DESIGN_SCHEMA, composition=new_composition())
    return validate_design(result)

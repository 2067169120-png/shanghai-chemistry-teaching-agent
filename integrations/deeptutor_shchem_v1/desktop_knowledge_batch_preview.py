"""Complete knowledge-card preview and source recheck without lesson writes."""
from .desktop_composition_sources import import_knowledge_cards
from .desktop_lesson_composition import digest, validate_resource, visible_blocks
from .desktop_teaching_framework import classroom_text


def batch_signature(result):
    if (not isinstance(result, dict) or set(result) != {'resources'}
            or not isinstance(result['resources'], list) or not 1 <= len(result['resources']) <= 30):
        raise ValueError('每次请选择1至30张知识卡。')
    rows = []
    for item in result['resources']:
        value = validate_resource(item)
        if value['original']['kind'] != 'knowledge_note':
            raise ValueError('此预览只接收教材知识卡。')
        # Each import allocates fresh work-copy IDs. Compare all source/content
        # fields in order, excluding only those opaque block/resource IDs.
        source = value['original']
        rows.append({'kind': source['kind'], 'source_title': source['title'],
            'evidence': source['evidence'], 'title': value['title'], 'locked': value['locked'],
            'source_blocks': [{k:v for k,v in b.items() if k != 'id'} for b in source['blocks']],
            'work_blocks': [{k:v for k,v in b.items() if k != 'id'} for b in value['blocks']]})
    return digest(rows)


def preview_batch(facade, selections):
    result = import_knowledge_cards(facade, selections)
    return {'result': result, 'signature': batch_signature(result)}


def recheck_batch(facade, selections, expected_signature):
    value = preview_batch(facade, selections)
    if value['signature'] != expected_signature:
        raise ValueError('知识卡内容或来源版本在预览后变化，请重新预览。')
    return value['result']


def preview_text(result):
    batch_signature(result)
    lines = []
    for index, item in enumerate(result['resources'], 1):
        lines.append(f"{index}. {classroom_text(item['title'])}")
        lines.extend(row['value'] for row in visible_blocks(item, student=True) if row['kind'] == 'text')
        lines.append('')
    return '\n\n'.join(lines)

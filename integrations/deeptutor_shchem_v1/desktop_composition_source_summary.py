"""Teacher-facing source reading without changing sealed material or evidence."""
from __future__ import annotations

import json

_KINDS = {'complete_question': '完整题', 'textbook_image': '教材图',
          'knowledge_note': '知识卡', 'personal_text': '个人文字', 'lesson_activity': '教学环节',
          'lesson_framework': '教案栏目', 'preparation_image': '教学图片', 'handout_image': '讲义原图与知识网',
          'handout_knowledge': '讲义知识段'}
_ROLES = {'shared_material': '公共材料', 'question': '题面', 'answer': '教师答案与解析',
          'analysis': '教师解析', 'rubric': '教师评分说明', 'teacher_note': '教师备注',
          'material': '教材素材', 'knowledge': '知识内容', 'personal': '个人文字',
          'student_task': '学生任务'}


def _pages(values):
    if not isinstance(values, (list, tuple)) or not values:
        return '待核对'
    if any(type(v) is not int or v < 1 for v in values):
        return '待核对'
    ordered = sorted(set(values)); spans = []; start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
        else:
            spans.append(str(start) if start == previous else f'{start}—{previous}')
            start = previous = value
    spans.append(str(start) if start == previous else f'{start}—{previous}')
    return '、'.join(spans)


def source_summary(item):
    """Use explicit source titles and source indices; never infer paper hierarchy."""
    original = item['original']; evidence = original['evidence']; blocks = original['blocks']
    kind = original['kind']; title = original.get('title') or '待核对'
    lines = [f"素材类型：{_KINDS.get(kind, '来源素材')}", f'原资料标题：{title}']
    if kind == 'complete_question':
        for roles, heading in (({'shared_material'}, '公共材料'), ({'question'}, '题面'),
                               ({'answer', 'analysis', 'rubric'}, '教师答案与解析')):
            relevant = [b for b in blocks if b['role'] in roles]
            indices = [b['source_index'] for b in relevant if type(b.get('source_index')) is int]
            lines.append(heading + '：' + ('原文区块' + _pages(indices) if indices else
                                           '有内容，区块位置待核对' if relevant else '此素材未记录'))
        image_count = sum(b['kind'] == 'image' for b in blocks)
        lines.append(f'来源图片：{image_count}处，按题面或教师答案所属位置保留')
        lines.append('原文区块表示Word中的位置，不是印刷题号；整卷、主题、小题与作答单元按原卷另核。')
    elif kind in {'textbook_image', 'knowledge_note'}:
        lines.extend(('PDF文件页：' + _pages(evidence.get('pdf_pages')),
                      '印刷页：' + _pages(evidence.get('printed_pages'))))
        choice = original.get('selected_from_knowledge')
        if choice:
            lines.append('选图来源知识卡：' + choice['title'] + '（保留所选版本；用途待教师核对）')
        if kind == 'knowledge_note':
            if 'concept_source' in evidence:
                lines.append('教材知识摘要：保留原知识记录、目录版本及章节或补充栏目归属；核验信息在来源详情中查看。')
            lines.append('知识内容及适用范围可在“阅读／编辑完整素材”查看；选用不会自动完成教师核对。')
        else:
            lines.append('原页裁选图片保留在当前作品中；图题及使用说明可在完整素材中核对。')
    elif kind in {'preparation_image', 'handout_image'}:
        asset = evidence['image_asset']
        lines.append(f"原图尺寸：{asset['width']} × {asset['height']} 像素")
        lines.append('原图片图注：' + asset['caption'])
        lines.append('图片与来源记录保存在本课；本次图题和课堂用途可编辑作品副本。')
        if kind == 'handout_image':
            source = evidence['handout_source']
            lines.append('原讲义：' + source['source_name'] + f"；Word原文区块{source['block_index']}")
    elif kind == 'handout_knowledge':
        section = evidence['section_snapshot']['section']
        lines.append('原栏目：' + section['title'])
        lines.append(f"Word原文区块：{section['start']}—{section['end']}（包含首尾）")
        lines.append(f"完整原文段落：{len(evidence['section_snapshot']['blocks'])}；原图：{len(evidence['image_assets'])}张")
    elif kind == 'personal_text':
        lines.append('作品中的个人文字；在完整素材中阅读或明确解锁后修改本作品副本。')
    elif kind == 'lesson_framework':
        lines.append('来自当前作品的教案框架；目标和导入可放在课件开头，小结、板书和课后任务放在结尾。')
        lines.append('已保留课件副本，可在同步预览中选择替换。' if item['framework_link']['mode'] == 'snapshot'
                     else '栏目保持联动；应用框架、保存或生成时更新已关联内容，单独编辑的课件文字会保留。')
    lines.append('原资源快照保留；修改作品副本后，来源快照仍可对照。')
    return '\n\n'.join(lines)


def source_snapshot_text(item):
    """Read all original text in order, including teacher-only blocks, without truncation."""
    lines = [source_summary(item), '原资源内容（含教师内容，供备课核对）']
    image_number = 0
    for row in item['original']['blocks']:
        heading = _ROLES.get(row['role'], '来源内容')
        if type(row.get('source_index')) is int:
            heading += f" · 原文区块{row['source_index']}"
        if row['kind'] == 'image':
            image_number += 1
            text = f'原图{image_number}，可在完整素材及画布核对。'
        else:
            text = row['value']
        lines.append(heading + '\n' + text)
    return '\n\n'.join(lines)


def raw_source_text(item, *, full=False):
    """Retain every exact field for explicit evidence inspection."""
    return json.dumps(item['original'] if full else item['original']['evidence'],
                      ensure_ascii=False, indent=2)

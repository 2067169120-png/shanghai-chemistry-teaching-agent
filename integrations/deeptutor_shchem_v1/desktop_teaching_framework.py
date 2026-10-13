"""Editable lesson headings and classroom text, separate from audit evidence."""
from copy import deepcopy
from pathlib import Path
import re

SCHEMA = 'shchem.teaching-framework.v1'
FIELDS = (
    ('knowledge_skills', '知识与技能'), ('process_methods', '过程与方法'),
    ('values', '情感态度与价值观'), ('focus', '教学重点'), ('difficulty', '教学难点'),
    ('methods', '教学方法'), ('introduction', '导入新课'),
    ('consolidation', '巩固小结'), ('homework', '布置作业'), ('board_design', '板书设计'))
HEADINGS = ('一、教学目标', '二、教学重点/难点', '三、教学方法', '四、教学过程', '五、板书设计')


def validate_framework(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', *(k for k, _ in FIELDS)}
            or value['schema_version'] != SCHEMA
            or any(not isinstance(value[k], str) or len(value[k]) > 12000 for k, _ in FIELDS)):
        raise ValueError('教案框架字段不完整或文字过长。')
    return deepcopy(value)


def classroom_text(value):
    """Remove review/disclaimer boilerplate from generated teaching text.

    This is a display projection. It never edits the saved source or changes
    review flags, and is not applied to original complete-question blocks.
    """
    if not isinstance(value, str):
        return value
    text = re.sub(r'^[ \t]*适用范围与待核对处[：:]', '适用范围：', value, flags=re.M)
    text = text.replace('个人整理笔记；印刷页码与化学内容仍须对照原书核对。', '')
    text = re.sub(r'核对提示[：:]', '比较条件：', text)
    text = text.replace('本地备课候选', '').replace('AI辅助活动建议。', '')
    # A number or claim explicitly awaiting verification is omitted, rather
    # than made definite by deleting its qualifier.
    text = ''.join(sentence for sentence in re.split(r'(?<=[。！？\n])', text)
                   if not re.search(r'待核实|待核验|尚无教师人工复核|原PDF文字提取|可能漏掉图片|'
                                    r'未作独立化学审校|化学审校仍待|AI辅助候选|本卡保留原页结论|须另读原图', sentence))
    text = ''.join(sentence for sentence in re.split(r'(?<=[。！？\n])', text)
                   if not re.search(r'(?:AI|人工智能)[^。！？\n]{0,60}(?:可能有误|可能存在错误|可能不准确|可能存在偏差)|'
                                    r'(?:不保证|无法保证|不能保证)[^。！？\n]{0,20}(?:准确性|正确性|完整性)|'
                                    r'(?:不替代|不能替代|不能代替)[^。！？\n]{0,20}(?:教师|专业)(?:判断|审查|审核)',
                                    sentence, flags=re.I))
    # An appended disclaimer is removable; chemical limitations remain intact.
    text = re.sub(r'(?:[，,；;]\s*)?(?:本(?:内容|教案|课件|材料|文档|结果|方案|资源)\s*)?'
                  r'仅供(?:教学|课堂|教师|学习)?参考(?:[，,]\s*(?:请|由)教师(?:自行)?(?:检查|核查|核对|审核|确认)(?:后使用)?)?[。.!！]?', '', text)
    text = re.sub(r'(?:[，,；;]\s*)?(?:课前|使用前|授课前)?(?:请|建议|需要|需由|应由)教师'
                  r'(?:自行|自己|逐项|逐条|逐页|进一步)?(?:检查|核查|核对|核实|核验|复核|审核|确认)'
                  r'(?:本(?:内容|教案|课件|材料)|内容(?:的)?(?:准确性|正确性)|后(?:使用|授课))?[。.!！]?', '', text)
    text = re.sub(r'本地整理候选[，,；;\s]*待教师核对[。.!！]?', '', text)
    text = re.sub(r'[（(][^（）()\n]*(?:待核实|待核验|待(?:教师)?核对|待(?:教师)?确认|待教师复核)[^（）()\n]*[）)]', '', text)
    # Remove only a review clause, rather than the chemical sentence before it.
    text = re.sub(r'(?:[，,；;]\s*)?(?:答案由教师|仍?需(?:由)?教师|仍?须(?:由)?教师|请教师|待教师|仍?待)(?:逐条|逐字|逐页|对照原(?:书|页)|重新)?(?:核对|核实|核验|确认|复核|审查)(?:后再(?:用于教学|使用))?[。.!！]?', '', text)
    text = re.sub(r'(?:待核实|待核验|待核对|待确认)[。.!！]?', '', text)
    return '\n'.join(line.rstrip() for line in text.splitlines()).strip()


def classroom_blocks(item, rows):
    if item['original']['kind'] not in {'knowledge_note', 'lesson_activity', 'lesson_framework'}:
        return rows
    projected = []
    for row in rows:
        row = deepcopy(row)
        if row['kind'] == 'text' and 'presentation' not in row:
            if row['value'].startswith(('原页依据（待核对）：', '原页依据：', '课堂核对建议：')):
                continue
            row['value'] = classroom_text(row['value'])
            if row['value'] in {'', '适用范围：', '适用范围与处：'}:
                continue
        projected.append(row)
    return projected


def classroom_candidate(value):
    """Keep uncertainties in candidate JSON; omit them from classroom artifacts."""
    result = deepcopy(value)
    def project(row):
        if isinstance(row, str):
            return classroom_text(row)
        if isinstance(row, list):
            return [project(v) for v in row]
        if isinstance(row, dict):
            return {k: deepcopy(v) if k in {'uncertainties', 'source_basis', 'image_assets', 'image',
                                           'material_text', 'teacher_answer'} else project(v)
                    for k, v in row.items()}
        return row
    return project(result)


def framework_from_design(payload):
    plan = payload['lesson_design']
    if 'teaching_framework' in plan:
        return validate_framework(plan['teaching_framework'])
    rows = [{'statement': g['text']} for g in plan['objectives']]
    activities = [{'title': n['title'], 'student_action': n['student_task'],
                   'teacher_action': n['teacher_action'], 'kind': n['kind']} for n in plan['nodes']]
    return framework_from_candidate({'objectives': rows, 'activities': activities})


def framework_from_candidate(candidate):
    result = {'schema_version': SCHEMA, **{k: '' for k, _ in FIELDS}}
    goals = {k: [] for k in ('knowledge_skills', 'process_methods', 'values')}
    for goal in candidate.get('objectives', []):
        text = classroom_text(goal.get('statement', ''))
        category = 'knowledge_skills'
        for key, label in FIELDS[:3]:
            if re.match(r'^' + re.escape(label) + r'\s*[：:]', text):
                category, text = key, re.sub(r'^' + re.escape(label) + r'\s*[：:]\s*', '', text)
                break
        if text: goals[category].append(text)
    result.update({k: '\n'.join(v) for k, v in goals.items()})
    activities = candidate.get('activities', [])
    method_names = {'课前诊断': '提问法', '观察与提问': '观察与提问法',
                    '解释与建模': '讲授与启发引导法', '例题讲解': '例题分析法',
                    '独立练习': '练习法', '反馈与小结': '讨论与归纳法'}
    result['methods'] = '、'.join(dict.fromkeys(method_names[a['kind']]
        for a in activities if a.get('kind') in method_names))
    if not result['process_methods'] and result['methods']:
        result['process_methods'] = '通过' + result['methods'] + '完成本课任务，交流并说明判断依据。'
    for slide in candidate.get('slides', []):
        notes = slide.get('teacher_notes', '')
        for key, label in (('focus', '教学重点'), ('difficulty', '教学难点'), ('methods', '教学方法')):
            match = re.search(r'(?:^|\n)' + label + r'[：:]\s*([^\n]+)', notes)
            if match: result[key] = classroom_text(match[1])
        if '板书' in slide.get('title', ''):
            result['board_design'] = '\n'.join(slide.get('content', []))
    result['homework'] = '\n'.join(task.get('instruction', '') if isinstance(task, dict) else task
        for task in candidate.get('homework', {}).get('tasks', []))
    return validate_framework(result)


def add_framework_opening(document, framework):
    framework = validate_framework(framework)
    document.add_heading(HEADINGS[0], 1)
    for index, (key, label) in enumerate(FIELDS[:3], 1):
        document.add_heading(f'{index}. {label}', 2)
        text = classroom_text(framework[key])
        if text: document.add_paragraph(text)
    document.add_heading(HEADINGS[1], 1)
    for index, (key, label) in enumerate(FIELDS[3:5], 1):
        document.add_heading(f'{index}. {label}', 2)
        text = classroom_text(framework[key])
        if text: document.add_paragraph(text)
    document.add_heading(HEADINGS[2], 1)
    if framework['methods']: document.add_paragraph(classroom_text(framework['methods']))


def teaching_source_citation(item):
    evidence = item['original']['evidence']
    name = Path(evidence['source_path']).name if evidence.get('source_path') not in {'', 'unknown', None} else ''
    pages = evidence.get('pdf_pages')
    page_text = ' · PDF第' + '、'.join(map(str, pages)) + '页' if isinstance(pages, list) and pages else ''
    return ('来源：' + name + page_text) if name else ''

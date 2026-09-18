"""Deterministic exam statistics. No model, executable spreadsheet or student DB writes."""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import posixpath
import re
import statistics
import uuid
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile


class ExamError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.message_zh = message


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _xml(data):
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise ExamError('表格含不支持的XML声明，请另存为普通xlsx。')
    return ET.fromstring(data)


def _tag(element):
    return element.tag.rsplit('}', 1)[-1]


def _children(element, name):
    return [e for e in element if _tag(e) == name]


def column_name(index):
    out = ''
    while index >= 0:
        index, rem = divmod(index, 26)
        out = chr(65 + rem) + out
        index -= 1
    return out


def _column(reference):
    m = re.fullmatch(r'([A-Z]+)([1-9]\d*)', reference or '')
    if not m:
        raise ExamError('工作表含无法识别的单元格坐标。')
    n = 0
    for c in m[1]:
        n = n * 26 + ord(c) - 64
    return n - 1, int(m[2])


def display(value):
    if isinstance(value, dict):
        return '【' + value.get('error', '需核对') + '】'
    if value is None:
        return ''
    return str(value)


def read_xlsx(path):
    """Read saved XLSX values only, including sparse and shared-string worksheets.

    Formula caches are used with an explicit warning. A missing cache/error is
    an invalid score, never zero. No recalculation, macros, URLs or Office launch.
    """
    path = Path(path)
    if path.suffix.lower() != '.xlsx':
        raise ExamError('请使用.xlsx成绩表；旧.xls、宏表和加密文件请在Excel中另存为普通xlsx。')
    try:
        if path.stat().st_size > 40 * 1024 * 1024:
            raise ExamError('成绩表超过40MB，请只保留本次考试工作表后再导入。')
        raw = path.read_bytes()
        from io import BytesIO
        with ZipFile(BytesIO(raw)) as z:
            infos = z.infolist()
            if len(infos) > 5000 or sum(x.file_size for x in infos) > 150 * 1024 * 1024:
                raise ExamError('成绩表展开后过大，请精简工作表。')
            if len({x.filename for x in infos}) != len(infos):
                raise ExamError('成绩文件含重复内部文件，请重新另存。')
            def get(name):
                if z.getinfo(name).file_size > 50 * 1024 * 1024:
                    raise ExamError('单个工作表过大。')
                return _xml(z.read(name))
            book = get('xl/workbook.xml')
            rels = {r.attrib['Id']: r.attrib for r in get('xl/_rels/workbook.xml.rels')}
            strings = []
            if 'xl/sharedStrings.xml' in z.namelist():
                for si in get('xl/sharedStrings.xml'):
                    strings.append(''.join(t.text or '' for t in si.iter() if _tag(t) == 't'))
            sheets = []
            for s in book.iter():
                if _tag(s) != 'sheet':
                    continue
                rid = next((v for k, v in s.attrib.items() if k.endswith('}id')), None)
                rel = rels.get(rid, {})
                if rel.get('TargetMode') == 'External' or not rel.get('Type', '').endswith('/worksheet'):
                    continue
                target = rel.get('Target', '')
                target = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('xl', target))
                if not target.startswith('xl/'):
                    raise ExamError('成绩表内部引用不正确。')
                grid, formula_cells, hidden_rows = {}, [], []
                for row in get(target).iter():
                    if _tag(row) != 'row':
                        continue
                    rn = int(row.attrib.get('r', '0'))
                    if row.attrib.get('hidden') == '1':
                        hidden_rows.append(rn)
                    for cell in row:
                        if _tag(cell) != 'c':
                            continue
                        col, number = _column(cell.attrib.get('r'))
                        # Ignore style-only cells without mistaking formatting to row 1M for data.
                        vals = _children(cell, 'v'); forms = _children(cell, 'f')
                        ins = _children(cell, 'is')
                        if not vals and not forms and not ins:
                            continue
                        if number > 10000 or col >= 256:
                            raise ExamError('本版每张表最多10000行、256列。请拆分考试数据。')
                        text = vals[0].text if vals else None
                        kind = cell.attrib.get('t', '')
                        if forms:
                            formula_cells.append(cell.attrib['r'])
                        if forms and text is None:
                            value = {'error': '公式没有已保存结果；请在Excel重算保存'}
                        elif kind == 'e':
                            value = {'error': 'Excel错误值 ' + (text or '')}
                        elif kind == 's':
                            value = strings[int(text)] if text is not None else None
                        elif kind == 'inlineStr':
                            value = ''.join(e.text or '' for e in cell.iter() if _tag(e) == 't')
                        elif kind in ('str', 'd'):
                            value = text
                        elif kind == 'b':
                            value = {'error': '布尔值不是成绩'}
                        elif text is None:
                            value = None
                        else:
                            try:
                                value = float(text)
                                if not math.isfinite(value):
                                    raise ValueError()
                                if value.is_integer():
                                    value = int(value)
                            except ValueError:
                                value = {'error': '非有限数值'}
                        grid.setdefault(number, {})[col] = value
                max_row = max(grid, default=1)
                max_col = max((max(r, default=0) for r in grid.values()), default=0) + 1
                data = [[grid.get(r, {}).get(c) for c in range(max_col)] for r in range(1, max_row + 1)]
                sheets.append({'name': s.attrib.get('name', '未命名'), 'rows': data,
                               'formula_cells': formula_cells, 'hidden_rows': hidden_rows,
                               'hidden': s.attrib.get('state', 'visible') != 'visible'})
            if not sheets:
                raise ExamError('未找到可读取的成绩工作表。')
            return {'source_name': path.name, 'source_sha256': sha256(raw).hexdigest(), 'sheets': sheets}
    except ExamError:
        raise
    except (OSError, BadZipFile, ET.ParseError, KeyError, ValueError, IndexError, TypeError):
        raise ExamError('无法读取成绩表。请检查文件完整性、加密状态并另存为xlsx。') from None


MISSING = {'', '-', '—', '未批', '待批', '待核对', '未录入', '缺考', '缺', '请假'}
ABSENT = {'缺考', '缺', '请假', 'absent'}


def _score(value, maximum):
    if isinstance(value, dict):
        return None, value.get('error', '单元格无效')
    if display(value).strip() in MISSING:
        return None, '缺失/未批'
    if isinstance(value, bool):
        return None, '布尔值不是成绩'
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None, '不是有效数字'
    if not math.isfinite(v) or not 0 <= v <= maximum:
        return None, f'得分超出0至{maximum:g}'
    return v, None


def _number(value, label, *, positive=True):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ExamError(label + '请填写数字。') from None
    if isinstance(value, bool) or not math.isfinite(v) or (v <= 0 if positive else v < 0):
        raise ExamError(label + '数值不正确。')
    return v


def build_exam(book, config):
    """config uses explicit zero-based columns and a one-based physical header row."""
    cfg = deepcopy(config)
    try:
        sheet = next(s for s in book['sheets'] if s['name'] == cfg['sheet'])
    except (StopIteration, KeyError):
        raise ExamError('请选择成绩所在工作表。') from None
    rows = sheet['rows']; header = cfg.get('header_row', 1)
    if type(header) is not int or not 1 <= header < len(rows):
        raise ExamError('表头行必须位于成绩数据之前。')
    width = len(rows[header - 1])
    def col(n, required=False):
        if n is None and not required:
            return None
        if type(n) is not int or not 0 <= n < width:
            raise ExamError('列映射不正确，请重新选择。')
        return n
    sid = col(cfg.get('student_col'), True)
    cl = col(cfg.get('class_col')); total = col(cfg.get('total_col')); status = col(cfg.get('status_col'))
    chosen = [x for x in (sid, cl, total, status) if x is not None]
    if len(chosen) != len(set(chosen)):
        raise ExamError('学生、班级、总分及缺考状态不能重复使用同一列。')
    maximum = _number(cfg.get('max_score'), '试卷满分')
    pass_score = _number(cfg.get('pass_score'), '达标线', positive=False)
    excellent = _number(cfg.get('excellent_score'), '高分线', positive=False)
    if not 0 <= pass_score < excellent <= maximum:
        raise ExamError('请设置0≤达标线＜高分线≤满分；这是本次分析阈值，不是官方等级分界。')
    questions = []
    seen = set()
    for q in cfg.get('questions', []):
        c = col(q.get('column'), True); number = str(q.get('question', '')).strip()
        if not number or number in seen or c in chosen:
            raise ExamError('题号不能为空或重复，成绩列不能被重复映射。')
        seen.add(number); chosen.append(c)
        questions.append({'column': c, 'question': number, 'max_score': _number(q.get('max_score'), '题目满分'),
                          'knowledge': str(q.get('knowledge', '')).strip(), 'chapter': str(q.get('chapter', '')).strip(),
                          'context': str(q.get('context', '')).strip()})
    if len(questions) > 200:
        raise ExamError('本版最多映射200个非重叠评分单元。')
    max_sum = math.fsum(q['max_score'] for q in questions)
    if total is None and (not questions or abs(max_sum - maximum) > .001):
        raise ExamError('按小题合计总分时，须映射全部非重叠小题，且满分之和等于试卷满分。')
    if max_sum > maximum + .001:
        raise ExamError('映射题目满分之和超过试卷满分；请勿同时选择大题总分和所属小问得分。')
    title = str(cfg.get('title', '')).strip()
    if not title:
        raise ExamError('请填写考试名称。')
    issues, students, ids = [], [], set()
    for rn, row in enumerate(rows[header:], header + 1):
        if all(v is None or v == '' for v in row):
            continue
        identity = display(row[sid]).strip(); class_name = display(row[cl]).strip() if cl is not None else '本次导入'
        if not identity or isinstance(row[sid], dict):
            issues.append({'row': rn, 'field': '学生', 'detail': '缺少有效学生标识，本行未纳入'}); continue
        if identity in {'合计', '平均', '平均分', '总计'}:
            issues.append({'row': rn, 'field': '学生', 'detail': '疑似统计行，本行未纳入'}); continue
        key = (class_name, identity)
        if key in ids:
            raise ExamError(f'第{rn}行存在同班同标识的重复记录。请使用唯一学号，或拆开不同考试。')
        ids.add(key)
        absent = display(row[status]).strip().lower() in ABSENT if status is not None else False
        if total is not None and display(row[total]).strip().lower() in ABSENT:
            absent = True
        item_scores = {}
        for q in questions:
            v, error = _score(row[q['column']], q['max_score'])
            if absent:
                v = None
            elif error:
                issues.append({'row': rn, 'field': q['question'], 'detail': error})
            item_scores[q['question']] = v
        if absent:
            total_score = None
        elif total is not None:
            total_score, err = _score(row[total], maximum)
            if err:
                issues.append({'row': rn, 'field': '总分', 'detail': err})
            if (questions and abs(max_sum - maximum) < .001 and all(v is not None for v in item_scores.values())
                    and total_score is not None and abs(math.fsum(item_scores.values()) - total_score) > .01):
                issues.append({'row': rn, 'field': '总分', 'detail': '总分与完整小题合计不一致；排除本行总分，保留有效小题'})
                total_score = None
        else:
            total_score = math.fsum(item_scores.values()) if all(v is not None for v in item_scores.values()) else None
        students.append({'id': f'S{len(students)+1:04}', 'local_label': identity, 'class': class_name or '未填写班级',
                         'excel_row': rn, 'absent': absent, 'total': total_score, 'scores': item_scores})
    if not students:
        raise ExamError('没有可分析的学生行，请检查表头行与学生列。')
    warnings = ['单次考试仅描述本次表现，不证明成绩已提升；分数不能直接确定失分原因。',
                '行/列隐藏及Excel筛选不会排除数据；本页按所选工作表全部数据行导入。']
    if sheet['formula_cells']:
        warnings.append(f"含{len(sheet['formula_cells'])}个公式单元格，仅使用Excel已保存结果，软件不重算；缓存可能过期，请先在Excel重算保存。")
    if not questions:
        warnings.append('只有总分：不生成小题或知识点掌握结论。')
    else:
        warnings.append('知识点来自教师明确映射；一道题只设一个主知识点，未映射项不冒充已掌握。请勿重复映射大题和小问。')
    return {'schema': 'shchem.exam.v1', 'id': uuid.uuid4().hex, 'created_at': datetime.now(timezone.utc).isoformat(),
            'title': title, 'source': {k: book[k] for k in ('source_name', 'source_sha256')},
            'config': cfg, 'maximum': maximum, 'pass_score': pass_score, 'excellent_score': excellent,
            'questions': questions, 'students': students, 'issues': issues, 'warnings': warnings}


def _stats(values):
    return {'n': len(values), 'mean': statistics.mean(values) if values else None,
            'median': statistics.median(values) if values else None,
            'sd': statistics.pstdev(values) if values else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def analyse(exam, class_name=None):
    members = [s for s in exam['students'] if class_name is None or s['class'] == class_name]
    valid = [s['total'] for s in members if s['total'] is not None and not s['absent']]
    n = len(valid); maximum = exam['maximum']
    overall = _stats(valid)
    overall.update(enrolled=len(members), absent=sum(s['absent'] for s in members),
                   unavailable=sum(s['total'] is None and not s['absent'] for s in members),
                   pass_n=sum(v >= exam['pass_score'] for v in valid),
                   high_n=sum(v >= exam['excellent_score'] for v in valid))
    overall['pass_rate'] = overall['pass_n'] / n if n else None
    overall['high_rate'] = overall['high_n'] / n if n else None
    edges = [0, .4, .6, .7, .8, .9, 1]
    distribution = []
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        distribution.append({'label': f'{a*100:g}–{b*100:g}%', 'n': sum(a <= v/maximum < b or (i == 5 and v == maximum) for v in valid)})
    items, groups = [], defaultdict(list)
    for q in exam['questions']:
        vals = [s['scores'][q['question']] for s in members if not s['absent'] and s['scores'].get(q['question']) is not None]
        st = _stats(vals); st.update(q)
        st.update(missing=len(members) - overall['absent'] - len(vals),
                  rate=math.fsum(vals)/(len(vals)*q['max_score']) if vals else None)
        items.append(st)
        if q['knowledge']:
            groups[q['knowledge']].append(st)
    knowledge = []
    for label, qs in groups.items():
        denominator = math.fsum(q['n'] * q['max_score'] for q in qs)
        earned = math.fsum((q['mean'] or 0) * q['n'] for q in qs)
        knowledge.append({'label': label, 'rate': earned / denominator if denominator else None,
                          'questions': [q['question'] for q in qs], 'responses': sum(q['n'] for q in qs),
                          'possible_points': denominator})
    classes = []
    for c in sorted({s['class'] for s in members}):
        vs = [s['total'] for s in members if s['class'] == c and not s['absent'] and s['total'] is not None]
        classes.append({'class': c, **_stats(vs)})
    scoped_rows = {s['excel_row'] for s in members}
    return {'title': exam['title'], 'scope': class_name or '全部导入班级', 'maximum': maximum,
            'pass_score': exam['pass_score'], 'excellent_score': exam['excellent_score'],
            'overall': overall, 'distribution': distribution, 'items': items, 'knowledge': knowledge,
            'classes': classes, 'students': members, 'warnings': exam['warnings'],
            'issues': [i for i in exam['issues'] if class_name is None or i['row'] in scoped_rows]}


def local_student_advice(student, report):
    """Observable priorities rather than unsupported mental/causal diagnoses."""
    if student['absent']:
        return '缺考记录：先核实补考或补测安排，不把缺考作为知识错误。'
    missing = [q['question'] for q in report['items'] if student['scores'].get(q['question']) is None]
    lost = sorted([(q['max_score']-student['scores'][q['question']], q) for q in report['items']
                   if student['scores'].get(q['question']) is not None], key=lambda x: -x[0])
    lines = []
    if student['total'] is None:
        lines.append('总分缺失或冲突：先核对数据，不做总分分层。')
    elif student['total'] < report['pass_score']:
        lines.append('本次低于自定达标线：先用短诊断检查基础概念和典型表达，再逐步补练。')
    elif student['total'] < report['excellent_score']:
        lines.append('本次位于自定达标线与高分线之间：优先处理失分集中题，核对表达、条件与解题步骤。')
    else:
        lines.append('本次达到自定高分线：核对剩余失分，再安排条件变化或迁移任务，避免只增加题量。')
    if lost and lost[0][0] > 0:
        lines.append('优先回看：' + '；'.join(f"{q['question']}（失{loss:g}分，{q['knowledge'] or '知识点待映射'}）" for loss, q in lost[:3] if loss > 0))
    if missing:
        lines.append('尚缺得分：' + '、'.join(missing) + '；缺失不等于不会。')
    lines.append('需结合原作答确认原因；一周后用同目标短测记录实际结果，本页不预测提分幅度。')
    return '\n'.join(lines)


from threading import RLock
_EXAM_WRITE_LOCK = RLock()
_UNCHECKED = object()


class ExamStore:
    def __init__(self, state_root):
        self.root = Path(state_root) / 'exam-analyses'

    def save(self, bundle, *, expected_revision=_UNCHECKED):
        with _EXAM_WRITE_LOCK:
            identity=bundle['exam']['id']
            if not re.fullmatch('[0-9a-f]{32}', identity):
                raise ExamError('分析记录身份无效。')
            path=self.root/(identity+'.json')
            if expected_revision is not _UNCHECKED:
                current=digest(self.load(identity)) if path.exists() else None
                if current != expected_revision:
                    raise ExamError('这份考试分析已在其他窗口更新，请先另存当前输入并重新打开；未覆盖最新记录。')
            return self._save(bundle)

    def _save(self, bundle):
        identity = bundle['exam']['id']
        if not re.fullmatch('[0-9a-f]{32}', identity):
            raise ExamError('分析记录身份无效。')
        self.root.mkdir(parents=True, exist_ok=True)
        import os, tempfile
        data = json.dumps(bundle, ensure_ascii=False, allow_nan=False, indent=2).encode()
        fd, name = tempfile.mkstemp(prefix='.exam-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(name, self.root / (identity + '.json'))
        except OSError:
            raise ExamError('分析记录未能保存；当前数据仍保留在窗口。') from None
        finally:
            Path(name).unlink(missing_ok=True)
        return identity

    def entries(self):
        result = []
        for p in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                b = json.loads(p.read_text(encoding='utf-8'))
                if b['exam']['schema'] == 'shchem.exam.v1':
                    result.append((p.stem, b['exam']['title']))
            except (ValueError, KeyError, OSError):
                continue
        return result

    def load(self, identity):
        if not re.fullmatch('[0-9a-f]{32}', identity):
            raise ExamError('分析记录身份无效。')
        try:
            b = json.loads((self.root/(identity+'.json')).read_text(encoding='utf-8'))
            if b['exam']['schema'] != 'shchem.exam.v1':
                raise ValueError()
            return b
        except (ValueError, KeyError, OSError):
            raise ExamError('分析记录无法读取，请保留原文件并重新导入成绩。') from None

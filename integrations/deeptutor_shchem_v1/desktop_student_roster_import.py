"""Read-only roster intake and explicitly confirmed local member updates.

File rows are never identities. Names are not a merge key. The final save reads
the source and current roster again instead of trusting preview validity flags.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re
from uuid import uuid4

from .desktop_exam_data import ExamError, column_name, digest, display, read_xlsx
from .desktop_student_roster import new_member, validate_roster

SCHEMA = 'shchem.student-roster-import-plan.v1'
RECEIPT_SCHEMA = 'shchem.student-roster-import-receipt.v1'
MAX_ROWS, MAX_COLUMNS, MAX_BYTES = 10000, 256, 40 * 1024 * 1024
CONFIG_KEYS = {'sheet','header_row','alias_col','member_col','profile_col','external_col'}
_MEMBER_ID = re.compile(r'[0-9a-f]{32}\Z')


def _now():
    return datetime.now(timezone.utc).isoformat()


def read_roster_file(path, *, encoding='auto', delimiter=None):
    """Saved XLSX values or UTF/BOM CSV/TSV; no recalculation or Office."""
    source = Path(path)
    if source.suffix.lower() == '.xlsx':
        book = read_xlsx(source)
        book['source_kind'] = 'xlsx'
        book['read_options'] = {'encoding':'auto','delimiter':None}
        return book
    if source.suffix.lower() not in {'.csv','.tsv'}:
        raise ExamError('名单支持普通.xlsx、.csv或.tsv；旧xls与宏表请另存。')
    try:
        if source.stat().st_size > MAX_BYTES:raise ExamError('名单文件超过40MB，请精简后导入。')
        raw=source.read_bytes()
    except OSError:raise ExamError('名单原文件无法读取，请检查文件位置和权限。') from None
    if encoding not in {'auto','utf-8-sig','utf-8','utf-16','gb18030'}:
        raise ExamError('请选择明确支持的文本编码。')
    codec = ('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig') if encoding=='auto' else encoding
    try:contents=raw.decode(codec,errors='strict')
    except UnicodeError:raise ExamError('名单编码无法读取；请明确选择UTF-8、UTF-16或GB18030后重读，不会静默替换乱码。') from None
    if '\x00' in contents:raise ExamError('名单包含空字符，请检查编码后另存为普通文字名单。')
    separator = delimiter if delimiter is not None else '\t' if source.suffix.lower()=='.tsv' else ','
    if separator not in {',','\t',';'}:raise ExamError('请选择逗号、制表符或分号作为名单分隔符。')
    rows, lines = [], []
    try:
        reader=csv.reader(StringIO(contents,newline=''),delimiter=separator,strict=True)
        previous=0
        for row in reader:
            if len(rows)>=MAX_ROWS or len(row)>MAX_COLUMNS:raise ExamError('名单最多10000条记录、256列，请拆分后导入。')
            rows.append(row);lines.append({'start':previous+1,'end':reader.line_num});previous=reader.line_num
    except csv.Error:raise ExamError('名单引号或分隔结构不完整，请核对原文件；未丢弃错误行。') from None
    if not rows:raise ExamError('名单文件为空。')
    return {'source_name':source.name,'source_sha256':sha256(raw).hexdigest(),
            'source_kind':source.suffix.lower().lstrip('.'),'read_options':{'encoding':encoding,'delimiter':separator},
            'sheets':[{'name':'名单','rows':rows,'formula_cells':[],'hidden_rows':[],
                       'hidden':False,'source_lines':lines}]}


def roster_headers(book, sheet_name, header_row):
    try:
        sheet=next(s for s in book['sheets'] if s['name']==sheet_name)
        if type(header_row) is not int or not 1<=header_row<len(sheet['rows']):raise ValueError()
        return [display(value).strip() for value in sheet['rows'][header_row-1]]
    except (TypeError,KeyError,ValueError,StopIteration,IndexError):
        raise ExamError('请选择名单工作表和位于数据之前的表头记录行。') from None


def _profile_ids(profiles):
    try:
        return {value if isinstance(value,str) else value.get('student_id') if isinstance(value,dict)
                else getattr(value,'student_id',None) for value in profiles}
    except (TypeError,ValueError):raise ExamError('当前匿名档案列表无法核验，已保留输入与原记录。') from None


def import_profiles(store):
    if not callable(store.profiles):return ()
    try:
        profiles=tuple(store.profiles());_profile_ids(profiles);return profiles
    except ExamError:raise
    except Exception:raise ExamError('当前匿名档案目录无法读取，已保留输入与原记录。') from None


def _cell_text(value):
    return display(value).strip()


def _plain_alias(value):
    return isinstance(value,str) and 1<=len(value)<=100 and not any(ord(c)<32 for c in value)


def preview_roster_import(book, config, roster, *, profiles=()):
    roster=validate_roster(roster)
    if not isinstance(config,dict) or set(config)-CONFIG_KEYS:
        raise ExamError('名单列配置不完整或含未知字段。')
    cfg={key:config.get(key) for key in CONFIG_KEYS};cfg['header_row']=config.get('header_row',1)
    headers=roster_headers(book,cfg['sheet'],cfg['header_row'])
    if not headers or len(headers)>MAX_COLUMNS:raise ExamError('名单表头为空或列数过多。')
    roles=['alias_col','member_col','profile_col','external_col'];used=[]
    for key in roles:
        column=cfg[key]
        if column is None and key!='alias_col':continue
        if type(column) is not int or not 0<=column<len(headers):raise ExamError('请明确选择别名列及可选身份列。')
        used.append(column)
    if len(used)!=len(set(used)):raise ExamError('别名、已有成员ID、匿名档案及原表标识不能重复使用同一列。')
    sheet=next(s for s in book['sheets'] if s['name']==cfg['sheet'])
    if len(sheet['rows'])>MAX_ROWS or any(len(row)>MAX_COLUMNS for row in sheet['rows']):
        raise ExamError('名单工作表最多10000条记录、256列，请拆分后导入。')
    members={m['id']:m for m in roster['members']};known_profiles=_profile_ids(profiles)
    profile_owners={m['profile_student_id']:m['id'] for m in roster['members'] if m['profile_student_id']}
    rows=[];blank=[];formulas=set(sheet.get('formula_cells',[]))
    for number,raw in enumerate(sheet['rows'][cfg['header_row']:],cfg['header_row']+1):
        if all(v is None or v=='' for v in raw):blank.append(number);continue
        def cell(key):
            index=cfg[key]
            return None if index is None or index>=len(raw) else raw[index]
        alias=_cell_text(cell('alias_col'));member_id=_cell_text(cell('member_col')) or None
        profile=_cell_text(cell('profile_col')) or None;external=_cell_text(cell('external_col')) or None
        errors=[];warnings=[]
        if not _plain_alias(alias) or isinstance(cell('alias_col'),dict):errors.append('别名为空、过长或含不支持的控制字符。')
        if len(raw)>len(headers) and any(v not in (None,'') for v in raw[len(headers):]):errors.append('数据列超过当前表头，请核对表头记录行。')
        for key in roles:
            column=cfg[key]
            if column is None:continue
            value=cell(key)
            if isinstance(value,dict) or column_name(column)+str(number) in formulas or (isinstance(value,str) and value.strip().startswith('=')):
                errors.append('映射身份／别名列含公式或错误值；请在原表核对为文字后重读。')
        if member_id and (not _MEMBER_ID.fullmatch(member_id) or member_id not in members):
            errors.append('已有成员ID不属于当前名册；不会按学号、别名或未知ID合并。')
        if profile and profile not in known_profiles:errors.append('匿名档案不属于当前私有学生目录或无法核验。')
        if member_id in members and profile and members[member_id]['profile_student_id'] not in (None,profile):
            errors.append('原成员已有不同匿名档案；请先在名册单独明确核对归属。')
        if profile in profile_owners and member_id and member_id!=profile_owners[profile]:
            errors.append('源匿名档案已属于当前另一稳定成员，不能通过导入改变归属。')
        if external and (len(external)>200 or any(ord(c)<32 for c in external)):errors.append('原表标识过长或含控制字符。')
        if member_id is None and any(m['local_alias']==alias for m in roster['members']):
            warnings.append('当前名册存在相同别名；不会自动匹配，请明确新建或指定已有稳定成员。')
        line=(sheet.get('source_lines') or [])[number-1] if number<=len(sheet.get('source_lines') or []) else None
        rows.append({'source_row':number,'source_lines':deepcopy(line),'alias':alias,'member_id':member_id,
                     'profile_student_id':profile,'external_id':external,'errors':list(dict.fromkeys(errors)),
                     'warnings':warnings,'conflicts':[]})
    if not rows:raise ExamError('名单没有非空数据记录。')
    ids=Counter(row['member_id'] for row in rows if row['member_id']);aliases=Counter(row['alias'] for row in rows)
    profile_counts=Counter(row['profile_student_id'] for row in rows if row['profile_student_id'])
    external_counts=Counter(row['external_id'] for row in rows if row['external_id'])
    for row in rows:
        if row['member_id'] and ids[row['member_id']]>1:row['conflicts'].append('源名单重复成员ID；保留行须唯一，其他行请明确排除。')
        if row['profile_student_id'] and profile_counts[row['profile_student_id']]>1:row['conflicts'].append('源名单重复匿名档案；不能保存到多个成员。')
        if row['profile_student_id'] in profile_owners and row['member_id'] is None:
            row['conflicts'].append('该匿名档案已属于成员'+profile_owners[row['profile_student_id']]+'；请明确更新到此稳定成员或排除，不能新建重复归属。')
        if aliases[row['alias']]>1:row['warnings'].append('源名单存在重名别名；不同新行生成独立ID，不自动合并。')
        if row['external_id'] and external_counts[row['external_id']]>1:row['warnings'].append('原表标识重复；它不是本机稳定ID，不按该值合并。')
    notices=['名单原文件不执行公式／宏，不调用Office或模型；姓名／学号／别名只用于本机名册。',
             '每条非空记录须明确新建、更新到稳定成员或排除；排除不会删除已有成员。']
    if sheet.get('hidden_rows') or sheet.get('hidden'):notices.append('当前工作表或部分行隐藏；本次仍预览全部数据记录，须明确排除。')
    if any(not label for label in headers) or any(count>1 for label,count in Counter(headers).items() if label):
        notices.append('存在空白或重复表头；按当前列位置明确选择，未按名称猜列。')
    plan={'schema':SCHEMA,'source':{k:book[k] for k in ('source_name','source_sha256','source_kind','read_options')},
          'config':cfg,'headers':headers,'roster':deepcopy(roster),'roster_sha256':digest(roster),
          'rows':rows,'blank_rows':blank,'notices':notices}
    plan['plan_sha256']=digest(plan)
    return plan


def _decision(decisions, row):
    value=decisions.get(row['source_row'])
    if not isinstance(value,dict) or set(value)-{'action','member_id'} or value.get('action') not in {'new','update','exclude'}:
        raise ExamError(f"名单第{row['source_row']}条记录尚未明确新建、更新或排除。")
    return value


def build_roster_import(plan, decisions, *, confirmed=False, acknowledge_warnings=False, reason=''):
    if not isinstance(plan,dict) or plan.get('schema')!=SCHEMA or plan.get('plan_sha256')!=digest({k:v for k,v in plan.items() if k!='plan_sha256'}):
        raise ExamError('名单核对计划已变化，请重新预览。')
    if confirmed is not True:raise ExamError('请明确确认列映射、逐行处理与本机保存范围。')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:raise ExamError('请填写本次名册导入核对依据（1至2000字）。')
    if not isinstance(decisions,dict) or any(type(key) is not int for key in decisions):raise ExamError('名单行处理选择格式不正确。')
    valid_rows={row['source_row'] for row in plan['rows']}
    if set(decisions)-valid_rows:raise ExamError('名单处理选择引用了不存在的源记录。')
    roster=validate_roster(plan['roster']);members={m['id']:m for m in roster['members']}
    changes=[];updates=set();selected=[]
    for row in plan['rows']:
        decision=_decision(decisions,row);action=decision['action']
        if action=='exclude':changes.append({'source_row':row['source_row'],'source_lines':deepcopy(row['source_lines']),'action':'exclude','member_id':None,'external_id':row['external_id']});continue
        if row['errors']:raise ExamError(f"名单第{row['source_row']}条记录仍有错误；请修原表重读或明确排除。")
        if (row['warnings'] or row['conflicts']) and acknowledge_warnings is not True:
            raise ExamError('请明确核对重名／重复项及排除范围；不会自动合并。')
        if action=='new':
            if row['member_id'] or decision.get('member_id'):raise ExamError('源记录已有成员ID；更新需选择该稳定成员，不能自动复制为新人。')
            member=new_member(row['alias']);member['profile_student_id']=row['profile_student_id']
            roster['members'].append(member);before=None
        else:
            identity=decision.get('member_id') or row['member_id']
            if identity not in members or (row['member_id'] and identity!=row['member_id']):
                raise ExamError('更新必须明确选择当前名册中一致的稳定成员ID。')
            if identity in updates:raise ExamError('同一稳定成员有多条保留更新行；请逐行核对并明确排除重复。')
            updates.add(identity);member=members[identity];before=deepcopy(member)
            incoming=row['profile_student_id']
            if incoming and member['profile_student_id'] not in (None,incoming):
                raise ExamError('已有档案归属不同；名单导入不能悄悄替换原档案。')
            member['local_alias']=row['alias']
            if incoming:member['profile_student_id']=incoming
        selected.append(member['id'])
        changes.append({'source_row':row['source_row'],'source_lines':row['source_lines'],'action':action,
                        'member_id':member['id'],'external_id':row['external_id'],'before':before,'after':deepcopy(member)})
    if not selected:raise ExamError('本次全部记录已排除，没有要保存的名册更改。')
    validate_roster(roster)
    receipt={'schema':RECEIPT_SCHEMA,'id':uuid4().hex,'at':_now(),'source':deepcopy(plan['source']),
             'config':deepcopy(plan['config']),'headers':deepcopy(plan['headers']),'before_revision':plan['roster']['revision'],
             'before_roster_sha256':plan['roster_sha256'],'reason':reason.strip(),'rows':changes,
             'warnings_acknowledged':acknowledge_warnings is True,'blank_rows':deepcopy(plan['blank_rows'])}
    receipt['receipt_sha256']=digest(receipt)
    return {'roster':roster,'receipt':receipt}


def _validate_import_receipt(receipt):
    """Retained private source/row evidence, not a new authorization."""
    fields={'schema','id','at','source','config','headers','before_revision','before_roster_sha256',
            'reason','rows','warnings_acknowledged','blank_rows','receipt_sha256'}
    def require(condition):
        if not condition:raise ExamError('名单导入历史凭据不完整或已变化，已保留原记录。')
    require(isinstance(receipt,dict) and set(receipt)==fields and receipt.get('schema')==RECEIPT_SCHEMA)
    require(isinstance(receipt['id'],str) and bool(_MEMBER_ID.fullmatch(receipt['id'])))
    require(isinstance(receipt['at'],str) and bool(receipt['at']))
    require(receipt['before_revision'] is None or isinstance(receipt['before_revision'],str) and bool(_MEMBER_ID.fullmatch(receipt['before_revision'])))
    require(isinstance(receipt['before_roster_sha256'],str) and bool(re.fullmatch('[0-9a-f]{64}',receipt['before_roster_sha256'])))
    require(isinstance(receipt['reason'],str) and 1<=len(receipt['reason'].strip())<=2000)
    require(type(receipt['warnings_acknowledged']) is bool)
    source=receipt['source'];require(isinstance(source,dict) and set(source)=={'source_name','source_sha256','source_kind','read_options'})
    require(isinstance(source['source_name'],str) and 1<=len(source['source_name'])<=500)
    require(isinstance(source['source_sha256'],str) and bool(re.fullmatch('[0-9a-f]{64}',source['source_sha256'])))
    require(source['source_kind'] in {'xlsx','csv','tsv'})
    options=source['read_options'];require(isinstance(options,dict) and set(options)=={'encoding','delimiter'})
    require(options['encoding'] in {'auto','utf-8-sig','utf-8','utf-16','gb18030'} and options['delimiter'] in {None,',','\t',';'})
    config=receipt['config'];require(isinstance(config,dict) and set(config)==CONFIG_KEYS)
    require(isinstance(config['sheet'],str) and bool(config['sheet']) and type(config['header_row']) is int and config['header_row']>=1)
    require(type(config['alias_col']) is int and config['alias_col']>=0)
    require(all(value is None or type(value) is int and value>=0 for key,value in config.items() if key.endswith('_col')))
    headers=receipt['headers'];require(isinstance(headers,list) and 1<=len(headers)<=MAX_COLUMNS and all(isinstance(value,str) for value in headers))
    require(all(value is None or value<len(headers) for key,value in config.items() if key.endswith('_col')))
    require(isinstance(receipt['blank_rows'],list) and all(type(row) is int and row>=1 for row in receipt['blank_rows']))
    rows=receipt['rows'];require(isinstance(rows,list) and 1<=len(rows)<=MAX_ROWS)
    positions=set()
    for row in rows:
        require(isinstance(row,dict) and type(row.get('source_row')) is int and row['source_row']>config['header_row'] and row['source_row'] not in positions)
        positions.add(row['source_row']);require(row.get('action') in {'new','update','exclude'})
        if row['action']=='exclude':
            require(set(row) in ({'source_row','action','member_id','external_id'}, {'source_row','source_lines','action','member_id','external_id'}) and row['member_id'] is None)
        else:
            require(set(row)=={'source_row','source_lines','action','member_id','external_id','before','after'})
            require(isinstance(row['member_id'],str) and bool(_MEMBER_ID.fullmatch(row['member_id'])))
            require(row['before'] is None if row['action']=='new' else isinstance(row['before'],dict))
            for member in (row['before'],row['after']):
                if member is None:continue
                require(isinstance(member,dict) and set(member)=={'id','local_alias','profile_student_id'} and member['id']==row['member_id'] and _plain_alias(member['local_alias']))
                require(member['profile_student_id'] is None or isinstance(member['profile_student_id'],str) and 1<=len(member['profile_student_id'])<=100)
            require(isinstance(row['after'],dict))
        if 'source_lines' in row:
            line=row['source_lines'];require(line is None or isinstance(line,dict) and set(line)=={'start','end'} and type(line['start']) is int and type(line['end']) is int and 1<=line['start']<=line['end'])
        require(row['external_id'] is None or isinstance(row['external_id'],str) and len(row['external_id'])<=200)
    require(receipt['receipt_sha256']==digest({k:v for k,v in receipt.items() if k!='receipt_sha256'}))
    return deepcopy(receipt)


def validate_import_receipt(receipt):
    try:return _validate_import_receipt(receipt)
    except (KeyError,TypeError,ValueError,AttributeError,IndexError):
        raise ExamError('名单导入历史凭据结构无法核验，已保留原记录。') from None


def commit_roster_import(store, path, plan, decisions, *, confirmed=False, acknowledge_warnings=False, reason=''):
    """Re-read input, current ownership and current roster before its CAS save."""
    if not isinstance(plan,dict) or plan.get('schema')!=SCHEMA:
        raise ExamError('名单核对计划无法核验，请重新预览；未保存。')
    options=plan.get('source',{}).get('read_options',{})
    book=read_roster_file(path,**options)
    if book['source_sha256']!=plan.get('source',{}).get('source_sha256'):
        raise ExamError('名单原文件已变化，请重新读取和预览；未保存旧计划。')
    previous=plan.get('roster')
    if not isinstance(previous,dict):raise ExamError('名单目标名册无法核验。')
    current=store.load(previous['id']) if previous.get('revision') is not None else validate_roster(previous)
    if digest(current)!=plan.get('roster_sha256'):raise ExamError('名册已被另一窗口修改，请重新核对；未覆盖当前名册。')
    profiles=import_profiles(store)
    fresh=preview_roster_import(book,plan.get('config'),current,profiles=profiles)
    if fresh!=plan:raise ExamError('名单内容、映射、归属或核对计划已变化，请重新预览；未采信旧有效标记。')
    result=build_roster_import(fresh,decisions,confirmed=confirmed,acknowledge_warnings=acknowledge_warnings,reason=reason)
    # A single roster transaction keeps the row/source receipt and member edits
    # together. Validation refuses altered or unknown retained receipts.
    candidate=deepcopy(result['roster']);candidate.setdefault('import_history',[]).append(result['receipt'])
    saved=store.save(candidate,expected_revision=current['revision'])
    return saved

"""Local class identities and explicit exam bindings; never an AI projection.

Aliases are teacher-only. The anonymous student domain retains its own UUIDs
and consent protocol. No name, row number or S-number is used to infer identity.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from .desktop_exam_data import ExamError, digest
from .desktop_state import _shared_lock, _state_file_lock, DesktopStateError

SCHEMA = 'shchem.local-student-roster.v1'
LIBRARY_SCHEMA = 'shchem.local-student-rosters.v1'
STATUS_LABELS = {'present': '实到', 'absent': '缺考', 'exempt': '免考', 'not_entered': '未录入'}
_ID = re.compile(r'[0-9a-f]{32}\Z')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, label, limit=100):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit or any(ord(c)<32 for c in value):
        raise ExamError(label + '请填写有效文字。')
    return value.strip()


def _identity(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ExamError('本地名册身份无法核验。')
    return value


def new_roster(label):
    return {'schema': SCHEMA, 'id': uuid4().hex, 'label': _text(label, '班级别名'),
            'revision': None, 'members': [], 'created_at': _now()}


def new_member(alias):
    return {'id': uuid4().hex, 'local_alias': _text(alias, '学生本地别名'), 'profile_student_id': None}


def validate_roster(roster):
    required = {'schema','id','label','revision','members','created_at'}
    if not isinstance(roster, dict) or not required <= set(roster) or set(roster)-required-{'import_history'} or roster.get('schema') != SCHEMA:
        raise ExamError('本地名册格式不完整，请保留原文件。')
    _identity(roster['id']); _text(roster['label'], '班级别名')
    if roster['revision'] is not None:_identity(roster['revision'])
    if not isinstance(roster['created_at'], str) or not roster['created_at']:
        raise ExamError('名册创建记录缺失。')
    members = roster['members']
    if not isinstance(members, list) or len(members) > 10000:
        raise ExamError('名册成员格式不正确或超过10000人。')
    ids, profiles = set(), set()
    for member in members:
        if not isinstance(member, dict) or set(member) != {'id','local_alias','profile_student_id'}:
            raise ExamError('名册成员记录不完整。')
        _identity(member['id']); _text(member['local_alias'], '学生本地别名')
        if member['id'] in ids:raise ExamError('成员ID重复；不会按别名合并。')
        ids.add(member['id'])
        profile = member['profile_student_id']
        if profile is not None:
            if not isinstance(profile, str) or not profile or len(profile)>100:
                raise ExamError('关联的匿名档案身份无效。')
            if profile in profiles:raise ExamError('同一名册中一个匿名档案不能绑定多个成员。')
            profiles.add(profile)
    if 'import_history' in roster:
        from .desktop_student_roster_import import validate_import_receipt
        history=roster['import_history']
        if not isinstance(history,list) or len(history)>10000:
            raise ExamError('名单导入历史格式无法核验，已保留原记录。')
        receipts=set()
        for receipt in history:
            validate_import_receipt(receipt)
            if receipt['id'] in receipts:raise ExamError('名单导入历史凭据ID重复，已保留原记录。')
            receipts.add(receipt['id'])
    return deepcopy(roster)


class DesktopStudentRosterStore:
    def __init__(self, private_root, *, profiles=None, submission=None):
        self.root = Path(private_root).resolve()
        self.path = self.root / 'student-rosters.v1.json'
        self._lock = _shared_lock(self.path)
        self.profiles = profiles
        self.submission = submission

    def _read(self):
        if not self.path.exists():return {'schema': LIBRARY_SCHEMA, 'rosters': {}}
        if self.path.is_symlink():raise ExamError('名册文件为链接，未读取。')
        try:value=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):raise ExamError('名册文件无法读取，已保留原记录。') from None
        if not isinstance(value, dict) or set(value) != {'schema','rosters'} or value['schema'] != LIBRARY_SCHEMA or not isinstance(value['rosters'],dict):
            raise ExamError('名册存储格式不正确，已保留原记录。')
        for key, roster in value['rosters'].items():
            validate_roster(roster)
            if key != roster['id'] or roster['revision'] is None:raise ExamError('名册身份或版本记录异常。')
        return value

    def entries(self):
        with self._lock:return [deepcopy(r) for r in self._read()['rosters'].values()]

    def load(self, identity):
        _identity(identity)
        with self._lock:
            value=self._read()['rosters'].get(identity)
            if value is None:raise ExamError('该本地名册不存在，请刷新。')
            return deepcopy(value)

    def check_profile(self, student_id):
        if student_id is None:return
        if student_id not in self.available_profile_ids():
            raise ExamError('匿名档案不存在于当前私有目录；未从别名猜测。')

    def available_profile_ids(self):
        if not callable(self.profiles):raise ExamError('当前匿名档案列表不可核验，未绑定。')
        try:
            return {v.get('student_id') if isinstance(v,dict) else getattr(v,'student_id',None) for v in self.profiles()}
        except Exception:
            raise ExamError('当前匿名档案目录无法读取，已保留教师输入与原记录。') from None

    def check_submission(self, student_id, submission_id):
        self.check_profile(student_id)
        if not student_id or not callable(self.submission):raise ExamError('请先绑定可核验的匿名档案。')
        try:value=self.submission(student_id=student_id, submission_id=submission_id)
        except Exception:raise ExamError('该作答不属于所选匿名档案或无法读取，未绑定。') from None
        owner=value.get('student_id') if isinstance(value,dict) else getattr(value,'student_id',None)
        identity=value.get('submission_id') if isinstance(value,dict) else getattr(value,'submission_id',None)
        if owner != student_id or identity != submission_id:raise ExamError('原作答所属档案无法核验，未绑定。')

    def save(self, roster, *, expected_revision=None):
        value=validate_roster(roster)
        requested={m['profile_student_id'] for m in value['members'] if m['profile_student_id']}
        if requested:
            available=self.available_profile_ids()
            if not requested <= available:raise ExamError('匿名档案不存在于当前私有目录；未从别名猜测。')
        try:
            with self._lock, _state_file_lock(self.path):
                library=self._read(); current=library['rosters'].get(value['id'])
                if (current['revision'] if current else None) != expected_revision:
                    raise ExamError('名册已被另一窗口修改，请重新读取；未覆盖最新名册。')
                if current and value['created_at'] != current['created_at']:
                    raise ExamError('名册创建身份发生变化，未保存。')
                if current:
                    history=current.get('import_history',[])
                    if value.get('import_history',[])[:len(history)] != history:
                        raise ExamError('原名单导入历史不可删除或改写，已保留磁盘与当前输入。')
                if requested:
                    available=self.available_profile_ids()
                    if not requested <= available:raise ExamError('保存前匿名档案归属已变化，请重新核对；未保存关联。')
                value['revision']=uuid4().hex
                library['rosters'][value['id']]=value
                self.root.mkdir(parents=True,exist_ok=True)
                fd,name=tempfile.mkstemp(prefix='.roster-',dir=self.root)
                try:
                    with os.fdopen(fd,'wb') as stream:
                        stream.write(json.dumps(library,ensure_ascii=False,allow_nan=False,indent=2).encode('utf-8'))
                        stream.flush();os.fsync(stream.fileno())
                    os.replace(name,self.path)
                finally:Path(name).unlink(missing_ok=True)
                return deepcopy(value)
        except (OSError, DesktopStateError):raise ExamError('名册正在使用或未能保存；当前输入保留，请重新读取后核对。') from None


def exam_status(row):
    status=row.get('attendance_status')
    if status in STATUS_LABELS:return status
    if row.get('absent'):return 'absent'
    return 'present' if row.get('total') is not None or any(v is not None for v in row.get('scores',{}).values()) else 'not_entered'


def identity_evidence(row):
    # Only explicitly recorded identity participates; no live alias lookup.
    return deepcopy(row.get('identity_binding'))


def _reason(reason):
    return _text(reason,'本次绑定或状态核对依据',2000)


def _reconcile(exam):
    bound={r.get('identity_binding',{}).get('member_id') for r in exam['students'] if r.get('identity_binding')}
    number=max([int(r['id'][1:]) for r in exam['students'] if re.fullmatch(r'S\d+',r.get('id',''))]+[0])
    for snapshot in exam.get('roster_snapshots',[]):
        for member in snapshot['roster']['members']:
            if member['id'] in bound:continue
            number+=1
            binding={'roster_id':snapshot['roster']['id'],'roster_revision':snapshot['roster']['revision'],
                     'member_id':member['id'],'profile_student_id':member['profile_student_id'],'submission_id':None}
            exam['students'].append({'id':f'S{number:04}','local_label':member['local_alias'],'local_alias':member['local_alias'],
                'class':snapshot['exam_class'],'excel_row':None,'roster_only':True,'identity_binding':binding,
                'attendance_status':'not_entered','absent':False,'total':None,
                'scores':{q['question']:None for q in exam['questions']}})
            bound.add(member['id'])


def attach_roster(exam, roster, *, exam_class=None, reason):
    roster=validate_roster(roster); reason=_reason(reason)
    if not roster['members'] or roster['revision'] is None:raise ExamError('请先保存含成员的本地名册。')
    exam_class=_text(exam_class or roster['label'],'本次考试班级')
    result=deepcopy(exam)
    snapshots=result.setdefault('roster_snapshots',[])
    if any(s['roster']['id']==roster['id'] or s['exam_class']==exam_class for s in snapshots):
        raise ExamError('该名册或考试班级已有快照；请保留旧身份，不自动替换。')
    previous_members={m['id'] for s in snapshots for m in s['roster']['members']}
    previous_profiles={m['profile_student_id'] for s in snapshots for m in s['roster']['members'] if m['profile_student_id']}
    if any(m['id'] in previous_members or (m['profile_student_id'] and m['profile_student_id'] in previous_profiles) for m in roster['members']):
        raise ExamError('本次其他班级名册已有相同成员ID或匿名档案，请先核对；未重复计入。')
    snapshots.append({'roster':roster,'exam_class':exam_class,'attached_at':_now()})
    result.setdefault('identity_history',[]).append({'at':_now(),'action':'attach_roster','roster_id':roster['id'],'reason':reason})
    _reconcile(result)
    return result


def bind_exam_row(exam, exam_student_id, member_id, *, store, submission_id=None, reason):
    reason=_reason(reason); result=deepcopy(exam)
    matches=[r for r in result['students'] if r['id']==exam_student_id]
    if len(matches)!=1:raise ExamError('请选择唯一的本次考试行。')
    row=matches[0]
    pairs=[(s,m) for s in result.get('roster_snapshots',[]) for m in s['roster']['members'] if m['id']==member_id]
    if len(pairs)!=1:raise ExamError('请从本次已确认名册明确选择成员。')
    snapshot,member=pairs[0]
    if row['class'] != snapshot['exam_class']:raise ExamError('成绩行与名册考试班级不同，请先核对班级范围。')
    store.check_profile(member['profile_student_id'])
    if submission_id:store.check_submission(member['profile_student_id'],submission_id)
    others=[r for r in result['students'] if r['id']!=row['id'] and (r.get('identity_binding') or {}).get('member_id')==member_id]
    if any(not r.get('roster_only') or r.get('total') is not None or any(v is not None for v in r.get('scores',{}).values()) for r in others):
        raise ExamError('该名册成员已绑定另一成绩行或已有明确分数；未合并或覆盖。')
    before=identity_evidence(row)
    binding={'roster_id':snapshot['roster']['id'],'roster_revision':snapshot['roster']['revision'],
             'member_id':member_id,'profile_student_id':member['profile_student_id'],'submission_id':submission_id or None}
    if before and {k:v for k,v in before.items() if k!='binding_revision'} == binding:
        return result
    binding['binding_revision']=uuid4().hex
    row['identity_binding']=binding;row['local_alias']=member['local_alias']
    for placeholder in others:
        if placeholder.get('attendance_revision'):
            row['attendance_status']=exam_status(placeholder);row['absent']=placeholder['absent']
            row['attendance_revision']=placeholder['attendance_revision']
    result['students']=[r for r in result['students'] if r not in others]
    result.setdefault('identity_history',[]).append({'at':_now(),'action':'bind_row','exam_student_id':exam_student_id,
        'source':deepcopy(result.get('source')),'excel_row':row['excel_row'],'before':before,'after':deepcopy(binding),
        'replaced_roster_rows':deepcopy(others),'reason':reason})
    _reconcile(result)
    return result


def set_exam_status(exam, exam_student_id, status, *, reason):
    if status not in STATUS_LABELS:raise ExamError('请选择实到、缺考、免考或未录入。')
    reason=_reason(reason);result=deepcopy(exam)
    rows=[r for r in result['students'] if r['id']==exam_student_id]
    if len(rows)!=1:raise ExamError('请选择唯一的考试学生行。')
    row=rows[0];before=exam_status(row)
    if before==status:return result
    row['attendance_status']=status;row['absent']=status=='absent'
    row['attendance_revision']=uuid4().hex
    # Original scores stay in the exam. Statistics project non-present rows as
    # unavailable; a later explicit correction never invents a zero or score.
    result.setdefault('identity_history',[]).append({'at':_now(),'action':'attendance','exam_student_id':exam_student_id,
        'before':before,'after':status,'reason':reason})
    return result


def empty_roster_exam(roster, *, title, maximum=100, pass_score=60, excellent_score=85, reason):
    from .desktop_exam_data import _number
    maximum=_number(maximum,'试卷满分');pass_score=_number(pass_score,'达标线',positive=False);excellent_score=_number(excellent_score,'高分线',positive=False)
    if not 0 <= pass_score < excellent_score <= maximum:raise ExamError('请设置0≤达标线＜高分线≤满分。')
    exam={'schema':'shchem.exam.v1','id':uuid4().hex,'created_at':_now(),'title':_text(title,'考试名称',200),
        'source':{'source_name':'明确名册考试（尚无成绩表）','source_sha256':None},'config':{},'maximum':maximum,
        'pass_score':pass_score,'excellent_score':excellent_score,'questions':[],'students':[],'issues':[],
        'warnings':['本次根据教师明确选择的本地名册建立；尚无导入成绩，未录入不等于缺考或0分。']}
    return attach_roster(exam,roster,reason=reason)

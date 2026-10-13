"""Versioned identity release/undo; never a score or submission rewrite."""
from copy import deepcopy
from math import isfinite
import re
from uuid import uuid4
from .desktop_exam_data import ExamError, digest
from .desktop_student_roster import bind_exam_row, validate_roster, _reconcile, _now, _reason

ACTION_SCHEMA = 'shchem.exam-student-identity-action.v1'


def _receipt_digest(value):
    try:return digest(value)
    except (TypeError, ValueError):
        raise ExamError('身份动作凭据内容无法核验，已保留原历史。') from None


def _receipt_row(row):
    required={'id','local_label','class','excel_row','absent','total','scores'}
    if (not isinstance(row,dict) or not required <= set(row)
            or any(not isinstance(row[key],str) or not row[key] for key in ('id','local_label','class'))
            or not isinstance(row['scores'],dict)
            or type(row['absent']) is not bool
            or (row['excel_row'] is not None and (type(row['excel_row']) is not int or row['excel_row']<1))
            or any(not isinstance(key,str) or not key for key in row['scores'])
            or ('identity_binding' in row and not isinstance(row['identity_binding'],dict))):
        raise ExamError('身份动作原行依据不完整，已保留原历史。')
    for score in [row['total'],*row['scores'].values()]:
        if score is not None and (type(score) not in (int,float) or (type(score) is float and not isfinite(score)) or score<0):
            raise ExamError('身份动作原行成绩依据无法核验，已保留原历史。')


def _validate_receipt(action):
    required={'schema','id','at','action','exam_student_id','reason','source_basis',
              'before_row','after_row','before_row_sha256','after_row_sha256',
              'before_student_order','added_roster_rows','removed_roster_rows','action_sha256'}
    if (not required <= set(action) or action['schema']!=ACTION_SCHEMA
            or action['action'] not in ('bind_row','unbind_row','undo_identity_action')
            or any(not isinstance(action[key],str) or not action[key] for key in ('id','at','exam_student_id','reason'))):
        raise ExamError('身份动作凭据格式无法核验，已保留原历史。')
    hashes=('source_basis','before_row_sha256','after_row_sha256','action_sha256')
    if any(not isinstance(action[key],str) or not re.fullmatch('[0-9a-f]{64}',action[key]) for key in hashes):
        raise ExamError('身份动作凭据SHA无法核验，已保留原历史。')
    _receipt_row(action['before_row']);_receipt_row(action['after_row'])
    identity=action['exam_student_id']
    if any(action[key]['id']!=identity for key in ('before_row','after_row')):
        raise ExamError('身份动作原行身份不一致，已保留原历史。')
    if any(action['before_row'][key]!=action['after_row'][key] for key in ('local_label','class','excel_row','total','scores')):
        raise ExamError('身份动作不能改写原来源行或成绩，已保留原历史。')
    order=action['before_student_order'];added=action['added_roster_rows'];removed=action['removed_roster_rows']
    if (not isinstance(order,list) or any(not isinstance(value,str) or not value for value in order)
            or len(order)!=len(set(order)) or identity not in order
            or not isinstance(added,list) or not isinstance(removed,list)):
        raise ExamError('身份动作原行顺序或占位行依据不完整，已保留原历史。')
    for row in added+removed:_receipt_row(row)
    added_ids=[row['id'] for row in added];removed_ids=[row['id'] for row in removed]
    if (len(added_ids)!=len(set(added_ids)) or len(removed_ids)!=len(set(removed_ids))
            or any(value in order for value in added_ids)
            or any(value not in order or value==identity for value in removed_ids)):
        raise ExamError('身份动作占位行身份或原顺序无法核验，已保留原历史。')
    if (action['before_row_sha256']!=_receipt_digest(action['before_row'])
            or action['after_row_sha256']!=_receipt_digest(action['after_row'])
            or action['action_sha256']!=_receipt_digest({key:value for key,value in action.items() if key!='action_sha256'})):
        raise ExamError('身份动作凭据内容或SHA已变化，已保留原历史。')


def _history(exam):
    history=exam.get('identity_history',[])
    if not isinstance(history,list) or any(not isinstance(entry,dict) for entry in history):
        raise ExamError('身份核对历史格式异常，已保留原记录，不能替换为空历史。')
    receipts={};undone=set()
    for entry in history:
        if 'schema' not in entry:
            # Legacy attach/bind/attendance observations remain unchanged, but
            # an unversioned receipt cannot consume a versioned action.
            if 'action_sha256' in entry or entry.get('action')=='undo_identity_action':
                raise ExamError('身份动作凭据版本缺失，已保留原历史。')
            continue
        _validate_receipt(entry)
        if entry['id'] in receipts:raise ExamError('身份动作凭据ID重复，已保留原历史。')
        if entry['action']=='undo_identity_action':
            action_id=entry.get('undone_action_id')
            if not isinstance(action_id,str):raise ExamError('撤销动作缺少原凭据身份，已保留原历史。')
            original=receipts.get(action_id)
            if (original is None or original['action'] not in ('bind_row','unbind_row')
                    or action_id in undone or original['exam_student_id']!=entry['exam_student_id']
                    or original['action_sha256']!=entry.get('undone_action_sha256')):
                raise ExamError('撤销动作与原凭据无法核验，已保留原历史。')
            undone.add(action_id)
        receipts[entry['id']]=entry
    return history


def _row(exam, identity):
    students=exam.get('students')
    if not isinstance(students,list) or any(not isinstance(row,dict) for row in students):
        raise ExamError('考试学生行格式无法核验，未更改身份。')
    rows=[row for row in students if row.get('id')==identity]
    if len(rows)!=1:raise ExamError('学生行身份缺失或重复，未解除或撤销绑定。')
    _receipt_row(rows[0])
    return rows[0]


def _source_basis(exam):
    return digest({key:exam.get(key) for key in ('id','source','config','questions')})


def _ownership(exam, binding, *, store=None, exam_class=None):
    if binding is None:return
    required={'roster_id','roster_revision','member_id','profile_student_id','submission_id'}
    if (not isinstance(binding,dict) or not required <= set(binding)
            or any(not isinstance(binding[key],str) or not re.fullmatch('[0-9a-f]{32}',binding[key]) for key in ('roster_id','roster_revision','member_id'))
            or any(binding[key] is not None and (not isinstance(binding[key],str) or not binding[key]) for key in ('profile_student_id','submission_id'))):
        raise ExamError('原学生绑定格式无法核验。')
    snapshots=exam.get('roster_snapshots',[])
    if not isinstance(snapshots,list):raise ExamError('考试冻结名册格式无法核验，未更改绑定。')
    for snapshot in snapshots:
        if (not isinstance(snapshot,dict) or not isinstance(snapshot.get('exam_class'),str)
                or not isinstance(snapshot.get('roster'),dict)):
            raise ExamError('考试冻结名册格式无法核验，未更改绑定。')
        validate_roster(snapshot['roster'])
    matches=[(snapshot,member) for snapshot in snapshots
             for member in snapshot['roster']['members'] if member['id']==binding.get('member_id')
             and snapshot['roster']['id']==binding.get('roster_id')]
    if len(matches)!=1:raise ExamError('原绑定不属于当前考试冻结名册，未更改。')
    snapshot,member=matches[0]
    if exam_class is not None and snapshot['exam_class']!=exam_class:
        raise ExamError('原绑定与成绩行考试班级不一致，未更改。')
    if (snapshot['roster']['revision']!=binding.get('roster_revision')
            or member['profile_student_id']!=binding.get('profile_student_id')):
        raise ExamError('名册版本或原档案归属不一致，未更改绑定。')
    if store is not None:
        try:
            store.check_profile(binding.get('profile_student_id'))
            if binding.get('submission_id'):
                store.check_submission(binding['profile_student_id'],binding['submission_id'])
        except ExamError:raise
        except Exception:
            raise ExamError('原匿名档案或作答归属当前无法核验，未更改绑定。') from None


def _event(before, after, identity, action, reason, *, event=None):
    old=_row(before,identity);new=_row(after,identity)
    old_ids={row['id'] for row in before['students']};new_ids={row['id'] for row in after['students']}
    entry=deepcopy(event or {})
    entry.update({'schema':ACTION_SCHEMA,'id':uuid4().hex,'at':_now(),'action':action,
                  'exam_student_id':identity,'reason':_reason(reason),'source_basis':_source_basis(before),
                  'before_row':deepcopy(old),'after_row':deepcopy(new),
                  'before_row_sha256':digest(old),'after_row_sha256':digest(new),
                  'before_student_order':[row['id'] for row in before['students']],
                  'added_roster_rows':[deepcopy(row) for row in after['students'] if row['id'] not in old_ids],
                  'removed_roster_rows':[deepcopy(row) for row in before['students'] if row['id'] not in new_ids]})
    entry['action_sha256']=digest(entry)
    return entry


def bind_exam_identity(exam, exam_student_id, member_id, *, store, submission_id=None, reason):
    """Existing binding semantics plus an immutable, reversible action receipt."""
    _history(exam);_row(exam,exam_student_id)
    result=bind_exam_row(exam,exam_student_id,member_id,store=store,submission_id=submission_id,reason=reason)
    if result==exam:return result
    history=result.get('identity_history',[])
    if not history or history[-1].get('action')!='bind_row':raise ExamError('本次绑定没有可核验的动作记录。')
    history[-1]=_event(exam,result,exam_student_id,'bind_row',reason,event=history[-1])
    return result


def unbind_exam_identity(exam, exam_student_id, *, expected_binding_sha256, reason, store=None):
    """Release only this row's identity; keep its scores and source row intact."""
    _history(exam)
    reason=_reason(reason);original=_row(exam,exam_student_id);binding=original.get('identity_binding')
    if not binding:raise ExamError('当前成绩行尚未绑定，不需要解除。')
    if digest(binding)!=expected_binding_sha256:raise ExamError('原绑定版本已变化，请重新核对；未解除新绑定。')
    _ownership(exam,binding,store=store,exam_class=original['class'])
    result=deepcopy(exam);row=_row(result,exam_student_id)
    row.pop('identity_binding',None);row.pop('local_alias',None)
    # A manually graded roster row may be released too. It remains an original
    # local observation, while the roster receives a new pending placeholder.
    row['roster_only']=False;row['identity_action_revision']=uuid4().hex
    _reconcile(result)
    result.setdefault('identity_history',[]).append(_event(exam,result,exam_student_id,'unbind_row',reason))
    return result


def undoable_identity_actions(exam, exam_student_id=None):
    history=_history(exam)
    undone={row.get('undone_action_id') for row in history if isinstance(row,dict) and row.get('action')=='undo_identity_action'}
    return [deepcopy(row) for row in history if isinstance(row,dict) and row.get('schema')==ACTION_SCHEMA
            and row.get('action') in {'bind_row','unbind_row'} and row.get('id') not in undone
            and (exam_student_id is None or row.get('exam_student_id')==exam_student_id)]


def undo_identity_action(exam, action_id, *, expected_action_sha256, expected_binding_sha256, reason, store=None):
    reason=_reason(reason)
    actions=[row for row in undoable_identity_actions(exam) if row.get('id')==action_id]
    if len(actions)!=1:raise ExamError('该动作不存在、已撤销或无法确认；未重写历史。')
    action=actions[0]
    expected=digest({key:value for key,value in action.items() if key!='action_sha256'})
    if action.get('action_sha256')!=expected or expected!=expected_action_sha256:
        raise ExamError('原动作凭据版本或内容无法核验，未撤销。')
    if action.get('source_basis')!=_source_basis(exam):raise ExamError('考试来源或评分映射已变化，旧身份动作不能直接撤销。')
    identity=action['exam_student_id'];current=_row(exam,identity)
    if (digest(current)!=action.get('after_row_sha256')
            or digest(action.get('before_row'))!=action.get('before_row_sha256')
            or digest(action.get('after_row'))!=action.get('after_row_sha256')):
        raise ExamError('原行、成绩、状态或绑定已变化，请重新核对；未撤销后续修改。')
    if digest(current.get('identity_binding'))!=expected_binding_sha256:
        raise ExamError('当前绑定版本已变化，未撤销新绑定。')
    _ownership(exam,current.get('identity_binding'),store=store,exam_class=current['class'])
    restored=deepcopy(action['before_row']);_ownership(exam,restored.get('identity_binding'),store=store,exam_class=restored['class'])
    if restored.get('identity_binding'):
        member=restored['identity_binding']['member_id']
        if any(row['id']!=identity and (row.get('identity_binding') or {}).get('member_id')==member
               and row not in action.get('added_roster_rows',[]) for row in exam['students']):
            raise ExamError('原成员已被其他行绑定，未合并或覆盖。')
    added=action.get('added_roster_rows',[]);removed=action.get('removed_roster_rows',[])
    if not isinstance(added,list) or not isinstance(removed,list):raise ExamError('原动作占位行依据不完整。')
    for row in added:
        if digest(_row(exam,row['id']))!=digest(row):raise ExamError('本次生成的名册占位行已有后续修改，不能覆盖。')
    current_ids={row['id'] for row in exam['students']}
    if any(row['id'] in current_ids for row in removed):raise ExamError('待恢复原占位行ID已被使用，未替换其他记录。')
    result=deepcopy(exam);removed_ids={row['id'] for row in added}
    result['students']=[row for row in result['students'] if row['id'] not in removed_ids and row['id']!=identity]
    if restored.get('identity_binding'):restored['identity_binding']['binding_revision']=uuid4().hex
    restored['identity_action_revision']=uuid4().hex
    # Reinsert only this action's rows using original neighbor IDs. Other rows,
    # including later additions, keep their current relative order and bytes.
    order=action.get('before_student_order')
    originals=[restored,*deepcopy(removed)]
    if not isinstance(order,list) or len(order)!=len(set(order)) or any(row['id'] not in order for row in originals):
        raise ExamError('原动作缺少唯一原行顺序，未猜测恢复位置。')
    for row in sorted(originals,key=lambda value:order.index(value['id']),reverse=True):
        index=order.index(row['id']);present={value['id']:i for i,value in enumerate(result['students'])}
        right=next((node for node in order[index+1:] if node in present),None)
        left=next((node for node in reversed(order[:index]) if node in present),None)
        position=present[right] if right is not None else present[left]+1 if left is not None else 0
        result['students'].insert(position,row)
    _reconcile(result)
    entry=_event(exam,result,identity,'undo_identity_action',reason)
    entry.pop('action_sha256');entry['undone_action_id']=action_id;entry['undone_action_sha256']=expected
    entry['action_sha256']=digest(entry);result.setdefault('identity_history',[]).append(entry)
    return result

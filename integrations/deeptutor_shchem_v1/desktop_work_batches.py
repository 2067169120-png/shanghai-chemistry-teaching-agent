"""Teacher-selected work batches and pending inputs beside existing student data.

The existing student manager owns identities, images and scores. This module
stores references and local working notes only, using its lock and atomic writer.
No model request, inferred student identity, score averaging or extra database.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4
from .desktop_state import utc_now
from .desktop_review_evidence import value
from .student_visual_analysis import _assert_components_not_reparse

CONDITIONS = {
    'unmarked': '未标记', 'needs_review': '待核对', 'missing_page': '资料缺页',
    'not_attempted': '未作答', 'not_taught': '尚未学到',
    'time_insufficient': '作答时间不足', 'unreadable': '图像难辨',
}
EDIT_FIELDS = {'score_edit','score_reason','teacher_note','decision','result',
               'primary_error','secondary_error','section'}


class WorkBatchError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.message_zh = message


def _text(text, label, maximum=1000, required=False):
    if not isinstance(text, str) or len(text) > maximum or (required and not text.strip()):
        raise WorkBatchError(f'{label}为空或过长，请检查。')
    return text.strip()


def _signature(summary):
    """Source/match changes invalidate context; a teacher score write does not."""
    rows = [{key: value(m,key) for key in ('match_id','question_page_sha256',
        'student_work_page_sha256','reference_answer_page_sha256','maximum_score')}
        for m in value(summary,'matches',())]
    return sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class WorkBatchStore:
    def __init__(self, facade):
        self.facade = facade
        self.manager = facade._student_manager_instance()
        self.path = self.manager.root / 'teacher-work-batches.v1.json'

    def _read(self, path, schema):
        _assert_components_not_reparse(path)
        if not path.exists():
            return {'schema':schema, 'revision':None}
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or data.get('schema') != schema:
                raise ValueError()
            return data
        except (ValueError,OSError):
            raise WorkBatchError('本地批次或暂存记录无法读取，原文件未修改。') from None

    def _write(self, path, record):
        _assert_components_not_reparse(path)
        record = deepcopy(record)
        record.update(revision=uuid4().hex,updated_at=utc_now())
        try:
            self.manager._write_private_json(path,record)
        except OSError:
            raise WorkBatchError('本地保存未完成，请保留当前窗口后重试。') from None
        return record

    @staticmethod
    def _expect(current, expected):
        if current.get('revision') != expected:
            raise WorkBatchError('记录已在其他窗口修改，请重新读取后核对；当前输入仍保留。')

    def list_batches(self):
        with self.manager._lock:
            record = self._read(self.path,'teacher-work-batches.v1')
            batches = record.get('batches',{})
            if not isinstance(batches,dict):
                raise WorkBatchError('作业批次索引格式不正确。')
            return deepcopy(sorted(batches.values(),key=lambda b:b['updated_at'],reverse=True))

    def get_batch(self, batch_id):
        return next((b for b in self.list_batches() if b['batch_id']==batch_id),None)

    def save_batch(self, title, class_label, members, *, batch_id=None, expected_revision=None):
        title = _text(title,'作业名称',120,True)
        class_label = _text(class_label,'班级备注',100)
        if not isinstance(members,list) or not members:
            raise WorkBatchError('请明确选择至少一份学生作答。')
        if len(members)>500:
            raise WorkBatchError('一个批次最多选择500份作答，请按班拆分。')
        seen=set();selected=[]
        with self.manager._lock:
            for member in members:
                if not isinstance(member,dict) or set(member)!={'student_id','submission_id'}:
                    raise WorkBatchError('作答引用不完整。')
                sid,sub=member['student_id'],member['submission_id']
                if sid in seen:
                    raise WorkBatchError('同一批次每名学生只选一份作答；替换旧版本后再保存。')
                self.manager.get_submission(sid,sub)  # existing ownership validation
                seen.add(sid);selected.append(dict(student_id=sid,submission_id=sub))
            all_data=self._read(self.path,'teacher-work-batches.v1')
            batches=all_data.setdefault('batches',{})
            if batch_id is not None and batch_id not in batches:
                raise WorkBatchError('该批次已不存在，请刷新批次列表。')
            old=batches.get(batch_id,{'revision':None})
            self._expect(old,expected_revision)
            bid=batch_id or uuid4().hex
            result={'batch_id':bid,'title':title,'class_label':class_label,
                'members':selected,'revision':uuid4().hex,'updated_at':utc_now()}
            batches[bid]=result
            self._write(self.path,all_data)
            return deepcopy(result)

    def available_submissions(self):
        """All histories, not only the newest 50; never read image bytes here."""
        rows=[]
        for student in self.facade.student_profiles():
            for sub in self.manager.list_submissions(student.student_id):
                summary=self.facade._student_submission_summary(sub)
                rows.append({'student_id':student.student_id,'submission_id':summary.submission_id,
                    'label':student.label_zh,'grade':student.grade,'created_at':summary.created_at,
                    'status':summary.status_zh,'candidate_available':summary.candidate_available,
                    'match_count':summary.match_count})
        return sorted(rows,key=lambda r:(r['label'],r['created_at']),reverse=False)

    def members(self,batch):
        profiles={p.student_id:p for p in self.facade.student_profiles()}
        result=[]
        for ref in batch['members']:
            row=dict(ref)
            p=profiles.get(ref['student_id'])
            row['label']=p.label_zh if p else '学生记录缺失'
            result.append(row)
        return result

    def _submission_path(self, summary, filename):
        sid,sub=value(summary,'student_id'),value(summary,'submission_id')
        live=self.manager._safe_read_submission(sid,sub)
        if _signature(self.facade._student_submission_summary(live)) != _signature(summary):
            raise WorkBatchError('题目或页面对应已变化，请重新打开本份作答后核对。')
        return self.manager._submission_path(sid,sub).parent / filename

    def load_pending(self, summary):
        with self.manager._lock:
            path=self._submission_path(summary,'teacher-review-pending.v1.json')
            data=self._read(path,'teacher-review-pending.v1')
            changed=bool(data.get('source_signature') and data['source_signature']!=_signature(summary))
            return {**deepcopy(data),'source_changed':changed}

    def save_pending(self, summary, review, changes, *, expected_revision, observations=None):
        if not isinstance(changes,dict):
            raise WorkBatchError('暂存输入格式不正确。')
        allowed={value(i,'match_id') for i in value(review,'items',())}
        for key,fields in changes.items():
            if key not in allowed or not isinstance(fields,dict) or set(fields)-EDIT_FIELDS:
                raise WorkBatchError('暂存输入与本份题目不一致。')
            if any(v is not None and (not isinstance(v,str) or len(v)>1000) for v in fields.values()):
                raise WorkBatchError('暂存内容过长或格式不正确。')
        observations = observations or {}
        if not isinstance(observations,dict):
            raise WorkBatchError('暂存状态格式不正确。')
        for key, row in observations.items():
            if key not in allowed or not isinstance(row,dict) or set(row)!={'condition','note'} or row['condition'] not in CONDITIONS:
                raise WorkBatchError('暂存状态与题目不一致。')
            _text(row['note'],'状态说明')
        if (value(summary,'student_id'),value(summary,'submission_id')) != (value(review,'student_id'),value(review,'submission_id')):
            raise WorkBatchError('不能将其他学生的输入保存到本份作答。')
        with self.manager._lock:
            path=self._submission_path(summary,'teacher-review-pending.v1.json')
            existing=self._read(path,'teacher-review-pending.v1')
            self._expect(existing,expected_revision)
            return self._write(path,{'schema':'teacher-review-pending.v1',
                'source_signature':_signature(summary),'review_revision':value(review,'revision'),
                'changes':deepcopy(changes),'observations':deepcopy(observations)})

    def conditions(self,summary):
        with self.manager._lock:
            data=self._read(self._submission_path(summary,'teacher-review-conditions.v1.json'),
                            'teacher-review-conditions.v1')
            return {**deepcopy(data),'source_changed':bool(data.get('source_signature') and
                                                       data['source_signature']!=_signature(summary))}

    def save_condition(self,summary,match_id,condition,note,*,expected_revision):
        if condition not in CONDITIONS or match_id not in {value(m,'match_id') for m in value(summary,'matches',())}:
            raise WorkBatchError('请选择本题的有效作答状态。')
        note=_text(note,'状态说明')
        if condition!='unmarked' and not note:
            raise WorkBatchError('请简要写明状态依据，不把空白直接判断为不会。')
        with self.manager._lock:
            path=self._submission_path(summary,'teacher-review-conditions.v1.json')
            data=self._read(path,'teacher-review-conditions.v1')
            self._expect(data,expected_revision)
            signature=_signature(summary)
            # Retain old observations, but mark them separately if source matches changed.
            if data.get('source_signature') not in (None,signature):
                data.setdefault('previous_contexts',[]).append({'source_signature':data['source_signature'],
                                                              'items':data.get('items',{})})
                data['items']={}
            data['source_signature']=signature
            items=data.setdefault('items',{})
            previous=items.get(match_id,{})
            entry={'condition':condition,'note':note,'updated_at':utc_now()}
            data.setdefault('history',[]).append({'match_id':match_id,'previous':previous,'current':entry})
            items[match_id]=entry
            return self._write(path,data)

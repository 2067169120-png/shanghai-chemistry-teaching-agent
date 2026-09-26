"""Synthetic grade and deterministic transport fixtures, not school data or grading evidence."""
from __future__ import annotations
from contextlib import contextmanager
import json
from types import SimpleNamespace
from .desktop_exam_data import read_xlsx, build_exam
from .desktop_exam_template import template_bytes
from .model_provider_settings import ModelProviderProbeContext
from .model_provider_probe import ProbeTransportResponse


def example(path):
    path.write_bytes(template_bytes());book=read_xlsx(path)
    cfg={'title':'合成化学考试 · 功能验收','sheet':'成绩','header_row':1,'student_col':0,'class_col':1,'status_col':2,
         'total_col':3,'max_score':100,'pass_score':60,'excellent_score':85,
         'questions':[{'column':i+4,'question':str(i+1),'max_score':25,'knowledge':k,'chapter':'合成范围'+str(i+1)}
                      for i,k in enumerate(('物质结构','反应原理','实验推理','有机转化'))]}
    return book,cfg,build_exam(book,cfg)


def candidate():
    return {'summary':'合成返回稿：仅用于测试数据绑定、显示与保存，不是实际教学结论。',
            'class_actions':[{'finding':'本次映射的第4题得分率相对较低。','question_ids':['4'],
                              'action':'先让学生列出反应条件，再对照原作答核对转化依据。','check':'用同目标短题检查条件与产物表达。'}],
            'groups':[{'target':'需基础巩固的学生','action':'先做短诊断，确认需补概念后再选练习。','check':'一周后记录实际短测，不预测提分。'}],
            'students':[], 'next_lesson':'先回看公共材料，再组织独立解释与订正。','retest':'一周后同目标短测，保留实际得分与缺考情况。',
            'limitations':['合成数据与固定返回稿不证明模型识图或评分准确率。']}


class FixtureStore:
    def __init__(self,vision=False):self.borrowed=0;self.vision=vision
    def invocation_policy(self,profile_id,expected_revision):
        if expected_revision!='fixture-revision':raise ValueError('stale')
        caps=['text','structured_output']+(['vision'] if self.vision else [])
        return {'effective_capabilities':caps,'capabilities':caps,'capability_evidence':{'declared':caps},
                'allowed_data_classes':['question_text_redacted','source_page_image'],
                'image_egress':'teacher_confirmed_visual_pages','base_url':'https://models.example/v1','model_id':'fixture-model'}
    @contextmanager
    def borrow_invocation_context(self,profile_id,expected_revision):
        self.borrowed+=1
        yield ModelProviderProbeContext(profile_id=profile_id,provider_id='openai_compatible',model_id='fixture-model',
            base_url_policy='openai_compatible_public_https_v1',base_url='https://models.example/v1',revision=expected_revision,
            api_key='synthetic-fixture-not-a-real-key',provider_kind='openai_compatible',api_style='chat_completions',local_endpoint_policy='deny')


class FixtureTransport:
    def __init__(self,value=None):self.value=value or candidate();self.requests=[]
    def send(self,request,*,cancel_event,deadline_monotonic):
        self.requests.append(request)
        response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(self.value,ensure_ascii=False)}}],
                  'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}}
        return ProbeTransportResponse(http_status=200,content_type='application/json',content_encoding=None,
                                      body=json.dumps(response,ensure_ascii=False).encode(),latency_ms=1,model_invoked=True)

"""One-time exact-context integration on the isolated revision branch."""
import os
from pathlib import Path
import subprocess

BASE='2a061bca4852e285fad411895e9702c2ae82d3af'
BRANCH='maintenance/exam-revisions-20260919'
assert os.environ['GITHUB_REF']=='refs/heads/'+BRANCH

def git(*args):
    return subprocess.check_output(['git',*args],text=True).strip()

assert git('rev-parse','HEAD^')==BASE
paths=[
    'integrations/deeptutor_shchem_v1/desktop_workbench/exam_dashboard.py',
    'integrations/deeptutor_shchem_v1/desktop_workbench/exam_followup_panel.py',
    'integrations/deeptutor_shchem_v1/desktop_exam_followup.py',
    'integrations/deeptutor_shchem_v1/desktop_exam_practice.py',
    'README.md','docs/WORKFLOW_STATUS.md','docs/roadmaps/audit-followup.md',
]
for name in paths:
    assert git('rev-parse','HEAD:'+name)==git('rev-parse',BASE+':'+name)
text={name:Path(name).read_text(encoding='utf-8') for name in paths}

def replace(name,before,after):
    assert text[name].count(before)==1,(name,before)
    text[name]=text[name].replace(before,after,1)

dashboard=paths[0]
replace(dashboard,'class ExamDashboard(QDialog):','class _ExamDashboardBase(QDialog):')
text[dashboard]+='\n\nfrom .exam_revision_ui import ExamRevisionMixin\n\n\nclass ExamDashboard(ExamRevisionMixin, _ExamDashboardBase):\n    """Existing exam dashboard with explicit local evidence-version support."""\n'
replace(dashboard,
    '    def request_ai(self):\n        if not self.exam or self._task:return\n        profile=self.model.currentData()',
    '    def request_ai(self):\n        if not self.exam or self._task:return\n        try:\n            self.check_disk_current(); self.render()\n        except ExamError as error:self.status.setText(error.message_zh);return\n        request_stamp=self.advice_input_stamp(); expected_disk=self._saved_revision\n        profile=self.model.currentData()')
replace(dashboard,
    "        scope=self.classes.currentData()\n        def done(result):\n            self.result=result;self.result_scope=scope;self.dirty=True;self.ai_result.setPlainText(advice_text(result));self.save_current()\n            self.status.setText('讲评草稿已返回。统计数字没有被模型修改，请教师核对建议后安排教学。')",
    "        scope=self.classes.currentData()\n        if request_stamp!=self.advice_input_stamp():\n            self.status.setText('确认期间资料已变化，请重新核对发送内容。');return\n        def done(result):\n            self.accept_advice_result(result,scope,request_stamp,expected_disk)")
panel=paths[1]
replace(panel,'class ExamFollowupPanel(QWidget):','class _ExamFollowupPanelBase(QWidget):')
start=text[panel].index('    def preview_paper(self):')
end=text[panel].index('    def record_result(self):',start)
segment=text[panel][start:end]
assert 4<=segment.count('self.current_for(task)')<=8
text[panel]=text[panel][:start]+segment.replace('self.current_for(task)','self.current_for(task,require_fresh=True)')+text[panel][end:]
text[panel]+='\n\nfrom .exam_revision_ui import FollowupRevisionMixin\n\n\nclass ExamFollowupPanel(FollowupRevisionMixin, _ExamFollowupPanelBase):\n    """Existing task editor with an explicit score-evidence review gate."""\n'
followup=paths[2]
replace(followup,"    return {'schema': 'shchem.exam-followup.v1', 'id': uuid4().hex, 'exam_id': exam['id'],",
    "    from .desktop_exam_revision import bind_task\n    return bind_task(exam, {'schema': 'shchem.exam-followup.v1', 'id': uuid4().hex, 'exam_id': exam['id'],")
replace(followup,"            'targets': targets, 'library_filter': None, 'links': [], 'exports': [], 'attempts': []}",
    "            'targets': targets, 'library_filter': None, 'links': [], 'exports': [], 'attempts': []})")
replace(paths[3],"        'id', 'exam_id', 'question', 'goal', 'links', 'practice_set')})",
    "        'id', 'exam_id', 'question', 'goal', 'links', 'practice_set',\n        '_baseline_evidence', '_evidence_reviews')})")
readme='README.md'
addition='''### 本机改分与下游过期提醒（X03，未发布源码候选）

PR #30的独立题集已接纳至维护源码。本轮在“考试分析 → 学生得分与行动”增加 **核对并修改成绩、重算本地统计、改分与旧建议记录**。明确选择学生和得分项，填写新值或勾选缺失，并写出修改依据；只保存本机分析副本，不改原Excel或来源哈希。取消不保存，失败保留输入。

完整题目映射按全部小题重算总分；仍缺分则总分缺失。映射不完整时，改小题后旧总分待核对，须单独确认总分，不按差值猜算。缺考不能直接改为有成绩。0与缺失分开，相同值不制造修改历史。

旧AI建议因成绩、题目映射、试卷、说明或范围变化而停止作为当前建议；没有版本依据的旧建议也须重新确认来源。原文可历史回看，**不会自动重新调用API**。导出当前报告或带入备课时不夹带过期AI正文；重新生成需要再次确认，可能计费。

“复练与复测”会显示受影响任务的 **核对改分影响** 入口。教师查看旧/新依据并确认保留安排后，保存新的核对记录，再重新预览输出。最初基线、实际复测和旧文件不删改；重算统计或核对任务都不等于旧AI建议重新有效。旧任务缺完整依据时明确补核，不凭本次S编号跨考试自动迁移。

[实现、状态和验收边界](docs/qa/2026-09-19-score-revisions.md)。这不是外部Excel自动同步或已分发文件召回；已复制到备课的旧材料也不会自动回写。v0.1.101已发布EXE不含本轮新增入口，最终检查结果以对应源码CI为准。

'''
replace(readme,'## 使用导航\n',addition+'## 使用导航\n')
assert text[readme].split('## 使用导航',1)[1]==Path(readme).read_text(encoding='utf-8').split('## 使用导航',1)[1]
replace('docs/WORKFLOW_STATUS.md','# 当前功能闭环与实施状态\n',
    '# 当前功能闭环与实施状态\n\n## X03 本机改分增量（未发布源码）\n\n当前维护已接纳PR #30。本轮候选补本机改分→本地重算→旧AI排除→任务依据待核对→教师明确重核→重新输出；原Excel、旧成品和实际复测保留。完整/部分映射的总分分别处理，0与缺失分开。外部重新导入、跨评分模块和已带入备课材料的传播仍待做，详见[本轮审查](qa/2026-09-19-score-revisions.md)。以下历史版本口径不变。\n')
replace('docs/roadmaps/audit-followup.md',
    '| X03 | 改分后统计、建议、任务的过期提示 | 部分版本检查，自动变更传播待做 |',
    '| X03 | 改分后统计、建议、任务的过期提示 | 新增本机改分、统计重算、AI版本绑定与任务依据重核候选；外部Excel/跨评分模块/已分发材料传播仍待做，见[验收边界](../qa/2026-09-19-score-revisions.md) |')
for name,value in text.items():Path(name).write_text(value,encoding='utf-8')
subprocess.run(['python','-m','compileall','-q','integrations','runtime/deeptutor_shchem'],check=True)
subprocess.run(['python','runtime/deeptutor_shchem/documentation_policy.py'],check=True)
git('diff','--check');git('add',*paths)
script='.maintenance/integrate-exam-revisions.py';git('rm',script)
assert set(git('diff','--cached','--name-only').splitlines())==set(paths)|{script}
git('config','user.name','github-actions[bot]')
git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
git('commit','-m','feat: wire local score revision gates into the existing exam workflow')
git('push','origin','HEAD:refs/heads/'+BRANCH)

# 维护者入口：版本、运行与验收

[教师使用说明](../../README.md)是场景入口，不再放提交时间线和测试数量。详细教师步骤分在[首次使用](../teacher/getting-started.md)、[备课与出卷](../teacher/lesson-and-paper.md)、[作业与考试](../teacher/assessment.md)、[保存与支持](../teacher/save-and-support.md)。这里不是旧主指南的整本复制。

## 版本边界

文档调整基线为 `0e395471eb55ab8181e79b6242ee5510694628b6`，即 PR #31 已合并的维护源码。默认 main 仅有此前发布指南同步，应用源码未整体整合，不将 README 重写视为主线代码整合。

| 范围 | 当前可据以说明的状态 |
| --- | --- |
| v0.1.101 Windows 试用包 | 已发布预发布版；原受测源码 c0ca081，发行提交8375019。后续维护按钮不在原包内 |
| PR #29 | 维护源码已接纳：存储／题篮保护，取消维护 push 的固定版本自动发布 |
| PR #30 | 维护源码已接纳：独立复练题集、整理窗口与回调保护 |
| PR #31 | 维护源码已接纳：本机改分、过期建议与复练依据核对 |
| Excel 对比重导 | 上轮仅本地候选，未提交、未完成 Windows UI 验收。本轮不搭载该候选代码 |
| 本轮教师手册与帮助 UI | 独立候选；是否接纳与测试结果以对应 PR 最终记录为准，不递增软件版本 |
| 自由拖拽画布、跨业务迁移 | 不列为已发布完整能力 |

## 保留的工程记录

[当前闭环与未完成项](../WORKFLOW_STATUS.md) · [原59项与X01—X10](../roadmaps/audit-followup.md) · [更新记录](../../CHANGELOG.md)

[PR26与发行关系](../qa/2026-09-19-pr26-audit.md) · [独立题集](../qa/2026-09-19-practice-set.md) · [题集编辑](../qa/2026-09-19-practice-editor.md) · [改分与下游](../qa/2026-09-19-score-revisions.md)

旧 QA 文档是当时的证据，不改写其中的历史运行状态。新验收结果写到 PR 及对应报告，不放进教师 README 开头。全仓测试、真实模型质量、多屏与新 EXE 不由某个定向集合代替。Issue #24 与未完成项独立追踪。

## 在明确源码版本上运行

先检查并保存本机未提交修改，不执行强制重置。源码与已发布包不同，选择需要验证的分支或标签后再运行。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

使用根目录 `启动源码桌面版.cmd`；遇到旧 VBS 打开旧 EXE 时改用上述入口，必要时运行 `检查运行环境.cmd`。不要把程序升级等同于题库或学生资料迁移。

## 文档维护规则

根目录一份当前 README：教师场景、输入、操作、输出、完成检查和重要使用边界。细节进入有明确任务的教师章节，开发/QA进入本页及既有状态文档。保留旧页内锚点作为兼容入口，不保留长导航和整本历史副本。

文档检查由“最低字数＋最低截图数”改为场景覆盖、章节可达、片段与图片真实存在、体量上限、版本与备份边界。不为通过检查凑字或堆图，不取消源文件与发布检查。旧截图保留实际版本说明；新 UI 截图只有真实运行后才可加入。

提交前运行：

```powershell
python runtime/deeptutor_shchem/documentation_policy.py
python -m pytest -q staging/coordination/deeptutor_gateway/tests/test_complete_readme.py
```

普通维护 CI 保持内容只读。完整 Office／EXE 检查与显式发布分开，不改旧标签或重发固定版本。

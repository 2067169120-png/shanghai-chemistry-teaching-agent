# 当前功能闭环与实施状态

**核查日期：2026-09-18。基线：已发布 v0.1.101，发行提交 `83750196ea1ffbdd116095175a87d5fba4e76b7c`，受测代码 `c0ca081357ccea5094ac449381dbbcb38654f47a`。**

[完整使用指南](../README.md) · [59项任务与X01—X10](roadmaps/audit-followup.md) · [原始0.1.99审查](ux/0.1.99-closure-audit.md) · [发布版源码证据](qa/0.1.101-readiness.json) · [同版EXE证据](qa/0.1.101-package.json)

## 1. 结论与口径

**尚未形成全业务闭环。** 当前已有两条较完整、但范围限定的操作链：

- **备课链：** 原有完成初稿 → 教师选择转换 → 编辑教学环节、任务与评价 → 同源生成教案/PPT/学习单 → 保存重开 → 可选登记成品备份 → 独立恢复后读取和重新预览。
- **讲评链：** 已有作答 → 教师正式评分 → 同卷统计，或明确映射的Excel → 本地考试分析 → 教师按真实标签选复练题 → 任务显式题目子集 → 两版练习卷 → 记录真实复测日期、分数及状态 → 报告。

两条链能通过材料追加衔接，但学生身份、题目对象、下游变更传播与跨业务恢复还未统一。教材与原题库未随程序分发；“在合成样例中能走通”不代表教师的全量资料已经完成整理。

本次检查包含当前代码入口、跨服务调用、保存对象、导出/恢复规则、原发行验证记录，以及本地可执行的相关核心回归。**没有在本轮重新运行全部Windows/EXE验收，也没有调用真实API、读取教师私有题库或验证课堂提分。** 原发布记录中的1280项测试不计成本次新增测试。

本轮本地定向集合完成**134项核心与文档测试**（0失败、0错误、0跳过），其中12项为文档完整性/历史策略测试，其余覆盖考试、复练、节点、初稿转换、草稿、备份和搜索；12项不是额外再加一次。原生Qt/Windows与自由画布未在本轮重验。

| 状态 | 本文含义 |
| --- | --- |
| 已接通（限定范围） | 有实际入口、保存/读回或输出路径，且存在相应发行验证；范围仍按表中限制 |
| 部分接通 | 某一方向或某类对象能传递，缺少反向同步、完整身份或其他必要环节 |
| 待接通 | 尚无满足该验收目标的发布实现；不以同名按钮或候选代码代替 |
| 本地候选 | 本次仓库基线外的试写代码，未进入发布版，不列入教师可用能力 |

## 2. 按教师工作逐链检查

| 工作链 | 当前入口与结果 | 状态 | 仍缺什么；对应任务 |
| --- | --- | --- | --- |
| 首页 → 原作品/当前编辑/题篮 | 打开既有作品，继续表单，沿用真实题篮 | 已接通（限定范围） | 全局教学活动中心不是现有首页；G01已有基础，不扩写成日程系统 |
| Word/PDF/图片 → 题答范围 → 标签 → 选题 | 原生Word阅读、图片导入与核对；来源支持的标签筛选 | 部分接通 | 复杂跨页题、失败项单独重试、旧公式确切定位、裁片质量和统一标签；C02—C09 |
| 题目 → 题篮 → 两版试卷 | 完整主题材料，连续文字号，扫描号手工区域替换，Office实际分页 | 已接通（限定范围） | 同图不同实例、图内引用、全卷细目表、调序撤销和原件保真；D03—D09、#14 |
| 学生原页 → 分析 → 教师评分/诊断 | 原作答同屏，AI稿、教师总分和诊断分开 | 部分接通 | 逐评分点改判、批注后批改卷导出、真实识图质量；E01/E02、F04 |
| 多人作答 → 作业批次 → 连续复核 | 明确选择已有作答，跨人暂存，重开读取 | 已接通（限定范围） | 不是批量上传与自动循环分析；小窗原图面积仍偏小；E01、G02 |
| 正式评分 → 班级/同卷统计 | 只读取正式记录；同原题/满分/答案来源分组；缺失不补零 | 部分接通 | 快照不会因之后改分自动重算，未必覆盖完整原卷；E06、X03/X07 |
| Excel＋试卷 → 可视化与API讲评 | 本地确定性图表，独立可选API建议，班级范围、历史和HTML报告 | 已接通（限定范围） | 列映射方案复用、整班名册、学生档案显式绑定与多考可比性；X01/X04—X07 |
| 考试问题 → 复练 → 复测 | 真实标签进入原题库，教师明确关联题集，两版实际导出，记录结果 | 部分接通 | 仍依赖全局题篮；无自动排序/等值提分/复测附件批改；E03/E04、X02/X06/X08 |
| 考试讲评 → 备课材料 | 追加当前统计与已核对建议，保留原材料 | 部分接通 | 单向文本摘要，不是成绩对象/目标/题目到节点的全程绑定；B03/B05、X03 |
| 初稿 → 教学环节 → 三类成品 | 预览选择追加，目标逐条确认，修改后提示旧输出，整套重生成 | 已接通（限定范围） | AI按节点限定字段修订、无损复杂公式/表格、局部刷新、Office外部回写；B01—B06 |
| 三类成品 → 备份 → 恢复继续 | 0.1.101登记node-exports按需备份；恢复可读三文件、重建实际PPT预览 | 已接通（限定范围） | 未保存/未登记或未勾选不等于打包；不是全业务迁移；A08/X09 |
| 教学模板/课堂工具 → 节点/反馈 | 模板可带入，工具可用，教师录入反馈可追加备课 | 部分接通 | 节点直接调用工具、独立授课态、反馈绑定到目标/学生；B08/B09、G03/G04 |
| 热点/材料命题 → 装置/有机图 → 答案 → 成品 | 已有材料与命题参考组件 | 待接通完整链 | 缺完整化学图编辑、确定性结构核验及统一成品验收；G05—G07 |
| 教材图＋知识＋个人资源 → 自由拖拽编排 | 仅0.1.102本地候选 | 本地候选 | 尚未在当前仓库集成，更不能称Windows可用；见第5节 |
| 全工作区 → 换电脑继续使用 | 当前只有范围明确的备课备份 | 待接通完整链 | 原题库、学生/作答/批次、考试/复测分别迁移及身份保持；A08/X09 |

上述状态是具体流程的核查，不把“已接通”推导成对应整个59项任务都完成。

## 3. 关键连接的代码与验证证据

| 检查对象 | 实现落点 | 现有测试/发行记录 | 判断 |
| --- | --- | --- | --- |
| 原批次正式分数进入考试 | [desktop_batch_exam.py](../integrations/deeptutor_shchem_v1/desktop_batch_exam.py)、[work_batch_desk.py](../integrations/deeptutor_shchem_v1/desktop_workbench/work_batch_desk.py) | [test_teaching_closure.py](../staging/coordination/deeptutor_gateway/tests/test_teaching_closure.py)、报告`teaching_closure` | 同卷/正式评分/缺失处理有连接；仍是快照 |
| 任务子集与实际复测 | [desktop_exam_followup.py](../integrations/deeptutor_shchem_v1/desktop_exam_followup.py) | `explicit_task_subset`、`dated_retest_saved`、`actual_pdf_export` | 真题引用与实际记录可保存；题目未独立于全局题篮 |
| Excel数据和API隔离 | [desktop_exam_data.py](../integrations/deeptutor_shchem_v1/desktop_exam_data.py)、[desktop_exam_ai.py](../integrations/deeptutor_shchem_v1/desktop_exam_ai.py) | [test_exam_analysis.py](../staging/coordination/deeptutor_gateway/tests/test_exam_analysis.py)、报告`exam_analysis` | 图表不执行模型代码；模拟接口不证明建议质量 |
| 初稿选择转入设计 | [desktop_lesson_import.py](../integrations/deeptutor_shchem_v1/desktop_lesson_import.py)、[lesson_import_dialog.py](../integrations/deeptutor_shchem_v1/desktop_workbench/lesson_import_dialog.py) | [test_lesson_import.py](../staging/coordination/deeptutor_gateway/tests/test_lesson_import.py)、报告`lesson_import` | 只追加所选环节，不覆盖；复杂图表/任务语义须教师整理 |
| 目标覆盖、锁定和输出版本 | [desktop_lesson_design.py](../integrations/deeptutor_shchem_v1/desktop_lesson_design.py)、[desktop_lesson_output.py](../integrations/deeptutor_shchem_v1/desktop_lesson_output.py) | [test_lesson_design.py](../staging/coordination/deeptutor_gateway/tests/test_lesson_design.py)、报告`lesson_design` | 显式关联/过期提示有效；不是自动验证教学充分性 |
| 登记成品的备份恢复 | [desktop_backup.py](../integrations/deeptutor_shchem_v1/desktop_backup.py)的`_design_outputs`、`plan_backup`、`restore_backup` | [test_lesson_output_backup.py](../staging/coordination/deeptutor_gateway/tests/test_lesson_output_backup.py)、`three_files_restored`/`actual_restored_pptx` | 原表“新三类成品未纳入”已过期，应改为可选范围已接通 |
| 搜索缓存与读图 | [desktop_explorer_index.py](../integrations/deeptutor_shchem_v1/desktop_explorer_index.py)、[desktop_word_image_batch.py](../integrations/deeptutor_shchem_v1/desktop_word_image_batch.py) | [test_explorer_speed.py](../staging/coordination/deeptutor_gateway/tests/test_explorer_speed.py)、报告`explorer_performance` | 后续筛选/多图重复工作减少；首次读取和超大图仍可能慢 |
| 备课草稿/恢复 | [desktop_preparation_drafts.py](../integrations/deeptutor_shchem_v1/desktop_preparation_drafts.py) | [test_desktop_preparation_drafts.py](../staging/coordination/deeptutor_gateway/tests/test_desktop_preparation_drafts.py)、报告`lesson_design` | 正式草稿、恢复副本与节点可读回；不等于所有业务都有异常恢复 |

原发行报告中的相关源码和EXE条目没有未捕获异常，但范围都写明为合成资料、局部场景。此次不会修改原JSON以重写过去的验收结果。

## 4. 四个不能忽略的断点

### 4.1 改分以后，其他模块没有自动跟着重核

教师改分 → 原评分记录更新；已生成的考试统计、API讲评、复练安排和课件不会全部自动重算。当前正确做法是重新读取正式评分并重做相关分析。下一步应记录来源版本，提示“统计需更新／建议需重核／课件材料可能过期”，由教师明确刷新，不静默调用API。对应X03、B05、F03。

### 4.2 复练题目仍依赖全局题篮

任务保存的是显式子集，避免把其他课的题带进去，但移除全局题篮中的项可能使旧任务需要重新关联。下一步应建立任务自己的稳定引用与版本快照，保留来源核验；不是复制一套平行题库。对应X02、D04。

### 4.3 Excel编号、原作答档案与原卷题号尚未统一

本次考试S01不等于下次考试S01；同名学生不能自动合并。原卷第8题、成绩列“Q8”和题库主题的小问也不能靠编号相似即认定同一对象。需要教师确认学生绑定、整卷—主题—小问关系和非重叠评分单元，再谈趋势、自动推荐及跨考分析。对应X04、X07。

### 4.4 目标有对应项，不代表内容已经充分覆盖

原0.1.99反例：目标同时包含动态平衡判据与平衡常数表达式，实际只选了一道判据题。0.1.100/101在教学设计中能显示未关联目标并由教师确认；这项检查尚未自动连到复练选题、题量/用时细目表，也不能替代化学语义判断。对应B03、D03、E03。

## 5. 最新参考图需求：正式纳入待验收，而不是提前写成可用

0.1.102存在不同命名的本地候选模块（`desktop_composition`与`desktop_page_composition`方向）。**本次不择一猜合并、不用候选测试数量替代仓库状态**；它们不属于v0.1.101源代码与程序。

| 验收场景 | 必须产生的实际结果 | 对应原任务 |
| --- | --- | --- |
| 资源目录＋筛选＋完整题面 | 找题保持来源、共同材料和题篮，不一次解析全库图片 | C07/C09、D01/D02/D08 |
| 题文/选项/解析与配图编辑 | 原件不改；明确当前作品副本；教师答案不泄漏到学生稿 | C03/C04/C05、B02 |
| 教材图片/知识卡/个人资料 | 可搜索已有条目或指定文件页/区域；素材带来源，能下次复用 | C09、G05—G07 |
| 纸面拖拽与内容顺序 | A4/A3/16:9；移动位置与排序区分；双栏不丢长题；保留所有条件 | B01/B06、D03/D05/D07 |
| 保存重开与成品 | 两道完整题＋教材图＋知识卡＋个人文字，PDF/PPTX实际与画布核对 | B04/B05/H02 |
| 作品及备份继续使用 | 统一原作品管理；恢复后原素材和独立题集继续编辑/导出 | A08、X02/X09 |

以上使用一个验收作品串联验证，不另造“漂亮效果图即完成”的标准。现有已发布功能不因候选未完成而删除。

## 6. 本次文档修正与仓库整理

- 当前根目录移除18份版本主README副本，保留唯一主指南；各模块自己的README用途不同，继续保留。
- 当前使用指南补齐0.1.101转换与备份路径，说明0.1.102未发布及main源码/发行维护分支的区别。
- 原任务表的A08、B01—B05、H01/H05等改为当前状态；59个ID及X01—X10均保留，历史发行流水账转由CHANGELOG与Git查看。
- 本页作为固定路径持续更新，不再为每次文档审查复制一整份主指南。旧报告作为有版本的历史证据保留，当前结论以本页及任务表为准。
- 修正指向已删除README的链接；发布检查、测试与AGENTS约定禁止重复归档并验证目录和实际图片。

不删除标签、分支历史、Release附件、原始QA或实际教学资料；不为了让主页面显示新版而暗中合并全部应用代码。主页面使用指南明确对应发布版。

## 7. 下一批推进顺序

**先完成新编排的实际使用链，再减少跨模块重复劳动。** 优先顺序为：统一0.1.102候选并在Windows验证 → 独立复练题集 → 来源/学生/题号映射与过期提示 → 全业务分模块备份 → AI限定环节修订与化学图形完整成品。

每一项都验收“输入来源→操作→保存→重新打开→实际成品/反馈→可继续使用”，同时记录失败路径。自动能力和人工核对入口分开，避免继续用新页面或功能名称掩盖数据未连接的问题。

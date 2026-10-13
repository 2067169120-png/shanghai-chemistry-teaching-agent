# 完整教材图文课日常采用与原页绑定修复

日期2026-10-09，维护源码；ROOT=`E:/项目/上海高中化学等级考/outputs/2026-10-09-workbench-course-adoption`。整体目标active；未提交、发布或重建EXE，renderer38／prompt35／EXE0.1.101保持。[教师入口](../teacher/lesson-and-paper.md#daily-course-materials)。

## 当前结果

真实Normal profile为 `C:/Users/20671/AppData/Local/ShanghaiChem/LocalCandidates/2026-10-07-same-lecture-questions/profile`。新增完整可编辑draft `prep-f13ba905c7eb46fcabd69279219b763e`，56备课／61业务对象／1523文件；`normal-adoption-result.json`记录原生create/compare_draft和精确readback。原60对象及原revision、其余state exact，原1443非state文件SHA／字节／mtime exact，新增79依赖文件。旧同名高分子课prep-0eeaf609abe340ecb389e65d1a97d0fa保留，不按标题或design ID去重覆盖。

课堂仍40页，22不同教材裁片＋7课外实景＋1原题图，81资源、87资产池、9原生clickEffect、原教学设计／知识网／完整讲义保留。87是资产池数量，不能称87张课堂图。实际Office为40页PPT、48页Word教案、21页Word讲义；同页答案和分步内容按点击展示，正文禁词审计无待核实／来源尾注／防御句，来源页码与授权在后台。

## 三处来源修正

上轮三新crop保存的receipt使用source revision，但Original误填preview revision，造成原页回看失败。此次在新版本纠正以下crop的source_revision并重算Original SHA，旧版本原样保留：

| crop | 原PDF／印刷页 | 内容 |
| --- | --- | --- |
| 3f96b1641d9d6a87059b8161cbbaab2d62114bb38d69ab7525de10658b43bc87 | 90／86 | 完整图7.24乙烯聚合 |
| a7d33bd157031ff74b4426b38d2c8cff985c1e0290a3671579ecc10db5a23570 | 91／87 | 塑料正文两个完整段 |
| 06663d87ea8e296acd63bafe0790f53ac836dac0776426d2469f8f33bf65a217 | 92／88 | 完整图7.25双轴八例 |

原书SHA 92f09535689cbb1192526a7f8b4dde2254869052d8e3b6d88b8bed9d354094ce、10,110,288字节保持。`native-classroom-source-binding-v2/source-binding-corrections.json`记录逐项before/after、真实preparation_textbook_crop_original binding及RGB verification。`verification-reviewed.json`明确除了3 revision/digest外还清空旧exports 1→0，以便重新导出；旧verification.json措辞通过新文件解释，旧回执不覆盖。全部81工作内容、素材位置、图文设置、教学环节和布局字段exact；旧74完整资源与83旧IMG exact。

真实reader调用TextbookPreparationCropService.crop，再渲染源PDF、核对框选RGB及SHA，返回crop_pixels_verified后才通过。独立只读复核重算三receipt、Original digest、reader binding digest，并核调用链；未重跑其171页渲染，不能把静态复核称独立视觉。根初次在PRIVATE真实目录验证，采用后在Normal存储再验证三次原页成功。

## 保存、入口与验证范围

| 验证 | 结果与证据 |
| --- | --- |
| 完整备份 | complete-normal-backup/verification.json；1444文件ZIP 1,357,135,253字节，CRC及独立解压全SHAbytesmtime相等。 |
| 采用前准备 | 只读preflight79项依赖具体列出；实际采用新建文件并逐项所有权登记，native guarded compare_draft提交。 |
| 原生认领边界 | ownership-regression/verification.json；6 OWN原生StateStore输入，不能扩称产品UI测试。 |
| 两轮独立采用审阅 | explorer/native-adoption-review-fixes.json、native-adoption-source-fix-review.json，P1／P2已同点修复，最终无新增。 |
| 新源绑定 | explorer/source-binding-review/review-v2.json及md，旧74和全81工作内容、source修正、171页比较记录核对通过。 |
| 原生布局 | all-rendered-pages-pixel-comparison.json；teacher91／static40／classroom40，共171页公开几何和实绘RGB exact。 |
| 实际Office等价 | actual-office-source-only-equivalence.json；本机PowerPoint／Word共109页原生文字和完整RGB像素exact。 |
| 根直接视觉 | 本轮直接看新Office PPT3、19、20、21、22详页；不是109页人工逐页阅读全文。此前有限视觉范围见上轮QA。 |
| Normal原生打开 | native-normal-course-readback-v1/verification.json；实际PreparationDraftDialog精确ID，QTest点打开按钮Accepted，payload exact，3新增原页RGB通过。 |
| 教材目录 | 同一只读facade.paths.workspace_root=DATA，386 concepts及目录实际读取成功。 |
| 正常入口 | normal-launcher/verify-only-check.json；87图／81资源五资格／6成品＋3metadata／PPT40页9点击全只读核对，无Qt或产品import、无文件写尝试。 |

旧private launcher把workspace_root设成CODE，原教材目录访问失败；本次只修实际交互分支至DATA，CODE保留import用途，旧脚本字节保存在previous-launcher-correction。旧私有稿source指针不覆盖，以Normal新稿作为当前入口。新launcher交互分支按固定draft ID读取当前revision，用户编辑后仍能用；verify-only仅对交付冻结版本作验收。完整MainWindow／Recovery并未构造或验收，也未生成其快照，独立chooser和只读服务不扩大为完整应用实测。

## 失败、保护与后续

前次无execute的preflight在错误source revision处失败，Normal仍1444。source-binding-v1用layout dict==误比较Qt对象及变化的composition容器失败；v2改逐公开几何和全部实绘像素，保留失败目录。私有入口修正辅助子进程cp936解UTF-8失败属于harness，另纯读取完成验收，旧子回执保留。

采用初审P1是读取commit后snapshot可能误认另一writer状态，回滚会覆盖并发成果；现仅exact_owned_append确认唯一新增记录和原生token、其余完整state不变后认领；uncertain commit保留依赖不删除。P2门禁字段位于resource顶层，已修实际读取层级。正常真实CAS守卫核完整state、文件manifest和donor源hash；回滚只能在原生锁下完整状态相等且文件所有权一致时执行。

Normal新稿原资格candidate_only=true，其余human_reviewed／generation_allowed／publication_allowed／official=false保持，工程检查不解锁化学或发布资格。QA415、六原件、上一QA与union60源码保持；知识库只改化学项目与INDEX该路由、旧历史及其他项目字节保留，不编辑95-Source。

原文回看后还缺携带真实章节和课堂插入位置的“本节按知识点继续选材”统一入口。具体源码证据来自本轮explorer，后续应复用已有knowledge_options、真实知识题关联、knowledge_textbook_options和freeze_insertion，而非把同节候选冒称已确认知识关联。更广教材／讲义题导入、QUOTE、44视频、原59及X01—X10仍继续，不把当前Normal采用视为整体完成。微信读书路线永久停用。

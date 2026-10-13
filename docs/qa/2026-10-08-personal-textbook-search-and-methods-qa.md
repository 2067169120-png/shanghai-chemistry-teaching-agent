# 个人教材书内查找与完整讲义方法单元（维护源码，未发布）

2026-10-08，e6ae源码。个人教材新增书内原文、目录与知识笔记查找；讲义知识目录补齐五个无Heading样式的明确“思维建模”单元。源码和定向测试已冻结，两项已完成独立真实Qt操作与根代理限定看图。讲义交接在既有课程的内存副本验证，本轮未保存正常课程或生成新Office。

证据根目录：`OUT = E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/`。当前教材只读快照见[教材元数据](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/read-only-current-normal-book.json>)；搜索源码与测试冻结见[搜索回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/worker/personal-textbook-search-worker-final-checks-v3.json>)，讲义编译与兼容回执见[讲义回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/handout/worker/verification.json>)。

## 个人教材查找范围

正常教材为《沪科技化学选修第二册【高清教材】》，105个PDF页、9个已保存章节条目、3条知识笔记。9个条目是保存的章节范围，不据此称原书有9个大章。原PDF与归档PDF均为74,172,867字节，SHA-256为`4eb751c3bc879d2ad41a2faa723b3b4af05b29ff78f3e89a2eaf9727ebb8a848`；该快照读取前后正常state SHA相等。

**在书内查找…** 可选择整本书或当前已保存章节，按“原文、目录与知识笔记”“仅原文文字”“已保存目录与知识笔记”查找。结果按类型标明，每页25项，显示总匹配与命中数；原文保留提取摘要，知识笔记显示完整保存内容及全部依据PDF页。输入限制为1—160字，化学式大小写保留；原文采用PDF已有文字，不新增OCR或持久索引。没有原生文字的范围仍可查已保存目录与笔记，再阅读原页。

选中结果与依据页，点 **定位原页** 才进入原PDF页。搜索、停止、关闭与定位均不自动选材或入课，原笔记勾选与待选图表保留。后台读取支持取消；旧查询、换书、章节或笔记版本变动、关闭后的回调不能覆盖当前结果，定位前再次核对来源与结果身份。相邻单张／批量裁图同时核对备课目标与busy状态，批量增加移除后重选同页的迟到结果守护。

## 五个完整方法单元

PKG-066《第07讲 化学实验方案的设计和评价》的[解析版原Word](<E:/项目/上海高中化学等级考/sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-066/第07讲 化学实验方案的设计和评价（复习讲义）（上海专用）（解析版）.docx>)，SHA-256为`edd4696d06e0d3ad762319eb3f17f609729fec086781b393f212fc90b9eaef6e`，1,136,494字节。当前生产读取426个原块、109处原图／显示对象、14个表格块；完整原块、表格及图引用见[原块证据](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/handout/production-blocks-426.json>)和[原图元数据](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/handout/production-assets-109-and-display-objects.json>)。区块编号不当作印刷题号或页码。

| 新独立单元 | 连续原块范围 | 保留内容 |
| --- | --- | --- |
| 物质制备型实验方案设计解题思路 | b56—61 | 四点方法正文及原分隔图 |
| 猜测假设型实验设计的一般思路 | b107—109 | 五步骤方法图及原分隔图 |
| 方案评价的一般思维模板 | b257—258 | 五步正文及原分隔图 |
| 操作、现象与结论一致性判断表格类实验题解题程序 | b290—292 | 判断流程原图及原分隔图 |
| 表格型实验设计与评价解题步骤 | b311—313 | 审题／析题／解题流程原图及原分隔图 |

b258正文完整为“弄清实验目的→明确各方案中各步原理→找出各方案中的异同→分析方案中‘不同’的优劣→得出评价结论”。其蓝色分隔图是附加显示对象；该段有正文，五个单元均有方法正文或方法图。原分隔图与原图块照原范围保留，未静默删除。

当前目录共19条：旧14条Heading目录的ID、范围和顺序关系保持，新增5条`word-method-bN`独立身份。识别只接受独立段落中的明确literal标题，排除当前生产题面、答案与共享材料全范围；相同字样在题答正文中不提升成知识目录。单元连续保留至下一真实Heading、下一明确知识单元或印刷题开始之前，含期间原图与表格；旧大栏目仍经过原角色检查。

[五单元编译证据](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/handout/worker/actual-method-compile-verification.json>)显示5个完整知识资源、8处原图引用、4个唯一图像资产，正文、表格及图块按原范围保留。该探针使用DATA原Word的只读source port，编译证据中的`source_path`如实指向DATA原件；与下节根使用正常归档来源的Qt交接回合分别记录。

## 旧知识卡兼容

新增目录改变全目录摘要，旧H1—H9栏目可精确接受由同一当前原件、reader revision与原Heading目录重新计算的旧摘要；任意旧摘要、改动原件、角色、范围、正文、表格或原图均不能通过。封存的旧卡与其`outline_revision`不改写，同段重新选材可去重并保留教师已有编辑。

正常实验课三张旧知识卡b29—41、b202—208、b209—242已只读核对原内容、角色与来源，并通过旧目录身份验证。旧卡`prepare → save → evaluate → knowledge_context`完整关联链及同段去重由隔离fixture回归覆盖；不把该测试称为向正常课写入关联或保存新课。旧卡继续按封存摘要返回选择身份，避免关联入口二次校验误拒绝。

## 最终测试分组

| 最终范围 | 结果 | JUnit |
| --- | --- | --- |
| 搜索及个人教材／裁图／阅读11模块 | 170通过，0失败／错误／跳过，111.89s | [related-tests-final-v3.xml](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/worker/related-tests-final-v3.xml>) |
| 明确方法标题、完整范围与旧目录兼容 | 21通过，0失败／错误／跳过，25.47s | [new-method-sections-final.xml](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/handout/worker/new-method-sections-final.xml>) |
| 知识与例题关联、旧卡完整链 | 47通过，0失败／错误／跳过，236.61s | [links-regression-final.xml](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/handout/worker/links-regression-final.xml>) |
| 知识／表格／picker／混合交接及关联UI等9模块 | 376通过、3跳过，0失败／错误，464.47s | [knowledge-picker-handoff-regression-final.xml](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/handout/worker/knowledge-picker-handoff-regression-final.xml>) |

各组按各自最终JUnit报告，不把旧跑次或跨组数量累计成独立测试总数。上述秒数为命令墙钟；源码／测试SHA、AST与完整命令保存在两份worker回执。

3项跳过均来自`test_handout_material_handoff`的无图片样例，JUnit实际原因如下：

- `test_invalid_or_foreign_mixed_batch_never_publishes[text-only-image_archive]`：`source has no image`。
- `test_source_change_during_owned_copy_rolls_back[text-only]`：`copy race requires image`。
- `test_concurrent_successful_image_owner_is_never_removed[text-only]`：`copy race requires image`。

首次裁图跨目标边界复现2失败、原文摘要断言预期不符1失败，以及方法标题落在题答范围内的初次2失败均保留原日志；最终守护／范围修复后通过，不累计为额外验收。

## 实际Qt与限定视觉

[native-search-v2回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/native-search-v2/verification.json>)已exit0：使用当前正常教材不可变快照及生产PersonalTextbookPanel／SearchDialog，实际查“分子”得69项（原文62、目录4、笔记3），25项分页并到达第26—50项；只查整理内容得4目录＋3笔记。“表面张力”得2原文页＋1笔记，显示完整笔记并实际定位PDF56。勾选笔记、待选PDF55、已预览参考、未保存的笔记编辑文字、当前章节与接收目标epoch均保留。1120×850和420×900查找窗口实际可用；6张机器截图中，根直接看02／03／04／05／06共5张，01未直接看。定位与原勾选见[实际PDF56截图](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/native-search-v2/05-located-real-pdf56-with-choices-preserved.png>)。

另用既有129页QA《选修第一册》作范围对照：“水解平衡”整本有5个原文页结果、7处命中，含PDF78；当前隔离章节PDF68—70无命中。该QA书与正常105页教材分开记录。正常1089文件及只读QA415文件的SHA／字节／mtime均相等、保护写尝试0；没有MainWindow、state writer或profile锁，无导入、保存及Office。工作线程已退出。`task_failures=[]`只保留为脚本采集字段，不据此推断全任务桥没有失败事件。

搜索native-v1在GUI前因根适配脚本将`task_failed`误作`failed`信号而失败，[原失败回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/native-search-v1/adapter-failure.json>)保留；v2移除错误监听，未修改产品以迎合脚本。

[native-methods-v6回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/native-methods-v6/verification.json>)已exit0：从正常解析版归档原Word读取真实来源，用生产`load_preparation_draft`投影保存记录为实际payload；既有14图的原字节复制到OUT隔离task store。真实HandoutKnowledgePicker勾选5单元、逐项改本课引导问题，筛选保留所选五项，取消不改原课；点 **确认本次知识段选择** 得到5个完整原范围。随后由生产编译器整批编译，并通过LessonCompositionPanel追加到既有25资源的内存副本，共30资源；原25资源完整保留。教师预览32页、静态学生预览29页，教师范围包含原来3页仅教师内容。3张旧卡仍返回封存的目录摘要，重复编译这5项新增0资源，实际撤销恢复原25项、重做恢复完整30项。

v6有7张机器截图，根全部直接看：01完整方案评价正文与引导问题、02原b108流程图、03实际420×900入口，以及05／06／07／08四个候选方法页的课堂画布。问题标题、原流程图和评价五步正文保留。窄窗全文reflow后viewport可能显示附近b76等上下文，只核确认按钮、选择题名与107—109完整确认范围，不声称缩窗后始终显示b108原图。

四个画布页是现有课程内存克隆追加的候选素材页。短方案评价单元独立页仍有留白，后续可与例题图文编排；原方法图保留原位图像素，本轮没有确认40人投影质量、完成正式课堂PPT设计或生成实际Office。根限定观察及12张直接看图的身份见[根限定视觉回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/root-native-limited-visual-review.json>)，原稿方法内容另见[原方法限定审阅](<E:/项目/上海高中化学等级考/outputs/2026-10-08-personal-textbook-fulltext/handout/root-method-source-review.json>)。

讲义native-v1—v5原JSON均保留：v1误将全文“方案评价”筛选期待为唯一行；v2只读SourcePort缺preview／asset回调；v3隔离task未复制既有14图；v4误认图片存放根；v5误用保存record中的`topic`，该字段在`core_fields`。v6修正根适配脚本并使用生产payload投影，未修改产品来绕过图片、来源或角色检查。各次正常1089／QA415全manifest保持。

## 保护与交付边界

讲义v6正常1089及只读QA415文件SHA／字节／mtime前后相等、保护写尝试0，两份冻结产品源码与归档原件身份保持，Qt窗口已关闭、任务终端exit0。本轮讲义只读证据与API编译另核两份DATA原Word不变；正常仍52份备课／57对象。worker未初始化正常facade或profile writer，原生回合未初始化普通MainWindow或state writer，未写正常或中央数据，未生成Office、保存备课、普通备份或发布。原文／方法图语义核对属于根代理限定原稿审阅，不将当前回执称为独立真人、完整化学或整课通过。微信读书停用，既有发布包未更新。

[教师使用步骤](../teacher/lesson-and-paper.md#personal-textbook-search-and-methods) · [上一轮选材清单与配图检索](2026-10-08-material-discovery-qa.md)。

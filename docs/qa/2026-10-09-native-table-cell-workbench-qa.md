# 原生讲义表格单元格编辑验证

日期：2026-10-09。当前维护源码 renderer v38，应用版本未递增，试用 EXE v0.1.101 未重建。整体目标继续；本页记录本轮工程核验及明确观察范围。操作入口见[教师指南](../teacher/lesson-and-paper.md#native-table-cell-edit)。

证据根目录 `ROOT = E:/项目/上海高中化学等级考/outputs/2026-10-08-native-table-cell-workbench`。本轮产品与测试共11文件（7产品、4测试），原8文件冻结及局部续页增量、UI后续清单分别保留。根最终冻结核对初始44文件中只有composition、panel、projection三文件变化，其余41文件保持；image_display另有独立修改前后清单，不在初44内。早先计数推算的验证脚本失败原件与JSON保留，未修改产品规避核验。源码新增功能不能视为旧EXE已更新。

## 已完成的功能与数据约束

讲义知识原表可在解锁后逐格修改文字。可选 `working_tables` 只记录与原单元格坐标绑定的图前、图间、图后文字；Original、原网格与合并、块ID／顺序／角色、逐次原图引用与字节不变，同一asset重复出现仍按不同图片块保留。仅实际变化写入侧车；全部恢复原片段时删除相应记录，最后一个表恢复后删除整个侧车。续接合并格只读，缺失／多余槽、保留占位前缀、摘要或工作文字失配均拒绝。

画布、PDF、PPT与原生Word共用确定性表格投影；图片偏移由当前文字重算。Word保留可编辑原生单元格、gridSpan／vMerge与原图顺序，PPT保留原图和可编辑文字。工作文字中的原有化学上下标转写继续使用既有规则。旧自由整段改稿不迁移，仍保留普通文字及独立原图；活动表格侧车与“知识讲解与笔记”互斥，须明确切回普通图文排版。

默认选项 **内容增加时只为这项素材续页** 只在单元格实际变化且原外框不足时执行。所选素材完整重排，新页插在其第一张原页之前，仅移除该素材独占的旧页；其他素材所有字段、位置、共用页和空白页保持。教师、学生静态、课堂三种范围均检查，实际学生独立两栏策略也纳入；合法空学生范围不作错误，不能安全跨页时拒绝并保留输入，不缩字、不截断。只改标题、锁回、取消或无修改应用不触发续页。父级原expected CAS保留，单元格与局部分页为一次历史事务，可整体撤销／重做。

真实来源覆盖062 B45（21原生格／9图出现）及072 B108、B112、B270、B305、B307；B101／B245／B247旧改稿未迁移。旧未版本化教师范围与source_roles v2分别保持，B270所在完整单元相邻的两PBrush对象与两次flip没有被更改或扩展许可。完整新单元为B254—297的44来源块、32图片出现、21唯一显示图、3原生表格；图片出现次数、唯一图与全课101资产分别计数。

## 测试与独立审查

以下为分范围记录，存在覆盖和重复运行，不相加成一个总通过数。

| 范围 | 已有回执 |
| --- | --- |
| 后端／输出60项 | `root-final-backend-tests.xml`：60 passed；逐层损坏、锁／CAS、无操作、回退、图槽、真实来源、Word结构与图字节、画布／PDF／PPT、保存／普通ZIP均覆盖。 |
| 既有表格回归72项 | `backend-worker/legacy-v1.xml`：72 passed，包括旧教师自由改稿仍保留九次独立图片。 |
| 最终UI45项 | `ui-worker/layout-followup/pytest-ui-layout-final.xml`及`final-freeze-verification.json`：45 passed，含局部续页默认选项、有效变化、超框保文、取消、一次历史提交。根早期`root-final-ui-tests.xml`的28项是前阶段记录。 |
| 局部排版19项 | `backend-worker/layout-followup/result-v1.xml`：12新增及7既有图片布局回归通过；真实75素材失败稿仅目标资源重新分配外框。 |
| 根最终UI＋局部排版57项 | `root-final-layout-ui-tests.xml`：最终冻结代码57 passed，21.34秒；与上述45项UI及12项新增排版重叠，不能再与19项工作代理布局测试累计。 |
| 独立后端159断言 | `independent-review/adversarial-backend-results.json`：159条独立断言通过，含真实来源、错误类型、跨资源、重复／覆盖坐标、未解锁与迟到CAS。 |
| Unicode修复6＋18检查 | `independent-review/ui-unicode-probe-final.json`的6组空操作与`ui-exact-edit-final-context.json`的18项精确编辑检查通过。 |
| 独立续页46检查 | `independent-review/layout-followup/layout-probe-results.json`：46项通过，含真实学生两栏安全拒绝、共享页面、无图表格、一次父级提交与迟到CAS。 |

后端初次测试中 Paint prog_id 预期与课时字符串的两个夹具错误分别纠正，原失败回执仍保留；最终根60项通过，不能把早期失败当成产品通过。局部续页的Qt图形弃用提示来自既有flip显示调用，未改变原图字节。作者及独立探针仅使用最小QApplication、纯内存owner和指定私有目录，未构造MainWindow或恢复控制器。

## 真实问题、修复和原生操作阶段

独立审查发现并关闭两个P2。第一项是Qt将未改的NBSP、CRLF、U+2028／U+2029规范化，导致解锁、切格、锁回、应用这种空操作也写入侧车；最终编辑器按实际更改范围保留未改原文字。第一轮修复又暴露分支缓存问题：重复行删除、撤销再走另一删除分支时，渲染文字哈希会误复用另一分支的CRLF；最终用实际Qt撤销位置、原文摘要及新命令分支失效处理。原失败见`ui-unicode-probe-results.json`与`ui-exact-edit-after-first-fix.json`，最终关闭依据见上述6＋18检查和独立`review-report.md`。

Native v1为验证脚本点击位置错误：QTest点击拉伸复选框中心未命中解锁指示区，截图仍锁定，产品正确丢弃未解锁输入。根改为实际指示区点击并断言已解锁；该次exit 1保留于`root-native-v1-harness-failure.json`，不计产品通过。

Native v2在真实全课把B270第1行第1格改为“羟甲基苯酚\n观察结构”后，数据有效，但旧外框在教师／学生静态／课堂都报“完整内容尚未放完”。这是实际容量问题，见`root-native-v2-capacity-failure.json`及`native-layout-probe-v1/verification.json`。修复保留换行和完整24磅文字，增加可见的所选素材局部续页选项，没有缩小测试样例。修复后同一素材仍为13个完整外框、32次原图，其他74项保持；后端详证见`backend-worker/layout-followup/data-v1/test_actual072_multiline_edit_0/verification.json`。

Native v3原生脚本终端exit 0，`native-table-cell-edit-v3/verification.json`记录真实锁定表、解锁改单格、固定原图槽、一次应用及精确撤销／重做。75素材／101资产的私有稿只重排该素材13外框，学生48页，B270四次原图保留，Original及其他素材与位置相等。当时机器阶段状态为`native_cell_edit_single_undo_save_restore_requires_root_visual_review`；随后根直接查看四张原生截图，范围见下节，不等于人类或课堂放映验收。

## 保存、恢复与原文件保护

Native v3通过私有草稿保存、实际版本读回和普通ZIP人工触发恢复，回执记录完整payload、101图及两代12个历史登记成品相等。ZIP含2草稿、114文件、无缺图／缺文件、无recovery快照。此处的12文件是已登记历史输出保留，不等于本轮最终Office转换或最终成品验收。

生产`restore_backup`在本轮OWN临时恢复路径构造过惰性的`PreparationRecoveryStore`读取器，用于备份记录校验；回执明确列出该临时路径。没有MainWindow、RecoveryController、自动恢复或恢复快照写入，因此不能称“零Recovery构造”。作者／独立探针没有构造这个读取器；根人工恢复与它们的边界分别记录。

Normal原1444文件与只读QA来源415文件，SHA-256、大小、mtime_ns全清单前后相等，`protected_write_attempts=[]`。来源JSON、原Word和所读原图亦逐份检查；所有新稿、验证图、临时存储和输出在本轮ROOT内，未接纳到正常工作台。既有正常35页高分子课程仍保留19处教材原内容切片和6张实物／现场照片，本轮没有新增课外照片或改写正常课程。

## 根实际Office、恢复与限定观察

根Office脚本终端exit 0，`native-table-cell-office-v1/verification.json`及实际分页回执记录48页PPT、48页Word教案和22页学生Word，保留9组点击。新成品作为第三代追加，前两代12文件字节保持。`native-table-cell-office-restore-v1/verification.json`终端exit 0，普通ZIP含三代18登记文件、101图、无缺文件；完整payload、图片和全部成品逐字节恢复相等，显式从恢复草稿选择窗口重开，原生学生表格及反应页无布局错误。

`root-limited-visual-review.json`记录9张直接查看的大图：Native v3四图、实际PowerPoint第6页、教案第7页、课堂Word第2页，以及恢复后的表格／反应两图；另看三份文档1—12页联系图，仅作概览。原图各在相应格内，增行完整显示，问题标题与图文解释保持；不写作全48／48／22页重新审查。实际Office结构／图字节核验以`root-cell-edit-office-audit.json`为准，Source允许真实重新分页，非Source原35页按page_id核对，不用错误的“只变一页”断言。最终文档策略、完整README14项及文件冻结见`documentation-checks-final.json`和`root-final-audit.json`，历史失败和阶段回执保留。

首次逐页结构脚本在第24页顶层形状数量断言失败，回执`root-office-audit-v1-failure.json`及冻结脚本保留。原生XML对照确认旧／新都是55个canvas元素，其中18个space、37个实际内容形状；PPT将其中27个形状放进四个点击动画组，加上顶层10形状仍为37。审计须按原形状ID递归匹配并验证组为恒等变换，不能只数顶层或跳过动画页；该诊断不依赖更改产品或重新生成成品，最终执行结果仍以上述结构与根收尾回执为准。

永久知识库仅同步索引中的化学项目行／日期和匹配项目页；共享索引原有其他项目内容保持。本轮写入器CRLF转换问题修正后，原项目历史字节与其他索引行精确核对，初次与最终记录分别保留在`root-project-memory-sync.json`／`root-project-memory-sync-v2.json`，不编辑95-Source。

工程通过不解锁化学正确性、人类复核、发布或来源权威性；`human_reviewed=false`、`chemistry_reviewed=false`、`published=false`。原59项／X01—X10、其他来源缺口及整体目标继续，微信读书路线仍停用。没有提交、发布或重建旧Release。

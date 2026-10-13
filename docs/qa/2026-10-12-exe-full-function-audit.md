# 2026-10-12 本地 EXE 全功能审查

本文保留2026-10-12的0.1.102 r1—r4审查历史。后续源码整合、工作台设计、0.1.103构建与发行见[2026-10-13验收](2026-10-13-workbench-design.md)；历史失败不追改。

## 最终身份与关闭条件（0.1.102历史）

已实测r4：BuildTag **v0.1.102-20261012-full-r4**，EXE SHA-256 **5bab11ff70d6e4520b0bfd42ce2a4d2962a6428fbc235a67eb4de8abde8b6208**；源码ledger **57257ac6a62d328f2d2816c9ba2f256bf25236e4999ab4ebb9dc63275cb095ea**。680源码、503 AST、65资源、420包文件／183483539字节，构建自然0。r3至r4仅QA模块变化，产品与入口字节、65资源和418包文件SHA未变，base_library.zip内155成员内容相等；r3扫描限定Office结果按该组件比较保留，未声称r4新执行扫描。

r4普通detached真实factory精准启闭自然0，复制420文件全部SHA相等；正式进程内Qt QA绑定同EXE SHA，主13素材／11资产／OUT31保存重开与三出口相等，本节3材料／7资产和Original／角色／教师已确认B37工作值相等。撤销后纯QA读取无composition的空结构发生KeyError，自然exit15，未执行完整来源ZIP及第二factory恢复。

受控源码整合已完成：12源码、45测试共57目标；680源码成员与r4 ledger相等，1404其他成员未变。README、状态文档及路线上一轮候选当时未应用；后续更新使用2026-10-13记录。r1—r3各自SHA与失败保留原范围；公开发行在本次历史截点仍为[v0.1.101](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/releases/tag/v0.1.101)。

## 六层证据与完整清单

72用户功能组与原59项／X01—X10逐项映射，六层可同时存在：

| 层 | 含义 | 不能推出 |
| --- | --- | --- |
| implemented | 源码、UI、保存路径在声明范围存在 | 文件或按钮存在不能推出PASS |
| local_backend_verified | 具名源码断言和版本对应结果 | 合成或候选不等实际模型或EXE |
| current_exe_probe_verified | 同一EXE SHA的限定探针及严格成品gate | 自然exit0不等scope接受或全功能通过 |
| actual_user_workflow_verified | 明确真实输入、动作、保存、完整重开及输出／恢复 | 准备输入、复制旧成品、SOURCE_ONLY不等冻结包全链 |
| partial_or_missing | 原要求未做、未完整或本次具体阻塞 | 已有部分不能一概写未实装 |
| external_not_verified | 真实API、模型质量、放映、跨设备等无对应证据 | 不用合成请求或只读Timeline冒充 |

当时矩阵与20组“已有／还缺”保留在 E盘审查报告 `E:/任务输出/上海高中化学等级考/2026-10-12-exe-full-function-audit/inventory/report/feature-state-review.md` 和 72／69机器映射 `E:/任务输出/上海高中化学等级考/2026-10-12-exe-full-function-audit/inventory/report/feature-state-model.json`。这是既有要求审查，不新增roadmap，不计算全项完成率；原要求仍见[审计任务表](../roadmaps/audit-followup.md)。

## 版本分开的实际结果

| 版本／范围 | 已有结果 | 仍不能称通过 |
| --- | --- | --- |
| r1包与九CLI | 九页、冷主窗、限定合成结果有旧SHA；九CLI自然0但最初8/9scope接受 | closure-lite历史Office退出false不能在PID稍后退出后回填当时成功；不借给r3 |
| r1真实课稿 | 13素材／11资产正式保存后8.105秒生成新6文件，Original、图、角色、旧版保持 | 教学设计质量、所有Office页或全课堂放映 |
| r1实际Office文件 | 新教案Word实际6页／学生PPT实际9页；自有只读COM Timeline识别第9页shape11、trigger1并退出 | 未执行Slideshow点击，不是r3全PPT／UI检查 |
| r2真实profile | 真生产factory、13素材／OUT31保存重开、三主文件SHA导出 | SourceFirst“选材来源在打开期间变化”，正式exit15，无ZIP／恢复 |
| r3扫描组卷 | 冻结SHA绑定，自然0／137秒，4自有Word严格退出；新建、改号、undo、两版1/2页、换序CAS、区域复用、8文件出口通过 | 合成Word题图、手工像素选择，不含OCR、实际model或真实题库质量 |
| r3真实profile | 正式flag、frozen=true、expectedSHA一致、run_source_first=true，13素材／OUT31保存重开和三出口通过 | 三材队列编译约8分钟后not_added、count0、字形错误；ZIP／二次恢复未到达 |

r3 SourceFirst原始失败：**“行内文字存在无法显示的字形，请载入支持的本地字体。”** 正式进程exit15，partial证据保留。后续r4通过正常工作副本入口仅修改教师已确认的B37教学文字与解锁状态，Original保持；实际三材料加入通过，后续纯QA撤销断言失败另列。最终恢复由2026-10-13新包继续核验。

离线实际r2 PYZ检查复现了Windows synthetic111把同一冻结物误判变化；原r2异步失败未返回offendingpath，与该根因的关联仍属推断。r3 statidentity候选70源码检查及实际PYZ完整SHA已核，不增加运行时校验豁免，也不把源码修正当业务通过。冷新卷读私库问题用专用basic K01—K19 fallback修正，21源码unique通过；不运行bulk标注、不伪造教材目录。这些修订并不证明r3 SourceFirst已经成功。

## 真实课输入和输出质量

真实主课输入的 lesson_design.nodes 为0，教案框架多空字段。Root已查看Word首页，知识目标只有一句，其余框架空。手工目标、框架、节点、活动和评价编辑功能存在；本样本未填满，输出生成不能称完整教学设计质量或化学教学充分性已通过。

静态文件、模型看图、COM Timeline、真实Slideshow、真人教学评价分别记账。后台保留Original、来源、版本和检查证据；不把审计来源、待核或失败提醒写入教师生成教学正文。

## 源码、UI、必要回放与原失败

| 检查组 | 原始记录结果 | 范围与处理 |
| --- | --- | --- |
| 原后端3993 | 3747pass／83call fail／141setup fail／22skip，自然exit1 | 原224失败全部分类，647源manifest无漂移 |
| 必要ACL185回放 | 184pass／1本机HTTP policy blocked | 原审计误拦保留，不是实际网络或模型连通通过 |
| 补充后端4434 | 4259pass／100call fail／49setup fail／26skip，自然exit1 | 收集190候选文件、实际189文件4434；1历史脚本缺失明示，不删为全PASS |
| 两后端组 | 共8427具名节点自然终态 | 原始与必要回放分开，重复运行不相加造unique |
| Source UI2016 | 原1221为1197pass／24fail，补795为757pass／38fail，无pending | 137文件、OWN样本，不是最终EXE全部PASS |
| UI62失败修订 | legacy4→12pass，narrow7→39+4，new15→23，supp36→194，2标签含于15组 | 原失败保留，对应修订已随后整合，各组不累计为新unique PASS |
| 后端必要合同回放 | 旧合同9相关44pass＋类spy3pass，mixed2相关32pass，Word2相关4pass | 具体反例／新合同验证，不覆写原失败 |

源码UI原harness曾两次OwnStop、一次AV；原进程记录保留，必要原节点自然补验与最终137文件ledger覆盖分列，不声称全部原进程自然结束。

42个archive inspector policy拒绝、历史输入／资产缺失、审计guard误拦、外部projection写入、真正产品缺口与旧合同分别分类。不能全称桌面产品失败，也不能从报告消失。具名节点、失败和自然退出见 后端原汇总 `E:/任务输出/上海高中化学等级考/2026-10-12-exe-full-function-audit/backend-tests/result-summary.json`、补充汇总 `E:/任务输出/上海高中化学等级考/2026-10-12-exe-full-function-audit/backend-tests/supplement/result-summary.json`、UI汇总 `E:/任务输出/上海高中化学等级考/2026-10-12-exe-full-function-audit/ui-tests/UI-summary.json`。后续正式整合45测试／12源码已按目标SHA、AST和变更范围核对；本轮文档与完整README检查在最终文档应用后由Root执行一次，结果见2026-10-13 QA。

NoSource／NoWarnings相关3模块prompt v36／source v3／course v6的116候选断言已进入r3；旧Original／schema保持，不表示实际模型教学质量已测。

## 窄窗限定范围

个人教材模式候选修正释放48px，39＋4聚焦通过，个人／builtin入口不串；7种尺寸viewport高度：

| 窗口 | 阅读高度px |
| --- | ---: |
| 1360×920 | 304 |
| 1000×920 | 261 |
| 700×960 | 282 |
| 700×920 | 242 |
| 700×800 | 122 |
| 580×960 | 219 |
| 420×960 | 126 |

后四种仍低于250px，不称所有窄／短窗充分阅读，不为本次审查临时扩为全面responsive改造。

## 实际保存和备份范围

| 范围 | 内容 | 限制 |
| --- | --- | --- |
| 默认备课ZIP | 已保存草稿、作品分类、题篮引用、恢复记录 | 非整机、原题库或学生／考试／复练完整迁移 |
| 勾选图片／成品 | 所选备课引用图片、登记任务／返回稿／成品及对应裁片回执 | 未勾选或未登记仅保引用，不能保证随包带走 |
| 显式完整来源 | 课程实际引用完整Word source batch及同批原文件，当前样本98份 | 同批未选文件也带上，未引用整库不自动打包；r4在纯QA撤销断言后未到达完整恢复 |
| 教材原PDF／机器位置 | 原书版本SHA与本机位置明确确认 | 普通备课ZIP不默认带外部五册PDF或机器绑定，搬家不按同名替书 |
| 个人教材专用ZIP | 原PDF、目录／章节、依据、候选／修订进度、笔记 | 非全工作区，外部五册、其他业务、裁选回执范围另列 |
| 学生／考试／复练 | 各业务自身记录和既有实际状态 | 分模块整迁、长期身份、附件链、跨设备未全完成 |

**0.1.102 r4完整来源状态：三材料加入通过，后续QA撤销断言中断；ZIP／第二factory未执行。** 按钮和生产路径存在，不等最终EXE完成98来源ZIP及第二次factory重开；不以准备输入包、SOURCE_ONLY或旧输出复制替代。

## 保护、存储与交付

中间保护1938文件元数据、31完整SHA检查通过；LocalCandidates搬E由全SHA核对并保兼容junction，最初routeFalse不是数据变化。Normal、QA、原件、旧成品及Office模板最终保护在后续QA列具名回执，不以中间结果替终核。旧 E:/项目/上海高中化学等级考/outputs/2026-10-12-exe-full-function-audit 保交叉链接，不迁或覆写。

0.1.102各包、原失败、具名组件检查和真实子链按本页保留；本轮0.1.103补链与发行见后续QA。实时API、教学质量、现场放映、跨设备与学生全业务恢复分别记录。微信读书停用，44视频或全库真人审查不作为本次软件检查的新增前置条件。

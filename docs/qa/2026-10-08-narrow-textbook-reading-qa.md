# 窄窗教材原页阅读 QA（原生操作与限定视觉完成，未发布）

证据根目录 `ROOT = E:/项目/上海高中化学等级考/outputs/2026-10-08-narrow-textbook-reading/`。本片改善上一轮真实700×960窗口中原PDF阅读区仅123px的限制，旧QA与冻结证据保留。

## 操作与代码核验

个人教材已加载、窄窗阅读且已勾选笔记时，预览、知识卡入课与清空勾选归入 **选材操作…**；**加入当前备课** 保持同排。QMenu转调原button.click，沿用已有预览、来源／角色／busy和明确确认流程。embedded reader的 **阅读详情** 短按钮与PDF页／缩放同行，完整说明保留在accessible text／tooltip；标题、页、翻页、裁图和记忆入口保持，独立reader未精简。教师操作见[窄窗阅读与选材](../teacher/lesson-and-paper.md#narrow-textbook-reading)。

UI冻结2产品／2测试，4 SHA／AST见`ROOT/compact-ui-final-checks-r1.json`。最终定向56 passed／22.70s／exit0（14 compact＋30 context＋6 space＋6 parallel），受影响回归45 passed／24.11s／exit0（15 personal＋25 crop＋5 source），对应`compact-ui-final-directional-r1.xml`与`compact-ui-final-regression-r1.xml`；两组与历史重跑分范围记录，不相加总数。

旧模拟fixture未安装正式UI字体，初52检查只保留历史阶段。按主窗一致的`install_ui_font`补测后，菜单单独方案在700宽页面／双行长状态下仅223px，不满足250目标，随后精简embedded详情布局。最终正式字体、129页内存PDF70、长原标题与双行位置／裁图记忆／上下文的700×960 host／588×863 page，合成viewport282px；自然知识卡成功状态仍282px，强制双行同一句成功或较长late／error状态为263px。菜单展开／Escape无材料效果，详情展开返回保原页，宽窗或回notes恢复原按钮；不把LessonDesignDialog返回写成撤销已加入知识卡。主窗原本已安装正式字体，上一片真实304／123px结果仍有效；这些合成尺寸不代替真实教材视觉。

## 真实原页与菜单操作

`ROOT/native-v1/verification.json`为passed，根确认原生脚本终端exit0。同一工作区外QA profile载入PDF70及既有恢复中的编辑器，真实QMenu展开／关闭、参考预览、明确知识卡入课、清空勾选、宽窗和notes恢复沿原入口完成。

| 实际场景 | 主窗 | 页面 | 上下文栏 | 原页阅读区 |
| --- | --- | --- | --- | --- |
| 初始窄窗 | 700×960 | 588×863 | 564×84 | 534×301 |
| 加入知识卡后窄窗 | 700×960 | 588×863 | 564×84 | 534×301 |
| 宽窗恢复 | 1366×960 | 1152×863 | 1128×41 | 798×344 |

实际窄窗阅读高度从123px增至301px，超过本片250px目标；原书、原页和选材入口保持。菜单取消保持编辑器与勾选，明确加入后新增1张**未保存QA知识卡**，旧41资源保留；清空仅清本次勾选，不撤回已加入卡片。07原图已重核为“未保存修改”，与08／10／11一致；根先前clean观察属误读，已纠正，无队列或产品bug归因、无额外代码或测试变动。

`ROOT/root-native-limited-visual-review.json`记录11张机器capture中，根模型直接查看9个独特像素画面：01／02／04／06／07／08／09／10／11；03／05与已看02的SHA完全相同，按重复图记录，不称11张逐张直接查看。限定范围包括窄窗原页、菜单／参考、宽窗恢复、清空保原页及notes恢复；06仅是短QA手工知识卡的编辑器回执，不作为新课堂或PPT教学质量证明。此范围不等于全教材／全课、人类或live验收。

## 保存、原件与版本范围

全部52份已保存QA备课记录保持相等；正常51备课／56对象／1059文件全manifest保持，原PDF的SHA／bytes／mtime保持。编辑器新增未保存卡与已存记录保护分别记录，隔离阅读记忆或表单恢复可能更新，不称整个QA profile零写。本片没有新原资料导入、课程保存或Office生成，上一轮31页PPT／26页Word与限定视觉保留当时范围，不重复计为新验收。

作品渲染v34／提示词v35／EXE v0.1.101保持，源码未发布、微信读书停用。原59项／X01—X10、中央validator exit1、人类／live／正式资格与整体active边界继续。

## PKG-066只读候选清单

本轮[PKG-066候选清单](<E:/项目/上海高中化学等级考/outputs/2026-10-08-narrow-textbook-reading/coverage/PKG-066-coverage-candidates-2026-10-08.md>)仅整理固定包的已有记录：30条讲义主张／137去重block、24方法单元、15组题面／解析原生候选（含5核心例题）、10条教材映射候选。字段存在与角色配对是候选完整性，不等于化学／视觉／答案或正式教学资格。

根主核`ROOT/coverage/root-candidate-metadata-review.json`确认12当前source SHA的元数据范围，24方法单元保留候选身份；不是化学或视觉审核。题目原页保留unknown、pair-aware答案对齐未完成、6未渲染资产及9 partial范围仍有缺口，10教材映射无section_key且blocked_pending_review。候选未正式入库，未改profiles／catalog／正式题库／源件；本片窄窗UI不解除这些门槛，不把包级block或单元页字段推为逐题原页证据。

## 证据与收尾

实现和测试见`ROOT/compact-ui-final-checks-r1.json`，真实操作、尺寸及保护范围见`ROOT/native-v1/verification.json`。限定视觉见`ROOT/root-native-limited-visual-review.json`（9个独特画面与2个哈希重复画面），候选元数据见`ROOT/coverage/root-candidate-metadata-review.json`；完整README14项、文档策略、限定diff与冻结SHA由`ROOT/documentation-final-checks.json`记录。

状态见[当前闭环记录](../WORKFLOW_STATUS.md#narrow-textbook-reading)；上一轮真实123px限制见[备课目标与切换QA](2026-10-08-textbook-preparation-context-qa.md)。

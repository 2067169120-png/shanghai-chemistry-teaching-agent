# 完整讲义知识与矢量原图 QA（正常新副本、实际 Office 与普通恢复完成，未发布）

本片始于 2026-10-07，收尾跨至 2026-10-08，文件名与证据目录保留开始日期。继续补齐 Word 讲义完整知识内容与矢量图预览／选用，维护源码未发布。
证据根目录 `ROOT = E:/项目/上海高中化学等级考/outputs/2026-10-07-handout-vector-images/`。

## 冻结实现与原文范围

- 支持本地 WMF palette 与零文字旧字体结构预检；显示转换版本为 `20261007-word-metafile-preview-v6-palette-geometry`，继续按记录结构、字体与文字约束判定可转换范围。
- `ChemWindow.Document` 仅按同容器、唯一 preview／OLE 关系的原文证据进行窄验证；原 Word／OLE 对象不执行，不据名称放开其他对象或推断化学正确性。
- standalone 图保留原 WMF source，另存有证据绑定的派生 PNG 供预览／作品使用；原图字节不覆盖。UI 使用核验后的 `display_bytes`，保留精确 `source`，并区分 `derived_preview` 与 `renderer_revision`；来源版本、原文范围与角色仍重核。
- 既有原文、知识、图片与讲义选材控件自动显示本地可转换图。**同时显示小图与符号** 未勾选时仍保留可尝试 WMF，勾选可读取全部原图；不把小字节数矢量图当作普通小图标排除。
- 本轮读取确切 b37—56 的 20 个原文块，6 处图出现对应 5 份独立显示图；b48／54 共用原 WMF，原 SHA `dbbf3a29835dbe325f65ee66e0cad30917e657f864ba8a7a633dcd5c741fd2d8`，派生 PNG SHA `b9f9e486549a69cdff38e4ebff6f4f806fdc9022197ecb535161791367886e26`，为完整双向平衡箭头。`shchem.handout-object-preview.v1` 记录同对象 preview／OLE 关系，`binary_interpreted=false`，不是执行或解释 OLE。
- 原 b49 表格中的显式完整上下标投影为 `n(H⁺)`、`c(H⁺)`、`Kₐ`，Word 保留真实上下标 run；转义、嵌套、未闭合或不支持的标记按原文保留。sealed 表格仅与紧邻完整短正文及小行内图局部分组，能容纳时正文与首个完整表头／数据行组同页，原字数、字号、表格合并、角色和图字节不变。

原 Word 包含外部关系，`ROOT/reference-word-run.txt` 记录既有 Office 门槛在启动 Word 前拒绝原件转换，exit 1。门槛未改、源文件未写；本轮没有取得原 Word 的实际物理页或原件 Office 视觉，不把 block 编号当作页码。生成的 Word／PPT 实际转换另行核验。

## 源码检查与失败记录

各组按实际冻结版本记录，互有覆盖，不相加为单轮总数：

| 范围 | 实际结果与边界 |
|---|---|
| 原后端 12 模块 | 465 passed／3 skipped／1 failed，失败为旧渲染版本字串断言，exit 1；日志和 XML 保留。 |
| 版本断言修正后定向 2 模块 | 只改过期测试为精确 `RENDERER_REVISION` 相等，原行为断言保持；50＋42＝92 passed／0 skipped，31.17s／exit 0。原 12 模块未重新全组跑。 |
| UI 当前回归 | 125 passed／0 skipped；此前 23／29 开发组已包含，不重复累计。合成显示字节核 UI 契约，真实矢量与讲义由原生课例补核。 |
| 表格上下标 4 模块 | 80 passed／0 skipped，48.87s／exit 0；初次 79 passed／1 failed 为新测试要求长原单元格仅一行，改为完整拼接比较后通过，产品未为该断言改变。 |
| 紧邻短说明局部分组 8 模块 | 160 passed／0 skipped，28.22s／exit 0；初轮 47 已包含。内存因果对照仅禁新局部分组：11 行前文条件下，旧路径说明／表头不同页，新路径同页，未改源文件。 |

## 原生阶段与最终 polished-v3

native-v1 完成原生 5 截图与确切选择 request，验收适配器误读 presentation 图字段而非原文快照导致 KeyError；native-v2 续核写回执时引用未定义 `vector`，产品未变。失败日志保留，不计两次脚本通过。native-v3 完成 20 原块／6 图出现／5 显示图、39 素材／17 图、实际 PPT31／教案25页、5点击与隔离 ZIP57715083 bytes／1稿17图6输出25文件恢复，正常1047前后保持；其 b49 裸上下标经根模型发现后修复，不将此阶段作为最终采用稿。

polished-v1 的仪器解释跨页、表格前短引言独占页，未完成 Word／备份；polished-v2 已完成实际 Office 和恢复，但末页仅一条易错点、留白仍多。最终 polished-v3 通过真实素材编辑，在作品 b55 原文后补“平衡方向／离子浓度／电离度分别判断、同温 Kₐ”的课堂笔记，实际单步撤销／重做成功；`original` 20 块与来源快照不改。

最终稿为 41 素材／19 图，教师／静态学生／课堂画布 33／30／31 页，实际 Microsoft PowerPoint31 页、Microsoft Word 教案26页，原5点击保持、题量仍2。完整知识安排在 Kₐ 后、多元弱酸前；装饰图限高、原定义与速率曲线同页，表格短引言与表头同 PPT18，完整后续与笔记在 PPT21。保留旧38素材／6环节／目标／12图与原外框。

新增沪科技选修一 PDF68／印刷62 的图3.4 pH计，经真实原页框选与来源绑定 API入课；新增 MediaLab TH Köln 的真实液体电导率仪原PNG，作者原发 CC BY-SA4.0。pH图、原照片和完整解释组成同页对照，安排在第一道实验判断题前；原件、作者、来源与许可记录在后台及单独许可记录，课堂正文没有附加来源或防御文字。

根模型限定复核 7 页实际 PPT（8、16—21）与 4 页实际教案（11、18—20）、2 张编排 UI 与作品编辑 UI。6 页 PPT／3 页 Word 与已看 polished-v2 像素文件逐字节相同，直接检查最终 PPT21／Word20；其余最终页未新看。三份生成正文文件的指定防御／来源附注命中为空；不是所有可能措辞或全课化学、人类、live 放映认证。

## 最终隔离备份与正常新副本

`ROOT/polished-v3/verification.json` 终态 exit 0。隔离普通 ZIP343049456 bytes，SHA `5eddf1a8cc30eafe89f3eb707b7fcbaba817d38a69878214893bc71b4fbe9c06`，包含4 QA备课／19图／24登记输出／45文件及recovery，缺失0；新目录恢复本课全文、19图及本课6成品相等。24是4稿累计输出，不写为本课24成品。

采用前正常1047文件 SHA／大小／mtime相等，protected写入0；这是采用前阶段保护结果。`ROOT/normal-adoption-result.json` 与 `normal-adoption-20261007T160403Z-745de5a5/adoption-receipt.json` 记录 `adopted_and_verified`／exit 0：新增《弱电解质的电离平衡：教材与完整讲义》，ID `prep-13e3d93159e64a6597b2894eebefa441`，登记成品包 `OUT-ea76ef2754f64c7fa3df91986d993550`，全文、19图与6成品重载相等。原50备课与全部55对象逐项保持，当前51备课／56对象。

采用前完整 profile ZIP980143820 bytes，SHA `870f17fb2551249d2dda3c6792f48ad3b66c7ab4eed1fa8080da275944c7276f`，全1047文件重新加载核SHA。采用时原1047文件仅授权新增备课的 `desktop-state` 变化，其余 SHA／大小／mtime保持；新增12文件为4份图片、6成品、`output.json` 与1份原pH裁选回执，正常共1059文件。7个新于本课的图片身份中3个已在其他正常课保存，实际新增图片文件为4；裁选JSON原样复制，5项候选／复核／发布门槛不改。

正常CMD入口沿既有 same-lecture 路径，在 **我的备课** 选择新标题。可直接打开 `ROOT/最终课堂/` 的《弱电解质的电离平衡-教案.docx》《弱电解质的电离平衡-课堂PPT.pptx》《弱电解质的电离平衡-学生学习单.docx》；素材许可说明与两份素材记录JSON单独保存。

`ROOT/ordinary-backup/verification.json` 终态 exit 0：普通备课 ZIP726811115 bytes，SHA `1f9fb430fb3eb05dc8c1a7bd0d5b11641a42f78597116e875005ee39fba594ff`，包含51备课／110引用图／50登记输出包／300成品文件／412总文件及recovery，缺失0。新目录恢复全部51记录相等，本课revision／全文／19图／6成品的SHA与字节相等，所有manifest文件亦相等。备份和恢复期间正常1059文件SHA／大小／mtime及状态保持，无外部输出写入。

普通 ZIP不包含其余5对象、DATA原始资料库、模型设置、学生历史与原教材裁选JSON；裁选JSON另存在正常profile及 `ROOT/最终课堂/素材记录/`，原资料继续在DATA。采用前完整profile ZIP与当前普通备课ZIP范围不同，不将普通ZIP记作全项目备份。

正常原生重开 v1 的保存稿、41素材19图和教师33／静态30页无布局错误，1059文件与状态保持；但验收脚本始终传 `frame_index=0` 且 fit未指定页，六张知识截图重复首帧。v1日志保留，不能据其写完整六页UI视觉通过。v2仅修frame index／明确page与六截图SHA唯一断言，从已保存正常课重新读取，不重放导出／入课；`normal-native-readback-v2/verification.json` 机器回执passed／exit 0，16截图中知识资源在教师／学生各6续页、各6个不同SHA，保存稿全文／41素材19图／教师33静态30页相等，无布局错误，1059文件与state保持。`ROOT/root-normal-workbench-visual-review.json` 记录根模型直接查看最终教师知识frame02—06，frame01与此前已看v1首帧字节相同，合计6页；另直接查看最终v2学生pH教材／实物电导率同页。只计这些限定画面，没有将16张机器截图或整套31页PPT／26页教案写成全部视觉验收，也没有人类或live放映验收。

## 已完成的视频身份与抽帧参考

`ROOT/video-reference-verification.json` 记录本代理前一只读轮实际重核的 44 个当前 MP4 SHA／bytes，全部与原索引相等，缺失／不匹配 0；本次写回执没有重复读取全部视频。主索引为 `E:/项目/上海高中化学等级考/outputs/2026-10-03-bdl-rebuild-plan/video-evidence/video-inventory.json`，SHA `29187a141aa1eb55e4acc874c86e0a04c44a0a25ab2cccc9bcc3a3a2598f1d66`，原始视频当前在 `E:/下载/BDL/`，索引中的旧 C 盘路径保留历史身份。

本轮实际只读查看 V27／V32／V28／V30 四张既有六帧图和 V27 29／37s、V32 39s、V10 32s 四张原帧，8 文件 SHA／bytes 与旧回执相等。V10 32s 可见小节树与原 PDF 页、分页／缩放；V27／V32支持筛选、明确候选池及折叠题答参考。V28 是分页 HTML 教学页，V30 有教材／大纲输入与 Word 教案表格；这些不是 WMF 解码或完整 Word 知识栏目控件的直接证据。

44 MP4 总长 4482.659s，约 74 分 43 秒；旧 probe 记录均无音轨／字幕轨，画面含烧录字幕。本轮没有新抽帧、连续播放或音轨核听，不称 44 视频全审或用视频解锁产品／化学质量。

## 版本、历史与证据入口

作品渲染 v34、提示词 v35 与 EXE v0.1.101保持；WMF派生显示版本见上段，源码未发布、未重建试用包。上一片262项、30页PPT／26页教案及限定视觉保留当时范围。原59项／X01—X10、中央validator exit1、独立人类／live／正式资格及全资料覆盖边界继续，整体目标active，微信读书停用。

- 已完成的本地视频身份与抽帧范围：`ROOT/video-reference-verification.json`。
- 原图／派生／对象证据：`ROOT/backend-real-wmf-memory-proof.json`；源码组：`backend-vector-final-checks.json`、`handout-metafile-ui-checks.json`、`table-script-final-checks.json`、`table-local-introduction-checks.json`与`table-local-introduction-causal-proof.json`。
- 阶段错误与续核：`ROOT/native-v1-run.txt`、`native-v2-run.txt`、`native-v3/verification.json`；最终隔离：`ROOT/polished-v3/verification.json`、`ui-stage.json`、`root-polished-final-visual-review.json`。
- 新图后台记录：`ROOT/textbook-candidates/ph-meter-candidate.json`、`extra-photos/conductivity-meter-lab-source.json`；正常采用：`ROOT/normal-adoption-result.json`与`normal-adoption-20261007T160403Z-745de5a5/adoption-receipt.json`，普通恢复：`ROOT/ordinary-backup/verification.json`；正常原生读取：`ROOT/normal-native-readback-v2/verification.json`与`root-normal-workbench-visual-review.json`。
- 根代理文档策略exit 0（`ROOT/policy-final.log`），完整README 14 passed／2.29s／exit 0（`ROOT/readme-final.log`与`readme-final.xml`），限定diff exit 0；收尾回执为`ROOT/documentation-final-checks.json`。本代理五文档本地链接／新旧锚点与限定diff检查exit 0，五文档冻结；本轮文档收尾不重新跑产品或Office。

操作初稿见[完整讲义与矢量图预览](../teacher/lesson-and-paper.md#handout-vector-images)；推进范围见[当前闭环状态](../WORKFLOW_STATUS.md#handout-vector-images)。

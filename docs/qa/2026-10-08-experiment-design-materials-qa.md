# 实验方案设计：原教材图、完整讲义与课堂成品

本片继续本地教材蒸馏与讲义选材，使用 PKG-066《化学实验方案的设计和评价》和沪科技必修第一册。作品名为《实验方案的设计与评价：目的、装置与证据》，面向上海高中化学等级考复习班，六环节共80分钟。只更新私人备课与验收文档，产品源码、应用版本、旧发行包和中央题库资格不变；微信读书路线停用。

## 原件阅读与选材

原卷、解析版分别经本机 Microsoft Word 转为21页、30页阅读副本。原文件有三处外部关系，安全转换器在 Office 前拒绝；私人展示副本只移除外部跳转关系，保留正文、内部书签、原媒体和嵌入图形回退，未替换归档原件。提取版本、内容块、资产和原文件 SHA、大小、mtime 保持。根模型只直接检查相关原页和原图，不以联系表代替全部51页逐页审核。

完整选取 b29—41 的设计要求与八项内容网络、b202—208 的三项评价原则、b209—242 的四个评价角度。原知识网保留，并增加独立放大讲解页。装饰横线缩小显示，不把横线算作知识图。教师作品中 b241 改为按具体装置的供热、压力、冷却与防倒吸要求安排拆卸；原始快照保持。

例题保留 b43—49 题面与 b50—54 答解，手动边界排除 b55 纯装饰线。题目键为 `word-question-35c087cbcd5c333e9891554e`，范围版本 `c6ededbee5f896843134a64c4f786503a54c96f095850d2f47eb134e199aa468`；它与封存范围复核 SHA 是不同字段。完整装置图、五个原小问和教师答案未拆成独立伪题。原图裁片门槛未放宽。

最终教师作品通过生产 `edit_resource` 只整理 b52 原提取的 LaTeX 下标、花括号与反应箭头显示，明确写为 `Cu＋Cl₂ —加热→ CuCl₂（氯化铜）`、`2Fe＋3Cl₂ —加热→ 2FeCl₃（氯化铁）`。两张原加热条件图及对应图示标记、答案角色与 presentation 保留，封存 original 及其 SHA 不变；这是教师工作副本的显示修正，不回写讲义原件或封存原题。

## 图文与授课安排

新增五处实际原生框选的教材切片：七节点实验流程、制氯装置、两条反应式、原理与条件原文、药品危险性表格。PDF第27／28页对应印刷第22／23页；切片保留完整像素与标题、列项、条件。洗气瓶和护目镜两张实物照片来自可核验的原 Wikimedia Commons 文件，公共领域许可、作者、原URL、采集及 SHA 保留在后台记录，不印课堂出处。不从照片猜测瓶中试剂或护目镜认证等级。

左上问题串起目的、原理、条件、装置、操作、证据和评价；正文保留可记笔记的讲解密度。知识讲解为主体，完整例题为辅助。教材二氧化锰／浓盐酸需加热的制氯路线，与例题高锰酸钾／浓盐酸路线明确区分。洗气瓶实物用于解释进气管的位置，护目镜实物与教材危险性表对应具体风险和措施。

课堂25页、教师画布27页、静态学生画布24页，三范围布局 errors 均为0；25资源、14唯一图片身份，其中五教材切片、两实物照片、三完整知识栏目和一道完整例题。四个点击资源对应7个原生点击：第11页四步气路讲解，第12—14页各一张同页答案卡；导出 PPT XML 的7个 `clickEffect` 与7组点击对象相符。生成课堂正文不附来源、候选资格或防御提醒，真实化学条件和实验安全知识保留。Word课堂讲义是完成态笔记，包含本课点击讲解，不称 Word 隐藏答案。

本机课程设计 skill 用于核对学习目标、导学问题、课堂任务与评价的衔接；实际成品仍由工作台原生编排与导出产生，未声称单独 PPT skill 流水线通过。

## 实际成品、保存与恢复

首次完成的课堂编排为 `classroom-final-v4`，实际 Office 为 `classroom-office-v2`；最终显示调整为 `classroom-final-v5`／`classroom-office-v3`，见本节后文。生产导出生成六份登记成品：教师课件、教案、课堂讲义、教师画布 PDF、静态学生画布 PDF 和学生课堂 PPT；全部文件 SHA、完整 payload fingerprint 与保存记录已核对。三份面向授课使用的实际 Office 转换如下，所有实际页 PNG、每组最多12页的联系表及文字 JSON 均保留。

| 实际文档 | 转换引擎 | 实际页数 | 逐页 PNG 宽度 |
| --- | --- | --- | --- |
| 学生课堂 PPT | Microsoft PowerPoint | 25 | 1600像素 |
| 教师教案 Word | Microsoft Word | 18 | 1200像素 |
| 课堂讲义 Word | Microsoft Word | 10 | 1200像素 |

实际学生 PPT 页数与25页课堂布局相等。office-v2 的25个学生 PPT 页与 office-v1 逐页 PNG SHA、像素尺寸全部相等；课堂讲义10页也全部像素相等。教师教案只有第10页像素变化，根模型重新直接查看该页的 b52 箭头与加热条件修正；其余17页像素相等。office-v2 结束时已另存新 QA 备课 `prep-3a1bd3c20d874e1ab7e3da6281839540`，旧58份 QA 备课全部相等，总数59；正常目录1059文件的 SHA、大小、mtime 与两份讲义原件保持，QA锁释放。office-v1 成品与原回执保留。

根模型限定视觉回执为 `passed_root_limited_visual_ready_for_private_normal_adoption`。已查看三份 office-v1 全页联系表，以及学生 PPT 第1、6、8、10、12、13、14、17、18页，教案第1、2、10、17页，课堂讲义第1、5、10页的大图；office-v2 继承像素相等页的相同检查范围，并重新查看教案第10页。限定发现覆盖教材切片与实物照片的教学关联、原知识网放大页、问题与同页答案卡、两种制氯路线区别及教案基本环节。联系表仅是概览，不等于全页大图审核；没有真人课堂试用或 live 逐次点击放映验收，不提升资料或中央题库资格。

本机 Microsoft PowerPoint 实际转换课堂PPT25页，Microsoft Word 实际转换教案18页、课堂讲义10页；六个登记成品核SHA。最终课堂-v2目录提供PPT及两份Word，课程可在工作台继续编辑，导出正文扫描无来源、防御或待核实提醒。教师b52反应式在作品副本中补完整箭头、加热条件和产物名称，封存原块、两个原条件图与答案角色保持；Office-v2修正教案第10页；最终Office-v3仅课堂第4、5页相对v2像素变化，其他23课堂页与两Word全部28页像素相同。原知识网的课堂显示高上限由72改111mm，图扩大约54%，局部重排把标题衔接到性质验证说明后；独立放大知识网页保持。

正常工作台新增《实验方案的设计与评价：目的、装置与证据》，备课 `prep-4d30bd48546e4353866ce28b739d8d29`、版本 `603bc5b7d38cae6959495615100f9b2d69327340520971d63b252ca456fbf59a`、最新成品包 `OUT-257ffaec124549c083d7d6e0e71d958f`。保存后全文、14图和6最新成品重载相等；原51备课及其版本保持。首次采用只给既有原生题目范围对象增加本题43—49／答50—54的手动边界，其他五项及其余55对象保持；首次新增23文件，正常51／56／1059变52／57／1082，不称全部旧56对象不变。最终更新仅改同一新课及其原生版本，created_at和其他56对象含范围配置保持，旧1082文件只授权desktop-state变化，新增最新6成品及output.json共7文件；现52备课／57对象／1089文件。采用前完整profile ZIP为1081663815B、SHA `fa4959fbe31113c135ca601c6d1576877c0a4628faf68e45538ab25fe3c92cea`，1059原文件通过CRC及新目录独立恢复SHA、字节、mtime核对。更新前第二份完整profile ZIP为1090738941B、SHA `44ab1b9bf3bb3a598856fbe86364bef337fec0658dd766d5be0978f1573c4159`，全部1082原文件再次通过CRC及独立恢复SHA、字节、mtime核对。

普通备份ZIP为735872682B、SHA `081cb33cf459842eb09263929078b9cfa38f8093e3475c8f6a10ee6e78eba438`。52备课、121引用图、306登记输出文件，缺失0；新目录独立恢复全部52记录、本课全文／版本／14图／6最新成品和manifest SHA、字节相等，正常1089文件与state保持。普通ZIP排除其余5对象、DATA原库、模型设置、学生历史和教材裁选JSON；未被当前备课引用的旧成品也不计普通ZIP范围；五个裁选与两张照片出处许可在最终课堂-v2/素材记录单独保存，含本次作品绑定，不称全项目迁移备份。

正常重开只读检查为passed／exit0，44机器截图按page_id与frame_index核唯一SHA，三完整知识栏目和完整五小问保留；例题在课堂分3页。根模型直接看其中6张独特大图，限定选择见root回执。教师与静态学生使用原生LessonCompositionPanel；课堂完成态使用公开renderer的普通只读Qt页面查看器，不称原生完成态切换或live点击放映。三范围0布局错误，正常全文、源角色、范围、成品与全1089文件保持。

## 证据与范围

私人完整证据目录为 `E:/项目/上海高中化学等级考/outputs/2026-10-08-experiment-design-materials/`。原件阅读见 `original-readback-r3.json`、`root-original-material-limited-review.json`；首次手动知识／题目选择见 `qa-api-v1/verification.json`；实际五次裁图与来源文字编辑见 `classroom-build-v3`、`classroom-build-v4/owned-text-edit.json`。课堂终态见 `classroom-final-v4/verification.json`、`classroom-final-v4/layout-summary.json`、`classroom-final-v4/teacher-equation-edit.json` 和 `classroom-final-v4/student-pages-unchanged.json`；一道完整例题三页答案制作的历史记录保留在 `classroom-final-v3/complete-question-derived-answers.json`。实际成品、完整 payload 和保存见 `classroom-office-v2/verification.json`、`classroom-office-v2/preparation-payload.json`、`classroom-office-v2/output-record.json`；实际学生 PPT 逐页像素比较见 `classroom-office-v2/student-ppt-pages-unchanged-v1.json`，根模型限定视觉范围见 `root-classroom-limited-visual-review.json`。

原件外部关系拒绝、首展示副本适配错误、原生入课脚本120秒超时、分页适配与手工文字来源字段错误均单独保留。完成态从已确认选择和生产API续做，不把这些失败改称通过，也不据适配器超时推断产品故障。原始讲义、教材、旧备课和旧资料保持各阶段保护检查；不会以局部机器或根模型检查替代真人、live放映或全库化学审核。

原59项、X01—X10、其他教材讲义覆盖和整体目标继续 active，未提交、推送或发行。操作见[图文编排与教学结构](../teacher/lesson-and-paper.md#paired-teaching-page)、[完整讲义原图](../teacher/lesson-and-paper.md#handout-vector-images)。

最终显示调整见classroom-final-v5/image-display-change.json、knowledge-layout-candidates及-r2/comparison.json；实际最终成品见classroom-office-v3。首次正常采用与最终同ID更新分别见normal-adoption-result.json、normal-update-result.json；普通ZIP和正常只读回看见ordinary-backup/verification.json、normal-native-readback-v2/verification.json；根最终限定Office／工作台视觉及文档检查回执保留。第5页缺问题标题的早期根观察经单图original复查已纠正，原页确有通用问题标题，未据此改产品代码。

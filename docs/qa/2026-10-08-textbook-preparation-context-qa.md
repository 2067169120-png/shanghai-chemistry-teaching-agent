# 教材书架备课目标与切换 QA（原生操作与最终只读复核完成，未发布）

证据根目录 `ROOT = E:/项目/上海高中化学等级考/outputs/2026-10-08-textbook-preparation-context/`。本轮处理教材选材时明确接收备课、切换目标保持阅读位置及异步确认归属，源码未发布。

## 操作与交接范围

教材书架顶部 **正在接收素材：** 优先显示真实载入作品名，未有作品名时显示课题；副行显示原课题（与作品名不同时）、课时和 **未保存修改／无未保存修改**。空白备课没有未保存修改不等于已经保存；该文案final-v3定向26 passed／4.80s保留历史，最终紧凑布局使用final-v4回执。**继续编辑／打开已有备课／新建备课** 使用既有备课操作。

成功切换或取消时，当前书、章节、原页、勾选与来源预览保持；切换目标后，旧目标的已加入标记按接收作品重置。选材预览仍保留，需教师再次明确确认加入，不因打开或新建备课自动入课。

异步确认绑定发起时目标。途中由A切至B，A的迟到成功或失败均不把A结果交给B；保留预览供重新明确加入。此约束不替代原来源版本与范围检查。

## 分版本检查与实际操作

- UI初轮26 passed／5.56s。`ROOT/textbook-context-ui-checks.json` 记录文案微调前定向组合78 tests／0 failures／0 errors／0 skipped，51.24s：含26新UI与52既有定向回归，互有覆盖不重复累计。最终UI final-v4为42 passed／0 failures／0 errors／0 skipped，14.31s，含30新UI与12既有space／parallel；78历史组未重跑、不与当前42相加。4文件SHA／AST记录于`textbook-context-ui-checks-final-v4.json`。
- UI回执覆盖作品名与课题、课时／修改状态、三入口请求、取消、busy守护、目标切换仅清已加入标记、原PDF第二页与来源／目录／勾选保持、迟到确认归属，以及360／420px宽度。新测试为内存控件，既有回归用临时合成文件，不计真实教材视觉。
- 根集成初定向5 passed／12.07s、较宽3模块24 passed／25.60s保留；最新main／studio／phase_a／新集成4模块23 passed／30.62s／exit 0（`root-related-final-v4.xml`），各组重叠，不与78相加。3产品与1新增集成测试SHA／AST冻结记录为`root-context-checks-final.json`；旧源section／audience字段、label代替section身份及缺测试文件的适配器错误保留，不将其计为通过。
- native-v1在GUI前因settings root位于project内而被既有门槛拒绝；native-v2使用外部独立LocalCandidates profile，验收适配器误把recent50当作全51，在GUI前断言失败。两次原profile保持，失败不计原生操作通过。
- native-v3复用同未变外部profile，state SHA与恢复原件相等，改用真实all-draft search；`ROOT/native-v3/verification.json` 机器回执passed，11截图、原PDF70页，书／节／原页／勾选／预览保持、只明确交接，隔离旧51草稿相等、QA新建后52。实际取消已有备课窗口与打开保存完整讲义作品已记录，终端exit 0。根模型直接看02／05／06后发现wide阅读区被说明栏挤小，故v3只作操作链证据，不宣称最终布局通过；UI final-v4已冻结，宽屏上下文左侧标签／右侧操作同行；阅读时隐藏重复介绍、摘要／resume，回到选材恢复这些信息。内存Qt的1366×960 host／1152×863 page中viewport336px／bar40px，个人540×900中viewport199px／bar76px，不承诺个人窄页300px。这些是合成内存PDF测量；最终实际教材readback只读同一QA profile，不重新导入或保存课程；实际尺寸与限定视觉另见下段。
- 第一次final-readback在保存备课选择窗口之后、完成读出之前由根代理中断，截图与`native-final-readback/attempt-status.json`保留、exit 1不计通过。之前归因于替换QMessageBox是提前推断，已更正；v2追加的确认handler并未触发，`restored_dirty_decisions=[]`，不能记作实际点击Yes。v2等待大课实际加载后只读完成，产品未改。

## 保存、版本与历史边界

最终实际1366×960窗口中，page1152×863、上下文1128×41、原PDF viewport798×304，三操作同行；700×960中page588×863、上下文564×84、viewport534×123。**当前限制：窄窗个人教材原页仍显拥挤，123px只验证操作与状态保持，不称窄窗阅读空间全面通过，也不承诺250／300px高度。** 合成内存Qt的336／199px测量与这些真实尺寸分开记录。

`ROOT/root-final-limited-visual-review.json`记录根模型直接看最终5张截图：保存完整讲义的选择窗口、宽屏PDF70、notes说明恢复、窄屏操作与回编辑后回同源。真实作品名与原2×40分钟课时、无未保存修改可见；阅读隐藏重复介绍、返回notes恢复、回编辑再回教材保持原书／节／reader／页／勾选／预览。6产品与2测试共8 SHA和root23／UI42／native一致，AST8通过。仅计5张画面的限定复核，不等于全部教材页／保存课程、人类或live验收。

本片“只读回看”指不导入／保存／生成课程、全部52已存作品记录相等；Qt启动／关闭与阅读可更新隔离阅读记忆或表单恢复，不宣称QA profile零写入。正常保护范围是v3原1059文件manifest保持，以及最终正常state与原PDF保持。

本轮不新增正常备课、不生成新的Word／PPT、不重复上一片31页PPT／26页Word的Office验收。native-v3机器回执中正常1059文件manifest相等、原教材PDF SHA／大小／mtime保持；正常51备课／56对象与既有成品没有新增，隔离QA52不是正常数量。最终`native-final-readback-v2/verification.json`为passed／exit 0，全部52条QA已存记录相等，正常state与原PDF SHA／大小／mtime保持、8源码／测试SHA匹配，无课程导入／保存／生成。文档不改来源、profiles或gates。

作品渲染v34／提示词v35／EXE v0.1.101保持，维护源码未发布，微信读书停用。原59项／X01—X10、旧中央validator exit1、独立人类／live／正式资格与整体active边界继续。

根代理只读核正常CMD→`open_workbench.lnk`→pythonw `launch_textbook_lesson_workbench.pyw`，CODE指当前e6ae工作区，STATE仍指`2026-10-07-same-lecture-questions/profile`；本轮未启动正常GUI。

## 证据与收尾入口

- UI契约与文案微调前组合：`ROOT/textbook-context-ui-checks.json`、`textbook-context-final-r2.xml`；文案final-v3：`textbook-context-ui-checks-final-v3.json`；最终布局：`textbook-context-ui-checks-final-v4.json`与`textbook-context-layout-final-v4.xml`。补充`textbook-context-layout-geometry-final-v4.json`的单case1 passed／3.76s已在42内，不加为43；内置notes独立540×900、context隐藏时viewport486×361／PDF10，与个人有context的199px场景分开。
- 根集成与原生尝试：`ROOT/root-integration-v3.xml`、`root-related-v2.xml`、`root-related-final-v4.xml`、`root-context-checks-final.json`、`native-v1/`、`native-v2/`、`native-v3/`；最终实际读取`native-final-readback-v2/verification.json`，限定视觉`root-final-limited-visual-review.json`；初次readback partial／attempt-status保留纠正记录。
- 完整README14项、文档策略、限定diff与冻结SHA由`ROOT/documentation-final-checks.json`记录；根与UI各4文件的产品／测试冻结范围见上述回执。

教师操作见[教材选材时确认备课](../teacher/lesson-and-paper.md#textbook-preparation-context)，状态见[当前闭环记录](../WORKFLOW_STATUS.md#textbook-preparation-context)。

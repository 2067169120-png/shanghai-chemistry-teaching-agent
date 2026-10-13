# 2026-10-11 多图批次与完整题原答案点击：局部验收记录

状态：**维护源码功能与独立新课完成本轮局部机器验收；新课已保存、导出及单课备份恢复。** 实际Office与现场放映的范围单列；不宣称全业务闭环或发布。

证据根：`E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration`。工作区：`C:/Users/20671/.codex/worktrees/e6ae/上海高中化学等级考`。当前为维护源码，renderer39／prompt35；v0.1.101 EXE未更新，微信读书停用，未提交发布或升级human／official／generation／publication资格。原59项、X01—X10、固定60任务与整体仍active。

## 1. 已实现并验证的产品合同

### 多图工作区

- 既有“教材原页／切片…”和“课外／本机配图…”进入同一ImageBatchWorkspace。可继续裁教材原页、从同一原页追加不同裁片、多选本机照片、复用本课／已有备课图片；以课堂用途而非单张原页为清单项。
- 在一处修改左上导学问题、课堂用途和教师范围，按授课顺序上移／下移，查看完整16:9／A4预览；修改后预览凭据失效，需要重看。Accepted仅交回有序selection_requests。
- `prepare_image_batch(facade, assets, resources, requests, guard=...)`只形成独立候选；publish后worker再核来源与回执，GUI一次history提交并在receipt确认图片所有权。history提交后sync异常不能用旧scope误删已引用图片。
- FrozenCompositionScope冻结history.value/back/forward、表单payload、assets、草稿目标和插入epoch；GUI信号撤销可记住A→B→A，worker仅读Event。取消、关闭、Stop、源晚变、目标变化均不部分加入。先前知识publication默认合同继续回归。
- Source、版本、像素SHA及新教材Original.evidence.crop_id与sealed crop receipt在后台保留，reader／backup依据完整身份闭包核验。课堂正文不带来源审计、待核验或许可提醒。

### 完整题面与答案

- 完整题有独立“题面同页／点击原答案…”按钮，隐藏整项素材通用点击勾选；默认未选答案。只绑定确切Original block ID，不把所有role=answer区块视为同一答案。
- 当前支持空选择（仅题面同页）或最多一块短单行文字答案；多行、答案图、公式对象和放不进标题旁槽的内容阻断，不缩字／删题。其他答案、完整解析与教师备注保留在教师范围。
- 原生预览区分课堂点击前、点击后、静态学生和教师完整页。静态学生Word/PDF继续隐藏教师答案，只有明确的课堂PPT答案进入点击显示。
- 工作副本改答案仍绑定Original的确切身份／role／kind；Original不改。clear只去配置、保外框和分页；A4/A3保留inactive intent，回16:9恢复。geometry、拖动、工作副本提交前检查教师、课堂和静态学生范围。

### 容量及版本

素材容量由96扩至128；每课图片128、页数160、每素材256块等边界保留。容量变更不表示可以无限导入。root冻结v3为117个源码文件；最终交付如有后续冻结应以root最终清单为准。

## 2. 按组记录测试，不累加重复开发轮次

| 集合 | 本轮结果及证据 | 范围 |
| --- | --- | --- |
| 图片作者 | 40 passed；`image-batch/logs/service-final01.log`、`image-batch/frozen-sha256.json` | 来源覆盖、队列、原图、Original用途、receipt重核、共享claim与取消清理 |
| 问答后端 | 54 passed；`question-reveal/backend-review-report.md` | 确切答案绑定、三流渲染、静态隐藏、clear／格式、对象和边界 |
| 问答定向回归 | 89 passed、4 deselected；同组报告 | 与54项分开记录，不能据此声称全仓回归 |
| Panel与原生Dialog组合 | 69 passed；`panel/final-tests.log` | 新20＋旧knowledge/pending31＋旧ImageDisplay16＋迁移既有picker2；互有覆盖的开发轮次不相加 |
| crop_id闭包根检查 | 9 passed；`crop-continuity/junit03.xml` | reader／backup闭包 |
| 素材128容量专测 | 5 passed；`crop-continuity/capacity01.xml` | 96→128；页面／区块等边界保留 |
| 旧集与单项修复复跑 | 原运行65 passed、1 failed；修复后该失败例单独1 passed；`crop-continuity/regression01.xml`、`regression-fix01.xml` | 两项会构造不适用personal环境的旧测试未运行；不把它们写成通过 |
| 文档策略与完整README | 首轮policy passed、14 passed；`documentation/documentation-policy-initial.log`、`documentation/readme-tests-initial.log` | 最终结果见`documentation/final`的policy／14项日志和JUnit；无应用／Facade／StateStore／Recovery构造，临时输出限OWN/documentation |

`test_teaching_image_picker_ui.py`是本轮开始前已经存在的未跟踪文件；仅迁移两个Panel直接交接测试到真实batch模态和安全owner，另外三个picker测试函数未改、未在该次验证中运行。迁移保留加入顺序、Original／原图保护、一次undo、迟到表单变化拒绝等断言，未用mock替代产品布局或publication。迁移前5942字节、SHA `114ABF7DB49C7C942D0C4DB836A910DA7266D94EBB00BD808B3C2A937FF5CB35`及mtime见 `panel/existing-test-migration/before.json`，原字节同目录保存；是否计入newlytrackedexisting由root相对incoming ledger登记，不能计成新建测试。

Panel最终五文件冻结见 `panel/frozen-sha256.json`；原生最终截图同源码哈希见 `panel/native-final/code-sha256.json`。69项包含真实Qt模态、真实私有图库、异步publication和history，没有构造MainWindow、Facade、StateStore或Recovery。

## 3. Native84／B350只作功能验证

输入来自2026-10-09 `actual-question-course/run-1/private-course-after.json`，84素材／107图片资产。其完整题为B350—361，双选；仅明确选定B356“【答案】AB”，B357—361的同role解析／总结未自动投影。原生框内文字32px即24pt，原题图按原比例保留，题干和ABCD同一页。工作副本引导问题改为“三种纤维的结构与性能有什么关系？”，原始题目快照保留。

最终proof：`panel/native-final/actual-native-panel-proof.json`。一次history提交、exact undo／redo、A4／16:9 intent往返、其余83资源及外框、所有Original／blocks、既有3个click resource ID均核对保持。源JSON SHA仍为 `26a06f7124819e8fb84febab5cbf5a6404be9a31227b6de3e11fab7f59f478f0`。107图复制进本次OWN私有目录逐字节核对；输入原课程不覆盖。

已查看最终1220宽窗、430窄窗及完整页面截图：

- [点击前原生窗口](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/panel/native-final/actual-native-answer-before.png>)
- [点击后原生窗口](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/panel/native-final/actual-native-answer-after.png>)
- [430窄窗](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/panel/native-final/actual-native-answer-narrow.png>)
- [完整题面与答案页](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/panel/native-final/actual-native-question-after.png>)

这些是真实题目的隔离功能验证图片，不是本轮扩充课堂的交付截图，也不能把原生快速预览当作Office物理分页或现场点击播放。图片批次作者的1120／430及A4／16:9截图使用合成测试图；只证明操作和布局，不充作课程的真实情境照片。

同一道B350另有独立实际PowerPoint 16.0核验，见 `office-question/office-physical-report.md`、`office-powerpoint-verification.json` 和 `office-physical-summary.json`：原题物理1页，题干及A—D均24pt，三结构原图为290×68.0936mm，字迹／图像在页内；仅B356答案组Appear／OnPageClick、Exit=0、Delay=0，题面其他8形状可见且无该动画。四页混合稿的原3素材9组与参考稿在Office对象读数中exact，新答案增加1组。输入PPT的SHA／字节／mtime保持；只读打开3文件、仅2份导出Office PNG。首轮在文件打开前的COM HWND失败另留证。**未启动现场放映，live_click_verified=false**；这项仅证明已列输入PPT的Office物理布局和保存的时间线，不证明本轮扩充课程全套通过。

## 4. 实际扩充课堂、六件输出与单课恢复

输入是`outputs/2026-10-09-word-quote-materials/native-question-guided-delivery-v2/profile/desktop-state.v1.json`中`prep-65ca51a279c44d76b212defdedf66465`的旧45页课，95素材／98图片。实际题为B382—392用途题；[题源决定回执](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/question-source-decision.json>)按封存Original `0d0b8b6d924cb5d27ab159d14d51f514fa1471512ab99f0177433324ad76d0cc`绑定B387“【答案】B”（block `CB-33bec99c77434e03a50ba6dfc5d7dfcd`）。首轮与B350双选功能片段的题源混用已纠正，B356／AB未代入本课。

实际混批的3张教材裁片和1张已有实景均从真实来源生成，A4为3预览页、16:9为4预览页，单history提交及undo／redo精确；另加3条本课解释，得到102素材／102图。[混批回执](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/mixed-batch-proof.json>)、[来源登记](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/supplement-source-register.json>)保留原图、用途、页码、源SHA和crop_id。教学衔接分别为：

| 本课用途 | 已读教材原内容／照片 | 边界 |
| --- | --- | --- |
| 从酯化反应读出可继续延链的反应位 | 必修二PDF99／印刷95，酯化条件与原反应式；crop `5a86805390320066f85bee929704825749b20bcb3fe004413c75a041c8f4cb78` | PET延链解释由教师联系本课既有PET原式，不把单次酯化原图称作PET原图 |
| 辨析线型连接与空间形态 | 选修三PDF82／印刷78，直链淀粉原文与图4.4；crop `b745d614aaa7a10248de2f4829fab7fc723581f8a6e01c4202d9deabd768123f` | 不把淀粉图称为橡胶结构或实验测量 |
| 比较天然纤维与聚酰胺的连接键 | 选修三PDF85／印刷81，图4.8；crop `c5d7143fdfa9d3f7a022078e7b242a57c4be1dea24aeee0cc7f750fe7757dae1`；已有蚕茧实拍（Fumikas Sagisavas，CC0 1.0） | 通用肽键示意，不声称为所拍蚕丝的精确序列；许可与原网页存于后台 |

最终使用主代理认可的C1／C2／C3／题页布局；旧反馈页从98mm右栏加宽到296mm，文本、Original和点击角色保持。所有其他外框相对已认可稿保持。[布局回执](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/accepted-layout-review.json>)记原生teacher104／static48／classroom49，errors=[]。三处新讲解在课堂第14／28／19页，实际完整题在第46页、原反馈第47页；只是原生布局页码，不借此声称Office物理页数。

### 保存、包内内容与成品

[保存和磁盘重开](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/native-save-reopen.json>)通过真实`DesktopWorkbenchFacade.create_preparation_draft`，draft `prep-1aa4ea21d9b443c2b84d772dc3394a77`、revision `d1c85d54b163cd250cfada561ffc9e7e2f9e1f1dcfb161800621a2626180d5d1`，保存位置为`E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/profile`。按确切草稿ID／revision重读内容相等；非直接写JSON冒充保存。

[六文件SHA清单](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/six-files.json>)的原生文件与中文友好副本逐字节一致，完整清单保留SHA-256／字节／mtime。成品如下：

| 用途 | 文件 |
| --- | --- |
| 课堂投影 | [高分子材料-课堂课件.pptx](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/交付文件/高分子材料-课堂课件.pptx>) |
| 学生讲义 | [高分子材料-课堂讲义.docx](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/交付文件/高分子材料-课堂讲义.docx>) |
| 教师教案 | [高分子材料-教师教案.docx](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/交付文件/高分子材料-教师教案.docx>) |
| 教师辅助课件 | [高分子材料-教师辅助课件.pptx](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/交付文件/高分子材料-教师辅助课件.pptx>) |
| 静态学生画布 | [高分子材料-课堂静态讲义.pdf](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/交付文件/高分子材料-课堂静态讲义.pdf>) |
| 教师画布 | [高分子材料-教师画布.pdf](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/交付文件/高分子材料-教师画布.pdf>) |

[包内内容检查](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/package-verification.json>)确认49张课堂幻灯片、旧9组timing exact、新B387答案组1个、学生Word隐藏B387、教师Word保全原答案块、三处新标题和解释仍可编辑、原出处前缀未重新带入课堂正文。PowerPoint包内timing不等于实际现场播放。

### 实际Office与直接视觉的范围

[Office汇总](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/final-proof/office-proof-summary.json>)与[PowerPoint原始回执](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/final-proof/office-ppt/office-powerpoint-verification.json>)记录PowerPoint16.0实际只读打开课堂49页、教师辅助104页和旧参考45页；课堂生成49张PNG及49页PDF。教师辅助PPT仅核页数，未逐页扫描形状或截图。当前10个效果＝原9个＋第46页B387答案的Appear／onClick；旧9个目标的拼接全文及效果参数相等，反馈加宽后的换行分块／几何是明确修改，另外8组几何保持。没有启动SlideShow，也未保存源PPT或调用应用Quit／进程kill。

课堂49页机器检查中，shape页外0、文字边界页外0；另有111个文字边界超出自身文本框的提示（包含既有内容），保存在[几何明细](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/final-proof/office-geometry-details.json>)。这些内部框界提示仍是本轮机器检查的限界，不能据零页外结果宣称全部内容无溢出。[主代理直接视觉记录](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/final-proof/root-visual-review.json>)仅包括5张联系图中的全49页缩略布局，以及实际导出第14／19／28／46／47页的单独查看；未逐页全分辨率精读，不构成真人化学复核。

Word本轮收口范围见[正式限制回执](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/final-proof/word-verification-limit.json>)：教师教案已在真实Word只读打开，Repaginate／ComputeStatistics实测57页，见[分页前置回执](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/final-proof/office-word/lesson_plan-repaginate-before-export.json>)。ExportAsFixedFormat长期未返回，因此教师Word导出PDF及学生讲义物理分页均未验证，不能把两份Word称为完成Office渲染验收。第一次可选GoTo逐页读取触发wwlib.dll崩溃，失败及Windows事件留存；唯一一次重试取消该调用，仍保留OWN会话11676／PID6836，无第三次重试、另起实例、Quit或kill。

教师Word原生文本实读保留B387及本题6个answer角色区块；学生Word OOXML隐藏这些原题教师答案区块，但保留3个既有personal课堂反馈块。因此只说“原题教师答案角色隐藏”，不声称学生材料没有任何答案性文字。现场点击播放仍未进行，真人化学复核、命题和发布资格未升级。

[主代理当前输入检查](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/final-proof/root-current-input-checks.json>)确认117个源码SHA与AST、六件原生／友好副本的SHA、字节和mtime均相符；该记录是当前源文件未被Office改写的证据。[独立最终课程核对](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/final-proof/final-course-independent.json>)复核同draft／revision、137文件ZIP与恢复字节、102个Original、28个回执身份及六件副本。完整测试与Office边界汇总在[独立矩阵](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/final-proof/completion-matrix.json>)；本版按上述已发生结果收口，不等Word导出返回后预填成功。

### 单课备份、恢复与继续编辑入口

[备份恢复回执](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/backup-roundtrip.json>)：ZIP SHA `a0b377a5da39ebf11f6733d33b7b1f46d46c98113ee95db1f065d095b18b2009`，185674976字节；137文件、102图、28教材裁片回执、6输出，缺失0。ZIP CRC与全部打包文件字节一致，恢复至`E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/可编辑课程工作副本`，真实Facade重开保持同draft／revision和内容。[恢复后28条原页读数](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/restored-original-page-reads.json>)在本机现有原书绑定路径下逐项通过原PDF裁片像素核验；没有在另一电脑实际验证，原书PDF与来源位置绑定记录未随包迁移。28是裁片引用数，不据此声称28个不同PDF页。

[打开高分子材料工作副本.cmd](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/打开高分子材料工作副本.cmd>)及PowerShell入口已生成，指向上述恢复副本和本维护源码；[入口检查](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/expanded-course/native-final-delivery-v1/launcher-verification.json>)核对路径、pythonw与确切课程，但`created_only_not_started=true`。**未启动完整MainWindow／Recovery，不声称完整主窗口交互或现场点击通过。** 原PDF、原始讲义和本机原书位置关联不打包；课程包不是全业务备份。导出与私有恢复不构成Normal接纳或正式题库发布。


## 5. 原数据保护、视频观察与整体边界

[root保护回执](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/root-final-protected.json>)确认Normal 1523文件／56备课／61业务对象、只读QA 415文件、六份原件、旧45页课程130项依赖及既有课程ZIP的SHA-256、字节数和mtime相等。新课只在OWN私有保存位置制作；本轮没有日常Normal接纳、完整应用恢复验收或发布。

[视频观察回执](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/video-observations.json>)记录主代理直接查看V17的24帧和V37的15帧，共7张联系图／39个不同原帧。两个文件均无音轨，源SHA、字节与mtime保持。采用的本地设计启示是“持续显示当前课和选材上下文”以及“原图与可编辑输出对照”；不是OCR准确率测量，也不是连续播放或全44视频完成。

原59项、X01—X10、M1全业务恢复、固定60任务、完整内容导入与联动、44视频及整体目标继续active。工程检查不升级真人、化学、官方、命题或发布资格。微信读书停用；renderer39／prompt35、旧EXE v0.1.101、未提交发布分别保持。

## 6. 文档与知识库同步

本次只更新根README、教师课程指南、维护者入口、新QA、WORKFLOW_STATUS、原59／X01—X10路线以及Obsidian化学项目和INDEX路由。原始字节快照见`documentation/before.json`及`final-records/before.json`；本收口阶段快照在`documentation/final/phase-before.json`。旧历史正文、状态和混合CRLF／独立LF保留；本轮另把知识库10/4记录的一处相对QA链接修为已存在的维护源码QA绝对路径，原历史文字不变。初次rg --files漏报该受忽略规则影响的文件，已用Test-Path与rg --no-ignore核实。所有修改均有字节逆构造与SHA核验，不重写旧QA历史或95-Source。

文档策略、完整README14项及链接／图片／锚点检查统一在本轮文字收口后执行；最终结果见`documentation/final/documentation-policy-final.log`、`readme-tests-final.log`、`readme-final.junit.xml`和`document-audit.json`。文档验证只加载文档策略模块，不构造应用、Facade、StateStore或Recovery；Python以-B禁字节缓存，pytest禁缓存，日志和临时目录均在本次OWN。

根README保持一份当前主指南，不建立版本归档。最终文件SHA、字节、mtime、行数／字符数及链接、锚点和PNG签名检查存于[文档审计](<E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration/documentation/final/document-audit.json>)，策略与完整README日志／JUnit同目录。原生源绑定的可编辑导出沿用工作台流程；主代理参考Presentations技能核可读性，不把本轮说成artifact-tool生成管线。

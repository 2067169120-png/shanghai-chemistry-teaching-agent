# 教材补图、问题导学和知识笔记编辑空间验证

日期2026-10-09；当前维护源码；renderer38／prompt35／旧EXE0.1.101保持。整体目标active。[教师操作](../teacher/lesson-and-paper.md#textbook-more-materials)。证据根 `ROOT=E:/项目/上海高中化学等级考/outputs/2026-10-09-textbook-edit-space`。

## 最终交付与来源

最终课件候选为 `native-classroom-more-materials-v8`，真实Office为 `native-office-more-materials-v2`，可编辑副本为 `native-editable-workbench-copy-v3/profile`。新增draft为prep-01895c7a86d04352b91384c9a87cbc71，实际facade保存／读回payload exact，实际PreparationDraftDialog.select_draft_id后selected.payload exact且按钮可用。原60业务对象及其余state保持，只有新稿、标准UUID revision token和updated_at新增；副本56备课／61业务对象。正常工作台仍55备课／60对象／1444文件，没有自动替换。

实际原课35页、19不同教材切片、6实物照片与1题图；本轮补3教材PNG＋1外图，最终40页、22教材／7照片／1题图，共30不同课堂图。87资产池含完整讲义素材，不能称87张课堂图。旧74资源完整对象、Original/work、placements、83旧资产metadata/bytes、全部click对象与数组exact；教学环节原80分钟、原1正式例题、知识网和完整教师读稿保持。

新教材源：`沪科技化学必修第二册【高清教材】.pdf`，10,110,288字节，SHA256 92f09535689cbb1192526a7f8b4dde2254869052d8e3b6d88b8bed9d354094ce，PDF90／91／92对应印刷86／87／88页。分别为完整图7.24、塑料正文两完整段、完整图7.25（两分类轴、八例图和全部线／标签）。实际ConceptTextbookPageCatalog＋TextbookPreparationCropService预览／确认，不伪造个人BOOKPAGE。根直接查看三个裁片原图后只在OWN请求确认；crop来源／页码／框／preview revision／SHA保持。五资格标志不提权。

实景为Commons原始PlasticsInjectionMoulderJones.jpg，2016×1434，453878字节，SHA256 5d7bad5237a41314e5414e287a13cc7ee384a915382b797da3a35a943fd1c1e9；作者Glenn McKechnie，CC BY-SA2.5。原始图片、页面、许可和注塑过程原始资料冻结于processing-photo，delivery另附图片授权说明。图为局部拆开设备，只做料斗／注射部件／模具区域观察，不断言运行中或特定树脂。真实图像store导入字节exact。独立读取原始来源、许可与Protolabs工艺资料；学生正文用“以热塑性塑料的注塑为例”说明，不把其套用所有塑料。

## 分组验证和布局

| 范围 | 结果与证据 |
| --- | --- |
| 笔记UI作者61 | ui-worker/pytest-final.xml，23新增＋38此前导航，不能重复算117项新测试。 |
| 独立UI13＋43 | independent-review/review.md；13新行为，原43=39最小Panel＋1顶层函数fake owner＋3关闭链fake owner，非完整应用43次退出。 |
| 根实际笔记14 | native-actual-note-edit-space-v1/verification.json；实际105页原书3笔记、Qt／TaskBridge、facade.__new__只读；菜单6动作、49—59章节与105页全书、保稿、参考／card回执、窄宽布局。 |
| 容量根19 | capacity-tests.xml；4新边界＋15旧contract，2与此次无关恢复／export检查排除；没有构造Recovery。 |
| 独立材料38 | independent-review/materials-review/candidate-v8-audit.json，含6独立边界输入；96/97资源、160/161页、stale Original及human_reviewed提权拒绝。 |

笔记窄420×960动作区270→34，笔记区396→585，正文viewport117→174；宽窗1366保留六按钮。仅6方法AST有变化，其余61原方法exact，按钮click原链保留。根直接看4实际原生截图，包括菜单、原章节阅读、窄正文保存取消、宽六按钮。不能扩大为完整MainWindow、Recovery、真人或课堂live验收。

实际Qt teacher86→91、static35→40、classroom35→40，旧页相对顺序及可见元素逐字段exact，errors=[]；新增10文字块字符全显示。图725 v7缩右栏约600×282像素的P2保留；v8全页1400.92×660.03像素，比例保持、所有分类轴／8图／标签可读；下一页三完整解释段，独立同点复验关闭。新照片图文并排，问题位于左上。

## Office与有限视觉

真实Microsoft PowerPoint导出40页；Microsoft Word教案48页、课堂讲义21页。click-and-word-content-audit记录PPT9原生clickEffect，正文无待核实／待核验／仅供参考／来源尾注／教师自行核对等防御句。原题与点击答案保留，不另加练习。教师Word长文保留完整讲义，不以页数冒称简短教案。

根直接看最终实际PPT3／19／20／21／22五新增页详图及4联系图（40页概览），教案1—24页两联系图及21页详图，讲义两联系图（全21页概览）及11页详图。概览不等于每页全文精读；未称48页Word全面视觉验收。根另查看可编辑副本精确新稿选择截图；实际完整主窗口没有启动。

## 冻结、失败与范围

两产品／两新测试最终60代码union，前58中56 SHA／bytes／mtime exact，2合法更新；最终AST解析与limited diff检查记录。Normal1444／QA415全指纹与六原件、上一QA保持，保护写尝试空；doc CAS与知识库只更新化学路由／项目，历史字节保持，不改95-Source。来源可追溯属性与工程验证分开，不解锁human_reviewed／chemistry_reviewed／publication_allowed。

素材v1对比误含原稿export、v2 local_image_request参数错误、v3触旧80资源上限、v4完整旧讲解同页不足、v5／v6照片说明换行超布局；v7曾成功39页但分类图小P2，最终v8改分完整大图与完整解释页为40。中间文件原样留证，无源文件／正常profile写入。private v1验收漏新增revision，v2误设revision=1；核actual DesktopStateStore._update的UUID token后v3通过，未为此改变产品。

V28“用智能体一键生成排版精美的课件(带语音讲解)，老师备课效率提升10倍”、V30“用智能体，3分钟就能制作出可打印的制式教案！”仅各本地六帧联系图；看到问题图文课堂页、教师目标／资料输入与Word教案。没有音轨／连续播放或44视频全覆盖。窄菜单为本地推导。此前V27机器来源ID／SHA正确，但人类别名误写“每个老师都应该建立自己的命题系统”；库存原题为“独立老师都应该建立自己的题库系统”，历史QA字节保留、本页明确更正。

ppt-master与curriculum-design-guide本轮已读；ppt-master默认手写SVG完整页流程按用户“工作台继续设计”要求改用实际源绑定与原生Office，未冒称完成SVG管线。未提交、发布或重建EXE，微信读书永久停用。更多教材／题库导入、QUOTE、44视频、原59及X01—X10及整体目标继续。

# 教材知识笔记切换与备课联动验证

日期：2026-10-09。当前维护源码，renderer38／prompt35、旧EXE v0.1.101保持；整体目标active。本页记录局部工程增量。[教师操作](../teacher/lesson-and-paper.md#textbook-note-navigation)。

证据根目录 `ROOT = E:/项目/上海高中化学等级考/outputs/2026-10-09-textbook-note-navigation`。四产品／两测试文件最终指纹见 `root-final-code-manifest.json`；52前序冻结代码、六原件及上一表格QA相等。

## 功能与保存范围

新NoteEditSession为纯内存缓存，不写业务状态。owner由原书ID、原PDF SHA、章节ID和笔记ID或NEW构成，分别保存精确名称、正文、页码、基线版本与当次确认。空白、超长、只改页的新稿也保留；同节只有一个新稿槽。Qt未编辑原值与控件渲染值分离，CRLF、CR、段分隔符和NBSP不会因重绘改成假dirty。

切笔记／章节／个人书、refresh、保存章节、追加目录、知识采纳notes_saved父回调和resume恢复相应稿。来源SHA、note revision、section revision变化／删除保留旧输入；范围收缩不由SpinBox夹掉旧页。实际编辑和基线变化清确认。成功仅清精确token匹配的提交；真实operation在service写入前核closed、epoch、selection、token并mark_started。取消、旧结果与提交后继续输入不能清其他／新稿。

已勾选dirty笔记先定位保存或取消，再生成参考／知识卡；其他未勾选稿不妨碍已保存选材。默认和Escape“返回编辑”；main预检早于保存窗口状态、disable、child.close和shutdown。明确放弃后撤销排队token，关闭链只确认一次。缓存限当前教材页面会话，没有崩溃／重启恢复承诺。

## 分组测试与独立审查

| 范围 | 最终回执 |
| --- | --- |
| 作者纯模型34 | `backend-worker/pytest-run2.log`及冻结清单；无Qt／IO／真实业务状态。 |
| 作者UI38 | `ui-worker/pytest-5.xml`；实际Panel／Bookshelf、内存服务和任务桥，中文字体与窄窗可达。 |
| 根最终97 | `root-tests-final-v2.xml`，18.08秒；34纯模型＋38 UI＋25原service。不与上述作者计数重复累计。 |
| 独立40导航／Unicode／目录 | `independent-review/frozen2-results.json`、`frozen2-additional-results.json`、`frozen2-outline-blank-results.json`；四组输入与运行前后源码SHA／字节／mtime固定。 |
| 独立3关闭链 | `independent-review/frozen2-close-chain-results.json`；实际函数体和fake owner，不是实际MainWindow实例退出。 |

独立43检查精确构成为39最小Panel行为、1顶层函数体／fake owner事件、3纯fake-owner链；不称43次完整应用UI。`review.md`没有未解决P1/P2。前期并发partial import失败和fixture navigate补充发生在首次有效运行前，原输入保留。

P2为追加目录时以非空章节标题决定是否保留表单；清空既有标题改页或NEW空标题仅填页，回执会恢复旧表单。固定两反例修前0/2，作者仅移除bool(title)门槛，最终同输入2/2，原38和关闭链再次通过；旧失败和freeze-v1副本完整保留。空标题仍不能由原service保存。

## 真实资料与持久联动

先只读现有Normal选修第二册105页／9节／3笔记，以及QA选修第一册129页／1节／1笔记，原PDF SHA核对。每个状态文件及整本PDF逐字节复制到ROOT各自私有profile，不拼接两书来源依据。

`native-actual-note-navigation-v3/verification.json`终端exit0：实际Qt Panel＋实际DesktopTaskBridge线程任务＋原真实PersonalTextbookStudyService／OWN DesktopStateStore，16精确检查。切条／节／书A-B-A、刷新不自动写盘；各自保存准确输入，最新Normal笔记进入真实参考和课件知识卡，note revision绑定完整内容；另一私有窗口更新后旧CAS失败、旧稿保留、返回编辑保持Panel与任务桥，再明确取消恢复当前已保存内容。

私有Normal60／QA53业务对象、来源、目录、knowledge drafts、其他笔记与五资格标志保持，只改各一条原有私有笔记及标准更新时间。原Normal1444／QA415全部SHA／大小／mtime前后相等，保护写入尝试为空。教师确认仅模拟于OWN私有工程验证，不冒称教师审核；没有正常接纳、课程新增或Office输出。

根native-v1误以literal多行文本搜索JSON编码字符串而失败；改为直接比较card字段。v2末尾误把真实state writer标准updated_at当越界变化；独立差异确认仅该元数据，最终显式记录时间戳并精确比较全部业务状态。两失败原脚本、部分截图和原始profile全清单保持，不计为通过；没有为它们改变产品。

## 限定视觉与参考视频

v3五张原生图来自透明standalone panel，不能当完整主窗口背景证明。根另以同一v3私有状态只读构造现有QWidget#WindowRoot样式容器。visual-v1的外层默认layout使请求420变成716，保留原截图与更正；visual-v2使用与原组件一致的NoConstraint外层且明确断言host和Panel均420，再观察正文、保存和取消的滚动可达。证据为 `native-actual-note-visual-v2/verification.json`及根限定观察回执；没有MainWindow／Recovery／任何恢复快照构造，不能扩大为完整主窗口退出、真人、化学或live课堂验收。

作者三张合成中文Bookshelf截图及字体缺字0记录另在`ui-worker/`，不混作真实资料截图。根观察仅限回执列出的图片，未称全面PPT／Word或所有教材视觉验收。

V43“题库系统为什么用obsdian？”与V27“每个老师都应该建立自己的命题系统”仅各六帧联系图、来源SHA核对；观察到目录与当前记录、图文题答与勾选。笔记编辑连续性是本地交互推导，未声称视频展示了草稿恢复；未听音轨或浏览全44视频。见`reference-ui-observations.json`。

本轮新增教材裁片／课外照片为0，正常35页高分子课堂的19处不同教材切片、6张实物／现场照片及问题导学、原完整讲义知识网保持。QUOTE完整范围与更广教材／题库覆盖、44视频对齐、原59及X01—X10继续；微信读书停用、未提交／发布。

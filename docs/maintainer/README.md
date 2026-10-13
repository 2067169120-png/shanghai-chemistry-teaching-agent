# 维护者入口：版本、运行与验收

## 2026-10-12 ROOT4：本节直接选材与带源课程复用

本节显式section／knowledge两范围，无card可读完整原页并选图／讲义／题，不隐式添加知识。注册／补充节原页以SECTIONPAGE、个人书原页以BOOKPAGE封存；旧crop位置确认后回本节链已修。UI首行显示节名和原页范围，空知识pane隐藏、不自动选首条；43页标签证据来自合成夹具，真实原页由Native回读另证。

带源备份checkbox默认off，明确选择后按所引用真实Word批次的完整descriptor／manifest／archive保存依赖；保原provenance八字段，实际恢复位置单独解析。正式讲义优先入口已用恢复后的98源目录实测：明确选一份讲义后仅查其24题，无知识卡的教材图／讲义知识网／完整题共3材料、7资产按队列一次加入，撤销重做与保存重开相等。新主讲稿13素材／11引用资产，原生学生／课堂9页、教师10页，课堂讲义选项实际勾选并生成六文件；实际导出教案、课堂讲义与课堂PPT三文件。原14素材归档、完整素材库15及旧14／12版成品全部保留。

测试按版本分列：原A／B／C／D一次28例UI通过，来源优先新28与默认混合23单列，安装后空环境2和受影响旧夹具13另有正式回执，不累加成新全库通过。[讲义优先入口安装](E:/项目/上海高中化学等级考/outputs/2026-10-11-direct-section-and-course-reuse/source-first-word/install-receipt.json) · [旧夹具安装与13例实测](E:/项目/上海高中化学等级考/outputs/2026-10-11-direct-section-and-course-reuse/source-first-word/fixture-maintenance/install-receipt.json)。SourceFileSet、runtime／watch、publication claims保持原检查；[本次阶段耗时](E:/项目/上海高中化学等级考/outputs/2026-10-11-direct-section-and-course-reuse/course-reuse/actual-source-first-v2/terminal-summary.json)的prepare77.359／publish146.375／recheck77.516s仍为分钟级，来源目录和选题缓存的warm耗时不能替代整批编译。

普通备课组件和原生文件已实测；启动脚本完成静态核验，未运行完整MainWindow或Office放映。模型PDF看图不升级真人／全库教学质量资格，EXE／版本／发布不改。旧03AV根因unknown及trace、旧测试失败、ROOT3两稿／8图／成品与原59／X01—X10保持。[新主稿Native回执](E:/项目/上海高中化学等级考/outputs/2026-10-11-direct-section-and-course-reuse/course-reuse/approved-main-v5-native-v1/final-proof.json) · [讲义优先及本节三材料回执](E:/项目/上海高中化学等级考/outputs/2026-10-11-direct-section-and-course-reuse/course-reuse/actual-source-first-v2/final-proof.json) · [教师步骤](../teacher/lesson-and-paper.md#forward-section-materials) · [ROOT4 QA](../qa/2026-10-12-direct-section-and-course-reuse-qa.md)。

## 2026-10-11 ROOT3：课堂素材编排与阶段read memo（局部验收）

新完整题短中性导航、16:9续页24pt及独立图水平居中保完整题面、原图、Original和教师自定义标题。当前renderer v41仅将16:9讲义知识的明确短示例导语优先接下一独立大图，图尺寸不变；18几何对照（16非目标相等／2目标修复）与当前production23通过。v40历史整组123通过／1失败／3跳过／1排除、版本断言单例修复1通过分别保留，不写整组重跑通过。

阶段read memo精确安装4module＋1test，production75通过／2既有runtime subprocess边界排除，SourceFileSet／runtime／watch前缀及claims后缀旧字节相同。新正式v40单成功分支同6资源／27资产实测prepare44.015s、publish82.984s（2独立compile）、recheck41.562s；四个fresh Stage新read域各native_tail1，一次history、undo／redo、OWN原子保存及实际Panel重开通过。候选52.824s仅prepare／discard历史，旧ROOT2四模式不计成新全套，不推性能倍数或整库秒级。

v41两原生草稿已保存／重开：完整库15／31；主讲实际去长栏目254—297后14／11，学生／课堂12、教师13为原生画布数。原39＋40同页、B37无孤词，ResourceEditor标题编辑再undo、5crop原页pixelverify及Original／role／block保持。Root直接看8张关键PNG；未作全页精读、Office、LiveSlideshow或真人资格升级。当前SourceUnion133 SHA／AST通过，unknown和mismatch为空。入口与后台关联见本轮native-final-proof下简明说明、详细打开说明和delivery-final.sidecar.json；课堂正文不带来源防御句。

Normal1523／QA415文件集合／stat及state完整SHA保持，当前保护清单原件、旧12成品与ZIP完整SHA／bytes／mtime保持；EXE、发布及正式资格不改。旧49页课与ROOT2历史证据保留。[教师入口](../teacher/lesson-and-paper.md#classroom-material-ux) · [本轮QA](../qa/2026-10-11-forward-section-materials-qa.md#classroom-material-ux)。

## 2026-10-11 从真实教材小节连续选材（维护源码）

普通教材的“本节备课选材…”以真实已保存章节建立上下文；`entry_kind=textbook_section`、`resource_id=None`，不要求先有课件卡。打开后先由教师明确选择知识点，初始焦点落在只读讲解区，避免Qt焦点事件自动选中首条。课程与加入位置由发起入口绑定。各子窗继续返回同一授课清单，教材知识、教材图、讲义完整栏目／知识网／原图与完整题可跨来源交错排序；讲义批次逐项保留确切封存来源，多题浏览逐题保留完整原ref。

最终有序请求交给Studio既有事务，整批一次加入／撤销。来源、裁片回执及目标在交接前后核验；请求准备与图片发布分开，取消或晚变按最终回执界定。后台契约、测试组、源码冻结与性能记录见[本轮QA](../qa/2026-10-11-forward-section-materials-qa.md)。

已保存Word回执／历史批次可明确点击“去本地 Word 题库”，此时切普通word_native并重置前次检索条件；普通关闭、取消与其他导入路径不覆盖原通道偏好。从导入返回可见题库时保留同一检索的原题位置与页次，新搜索／换来源／用户滚动不接受迟到的旧位置恢复。当前滚动17／入口18／续读路由28合并63个唯一安全Qt用例通过，包含真实窄Word阅读器迟到PNG使状态文字缩短及卡片高度下降后的同上下文定位；真实98来源及3580可查题由独立只读消费者核档，不据旧quick0阻断。选材条件3547／导出条件3544与属性缺口详见本轮QA。

来源阶段缓存只用于单次编译，prepare与publish／recheck分别核验实际原件；material的完整SHA、identity／ChangeTime、目录成员事件和原生末端检查保持。运行时代码按实际可信加载辨别，运行时JSON只接受已配置C↔E别名，其物理字节仍完整核SHA／identity／watch，显式绑定资料优先。当前后端7文件冻结为`backend/source-freeze-perf-runtime-data-final.json`，helper为01c596，独立同源完整56／56通过；前向UI25和本地入口／滚动63分别按各自源码与测试范围记，旧39／48／53与开发重复轮次不累加。整体SourceUnion128的期望SHA／AST均通过，旧117内10处变更全部有owner证据、无unknown／missing／mismatch；见`audit/completion-coordinator/source-union-final.json`。最终真实六项耗时与事务范围单列下文。

最终`native-proof/transaction-final-v3/final-proof.json`记录四分支终态通过：普通已登记E3的4.2小节按同一授课顺序导入6素材／27资产，发布28份本批来源文件，整批导入一次history，undo／redo、Original／标题／角色／block ID、裁片回读及保存重开均相等。发布后待确认编辑器取消、目标晚改、只读来源端口晚不可用均未入课，空课空资产恢复且28份本批文件清理；目标晚改保留新的教师字段。最终保存16:9，一栏／两栏为Native排版功能证明，默认留白及完整题跨页仍在。实测prepare106.797s、publish187.515s、recheck85.609s仍为分钟级，Word／claims完整编译成本待优化；本轮未生成或验收成品教学PPT、Office物理页数及全页教学质量。

分离提纯7条独立修订已写入DATA的reading-notes-20261011-separation-source-repair.json，并向catalog追加1行（174→175，旧字节前缀相等）；旧7个ID、旧包及8份来源文件保持。普通TextbookBookshelfService.snapshot已读到7条，现为5册／60节／386概念／322素材／474条可选研读。386／322是当前消费者口径，与旧上游383／319不同，不能解释为本轮新增3概念与3图；本轮新增7条可选提示。2个历史失败包保留，counts_complete=false。主代理所选内容模型复核完成，candidate_only=true，真人、教学、检索、命题及发布资格继续关闭。

本轮V32／V43只实际查看4张联系图对应31个离散时刻，两源无音轨；只用于章节上下文、原文阅读和累积选材设计参考。此前49页高分子课、原QA与其Office范围保持。本轮目标为教材／讲义／完整题选材与备课联动，原59项、X01—X10中的其他业务继续各自追踪。v0.1.101试用EXE未更新，微信读书停用；无发布或正式资格升级。[教师操作](../teacher/lesson-and-paper.md#forward-section-materials)。

## 2026-10-11 多图工作区与完整题原答案点击（维护源码）

Panel的教材原页／切片和课外／本机配图入口复用ImageBatchWorkspace：本批导学问题、用途、教师范围与顺序集中编辑，完整预览后只返回有序selection_requests。prepare不写目标图库；publish交接后在worker再核source与crop receipt，GUI在单次history提交处接收所有权。FrozenCompositionScope冻结value/back/forward、表单payload、资产、草稿目标与insertion epoch；GUI信号记住A→B→A，worker只检查Event。取消、Stop、源晚变与未提交清理不部分加入；history已引用图片不受旧guard撤销。

完整题使用题面同页／点击原答案原生对话框，默认答案选择为空，按确切Original block ID选择最多一块短单行文字。其他answer角色的解析不自动包含。题面保原图与24磅正文；静态学生Word/PDF隐藏教师答案，课堂PPT仅投影明确选定块，工作答案修改仍绑定原区块身份、角色与kind。配置随A4/A3保留为inactive，回16:9恢复；clear保外框。手工geometry、拖动和作品副本提交调用三流布局检查，失败保旧稿。

ImageBatch新教材Original.evidence.crop_id与实际sealed receipt绑定；reader和backup仍核来源版本、页码、像素与回执闭包，不以同名图片猜来源。后台来源、授权与核验记录不带入课堂正文。素材容量从96扩至128，图片128、页面160、每素材256块边界仍在；本轮容量不是无上限导入。源码冻结v3包含117文件，容量专测5项；renderer39／prompt35，EXE v0.1.101未重建，微信读书停用，未发布。

验证按组记录，不能把重复开发轮次累加：图片作者40；问答后端54与回归89（4 deselected）；Panel组合69（新增20、旧knowledge/pending31、旧ImageDisplay16、既有picker合同迁移2）；crop_id闭包根9；旧集65及后续1项修复复跑分别留证，2项会构造不适用personal环境的旧测试未运行。既有test_teaching_image_picker_ui.py不是新建测试，迁移前字节、SHA与mtime在OWN/documentation关联的panel证据中保留。

真实Native功能验证使用B350—361完整双选题，只选B356“AB”，在84素材／107图隔离副本验证提交、undo/redo、A4往返、Original与旧3个click ID；实际PowerPoint另验24pt同页与旧9组参数，未现场放映。扩充课则沿用旧45页课程B382—392用途题及B387“B”，102素材／102图已真实保存、磁盘重读并生成6份原生输出；课堂49／静态48／教师104为原生布局。单课137文件ZIP已恢复、逐项核字节，28裁片回执与本机现有原书绑定路径下的像素回读通过，原书PDF与来源位置绑定不打包；入口已生成核对但未启动完整主窗口。实际PowerPoint确认课堂49页／教师辅助104页，课堂49页导出PNG/PDF；主代理查看全49页缩略及5关键页，教师PPT仅计数。Word物理分页单列QA，不等同原生布局；未现场放映。 原59项、X01—X10、固定60任务与全业务闭环仍active。

证据根：`E:/项目/上海高中化学等级考/outputs/2026-10-11-classroom-integration`；最终独立课程在`expanded-course/native-final-delivery-v1`。教师操作见[多图选材](../teacher/lesson-and-paper.md#image-batch-classroom)、[题面／答案](../teacher/lesson-and-paper.md#question-page-answer)与[课例／继续编辑](../teacher/lesson-and-paper.md#integrated-course)。本轮[QA](../qa/2026-10-11-classroom-integration-qa.md)分列来源、原生保存、输出包、Office、单课恢复及未验范围；旧QA保留历史结论，Normal／QA／六原件／旧45页课程保持。

## 2026-10-09 完整题课堂标题与来源前缀投影

Workspace的question selection仅新增可选title；严格1—160字符、trim后深拷贝，题源仍为既有key／revision／points。Native metadata只有title，保同来源八字段及question版本；重选可更新points而保title。编译在完整Original封存后、旧课去重前overlay；stamp与再编译核标题。默认空初值改为清理已知UI标签和确切出处后的题干预填，已有手填title原样保留。不是修复既有空初值回写漏洞。

新helper printed_source_span只识别首段题号／例题／变式及单双选之后的一处闭合年份·地域·考试标签；未知学校／格式／相邻歧义保持。仅fresh未编辑complete_question使用。删除不能交叉script／EQ／inline marker；实际SequenceMatcher位置须等于逐字符平移，并与独立算术投影的display_tokens严格相等。无法证明时保持原文，不增加课堂提醒；Original和presentation不会重新封存或变更。规则是显示投影，非地域门槛或官方资格证据。

真实最小Qt／TaskBridge：只读Normal81素材／87图，私有课程按分类网B45→完整栏目B254—297→完整题B350—361加入，得到84素材／107图；44源块／3表／32原图出现保留。默认标题不改保存、改问题标题、同题重选、旧81／87内容与位置、单undo／redo及同源空清单取消均通过。三新增素材原生输出2Word／2PPT／2画布PDF：学生16页、教师16页为画布／包内页数；四包正文无本题18字出处，23源图字节一致，题号／双选／题图／选项与教师答案保留，学生答案隐藏。未启动Office，Word物理页数未知，不等同现场放映。

作者标题60、来源投影44与独立3分别通过，不叠加旧运行。源码union101＝旧96中94 exact／2改，另纳入3新建文件与2既有修改文件。课堂新题默认未启用点击，完整题仍分页；本轮是交接验证片段，课堂教学设计和图片补充继续。Normal1523／56备课／61对象、QA415、六原件、既有45页成品及课程ZIP保持；未保存日常课／注册新课，renderer38／prompt35／EXE0.1.101不改，无发布或资格升级。

初轮作者fixture错误、独立只读探针及真实导出首轮fitz依赖假设失败保留；导出仅OWN验收脚本改pypdfium2后新目录重跑，无产品变化。证据根outputs/2026-10-09-question-classroom-copy；[QA](../qa/2026-10-09-question-classroom-copy-qa.md)。

## 2026-10-09 普通编排面板混合讲义入口

Panel.add_handout_materials使用现有request-only行为的HandoutMaterialWorkspace，深拷贝完整request，复用prepare_handout_materials四参及publication queue。只新增可选文案／revocation Event，原知识默认不改；冻结value/back/forward与表单、目标、assets及insertion epoch，GUI核值、worker仅Event。pending工作副本重新核Original，不第二次publish；提交后按current/back/forward引用保图，不用旧资产guard撤销自身成功。

作者新31＋安全旧31为62个唯一测试；独立15反例另组通过。真实最小Qt／TaskBridge／原生子窗／编译发布：Normal只读81素材／87图的私有副本，分类图B45→完整栏目B254—297→完整题B350—361，得到84素材／107图；44源块／3原表／32原图出现保留，旧81内容／位置和87图、单undo／redo及同源空清单取消exact。非表段落28图＋表内4图，不以候选28误判缺图。

本轮是来源与交接验证稿，非新交付课堂；完整题默认长标题仍需课堂编辑，新增题默认未启用点击。旧3个click resource ID及既有9参数分别记录，不宣称实播放映。Normal1523／56备课／61对象、QA415、六原件、既有45页成品与课程ZIP保持；无Normal保存／新Office／完整应用构造／版本或发布。

首轮独立夹具类名setup错误、实际run-1驱动漏必填引导问题和根文档混合换行预检失败均留证，最终通过范围与初试分开。源码union96＝旧95中94 exact＋Panel改1＋新test1。ROOT：outputs/2026-10-09-teaching-material-entry。V28／V30补看89个原帧，仅模型离散观察；44视频原件SHA保持，未计为全44完成。renderer38／prompt35／EXE0.1.101不改，微信读书停用；完整业务恢复、固定60任务、原59／X01—X10与整体active继续。[QA](../qa/2026-10-09-composition-lecture-materials-qa.md)。


## 2026-10-09 教材来源连续使用局部改进

desktop_backup从所选记录／恢复稿／保留设计的已结束任务payload中匹配Original来源＋页码＋image SHA的唯一sealed receipt，校验typed source identity及正整数页码，窄ZIP allowlist并在restore再核闭包；生成器已投影掉设计的普通任务不能凭同图片反推来源。原PDF／独立教材库／本机位置文件不纳入备课ZIP，旧包缺回执列gap。

desktop_textbook_crop_reading严格JSON、整页raster SHA与RGBA裁片核对。source_locations仅为当前服务已确认的同字节／同版本原书路径生成本机关联；stage后台全页核验，publish再次核原输入／proof／位置CAS，UI还核冻结scope与关闭状态。dialog取消／父关闭／迟到不发布，关联后真实reader重开原页；不改Original或registry激活字节。

专项54、根reader31（两旧personal夹具不运行）、UI44、独立18与12分别留证；P2typed identity修复前恶意ZIP与首次fixture格式失败保存。Root误选含StateStore构造的旧测试范围在OWN私有tmp中中止，最终范围和实际单课包采用Frozen只读State／Recovery与最小Qt，不使用完整应用构造。实际130文件单课包／98图／25receipt／6成品exact，25恢复原页实像素与搬迁M2确认／三取消通过。完整业务恢复与固定60任务仍未完成；renderer38／prompt35／EXE不改。ROOT：outputs/2026-10-09-course-source-continuity；[QA](../qa/2026-10-09-course-source-continuity-qa.md)。

## 2026-10-09 普通讲义续读局部改进

QuestionExplorerPage为真实默认入口，旧LibraryPage同样复用Workspace resume_source；只有_outline_ready完整核验后给continuation_source，父页finished还核owned dialog和closed。strict initial_source默认门禁不变，普通失效源清旧绑定后回目录。新增25、根回归23、独立21按范围分别通过，根真实Word两次request-only选材及私有编译通过；未运行完整窗口／StateStore／Recovery，不保存Normal或重导Office。完整业务snapshot和crop receipt显式搬家关联仍待实施，当前有备课恢复、任务历史和个人教材独立包；不要误写为完全没有恢复。固定60任务缺逐项输入与运行回执，旧查询不折算。ROOT：outputs/2026-10-09-continuous-material-workbench，整体active。[QA](../qa/2026-10-09-continuous-lecture-selection-qa.md)。

## 2026-10-09 静态QUOTE v2与待交接课堂工作副本

`word_static_quote`重新解析完整paragraph XML并约束Character缩进的Normal21／24半磅、字值／twips和字体，保三次源出现／两处可见、缓存、锚点及透明效果。`checked_material_work_copy`保Original／图／范围／资格；`recheck_published`在worker重编译原请求；三个入口最终核epoch／历史／资产，一次history。精确receipt回滚及引用检查防提交后删图。普通讲义知识仍支持1—20多来源请求。

frame82、独立几何9／v2双版2／API20与UI54按版本分组；旧47仅v1，文案21不替代事务复跑。Native3源导入和Office物理完成不等于教学排版通过，bounds诊断与失败留证。新图文稿：最终独立副本45课堂页／95素材／98资产，新增3教材原片、3实景图、1完整知识网及5问题导学页；真实API保存并读回prep-65ca51a279c44d76b212defdedf66465。真实Office为PPT45页、Word教案54页／讲义24页，7新增图字节一致，9点击参数保持；无页外裁切，104个框宽提示（新页15个）留证，未实播点击。代码冻结、保护与实际保存须以最终回执补齐：Normal1523文件、56备课／61业务对象及QA415文件、六原件SHA／字节／mtime全等；只新增私有课程与成品。ROOT为`outputs/2026-10-09-word-quote-materials`，renderer38／prompt35／EXE不改，无提交发布。[QA](../qa/2026-10-09-word-quote-materials-qa.md)。


## 2026-10-09 本节讲义知识网桥接与静态EQ去重

ROOT：E:/项目/上海高中化学等级考/outputs/2026-10-09-section-handout-knowledge。desktop_section_handout_sources从validated handout_knowledge Original八字段与resource ID／Original SHA绑定候选；只读handout_source_context双读，working edits不作身份，角色／hash／版本／批次严格保留。Workspace initial_source保留8字段并锁定，current outline与封存physical path失配明确拒绝；browse仍手选。Section交handout_materials＋material_request＋course_handout_source，后端重核候选和教材知识，将existing+知识additions交给MaterialPublication，lecture内部selection_order不重新按类型排序。无自动语义关系或资格升级。

_same_section仅deepcopy完整snapshot中的static EQ image_block_id按Original.blocks逐次出现位置规范化，其他EQ／原图／容器／裁剪／条件不忽略。根真实55块7EQ栏目重选零新增且教师编辑保持；独立同字节另一出现仍不相等。真实混合发布→最小Panel单undo-redo、precommit清理／postcommit保留引用图通过。helper25、UI94与最后50、根53、独立54分组留证，fixture只读／内存状态，无StateStore／MainWindow／Recovery构造。

actual Qt私有81→83素材、87→105图、54学生页，通用材料44块3原表32原图出现完整，旧B29网络去重及全部旧资源位置保持。最后1366／420截图对应最终UI SHA，首次整合期间后续仅窄页签布局调整，读选契约未变；不是全54页人工检查或Office验收。Normal40页课程／现有Office保持，未新采用／重导。union74以code-final-freeze为准（前70：67不变3更新，加既有Workspace1和新3）；上一维护段落旧union68说法留作历史，上一root-final-audit实际union70为authority。V10／43／27九新离散帧只借鉴目录、阅读及本次清单，不称全44或音轨；原59／X01—X10／QUOTE及整体active继续。[QA](../qa/2026-10-09-section-handout-knowledge-qa.md)。


## 2026-10-09 来源原页与本节统一选材

ROOT=`E:/项目/上海高中化学等级考/outputs/2026-10-09-contextual-section-materials`。desktop_section_materials只读scope/recheck；SectionMaterialsDialog只交知识、裁图或题请求；desktop_section_material_handoff最终来源重核、已保留card去重绑定和MaterialPublication接入；Panel冻结原页意图／插入位置并完成一批history。默认知识仅来自真实binding，未知／跨书／unmapped不猜；讲义browse与linked保留完整原图和答案，候选／human／official／generation／publication资格不变。

旧三产品修改＋新三产品、五新测试冻结union68，旧57源码不变。作者上下文41／真实handoff15／UI25／Panel23，根旧插入28与独立39分别留证。独立两P2（资源A→B→A、未commit图片表单悬空）已同输入复验；history已commit但sync异常须accept引用图，未commit仅在form精确等于本批结果时恢复旧assets，避免覆盖并发编辑。裁片缓存允许保留闭合候选；不宣称事务回滚所有文件。

root native v1／v2缺install_ui_font导致单原题native字体访问异常，A/B正式字体单题与最终完整Qt通过；v3／v4 FrozenState未实现draft_snapshot，v5—v7读取87旧IMG时PreparationImageStore noop mkdir被audit拒绝，实际profiles exact。v8安装正式字体、纯_draft_snapshot只读适配、直接安全IMG字节读口，真实81资源完整Panel、17知识／塑料知识与图7.25／问题图题／83资源42学生页／旧位置和单undo-redo通过，全profiles零写尝试。不把私有验收稿当已保存日常稿；无MainWindow／Recovery／StateStore构造、Normal采用、新Office、提交或发版。本轮无新增视频阅读，44视频与完整QUOTE／59／X01—X10整体active继续。[QA](../qa/2026-10-09-contextual-section-materials-qa.md)。


## 2026-10-09 日常工作台完整课程追加

ROOT=`E:/项目/上海高中化学等级考/outputs/2026-10-09-workbench-course-adoption`。实际课程链native-classroom-source-binding-v2→native-office-source-binding-v1→Normal新稿 `prep-f13ba905c7eb46fcabd69279219b763e`；最终56／61／1523，旧1443非state文件＋旧60对象/revision/其余state保持。4缺失IMG、3新crop receipt、6registered output＋3metadata和后台历史／修正证据共79新文件。完整1444文件ZIP与独立逐文件SHAbytesmtime readback保留。

两真实故障修正：旧private launcher以CODE为workspace_root，改DATA（CODE仍作为import root）；三新crop Original.source_revision误为preview_revision，改receipt revision并重算封存digest，旧课程不覆盖。全部课堂工作内容／图片／位置／资源顺序原样，新export关联新指纹。verification-reviewed.json明确exports清空1→0，不把旧all_other_payload_fields_exact措辞误称整payload仅6字段差异。三实际Normal原页renderer逐RGB通过；171原生layout几何像素及109实际Office页文字像素相等。

采用前独立审阅发现提交后snapshot可能含另一writer状态、误读gate层级：已分别加exact_owned_append全状态认领和正确顶层资格读取。6 OWN原生StateStore案例覆盖准确认领／并发加对象／改旧对象／其他state／错revision／不确定None。未认领提交保留依赖；回滚须持原生锁且完整当前state等于认领快照。Normal采用实际native CAS，验收冻结只读facade.__new__／独立原生chooser，不构造完整MainWindow／Recovery。

原private入口已修目录，但历史private课程本身不覆盖；新Normal入口读取实时revision，验收verify-only冻结payload87IMG／81资格／6文件＋3metadata／40页9点击且阻断Qt／产品import和写入。v1布局比较失败保留（Qt对象不能用dict==验证），v2改公开几何和实绘像素；先前preflight在source revision处停止且未写Normal。union60源码、renderer38、prompt35、EXE0.1.101不改，未提交／发版／升级human或publication资格。后续入口缺口见QA。[QA](../qa/2026-10-09-workbench-course-adoption-qa.md)。


## 2026-10-09 笔记窄窗菜单与教学素材容量

ROOT=`E:/项目/上海高中化学等级考/outputs/2026-10-09-textbook-edit-space`。仅personal_textbook_panel的构造／布局／同步与三回执方法改动；其余61方法AST exact，旧capture/save/compile/reader/close链保持。宽窗、其他页签与阅读模式保留原操作，窄窗knowledge-note六操作通过旧button.click转发，文本／enabled实时同步。desktop_lesson_composition仅资源80→96；160页、原来源与资格门禁、renderer38／prompt35保持。两产品／两新测试冻结union60，旧56 unchanged。

UI作者61及独立13＋43分组，根真实Panel／TaskBridge14只读检查；容量根19（新4旧15）和独立材料38汇总／6纯边界输入另记。实际原课精确保全，最终native-classroom-more-materials-v8→native-office-more-materials-v2→native-editable-workbench-copy-v3，40页／87资产／81资源／9点击。v1—v7素材中间失败与v7图725 P2留证，不作最终交付；private v1忘计新增draft_revisions、v2错假设token=1，actual _update为UUIDhex，v3修根验收而非产品。v1／v2保存本身正确，但不作最终可编辑入口。

Normal／QA／六原件只读；MainWindow／Recovery无实例化或snapshot。仅用户双击launcher的交互分支才创建实际完整应用，verify-only只读。源图／版本／页码／SHA及授权在后台，student正文无来源／防御句。工程检查不能改human／chemistry／publication资格。未提交、发版、改EXE；整体active。[QA](../qa/2026-10-09-textbook-edit-space-qa.md)。


## 2026-10-09 教材笔记导航与关闭预检

本轮4产品／2测试：新纯内存NoteEditSession，personal panel冻结owner与raw/render文本映射，bookshelf刷新前capture及close委托，main仅closeEvent前置4行，其他45方法原样。存储schema、真实service、源PDF、renderer38与prompt35不改；52前序冻结代码保持。作者纯34／UI38与根97重叠，独立固定40导航／目录及3 fake-owner关闭链另计。目录空标题P2旧失败保留，同输入修复；默认／escape返回编辑，排队保存实际operation核epoch/token、mark_started后按精确回读对账。

ROOT=`E:/项目/上海高中化学等级考/outputs/2026-10-09-textbook-note-navigation`。根actual native-v3终端0、16检查；实际两教材和完整state只复制到OWN，真实DesktopStateStore／TaskBridge保存与CAS、最新参考/card通过，60／53原业务对象不改。原Normal1444／QA415全SHAbytesmtime、protected writes空。无MainWindow、Recovery或snapshot，未接纳正常数据。visual-v2只读同私有state，现有WindowRoot样式且layout不强制最小宽，严格420断言后截图；旧透明底／实际716宽的visual-v1保留，不作为420证明。根harness的JSON多行字符串与标准updated_at两个错误留原失败，非产品修复；限定视觉不升级为完整应用／化学／human／live。会话缓存不能称重启恢复，未提交／发布或重建EXE。[QA](../qa/2026-10-09-textbook-note-navigation-qa.md)。


## 2026-10-09 原生表格单元格与局部续页

7产品／4测试文件；严格source／table哈希绑定文字段，网格、合并和原图slots不可改。Qt原始空白／UTF-16编辑与Undo分支修复两真实P2；独立159后端断言、18精确编辑与6无改动检查另留档。有效单元格变化默认仅所选资源完整续页，保持共享页、空页、其他资源与策略；checkbox取消续页时溢出保留输入，错误与CAS原子。根最终60后端、57 UI／布局及旧72回归按组记录，worker19含7旧图片布局回归、独立46续页检查，不累计重复。实际私有增行仍13帧48学生页，Office48／48／22与9点击，101图／三代18成品保存恢复重开相等。11final、41旧代码、六原件、489旧私有文件冻结核验；正常1444／QA415未变。

renderer38／prompt35，旧EXE0.1.101未重建；无新外图、正常课程、提交或发布。根只做限定模型观察，不提升化学／真人／live门槛。手动恢复会在私有临时路径构造无快照PreparationRecoveryStore读取器；没有MainWindow、RecoveryController、自动恢复或快照写入，不能写“零Recovery构造”。原35页课仍19教材切片6照片，QUOTE／笔记切换／44视频／59／X01—X10及整体active继续。[操作](../teacher/lesson-and-paper.md#native-table-cell-edit) · [QA](../qa/2026-10-09-native-table-cell-workbench-qa.md)。

## B01／B05／C05局部推进：2026-10-08 完整高分子栏目原图与批量加入位置（维护源码、隔离验收）

真实Normal来源PKG-072 B254—297完整44块／32原图出现／21原媒体／3表格入课，PBrush同对象PNG与两flipV严格保来源及原像素，不执行OLE，全局Word／锚点守卫保持。备课来源和画布设加入位置，整批13续页插到所选素材前／后，旧素材所有字段、共享页、几何及6输出历史保持，一次撤销重做；1200×800左侧动作滚动，清单与加入目标固定，旧60方法保持。教材旧异步回调借用新位置的P2已真实复现并修复；失败留证。复用旧教师图片隐藏课堂知识的错误修复：新来源角色v2按当前栏目／教师范围判定，旧Original保持，问题答案范围守卫保持。根实际选材、正常草稿只读克隆、学生13新帧／32图／3表可见；实际私有整课48PPT／48教案／22课堂Word、9点击保留。101图和两代12成品私有保存／ZIP恢复／显式重开相等；限定模型观察，不称新完成教学设计或人类／live验收。

正常55／60／1444、QA415及六原件全SHA／字节／mtime保持；原74素材83图课堂35／45／19、19教材裁片6照片未改写，无新增外图或正常课。QUOTE完整范围仍阻断，44视频／59／X01—X10及整体active继续；renderer37／prompt35／旧EXE未更新、新v2保存稿使用修复源码、微信读书停用、未发布。[操作](../teacher/lesson-and-paper.md#complete-source-insertion) · [QA](../qa/2026-10-08-polymer-paint-preview-workbench-qa.md)。

[教师使用说明](../../README.md)是场景入口，不再放提交时间线和测试数量。详细教师步骤分在[首次使用](../teacher/getting-started.md)、[备课与出卷](../teacher/lesson-and-paper.md)、[作业与考试](../teacher/assessment.md)、[保存与支持](../teacher/save-and-support.md)。这里不是旧主指南的整本复制。

## 版本边界

2026-10-08窄窗教材阅读增量：personal已加载窄阅读且有勾选时，预览／知识卡／清空归选材操作QMenu，转调原button.click、confirm同排；embedded阅读详情短文案与页／缩放同行，完整accessible／tooltip与原页、翻页、裁图、记忆及来源／角色／busy／目标守护保持，独立reader未精简。初52无正式font fixture仅历史，旧真实304／123px有效；补font菜单单独223px后继续精简。最终personal_textbook_panel／textbook_source_dialog及2test共4 SHA／AST冻结，定向56／22.70s与回归45／24.11s分组不累计，正式font129内存页PDF70／长标题／双行位置的合成282／263px另列。native-v1 passed／终端exit0，真实700×960／page588×863／context564×84／reader534×301，初始及知识卡回执相同；宽1366×960／page1152×863／context1128×41／reader798×344。实际菜单取消／参考／明确加入1未保存QA卡／清空及宽窗／notes恢复完成，旧41资源、52已存QA记录、正常51／56／1059全manifest与原PDF保持；阅读记忆／恢复可更新不称QA零写，无课程保存／原资料导入／Office生成。root-native-limited-visual-review.json核11图与4代码测试SHA、直接看9独特图，03／05同已看02 SHA；07“未保存修改”原图重核纠正根先前误读，无额外产品／测试改动，06仅短QA卡回执。PKG-066 coverage与根12 source SHA主核仅元数据候选、不正式入库／化学或视觉PASS，逐题原页／对齐／资产／教材节绑定缺口继续。旧31PPT／26Word、v34／prompt35／EXE0.1.101、59／X01—X10及整体active保持，限定视觉非全教材／课堂、人类或live，微信读书停用、未发布；完整README14项、文档策略、限定diff与冻结SHA由本轮ROOT/documentation-final-checks.json记录。[本轮QA](../qa/2026-10-08-narrow-textbook-reading-qa.md)。

2026-10-08教材备课选材目标增量：main_window／studio_preparation／preparation_recovery复用既有编辑／打开／新建流程，PreparationSelectionContext连接教材书架与个人教材选材上下文；真实work_title优先，原topic／lesson_timing与dirty状态可见，空白dirtyfalse文案改为“无未保存修改”。切换成功或取消保持书／节／原页／勾选／来源预览，目标变化仅清已加入标记、不自动交接。异步确认锁定发起目标，A迟到结果不送B，预览保留供重新明确加入，来源版本检查保持。文案微调前UI78（26新＋52旧）、根较宽24（含集成5）分别通过，不重复累计；“无未保存修改”文案final-v3定向26通过／4.80s，最新main／studio／phase_a／新集成4模块23通过／30.62s／exit0与较宽24重叠不加；最终UI final-v4 42通过／14.31s（30新＋12旧space／parallel）、4文件SHA／AST冻结，历史78未重跑不累计。native-v1 settings项目内拒绝、v2 recent50误作全51 GUI前断言保留；v3复用同外部未变profile并改真实all-draft search，机器passed：11截图、PDF70页、阅读选择预览保持、旧51 QA草稿相等而隔离新建后52，正常1059及原PDF SHA／bytes／mtime保持。native-v3 exit0，根直接看02／05／06发现wide阅读区挤小，仅计操作链；紧凑上下文左标签右操作同行、阅读时隐藏重复介绍／summary／resume并在返回选材恢复；内存1366×960 host／1152×863 page viewport336／bar40px，个人540×900 viewport199／bar76px，无个人300px承诺。同QA profile实际只读readback、不重导入／保存；首次在选择窗口后未完成读出由根中断，partial／attempt-status exit1保留；QMessageBox归因已纠正，v2确认handler未触发（decisions=[]）。最终v2只读等待实际加载后passed／exit0保全部52 QA记录／正常state／原PDF／8SHA，无导入／保存／生成；实际宽window1366×960／page1152×863／context1128×41／reader798×304，窄window700×960／page588×863／context564×84／reader534×123仍挤、仅验操作不承诺高度。root-final-limited-visual-review.json直接看5图，阅读/notes说明恢复与返回编辑保持PDF70／选择预览，6产品2test AST8pass及SHA与root23/UI42/native匹配；限定范围不扩大；完整README14项、文档策略、限定diff与冻结SHA由本轮ROOT/documentation-final-checks.json记录。只读限课程数据，Qt阅读记忆／表单恢复可在隔离profile更新，不称QA profile零写；正常保证为v3全1059 manifest与最终state／原PDF保持。正常CMD→open_workbench.lnk→pythonw launch_textbook_lesson_workbench.pyw已只读核CODE=e6ae／STATE=既有same-lecture profile，未启动正常GUI。正常51备课56对象与原课成品无新增，无新正常课／Office、无profile或gates文档写入。v34／prompt35／EXE0.1.101、旧31PPT／26Word及59／X01—X10原范围保持，微信读书停用、未发布、整体active。[本轮QA](../qa/2026-10-08-textbook-preparation-context-qa.md)。

2026-10-07完整讲义与矢量原图增量：WMF palette／零文字旧字体预检、ChemWindow同容器唯一preview/OLE的`shchem.handout-object-preview.v1`窄证据、保原source与派生PNG；`binary_interpreted=false`、原Word/OLE不执行、原字节不覆盖，显示v6与精确SHA／角色／版本保持。UI显示`display_bytes`并保`source`／derived标志，小图筛选保留可尝试WMF；确切b37—56共20块6图出现5显示图。表格显式完整上下标和紧邻短正文局部分组，源表／字数／字号／角色／图不改。旧后端465通过3skip1旧版本断言失败保留，修测试后92通过；UI125、表格80、局部分组最终160分别通过不累计。原Word外部关系在启动Office前拒绝，原物理页未知；native-v1/v2验收脚本错误、native-v3裸标记阶段与polished-v1/v2版面迭代保留。最终polished-v3实际PPT31／教案26、41素材19图5点击2题、PDF68印62教材pH图与CC BY-SA4.0真实电导率仪原PNG同页，b55仅作品补笔记、原快照不变，来源许可留后台。根模型限定7PPT／4Word／3UI，6PPT／3Word与v2已看字节相同；隔离ZIP343049456B、4QA稿19图24输出45文件恢复本课全文／19图／6成品相等。正常采用`adopted_and_verified`／exit0，新稿prep-13e3d93159e64a6597b2894eebefa441及OUT-ea76ef2754f64c7fa3df91986d993550重载全文19图6成品，现51备课56对象、旧50／55逐项相等；980143820B before-profile全1047核SHA，原文件仅授权state变化，4新图文件／6成品／output.json／pH原裁选回执共12、现1059，五gate保持。普通ZIP726811115B／SHA1f9fb430fb3eb05dc8c1a7bd0d5b11641a42f78597116e875005ee39fba594ff，51备课110引用图50包300成品412文件缺0，fresh restore全部51记录及本课revision／全文19图6成品相等，正常1059及state保持；5其他对象、DATA／模型／学生历史／裁选JSON排除，原裁选记录另存在profile与最终课堂素材记录。正常UI-v1回执与首帧重复记录保留，v2仅修验收frame-index／explicit-page／SHA唯一检查、保存课重读passed／exit0，16截图且教师／学生知识各6不同SHA续页，全文／41素材19图／33教师30静态相等、0布局错误、1059／state保持；root-normal-workbench-visual-review.json记录根模型直接看最终教师知识2—6、首帧与已看v1字节同，另直接看v2学生pH／实物电导率同页，不扩大为16图或31页PPT／26页Word全审。作品v34／提示词35／EXE0.1.101、44视频身份与抽帧边界、59／X01—X10与整体active保持，跨日收尾保原路径，未发布、微信读书停用。[本轮QA](../qa/2026-10-07-handout-vector-images-qa.md)。

2026-10-07教学原图显示与局部分页增量：逐原image_block_id自动或毫米宽高上限，完整原图等比缩小；原生“图片显示与分页…”提供缩略选图、三范围测量完整预览与明确单事务局部重排，其他位置及共享页保持、取消不改／一步撤销。原来源、角色、正文及像素保持；composition Word 教案／课堂讲义／学习单按原宽度fit再等比cap，无设置／非composition材料沿旧路径，未改Word正文分页；画布／PDF／PPT知识短编号标题随正文、字号不缩。真实43资源16图副本两次分别单步undo-redo、7UI；教师／静态学生／课堂33／29／30页0errors，实际PowerPoint30页5点击保持、实际Word教案26页；根模型限定3PPT页／6Word页，两Word XML原图SHA／caps相符，学生Word未实际转换。banner6mm／完整知识网同页、b32随b33；b36原段仅标题，不延伸、不称整课设计全面PASS。隔离ZIP58371981 bytes、1稿16图6输出24文件缺0及独立恢复相等；正常50备课55对象1047文件SHA／bytes／mtime／状态、38资源24页12图课例／模板与上一QA全文件保持，0保护写。主QA成功UI／保存／PPT后重复标题定位断言失败，续核已有保存稿／输出exit0，不重放UI／重导产品、不算原失败通过。最终20文件262通过／0跳过、257.42s／exit0，新增43后端／16UI已包含，27源码／测试SHA冻结；旧稿／ZIP默认路径与历史成品兼容，渲染源码v34纳入指纹，EXE v0.1.101／提示词v35保持。上一轮196／3、31页QA、59／X01—X10历史保持，无新完整视频、不猜控件，微信读书停用，未发布、整体active。[本轮QA](../qa/2026-10-07-teaching-image-display-qa.md)。

2026-10-07混合讲义选材顺序增量：编号清单、上下移、选中跟随与确认禁改；`selection_order`严格保留本批全项唯一排列，知识整批／完整题各一次编译后仅排序新增资源，取消、去重、来源角色及教师副本保持。最终5测试文件196通过／3条件跳过，2源码及5测试SHA封存，开发组不累计；真实PKG-056严格候选经native list handler交错入课5项、43资源／16图，Child picker未全链重放。实际调序／确认／导入、单次撤销重做、教师34／静态学生30／课堂31页与实际PowerPoint31页核对，隔离ZIP58375179 bytes、1稿／16图／6输出／24文件独立恢复相等。根模型检查7新增Office页、3主UI／4辅助UI与2原PNG，Word仅导出恢复核SHA；装饰banner及页尾下级标题待进一步教学排版，不称31页设计全面PASS。正常50备课／55对象／1047文件SHA／bytes／mtime／状态、38资源24页课例及模板不变；两个真实阶段exit0。V27／V32仅作筛选与原文预览参考；EXE v0.1.101／渲染v33／提示词v35、旧课堂／模板、59／X01—X10与旧validator exit1边界保持，微信读书停用、未发布、整体active。[本轮 QA](../qa/2026-10-07-mixed-material-teaching-order-qa.md)。

2026-10-07图文讲解页／个人结构模板增量：明确选两资源左右／上下、标题与第二项点击、三范围预览、完整续页与整步撤销；学生独立排版是否切为组成位置由 checkbox 明示。模板仅投影所选框架／目标／节点时长和五教师组织字段，原材料、独立答案字段、图片与引用排除，自由文字不作语义脱敏；预览／搜索、30步名称撤销、软删除恢复、可选ZIP集合、默认追加／无绑定未编辑空结构替换及版本重核接通。冻结13文件164通过，开发23／8、21／此前71重叠不求和。隔离真实UI8图、6环节80分钟模板追加／一次撤销，实际PPT24页／第16组合页／5点击通过；Word仅生成与恢复核哈希。QA ZIP1稿／1模板／12图／1包6成品／20文件独立恢复相等，不是正常50备课全量备份；正常50备课／55对象／1047文件及状态保持，无新正常课例或外图。v3前段回执错误中止，UI不重放，独立Office／恢复续核exit0；根模型限定视觉，人类／live／正式资格未提升。V28／V30结构参考不证明视频含自存模板；旧中央validator exit1保留。EXE v0.1.101／渲染v33／提示词v35不变，未提交发布，B08与整体按局部范围继续。[本轮 QA](../qa/2026-10-07-teaching-page-and-templates-qa.md)。

2026-10-07弱电解质课堂稿内容与文档增量：正常工作台另存《弱电解质的电离平衡：从实验到微粒》，6 环节 80 分钟、38 资源／12 图（6 教材／3 讲义／3 实景），原图与旁栏解释同页；2 原题同页独立讲解及 3 步稀释过程共 5 原生点击效果。教师／学生静态／课堂画布 26／23／24 页，实际 PowerPoint 24 页、Word 教案 23 页、完成态笔记讲义 11 页。真实服务保存重开后 50 备课／55 对象，之前 54 对象和 51 资源／37 页主稿保持；1026→1047 持久文件，旧文件仅授权桌面状态变化。根模型检查实际 PPT 联系表与指定 7 页大图、Word 1／2 页及笔记 1 页，无人类复核、live 放映或正式晋级。真实原生 UI 重开学生静态 23 页／教师 26 页无布局错误，全部 1047 文件及状态保持；普通 ZIP 402 文件／106 图／49 成品包／294 成品文件独立恢复，50 备课记录、本课 12 图与 6 登记输出相等，0 越界写。普通 ZIP 排除其余 5 对象与原资料库、关系、配置、学生历史，不称全项目备份。全局 validator exit 1、旧 catalog snapshot／quality gate 问题保留，本轮未改 catalog。只更新资料、私人备课内容与文档，EXE v0.1.101／渲染 v33／提示词 v35不变，未提交发布；原 59 项／X01—X10与整体 active 保持。[保存、实际 Office 与范围证据](../qa/2026-10-07-weak-electrolyte-classroom-qa.md)。下列记录保留各切片当时状态。

2026-10-07原图选材与目录增量：正常新增弱电解质7知识／4教材切片／2实景选材稿，13素材／6图，原48备课和37页成品保持；49备课／54对象／1026持久文件，新普通ZIP49备课／100图／288成品／390文件独立恢复通过。研读容量256文件／8192原notes／32MiB，超限原子清空候选；实库60／506／467、两旧包失败及32旧独立ID迁移边界保留。最终83定向／68相关通过，269扩大组早于最后守卫微调。Word显示原标题、原图区块／实际图数和明确1—9级导航，旧preview／原件不变，282检查及真实原Word核对通过；图片问题图题与修改后搜索66通过。本机字体回退修复连续公式⇌缺字，54字体／渲染及最终47阅读／选图通过，13素材实际画布原公式、左上标题和全部内容正常。源码未发布，EXE v0.1.101／渲染v33／提示词v35保持，发行配置仍7早期文件；不计全部数据覆盖或正式资格。[根模型视觉与最终验收](../qa/2026-10-07-reading-catalog-scalability-qa.md)。下列版本记录为各切片当时状态。

2026-10-07问题导学／阅读增量：弱电解质7条派生候选已入CODE与DATA目录，原460条可选revision及原档案保持，当前467条、本节15条；3原图通过实际讲义与放大入口。7课堂投影无来源或防御提醒，正式资格不变。私有reference筛选／导航／设置默认折叠，普通行为保持；定向14与相关167分别通过，真实宽窄窗全文／图与正常1016文件保护通过。无新课程／Office／EXE，源码未发布；506原notes距512上限余6，后续扩展先处理容量。[内容QA](../qa/2026-10-07-weak-electrolyte-note-recovery-qa.md) · [阅读QA](../qa/2026-10-07-private-reference-reader-qa.md)。

2026-10-07原文选材与教材入口增量：备课／题库打开原Word选材工作台，知识栏目、原图知识网、完整题答仅勾选，整批确认后制作课堂副本并追加；确切来源、停止／关闭／迟到守护、去重保持编辑、单批撤销／重做沿用。教材concept／研读笔记／个人已保存BNOTE均接入确切关系窗口，显式交接保留原作用域对象。撤销后画布重新定位；关联题首次编译后两端只读重核；窗口同步终态。当前后端58通过／3条件跳过、UI88／教材入口36／路由5／终态14各组通过；真实BNOTE duplicate、当前混合交接、37页实际撤销／重做像素和几何均通过，按版本范围记录。各组测试版本、失败分类和正常1016文件保护见[本轮QA](../qa/2026-10-07-source-material-workspace-qa.md)。正常48备课／53对象、51资源／37课堂页及现有Office保持；源码未发布，无新EXE，渲染v33／提示词v35与正式资格保持。

2026-10-07知识与具体题关系增量：新增独立集合与JSON恢复，支持登记concept/研读笔记、个人BNOTE和完整讲义栏目；保存教师理由、确切两端版本/范围，修改/解除/反查、读原内容及加入本课。Word锁→任务发布事件→状态CAS，最终交接重核，取消/变化零交接、重复保持编辑。基线115、冻结transfer76/后端3、当前UI27按版本分别记录，旧入口正文来源契约修正后Word14通过；真实Qt保存/反查/题答/入课撤销与原知识8图只读通过，后者表格尚为逐格正文。正常48备课/旧52对象保持，新增2条初始关系后53对象，1016文件仅desktop-state获授权变化；课堂37页/成品和旧ZIP保持，关系另行JSON备份。C09部分推进，源码未发布、整体active、正式资格不变。[本轮QA](../qa/2026-10-07-knowledge-question-relations-qa.md) · [当前状态](../WORKFLOW_STATUS.md#knowledge-example-relations)。

2026-10-07同源性能增量：讲义知识导航改用精确归档前后核验和有界内存预览，限定Word库存跳过全批次枚举，原manifest／闭包／实际SHA／大小及题目版本校验保持；全局目录未改。右栏优先显示阅读、选图／知识／例题。真实同来源目录／图／全文冷轮4.475／5.872／6.460秒、暖轮4.331／6.232／6.265秒，非受控A/B或整窗验收。来源101／库存与标签原文352／同源UI29／阅读几何33各组通过；实际37页课例选图、原完整题、修改去重、撤销返回通过。外层脚本保护变量覆盖失败经独立1016文件SHA／版本清单补核，原普通48备课、全部原文件／属性／历史及成品保持，无新稿／Office／EXE。C09细概念关系未建立，整体active，原59项／X01—X10不计全部完成。[本轮QA](../qa/2026-10-07-scoped-source-performance-qa.md)。

2026-10-07连续选材增量：完整讲义知识段可继续选同份Word原图／知识／完整题，严格保留文档8字段与图片12字段两种身份；零图文档可选题，作用域前后重核、独立勾选、取消／迟到守护沿用。原图按原出现位置、封存用途与范围去重，作品修改保持；本次会话的素材／续页／目录／页签位置返回恢复，不写入草稿。实际51资源课例的79原图／19题目录、取消加入重复、撤销恢复与第22页返回通过；最新ZIP回读48备课、94图、288成品，当前3知识段／6输出保持。普通profile1016文件不变，本轮无新稿／Office／EXE。后端115／UI71／返回定位相关25范围见[本轮QA](../qa/2026-10-07-same-handout-material-flow-qa.md)。整体active，原59项、X01—X10与正式资格保持。

2026-10-07讲义知识增量：“讲义知识…”按元数据列资料，只读所选原Word，以独立索引提供明确1—9级完整栏目与原图知识网；每批1—20段，各填问题标题／教师范围，封存完整原块、公式、表格及逐次原图，整批撤销恢复与去重保持编辑。旧preview不变，题答栏目走完整题入口，教师图片范围继续保护；完整嵌入原图保留Word显示对象XML SHA与尺寸，原表改为可编辑表格。98项定向、37项表格／渲染及32项v33相关检查分别通过。实际两课时80分钟／51资源／37学生课堂页，含13教材图、6实景、3完整知识段与1原网、2原题；9点击素材在PPTX读回17个clickEffect，Microsoft Word教案28／课堂讲义18页。实际Office与根代理限定视觉检查通过，原始snapshot不变，3处化学勘误只改作品副本。普通工作台另存并原生重开，旧47备课保持、新增1份后48备课／52对象，989保护文件仅2份授权状态变化，987份匹配，历史选题及6成品摘要保持。渲染v33，EXE未重建、源码未发布，未增加正式资格或独立真人审核。[本轮QA](../qa/2026-10-07-handout-knowledge-sections-qa.md) · [当前记录](../WORKFLOW_STATUS.md#handout-knowledge)。

2026-10-07当前维护增量：讲义选图可按原文栏目和附近文字查找，通读整份原文后选图返回；课件中的原知识网可回到同一份讲义继续选材。目录迁移后按文件版本和原图摘要重核，同图同用途去重。原25页课堂设计与教案Word、课堂PPT、Word讲义保持。119项相关检查、真实同源阅读/选图/撤销和CMD入口通过；原文件SHA、备课与成品保护见回执。source_path只作历史位置，身份仍由源版本、确切区块与像素摘要确定；取消或迟到回调不导入。渲染v29／提示词v34，EXE未重建，源码未发布。[验收](../qa/2026-10-07-lecture-image-reading-qa.md)。

2026-10-06前片维护增量：“讲义图…”只读选择原图、完整放大、版本与像素重核、确认加入及一次撤销接通。问题标题引导10教材切片、4实景照片和原知识网，保留完整24磅知识笔记；40分钟／25页，实际Word25／14页与PowerPoint16点击。44草稿／264成品／70引用图备份及CMD恢复、686相关检查通过，文档与README见回执。渲染v29／提示词v34；中央原问题和旧扩大Word失败保留，原数据/catalog171不变，EXE未重建、源码未发布。[验收](../qa/2026-10-06-handout-maps-and-classroom-images-qa.md)。

2026-10-06前序快照，工作区HEAD `e08c3e7`：默认与旧资料库、画布均接入完整Word题模式，当前范围材料清单独立于旧属性，保留原文／图像／答案角色和确切版本，同版本去重。7既有模块、2新测试文件、21新检查；修正后38项回归通过，成功阶段去重106case。持久候选合并465个明确Word文件、3579条目录与前序13备课；当前6素材／40分钟，16:9学生PPT6页／教师9页，4份Office22页。15草稿／90登记成品／41图片摘要恢复及普通CMD入口通过；旧A4模型视觉误判已由当前版纠正并保留历史。教材385／素材322、研读499／460、catalog169、Word阅读身份729与702待办；正式题／标签0。普通ZIP不含Word库、原教材或配置。渲染v13与EXE v0.1.101保持，微信读书停用，未提交发布。[验收](../qa/2026-10-06-library-question-handoff-qa.md)。

2026-09-28 前序基于 PR #53 合入 main 的 `89c7da3`，完善分页导航、按需读页、缓存释放和高分屏显示，同时冻结下一批教材与题库阅读材料。见[前序导航验收](../qa/2026-09-28-preview-navigation.md)与[当前闭环记录](../WORKFLOW_STATUS.md)。任务停止、原子结构研读及6题补缺保留[上一轮验收](../qa/2026-09-28-office-cancel-atomic.md)。普通回归与真实Office检查分别记录，合成PDF仅验证阅读器，不证明实际PPTX转换或PowerPoint呈现一致。已发布 v0.1.101 包仍是原版本；源码更新不替换旧 EXE。

目录修复支持缺失或不可解析的 `scope.json`、`active-attempts.json` 及未完成修复留下的阻断状态，只从身份完全匹配、机器复核已保存的页组记录重建。教师每组选一份记录，预览后明确确认；保存前重核版本并锁定批次，保留旧目录的精确字节与旧记录。修复不调用模型，返回普通预览后才可另行确认发送。合法 JSON 中的范围身份冲突、非法活动版本结构、重复键、链接或超限仍阻断；不可变汇总候选的版本历史损坏、化学语义和题目层级修复仍未覆盖，C02 保持部分完成。

| 范围 | 当前可据以说明的状态 |
| --- | --- |
| v0.1.101 Windows 试用包 | 已发布预发布版；原受测源码 c0ca081，发行提交8375019。后续维护按钮不在原包内 |
| PR #29 | 维护源码已接纳：存储／题篮保护，取消维护 push 的固定版本自动发布 |
| PR #30 | 维护源码已接纳：独立复练题集、整理窗口与回调保护 |
| PR #31 | 维护源码已接纳：本机改分、过期建议与复练依据核对 |
| Excel 对比重导 | 上轮仅本地候选，未提交、未完成 Windows UI 验收。本轮不搭载该候选代码 |
| PR #32 教师手册与帮助 UI | 已接纳到维护源码并纳入主线整合，不递增软件版本 |
| 自由拖拽画布、跨业务迁移 | 不列为已发布完整能力 |

## 保留的工程记录

[当前闭环与未完成项](../WORKFLOW_STATUS.md) · [原59项与X01—X10](../roadmaps/audit-followup.md) · [更新记录](../../CHANGELOG.md)

[PR26与发行关系](../qa/2026-09-19-pr26-audit.md) · [独立题集](../qa/2026-09-19-practice-set.md) · [题集编辑](../qa/2026-09-19-practice-editor.md) · [改分与下游](../qa/2026-09-19-score-revisions.md)

旧 QA 文档是当时的证据，不改写其中的历史运行状态。新验收结果写到 PR 及对应报告，不放进教师 README 开头。全仓测试、真实模型质量、多屏与新 EXE 不由某个定向集合代替。Issue #24 与未完成项独立追踪。

## 在明确源码版本上运行

先检查并保存本机未提交修改，不执行强制重置。源码与已发布包不同，选择需要验证的分支或标签后再运行。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

使用根目录 `启动源码桌面版.cmd`；遇到旧 VBS 打开旧 EXE 时改用上述入口，必要时运行 `检查运行环境.cmd`。不要把程序升级等同于题库或学生资料迁移。

## 文档维护规则

根目录一份当前 README：教师场景、输入、操作、输出、完成检查和重要使用边界。细节进入有明确任务的教师章节，开发/QA进入本页及既有状态文档。保留旧页内锚点作为兼容入口，不保留长导航和整本历史副本。

文档检查由“最低字数＋最低截图数”改为场景覆盖、章节可达、片段与图片真实存在、体量上限、版本与备份边界。不为通过检查凑字或堆图，不取消源文件与发布检查。旧截图保留实际版本说明；新 UI 截图只有真实运行后才可加入。

提交前运行：

```powershell
python runtime/deeptutor_shchem/documentation_policy.py
python -m pytest -q staging/coordination/deeptutor_gateway/tests/test_complete_readme.py
```

普通维护 CI 保持内容只读。完整 Office／EXE 检查与显式发布分开，不改旧标签或重发固定版本。

# 蛋白质、核酸与高分子：完整讲义、教材切片和课外照片

本轮证据目录：`E:/项目/上海高中化学等级考/outputs/2026-10-08-protein-polymer-workbench/`。当前维护源码未发布，微信读书路线停用。完整原件、素材编译、原生UI、v3b课堂、实际Office、根限定视觉、隔离QA保存恢复，以及正常新副本保存和原生明确重开均已完成。正常资料区现为54份备课／59对象／1245文件。原59项与X01—X10的全项完成标志、来源资格和正式发布门槛不变。

## 完整原件和题目边界

正常资料库的062／072解析版归档分别为 `4bf36447…cc91`／`01f91a44…9c33`，原卷版为 `dc87d7b8…166e`／`dd19914c…e9527`。四份DOCX、XML和原媒体均保留，题目按完整原文、原图和答案配对，不只按印刷题号。

062完整知识范围39—70、71—103、230—238、240—254，经实际生产导入保留89个来源区块、45次图像出现、25个唯一图像；表45九次图像引用及其源单元格位置全部保留。预览盘点与实际原生编译的图像出现计数不同，不能用前者代替后者。原b32知识网保留原像素。072原卷385／解析567区块完整读取，24组完整题答候选保留，19组原图内容相同、5组不同，不将不同图片伪称一致。

旧中央索引把导言表格中的考情分析和学习目标误判成完整题：四原件共12条旧候选（062四条、072八条）在源绑定覆盖记录中均改记为知识文本，本轮这12个锚点的有效完整题命中为0；此0不代表两讲没有真实完整题。`source-candidate-reclassification-overlay.json`保留旧记录与逐项依据，旧索引和历史结论不覆盖。现代导入另外修正072“2．合成纤维”跨段借取记忆空格的假题，原卷25→24，剩余24条完整身份／范围／版本保持，解析24条保持。知识、主题、印刷小题和最小作答单元不混用。

## 源码修正和素材

原生图像读取只新增合法 `a14:useLocalDpi` 元数据和标准静态VML `_t75`显示模板的精确校验；仍拒绝未知扩展、自定义几何、浮动图、外部关系和未验证的变换，不解释OLE。图像组137测试通过，知识编号组229测试通过；组间有重叠，不累计为独立总数。两生产模块及对应测试逐文件SHA冻结。

新增19个生产格式素材／19张图：14张教材原内容切片、5张真实照片。教材裁片绑定原书SHA、双页码、原页版本、渲染尺寸、坐标和独立裁选回执。蛋白质四级结构和PLA循环使用修正后的v2裁片，早期裁片与记录保留。鸡蛋、PET瓶、尼龙绳、橡皮筋及Webb遮阳板照片保留原字节、原页面、作者和许可。作者及出处留在后台，不进入教学正文。

19项是跨蛋白质／核酸／高分子的素材池，不能写成本课全部展示。当前蛋白质课堂实际展示其中6张教材切片，以及鸡蛋、尼龙绳2张实物照片；八项均与解释相邻，其余11项留在素材池。证据为`teaching-visuals/final-verification.json`与`protein-course-worker/native-course-v3b/verification.json`。

## 完整知识保留与v3b课堂候选

四段知识经真实生产API的`teacher_only`请求重新导入为4张教师完整阅读卡，原范围、89个来源区块、45次图像出现和栏目快照保持，来源图文逐项映射。原六素材及b32原知识网另存`sourcekeep/`，完整题199—209的原题与答案快照保持。课堂侧使用有来源映射和勘误记录的手工知识转写，不把转写称为原生完整栏目或原图区块；导言、考情等非课堂教学内容在后台保留，未删除原件或裁短原段。

冻结课堂来源为`protein-course-worker/native-course-v3b/`，54项作品素材／45个图像资产池；课堂27页、静态学生27页、教师51页，三范围排版错误均为0。配置9个原生点击，例题与反馈同在第16课堂页；此处为原生渲染与配置范围，实际Office证据另列，未作现场放映检查。`source-audit.json`记录真实教师范围导入和课堂手工转写，`layout-audit.json`记录分页，`final-freeze.json`绑定文件SHA。v1及v2／v3早期失败、v2b候选仍保留。

含图表格的原生课堂投影仍待后续；当前手工转写课堂与教师完整阅读卡不构成该项解锁。也不将27页课堂候选扩大为三份高分子整课、全化学审校、真人或40人实地投影验收。

## 实际Office与根限定视觉

`native-office-protein-v1/verification.json`记录本机Microsoft PowerPoint实际转换课堂PPT27页、Microsoft Word实际转换教案31页／课堂讲义13页，六个登记成品绑定`OUT-7e0a769764094283bc77d71f82d856f9`。导出回执原状态`actual_office_export_requires_root_visual_review`保留；随后`root-reviewed-classroom.json`以`approved_private_classroom`核准同一私有课堂，绑定Office版payload SHA `2671cb8ba0f25b4ac3653df9d6a8abe4284219d76972b22b7946772d2d243ff1`及成品记录SHA。

根模型直接查看三类全部页的8张contact概览和5张大图，共13个唯一图文件；大图范围为课堂第16／21页、教案第1页、讲义第8／11页。`root-actual-office-visual-scope.json`逐文件绑定SHA、字节数与mtime。正文扫描未见来源、核验或防御措辞；六教材切片和两实物照片与讲解相邻，完整知识原文保持教师范围。PPT时序XML核到9组`clickEffect`，完整题所在第16页为1组；实际Office PDF为完成态，未检查live点击播放，不称独立真人、全化学或40人现场投影验收。该导出检查时正常1112与既有QA415全SHA／字节／mtime保持；后续保存结果另验。

## 高分子讲义当前范围

072完整知识尚未解锁。整段加聚、缩聚与材料单元的生产混合导入仍分别存在浮动形状、裁剪／翻转／旋转或缺失原图依据，未裁短原段来绕过。B124／B126共享WMF含24条与MS-WMF规范长度矛盾的旧式escape；完整3816字节与原记录保留，未调用渲染器、剥离记录或猜画公式。因此19张素材齐备不代表三份高分子整课已导入完成。诊断与14份规范原页在 `PKG-072/knowledge-wmf-gap/final-newrun/`。

## 保存前保护与完整备份

初始正常53份备课／58对象／1112文件、既有只读QA415文件，读取和候选构建均核SHA、字节和mtime。正常完整profile备份为1,161,845,466字节，ZIP全1112项CRC检查并独立恢复回读通过，原profile保持。`fresh-normal-backup/verification.json`为当前完整备份回执。

## 隔离QA保存、明确重开与恢复

`qa-worker/verified-course/verification.json`以`saved_reopened_and_restored_private_course_verified`完成核准课堂的隔离保存、原生Studio明确重开和独立恢复，回执SHA `41fdb2ef59835bd06892c3a1bc0c1a482e05ab3fd705169acf6f54bd6f4dd503`。保存后与恢复后的两次原生chooser均明确点击“打开”，十字段、全文、revision、编排、45个图像资产及六个登记成品保持一致；无MainWindow或Recovery控制器，未再转换Office。

普通备课ZIP为30,349,673字节，SHA `3388f8fd83bc199f13c2fdd4a598e336f4f5baf02a9f5ff524ecc5fb0bb2b294`，52个备份文件为state＋45图＋6成品，CRC检查通过、缺失0；独立恢复后的清单SHA／字节与完整payload均相等。所选素材快照、图像字节及六成品在ZIP内；原始DOCX／PDF资料库和后台来源、裁选、许可回执不在普通备课ZIP内。

该QA运行前后正常1112文件与既有只读QA415文件全SHA／字节／mtime一致。`actual-source-preservation.json`核对本课实际六原件共158,440,224字节全部保持，另对两棵原始资料树记录写入尝试0；此前596文件全树哈希扫描已中断并留证，不称全树哈希通过。

## 原生UI与补充事件审计

真实Workspace→四完整知识picker→b32原图picker→完整题199—209确认→Studio→CompositionPanel已执行，确认请求与六素材计划相等；首次6素材／26图通过一次历史事务加入，撤销／重做精确恢复，重复请求新增素材与历史均为0。来源切换、关闭窗口及目标变更的迟到结果检查完成，实际后端返回未替换；Qt为offscreen，调度替身只控制任务时序，未启MainWindow或Recovery。

`native-ui-worker-protein-v2-event-audit/checks.json`状态为`corrected_event_audit_of_existing_actual_run`，补充审计通过；`raw-files-freeze.json`绑定v2-final全部51个证据文件及6张原生截图的SHA、字节数和mtime。只有一次非空publish6／accept6；重复请求publish0／accept0无新增。取消测试故意执行真实迟到操作，产生publish0及两次幂等discard0，无accept或素材采用。生产26文件、原DOCX和正常1112／既有QA415文件前后清单一致，保护写入尝试0；这是该原生运行当时的只读保护范围，不预判后续正常保存。

原失败记录未改成PASS：v1为QTextLayout审计JSON序列化失败；v2为空图片字段的运行时省略断言错误；v2-complete为请求不存在title字段的审计错误；v2-final为末尾事件清单断言过严。v2-final已记录的界面行为检查通过，补充审计只更正末尾事件判定，没有重放或伪造界面结果；随后多余的v2-verified重放已中止，不作完成证据。未据这些脚本错误改生产接口或提升来源资格。

## 正常新副本采用与原生明确重开

`normal-adoption-result.json`状态为`adopted_and_verified_private_course`，SHA `f6084bef5883ecb05a8b067a73b04e04a491564f4cbfc495e6fa30dca38ee6a8`，详细回执位于`normal-adoption-ac05e131f6/`。正常资料区新增私有备课“蛋白质与核酸：结构怎样决定性质与功能？”，draft为`prep-2cd1935637044c92af1e5dd71dab5ff0`，revision为`7d17d07bd358b1230f4a617f796c17cb3acf1d5703e5f9e8c719fd52637c3344`；这是正常新副本，与隔离QA的draft分别保留。54份备课／59对象／1245文件包括133个新增文件，45图与六个登记成品回读一致；原53份备课／58对象的全部记录和revision精确保持，原1112文件仅state有授权追加，其余SHA／字节／mtime均保持。原题范围及来源资格不变。

`native-normal-reopen/verification.json`状态为`normal_saved_course_native_reopen_verified`，SHA `fb1e6204e810e58f411030cb93b83be03023a03f58a6f1f3f66fbe5e8a799cc8`。真实picker搜索主题命中1项，经QTest明确点击打开正常新副本，完整payload与核准课堂相等，draft与revision正确；重开前后正常1245／既有QA415文件全SHA／字节／mtime保持，写入尝试0，无MainWindow。

首轮正常采用预检因把教材专用`kind`字段用于五条`original_photo_record`照片记录而触发KeyError，写入前停止，原1112文件全SHA／字节／mtime保持；`normal-adoption-preflight-v1-failed.json`保留原失败。按14条教材与5条照片分别精确校验修正后重试成功，未改写首轮失败。

## 后续范围

072完整知识、含图表格原生课堂投影、全资料覆盖、独立化学与真人课堂验收仍待后续；原59项／X01—X10和整体目标继续active。本轮私有课例保存不解锁来源资格或正式发布门槛，未发布，应用版本及旧发行不变。

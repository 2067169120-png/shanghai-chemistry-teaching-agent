# 2026-10-08 原生含图知识表格与扩图备课验证

本轮维护源码已完成含图Word知识表格的画布、PDF、PPT和Word输出联动，并补充两幅教材原图、一张真实蚕茧照片及DNA原图复用。062讲义的四个完整知识单元保留89个来源块、45次原图出现；其中B45为7行3列、21个单元格，9次图片出现对应5个不同图片资产。采用稿v10为60份素材、48个图片资产；Windows布局为教师54页、课堂28页，实际Office为28页PPT、34页教案、14页讲义。独立Windows保存、重开和普通单课备份恢复已通过，正常工作台已更新原备课并重开核对，未新增重复备课。

证据根目录为 `E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench`。本轮是在[上一轮蛋白质课验证](2026-10-08-protein-polymer-workbench-qa.md)基础上继续维护源码；应用试用包仍为v0.1.101，提示模板版本仍为35，没有重建EXE或发行附件。

<a id="native-table"></a>
## 原生表格与输出

来源DOCX的SHA-256为 `4bf36447638b9835792e99c25ce5fed43dbddc13f326e69e585efe64f3f7cc91`。显示投影按封存表格的行、列及图片出现顺序建立对应关系，逐次检查占位符、原文本、来源位置、尺寸、摘要和可见范围，并核对当前工作图片块。相同资产在源文出现多次时保留全部出现；只有完整投影成功后才消费对应的独立图片块，避免表内显示后又在表外重复。

| 项目 | 实际结果 |
| --- | --- |
| 源表结构 | B45完整7×3、21格；9次图出现、5个唯一资产，同一资产出现5次 |
| 原图单元格 | R3C2×1、R4C2×1、R5C2×2、R6C2×2、R7C2×1、R7C3×2 |
| Canvas、PDF | 图文在对应单元格内；保留整行分页、续页表头、合并单元格行为；采用24pt正文和1∶2∶2三列投影 |
| PPT | 表格图文位置与画布一致；文字为可编辑文本框，递归累计单元格、行与文字偏移，文本框高度取实际字形高度 |
| Word | 真实可编辑表格；9次原图全部进入对应单元格，图片宽度受实际单元格可用宽度限制，无表外孤立重复 |
| 原始证据 | 原件、封存快照、图片原字节与出现顺序保留；R7C3两图使用源对象缓存显示图片，不解释或执行OLE二进制 |

教师修改表格工作文本、替换图片、改变图片顺序或来源范围后，旧单元格绑定不再生效，按工作副本回退；只调整图片显示尺寸不会被当作换图。未知对应关系不生成原表投影，非法原快照或摘要仍报错。回归覆盖重复资产、多层坐标、横纵合并、分页、深色系统配色下的正文可读性、Word单元格限宽及编辑后的回退。

证据见[实现冻结](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/implementation/implementation-freeze.json>)、[B45实际核验](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/implementation/actual-block45-verification.json>)及[逐出现坐标与来源映射](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/implementation/actual-block45-occurrences.json>)。实现冻结文件SHA-256为 `eb6566313ce5911a7ee0b1aea3f2f129728f475390095ee1a6c2ca6fca030b4b`。

完整来源导入QA使用6份素材，教师画布30页、学生画布29页；B45在第3、4页，分别显示源行1—5、重复表头及源行6—7。两份QA Word各保留46次可见源图出现，其中B45恰好9次。这套全源导入QA用于检验完整性，未作为新的课堂教学设计交付。

实际Microsoft PowerPoint将这份学生全来源QA转为29页PDF，主代理直接检查第3、4页的实际表格单元格，并核对PDF文字与几何。第4页完整续标题为26字，PPT框位于x15、y15毫米，宽290、高13.493722毫米；实际PDF字形边界距页顶45.594—74.721pt，指定截图的标题黑字边界为y82—131像素，没有页顶裁切。第4页图片SHA-256为 `cb32b4a68fa3621516c4dfe8b508625499cf3f49c729e0d8d2d53c6fd9e6c75c`。见[实际源表PPT转换回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-actual-table-ppt-v2/verification.json>)、[实际第3页](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-actual-table-ppt-v2/table-page-03.png>)与[实际第4页](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-actual-table-ppt-v2/table-page-04.png>)。此结论限定于模型视觉、实际单元格及PDF文字几何检查；最终28页课堂保持自己的教学编排，教师完整源表继续留在教师范围。

<a id="current-course"></a>
## 当前扩图课及实际Office（v10）

本轮采用稿保留两课时、每课时40分钟的教学安排。B45原来的教师改写读稿单独保存在教师笔记“六种常见氨基酸怎样比较？”中，角色为 `teacher_note`；经明确整理，源表工作副本回到封存原文以启用完整表格显示。这项课例整理与渲染器对教师编辑的回退行为分别验证。

| 核对项 | v10结果 |
| --- | --- |
| 素材与资产池 | 60份素材、48个图片资产；相对55素材过渡课新增3个图片资产，原45个资产的元数据和字节保留 |
| 原来源 | 原54份素材的来源快照全部相等；四知识89块、45次原图、B45表内9图和教师笔记保留 |
| 课堂设计 | 原27页的ID与顺序保留，其中24页内容不变；原第4、15、23页补充配图，原第15页后插入1页，合计28页 |
| 图文与互动 | 三知识网、两流程、完整四选项题及9组点击保留；正文保持24pt，教师专用完整来源不进入学生范围 |
| Windows实际布局 | 教师54页、学生静态28页、课堂28页；全部0布局错误、0空页 |
| 实际Microsoft PowerPoint | 课堂PPT为28页，9组点击 |
| 实际Microsoft Word | 教案34页、课堂讲义14页；5份新增图文素材放在对应知识段落旁 |

本次新增图4.6两性离子原图（PDF84／印刷80）、图4.11黄色反应原图（PDF87／印刷83），来自同一教材原页裁选入口，保留源文件SHA、原页、裁选边界与原生导入回执；新增蚕茧照片来自Fumikas Sagisavas的《Silkworm cocoons》，许可为CC0 1.0；已有DNA教材配对图复用于双链计数页。原件与许可证据见[教材裁选记录](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/extra-protein-visuals/crop-materials.json>)和[照片权属核验](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/extra-protein-visuals/external-cocoon-attribution-v3.json>)。

实际PPT逐图字节和位置核对得到：课堂28页中16页含图片，共17次图片出现、16个唯一图片；教材原内容裁片为8个唯一素材、9次出现，讲义图5次，实物照片3次。教材裁片中含原第14页的一处教材原文段落截图，不能把全部裁片都称为图示。含图页为1、3、4、5、8、10、12、13、14、15、16、19、20、22、24、26。相比过渡课的13次图片出现（6教材、5讲义、2照片，分布12页），本轮增加两幅教材图示、一张照片及一次已有DNA图复用。见[实际PPT图数、标题与点击核对](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-qa-worker/extra-visuals-saved-restored-windows-v1/actual-picture-title-click-proof.json>)。

| 最终课堂页 | 图文用途 | 实际Office图 |
| --- | --- | --- |
| 4 | 两性离子原图与完整酸碱反应解释相邻 | [第4页](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-expanded-course-office-v10/actual-office/student_presentation/page-004.png>) |
| 15 | 黄色反应原图保留试管、浓硝酸和黄色现象，完整解释在旁 | [第15页](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-expanded-course-office-v10/actual-office/student_presentation/page-015.png>) |
| 16 | 蚕茧实照联系蛋白质组成与连接；酶的完整知识仍在相邻栏 | [第16页](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-expanded-course-office-v10/actual-office/student_presentation/page-016.png>) |
| 24 | 复用DNA配对图，配合“只数一条链，还能断言A＝T吗？”及完整计数说明 | [第24页](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-expanded-course-office-v10/actual-office/student_presentation/page-024.png>) |

v10输入候选SHA-256为 `4ae0783ec8525fa03e3cd4b2e95293e085cfaf4d5aaab7c8ff254a30d5901736`，登记成品后的保存稿SHA为 `ebb17e33708150c5f9930bda177bd891deca40a2f944ecdd56454438ca0cad32`；Office登记ID为 `OUT-d4c805fe8f5248b9b3f8014b2ffc9edf`，登记文件SHA为 `041e75e21cf799168f07e5de6b3ae5209f30224f65e0cbe7be851179d66e3d21`。打开[课堂PPT](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-expanded-course-office-v10/课堂文件/课堂PPT.pptx>)、[教案Word](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-expanded-course-office-v10/课堂文件/教案.docx>)和[课堂讲义Word](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-expanded-course-office-v10/课堂文件/课堂讲义.docx>)。文件摘要与Office引擎见[最终Office回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-expanded-course-office-v10/verification.json>)。

主代理直接查看了覆盖全部实际Office页面的8张联系图、上述4张课堂大图及教案第27页原生表格大图，共13个不同文件；新增配图清晰且与知识相邻，Word表内9图可见。见[根视觉核查回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/root-final-office-visual-review.json>)。该回执为模型核查，`human_reviewed=false`、`live_classroom=false`，未扩大为真人试用或课堂实投。

55素材／45资产／27课堂页、实际Office27／31／13的v36过渡课，以及实际Office28／33／14的v8扩图稿，均保留为OWN历史候选，未用于本轮正常采用。过渡课完成了原54来源快照和全部原27页元素不变的核对，见[过渡Office回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-current-course-office-v36/verification.json>)；该历史结论不替代最终扩图课的变化清单。

排版版本由 `shchem.composition-classroom-exposition.v35` 升为v36，内容指纹随真实输出语义变化。对同一旧保存稿，历史v35指纹为 `c0b0d459ccaaf36cb8e3f8e483803ba8cf73a2595ef26f5933bd067a014a1570`，按v36计算为 `ab82723b2bb22bdd10a78240313292840781998a3d5ae164c2b262a4ca4b0804`；最终v10成品指纹为 `610c980bbf491b4f5e38bb141a9e0988baf86f11df7111675d1a01379f5c58a2`。旧v35六件登记成品经 `checked_file` 按原哈希仍可读取，兼容检查所用的历史保存稿字节未变，未重标旧文件版本或为其他旧课批量重生成。详见[版本兼容与实现交接](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/implementation/implementation-handoff.json>)。

<a id="native-windows-layout"></a>
## Windows固定续框问题及v10修复

v9在真实Windows生产字体环境中出现教师54页／学生30页／课堂30页，教师布局有3项错误，学生范围多出2张空页。失败涉及“氨基酸的结构和性质”和“蛋白质的结构和性质”两个教师完整读稿的既有固定续框；此前“只是空尾框”的初步猜测已撤销。表格及正文虽使用固定像素字号，当前平台的字体回退与实际行度量仍参与换行和表格分组，offscreen结果不能替代Windows验证。[v9失败探针](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/extra-protein-visuals/windows-v9-baseline-probe.json>)原样保留。

v10在Windows原生排版入口中仅重新排满这两个教师单元，各自8个固定框换为8个新框；所有60份素材记录、48个资产、公开课堂内容、4处目标图文几何、24张无关课堂页及互动保持，未手动过滤空页、缩小字号或删图。随后Windows生产应用、新进程offscreen及主代理独立Windows只读复放均得到54／28／28页、0错误、0空页。此次为课例工作副本的排版调整，v36代码冻结保持。见[原生重排验证](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/extra-protein-visuals/candidate-course-v10/verification.json>)、[原生重排请求与结果](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/extra-protein-visuals/candidate-course-v10/native-reflow-requests-results.json>)和[根独立Windows复放](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/root-native-v10-layout-proof.json>)。

<a id="native-edit"></a>
## 原生Qt操作与教师改文

最终v10在独立Windows QA中通过原生备课列表“打开”保存稿及普通备份恢复稿，完整保存稿与10项教师输入字段相等。恢复后B45投影成功、布局无错误，九次图片全部在表格内，表外重复为0。课例沿用图片高度限制，表格分为源行1—6，以及重复表头加源行7；该分页与前述全源导入QA不同。

以下为v10独立Windows恢复稿中九次图片的实际坐标，单位为毫米；页A、B分别为 `CP-8601436e0fad4d7895c48b0a0b96f3e3`、`CP-074698ee21c2409e9d0f4c82d4289ba4`，完整精度和图片块ID保存在[最终保存恢复回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-qa-worker/extra-visuals-saved-restored-windows-v1/verification.json>)。

| 出现顺序 | 源单元格 | 页 | x | y | 宽 | 高 |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | R3C2 | A | 98.989 | 79.037 | 7.553 | 7.000 |
| 2 | R4C2 | A | 157.595 | 96.727 | 7.553 | 7.000 |
| 3 | R5C2 | A | 74.863 | 125.794 | 15.313 | 7.000 |
| 4 | R5C2 | A | 114.335 | 125.794 | 7.553 | 7.000 |
| 5 | R6C2 | A | 98.989 | 143.484 | 6.611 | 7.000 |
| 6 | R6C2 | A | 105.604 | 143.484 | 7.553 | 7.000 |
| 7 | R7C2 | B | 118.891 | 50.558 | 7.553 | 7.000 |
| 8 | R7C3 | B | 190.863 | 50.558 | 7.636 | 7.000 |
| 9 | R7C3 | B | 232.402 | 50.558 | 5.250 | 7.000 |

教师改文回退另在55素材过渡候选的Qt offscreen原生控件中验证：素材编辑窗口解锁后临时修改表格文字，旧表格投影和图片消费均停止。沿用旧外框时只放下8次普通图片，界面明确提示完整内容尚未放完；不会悄悄截断后导出。再通过原生“一栏、按顺序排满并续页”得到全部9次图片且无布局错误，未复用旧单元格绑定。撤销、重做、再撤销均已验证，最后回到候选原表，临时测试改文未保留；原教师读稿笔记继续保留。

证据见[过渡Qt交互回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-qa-worker/native-interactions-v3/verification.json>)与[改文后续页结果](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-qa-worker/native-interactions-v3/native-complete-fallback-after-repagination.png>)。最终Windows的表格与范围见[恢复后表格及末格原图](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-qa-worker/extra-visuals-saved-restored-windows-v1/native-restored-table-and-ole-cell.png>)。这两组交互均未启动MainWindow或Recovery，未写正常保存课。

<a id="polymer"></a>
## 聚合物的实际完成范围

最终真实archive上下文编译只接受以下三个完整命名栏目。早期只读备忘提出过四栏目候选，第四栏目B232—B253因B238第三图 `flipV=1` 尚未获得忠实显示验证而保留待核，不能把早期82块／44次图的建议计作最终完成。

| 已编译完整栏目 | 来源范围 | 来源块 | 原图出现 |
| --- | --- | ---: | ---: |
| 知识点2 加成聚合反应 | B48—B88 | 41 | 35 |
| 思维建模 高聚物单体的推断方法 | B204—B207 | 4 | 2 |
| 知识点3 功能高分子材料 | B298—B312 | 15 | 3 |
| 三栏目合计 | 各完整栏目范围 | 60 | 40 |

另保留完整题面B350—B355与答案B356—B361，合计4份素材、26个唯一图片资产。该结果验证完整原文及嵌入图片像素的候选编译，不等同于忠实复现全部Word版式；未完成其父知识单元或整讲义，也尚未保存到正常库。见[最终三栏目编译回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-polymer-complete-columns/verification.json>)。

完整上级单元仍有以下精确缺口，逐对象证据见[只读阻断备忘](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/polymer-gap/POLYMER_FULL_GAP.md>)。

- 加聚上级范围包含B34—B47；072讲义B45含浮动定位及QUOTE字段的代码／结果分支图片，具有不同尺寸、透明和位移语义。本轮未实施QUOTE解析，不能简单把三图串排后声称完整。
- 缩聚知识B90—B144含7处空EQ结果缓存：B117、B120、B124、B126、B128、B129、B131；字段代码承载反应箭头、催化剂、H＋及加热条件。仅检查图形不能证明文字完整。本轮增加字段完整性门禁，覆盖空结果与有缓存的复杂字段；未实现EQ或QUOTE内容解析。
- B124、B126的未知WMF位于EQ代码分支，原卷与解析版均无可替代的完整缓存显示；真实参考编译33次图出现仅31次可读，未知WMF绘制调用为0。没有删除不兼容记录或猜绘条件。缩聚配套题B192—B203另有B203源图裁剪缺口。
- 材料完整范围B232—B312仍包含B284两个PBrush对象，二者各自绑定同一10×31像素缓存图；必须保留两次出现。现有对象守卫继续拒绝，未执行OLE或把缓存候选写成已支持。B238等翻转、B258亮度／对比度、B293裁剪仍需逐项忠实显示核验。

<a id="context"></a>
## 自足题面判断与旧审核保留

材料题的B350已明确芳纶、玻璃纤维及碳纤维，B351原图包含同三项；B355“上述材料”指当前完整题面。本轮修正自动属性建议中过宽的缺上下文判断，前题依赖或确有缺失材料的情形继续阻断。已有教师材料审核保持原值，修正启发式不会静默覆盖旧审核。

先在独立OWN副本完整复制3684条属性及9737条历史，通过原生 `WordQuestionAttributeStore.save_many` 对唯一题目key `word-question-9a95412ff322252f7106ae9b` 作带预期版本的保存，历史增至9738条。全部旧历史及无关属性不变，过期预期版本被拒绝；显式保存前旧审核仍阻止读取，保存后题面B350—B355及答案B356—B361可完整读取。此阶段未修改正常属性库，见[上下文修复冻结](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/context-fix/handoff-freeze.json>)与[OWN原生并发版本保存回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/context-fix/candidate-native-cas-verification.json>)。

最终在正常属性库中对同一题目执行原生预期版本CAS，唯一业务字段变化为 `material_status.missing_context: true → false`，保存版本为5、revision为 `3ce8dc059002463405ddd3b2a87b9b564ca7d580341f33de76f3afa6b58bd8b3`；3684条当前属性保持总数，历史由9737增至9738。其他3683条属性和全部既有历史保留，题面B350—B355的1次原图及答案B356—B361回读通过。此为自动缺上下文判断的明确修正，`teacher_confirmed=false`；不将模型核查写成人工确认，也不代表聚合物整单元已导入。唯一受此操作改变的既有文件为 `word-question-attributes.sqlite3`，见[正常库唯一题目CAS回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/normal-exact-question-context-result.json>)。

<a id="tests"></a>
## 回归结果、保护范围与视觉证据

| 检查集合 | 最终结果 | 记录 |
| --- | --- | --- |
| 含图表格、相关渲染和输出 | 206通过 | [表格回归XML](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/implementation/final-regressions.xml>) |
| 字段完整性与相关导入 | 143通过、3跳过 | [字段最终回归XML](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/field-completeness-tests-v2.xml>) |
| 自足上下文判断与属性 | 148通过，包含42个专用用例 | [上下文回归记录](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/context-fix/pytest-regression.txt>) |

上述集合有交叉，不相加为总用例数。字段门禁首次回归有9项失败：预览块未携带字段features，初版读取了错误层级；修复为读取真实 `WordNativeTextReader.read(...).features` 后，再运行得到143通过、3跳过，另有10项针对性检查通过。[首次记录](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/field-completeness-tests.xml>)与最终记录分别保留。

截至独立候选各回执记录的阶段，正常profile1245个文件及只读QA来源415个文件的SHA-256、字节数与mtime全部相等，保护目录写入尝试为0。这是采用前证据。最终采用后正常profile为1282个文件；只有下节列出的授权新增和两份既有文件变化，不能再以“1245全部未变”描述最终状态。QA来源415个文件及原始来源文件保持。

视频参考扫描了44个标题，校验并探测4个原始MP4；探索核查看过9个新提取画面及3张既有联系图，未连续播放或收听44段视频。主代理另直接查看V28的64秒、101秒两个画面，分别用于理解完整三列表与图旁任务安排。视频只支持课堂组织参考，不证明原生表格导入或导出成功。实际软件与Office图像核查均为模型检查，`human_reviewed=false`、`live_classroom=false`；不写作真人试用或真实投影验收。

<a id="normal-adoption"></a>
## 正常工作区采用与最终保存恢复

最终v10已在独立Windows QA中完成原生保存、重开与普通单课备份恢复；保存稿与恢复稿全文及10项教师输入完全相等，60份素材、48个图片资产、四知识89块／45次原图、B45表内9图和六件登记成品均保留。恢复后为54／28／28页、0错误、0空页。备份包含1份备课、48图、6成品，共55个内容文件，连同清单共56个ZIP条目，缺失文件为0，ZIP CRC通过。该普通单课备份不包含原始教材／讲义库和后台裁选、许可回执，原件仍需另存。

回执：[最终Windows保存恢复验证](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-qa-worker/extra-visuals-saved-restored-windows-v1/verification.json>)，SHA-256 `6aa5321fc3dc5df245a28239121c84e1700783475789d9a49322867ca30966cc`；[普通单课备份](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-qa-worker/extra-visuals-saved-restored-windows-v1/expanded-protein-course.shchem-backup.zip>)，SHA-256 `b3e7d2729eb19fe9ce07e95baf7482d7b9daca0165501a6470accb3ec5b4f3fe`。

正常工作区已通过原生预期版本比较更新原备课 `prep-2cd1935637044c92af1e5dd71dab5ff0`，新版本为 `d5952fb202328c7d04eed3a591e809b3e48ef91dfc57184332dc2901f5022a3f`。备课仍为54份、工作对象仍为59个；原54素材来源快照、其他58个工作对象及其版本、旧图片和旧v35六件成品保留。成品历史由1组增至2组，新增的是本轮v10登记成品；55素材过渡稿与v8稿未采用。

采用前完整备份1245个正常profile文件，ZIP CRC及独立解压后的SHA、字节数、mtime回读一致。最终新增37个文件，包含3个图片资产、新成品与预览、裁选回执及后台来源／许可核验；备课更新改变 `desktop-state.v1.json`，上述唯一题目CAS改变 `word-question-attributes.sqlite3`。正常profile最终1282个文件，QA来源415个文件保持。精确清单和完整备份摘要见[正常原备课更新回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/normal-update-result.json>)，采用依据见[绑定的课程核验记录](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/root-approved-private-course.json>)。

采用后以真实Windows平台打开原生备课列表，搜索、选择原课并显式点击“打开”，回读全文与目标保存稿相等，60素材、48资产及两组登记历史一致。该步骤未启动MainWindow，未执行生成或正常库恢复；其范围是原生备课入口与保存稿回读，独立恢复由前述Windows QA完成。见[正常课原生重开回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-native-image-table-workbench/native-normal-updated-reopen/verification.json>)。全部采用限本地私人备课，未将模型核查标为真人确认或对外发布。

[返回教师操作](../teacher/lesson-and-paper.md#native-image-tables) · [返回主指南](../../README.md)

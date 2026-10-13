# 2026-10-08 混合讲义所选素材编辑 QA

状态：维护源码的回归与独立 Windows 原生操作已通过，最终原生回执为 `native-edit-v3`。本页只记录混合讲义清单内编辑的范围；本轮整体目标仍在继续，未新增 Normal 保存或 Office 成品，未发布新试用包。

## 入口与行为范围

维护源码入口为 **备课 → 从讲义选材… → 本次授课顺序 → 编辑所选素材…**。教师操作见[混合讲义选材](../teacher/lesson-and-paper.md#mixed-material-teaching-order)。

| 所选素材 | 此编辑入口可改 | 保持的内容 |
| --- | --- | --- |
| 知识栏目 | 本课引导问题、仅教师可见 | 来源身份、所选栏目与原文、混合清单位置 |
| 讲义原图 | 本课引导问题、讲解用途、仅教师可见 | 原图片及其来源绑定、混合清单位置 |
| 完整题与答案 | 此编辑入口不可用 | 原选题请求、题答与原分值 |

点击 **保存本次修改** 只替换当前选材项的上述课堂设置，并保持选中位置。知识和原图的本课引导问题必填且最多 160 字，原图讲解用途最多 500 字。点击 **取消** 保持原设置。

同一原图允许不同讲解用途或适用范围。修改后若与清单另一项形成相同的原图、用途与范围组合，保存被拒绝，两项均保留；不会合并或丢弃另一项。标题不参与此重复判断。

完整题没有元数据编辑窗口，按钮提示为“完整题与答案保留原选题设置；课件讲解可在加入备课后编辑。”上游完整选题窗口仍支持原有练习设置；其选题请求存在 `points`，但本课资源没有相应持久化字段。本轮新增的编辑入口保留该请求的题答与分值，也不把试运行中的额外分值控件列为交付。

## 来源与交接边界

- 编辑期间保持原混合顺序、数量、来源、栏目与原图身份；此窗口不重读原文。切换讲义、关闭窗口、原清单或栏目版本已变化后的迟到结果不能写回。
- 最终 **加入当前备课** 重新读取并比较讲义来源，按整批 `selection_order` 交接。核对期间不能编辑或调整清单。
- 清单编辑用于本批新增素材，沿用既有去重与教师编辑保护。已入课素材通过 **素材与页面编排 → 阅读／编辑完整素材…** 修改作品副本；再次选择相同来源不会成为覆盖旧课标题的入口。
- 普通来源读取失败保留原清单，并允许重试；只有实际来源版本不一致才标记来源过期，要求重新读取。
- 原有答案范围合同保持：`source_role=answer` 的知识和原图锁定为仅教师可见。正常导入目录当前没有该角色，答案范围只能依据夹具测试记录，不能冒称真实答案讲义操作已验收。
- 完整知识的字段与图片守卫继续生效。高分子讲义仅有缓存文字不足以解锁完整知识；本轮没有降低门槛、解释未知字段或扩大已完成导入范围。诊断见[本轮选题依据](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/next-import-source-diagnosis.json>)。

## 证据状态

本轮证据根目录为 `E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow`。

| 项目 | 当前记录 |
| --- | --- |
| 源码与专用测试 | 最终 `00af2ec…` 源码；编辑专项及既有窗口 45 passed／2 deselected，整批交接 76 passed／3 skipped，分别报告 |
| 独立 Windows 原生资料与交接 | `native-edit-v3` 绑定最终源码；真实编辑、编译与画布、整批撤销／重做、OWN 保存及原生重开通过 |
| Normal 与 QA 身份保护 | Normal 54 份备课、59 个对象、1,282 文件和只读 QA 415 文件保持；全部 SHA、大小、修改时间相等，保护目录无写入尝试 |

起始状态见[初始状态回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/initial-current-state.json>)。当前正常课例为 60 份资源、48 个图片资产，既有实际 Office 为 28 页课堂 PPT、34 页 Word 教案、14 页讲义。本轮只在 OWN 隔离副本保存和重开，不新增正常课例保存、导出或 Office 渲染。上一轮已经采用的课程、普通保存恢复与实际 Office 证据保持在[原生含图表格 QA](2026-10-08-native-image-table-workbench-qa.md)，本轮不覆盖其历史结果。

## 当前源码回归

[实施冻结回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/worker/metadata-edit-final-freeze.json>)绑定以下文件与结果；[根代理复核](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/root-reviewed-code-and-tests.json>)重新核对了源码、SHA、AST 和 JUnit：

| 文件或回归 | SHA-256／结果 |
| --- | --- |
| `handout_material_workspace.py` | `00af2ec71ab9fd083188ed20bbbd11150360f42e31142c634906b4ad0fdcddf9` |
| `test_handout_material_metadata_edit_ui.py` | `80d53bbc68048cadf3ebdd47bc45bc0a9a1a7c83a5980a50718b0cb6dabf00d2` |
| [最终窗口回归](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/worker/metadata-edit-ui-v3.xml>) | 22 项编辑专项与 23 项既有选材窗口测试通过；2 项需要 MainWindow 的测试未执行。回执 SHA `700442c7184aaa38c00a91ac0de53eec8b87eea7012adbcb5944b0f79f1cbe84` |
| [整批交接回归](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/worker/metadata-edit-handoff-regression.xml>) | 76 passed／3 skipped；3 项跳过分别为纯文字来源无图片、两项图片复制竞态不适用。回执 SHA `b2539ebd24e0124610d857e655fbf197729075e233087adcb2df6dc162cb6cac` |

编辑专项覆盖预填与原位替换、同图不同用途、取消、完整题原值保持、答案来源范围锁定、用途或范围碰撞、容量上限、空标题与过长用途、编辑期间的动作限制、来源或窗口已变化后的迟到回调、真实版本不符与普通读取失败的不同处理，以及宽窄窗口下的控件可达性。这些是字典夹具与隔离临时目录测试；未由该回执打开正常配置、只读 QA 配置、MainWindow 或恢复入口。

历史测试保留：首次 `metadata-edit-ui-first.xml` 为 55 passed／2 failed／2 deselected，失败来自答案角色夹具没有重新选择变更后的来源；随后试验 `metadata-edit-ui-final.xml` 的 57 passed／2 deselected 包含后来撤除的分值编辑，不能代表当前交付范围。`metadata-edit-ui-v2.xml` 的 45 passed／2 deselected 早于来源版本错误文案修订。最终口径为上表 v3 和整批交接回归，各次结果不相加。

原生操作历史也单独保留：`native-edit-v1` 跑过实际流程，但执行期间生产源码摘要发生变化，不能作为最终源码稳定通过的依据；`native-edit-v2` 因 QA 检查脚本把放置字典按列表切片而停止，未验收，无需修改生产代码。最终通过口径为绑定 `00af2ec…` 的 `native-edit-v3`。

## 独立 Windows 原生验收

[最终原生回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/native-qa-worker/native-edit-v3/verification.json>)的 SHA 为 `c55bccd17a31a94a34a27bf18c3daba470c5e156cf8c52a28e086f4fc3e79069`。运行平台为 Qt `windows`，使用正式应用字体族 Microsoft YaHei UI、Cambria Math、Segoe UI Symbol；所有来源与编译结果来自实际后端，运行前后生产源码保持。

使用真实 PKG-062《第07讲 蛋白质 核酸（解析版）.docx》，原归档 SHA 为 `4bf36447638b9835792e99c25ce5fed43dbddc13f326e69e585efe64f3f7cc91`、2,480,135 字节，前后摘要、大小和修改时间相等。[来源准备回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/native-qa-worker/preparation-v1/verification.json>)记录完整来源为 428 块、159 原图、16 栏目、23 题；本次选择原图、四段完整知识和一道完整题，形成 6 份资源、26 个图片资产，四段知识保留 89 源块、45 次知识图片出现及原知识网。来源文件名含“解析版”，其实际 `source_role` 仍为 `handout`；真实目录中 `answer` 角色为 0。

| 核验环节 | 实际结果 |
| --- | --- |
| 清单编辑与取消 | 知识仅改本课引导问题与范围；原图仅改引导问题、用途与范围，原图范围先勾选后取消均生效；原位置和来源绑定保持。取消与重复用途碰撞均保留原清单 |
| 完整题与来源重核 | 完整题编辑按钮不可用，原题、答案、范围和请求分值 2 保持；未声称画布支持这个分值字段。最终来源核对期间编辑入口禁用 |
| 编译、画布与整批操作 | 编辑后的知识引导问题、原图问题与用途进入真实资源和原生画布，知识画布布局错误为 0；一次整批撤销／重做完整通过 |
| 现有课例保护 | 原课隔离克隆中的旧 60 份资源、48 个资产及原始图片副本、54 页记录、60 个放置记录保持；其他核心字段、教学设计、成品记录与 9 次点击设置保持。新用途只新增 1 份图片资源，复用原资产；重复知识与完整题没有覆盖教师已改内容 |
| 保存和重开 | 使用生产保存入口写入 OWN，随后通过 `Studio._open_draft` 的原生模态选择器点击重开；另建独立读取对象核对草稿 ID、revision、整个 payload 和全部 26 个图片资产，结果相等 |
| 宽窄窗口 | 清单动作和知识编辑控件经滚动可达。1200×850 与 420×760 为 Qt 逻辑尺寸，对应 PNG 实际 1500×1063 与 525×950 像素，符合 Windows 125% 缩放；这些均不代表物理显示器分辨率 |

代表页面见[知识编辑](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/native-qa-worker/native-edit-v3/03-native-knowledge-edit-prefilled-and-revised.png>)、[原图问题与用途](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/native-qa-worker/native-edit-v3/04-native-image-edit-question-purpose-scope.png>)、[窄窗口清单](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/native-qa-worker/native-edit-v3/06-mixed-selection-edit-reachable-420px.png>)、[实际原图讲解画布](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/native-qa-worker/native-edit-v3/08-edited-image-question-purpose-native-canvas.png>)、[原生重开选择器](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/native-qa-worker/native-edit-v3/11-native-saved-edited-input-chooser.png>)。

QA 代理直接查看了全部 14 张截图，并核对克隆和独立重开结果，[视觉与读回的尺寸补正回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/native-qa-worker/native-edit-v3/final-model-visual-and-readback-review-v2.json>) SHA 为 `228c7ea7751689c60ffb33ab2e9433b4d0e4c5214230815e53248c90115b09ba`。原 `verification.json` 与初版视觉回执曾把 Qt 逻辑尺寸写入 `pixel_dimensions`；补正回执将逻辑尺寸另列，并从全部 PNG 文件头读取真实像素尺寸。原回执和 14 张截图的字节、SHA 与修改时间保持，无需重跑原生流程。

上述视觉复核是模型检查，不是真人或现场教学验收，不据此认定化学正确性或允许发布。原生流程未启动 MainWindow 或 Recovery，未初始化 Normal 写入器或图片仓库；Normal 1,282 文件及只读 QA 415 文件的完整身份比对通过。

## 参考与验证边界

本轮根代理直接查看并重新核对原文件 SHA 的参考范围为 V10 32 秒、V28 101 秒与 V32 39.06 秒三帧，见[参考视频回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-workbench-next-flow/root-reference-video-review.json>)。三帧用于原文、候选池和课堂成品的布局参考；编辑缺口来自当前代码核对。没有据此声称视频演示了清单编辑，也没有完成 44 个完整视频或音频审阅。模型视觉检查不是真人教师验收。

## 实现与验证入口

- [混合选材窗口](../../integrations/deeptutor_shchem_v1/desktop_workbench/handout_material_workspace.py)
- [编辑窗口专项测试](../../staging/coordination/deeptutor_gateway/tests/test_handout_material_metadata_edit_ui.py)

不同回归集合分别报告，不相加；真实资料验收与夹具的答案角色锁定测试保持区分。本轮没有完成高分子整单元导入或软件发行，整体目标仍在继续。

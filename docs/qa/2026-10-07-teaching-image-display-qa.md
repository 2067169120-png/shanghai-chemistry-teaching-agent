# 教学图片显示与局部分页 QA（真实隔离 Office 与恢复完成，未发布）

2026-10-07。本轮围绕原图显示大小与知识栏目分页继续优化维护源码，未提交发布。
证据根目录 `ROOT = E:/项目/上海高中化学等级考/outputs/2026-10-07-teaching-image-display/`。

## 实现范围

- 针对具体原 `image_block_id` 设置自动显示或指定毫米宽／高上限，等比缩小完整原图；原像素、来源、角色、正文及原快照保持。
- 原生 **图片显示与分页…** 按原图区块选择具体图片并显示缩略图；宽／高上限的 0 表示自动，可恢复该图自动大小。按教师完整页／课堂 PPT 完成态／静态学生页读取测量后的完整预览，支持续页导航与放大。
- 教师明确选择是否在同一事务中仅重新分页当前素材，使其使用独立页面；其他素材位置和共享页保持，取消不应用，修改可一步撤销。
- 用 b22—23 原栏目装饰 banner 与完整知识网检验逐图上限；不删除 banner 或裁剪知识网来取得更少页数。
- handout 知识短编号标题在画布／PDF／PPT 中随正文分页，沿用正文文字字号，不靠缩小字体消除孤立标题。
- composition Word 教案及学生文档（课堂讲义／学习单）先按 Word 原有可用宽度适配，再按同一逐图上限等比缩小；无设置及非 composition 节点材料沿用原路径。没有重写 Word 正文分页，不以 PPT 页数预测 Word 页数。
- 没有显示设置的旧草稿与普通 ZIP 保持默认路径；新设置随作品保存、恢复。历史登记成品仍可打开，不重写旧输出。

## 真实原生操作与 Office

从上一轮 43 资源／16 图保存稿复制到正常 DATA 之外的新 profile。实际 Qt 操作取消、设置上限、局部重新分页、两次分别单步撤销／重做；取消与撤销／重做前后 payload 相等。仅两项所选素材的位置改变，仅知识网素材增加显示设置，原 blocks、来源、角色、SHA 及 16 图资产相等。7 张原生截图记录宽窄窗、完整三范围预览、放大与应用后画布。

b22 banner 高度上限 6 mm，b23 完整知识网高度上限 125 mm，三范围中这一素材均为一页。b29—36 选段的 b32 分类短标题与 b33 正文在同页，字号未缩小。全作品教师／静态学生／课堂画布为 33／29／30 页，三范围均 0 排版 errors；实际 Microsoft PowerPoint 为 30 页，原 5 个点击效果保持。

根模型限定检查实际 PPT 第 26／27／30 页：分类标题与相应正文相随，原 banner 与完整知识网同页。原选段末尾 b36 只有“(2)导电性强弱”，本片没有延伸原文，不把该截取范围称为全套分类课堂或完整 30 页课堂设计质量通过。教材／课外素材数量没有新增，正常课仍是原 12 图结构。

两份 Word XML 验证原图片字节 SHA 保持，banner 显示为约 22.39 × 6 mm，完整知识网按 Word 可用宽度显示为 165 × 88.73 mm，均满足所设上限。实际 Microsoft Word 教案为 26 页；根模型限定检查第 7／8／23／24／25／26 页共 6 页，第 25 页完整知识网与 banner 同页，无新增裁切。学生 Word 已生成、核 XML 与恢复 SHA，本轮未实际转换或逐页视觉检查。

主脚本完成原生 UI、保存与实际 PPT 后，QA 误取首个重复 Word 标题导致断言失败，保留 `native-v1-run.txt` 与 `native-v1/failure.json`，不计该脚本通过。续核从精确的最后资源标题和已有保存稿／输出继续，没有重放 UI 或再次导出产品；`native-v1-continue-run.txt` 与 `native-v1/verification.json` 终态 exit 0。

## 保存、普通备份与保护范围

普通 ZIP 为 58371981 bytes，SHA-256 `fca0a994ea6ea7892f12a89011d72b50070ae18e09b50e8bbd289cbf0c398fc3`，包含 1 QA 草稿／16 图／6 登记输出／24 文件，缺失 0。全新目录恢复完整草稿、逐图显示设置、16 图字节及 6 登记成品摘要相等；这是隔离课例的普通备份，不是正常 50 份备课全量备份。

正常 1047 文件 SHA／大小／mtime／状态、50 备课／55 对象、原 38 资源／24 页课堂／12 图及模板保持，上一轮 43 资源／31 页 QA 源全部文件保持，protected 写入尝试为 0。QA 课例未保存到正常目录。

上一轮顺序切片的196通过／3条件跳过、31页隔离QA课例及对应限定复核保留历史范围，不作为本轮显示与分页已经通过的证据。

## 最终相关检查与版本

最终 20 文件联合测试 262 passed／0 skipped，257.42s，exit 0；43 项新增后端、16 项新增 UI 与旧组均已包含，不另累计开发组。检查涵盖新旧草稿、逐图绑定及上限、三范围、Word XML、取消／stale／单次撤销、原资料与其他素材保持、保存／普通 ZIP／历史登记成品等相关路径。27 个源码／测试文件 SHA 冻结于 `ROOT/frozen-related-files.json`。

渲染源码已从 v33 升为 `shchem.composition-classroom-exposition.v34`，使显示与分页改动进入成品指纹；无设置旧稿默认兼容，历史登记成品仍按已登记文件摘要读取。EXE 仍 v0.1.101、提示词 v35，应用未发布，微信读书停用。最终完整 README 14 项通过／1.40s／exit 0，文档策略及本片限定 diff 检查 exit 0；初次 README 9005 字超过 compact 上限的失败日志保留，修正文案后终核通过，不计初次失败通过。

## 视频、覆盖与资格

本轮无新增完整视频播放或音轨审核，不从视频推测图片控件。根模型视觉终态仅限上述 3 页实际 PPT、6 页实际教案与 7 张原生 UI；人类复核、live 放映和正式资格未提升。正常课堂仍有 6 教材切片／3 讲义图／3 实景，本片未新增图。原 59 项／X01—X10、旧课堂、模板及中央 validator exit 1 边界保持；整体目标继续 active，不以局部排版改动认定全资料覆盖、化学正确性或整课质量通过。

## 证据入口

- 原生操作与源内容保持：`ROOT/native-v1/ui-stage.json`；实际结果与保护汇总：`ROOT/native-v1/verification.json`。
- 实际 Office：`ROOT/native-v1/actual-ppt.json`、`actual-word.json`、`word-image-extents.json`，以及 `actual-affected-office-page-{26,27,30}.png`、`actual-affected-word-page-{07,08,23,24,25,26}.png`。
- 备份与恢复：`ROOT/native-v1/backup-manifest.json`、`restore-receipt.json`；正常与前序 QA 前后清单分别为 `normal-before/after.json`、`previous-qa-before/after.json`。
- 最终检查与冻结：`ROOT/final-related-test-receipt.json`、`final-related-tests.xml`、`final-related-tests.log`、`frozen-related-files.json`；限定视觉：`ROOT/root-limited-visual-review.json`。
- 文档终态：`ROOT/documentation-policy-final.log`、`complete-readme-final.log/xml`；初次失败保留于 `ROOT/complete-readme-tests.log/xml`。不以机器测量代替人类或 live 放映验收。

操作见[调整原图大小与当前素材分页](../teacher/lesson-and-paper.md#teaching-image-display)；推进范围见[当前闭环状态](../WORKFLOW_STATUS.md#teaching-image-display)。

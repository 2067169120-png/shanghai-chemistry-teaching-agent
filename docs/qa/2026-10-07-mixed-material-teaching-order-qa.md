# 混合讲义选材授课顺序 QA（真实隔离顺序功能终核与限定模型复核完成）

2026-10-07。本轮修复选材清单与实际入课顺序不一致的问题；维护源码未提交发布，应用版本不变。
证据根目录 `ROOT = E:/项目/上海高中化学等级考/outputs/2026-10-07-mixed-material-teaching-order/`。

## 问题与改动范围

旧清单按教师交错选取的顺序显示，确认时拆成知识／图片／题目三组，后端按类型拼接，导致例如原图→知识→完整题→原图→知识在入课后被重新分组。该差异来自源码路径核对，不是视频观察结论。

- **本次授课顺序 · 按这个顺序加入备课** 显示编号，支持 **上移／下移**；移动后选中项跟随，移除后选择相邻项。
- 确认中禁止改动清单，保留停止、关闭和来源变化守护。
- 请求保留三类原始选材及 `selection_order=[{kind,index}]`，后端核对完整、唯一的本批全项排列；遗漏、重复、越界或类型不符拒绝交接。
- 完整题一次编译、知识整批一次编译，再仅按明示顺序排列新增资源；原题角色、来源身份、完整材料、去重及已有教师副本保持。
- 单批交接仍进入原教学设计历史，可整步撤销／重做；不按名称猜测材料关系，不把选择顺序当原卷题号。

## 冻结源码与只读候选

最终 5 测试文件联合检查：`ROOT/related-tests.txt` 为 **196 passed、3 skipped、exit 0，228.31s**，包含最终编号标签与 UI 行为；`ROOT/frozen-related-files.json` 封存 2 源码及 5 测试文件 SHA。开发 UI 初组 25、后端初组 75及后续新增 1 项按当时版本保留；与最终组重叠，不累计。

PKG-056 原 Word 为 298 块／134 图，候选读取保持原 SHA、bytes、mtime及正常 1047 文件。严格选取顺序为 b40 原图 → b29—36 完整知识栏目 → 75—81 完整题答 → b263 原图 → b22—23 完整知识网栏目。b37—56 因 b48／b54 WMF 缺口未选，Word 物理页为 unknown；本轮不称整份讲义或全部题目覆盖。

## 真实窗口、输出及恢复

`ROOT/native-v1-run.txt` 与 `ROOT/native-controls-v1-run.txt` 两阶段均 exit 0。全新隔离 profile／恢复 profile位于 DATA 项目外，使用只读正常导入根及原 38 资源课堂副本。
候选是确切原文选择，经 native list handler 加入清单；原 Child picker没有全链重放。真实 move／confirm／import、单次撤销和重做接通，五新增资源依次为原图／知识／完整题／原图／知识；原资源、环节、目标、位置和 12 图保持，结果为 43 资源／16 图。宽、420px窄窗口按钮与编号可达，辅助阶段没有导入、保存或导出。

导出后教师／静态学生／课堂为 34／30／31 页，实际 Microsoft PowerPoint 为 31 页，新增素材对应实际页 25—31。根模型查看全部 7 张新增实际 Office 页、3 张主 UI、4 张辅助 UI及两张原 PNG；不是重审原 24 页，也不是独立人类或 live 放映验收。完整氨水题及四项原选项保留，答案沿用原教师角色；本轮没有另为新题设计答案动画。
第 30 页保留原栏目装饰 banner，第 26 页有下级标题留在页尾；后续仍需教学排版，不能把本轮顺序功能通过写成 31 页成品课堂设计质量全面 PASS。本轮新 Word 仅导出及恢复核 SHA，未再次转换或逐页视觉检查。

隔离普通 ZIP 为 **58375179 bytes**，SHA-256 `fae9aa727d604e526015f9d64da1202c151520ad87d224ee3e0b880fe13ee743`；包含 1 份 QA 草稿／16 图／1 包 6 登记输出／24 文件、缺失 0。独立全新目录恢复后完整草稿、全部图片及 6 个输出的 SHA／bytes相等；这不是正常 50 份备课的全量备份，原资料库、关系、配置和学生历史不在本轮 ZIP 范围。

正常 SOURCE 1047 文件的 SHA／bytes／mtime及状态保持，50 备课／55 对象、正常 38 资源／24 页课堂及模板不变；原 Word 与教材未改，QA 课例未写入正常目录，保护范围外写入尝试 0。

最终完整 README检查 **14 passed in 2.31s／exit 0**，文档策略 **exit 0**（Teacher scenarios, compact README, chapters, images, history and version boundaries: OK），见 `ROOT/readme-tests.txt` 与 `ROOT/documentation-policy.txt`；本片限定 `git diff --check` exit 0，五文档本地链接无缺失、新旧锚点唯一。

## 视频与资格边界

沿用已核的 V27 第 37 秒、V32 第 39 秒原帧作为筛选、完整图文阅读与选材预览参考；不声称视频展示交错授课顺序，也不声称本轮新增完整播放或音轨审核。
EXE v0.1.101／渲染 v33／提示词 v35保持；微信读书停用，原 59 项／X01—X10、旧课堂及模板增量保留，整体目标继续 active。旧中央 validator exit 1边界保持，本轮不称全局通过；正式资格、人类复核及 live 放映状态不提升。

## 源码与证据入口

- `integrations/deeptutor_shchem_v1/desktop_workbench/handout_material_workspace.py`：编号清单、调序、确认守护与显式排列请求。
- `integrations/deeptutor_shchem_v1/desktop_handout_material_handoff.py`：全项排列校验、按教师顺序构建新增资源及单批交接。
- `ROOT/related-tests.txt`、`frozen-related-files.json`：最终 196 项通过、3 条件跳过与 7 个相关源码／测试的冻结 SHA。
- `ROOT/readonly-final-verification.json`、`candidate-request-new-question.json`、`expected-five-source-keys.json`：原来源保护、严格五项候选与身份；不代替课堂化学或人类视觉资格。
- `ROOT/native-v1/verification.json`、`ui-stage.json`、`native-confirmed-request.json`、`01-*.png` 至 `03-*.png`：真实清单交接、单次撤销重做与正常文件保护。
- `ROOT/native-controls-v1/verification.json`、`01-*.png` 至 `04-*.png`：真实上下移按钮、编号及宽窄窗口；该阶段无导入／保存／导出。
- `ROOT/native-v1/actual-ppt.json`、`actual-new-office-page-25.png` 至 `31.png`：实际 PowerPoint 及限定新增页检查。
- `ROOT/native-v1/backup-plan.json`、`backup-manifest.json`、`restore-receipt.json`、`saved-qa-draft.json`、`normal-before.json`、`normal-after.json`：隔离 ZIP与服务保存、恢复和正常范围保护。
- `ROOT/root-limited-visual-review.json`：根模型检查实际新增页、两阶段 UI及原 PNG的限定范围、图像 SHA和排版后续项。两阶段 `verification.json` 保留机器阶段 `machine_pass_requires_root_limited_visual_review`，不将其改为人类／live PASS。

操作见[讲义选材授课顺序](../teacher/lesson-and-paper.md#mixed-material-teaching-order)；当前推进范围见[闭环状态](../WORKFLOW_STATUS.md#mixed-material-teaching-order)。

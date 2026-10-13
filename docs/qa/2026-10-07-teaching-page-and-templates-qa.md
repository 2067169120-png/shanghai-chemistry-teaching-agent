# 图文讲解页与教师自存模板 QA（隔离机器终核与限定模型复核完成）

2026-10-07。本轮维护源码增加两项显式教学组织操作，未提交发布或更新应用版本。
证据根目录 `ROOT = E:/项目/上海高中化学等级考/outputs/2026-10-07-teaching-page-and-templates/`；UI 阶段与独立 Office／恢复续核分别保留回执。

## 源码与交互范围

- **组成图文讲解页…**：教师确认当前作品中两项不同资源，第一项在左／上，第二项在右／下；编辑导学问题与讲解标题，选择第二项是否点击显示。
- 对话框可按教师完整页、课堂 PPT 完成态、静态学生页逐页预览并放大；长文及完整图片续页，其他素材的原位置、内容和来源身份保持，一次应用可整步撤销。
- **课堂PPT与静态学生页沿用组成页的位置** 默认勾选；已有学生独立排版时明示切换，不勾选保留现有学生排版。点击讲解在静态学生范围省略，原题教师答案角色保持。
- **存为我的教学结构模板…**：勾选环节及是否保存教案框架、目标列表与关联，先读将保存内容的完整预览，再明确保存。
- 模板保留框架、目标、节点类型／时长及标题、教师活动、学生任务、预期产出、评价依据五项教师组织文字；排除材料正文、独立答案字段、图片、原 ID、来源和成品引用。
- 这是字段隔离，不是语义脱敏：自由教师文字按原文保留，其中写入的答案或姓名不会自动清除。
- **我的模板** 可搜索、完整预览、重命名及撤销最近最多 30 步重命名、移到已删除列表及恢复；普通 ZIP 的可选模板集合包含活动与已删除记录。
- `teaching_templates` 为可选字段，旧状态／旧 ZIP 缺少时沿用旧路径；存在时严格验证，损坏模板记录阻止无关保存，保留原数据。
- 应用默认追加环节；只有真正未编辑且没有页面绑定的空环节结构可替换。已有教师框架文字保持，仅按选项补空栏目；应用创建新节点／目标身份、前后版本重核，可整步撤销。

## 当前验证范围

冻结源码最终 13 文件联合回归：`ROOT/final-related-tests.txt` 记录 **164 passed in 53.32s**。开发阶段图文组合后端 23 项／新 UI 8 项、模板最终新用例 21 项／此前相关联合组 71 项分别报告通过；它们与最终组有重叠，不相加。
真实弱电解质隔离 clone 的 v3 UI 阶段完成实际模态预览／应用、组合单次撤销重做、6 环节 80 分钟模板保存、追加为 12 环节后单次撤销回原 6 环节及模板库查看，共 8 张 UI 截图。仅所选两个 placement 变化，原资源、原节点和 12 图保持；整课导出及隔离新稿保存完成。
独立 Office／恢复续核 exit 0：教师 26／静态学生 23／课堂 24 页均无布局错误，实际 Microsoft PowerPoint 24 页，第 16 页完整原分布图、问题标题与讲解同页，保留 5 个 clickEffect。根模型查看全部 8 张 UI 截图（含宽窗、420px 窄窗与模板预览）及实际 PPT 第 16 页；不是本轮重审全部 24 页，不授予人类或 live 验收。
隔离 QA 普通 ZIP 57265582 bytes，SHA-256 `68727dcc6e42dacd409952c6d78f096fba92583cb37983da86c133d548fa479d`；含 1 份 QA 备课／1 模板／12 图／1 包 6 成品／20 文件，缺失 0。项目外全新目录恢复后完整稿、模板、12 图与 6 登记输出的 SHA／bytes 相等；这是隔离 QA ZIP，不是正常 50 备课全量备份。
首轮隔离 profile 误放 DATA 项目内，`ModelProviderSettingsStore` 在窗口打开前拒绝；已中止，保留 `ROOT/native-v1/failure.txt`。v2 的 profile 与恢复目录改用项目外新 AppData 路径；这是隔离核对配置纠正，不计作首轮 UI 通过。
v2 把省略 advanced 空字符串的有效保存稿交给严格恢复副本载入器，在组合／模板窗口打开前结束，保留 `ROOT/native-v2/failure.txt`。v3 按正常打开保存稿的 `.get(key, "")` 转换补空串，使用全新项目外隔离及恢复目录；v1／v2均不计通过。
v3 的回执脚本随后误读不存在的 `DraftReceipt.revision`，中止并保留 `ROOT/native-v3/failure.txt`，不称 v3 脚本完整 exit 0。根代理不重放已核 UI，后续独立 Office／恢复脚本从生产保存服务的 options 读取实际版本；两个阶段分别记证据。
正常工作区前后 1047 文件的 SHA／bytes／mtime 与状态相同，50 备课／55 对象保持，无正常新增课例或外图；隔离课程复用上一轮的 12 图。

## 视频对照与限制

沿用 V28／V30 课堂结构、图文解释和教案／课件联动的抽帧观察作为产品参考。已有视频证据没有展示教师自存模板，本轮模板是产品推导，不声称复现视频原功能或完整观看／音轨核听。
EXE v0.1.101／渲染 v33／提示词 v35保持；旧弱电解质 24 页课堂、原课程与 59 项／X01—X10历史保留，B08按本轮范围推进，整体目标继续 active。
本轮 Word 成品仅生成及恢复核对哈希，没有再转换逐页检视；独立人类复核、live 放映及正式资格提升均 false。旧中央 validator exit 1边界保留，本轮不宣称全局通过或整个目标完成。

## Evidence paths

- `ROOT/final-related-tests.txt`：冻结源码 13 文件最终联合回归 164 项。
- `ROOT/native-v3/ui-stage-evidence.json`、`01-*.png` 至 `08-*.png`、`failure.txt`：实际 UI 操作与部分脚本回执边界，无 UI 重放。
- `ROOT/office-and-restore-run.txt`、`native-v3/verification.json`、`actual-ppt.json`、`actual-paired-office-page.png`：独立续核、实际 PPT 及隔离恢复终态；verification 保留机器阶段的 `machine_pass_requires_root_limited_visual_review`。
- `ROOT/native-v3/root-limited-visual-review.json`：后续根模型限定检查全部 8 张 UI 与实际 PPT 第 16 页，无人类／live验收或新 Word 实际转换。
- `ROOT/native-v3/backup-plan.json`、`backup-manifest.json`、`restore-receipt.json`、`normal-before.json`、`normal-after.json`：隔离 ZIP 保存范围、恢复与正常文件保护。
- `ROOT/native-v1/failure.txt`、`native-v2/failure.txt`：前两次窗口前配置／载入适配问题，不计通过。

## 源码入口

- `integrations/deeptutor_shchem_v1/desktop_composition_pairing.py`、`desktop_workbench/composition_pairing_dialog.py`、`lesson_composition_panel.py`：组合、三范围排版预览与整步历史。
- `integrations/deeptutor_shchem_v1/desktop_teaching_templates.py`、`desktop_workbench/teaching_template_dialogs.py`、`lesson_design_dialog.py`、`studio_preparation.py`、`studio_templates.py`：结构投影、明确保存、搜索与应用。
- `integrations/deeptutor_shchem_v1/desktop_state.py`、`desktop_backup.py`：可选模板集合的校验和普通 ZIP 保存范围。

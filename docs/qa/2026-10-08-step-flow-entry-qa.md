# 分步讲解入口验收（2026-10-08）

素材编排新增 **分步讲解…**：教师填写左上导学问题和2—6个完整步骤，每步有标题及可选说明，可增删、上下调整顺序，选择纵向或横向流程。文字原样保留，预览测量完整内容，溢出时拒绝加入。16:9默认纵向和逐步点击；A4可直接加入完整步骤，点击选项关闭，不自动切换整课画幅。新课堂流程采用10mm页边距、300×160mm外框，说明保持27磅。

取消不改作品；确认通过现有`personal_text`、`add_result`接收，一次撤销／重做完整恢复。手工文字的来源SHA和版本均为`unknown`，已有原图、原题及答案角色保持。预览定时回调前后、确认和接收测量后复核当前设计摘要与完整图片快照，迟到结果不能覆盖已变化的作品或图片。

渲染修订为`shchem.composition-classroom-exposition.v35`：导学问题作为普通文本形状常显，每个完整步骤及其入箭头按顺序各有一次原生`clickEffect`。应用发行版本、既有发布包未更新。

## 完整流程与测试

[worker冻结回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-experiment-method-classroom/flow-worker/verification.json>)绑定8个授权源码／测试文件的SHA、AST与最终JUnit。10模块 **148 passed、0 skipped、3 deselected**，新入口20例包含增删排序、取消、一次撤销重做、420像素按钮可达、迟到设计／图片与测量后复核；旧流程、编辑、来源角色、字体和分页回归通过。各次历史失败及修复记录保留，测试轮次不累加。

三个未选的旧例使用会启动MainWindow的fixture；按本任务不启动普通MainWindow的范围未执行，不能记作通过或跳过：

- `test_native_notes_switch_changes_one_design_step_and_undo_restores`
- `test_native_student_teacher_switch_matches_export_without_history_edit`
- `test_purpose_buttons_are_one_undoable_transaction`

[完整原文测量记录](<E:/项目/上海高中化学等级考/outputs/2026-10-08-experiment-method-classroom/flow-worker/current-ledger-measurement.json>)使用根直接看原图后提供的转写ledger，未进行OCR。b108完整五步横向可放入一页：真实PPTX保留全部标题／说明、正文27磅、五次点击及XML目标顺序，导学问题不在动画组；课堂讲义Word的五个节点格均为完整可编辑文字。b258五步横向亦通过。b291完整三节点、b312完整四节点在横向和纵向均溢出，文字完整保留并拒绝加入；整课可保留原图和全部文字，以完整`notes`讲解页分区或连续组织。上述文件结构核验不代替整课实际Office验收。

## 根实际界面与只读保护

[root native-step-flow-v2回执](<E:/项目/上海高中化学等级考/outputs/2026-10-08-experiment-method-classroom/native-step-flow-v2/verification.json>)使用正常已保存原课的不可变快照（25项资源、14图），通过生产QDialog真实入口执行取消、完整五步加入、一次撤销及重做；原25项和14图完整保留，未保存正常课。根直接看了01完整编辑器、02实际420×900窄窗、04加入后的原课画布共三张截图；03确认图仅机器留存，未直接看，不扩为四张视觉审阅。入口与课堂画布记录见[实际加入后的画布](<E:/项目/上海高中化学等级考/outputs/2026-10-08-experiment-method-classroom/native-step-flow-v2/04-actual-course-after-flow-added.png>)。

v2正常1089文件、只读QA415文件的SHA／字节／mtime前后完整相等，保护写尝试为0。首次[root native-step-flow-v1证据](<E:/项目/上海高中化学等级考/outputs/2026-10-08-experiment-method-classroom/native-step-flow-v1>)中，根只读适配器调用图片store触发对已存在目录的`mkdir`，被audit拒绝；原文件保持，其失败证据保留，不算成功。v2改用原字节读取及`verify_image_bytes`，没有修改产品来规避保护。

## 本QA边界

本页仅验收分步入口与上述有限流程，未声明整课素材编排、实际Office呈现、正常新课采用完成。[本轮构建输出目录](<E:/项目/上海高中化学等级考/outputs/2026-10-08-experiment-method-classroom>)中的整课成果由独立整课QA验收。本入口回合未使用普通MainWindow、Office应用、网络、模型API或微信读书，不自动转写原图，不提升独立真人、真实课堂、化学完整性、正式入库或发布门槛。

[上一轮教材查找与方法单元验收](2026-10-08-personal-textbook-search-and-methods-qa.md)

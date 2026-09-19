# 沪上化学智研台 · 教师使用说明

把手头的教材、题目、学生作答和成绩表，用于备课、出卷、讲评与复练。

**第一次使用请下载 Windows 试用包 v0.1.101。** 本页以实际教学任务说明操作；标注“维护源码”的功能尚未进入该试用包。软件不附带个人题库、教材或学生资料。

[下载试用包](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/releases/tag/v0.1.101) · [第一次使用](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/getting-started.md) · [操作遇到问题](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/save-and-support.md#problems)

<a id="features"></a><a id="flows"></a><a id="home"></a>
## 今天要完成什么？

| 教学场景 | 从哪里进入 | 完成后得到什么 |
| --- | --- | --- |
| 明天要讲一节新课 | [备课 → 教学环节](#preparation) | 可继续修改的教案、PPT 和学生学习单 |
| 给班级出一份练习卷 | [题库 → 组卷](#paper) | 学生版与教师版 DOCX、PDF |
| 复核一份作答或一批作业 | [学生分析 → 同屏批改／作业批次](#student) | 已记录的教师评分、诊断及实际作答情况 |
| 月考后决定先讲什么 | [学生分析 → 考试分析（Excel）](#exam) | 本地统计图表、待核对问题及离线分析报告 |
| 给几名学生安排复练 | [考试分析 → 复练与复测](#closure) | 有明确对象与目标的任务、练习卷及实际复测记录 |
| 下次接着备课或换电脑 | [我的备课 → 保存与备份](#works) | 可重开的草稿、独立成品副本和所选范围的备份 |

这些任务不必从头依次完成。有现成题目就从选题开始，有成绩表就从考试分析开始。按 **Ctrl+K** 搜索功能，按 **F1** 查看帮助。

<a id="install"></a><a id="import"></a><a id="settings"></a>
## 第一次打开，先做这三件事

**完整解压再启动。** 解压 Windows 试用包，双击 `沪上化学智研台.exe`；不要在压缩包内运行，也不要只复制 EXE 或删除旁边的 `_internal`。核对下载来源与校验文件，不要关闭 Windows 防护。

**准备自己已有的材料。** 初装题库为空是正常现象。先导入一份有权使用的 Word 讲义，核对完整题目与答案；原文件请单独保管。不要为了升级重复导入全部资料。

**先走一条不需要 AI 的流程。** 查找已有题目、本地成绩统计、手动教学环节编辑和课堂工具不需要 API。生成 AI 初稿或建议才需配置模型并确认发送；实际试卷分页需本机 Office 转换工具。详见[安装、资料和模型设置](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/getting-started.md)。

<a id="preparation"></a><a id="lesson-design"></a><a id="templates"></a>
## 场景一：明天要讲一节新课

**准备：** 课题、授课对象、课时、可检查的学习目标，以及教材节选或完整例题。

进入 **备课 → 教学环节**。按课堂进程安排提问、讲解、例题、学生练习和反馈；逐项写清“学生做什么、留下什么结果、教师怎样核对”。已有初稿可通过“从已有初稿加入…”预览并勾选，不必重新抄一遍。

保存草稿后，生成 **教案／PPT／学习单**，打开实际文件核对公式、图表、答案与分页。修改环节后再生成新版本，不把旧成品当作已同步。

**完成标志：** 草稿能重开，三类成品都能打开，学生学习单中没有教师答案。PPT 的讲者备注可能含答案，不宜直接发给学生。

[按步骤备课与检查成品](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/lesson-and-paper.md#lesson)

<a id="library"></a><a id="paper"></a>
## 场景二：给班级出一份练习卷

**准备：** 已导入并核对的题目、这次练习的目标、预计用时和分值安排。

在 **题库** 按来源、教材章节或知识点找题，阅读完整题面与答案后加入选题篮；进入 **组卷** 调整题序与配分，再查看学生版、教师版的实际分页。主题公共材料不要为了少印一页而拆掉。

**完成标志：** 两版题号对应，公共材料、图像和作答区域完整，学生版没有答案，导出的 DOCX 和 PDF 都能打开。扫描图内部的旧题号及跨题引用仍需逐项核对。

[选题、编号与打印前核对](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/lesson-and-paper.md#paper)

<a id="student"></a>
## 场景三：复核一份作答或一批作业

**准备：** 学生原作答、对应原题、参考答案与真实满分；不要只拿总分推断错因。

在 **学生分析** 打开已有分析，再进入“打开作答与评分（同屏批改）”。先看原页，再填写分数与理由，点击“记录本题评分”。处理同一作业的多名学生时，用 **作业批次** 选择已有作答，逐人核对。

**完成标志：** 正式评分已记录，未完成输入已暂存，缺页、未作答等实际情况单独记明。AI 建议和暂存输入都不等于教师正式评分。

[单份复核、批次暂存与正式评分](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/assessment.md#grading)

<a id="exam"></a>
## 场景四：月考后决定先讲什么

**准备：** 同一场考试的普通 `.xlsx` 成绩表，以及可选的原试卷。只有总分也能分析，但不能据此定位逐题失分。

进入 **学生分析 → 考试分析（Excel）**，导入后核对工作表、表头、学生列、题号和真实满分。先处理“数据核对”中的缺失或冲突，再看班级分布、低得分率题目和学生明细。需要讲评草稿时，再添加试卷并明确确认发送给模型。

**完成标志：** 统计范围与有效人数正确，结论能回到原题和原作答核实；保存分析并导出离线报告。分数只能帮助确定复核顺序，不能直接证明粗心、态度差或已经掌握。

[整理成绩表、读图表与形成讲评安排](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/assessment.md#exam)

<a id="closure"></a>
## 场景五：给几名学生安排复练，并记录实际结果

从 **考试分析 → 复练与复测** 选择题目与学生，写出一个可检查的目标和计划日期，到真实题库选择完整题目，再回任务核对并输出练习卷。做完后点“记录实际复测”，填写实际日期、实际满分与得分；未作答不要记成零分。

**维护源码补充：** 已保存题集可独立整理，移出全局题篮后仍保留引用，但原题文件必须在。成绩更正后，先查看“核对改分影响”，确认安排仍适合，再重新预览与导出；旧 AI 建议不会因此自动恢复有效。

**完成标志：** 能重开任务并找到实际复测记录，而不是只生成了一份练习卷。不同卷的分数不要直接相减当成提分效果。

[复练、改分后重核与版本区别](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/assessment.md#followup)

<a id="classroom"></a>
## 课堂上：计时、分组与记录反馈

在 **课堂工具** 使用倒计时、点名、随机分组或随堂反馈。课堂反馈可追加回备课材料，追加后记得保存。动态平衡演示是简化模型，不是实测数据。

[课中使用与课后整理](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/lesson-and-paper.md#classroom)

<a id="works"></a>
## 场景六：下次接着做，或把成果交给同事

**接着编辑：** 保存草稿，下次从“我的备课”按课题或作品名称打开。恢复副本用于找回最近成功保存的编辑，不替代正式草稿和原图备份。

**只交成品：** 导出需要的版本，教师稿与学生稿分别发放。直接在 Office 中修改导出副本，不会自动改回工作台中的教学设计。

**准备迁移：** 在备份窗口检查所选范围和缺失清单。备课 ZIP 不是整机备份，**不包含学生作答／批次／批改暂存，也不包含原 Word／公众号完整题库**；这些原资料须另外妥善保存。先在独立目录恢复核对，不覆盖唯一原件。

[保存、交付、备份与恢复清单](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/save-and-support.md#save)

<a id="faq"></a>
## 卡住时，先看这里

“没有题目”先查实际资料与筛选；“无法分页”先查本机 Office；“建议已过期”先核对发生变化的成绩或材料。不要通过清空资料或反复调用模型来排错。

[按现象排查问题](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/save-and-support.md#problems) · [哪些材料会发送给模型](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/teacher/getting-started.md#privacy)

<a id="verification"></a><a id="workflow-status"></a><a id="visual-composition"></a><a id="documentation"></a>
## 版本与维护信息

当前试用包不包含后来维护源码的全部按钮；Excel 对比重导和自由拖拽画布仍不能按已发布功能使用。帮助中心改版也需使用包含该改动的源码。

**main 仅同步本说明，应用源码仍须使用明确的维护分支或版本标签。** 教师不需要阅读提交号或测试日志来完成以上操作。开发运行方式、功能适用版本、未完成项与验收证据统一放在[维护者入口](https://github.com/2067169120-png/shanghai-chemistry-teaching-agent/blob/a63bfdc72a4d428220f33d5c106d246ffa7d6840/docs/maintainer/README.md)。

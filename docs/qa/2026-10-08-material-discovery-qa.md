# 教材选材清单与已有配图检索（维护源码，未发布）

2026-10-08，e6ae源码。补齐旧课程配图查找、可见缩略图与固定教材跨章节单项管理，不新增教材、题目、备课或Office输出。私人证据目录：`IMAGE = E:/项目/上海高中化学等级考/outputs/2026-10-08-image-selection-gallery/`；`QUEUE = E:/项目/上海高中化学等级考/outputs/2026-10-08-material-selection-queue/`。

## 操作与来源契约

**从已有备课选图片…** 按作品名或原课题检索当前架，每页25份，读取当页payload的准确图片数；零图课程可查看，不能确认选图。旧最近50兼容契约保持；没有search接口的只读客户端沿用旧入口。归档和回收站未纳入当前架搜索，不宣称覆盖所有隐藏作品。

新 `saved_preparation_image_catalog()` 核来源版本与角色，只封存来源元数据、范围与完整payload摘要，不读取像素；旧 `saved_preparation_images()` 全图校验接口保持。列表只在可见项后台逐图读取96×64缩略，缓存限2MiB／64项；成功像素按SHA共享，失败按来源identity记录，保留同图不同课程的全部绑定。完整预览校验原字节，明确导入再核版本、范围和字节；搜索、关闭和迟到回调不产生交接。仅教师图片与完整题入口规则保持，没有凭文件名增加来源分类。

**选材清单…** 显示固定教材跨章知识、研读提示和图表的稳定身份及已知位置。单项移除不重建章节，保留原reader、页、缩放、当前行和备课目标；知识／研读变化使文字预览失效，图表变化只作废本批图表。定位明确切到真实书／节／条目并清除旧节reader，不把旧B页显示为A节。个人教材仍用原笔记与待选图表入口，本轮未实现个人书正文搜索。

批量图表核对load、selection、target三个epoch，拒绝移除后重新勾选同ID的迟到结果。补齐相邻单张裁图的close、loading、busy、书／原项版本与接收目标检查；无关勾选变化不取消合法单张结果。源快照、既有已入课内容和正式门槛不改。

## 最终检查与真实Qt

| 最终范围 | 结果 | 回执 |
| --- | --- | --- |
| 图片新后端／UI及既有三模块 | 80通过，0失败／错误／跳过，82.10s | `E:/项目/上海高中化学等级考/outputs/2026-10-08-experiment-design-materials/image-picker-worker-final-checks-v4.json` |
| 新教材清单及单张裁图守护 | 30通过，8.34s | `QUEUE/selection-list-ui-final.xml` |
| 四模块教材回归 | 75通过，32.54s | `QUEUE/selection-list-related-final.xml` |

上述分组不累计旧跑次。根核对JUnit数目、SHA及12个当前源码／测试文件AST；5个产品文件与两份原生回执匹配。图片早期布局／测试断言失败、missing路径异常类型预期错误及不合法handout角色fixture保留在原日志；完成布局修复与相应fixture修正，v3至v4仅修不合法角色fixture，不绕过原角色约束。教材默认解释器缺PySide6、native-v1路径分隔符比较错误分别保留；改用既有环境及规范化路径，未安装依赖或放宽保护。

`IMAGE/native-v1/verification.json`：使用正常52课不可变快照、生产草稿读取服务和真实原图的独立Qt窗口。第一页只读25份元数据、像素读取0；三页到达52份，第51／52两份旧课各8图可见，标题搜索准确定位最旧课程。实际saved_button打开新选课窗口后暂存旧8图选择，取消不交接；实验课14图可见缩略，洗气瓶问题／用途跨筛选保持，420px确认可达。7机器截图，根直接看02／04／05／06／07共5图，见`root-image-picker-limited-visual-review.json`。不称普通主窗口重开、保存或导入验收。

`QUEUE/native-v2/verification.json`：stateless真实固定教材目录及生产原页服务的独立QWidget，无MainWindow、state store或恢复。必修一A=1.1物质分类、B=1.2物质的量，共四项三类；移除A知识后B知识、A研读和A图表仍在，B PDF21及缩放／目标保持，旧文字确认禁用。定位A图表后选中正确条目，读取PDF12／印刷7完整原页。6机器截图，根直接看02／03／04／06共4图，见`root-selection-limited-visual-review.json`。真实清单宽720px、窄宿主清单660px；360／420／1100px为合成控件检查，不把660px截图称420px实际阅读。无交接、保存、导出或profile锁，隔离profile空，Qt及任务已退出。

两个原生回合正常1089文件SHA／字节／mtime前后相等，仍52备课／57对象；QUEUE读取的66份DATA原件前后相等、保护写尝试0。既有实验课25页PPT、18页教案、10页课堂讲义及6成品保持，本轮不重复Office或备份恢复验收，沿用[课例原回执](2026-10-08-experiment-design-materials-qa.md)。

## 视频依据与边界

根直接看V27原36.82秒帧的搜索、候选缩略和已选区，以及V32原39.06秒帧的逐项候选池；两原视频SHA与44项清单相等，见`IMAGE/video-reference.json`。这是本次搜索、视觉识图和清单管理的设计依据，不称播放全部44视频或验证参考视频的交互实现。

源码启动器仍指e6ae与原正常profile，重开后可用；EXE v0.1.101、渲染v34／提示词v35未改。未提交／发布，微信读书停用。模型只限定看上述9张当前界面，不替代独立真人、live课堂、全教材覆盖或正式资料资格；原59项／X01—X10与整体goal继续。

[教师操作](../teacher/lesson-and-paper.md#material-discovery) · [当前状态](../WORKFLOW_STATUS.md#material-discovery)。

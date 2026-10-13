# 教材研读目录扩容、Word原栏目与弱电解质图文选材QA

2026-10-07，本轮维护当前源码并保存本地候选素材草稿，未发布。教材研读目录增加整库预算与完整性诊断，Word选材目录采用独立显示投影；在同一份新增弱电解质素材草稿中保留7张知识卡、4幅教材原内容切片和2幅课外实景。原有51素材、37页、两课时课程保持，未新导出Office。这些结果不代表旧笔记迁移完成、教师人工审阅完成、正式使用资格提升或总目标完成。

## 教材研读目录与旧记录

读取预算为256个文件、8192条原始记录和32 MiB总读取量；单文件512 KiB、正文4000字符、单记录64页限制保持。重复、无效及不匹配记录先计入读取预算，不能过滤后再计算。全局容量越界时整库候选清空并说明原因；未完整扫描的计数是已观察下界，`scan_complete`、`counts_complete`均为false。原PDF的已核身份、原页阅读、笔记revision及目录revision不由诊断字段替代；书架一次读取笔记包，既有加载接口与笔记投影形状保持。

真实本地盘点为60个研读文件、506条原始记录、499个唯一ID，书架可选467条；5册、60正式节、386知识条目、322图锚保持。实库诊断为`scan_complete=true`、`capacity_exceeded=null`，但两个旧包仍有读取失败、`counts_complete=false`，不将可选467条表述为全部记录已完整通过。旧467条归一化笔记及revision保持，见[真实目录摘要](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/crop-material-draft/root-real-catalog.json)。旧32条未进入书架的唯一ID仍待迁移：PKG-014、016、024、028、032、038合计31条旧核心记录，另有PKG-044的1条直接来源孤立记录。六个旧核心包缺原记录中的`source_path`；包manifest可定位Word及SHA不能补称旧记录原本具有这些字段。SEP-07的记录册别与文件级教材来源不一致，仍保留原值待核。本轮只盘点，不改旧研读包、不提升来源或审阅资格。[旧32条盘点、受保护输入与限制](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/legacy-inventory/legacy-inventory-summary.json)。

最终源码在新进程中通过83项定向测试和68项相关测试，合计151项不同测试，失败、错误、跳过均为0。269项扩大消费端回归发生在最后一处读取期间文件增长预算保护之前，保留为较早版本证据；初轮82项已由83项取代，不能把重复组相加或称269项覆盖最终微调。各组模块、时间、文件SHA与顺序见[目录测试最终报告](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/catalog-tests/result-summary.json)。

## Word原栏目与完整原文

显示函数优先保留已有原标题；有可定位真实图片且marker数量匹配时，图片标题显示为“原图栏目 · 区块N”，原栏目另列区块范围和原图数量。同名栏目仍以来源绑定、`word-heading-b{start}`和原区块定位，不能以标题作为身份。完整H1—H9导航独立传入阅读器，不改原preview的H1/H2目录、正文、来源metadata、source revision、outline revision或导入section snapshot。标签清理仅作用于可确认的图片marker；缺失资产、未知对象、歧义asset ID及字面marker保留。图数表示可定位且ID不歧义的原图asset数量，不冒称XML出现次数；学生正文不插入目录计数、来源SHA或审校说明。

Word显示改动阶段的10组282项全部通过，覆盖同名定位、H3/H9导航、源metadata保持、长文、搜索、HTML转义、原图动作、表格、Word选题和来源校验。真实PKG056仍为298区块、134图，原Word SHA为`8cda068028bafa475614cd124effaf56caea133084813b9609926af8e325bfb2`，提取revision为`465eee61e5e2c5d94a478c24b857e17eb64f9d33e3725d75aadea3eff8f20556`。Word native-v3在当时1024个正常配置文件基线上保持hash、mtime及QA状态，0写入尝试、无选材保存；该1024数是加入两张实景之前的历史基线。截图使用Microsoft YaHei UI，抽样字形缺字数为0。[Word显示阶段最终测试、来源与SHA报告](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/source-title-tests/verification-report.json)；[真实知识点2标签](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/source-title-tests/native-v3/02-pkg056-readable-knowledge2-1280.png)；[原栏目选材与问题标题](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/source-title-tests/native-v3/05-pkg056-knowledge-picker-1280.png)。

## 同一份图文素材草稿

新增草稿“弱电解质电离平衡：教材原图与问题导学素材”，ID为`prep-48bba6a127aa476495903947b4962480`。四幅教材原内容切片来自选择性必修1第三章3.2，PDF70—72／印刷64—66，分别用于速率与浓度、电离常数定义、同温度酸强弱比较、电离度与浓度的引导问题。保留原教材SHA、页码、裁切边界、渲染及裁片revision和图像SHA；没有替换原教材或重画原图。四张切片先与7张知识卡保存到这一份新草稿，后续两张实景追加到同一草稿，最终7知识卡、13素材帧、6图；13素材帧不是正式课堂分页完成或Office页数。[切片保存及只读补验](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/crop-material-draft/verification.json)；[同一草稿加入实景后的最终读回](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/context-photo-integration/verification.json)。

两份JPEG已经主代理查看实际像素；此处“主代理视觉核对”不等于`human_reviewed=true`。照片及原来源记录保持，未将课外实景伪装为教材绑定。

| 实景 | 引导问题及图像边界 | 来源记录 |
|---|---|---|
| pH色卡包装 | “pH色卡能单独判断酸的强弱吗？”图中是两盒色卡包装，未显示使用后的试纸或待测溶液；有NICE字样及时间水印，不能据图推出溶液测量结果。 | SAHAYA RAJAN S；[Commons原页](https://commons.wikimedia.org/wiki/File:Ph_test_strips.jpg)登记CC0 1.0。文件上传于2026-01-26、拍摄于2024-11-11，分别保留。 |
| USGS测量现场 | “用仪器测酸碱性时，还要记录哪些条件？”探头、电缆、仪器可见，仪表反光导致读数不可辨；有YSI字样，不推断pH值或水体颜色成因。 | Rob Runkel／USGS；[原媒体页](https://www.usgs.gov/media/images/measuring-ph)发布日期2015-08-13，登记Public Domain。 |

来源页面、原图URL、作者、许可、采集时间、原图SHA及上述视觉边界保存在[实景来源记录](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/context-photo-integration/provenance.json)中；这些后台metadata不进入学生正文。原图SHA分别为`f0e1fc202307ac433f194d243fb82c7556ee7166116fd0fd575e06f9e2e8a44d`与`283b8d898cf26f83e9adf4f16f804d699d3bed776dab4681bcedd690bb8ced85`。新归档保留原catalog字节前缀并追加2行。实景集成后的SOURCE为1026文件，49份备课草稿（preparation）、54个全部草稿／状态对象；仅新增2个图像文件并更新新草稿状态，其余53个对象（含48份其他备课草稿）、原51素材课程及2条显式relations保持。`human_reviewed=false`，正式权限未提升。

## 验证边界、备份与发行

切片首次安装已成功保存，但尾部验证错误要求`updated_at`、新草稿revision等合法保存字段全等，导致验证断言失败；只读补验又曾把安装自有的临时锁纳入持久文件基线。已释放该锁并以只读方式证明旧稿与旧revision保持，没有重新安装或创建重复草稿。原生recent-crops初次检查将对已存在目录的`mkdir(exist_ok=True)`判为写入；后续只允许这种无害存在检查，仍拒绝实际创建、文件写入和删除。Word native-v2在主代理另行授权保存新草稿期间主动中断，不计最终通过；最后以新基线完成native-v3。失败及中断记录保留，不能计为通过。

加入实景后的recent-crops-native-v3已从真实“最近保存课程”打开同一新草稿，6个问题图题和6幅完整原图放大通过；1026文件的SHA、mtime和state一致，0实际写入尝试。主代理已查看新课程入口及两张实景选用界面，见[6图阶段原生回执](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/recent-crops-native-v3/verification.json)。较早recent-crops-native-v2只验证当时1024文件、4图阶段。

随后修复原图列表中的长课程名前缀遮住图题：首行显示教师的问题图题，长来源课程留在tooltip，列表换行并关闭横向滚动；搜索包含已编辑的标题和用途，不改变图片及来源身份。该显示微调通过11项定向及55项保存／阅读器相关测试，共66项、无失败；这些分组与前述回归分别报告，不混算为全局唯一测试数量。日志见[图片列表定向测试](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/image-picker-tests/targeted.log)和[相关测试](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/image-picker-tests/related.log)。最终recent-crops-native-v4已验证6个不同问题图题位于首行、无横向滚动、编辑后的问题搜索有效，SOURCE1026文件的字节、mtime及state保持；主代理已查看两张实景选用界面和编辑搜索界面。[最终图片列表原生回执](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/recent-crops-native-v4/verification.json)。

![最近保存课程中的pH测量问题与原图](screenshots/2026-10-07-reading-catalog-materials/07-ph-measurement-question.png)

普通ZIP备份material-backup-v2已通过：49份备课草稿、100张引用图、288个既有成品文件，共390文件、587813327字节（未压缩）；ZIP本体573236404字节，SHA为`b927dd21ace6caa1267f98451433ef6f61c52cfd59d04cc7ec9d6f55854ef7fa`。隔离恢复后新草稿payload相同、6图SHA逐一相等，正常1026文件保持；原始资料库与credentials不包含在普通备份中，原图归档及完整来源资料仍在DATA工作区。首轮备份脚本误将StateStore文件路径传作目录，在备份前只读阶段失败，正常资料没有写入，失败记录保留。[备份与隔离恢复回执](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/material-backup-v2/verification.json)。

新增草稿的首轮真实素材画布检查中，两张知识卡因`⇌`（U+21CC）紧邻`H⁺`时触发Qt字体fallback缺字而中止，只有其余已测素材未报告溢出，不能把首轮称为13帧完整布局通过。首轮独立透明控件截图背景呈黑；最终v2改用实际QDialog并点击专注页面。原公式与state未改，此前带空格的化学字形样本未覆盖完整段落情形；缺字保护提示继续保留，仅补本地字体families fallback。

最终本机实际使用Microsoft YaHei UI＋Segoe UI Symbol；未列入当前不可用的Cambria字体。完整连续化学段落在11和12点字体下缺字数为0，17项定向与37项相关测试共54项通过。最终`typography.py` SHA为`d32a27528f8c51eefa97531db5838c8e29f8a99f8369b89ca6d68c8b3ccb3e2a`，见[字体组报告与完整段落证明](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/typography-tests/result-summary.json)。最终字体代码后另跑Word reader 36项及两类picker共11项，合计47项通过；这47项与之前282／66项分别记录，不把重复执行当新增全组覆盖。[最终字体后的阅读与选材回归](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/typography-tests/final-reading-and-picker.xml)。

最终material-canvas-native-v2的13个素材完整渲染，`layout_errors=[]`，SOURCE1026文件SHA、mtime及state保持，原公式text相同。主代理已查看原教材曲线、电离常数、分步常数和USGS实景的实际画布：问题标题在左上、公式箭头正常、正文完整，正文没有来源提醒。[13素材最终布局与原图SHA回执](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/material-canvas-native-v2/verification.json)。这一验证覆盖本机实际素材画布，不代表Word／PPT导出版面、所有字形或完整课堂交付；未生成新的Office成品，`human_reviewed=false`保持。

本轮未重新构建或发布EXE。当前`desktop_resources.json`仍仅列7份早期研读文件；本地60份不等于发行数据覆盖，也不能据此声称本轮目录预算或Word显示源码已进入发行。原51素材、37页、两课时课程保持；未新导出Word、PPT或PDF，尚未完成教师化学审阅、Office实际版面及完整课堂交付。旧32条迁移、两个旧包失败和后续图文课程编排仍待处理，候选状态与正式权限门槛不变，总目标继续进行。

最终收尾回执已通过：1026个正常配置文件与最终画布基线逐一SHA、size及mtime相等，旧53个对象及revision保持；受保护的79份原始笔记、manifest、库存、教材和Word文件SHA、size及100 ns精度mtime相等，另核PKG056原Word SHA，原catalog前缀及照片归档原JPEG字节保持。13素材均完成布局测量，主代理逐图查看范围为前述4页，不表述为13页全部另行像素审阅。[最终汇总与全文件SHA保护](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/root-final-acceptance.json)。

文档策略、完整README14项及范围内`git diff --check`通过；CRLF提示不作测试失败。[文档与最终阅读测试证明](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/final-document-and-test-proof.json)；[完整README最终测试](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability/final-complete-readme.xml)。各阶段的原始失败及通过回执保持。完整本地证据目录：[2026-10-07-reading-catalog-scalability](E:/项目/上海高中化学等级考/outputs/2026-10-07-reading-catalog-scalability)。本记录只新增这一份QA文档，没有复制原资料或截图，也没有另存整本README。

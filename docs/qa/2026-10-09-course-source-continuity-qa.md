# 2026-10-09 教材裁片凭据备份与原书位置确认

<a id="course-source-continuity"></a>

范围：维护源码的教材素材持续使用链。课堂图文内容与既有Office成品保持，本轮不增加素材或重导Office。

备课备份现在按实际所选草稿、恢复稿及保留设计的已结束任务payload中的封存Original，匹配唯一有效裁片回执。回执校验来源身份、版本、双页码、图SHA与封存摘要；恢复时再次核对。无图选项仍可保留回执，图片是否打包仍由图片选项决定。旧包或旧课缺回执时列出缺口；不能从相同图片反推来源，也不接受两个冲突回执。原PDF和本机位置记录不在备课ZIP中。

当前资料库已定位到相同字节、同版本原教材且物理路径改变时，课件原页入口显示旧位置和新位置，由教师点“关联并打开原页”。后台先完整核验原书、原页raster与裁片RGBA，发布前再核来源、输入和位置版本；界面同时核当前素材、图片表单、插入锚点与关闭状态。取消、待检查时关闭和父窗口关闭均不发布。确认只增加本机位置记录，不改封存Original、课堂工作文字、原PDF、激活目录或任何资料资格；关联后仍由实际reader重新打开原页。

验证按范围分组，重跑次数不累计为不同用例：

| 范围 | 当前证据 | 结果 |
| --- | --- | --- |
| 备份闭包与恶意重封印字段 | `backup-worker/test-run-4/junit.xml`，实际路径以`backup-implementation-receipt-v2.json`为准 | 54通过 |
| 根source location与安全旧reader回归 | `root-source-tests-directed-final/junit.xml` | 31通过，2个旧构造夹具未运行 |
| 最小Qt dialog／Panel时序、关闭与scope | `relocation-exploration/ui-implementation-receipt.json`及最终日志 | 44通过 |
| 独立真实个人书reader／locator | `backup-boundary-review/tests/reader-first.xml`与`publisher-final.xml` | 16＋2通过 |
| 独立真实producer备份／恢复与恶意ZIP | `backup-boundary-review/tests/backup-final.xml` | 12通过 |

独立审查发现的P2：personal source ID／volume约束和printed page布尔值未被备份完整性判定拒绝。保留三份攻击ZIP后窄修typed source身份与页码域；当前源码拒绝三份原攻击包，独立12项重新通过。首轮独立夹具“1课时”格式错误、作者备份早期失败和根误选旧构造夹具的中止范围均留在本地，不作为最终成功证据。根中止的旧测试仅使用OWN私有tmp；最终31项和实际单课验证使用只读／内存状态，不运行完整Facade、MainWindow或Recovery构造。

真实课验证以此前交付的45页问题导学图文课为来源，只拷所选一课：95资源、98图片、25回执与6成品，不带其它56备课。先从候选实际读取25原页，完成全原书SHA、整页raster与裁片像素检查。根再调用真实备份create／inspect／restore，在新目录恢复130个打包文件；ZIP CRC、文件SHA和字节一致，95资源的课堂工作内容及Original、98图片、25回执、6成品保持，恢复后25原页再次实际逐像素通过。

真实M2搬迁只逐条复制实际读取闭包：65份必要元数据与唯一M2原PDF，全部保原字节，不递归复制业务库或其它原书。实际最小Qt使用正式Fusion字体、真实后台任务、Panel槽、新dialog与QPdf reader，取消、检查中关闭、父关闭均无新增；确认后仅新增一个本机位置记录，并真正打开PDF90／印刷86。根直接查看确认窗与原页窗两张截图，中文可读、白底完整页可见，仅为模型截图观察。第三张独立QPdf PNG含alpha，保留作原始诊断，不作为白底可读展示或教师现场验收。

Normal1523文件／56备课／61业务对象、QA415文件、六原件与此前交付读取文件的SHA／字节／mtime保持。实际45页PPT、54页Word教案、24页Word讲义及9个点击参数沿用既有成品，未在本轮实播动画。renderer38、prompt35与旧EXE0.1.101保持，未提交发布、升级资料资格或改写95-Source。

本地根证据：`E:/项目/上海高中化学等级考/outputs/2026-10-09-course-source-continuity/root-final-audit.json`；实际单课包在同根`course-archive`，新恢复目录为`course-archive/restored-profile`。来源PDF仍从原DATA资料库读取。完整业务来源恢复、固定60来源逐项任务、44视频完整范围、原59／X01—X10及整体目标继续active，本轮只完成这条局部使用链。

[教师操作](../teacher/lesson-and-paper.md#course-source-continuity) · [保存范围](../teacher/save-and-support.md#save) · [状态](../WORKFLOW_STATUS.md)

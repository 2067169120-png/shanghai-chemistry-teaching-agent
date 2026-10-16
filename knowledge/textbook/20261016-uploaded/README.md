# 上海高中化学教材：2026-10-16 上传批次

本目录对应用户提供的五册上海科学技术出版社教材。支持按知识点找章节、把印刷页码换成 PDF 页码、读取原页和裁取指定图文。分支为 `data/shanghai-textbooks-20261016`，未合并 main，不修改应用版本或 Release。

## 当前交付状态

已入分支：五册原件校验清单、60节目录索引、18处教材节选坐标、离线调用工具及其12项测试。五册 PDF 原件及 PNG 裁片尚未成功写入 GitHub；不能仅凭索引存在就显示为教材已安装。

配套资源包 `上海高中化学教材_原件与调用索引_20261016.zip` 包含五册完整原件、18张教材裁片、索引、脚本、目录登记与本地验证记录。资源包在对话附件交付，不是本仓库 Release 附件。包内文件与本次上传原件逐册进行 SHA-256 核验。

本次配套的奉贤错题讲义为14页、一题一页，嵌入18处教材原页节选；讲义保留学生作答，因此不提交到此公开分支。

## 五册原件与页码

- `bx1`：必修第一册，原 PDF 实际154页。
- `bx2`：必修第二册，124页。
- `xb1`：选择性必修1《化学反应原理》，130页。
- `xb2`：选择性必修2《物质结构与性质》，106页。
- `xb3`：选择性必修3《有机化学基础》，134页。

共648个原 PDF 页面。原件不重排、不压缩、不覆盖其他版本。原件的逐册路径、字节数及 SHA-256 见 [manifest.json](manifest.json)。其中“现行版”沿用用户文件名，不代替出版版次。

正文中，选择性必修1的 PDF 页码为印刷页码加7；其余四册正文为加5。封面、目录等直接按文件页码定位。所有 `pdf_page`、`pdf_start` 与 `pdf_end` 均从1开始。

## 直接调用

以下命令在仓库根目录执行。工具需要 Python 3.10或更高版本；检索和校验仅用标准库，渲染另需 PyMuPDF。

```bash
# 检查本地五册原件是否齐全、是否与本次上传一致
python knowledge/textbook/20261016-uploaded/textbook_tools.py verify

# 按知识点查目录与节选；不会将目录检索冒充全文OCR
python knowledge/textbook/20261016-uploaded/textbook_tools.py search "溶度积"

# 取选择性必修1印刷第75页，也就是PDF第82页
python knowledge/textbook/20261016-uploaded/textbook_tools.py page --book xb1 --printed 75 --output textbook-page75.png

# 按已核对的节选编号裁图
python knowledge/textbook/20261016-uploaded/textbook_tools.py crop --id q37-ksp --output textbook-ksp.png

# 运行测试
python knowledge/textbook/20261016-uploaded/test_textbook_tools.py -v
```

输出图片路径必须是新文件，不覆盖已有图片。裁图前同时校验原件哈希、页数、页码与坐标。原图中文字和公式不重新识别或重写。

## 安装资源包中的原件

将资源包解压到单独目录，然后在仓库根目录执行以下命令；把示例目录替换为实际解压目录。

```bash
python knowledge/textbook/20261016-uploaded/textbook_tools.py install --source-dir "/path/to/unpacked-resource-pack"
python knowledge/textbook/20261016-uploaded/textbook_tools.py verify
```

`install` 在复制前核验全部五个来源，按清单放到 `sh-chem-db/01_官方考纲教材/上海高中化学_现行版/`，并向 `sh-chem-db/catalog.csv` 追加本批登记。同一路径已有不同原件时停止，不覆盖旧文件；重复安装不重复登记同一哈希。

本地安装与 GitHub 提交是两件事。剩余交付项是把这五个 PDF 原件按清单路径提交到本分支，并读回核对；当前仍为 `pending_upload`。本工具不会自行执行 Git 命令、发起网络上传、调用模型或修改桌面工作台数据库。

## 来源和验证

目录标题与页码来自五册原书目录；关键词是配套检索标签。18处节选逐张核对对应教材页和裁切范围；第15题所用图2.3的裁框只保留成键示意，避免带入相邻栏目的装饰。

本地通过12项工具测试，并实际执行原件安装、SHA-256核验、溶度积检索、整页渲染和指定节选裁图。测试脚本与本分支文件的 Git blob 哈希一致。具体记录见 [verification.json](verification.json)。这不代表全项目CI或桌面界面集成已验收。

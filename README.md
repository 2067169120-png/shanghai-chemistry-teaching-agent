# 沪上化学智研台

面向上海高中化学教师的 Windows 本地工作台，支持题库浏览、题篮组卷、教师版与学生版导出、教材与讲义关联、课堂课件编排及本地修订。

本仓库用于软件源代码和测试的版本管理。教材、讲义、公众号原图、试卷、学生资料、个人设置、模型密钥、构建包及过程截图保留在本机，不随代码上传。

## 当前范围

- 原生桌面界面位于 `integrations/deeptutor_shchem_v1/desktop_workbench`，不依赖网页套壳运行。
- 题库读取保留原卷主题、小题与作答单元的对应关系，以及原图和来源绑定。
- 来源参考答案与 AI 补充解答分别存储、标注。补充答案不会改写原归档的“未附答案”状态，也不冒充官方评分标准。
- 课堂备课支持两课时编排、知识页与例题页插入、教师提示及学习单。具体课堂效果仍需教师使用后反馈。

## 本地开发

使用 Windows 和 Python 3.12，建立独立环境后安装：

```powershell
python -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r runtime/deeptutor_shchem/desktop_requirements.txt
.\.venv-desktop\Scripts\python.exe -m pip install pytest ruff
```

本软件当前读取已有的本地资料库。开发机需要在工作区配置 `sh-chem-db`，或通过 `SHCHEM_WORKSPACE_ROOT` 指向同时包含该目录和 `integrations/deeptutor_shchem_v1` 的本地工作区。空仓库克隆不包含教学资料，也不等于已经具备完整可运行题库。不要用虚构的题库或来源元数据填补缺失资料。

```powershell
.\.venv-desktop\Scripts\python.exe runtime/deeptutor_shchem/desktop_teacher_workbench.pyw
```

题库读取、正式生成等部分能力还依赖本机安装的项目控制器及其受控资料。需要外部模型的功能由用户在软件中单独配置；请勿将密钥写入代码或提交历史。

## 检查与构建

可独立运行的纯逻辑测试示例：

```powershell
.\.venv-desktop\Scripts\python.exe -m pytest staging/coordination/deeptutor_gateway/tests/test_supplemental_answers.py -q
```

真实题库集成测试需要本地资料，不能用纯逻辑测试通过来替代原图、化学答案或课堂使用核对。导出文档应查看实际分页，桌面包应另外检查启动和退出。

```powershell
powershell -ExecutionPolicy Bypass -File runtime/deeptutor_shchem/desktop_build.ps1 -Python .venv-desktop/Scripts/python.exe -BuildTag local-review
```

## 版本管理约定

在功能分支开发，检查通过后通过拉取请求合并。软件版本、更新说明、测试结果分开记录；保留来源原件，不批量改写或上传本地资料。

仓库未附开源许可证。第三方代码、教学资料与原始图片的授权不因该仓库存在而改变。

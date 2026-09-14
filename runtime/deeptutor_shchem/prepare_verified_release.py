"""Publish only artifacts produced by the preceding successful Windows job.

Explicit source/asset lists; no discovery of teacher materials or credentials.
A release is left in draft until all attachments have uploaded successfully.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.1.87"
BRANCH = "feature/desktop-onboarding-0.1.87"
TAG = "v0.1.87"


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    if os.environ.get("GITHUB_EVENT_NAME") != "push" or os.environ.get("GITHUB_REF_NAME") != BRANCH:
        raise RuntimeError("Publishing is limited to an explicit push to the release branch")
    sha, repo = os.environ["GITHUB_SHA"], os.environ["GITHUB_REPOSITORY"]
    if command("git", "rev-parse", "HEAD") != sha:
        raise RuntimeError("Checkout does not match tested revision")
    if command("git", "ls-remote", "origin", "refs/heads/" + BRANCH).split()[0] != sha:
        raise RuntimeError("Branch advanced; do not overwrite newer work")
    if command("git", "ls-remote", "--tags", "origin", "refs/tags/" + TAG):
        raise RuntimeError("Version tag already exists; never move or replace it")
    source = ROOT / ".release-input"
    ready = json.loads((source / "readiness-qa/onboarding-smoke.json").read_text(encoding="utf-8"))
    packaged = json.loads((source / "package-qa/package-verification.json").read_text(encoding="utf-8"))
    for report in (ready, packaged):
        assert report["version"] == VERSION and report["source_commit"] == sha
        assert not report["uncaught_errors"] and len(report["routes_opened"]) == 8
    assert packaged["frozen"] and packaged["detached_directory"]
    assert packaged["cleared_python_and_workspace_environment"]
    assert packaged["normal_native_start_and_close"] and not packaged["system_fonts_bundled"]
    assert packaged["editable_pptx_slides"] == 5 and packaged["pdf_pages"] == 2
    junit = ET.parse(source / "readiness-qa/pytest.xml")
    suites = list(junit.getroot().iter("testsuite"))
    tests = {key: sum(int(s.get(key, 0)) for s in suites)
             for key in ("tests", "failures", "errors", "skipped")}
    assert tests["tests"] > 218 and not any(tests[k] for k in ("failures", "errors", "skipped"))
    screenshot_root = ROOT / "docs/screenshots/v0.1.87"
    screenshot_root.mkdir(parents=True, exist_ok=True)
    for report, folder in ((ready, source / "readiness-qa"), (packaged, source / "package-qa/probe")):
        for item in report["screenshots"]:
            name = item["file"]
            assert Path(name).name == name and name.endswith(".png")
            data = (folder / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == item["sha256"]
            (screenshot_root / name).write_bytes(data)
    ready.update(tests=tests, workflow_run=os.environ["GITHUB_RUN_ID"])
    write_json(ROOT / "docs/qa/0.1.87-readiness.json", ready)
    write_json(ROOT / "docs/qa/0.1.87-package.json", packaged)
    readme = ROOT / "README.md"
    old = readme.read_text(encoding="utf-8")
    archive = ROOT / "README-0.1.86-archive.md"
    if not archive.exists():
        archive.write_bytes(readme.read_bytes())
    header = (ROOT / "docs/releases/0.1.87-guide-header.md").read_text(encoding="utf-8")
    start = old.index("## 01 首页")
    body = old[start:]
    replacements = {"docs/screenshots/v0.1.84/home.png": "docs/screenshots/v0.1.87/packaged-home.png",
                    "docs/screenshots/v0.1.84/student.png": "docs/screenshots/v0.1.87/packaged-student.png",
                    "docs/screenshots/v0.1.84/preparation.png": "docs/screenshots/v0.1.87/packaged-preparation.png",
                    "docs/screenshots/v0.1.84/templates.png": "docs/screenshots/v0.1.87/packaged-templates.png",
                    "docs/screenshots/v0.1.84/classroom.png": "docs/screenshots/v0.1.87/packaged-classroom.png"}
    for old_path, new_path in replacements.items():
        body = body.replace(old_path, new_path)
    # Keep historical demonstrations as historical, not mislabeled new test data.
    body = body.replace("（0.1.84未改版页面）", "（0.1.87实际便携程序）")
    body = body.replace("feature/desktop-reliability-0.1.86", BRANCH)
    first, last = body.index("## 09 导入资料与模型设置"), body.index("## 10 进度、快捷入口与帮助")
    body = body[:first] + "## 09 导入资料与模型设置\n\n![导入资料（0.1.84操作参考）](docs/screenshots/v0.1.84/import.png)\n\n导入仍走本机原生Word/PDF/图片流程；固定98份资料包入口仅适用于已有该资料包的机器。视觉识别另选模型并确认。设置的新界面与操作见本文‘本版设置与本机检查’。本次没有重导原资料。\n\n" + body[last:]
    first, last = body.index("## 版本与验证"), body.index("## 开发定位")
    body = body[:first] + "## 版本与验证\n\n本次交付使用v0.1.87标签；受测/打包提交与只增补图文证据的标签提交分别记录在Release的RELEASE-MANIFEST.json。详见本文开头及机器报告。main和已有PR均未自动合并。历史0.1.86性能与恢复验证保留在其报告中，不与本批测试数量相加。\n\n" + body[last:]
    text = header.replace("{{TEST_COUNT}}", str(tests["tests"])) + "\n\n" + body
    readme.write_text(text, encoding="utf-8")
    change = ROOT / "CHANGELOG.md"
    existing = change.read_text(encoding="utf-8")
    change.write_text(existing.replace("# 更新记录\n", "# 更新记录\n\n## 0.1.87 · 2026-09-14 · 连接设置与Windows试用交付\n\n基础连接/高级预算分层，本机诊断与55项软件资源清单。Windows源码回归与设置交互通过；同一代码打包到独立目录验证8页、草稿、5页PPTX和2页教案PDF，未调用模型。新增真实设置与便携程序截图，发布不可移动标签v0.1.87及便携/源码/证据附件。测试异步读取改为有超时的条件等待；历史失败保留。未签名，未完成真实题库/全部硬件/实际PPTX视觉一致性验收。详见docs/qa/0.1.87-readiness.json与0.1.87-package.json。\n", 1), encoding="utf-8")
    roadmap = ROOT / "docs/roadmaps/audit-followup.md"
    lines = roadmap.read_text(encoding="utf-8").splitlines()
    statuses = {"A04": "0.1.87便携包及版本身份通过隔离Windows验证；未签名，非全硬件验收",
                "A05": "0.1.87声明55项资源，独立目录8页与样例导出通过；实际资料另验",
                "F01": "0.1.87设置分层已完成Windows交互测试；无真实API请求",
                "H01": "0.1.87首次标签/预发布/附件校验闭环；main未自动合并"}
    for index, line in enumerate(lines):
        for key, value in statuses.items():
            if line.startswith("| " + key + " |"):
                columns = line.split("|")
                columns[-2] = " " + value + " "
                lines[index] = "|".join(columns)
    roadmap.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for image in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        assert (ROOT / image).is_file(), image
    command("git", "config", "user.name", "github-actions[bot]")
    command("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    command("git", "add", "-f", "README.md", "README-0.1.86-archive.md", "CHANGELOG.md", "docs/roadmaps/audit-followup.md",
            "docs/screenshots/v0.1.87", "docs/qa/0.1.87-readiness.json", "docs/qa/0.1.87-package.json")
    command("git", "commit", "-m", "docs: record verified 0.1.87 Windows release and actual screenshots [skip ci]")
    delivery_sha = command("git", "rev-parse", "HEAD")
    command("git", "push", "origin", "HEAD:refs/heads/" + BRANCH)
    output = ROOT / "release-delivery"
    output.mkdir(exist_ok=True)
    windows_name = "ShanghaiChem-0.1.87-Windows-x64.zip"
    windows_zip = source / "release-assets" / windows_name
    with ZipFile(windows_zip) as bundle:
        assert not any(Path(n).suffix.lower() in (".ttf", ".ttc", ".otf", ".woff", ".woff2") for n in bundle.namelist())
        builds = [n for n in bundle.namelist() if n.endswith("/BUILD.json")]
        assert len(builds) == 1 and json.loads(bundle.read(builds[0]))["source_commit"] == sha
    shutil.copyfile(windows_zip, output / windows_name)
    command("git", "archive", "--format=zip", "--output=" + str(output / "ShanghaiChem-0.1.87-source.zip"), "HEAD")
    with ZipFile(output / "ShanghaiChem-0.1.87-Windows-QA.zip", "w", ZIP_DEFLATED) as archive_zip:
        for folder in ("readiness-qa", "package-qa"):
            for path in sorted((source / folder).rglob("*")):
                if path.is_file(): archive_zip.write(path, str(path.relative_to(source)))
    import markdown
    html = markdown.markdown(text, extensions=["tables", "fenced_code", "toc"])
    def embed(match):
        path = ROOT / match[1]
        if path.is_file() and path.suffix == ".png":
            return 'src="data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode() + '"'
        return match[0]
    html = re.sub(r'src="([^"]+)"', embed, html)
    (output / "ShanghaiChem-0.1.87-Guide.html").write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>沪上化学智研台0.1.87</title><style>body{max-width:1050px;margin:40px auto;padding:0 24px;font:17px/1.8 system-ui}img{max-width:100%}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px}pre{overflow:auto;background:#f5f5f5;padding:16px}</style>' + html + '</html>', encoding="utf-8")
    files = [{"file": p.name, "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(output.iterdir()) if p.is_file()]
    write_json(output / "RELEASE-MANIFEST.json", {"version": VERSION, "tag": TAG,
        "tested_source_commit": sha, "release_commit": delivery_sha,
        "workflow_run": os.environ["GITHUB_RUN_ID"], "tests": tests,
        "unsigned_trial": True, "assets": files})
    (output / "SHA256SUMS.txt").write_text("\n".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name for p in sorted(output.iterdir()) if p.is_file()) + "\n", encoding="utf-8")
    command("git", "tag", "-a", TAG, "-m", "0.1.87 verified Windows trial; code " + sha)
    command("git", "push", "origin", "refs/tags/" + TAG)
    assets = [str(p) for p in sorted(output.iterdir()) if p.is_file()]
    command("gh", "release", "create", TAG, "--repo", repo, "--verify-tag", "--draft", "--prerelease",
            "--title", "0.1.87 · Windows原生试用版", "--notes-file", "docs/releases/0.1.87.md", *assets)
    command("gh", "release", "edit", TAG, "--repo", repo, "--draft=false", "--latest=false")
    print("Published", TAG, "code", sha, "delivery", delivery_sha)


if __name__ == "__main__":
    main()

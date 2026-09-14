"""Publish the checked Windows artifacts and explicit screenshot evidence.

No personal directory discovery. Version tags and existing release assets are
never moved/overwritten; publish only after a successful same-revision verify job.
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
VERSION = "0.1.88"
BRANCH = "feature/work-organization-0.1.88"
TAG = "v" + VERSION


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    if os.environ.get("GITHUB_EVENT_NAME") != "push" or os.environ.get("GITHUB_REF_NAME") != BRANCH:
        raise RuntimeError("Only an explicit push to the version branch may publish")
    sha, repo = os.environ["GITHUB_SHA"], os.environ["GITHUB_REPOSITORY"]
    assert command("git", "rev-parse", "HEAD") == sha
    assert command("git", "ls-remote", "origin", "refs/heads/" + BRANCH).split()[0] == sha, "Branch advanced"
    assert not command("git", "ls-remote", "--tags", "origin", "refs/tags/" + TAG), "Tag exists; never move it"
    source = ROOT / ".release-input"
    ready = json.loads((source / "readiness-qa/onboarding-smoke.json").read_text(encoding="utf-8"))
    packaged = json.loads((source / "package-qa/package-verification.json").read_text(encoding="utf-8"))
    for report in (ready, packaged):
        assert report["version"] == VERSION and report["source_commit"] == sha
        assert not report["uncaught_errors"] and len(report["routes_opened"]) == 8
        organization = report["work_organization"]
        assert organization["model_calls"] == 0
        assert all(organization[key] for key in ("draft_lifecycle", "task_lifecycle", "original_drafts_unchanged",
            "task_files_unchanged", "unsaved_editor_preserved", "recovery_file_unchanged", "original_topic_search"))
    assert packaged["frozen"] and packaged["detached_directory"] and packaged["normal_native_start_and_close"]
    assert packaged["cleared_python_and_workspace_environment"] and not packaged["system_fonts_bundled"]
    assert packaged["editable_pptx_slides"] == 5 and packaged["pdf_pages"] == 2
    suites = list(ET.parse(source / "readiness-qa/pytest.xml").getroot().iter("testsuite"))
    tests = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    assert tests["tests"] > 263 and not any(tests[k] for k in ("failures", "errors", "skipped"))
    target = ROOT / "docs/screenshots" / TAG
    target.mkdir(parents=True, exist_ok=True)
    names = set()
    for report, folder in ((ready, source / "readiness-qa"), (packaged, source / "package-qa/probe")):
        for item in report["screenshots"]:
            name = item["file"]
            assert Path(name).name == name and name.endswith(".png") and name not in names
            names.add(name)
            data = (folder / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == item["sha256"]
            (target / name).write_bytes(data)
    ready.update(tests=tests, workflow_run=os.environ["GITHUB_RUN_ID"])
    write_json(ROOT / f"docs/qa/{VERSION}-readiness.json", ready)
    write_json(ROOT / f"docs/qa/{VERSION}-package.json", packaged)
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "{{VERIFICATION_SUMMARY}}" in text
    summary = (f"同一提交完成 **{tests['tests']}项Windows定向测试，0失败、0错误、0跳过**。"
               "源代码窗口和复制到独立目录的同一发行EXE，均完成作品重命名、归档、回收站与还原的真实点击检查；"
               "核对原草稿、任务文件、当前未保存表单和恢复文件未改变。8页导航与既有5页PPTX、2页教案PDF生成通过。"
               "本地136项是重叠子集，不与Windows计数相加。具体范围和原始结果见机器报告与验收包。")
    text = text.replace("{{VERIFICATION_SUMMARY}}", summary)
    readme.write_text(text, encoding="utf-8")
    for image in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        assert (ROOT / image).is_file(), image
    roadmap = ROOT / "docs/roadmaps/audit-followup.md"
    content = roadmap.read_text(encoding="utf-8").replace("0.1.88代码完成；Windows与发行验证结果见本版机器报告",
        "0.1.88已实现；Windows源代码与独立EXE验证通过，详见本版机器报告")
    roadmap.write_text(content, encoding="utf-8")
    command("git", "config", "user.name", "github-actions[bot]")
    command("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    command("git", "add", "-f", "README.md", "docs/roadmaps/audit-followup.md", str(target.relative_to(ROOT)),
            f"docs/qa/{VERSION}-readiness.json", f"docs/qa/{VERSION}-package.json")
    command("git", "commit", "-m", "docs: record 0.1.88 verified work organization and native screenshots [skip ci]")
    delivery_sha = command("git", "rev-parse", "HEAD")
    command("git", "push", "origin", "HEAD:refs/heads/" + BRANCH)
    output = ROOT / "release-delivery"
    output.mkdir(exist_ok=True)
    windows_name = f"ShanghaiChem-{VERSION}-Windows-x64.zip"
    windows_zip = source / "release-assets" / windows_name
    with ZipFile(windows_zip) as bundle:
        assert not any(Path(n).suffix.lower() in (".ttf", ".ttc", ".otf", ".woff", ".woff2") for n in bundle.namelist())
        builds = [n for n in bundle.namelist() if n.endswith("/BUILD.json")]
        assert len(builds) == 1 and json.loads(bundle.read(builds[0]))["source_commit"] == sha
    shutil.copyfile(windows_zip, output / windows_name)
    command("git", "archive", "--format=zip", "--output=" + str(output / f"ShanghaiChem-{VERSION}-source.zip"), "HEAD")
    with ZipFile(output / f"ShanghaiChem-{VERSION}-Windows-QA.zip", "w", ZIP_DEFLATED) as archive_zip:
        for folder in ("readiness-qa", "package-qa"):
            for path in sorted((source / folder).rglob("*")):
                if path.is_file():
                    archive_zip.write(path, str(path.relative_to(source)))
    import markdown
    from markdown.extensions.toc import slugify_unicode
    html = markdown.markdown(text, extensions=["tables", "fenced_code", "toc"],
        extension_configs={"toc": {"slugify": slugify_unicode}})
    def embed(match):
        path = ROOT / match[1]
        if path.is_file() and path.suffix == ".png":
            return 'src="data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode() + '"'
        return match[0]
    html = re.sub(r'src="([^"]+)"', embed, html)
    def link(match):
        href = match[1]
        if href.startswith(("#", "http:", "https:", "mailto:")):
            return match[0]
        return 'href="https://github.com/' + repo + '/blob/' + TAG + '/' + href + '"'
    html = re.sub(r'href="([^"]+)"', link, html)
    (output / f"ShanghaiChem-{VERSION}-Guide.html").write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>沪上化学智研台' + VERSION + '</title><style>body{max-width:1050px;margin:40px auto;padding:0 24px;font:17px/1.8 system-ui}img{max-width:100%}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px}pre{overflow:auto;background:#f5f5f5;padding:16px}</style>' + html + '</html>', encoding="utf-8")
    assets = [{"file": p.name, "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
              for p in sorted(output.iterdir()) if p.is_file()]
    write_json(output / "RELEASE-MANIFEST.json", {"version": VERSION, "tag": TAG,
        "tested_source_commit": sha, "release_commit": delivery_sha, "workflow_run": os.environ["GITHUB_RUN_ID"],
        "tests": tests, "unsigned_trial": True, "assets": assets})
    (output / "SHA256SUMS.txt").write_text("\n".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name
        for p in sorted(output.iterdir()) if p.is_file()) + "\n", encoding="utf-8")
    command("git", "tag", "-a", TAG, "-m", VERSION + " verified Windows trial; code " + sha)
    command("git", "push", "origin", "refs/tags/" + TAG)
    command("gh", "release", "create", TAG, "--repo", repo, "--verify-tag", "--draft", "--prerelease",
        "--title", VERSION + " · 作品整理与Windows试用版", "--notes-file", f"docs/releases/{VERSION}.md",
        *[str(p) for p in sorted(output.iterdir()) if p.is_file()])
    command("gh", "release", "edit", TAG, "--repo", repo, "--draft=false", "--latest=false")
    print("Published", TAG, "code", sha, "delivery", delivery_sha)


if __name__ == "__main__":
    main()

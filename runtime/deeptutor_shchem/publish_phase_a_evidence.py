"""Publish only this isolated run's four screenshots and two machine reports."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
BRANCH = "feature/desktop-reliability-0.1.86"
NAMES = ("recovery-restored.png", "recovery-normal-reopen.png", "work-search-all.png", "work-search-oldest.png")


def main():
    output = ROOT / "phase-a-qa"
    report = json.loads((output / "phase-a-smoke.json").read_text(encoding="utf-8"))
    expected = os.environ["SHCHEM_SOURCE_SHA"]
    if report["source_commit"] != expected or report["uncaught_errors"]:
        raise RuntimeError("Wrong source revision or failed native run")
    xml = ElementTree.parse(output / "pytest.xml").getroot()
    suites = [xml] if xml.tag == "testsuite" else list(xml.findall("testsuite"))
    report["tests"] = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    if report["tests"]["failures"] or report["tests"]["errors"]:
        raise RuntimeError("Cannot publish a failed regression run as successful")
    report["workflow_run"] = os.environ.get("GITHUB_RUN_ID")
    performance = json.loads((output / "performance.json").read_text(encoding="utf-8"))
    if performance["source_commit"] != expected:
        raise RuntimeError("Performance report belongs to another source")
    target = ROOT / "docs/screenshots/v0.1.86"
    target.mkdir(parents=True, exist_ok=True)
    records = {row["file"]: row for row in report["screenshots"]}
    paths = []
    for name in NAMES:
        source = output / name
        if hashlib.sha256(source.read_bytes()).hexdigest() != records[name]["sha256"]:
            raise RuntimeError("Screenshot changed after capture")
        shutil.copy2(source, target / name)
        paths.append(str((target / name).relative_to(ROOT)))
    report_path = ROOT / "docs/qa/0.1.86-phase-a.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(output / "performance.json", ROOT / "docs/qa/0.1.86-performance.json")
    paths.extend(["docs/qa/0.1.86-phase-a.json", "docs/qa/0.1.86-performance.json"])
    subprocess.run(["git", "add", "-f", "--", *paths], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode:
        subprocess.run(["git", "commit", "-m", "docs: publish verified 0.1.86 recovery, search and performance evidence [skip ci]"], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:" + BRANCH], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()

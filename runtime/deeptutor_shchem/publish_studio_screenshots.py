"""Publish the exact allow-listed synthetic CI screens for the README.

Runs only on this feature branch after tests. Never discovers personal files.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
BRANCH = "feature/desktop-studio-0.1.84"
NAMES = (
    "home", "library", "paper", "student", "preparation", "templates", "classroom", "mywork",
    "classroom-participation", "classroom-equilibrium", "classroom-feedback",
    "import", "settings", "progress", "template-preview", "commands", "help", "templates-compact",
)


def main() -> None:
    sha = os.environ["GITHUB_SHA"]
    if (os.environ.get("GITHUB_EVENT_NAME") != "push"
            or os.environ.get("GITHUB_REF") != "refs/heads/" + BRANCH):
        return
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    folder = ROOT / "studio-qa"
    report = json.loads((folder / "smoke-report.json").read_text(encoding="utf-8"))
    if report["source_commit"] != sha or report["uncaught_errors"] or report["version"] != "0.1.84":
        raise RuntimeError("Screenshots do not match the successful source run")
    listed = {item["file"]: item for item in report["screenshots"]}
    if set(listed) != {name + ".png" for name in NAMES}:
        raise RuntimeError("Unexpected screenshot manifest")
    suites = ET.parse(folder / "pytest.xml").getroot()
    results = suites.findall("testsuite") if suites.tag == "testsuites" else [suites]
    tests = {key: sum(int(s.get(key, "0")) for s in results)
             for key in ("tests", "failures", "errors", "skipped")}
    if not tests["tests"] or any(tests[key] for key in ("failures", "errors", "skipped")):
        raise RuntimeError("Regression run was not fully successful")
    report["tests"] = tests
    report["workflow_run"] = os.environ["GITHUB_SERVER_URL"] + "/" + repo + "/actions/runs/" + os.environ["GITHUB_RUN_ID"]
    # Enrich the local artifact and publish exactly the same public report.
    encoded_report = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    (folder / "smoke-report.json").write_bytes(encoded_report)

    def api(path, body=None, method=None):
        request = Request("https://api.github.com/repos/" + repo + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          method=method,
                          headers={"Authorization": "Bearer " + token,
                                   "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    current = api("/git/ref/heads/" + BRANCH)["object"]["sha"]
    if current != sha:
        print("Branch advanced; newer work is unchanged.")
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("sha=" + sha + "\n")
        return
    base = api("/git/commits/" + sha)["tree"]["sha"]
    entries = []
    for name in NAMES:
        source = folder / (name + ".png")
        data = source.read_bytes()
        if len(data) > 2_000_000 or hashlib.sha256(data).hexdigest() != listed[source.name]["sha256"]:
            raise RuntimeError("Screenshot bytes changed after the smoke run")
        blob = api("/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        entries.append({"path": "docs/screenshots/v0.1.84/" + source.name,
                        "mode": "100644", "type": "blob", "sha": blob["sha"]})
    blob = api("/git/blobs", {"content": base64.b64encode(encoded_report).decode(), "encoding": "base64"})
    entries.append({"path": "docs/qa/0.1.84-studio-smoke.json", "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = api("/git/trees", {"base_tree": base, "tree": entries})
    commit = api("/git/commits", {"tree": tree["sha"], "parents": [sha],
                                "message": "docs: refresh verified 0.1.84 native screenshots [skip ci]"})
    api("/git/refs/heads/" + BRANCH, {"sha": commit["sha"], "force": False}, method="PATCH")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("sha=" + commit["sha"] + "\n")
    print("Published 18 synthetic native screenshots and their source report.")


if __name__ == "__main__":
    main()

"""Publish only isolated CI screenshots; never discover or upload personal files."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

BRANCH = "integration/desktop-closure-20260914"
ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    if os.environ.get("GITHUB_EVENT_NAME") != "push" or os.environ.get("GITHUB_REF") != "refs/heads/" + BRANCH:
        return
    sha = os.environ["GITHUB_SHA"]
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    report = json.loads((ROOT / "desktop-qa/smoke-report.json").read_text(encoding="utf-8"))
    if report["source_commit"] != sha or report["uncaught_errors"]:
        raise RuntimeError("Screenshot evidence does not match this successful run")

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
        print("Branch advanced; leave newer work untouched.")
        return
    base = api("/git/commits/" + sha)["tree"]["sha"]
    entries = []
    for source, target in (
        ("desktop-qa/home-empty.png", "docs/screenshots/desktop-home.png"),
        ("desktop-qa/library-progress-empty.png", "docs/screenshots/library-progress.png"),
        ("desktop-qa/smoke-report.json", "docs/qa/0.1.83-windows-smoke.json"),
    ):
        data = (ROOT / source).read_bytes()
        if len(data) > 2_000_000:
            raise RuntimeError("Unexpected screenshot artifact size")
        blob = api("/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        entries.append({"path": target, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = api("/git/trees", {"base_tree": base, "tree": entries})
    commit = api("/git/commits", {"tree": tree["sha"], "parents": [sha],
        "message": "docs: refresh verified Windows screenshots [skip ci]"})
    api("/git/refs/heads/" + BRANCH, {"sha": commit["sha"], "force": False}, method="PATCH")
    print("Published two empty-library screenshots and their source-commit report.")


if __name__ == "__main__":
    main()

"""Run scoped native UI regressions in independent interpreters, with JUnit totals."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Use a fresh test report directory")
    tests = [(ROOT / file).resolve(strict=True) for file in args.files]
    allowed = ROOT / "staging/coordination/deeptutor_gateway/tests"
    if any(
        not file.is_relative_to(allowed) or not file.name.startswith("test_")
        for file in tests
    ):
        raise RuntimeError("Only named gateway regression files are supported")
    if len({file.stem for file in tests}) != len(tests):
        raise RuntimeError("Test names must be unique")
    args.output.mkdir(parents=True)
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8"}
    rows = []
    for path in tests:
        report = args.output / (path.stem + ".xml")
        start = time.monotonic()
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(path),
                "-q",
                "--junitxml",
                str(report),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        (args.output / (path.stem + ".txt")).write_text(
            run.stdout + run.stderr, encoding="utf-8"
        )
        if not report.exists():
            raise RuntimeError("No JUnit produced: " + path.name)
        xml = ET.parse(report).getroot()
        suites = list(xml) if xml.tag == "testsuites" else [xml]
        row = {
            field: sum(int(suite.attrib.get(field, 0)) for suite in suites)
            for field in ("tests", "failures", "errors", "skipped")
        }
        row.update(
            file=path.relative_to(ROOT).as_posix(),
            exit_code=run.returncode,
            seconds=round(time.monotonic() - start, 3),
        )
        row["passed"] = row["tests"] - row["failures"] - row["errors"] - row["skipped"]
        rows.append(row)
        print(json.dumps(row), flush=True)
        summary = {
            "version": args.version,
            "execution_mode": "one independent interpreter per file",
            "files": len(rows),
            "results": rows,
            "real_model_called": False,
            **{
                field: sum(item[field] for item in rows)
                for field in ("tests", "passed", "failures", "errors", "skipped")
            },
        }
        (args.output / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        if run.returncode:
            print(run.stdout[-5000:] + run.stderr[-2000:], flush=True)
            return run.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

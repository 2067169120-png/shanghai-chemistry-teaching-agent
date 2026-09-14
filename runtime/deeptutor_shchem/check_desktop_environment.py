"""Run from a source checkout even when Qt is missing; emits no model secrets."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description="本机依赖检查；不发起网络或模型请求")
    parser.add_argument("--json", type=Path, help="Optional report output; no local paths or credentials")
    args = parser.parse_args(argv)
    from integrations.deeptutor_shchem_v1.desktop_environment import collect_environment_report, report_text
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths, DesktopPathError
    try:
        paths = DesktopPaths.discover(ROOT)
    except DesktopPathError:
        print("工作区设置无效。请检查 SHCHEM_WORKSPACE_ROOT；未改动现有资料。")
        return 2
    report = collect_environment_report(paths)
    # Readiness errors are kept in the report; an absent optional Office must
    # not turn a successful diagnostic run into an application launch failure.
    print(report_text(report))
    if args.json:
        try:
            args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            print("检查已完成，但报告无法写入指定文件。")
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

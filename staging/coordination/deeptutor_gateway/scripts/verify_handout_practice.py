"""No network; inspect real native bindings and optionally export a small QA set."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    HandoutCandidateService,
)
from integrations.deeptutor_shchem_v1.desktop_handout_practice import (
    HandoutPracticeService,
    NativeParagraphs,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    state = DesktopStateStore(Path(tempfile.mkdtemp(prefix="shchem-practice-qa-")))
    service = HandoutCandidateService(ROOT, state)
    items = [
        item
        for item in service.catalog()["items"]
        if item["classification"] == "native_text_complete"
    ]
    native = NativeParagraphs(ROOT)
    failed = []
    for item in items:
        try:
            native.read(item, "question")
            native.read(item, "answer")
        except Exception as exc:
            failed.append(
                {
                    "package": item["package_id"],
                    "title": item["title"],
                    "error": str(exc),
                    "cause": str(exc.__cause__),
                }
            )
    result = {
        "native_complete": len(items),
        "native_verified": len(items) - len(failed),
        "failed": failed,
        "state_root": str(state.root),
    }
    if args.export:
        selected = [item for item in items if item["package_id"] == "PKG-033"][:3]
        selected += [
            item
            for item in items
            if item["package_id"] == "PKG-051" and item["printed_number"] == "5"
        ]
        practice = HandoutPracticeService(service)
        record = practice.export(
            "电解质与物质检验讲义练习",
            [{"key": item["key"], "revision": item["revision"]} for item in selected],
            2,
        )
        result["export_id"] = record["export_id"]
        result["files"] = {
            role: str(practice.artifact_path(record["export_id"], role))
            for role in ("student", "teacher")
        }
        result["native_subscript_runs"] = sum(
            run.font.subscript is True
            for item in selected
            for paragraph in native.read(item, "question")
            if hasattr(paragraph, "runs")
            for run in paragraph.runs
        )
        if args.render:
            from integrations.deeptutor_shchem_v1.paper_export_renderer import (
                RendererToolchain,
                _render_with_canonical_docx_tool,
            )

            dependency_root = Path(
                "C:/Users/20671/.cache/codex-runtimes/codex-primary-runtime/dependencies"
            )
            toolchain = RendererToolchain(
                python_exe=dependency_root / "python/python.exe",
                render_docx_script=Path(
                    "C:/Users/20671/.codex/plugins/cache/openai-primary-runtime/documents/26.905.11957/skills/documents/render_docx.py"
                ),
                pdftoppm_exe=dependency_root
                / "native/poppler/Library/bin/pdftoppm.exe",
                dpi=150,
                conversion_backend="word_com",
            )
            result["renders"] = {}
            for role, filename in result["files"].items():
                output = (
                    ROOT / "runtime/deeptutor_shchem/qa" / record["export_id"] / role
                )
                pages, pdf, receipt = _render_with_canonical_docx_tool(
                    Path(filename), output, toolchain=toolchain
                )
                result["renders"][role] = {
                    "pages": [str(path) for path in pages],
                    "receipt": receipt,
                }
            report = (
                ROOT
                / "runtime/deeptutor_shchem/qa"
                / record["export_id"]
                / "verification.json"
            )
            report.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

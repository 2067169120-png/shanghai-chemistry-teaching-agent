"""Local-only actual source regression; does not publish source material."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def main():
    from integrations.deeptutor_shchem_v1.desktop_word_question_export import (
        export_word_questions,
    )
    from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
        index_word_questions,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-state", type=Path, required=True)
    parser.add_argument("--preview", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Use a new output folder")
    selected, counts = [], []
    for path in args.preview:
        preview = json.loads(path.read_text(encoding="utf-8"))
        items = index_word_questions(preview)
        counts.append(
            {"source_sha256": preview["source_sha256"], "questions": len(items)}
        )
        anchors = [10, 28, 90] if len(preview["blocks"]) == 318 else [184]
        data = (
            args.archive_state
            / "visual-import-v2/sources"
            / (preview["source_sha256"] + ".docx")
        ).read_bytes()
        assert hashlib.sha256(data).hexdigest() == preview["source_sha256"]
        for anchor in anchors:
            item = next(row for row in items if row["block_start"] == anchor)
            selected.append(
                dict(
                    item,
                    source_bytes=data,
                    source_name=preview["source_name"],
                    points=2,
                )
            )
    payload = export_word_questions("电解质与有机结构练习", selected)
    args.output.mkdir(parents=True)
    report = {
        "sources": counts,
        "selected": len(selected),
        "warnings": payload.get("warnings", []),
        "files": {},
    }
    for role in ("student", "teacher"):
        target = args.output / (role + ".docx")
        target.write_bytes(payload[role + "_bytes"])
        report["files"][role] = {
            "path": str(target),
            "sha256": hashlib.sha256(payload[role + "_bytes"]).hexdigest(),
        }
    if args.render:
        from integrations.deeptutor_shchem_v1.paper_export_renderer import (
            RendererToolchain,
            _render_with_canonical_docx_tool,
        )

        dependencies = Path(
            "C:/Users/20671/.cache/codex-runtimes/codex-primary-runtime/dependencies"
        )
        tools = RendererToolchain(
            python_exe=dependencies / "python/python.exe",
            render_docx_script=Path(
                "C:/Users/20671/.codex/plugins/cache/openai-primary-runtime/documents/26.905.11957/skills/documents/render_docx.py"
            ),
            pdftoppm_exe=dependencies / "native/poppler/Library/bin/pdftoppm.exe",
            dpi=150,
            conversion_backend="word_com",
        )
        for role, info in report["files"].items():
            pages, _, receipt = _render_with_canonical_docx_tool(
                Path(info["path"]), args.output / role, toolchain=tools
            )
            info["pages"] = [str(page) for page in pages]
            info["receipt"] = receipt
    (args.output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "sources": counts,
                "selected": len(selected),
                "warnings": len(report["warnings"]),
                "pages": {
                    role: len(info.get("pages", []))
                    for role, info in report["files"].items()
                },
            }
        )
    )


if __name__ == "__main__":
    main()

"""Render a hash-verified local source for multimodal page inspection."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from import_analysis_teaching_pack import verified_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Use a fresh page-review output directory")
    sources = verified_sources(ROOT / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/archive_inventory.csv")
    path, row = next((p, r) for p, r in sources if r["package_id"] == args.package)
    from integrations.deeptutor_shchem_v1.paper_export_renderer import (
        RendererToolchain,
        _render_with_canonical_docx_tool,
    )
    deps = Path("C:/Users/20671/.cache/codex-runtimes/codex-primary-runtime/dependencies")
    toolchain = RendererToolchain(
        python_exe=deps / "python/python.exe",
        render_docx_script=Path("C:/Users/20671/.codex/plugins/cache/openai-primary-runtime/documents/26.905.11957/skills/documents/render_docx.py"),
        pdftoppm_exe=deps / "native/poppler/Library/bin/pdftoppm.exe",
        dpi=150, conversion_backend="word_com",
    ).validated()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pages, pdf, renderer = _render_with_canonical_docx_tool(path, args.output.resolve(), toolchain=toolchain)
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        raise RuntimeError("Original changed during source render")
    report = {"package_id": args.package, "source_name": path.name, "source_sha256": row["sha256"],
              "page_count": len(pages), "rendered_pdf": pdf.name,
              "source_unchanged": True, "pages_visually_reviewed": [], "renderer": renderer}
    (args.output / "render-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"package_id": args.package, "page_count": len(pages), "source_unchanged": True}), flush=True)


if __name__ == "__main__":
    main()

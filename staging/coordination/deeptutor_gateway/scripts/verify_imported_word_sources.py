"""Exercise real Word sources through isolated desktop import and preparation.

No source editing, central-library writes, model requests, or personal settings.
The report is an extraction/integration check, not chemistry or visual approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_visual_import_v2 import (
    DesktopImportBridgeError,
)


class NoModel:
    def list_metadata(self):
        return []

    def borrow_invocation_context(self, *args, **kwargs):
        raise AssertionError("A native Word check must not invoke a model")


class NoPageRendering:
    def __init__(self):
        self.calls = 0

    def render(self, *_args, **_kwargs):
        self.calls += 1
        raise DesktopImportBridgeError("page_preview_not_requested", "本次原生正文检查不渲染页面。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Use a new QA directory")
    args.output.mkdir(parents=True)
    paths = DesktopPaths.from_workspace(ROOT, state_root=args.output / "isolated-state")
    unused = object()
    renderer = NoPageRendering()
    facade = DesktopWorkbenchFacade(
        paths, theme_reader=unused, supplemental_reader=unused,
        curriculum_reader=unused, search_reader=unused, provider_store=NoModel(),
        state_store=DesktopStateStore(paths.state_root), paper_export_jobs=unused,
        visual_import_renderer=renderer,
    )
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in args.source}
    receipt = facade.save_visual_import_batch(handout_files=args.source, source_type="真实Word离线核对")
    rows = []
    for index, source in enumerate(facade.imported_word_sources(receipt.batch_id), 1):
        sid = source["source_id"]
        preview = facade.imported_word_preview(receipt.batch_id, sid)
        (args.output / f"source-{index}-preview.json").write_text(
            json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Bounded source selection, explicitly recorded rather than a truncated
        # whole-document reference. All original blocks remain in the preview.
        end = min(35, len(preview["blocks"]))
        reference = facade.imported_word_reference(
            receipt.batch_id, sid, preview["source_sha256"], 1, end, preview["revision"]
        )
        (args.output / f"source-{index}-reference.json").write_text(
            json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        verified_assets = 0
        for asset in preview["assets"]:
            if asset["preview_supported"]:
                content = facade.imported_word_asset(receipt.batch_id, sid, asset["asset_id"])
                if hashlib.sha256(content["bytes"]).hexdigest() != asset["sha256"]:
                    raise AssertionError("Embedded image bytes differ")
                verified_assets += 1
        rows.append({
            "source_name": preview["source_name"], "source_sha256": preview["source_sha256"],
            "import_state": source["import_state"], "blocks": len(preview["blocks"]),
            "characters": sum(len(block["text"]) for block in preview["blocks"]),
            "blocks_with_warnings": sum(bool(block["warnings"]) for block in preview["blocks"]),
            "embedded_images": len(preview["assets"]), "raster_images_verified": verified_assets,
            "reference_selected_blocks": [1, end], "reference_characters": len(reference["materials"]),
            "original_name_preserved": preview["source_name"] in reference["materials"],
        })
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in before.items())
    report = {
        "sources": rows, "original_files_unchanged": unchanged,
        "source_count": receipt.source_count, "native_source_count": receipt.native_quick_count,
        "visual_pending_count": receipt.visual_queue_count, "page_renderer_calls": renderer.calls,
        "external_model_called": False, "personal_configuration_read": False,
        "formal_library_written": False, "visual_review_complete": False,
    }
    (args.output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not unchanged:
        raise AssertionError("Source changed during check")


if __name__ == "__main__":
    main()

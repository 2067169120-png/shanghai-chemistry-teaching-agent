"""Read-only check of real original-lesson navigation and bound image previews."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise RuntimeError("Use a fresh report path")
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_lecture_library import (
        lecture_catalog,
        search_lectures,
    )
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths

    class NoProviders:
        def __getattr__(self, name):
            raise RuntimeError(
                "Read-only original Word check must not access providers"
            )

    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=args.state_root),
        provider_store=NoProviders(),
    )
    state_path = facade.state_store.path
    before = hashlib.sha256(state_path.read_bytes()).hexdigest()
    reads = []
    original = facade._restore_visual_import_sources

    def counted(descriptor, *, source_ids=None):
        reads.append(sorted(source_ids) if source_ids is not None else None)
        return original(descriptor, source_ids=source_ids)

    facade._restore_visual_import_sources = counted
    start = time.monotonic()
    catalog = lecture_catalog(facade)
    catalog_seconds = time.monotonic() - start
    if reads:
        raise RuntimeError("Metadata navigation read all original documents")
    rows = catalog["items"]
    samples = []
    for term in ("电离", "原子", "氧化还原", "有机", "内能"):
        matches = search_lectures(rows, term)
        entry = {"query": term, "matching_sources": len(matches)}
        if matches:
            selected = matches[0]
            reads.clear()
            preview = facade.imported_word_preview(
                selected["batch_id"], selected["source_id"]
            )
            # Use one explicitly located ordinary picture block, preserving
            # the whole selected paragraph/table instead of manufacturing a question.
            picture = next(
                (
                    asset
                    for asset in preview["assets"]
                    if asset.get("preview_supported")
                ),
                None,
            )
            if picture:
                index = picture["block_index"]
                ref = facade.imported_word_image_reference(
                    selected["batch_id"],
                    selected["source_id"],
                    preview["source_sha256"],
                    index,
                    index,
                    expected_revision=preview["revision"],
                )
                entry.update(
                    source_sha256=preview["source_sha256"],
                    block_index=index,
                    original_blocks=len(preview["blocks"]),
                    picture_reference_count=len(ref["image_references"]),
                    unique_images=len(ref["image_assets"]),
                    image_issues=len(ref["image_issues"]),
                )
            if any(call != [selected["source_id"]] for call in reads):
                raise RuntimeError("Opening one original read unrelated sources")
            entry["only_selected_original_read"] = True
        samples.append(entry)
    if hashlib.sha256(state_path.read_bytes()).hexdigest() != before:
        raise RuntimeError("Read-only verification changed personal state")
    report = {
        "imported_originals": len(rows),
        "with_bound_navigation_index": sum(row["indexed"] for row in rows),
        "catalog_seconds": round(catalog_seconds, 4),
        "catalog_original_file_reads": 0,
        "warnings": catalog["warnings"],
        "samples": samples,
        "personal_state_unchanged": True,
        "provider_accesses": 0,
        "images_committed": 0,
        "chemical_review_completed": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()

"""Read real imported Word sources without touching provider settings or labels."""

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
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths

    class NoProviders:
        def __getattr__(self, name):
            raise RuntimeError(
                "Read-only Word verification must not access providers: " + name
            )

    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=args.state_root),
        provider_store=NoProviders(),
    )
    service = facade._word_questions()
    tracked = [facade.state_store.path, service.attribute_store.path]

    def hashes():
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in tracked
            if path.exists()
        }

    before = hashes()
    start = time.monotonic()
    catalog = facade.word_question_catalog()
    full_seconds = time.monotonic() - start
    selected = next(
        row
        for row in catalog["items"]
        if any(
            asset.get("preview_supported")
            for block in row["question_blocks"]
            for asset in block["assets"]
        )
    )
    asset = next(
        asset
        for block in selected["question_blocks"]
        for asset in block["assets"]
        if asset.get("preview_supported")
    )
    calls = []
    restore = facade._restore_visual_import_sources

    def counted(descriptor, *, source_ids=None):
        calls.append(sorted(source_ids) if source_ids is not None else None)
        return restore(descriptor, source_ids=source_ids)

    facade._restore_visual_import_sources = counted
    checks = []
    for name, action in (
        (
            "selected_source",
            lambda: facade.word_question_source(selected["key"], selected["revision"]),
        ),
        (
            "selected_image",
            lambda: facade.word_question_image(
                selected["key"], selected["revision"], asset["asset_id"]
            ),
        ),
        (
            "attribute_options",
            lambda: facade.word_question_attribute_options(
                selected["key"], selected["revision"]
            ),
        ),
    ):
        calls.clear()
        start = time.monotonic()
        value = action()
        elapsed = time.monotonic() - start
        expected = [[selected["archive_source_id"]]]
        if calls != expected:
            raise RuntimeError("Selected source caused an unrelated archive read")
        if (
            name == "selected_image"
            and hashlib.sha256(value["bytes"]).hexdigest() != asset["sha256"]
        ):
            raise RuntimeError("Original raster differs from source")
        checks.append(
            {
                "operation": name,
                "seconds": round(elapsed, 4),
                "archive_source_reads": len(calls),
            }
        )
    after = hashes()
    if before != after:
        raise RuntimeError(
            "Tracked personal state changed during read-only verification"
        )
    report = {
        "source_count": len(catalog["sources"]),
        "candidate_count": len(catalog["items"]),
        "full_catalog_seconds": round(full_seconds, 4),
        "checks": checks,
        "tracked_state_hashes_unchanged": True,
        "provider_accesses": 0,
        "selected_source_sha256": selected["source_sha256"],
        "state_hashes": after,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "state_hashes"}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

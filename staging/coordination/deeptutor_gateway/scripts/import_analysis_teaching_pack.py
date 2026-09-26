"""Explicitly import the verified analysis-only teaching pack, without a model."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def verified_sources(inventory):
    base = inventory.resolve(strict=True).parent
    with inventory.open(encoding="utf-8-sig", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["document_role"] == "解析版"]
    rows.sort(key=lambda row: row["package_id"])
    if len(rows) != 98 or len({row["package_id"] for row in rows}) != 98:
        raise RuntimeError("Expected exactly 98 analysis documents")
    sources, digests = [], set()
    for row in rows:
        source = (base / row["output_relative_path"]).resolve(strict=True)
        if not source.is_relative_to(base) or source.suffix.lower() != ".docx" or source.is_symlink():
            raise RuntimeError("Invalid analysis source path")
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != row["sha256"] or len(data) != int(row["bytes"]):
            raise RuntimeError("Analysis source no longer matches its inventory")
        if digest in digests:
            raise RuntimeError("Duplicate analysis source")
        digests.add(digest)
        sources.append((source, row))
    return sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sources = verified_sources(args.inventory)
    print(json.dumps({"verified_analysis_files": len(sources), "bytes": sum(int(row["bytes"]) for _, row in sources), "apply": args.apply}), flush=True)
    if not args.apply:
        return
    if args.report.exists():
        raise RuntimeError("Use a fresh report path")
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths

    class NoProviders:
        calls = 0

        def list_metadata(self):
            return ()

        def borrow_invocation_context(self, *_args, **_kwargs):
            self.calls += 1
            raise RuntimeError("Offline corpus import must not invoke models")

        def __getattr__(self, name):
            raise RuntimeError("Offline import must not read provider settings: " + name)

    providers = NoProviders()
    paths = DesktopPaths.from_workspace(ROOT, state_root=args.state_root)
    facade = DesktopWorkbenchFacade(paths, provider_store=providers)
    before = facade.list_imported_word_batches()
    started = time.monotonic()

    def progress(value):
        print(json.dumps({"stage": value.get("stage"), "seconds": round(time.monotonic() - started, 1)}), flush=True)

    receipt = facade.save_visual_import_batch(
        handout_files=tuple(path for path, _ in sources),
        source_type="上好课2026上海一轮复习·解析版",
        group_id="teaching-pack-analysis98",
        progress_callback=progress,
    )
    descriptor = facade._saved_visual_import_batch(receipt.batch_id)
    archived = facade._restore_visual_import_sources(descriptor)
    expected = {row["sha256"] for _, row in sources}
    actual = {source.source_sha256 for source in archived}
    if actual != expected or len(archived) != 98:
        raise RuntimeError("Saved sources differ from the 98 confirmed analysis files")
    if providers.calls:
        raise RuntimeError("Unexpected model invocation")
    report = {
        "batch_id": receipt.batch_id,
        "state_root": str(paths.state_root),
        "source_count": len(archived),
        "before_word_batches": len(before),
        "after_word_batches": len(facade.list_imported_word_batches()),
        "source_role": "解析版",
        "source_hashes_verified": True,
        "provider_calls": providers.calls,
        "source_status": receipt.status,
        "question_indexing_complete": False,
        "question_attributes_complete": False,
        "knowledge_distillation_complete": False,
        "sources": [{"package_id": row["package_id"], "source_name": path.name, "sha256": row["sha256"]} for path, row in sources],
        "seconds": round(time.monotonic() - started, 2),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("sources", "state_root")}), flush=True)


if __name__ == "__main__":
    main()

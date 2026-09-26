"""Warm source-bound previews and record actual per-document question counts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from import_analysis_teaching_pack import verified_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
        PreparationSourcesService,
    )
    from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import (
        WordPreviewCache,
    )
    from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
        index_word_questions,
    )

    sources = verified_sources(args.inventory)
    cache = WordPreviewCache(args.state_root / "word-question-previews")
    reader = PreparationSourcesService(ROOT)
    started = time.monotonic()
    records = []
    args.report.parent.mkdir(parents=True, exist_ok=True)
    for path, metadata in sources:
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise RuntimeError("Source changed after inventory verification")
        preview = cache.load(data, path.name)
        hit = preview is not None
        if preview is None:
            preview = reader.word_preview_bytes(data, path.name)
        if not cache.save(data, path.name, preview):
            raise RuntimeError("Native preview cache could not be persisted")
        questions = index_word_questions(preview)
        row = {
            "package_id": metadata["package_id"], "source_name": path.name,
            "source_sha256": metadata["sha256"], "source_revision": preview["revision"],
            "question_count": len(questions), "block_count": len(preview["blocks"]),
            "asset_count": len(preview["assets"]), "warning_count": len(preview["warnings"]),
            "questions_with_answer_blocks": sum(bool(q["answer_blocks"]) for q in questions),
            "questions_with_context": sum(bool(q["context_blocks"]) for q in questions),
            "export_ready_candidates": sum(q.get("export_ready") is True for q in questions),
            "cache_hit": hit,
        }
        records.append(row)
        report = {"completed_files": len(records), "expected_files": 98,
                  "question_count": sum(v["question_count"] for v in records),
                  "source_hashes_verified": True, "teacher_reviewed": False,
                  "provider_calls": 0, "sources": records,
                  "seconds": round(time.monotonic() - started, 2)}
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({**row, "seconds": report["seconds"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

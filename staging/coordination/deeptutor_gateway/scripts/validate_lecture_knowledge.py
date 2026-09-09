"""Validate the 46 concise lecture indexes against their intact original Word files."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def validate(state_root: Path):
    from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import (
        WordPreviewCache,
    )
    inventory = ROOT / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/archive_inventory.csv"
    with inventory.open(encoding="utf-8-sig") as stream:
        originals = {r["package_id"]: r for r in csv.DictReader(stream) if r["document_role"] == "解析版" and "复习讲义" in r["entry_path"]}
    rows = []
    for name in ("part-a.jsonl", "part-b.jsonl", "part-c.jsonl"):
        for line in (ROOT / "knowledge/lectures" / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    if len(rows) != 46 or len({r["id"] for r in rows}) != 46 or {r["package_id"] for r in rows} != set(originals):
        raise ValueError("The 46 lecture source identities do not match")
    cache = WordPreviewCache(state_root / "word-question-previews")
    sources = []
    for row in sorted(rows, key=lambda r: r["package_id"]):
        source = originals[row["package_id"]]
        path = inventory.parent / source["output_relative_path"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != source["sha256"] or row["source_sha256"] != source["sha256"] or row["source_name"] != path.name:
            raise ValueError("Lecture source/hash/name mismatch: " + row["package_id"])
        preview = cache.load(data, path.name)
        if preview is None:
            raise ValueError("Missing verified lecture text")
        positions = {b["index"] for b in preview["blocks"]}
        if row["human_reviewed"] is not False or row["review_status"] != "ai_distilled_pending_teacher_review":
            raise ValueError("The index must not imply human approval")
        claim_count = 0
        for group in ("knowledge", "methods", "pitfalls"):
            for claim in row[group]:
                if set(claim) != {"summary", "block_indices"} or not isinstance(claim["summary"], str) or not claim["summary"].strip():
                    raise ValueError("Incomplete indexed claim")
                if not claim["block_indices"] or any(type(i) is not int or i not in positions for i in claim["block_indices"]):
                    raise ValueError("Nonexistent source block in " + row["package_id"])
                if "【待查看原文" in claim["summary"]:
                    raise ValueError("Raw missing-object marker cannot become a knowledge statement")
                claim_count += 1
        sources.append({"package_id": row["package_id"], "title": row["title"], "source_sha256": row["source_sha256"],
                        "knowledge_count": len(row["knowledge"]), "method_count": len(row["methods"]), "pitfall_count": len(row["pitfalls"]),
                        "claim_count": claim_count, "all_block_refs_exist": True})
    return {"schema_version": 1, "source_pack_analysis_documents": 98,
            "lecture_index_count": 46, "other_exercise_and_test_documents": 52,
            "purpose": "辅助查找原教案知识与方法，不替代98份完整原文、表格、图片及答案。",
            "source_hashes_and_block_refs_verified": True, "all_chemical_claims_teacher_reviewed": False,
            "knowledge_claims": sum(s["knowledge_count"] for s in sources),
            "method_claims": sum(s["method_count"] for s in sources),
            "pitfall_claims": sum(s["pitfall_count"] for s in sources), "sources": sources}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = validate(args.state_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "sources"}, ensure_ascii=True))


if __name__ == "__main__":
    main()

"""Read-only, hash-bound comparison of the 105 historical missing-answer leads.

Only the requested report is written. No provider, importer, personal database,
or preview save method is invoked. Source text remains in private QA reports.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import WordPreviewCache
from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
    _ANSWER,
    QUESTION_INDEX_REVISION,
    _marker,
    index_word_questions,
)


def audit(inventory: Path, cache_root: Path, historical_audit: Path, baseline: Path | None = None) -> dict:
    historical = json.loads(historical_audit.read_text(encoding="utf-8"))
    leads = [f for f in historical["findings"] if f["kind"] == "no_answer_range"]
    if (historical["index_revision"] != "20260910-word-theme-question-index-v3" or
            len(leads) != 105 or
            len({(f["package_id"], f["block_index"]) for f in leads}) != 105):
        raise ValueError("Expected the 105 frozen missing-answer leads")
    with inventory.open(encoding="utf-8-sig", newline="") as stream:
        sources = [r for r in csv.DictReader(stream) if r["document_role"] == "解析版"]
    if len(sources) != 98 or len({r["sha256"] for r in sources}) != 98:
        raise ValueError("Expected the 98 unique analysis-version sources")
    cache = WordPreviewCache(cache_root)
    counts, records, source_records = Counter(), [], []
    previous = {}
    if baseline is not None:
        previous = {s["package_id"]: s for s in json.loads(baseline.read_text(encoding="utf-8"))["sources"]}
    differences = {"added": [], "removed": [], "changed": []}
    answer_provenance = Counter()
    source_issues = []
    comparison_fields = ("block_start", "question_end", "answer_start", "block_end", "export_ready")
    for row in sorted(sources, key=lambda r: r["package_id"]):
        path = inventory.parent / row["output_relative_path"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Source changed: " + row["package_id"])
        preview = cache.load(data, path.name)
        if preview is None:
            raise ValueError("Verified native preview unavailable: " + row["package_id"])
        items = index_word_questions(preview)
        by_start = {item["origin_block_start"]: item for item in items}
        original_blocks = {block["index"]: block for block in preview["blocks"]}
        if row["package_id"] == "PKG-086":
            item = by_start[188]
            if (item["question_end"], item["answer_start"], item["block_end"]) != (193, 194, 197):
                raise ValueError("The individually reviewed source-answer issue changed")
            source_issues.append({
                "issue_id": "PKG-086-b188-original-answer-parts-missing",
                "status": "needs_review",
                "kind": "source_answer_incomplete",
                "package_id": row["package_id"],
                "source_name": path.name,
                "source_sha256": row["sha256"],
                "preview_revision": preview["revision"],
                "question_key": item["key"],
                "question_range": [188, 193], "answer_range": [194, 197],
                "evidence": "原题190/191/193区块列有(1)(2)(3)，原答案194及详解197只列(1)；195/196为总分析，没有(2)(3)的对应作答。198起为下一道明确变式题，不应借入下题答案。",
                "unanswered_part_candidates": ["(2)", "(3)"],
                "suggested_handling": "保留完整原题原答，标注原讲义答案不全、待补答核对；不修改原Word、不生成或猜测答案。",
                "human_reviewed": False,
                "question_blocks": item["question_blocks"],
                "answer_blocks": item["answer_blocks"],
                "next_block": original_blocks[198],
            })
        for item in items:
            for block in item["question_blocks"] + item["answer_blocks"] + item["context_blocks"]:
                original = original_blocks[block["index"]]["text"]
                if block.get("display_only_split"):
                    start, end = block["text_range"]
                    if block["source_text"] != original or block["text"] != original[start:end]:
                        raise ValueError("Altered display-only source slice")
                elif block["text"] != original:
                    raise ValueError("Indexed text is not the intact source text")
            if item["answer_blocks"]:
                if _ANSWER.search(item["answer_blocks"][0]["text"]):
                    answer_provenance["original_answer_or_analysis_marker"] += 1
                else:
                    if item["export_ready"] or item["boundary_status"] != "needs_review":
                        raise ValueError("Unmarked answer cannot be silently approved")
                    answer_provenance["unmarked_original_choice_pending_review"] += 1
        if previous:
            old = {i["key"]: i for i in previous[row["package_id"]]["items"]}
            current = {i["key"]: i for i in items}
            for key in sorted(current.keys() - old.keys(), key=lambda k: current[k]["block_start"]):
                item = current[key]
                differences["added"].append({
                    "package_id": row["package_id"], "source_sha256": row["sha256"],
                    "candidate_type": "explicit_label" if _marker(item["question_blocks"][0]["text"]) else "numbered_parent" if item.get("nested_section_starts") else "numbered_question",
                    "item": item,
                })
            for key in sorted(old.keys() - current.keys(), key=lambda k: old[k]["block_start"]):
                differences["removed"].append({"package_id": row["package_id"], "previous": old[key]})
            for key in sorted(old.keys() & current.keys(), key=lambda k: current[k]["block_start"]):
                changed = {field: {"before": old[key][field], "after": current[key][field]} for field in comparison_fields if old[key][field] != current[key][field]}
                if changed:
                    differences["changed"].append({"package_id": row["package_id"], "key": key, "changes": changed, "current": {k: current[key][k] for k in comparison_fields + ("chapter",)}})
        source_counts = {
            "questions": len(items),
            "with_answers": sum(bool(i["answer_blocks"]) for i in items),
            "without_answers": sum(not i["answer_blocks"] for i in items),
            "with_context": sum(bool(i["context_blocks"]) for i in items),
            "export_ready": sum(i["export_ready"] for i in items),
        }
        counts.update(source_counts)
        source_records.append({
            "package_id": row["package_id"], "source_sha256": row["sha256"],
            "source_name": path.name, "preview_revision": preview["revision"], "counts": source_counts,
            "items": [{k: i[k] for k in ("key", "origin_block_start", "block_start", "question_end", "answer_start", "block_end", "export_ready")} for i in items],
        })
        blocks = preview["blocks"]
        positions = {b["index"]: p for p, b in enumerate(blocks)}
        for lead in (f for f in leads if f["package_id"] == row["package_id"]):
            start = lead["block_index"]
            item = by_start.get(start)
            position = positions[start]
            end_position = positions[item["block_end"]] if item else position
            # These classifications were checked individually against source
            # paragraphs in the frozen 105-lead audit; not a product heuristic.
            kind = {
                ("PKG-015", 121): "decimal_measurement_misread_as_heading",
                ("PKG-076", 311): "decimal_measurement_misread_as_heading",
                ("PKG-035", 206): "internal_numbered_section_with_combined_answers",
                ("PKG-040", 265): "unmarked_original_choice_and_explanation",
            }.get((row["package_id"], start), "knowledge_or_method_numbering_not_question")
            records.append({
                "package_id": row["package_id"], "source_sha256": row["sha256"],
                "source_name": path.name, "historical_key": lead["question_key"],
                "origin_block_start": start,
                "source_review_classification": kind,
                "current_outcome": "removed_candidate" if item is None else "answer_range_found" if item["answer_blocks"] else "still_no_answer_range",
                "current_item": item,
                "source_blocks": blocks[max(0, position - 3):min(len(blocks), end_position + 5)],
            })
    return {
        "schema_version": "analysis98-missing-answer-boundaries.v1",
        "index_revision": QUESTION_INDEX_REVISION,
        "historical_index_revision": historical["index_revision"],
        "source_count": len(source_records), "historical_lead_count": len(records),
        "source_hashes_recomputed": True, "provider_calls": 0,
        "personal_state_writes": 0, "teaching_approval": False,
        "counts": dict(counts),
        "answer_provenance_counts": dict(answer_provenance),
        "all_indexed_block_text_matches_source": True,
        "historical_classification_counts": dict(Counter(r["source_review_classification"] for r in records)),
        "outcome_counts": dict(Counter(r["current_outcome"] for r in records)),
        "keyed_difference_fields": comparison_fields,
        "keyed_difference_counts": {k: len(v) for k, v in differences.items()},
        "keyed_differences": differences,
        "source_issues": source_issues,
        "sources": source_records, "historical_leads": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--historical-audit", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--source-issue-report", type=Path)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError("Use a fresh report path")
    if args.source_issue_report is not None and args.source_issue_report.exists():
        raise ValueError("Use a fresh source-issue report path")
    report = audit(args.inventory, args.cache_root, args.historical_audit, args.baseline)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.source_issue_report is not None:
        args.source_issue_report.parent.mkdir(parents=True, exist_ok=True)
        args.source_issue_report.write_text(json.dumps(report["source_issues"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("sources", "historical_leads", "keyed_differences", "source_issues")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

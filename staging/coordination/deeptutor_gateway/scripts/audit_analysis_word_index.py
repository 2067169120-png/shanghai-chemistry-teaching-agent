"""Read only the 98 inventory-bound native caches and report review candidates.

No source documents, private settings, student files, models or central indexes
are opened or changed. Heuristics are review leads, not confirmed split errors.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import (
    CACHE_REVISION,
    MAX_PREVIEW_BYTES,
    WordPreviewCache,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
    QUESTION_INDEX_REVISION,
    index_word_questions,
)

EXPLICIT = re.compile(
    r"^[\s【\[（(]*(?:即学即练|同步练习|随堂练习|针对训练|典例|例题|变式(?:训练|练习)?|例)\s*[0-9０-９一二三四五六七八九十]"
)
ATTRIBUTION = re.compile(r"[（(【\[]([^（）()【】\[\]\n]{2,140})[）)】\]]")
PROVINCES = re.compile(
    r"北京|天津|重庆|浙江|江苏|山东|湖南|湖北|广东|福建|安徽|江西|河南|河北|山西|陕西|四川|云南|贵州|广西|海南|辽宁|吉林|黑龙江|甘肃|青海|宁夏|新疆|内蒙古|西藏|全国"
)
ANSWER_CUE = re.compile(
    r"故选\s*[A-HＡ-Ｈ]|正确答案为|答案应为|本题考查|【(?:解析|详解|答案)】"
)
REFERENCE = re.compile(r"根据上述|上述材料|以上材料|前题|上一题|根据上图|根据前图")


def _finding(kind, package_id, item=None, block=None, **extra):
    return {
        "kind": kind,
        "package_id": package_id,
        "question_key": item.get("key") if item else None,
        "block_index": block.get("index") if block else None,
        "excerpt": str(block.get("text") or "")[:200] if block else "",
        "status": "review_candidate_not_confirmed_error",
        **extra,
    }


def inspect_preview(preview, package_id):
    items = index_word_questions(preview)
    starts = {item["origin_block_start"] for item in items}
    findings = []
    for block in preview["blocks"]:
        if EXPLICIT.match(block.get("text", "")) and block["index"] not in starts:
            owner = next(
                (
                    item
                    for item in items
                    if item["block_start"] <= block["index"] <= item["block_end"]
                ),
                None,
            )
            findings.append(
                _finding(
                    "explicit_marker_not_question_start",
                    package_id,
                    owner,
                    block,
                    owner_unit=owner.get("selection_unit", "printed_question_candidate")
                    if owner
                    else None,
                )
            )
    for item in items:
        blocks = item["question_blocks"]
        question_text = "\n".join(block.get("text", "") for block in blocks)
        if not item["answer_blocks"]:
            findings.append(_finding("no_answer_range", package_id, item, blocks[0]))
        for block in blocks:
            text = block.get("text", "")
            if ANSWER_CUE.search(text):
                findings.append(
                    _finding("answer_language_in_question", package_id, item, block)
                )
            for match in ATTRIBUTION.finditer(text):
                quote = match.group(0)
                if PROVINCES.search(quote) and re.search(
                    r"20\d{2}|模|高[一二三]|高考|联考|期[中末]", quote
                ):
                    findings.append(
                        _finding(
                            "non_shanghai_attribution",
                            package_id,
                            item,
                            block,
                            citation=quote,
                            original_exam_type_may_be_unknown=not bool(
                                re.search(
                                    r"一模|二模|高考|校考|期中|期末|等级考", quote
                                )
                            ),
                        )
                    )
        if not item["context_blocks"] and REFERENCE.search(question_text):
            findings.append(
                _finding(
                    "unlinked_back_reference",
                    package_id,
                    item,
                    blocks[0],
                    may_reference_same_question=True,
                )
            )
        assets = [
            asset
            for block in blocks + item["context_blocks"]
            for asset in block.get("assets", [])
        ]
        if re.search(r"如图|下图|图中|装置图", question_text) and not assets:
            findings.append(
                _finding("image_cue_without_asset", package_id, item, blocks[0])
            )
        if len(blocks) > 35 and item.get("selection_unit") != "theme_big_question":
            findings.append(
                _finding(
                    "long_non_theme_question_range",
                    package_id,
                    item,
                    blocks[0],
                    question_blocks=len(blocks),
                )
            )
    return items, findings


def audit(inventory, cache_root):
    with inventory.open(encoding="utf-8-sig", newline="") as stream:
        rows = [
            row for row in csv.DictReader(stream) if row["document_role"] == "解析版"
        ]
    if len(rows) != 98 or len({row["sha256"] for row in rows}) != 98:
        raise ValueError(
            "Audit requires exactly the 98 unique analysis-version inventory rows"
        )
    cache = WordPreviewCache(cache_root)
    sources, findings, counts = [], [], Counter()
    for row in sorted(rows, key=lambda row: row["package_id"]):
        name = Path(row["output_relative_path"]).name
        path = cache._path(row["sha256"], name)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing safe preview cache for " + row["package_id"])
        with gzip.open(path, "rb") as stream:
            data = stream.read(MAX_PREVIEW_BYTES + 1)
        if len(data) > MAX_PREVIEW_BYTES:
            raise ValueError("Preview exceeds read bound")
        record = json.loads(data)
        preview = record.get("preview")
        if record.get("cache_revision") != CACHE_REVISION or not cache._valid(
            preview, row["sha256"], name
        ):
            raise ValueError("Stale or mismatched cache for " + row["package_id"])
        items, candidate_findings = inspect_preview(preview, row["package_id"])
        findings.extend(candidate_findings)
        source_counts = Counter(
            {
                "questions": len(items),
                "with_answer_range": sum(bool(item["answer_blocks"]) for item in items),
                "with_shared_context": sum(
                    bool(item["context_blocks"]) for item in items
                ),
                "theme_units": sum(
                    item.get("selection_unit") == "theme_big_question" for item in items
                ),
                "boundary_export_ready": sum(item["export_ready"] for item in items),
            }
        )
        counts.update(source_counts)
        sources.append(
            {
                "package_id": row["package_id"],
                "source_sha256": row["sha256"],
                "source_name": name,
                "counts": dict(source_counts),
                "candidate_counts": dict(
                    Counter(f["kind"] for f in candidate_findings)
                ),
            }
        )
    return {
        "schema_version": "word-analysis98-index-review.v1",
        "index_revision": QUESTION_INDEX_REVISION,
        "scope": "98 inventory-bound cached previews; source byte hashes not recomputed in this audit",
        "source_count": len(sources),
        "counts": dict(counts),
        "candidate_counts": dict(Counter(f["kind"] for f in findings)),
        "sources": sources,
        "findings": findings,
        "confirmed_error_count": None,
        "teaching_approval": False,
        "original_exam_authority_inferred": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError("Use a fresh audit report path")
    report = audit(args.inventory, args.cache_root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"sources", "findings"}
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

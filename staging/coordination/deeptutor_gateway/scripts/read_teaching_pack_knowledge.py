"""Read cached source paragraphs for semantic review without model calls."""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("packages", nargs="+")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--min-length", type=int, default=25)
    parser.add_argument("--max-block-length", type=int, default=700)
    parser.add_argument("--blocks", help="Inclusive source block start:end, without question exclusion")
    args = parser.parse_args()
    from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import (
        WordPreviewCache,
    )
    from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
        index_word_questions,
    )
    inventory = ROOT / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/archive_inventory.csv"
    with inventory.open(encoding="utf-8-sig") as stream:
        rows = [r for r in csv.DictReader(stream) if r["document_role"] == "解析版" and r["package_id"] in args.packages]
    for row in rows:
        path = inventory.parent / row["output_relative_path"]
        preview = WordPreviewCache(args.state_root / "word-question-previews").load(path.read_bytes(), path.name)
        if preview is None:
            raise RuntimeError("No verified source preview")
        questions = index_word_questions(preview)
        excluded = {b["index"] for q in questions for b in q["question_blocks"] + q["answer_blocks"]}
        print("SOURCE", row["package_id"], path.name, row["sha256"])
        for block in preview["blocks"]:
            if args.blocks:
                start, end = map(int, args.blocks.split(":"))
                if not start <= block["index"] <= end:
                    continue
            content = block["text"]
            clean = re.sub(r"【待查看原文：[^】]+】", "", content).strip()
            if not args.blocks and (block["index"] in excluded or len(clean) < args.min_length):
                continue
            displayed = content[:args.max_block_length]
            if len(displayed) < len(content):
                displayed += " [TRUNCATED: inspect full block before using omitted content]"
            print(str(block["index"]) + " " + displayed)


if __name__ == "__main__":
    main()

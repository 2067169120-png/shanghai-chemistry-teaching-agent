"""Persist evidence-labelled attributes for the user's 98 analysis sources."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
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
    from integrations.deeptutor_shchem_v1.desktop_word_preview_cache import (
        WordPreviewCache,
    )
    from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
        WordQuestionAttributeStore,
        load_attribute_catalog,
        suggest_attributes,
    )
    from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
        index_word_questions,
    )

    cache = WordPreviewCache(args.state_root / "word-question-previews")
    store = WordQuestionAttributeStore(args.state_root)
    taxonomy = load_attribute_catalog(ROOT)
    summary, attributes = [], []
    for path, metadata in verified_sources(args.inventory):
        preview = cache.load(path.read_bytes(), path.name)
        if preview is None:
            raise RuntimeError("Warm and verify native previews before annotating")
        title = re.sub(r"【待查看原文：[^】]+】", "", preview["blocks"][0]["text"]).strip()
        origin = {"collection_name": "上好课2026上海化学一轮复习讲练测", "package_id": metadata["package_id"],
                  "source_name": path.name, "document_role": "解析版", "lecture_topic": title,
                  "usage_context": "高三一轮复习；上海专用资料包；原题考试身份另据题文标注"}
        suggestions = [suggest_attributes(q, origin, taxonomy) for q in index_word_questions(preview)]
        saved = store.save_many(suggestions)
        attributes.extend(saved)
        summary.append({"package_id": metadata["package_id"], "question_count": len(saved)})
        print(json.dumps(summary[-1]), flush=True)
    report = {"source_count": len(summary), "question_count": len(attributes),
              "primary_knowledge_counts": dict(Counter(a["primary_knowledge"]["id"] for a in attributes)),
              "original_exam_type_counts": dict(Counter(a["original_source"]["exam_type"]["value"] for a in attributes)),
              "mapped_to_textbook_section": sum(bool(a["curriculum_candidates"]) for a in attributes),
              "answer_status_counts": dict(Counter(a["answer_status"]["value"] for a in attributes)),
              "annotation_source_counts": dict(Counter(a["annotation_source"] for a in attributes)),
              "provider_calls": 0, "teacher_confirmed": False, "sources": summary}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "sources"}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()

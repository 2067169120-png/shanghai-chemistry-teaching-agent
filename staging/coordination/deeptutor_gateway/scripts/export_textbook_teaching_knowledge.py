"""Export existing concise candidate textbook knowledge, not original textbook pages.

This offline derived reference is user-authorized for sharing. It does not
reclassify the central candidate records, grant teaching authority, or claim a
new review of their chemistry. The input statements are already short AI-written
concept summaries; only their presentation is reorganized here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

SOURCE_DIR = "sh-chem-db/kb/textbook_knowledge_map_v1_2026-08-28"
SCHEMA = "shchem.user-authorized-textbook-reference.v1"
GATES = (
    "human_reviewed",
    "retrieval_ready",
    "teaching_use_allowed",
    "generation_allowed",
    "publication_allowed",
)
DIMENSIONS = {
    "K": "knowledge_points",
    "A": "abilities",
    "C": "contexts",
    "R": "response_types",
    "D": "difficulty",
}
FILES = (
    "concepts.jsonl",
    "manifest.json",
    "textbook_directory.json",
    "coverage_report.json",
    "blocked_items.jsonl",
)
PRIVATE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|file://|/(?:Users|home|mnt)/|\bsk-[A-Za-z0-9_-]{20,}|\bBearer\s+[A-Za-z0-9._-]{16,})",
    re.IGNORECASE,
)


def _json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _safe(value):
    if isinstance(value, str) and PRIVATE.search(value):
        raise ValueError(
            "Private machine path or credential-like content in derived export"
        )
    if isinstance(value, dict):
        for key, item in value.items():
            _safe(key)
            _safe(item)
    elif isinstance(value, list):
        for item in value:
            _safe(item)


def _candidate(value):
    if (
        value.get("candidate_only") is not True
        or value.get("review_status") != "candidate-only"
        or any(value.get(key) is not False for key in GATES)
    ):
        raise ValueError(
            "Source candidate status differs from this derived-export contract"
        )


def _summary(statement):
    if (
        not isinstance(statement, str)
        or not statement.strip()
        or len(statement) > 240
        or "\n" in statement
    ):
        raise ValueError(
            "Expected a concise existing concept summary, not a chapter or full question"
        )
    _safe(statement)
    # Reframe the existing short, non-verbatim candidate summary as reading
    # notes. Preserve qualifiers, formulae and condition order without inventing
    # chemistry through broad synonym replacement or fresh model calls.
    clauses = [part.strip(" 。") for part in statement.split("；") if part.strip(" 。")]
    return "学习要点：" + "；同时注意：".join(clauses) + "。"


def build_export(source_root, taxonomy_path):
    root = Path(source_root)
    raw_inputs = {name: (root / name).read_bytes() for name in FILES}
    concepts = [
        json.loads(line)
        for line in raw_inputs["concepts.jsonl"].decode("utf-8-sig").splitlines()
        if line.strip()
    ]
    manifest = json.loads(raw_inputs["manifest.json"])
    directory = json.loads(raw_inputs["textbook_directory.json"])
    upstream_coverage = json.loads(raw_inputs["coverage_report.json"])
    blockers = [
        json.loads(line)
        for line in raw_inputs["blocked_items.jsonl"].decode("utf-8-sig").splitlines()
        if line.strip()
    ]
    taxonomy_raw = Path(taxonomy_path).read_bytes()
    taxonomy = json.loads(taxonomy_raw)
    _candidate(directory)
    sources = {
        source["source_id"]: source
        for source in manifest["sources"]
        if source["kind"] == "textbook"
    }
    chapters, sections, volumes = {}, {}, {}
    for volume in directory["volumes"]:
        volumes[volume["volume_id"]] = volume
        for chapter in volume["chapters"]:
            chapters[chapter["chapter_id"]] = chapter
            for section in chapter["sections"]:
                sections[section["section_key"]] = section
    labels = {
        row["id"]: row["name"]
        for rows in taxonomy["dimensions"].values()
        for row in rows
    }
    records, ids = [], set()
    for concept in concepts:
        _candidate(concept)
        concept_id = concept["concept_id"]
        if concept_id in ids:
            raise ValueError("Duplicate concept identity")
        ids.add(concept_id)
        source = sources[concept["volume_id"]]
        volume = volumes[concept["volume_id"]]
        chapter = chapters[concept["chapter_id"]]
        section_key = concept.get("section_key")
        section = sections.get(section_key)
        if section_key is not None and (
            section is None or section["chapter_id"] != chapter["chapter_id"]
        ):
            raise ValueError("Invalid explicit section-to-chapter relationship")
        if (
            chapter["volume_id"] != volume["volume_id"]
            or source["sha256"] != concept["source_sha256"]
            or volume["source_sha256"] != source["sha256"]
        ):
            raise ValueError("Source identity or chapter relationship mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
            raise ValueError("Invalid source digest")
        pages = concept["pdf_pages"]
        if (
            not isinstance(pages, list)
            or not pages
            or any(
                type(page) is not int or not 1 <= page <= source["page_count"]
                for page in pages
            )
        ):
            raise ValueError("Concept pages fall outside the recorded textbook")
        tags = {}
        for dimension, tag_ids in concept["tags"].items():
            if dimension not in DIMENSIONS or not isinstance(tag_ids, list):
                raise ValueError("Unknown tag dimension")
            tags[dimension] = [
                {"id": tag, "label": labels.get(tag, "unknown")} for tag in tag_ids
            ]
        record = {
            "schema_version": SCHEMA,
            "id": "TEXT-" + concept_id,
            "concept_id": concept_id,
            "title": concept["title"],
            "summary": _summary(concept["statement"]),
            "derivation_method": "existing_ai_concept_summary_reorganized_no_new_chemistry_review",
            "tags": tags,
            "grade": "unknown",
            "exam_type": "not_applicable_textbook_reference",
            "curriculum": {
                "volume_id": volume["volume_id"],
                "volume_title": volume["volume_title"],
                "chapter_id": chapter["chapter_id"],
                "chapter_title": chapter["chapter_title"],
                "section_key": section_key,
                "section_title": section["section_title"] if section else None,
                "supplement_node_key": concept.get("supplement_node_key"),
                "mapping_status": "existing_candidate_section_mapping"
                if section
                else "supplement_node_not_numbered_section",
            },
            "source": {
                "title": source["title"],
                "sha256": source["sha256"],
                "pdf_pages": pages,
                "printed_pages": None,
                "evidence_refs": concept.get("evidence_refs", []),
                "concept_record_sha256": _sha(_json_bytes(concept)),
            },
            "source_status": {
                "kind": "existing_candidate_summary_not_newly_verified",
                "review_status": concept["review_status"],
                "verification_status_claim": concept.get(
                    "verification_status", "unknown"
                ),
                "source_file_status_claim": source.get("source_status", "unknown"),
                "fresh_source_review_performed": False,
            },
            "user_authorized_derived_export": True,
            "export_authorization_scope": "nonformal_derived_reference_only_not_original_source_distribution_or_central_promotion",
            "review_status": concept["review_status"],
            "candidate_only": True,
            **{key: concept[key] for key in GATES},
        }
        _safe(record)
        records.append(record)
    records.sort(key=lambda row: row["concept_id"])
    concepts_by_volume = Counter(row["curriculum"]["volume_id"] for row in records)
    covered_sections = {
        row["curriculum"]["section_key"]
        for row in records
        if row["curriculum"]["section_key"]
    }
    gaps = {
        "numbered_sections_without_concepts": sorted(set(sections) - covered_sections),
        "concepts_without_numbered_section": [
            row["concept_id"]
            for row in records
            if row["curriculum"]["section_key"] is None
        ],
        "grade_unknown_count": len(records),
        "concept_printed_pages_unknown_count": len(records),
        "upstream_known_gaps_as_claims": upstream_coverage.get("known_gaps", []),
        "upstream_blockers": [
            {
                key: row.get(key)
                for key in (
                    "blocker_id",
                    "volume_id",
                    "scope_node_key",
                    "issue",
                    "status",
                    "evidence_refs",
                )
            }
            for row in blockers
        ],
        "not_exported_in_this_concept_package": [
            "原教材正文、扫描页及图片",
            "整份Word讲义或教案",
            "全部实验、方程式实体、典型任务与易错点库",
            "教师人工复核结论",
            "当前上海统一教学进度或考试频率判断",
        ],
    }
    knowledge_raw = b"".join(_json_bytes(record) + b"\n" for record in records)
    coverage = {
        "schema_version": SCHEMA,
        "title": "五册教材已有简明概念的派生参考包",
        "scope": "仅重整现有候选概念摘要，不声称本轮重新阅读或复核五册教材，也不声称完成全部教材任务类型蒸馏。",
        "counts": {
            "source_concepts": len(concepts),
            "exported_readable_concepts": len(records),
            "volumes": len(concepts_by_volume),
            "chapters": len({row["curriculum"]["chapter_id"] for row in records}),
            "numbered_sections_with_concepts": len(covered_sections),
            "directory_numbered_sections": len(sections),
            "human_reviewed_concepts": 0,
            "upstream_blockers": len(blockers),
        },
        "by_volume": [
            {
                "volume_id": key,
                "title": sources[key]["title"],
                "concept_count": concepts_by_volume[key],
                "source_sha256": sources[key]["sha256"],
                "source_pdf_page_count": sources[key]["page_count"],
            }
            for key in sorted(concepts_by_volume)
        ],
        "input_fingerprints": [
            {"name": name, "sha256": _sha(raw)} for name, raw in raw_inputs.items()
        ]
        + [{"name": "knowledge_taxonomy.json", "sha256": _sha(taxonomy_raw)}],
        "knowledge_sha256": _sha(knowledge_raw),
        "coverage_gaps": gaps,
        "quality_checks": {
            "all_records_have_readable_content": True,
            "long_source_statements_rejected": True,
            "original_pages_or_documents_included": False,
            "machine_absolute_paths_or_credentials_included": False,
            "original_textbook_similarity_review": "not_performed_original_pdf_not_read_in_this_export",
            "fresh_chemistry_review": "not_performed_existing_candidate_claims_preserved",
        },
        "user_authorized_derived_export": True,
        "source_status": "candidate_only_claims_preserved",
        "review_status": "candidate-only",
        "candidate_only": True,
        **dict.fromkeys(GATES, False),
    }
    _safe(coverage)
    return records, coverage, knowledge_raw


def export(source_root, taxonomy_path, output_root):
    source = Path(source_root).resolve()
    output = Path(output_root).resolve()
    if (
        output == source
        or output.is_relative_to(source)
        or source.is_relative_to(output)
    ):
        raise ValueError(
            "Derived output must be separate from the central knowledge source"
        )
    _records, coverage, raw = build_export(source, taxonomy_path)
    output.mkdir(parents=True, exist_ok=True)
    (output / "knowledge.jsonl").write_bytes(raw)
    (output / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return coverage


def main():
    workspace = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=workspace / SOURCE_DIR)
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=workspace / "sh-chem-db/kb/knowledge_taxonomy.json",
    )
    parser.add_argument("--output", type=Path, default=workspace / "knowledge/textbook")
    args = parser.parse_args()
    result = export(args.source_root, args.taxonomy, args.output)
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()

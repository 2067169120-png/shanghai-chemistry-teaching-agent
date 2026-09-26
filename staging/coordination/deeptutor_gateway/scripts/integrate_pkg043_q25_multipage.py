"""Emit a narrow reviewed Q25 patch; verify live integration without promotion.

The frozen packet and pre-integration backup are inputs, never rewritten.
Only the patch mode emits edits; applying them requires the caller's patch tool.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    HandoutCandidateError,
    HandoutCandidateService,
    load_reader,
)
from integrations.deeptutor_shchem_v1.desktop_handout_practice import (
    NativeParagraphs,
)

PRODUCT = ROOT / "sh-chem-db/kb/teaching_handout_question_slices_v1_2026-08-28"
NATIVE = PRODUCT / "native_ooxml_v2"
BATCH_ID = "NATIVE-BATCH-NV2W2-PKG043-A02"
BATCH = NATIVE / "batches" / BATCH_ID
QA = ROOT / "runtime/deeptutor_shchem/qa/pkg043-q25-multipage-integration-20260909"
PACKET = (
    ROOT
    / "staging/coordination/deeptutor_gateway/handout_multipage_answer_import_20260909"
)
Q25 = "PQ-9844af38112b410b"
OLD_CLASS = "hybrid_visual_required"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def pipeline():
    spec = importlib.util.spec_from_file_location(
        "_q25_integration_pipeline", NATIVE / "scripts/native_fastlane_pipeline.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def expected():
    before = read_rows(QA / "batch-before/question_candidates.jsonl")
    assert len(before) == 68
    rows = copy.deepcopy(before)
    matches = [row for row in rows if row["candidate_id"] == Q25]
    assert len(matches) == 1
    q25 = matches[0]
    assert q25 == read_json(PACKET / "fixtures/q25_live_candidate_unchanged.json")
    assert q25["completeness"]["classification"] == OLD_CLASS
    assert q25["completeness"]["reasons"] == [
        "answer_page_binding_requires_visual_confirmation"
    ]
    q25["completeness"].update(classification="native_text_complete", reasons=[])
    assert q25 == read_json(PACKET / "fixtures/q25_reviewed_candidate_copy.json")
    # Keep historical automatic page-binding findings as source provenance.
    # The reviewed completeness classification does not assert human review.
    q25.update(
        fastlane_candidate=True,
        workbench_import_status="candidate_ready_for_separate_import_adapter",
    )
    module = pipeline()
    assert q25["claims"] == module.CLOSED_GATES
    old_imports = read_rows(QA / "batch-before/candidate_import_records.jsonl")
    assert len(old_imports) == 28 and all(
        row["import_record_id"] != Q25 for row in old_imports
    )
    imports = old_imports + [module.candidate_import_record(q25)]
    imports.sort(key=lambda row: row["printed_question"]["sequence_in_document"])
    fastlane = [row for row in rows if row["fastlane_candidate"] is True]
    hybrid = [row for row in rows if row["completeness"]["classification"] == OLD_CLASS]
    assert len(fastlane) == len(imports) == 29 and len(hybrid) == 39
    assert sum(len(row["printed_question"]["atomic_parts"]) for row in rows) == 85
    summary = read_json(QA / "batch-before/batch_summary.json")
    counts = Counter(row["completeness"]["classification"] for row in rows)
    for classification in summary["classification_counts"]:
        summary["classification_counts"][classification] = counts[classification]
    summary["blocker_reason_counts"] = dict(
        sorted(
            Counter(
                reason for row in rows for reason in row["completeness"]["reasons"]
            ).items()
        )
    )
    summary["fastlane_candidate_count"] = len(fastlane)
    summary["candidate_import_records_written"] = len(imports)
    summary["hybrid_visual_question_count"] = len(hybrid)
    summary["per_package"]["PKG-043"]["classification_counts"] = copy.deepcopy(
        summary["classification_counts"]
    )
    summary["per_package"]["PKG-043"]["candidate_import_records_written"] = len(imports)
    summary["validation"]["report_path"] = f"batches/{BATCH_ID}/validation_report.json"
    assert summary["claims"] == module.CLOSED_GATES
    # Read-only verification of unchanged original document and rendered pages.
    source = q25["source_document"]
    assert (
        module.sha256_file(module.INTAKE_ROOT / source["relative_path"])
        == source["sha256"]
    )
    answer = q25["answer_alignment"]
    anchors = q25["page_binding"]["pages"] + answer["solution_page_binding"]["pages"]
    assert [p["page"] for p in anchors] == [5, 8, 9]
    for anchor in anchors:
        path = (PRODUCT / anchor["path"]).resolve()
        assert path.is_relative_to((PRODUCT / "batches").resolve())
        assert module.sha256_file(path) == anchor["sha256"]
    return {
        "question_candidates.jsonl": rows,
        "candidate_import_records.jsonl": imports,
        "fastlane_candidates.jsonl": fastlane,
        "hybrid_visual_queue.jsonl": hybrid,
        "batch_summary.json": summary,
    }


def patch():
    parts = ["*** Begin Patch"]
    for name, value in expected().items():
        path = BATCH / name
        # Reject unrelated concurrent edits instead of overwriting them.
        live = read_rows(path) if name.endswith("jsonl") else read_json(path)
        backup = QA / "batch-before" / name
        original = read_rows(backup) if name.endswith("jsonl") else read_json(backup)
        assert live == original or live == value, f"Concurrent change: {name}"
        old = path.read_text(encoding="utf-8").splitlines()
        new = (
            [
                json.dumps(
                    row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                for row in value
            ]
            if name.endswith("jsonl")
            else json.dumps(value, ensure_ascii=False, indent=2).splitlines()
        )
        diff = list(difflib.unified_diff(old, new, n=1, lineterm=""))[2:]
        if diff:
            parts.append("*** Update File: " + path.relative_to(ROOT).as_posix())
            parts.extend("@@" if line.startswith("@@") else line for line in diff)
    return "\n".join(parts + ["*** End Patch"])


def verify():
    values = expected()
    for name, value in values.items():
        actual = (
            read_rows(BATCH / name)
            if name.endswith("jsonl")
            else read_json(BATCH / name)
        )
        assert actual == value, name
    before = read_rows(QA / "batch-before/question_candidates.jsonl")
    after = values["question_candidates.jsonl"]
    assert [r for r in before if r["candidate_id"] != Q25] == [
        r for r in after if r["candidate_id"] != Q25
    ]
    assert read_rows(QA / "batch-before/candidate_import_records.jsonl") == [
        r
        for r in values["candidate_import_records.jsonl"]
        if r["import_record_id"] != Q25
    ]
    reader = load_reader(ROOT)
    catalog = reader.catalog()
    assert len(catalog["items"]) == 285 and not catalog["warnings"]
    item = next(
        i
        for i in catalog["items"]
        if (i.get("editable_source") or {}).get("candidate_id") == Q25
    )
    assert item["candidate_only"] is True
    assert HandoutCandidateService._decorate(item, {})["practice_eligible"] is True
    prior = read_json(QA / "catalog-before.json")["revisions"]
    assert {
        i["key"]: i["revision"] for i in catalog["items"] if i["key"] != item["key"]
    } == {key: rev for key, rev in prior.items() if key != item["key"]}
    assert item["revision"] != prior[item["key"]]
    service = HandoutCandidateService(ROOT, None)
    for role in ("question", "answer"):
        for index, page in enumerate(item[role + "_pages"]):
            content = service.page_bytes(item["key"], item["revision"], role, index)
            assert hashlib.sha256(content).hexdigest() == page["sha256"]
    try:
        service.page_bytes(item["key"], prior[item["key"]], "question", 0)
    except HandoutCandidateError as exc:
        assert exc.code == "handout_page_stale"
    else:
        raise AssertionError("Old revision accepted")
    native = NativeParagraphs(ROOT)
    role_counts = Counter()
    for candidate in catalog["items"]:
        if (
            candidate["batch_id"] == BATCH_ID
            and HandoutCandidateService._decorate(candidate, {})["practice_eligible"]
        ):
            for role in ("question", "answer"):
                assert native.read(candidate, role)
                role_counts[role] += 1
    assert dict(role_counts) == {"question": 29, "answer": 29}
    return {
        "valid": True,
        "catalog_count": len(catalog["items"]),
        "catalog_native_text_complete": sum(
            i["classification"] == "native_text_complete" for i in catalog["items"]
        ),
        "batch_candidates": len(after),
        "batch_atomic_parts": 85,
        "batch_native": 29,
        "batch_hybrid": 39,
        "batch_import_records": 29,
        "derived_fastlane_rows_before": len(
            read_rows(QA / "batch-before/fastlane_candidates.jsonl")
        ),
        "derived_fastlane_rows_after": len(values["fastlane_candidates.jsonl"]),
        "other_67_candidate_records_unchanged": True,
        "old_28_import_records_unchanged": True,
        "other_284_reader_revisions_unchanged": True,
        "native_role_reads": dict(role_counts),
        "q25_page_reads": 3,
        "old_revision_rejected": True,
        "q25_key": item["key"],
        "q25_revision": item["revision"],
        "candidate_only": True,
        "human_review_completed": False,
        "answer_correctness_verified": False,
        "formal_gates_changed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("patch", "verify"))
    args = parser.parse_args()
    print(
        patch()
        if args.mode == "patch"
        else json.dumps(verify(), ensure_ascii=False, indent=2)
    )

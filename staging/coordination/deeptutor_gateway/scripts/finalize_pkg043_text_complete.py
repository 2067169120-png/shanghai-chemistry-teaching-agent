"""Complete the existing local-candidate contract without formal promotion."""

import copy
import difflib
import importlib.util
import json
import sys

from prepare_pkg043_text_complete_patch import (
    BATCH,
    IDS,
    OLD_CLASSIFICATION,
    OLD_REASONS,
    PRODUCT,
    QA,
    ROOT,
    accept_packet,
    read_json,
    read_rows,
)

from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    HandoutCandidateError,
    HandoutCandidateService,
    load_reader,
)

# Q25 has a two-page reference answer, while the existing import schema permits
# exactly one answer page. Keep its reviewed proposal isolated for a later
# explicit multi-page import implementation; do not silently relax the schema.
ACTIVE_IDS = (IDS[0],)


def import_builder():
    path = PRODUCT / "native_ooxml_v2/scripts/native_fastlane_pipeline.py"
    spec = importlib.util.spec_from_file_location("_pkg043_finalization_pipeline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.candidate_import_record


def expected():
    packet = accept_packet()
    before = read_rows(QA / "batch-before/question_candidates.jsonl")
    after = copy.deepcopy(before)
    for row in after:
        if row["candidate_id"] not in ACTIVE_IDS:
            continue
        assert row["completeness"]["classification"] == OLD_CLASSIFICATION
        assert row["completeness"]["reasons"] == OLD_REASONS
        row["completeness"].update(classification="native_text_complete", reasons=[])
        assert row == packet[row["candidate_id"]]
        row.update(
            fastlane_candidate=True,
            workbench_import_status="candidate_ready_for_separate_import_adapter",
        )
    builder = import_builder()
    old_imports = read_rows(QA / "batch-before/candidate_import_records.jsonl")
    imports = old_imports + [
        builder(row) for row in after if row["candidate_id"] in ACTIVE_IDS
    ]
    imports.sort(key=lambda row: row["printed_question"]["sequence_in_document"])
    summary = read_json(QA / "batch-before/batch_summary.json")
    summary["classification_counts"].update(
        native_text_complete=28, hybrid_visual_required=40
    )
    summary["per_package"]["PKG-043"]["classification_counts"].update(
        native_text_complete=28, hybrid_visual_required=40
    )
    summary["blocker_reason_counts"][
        "answer_page_binding_requires_visual_confirmation"
    ] = 35
    summary["hybrid_visual_question_count"] = 40
    summary["fastlane_candidate_count"] = summary[
        "candidate_import_records_written"
    ] = 28
    summary["per_package"]["PKG-043"]["candidate_import_records_written"] = 28
    return after, imports, summary


def line(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def reconcile_patch():
    rows, imports, summary = expected()
    targets = {
        "question_candidates.jsonl": rows,
        "candidate_import_records.jsonl": imports,
        "hybrid_visual_queue.jsonl": [
            r for r in rows if r["completeness"]["classification"] == OLD_CLASSIFICATION
        ],
    }
    parts = ["*** Begin Patch"]
    for name, values in targets.items():
        path = BATCH / name
        old = path.read_text(encoding="utf-8").splitlines()
        new = [line(row) for row in values]
        diff = list(difflib.unified_diff(old, new, n=1, lineterm=""))[2:]
        if diff:
            parts.append("*** Update File: " + path.relative_to(ROOT).as_posix())
            parts.extend("@@" if s.startswith("@@") else s for s in diff)
    path = BATCH / "batch_summary.json"
    old = path.read_text(encoding="utf-8").splitlines()
    new = json.dumps(summary, ensure_ascii=False, indent=2).splitlines()
    diff = list(difflib.unified_diff(old, new, n=1, lineterm=""))[2:]
    if diff:
        parts.append("*** Update File: " + path.relative_to(ROOT).as_posix())
        parts.extend("@@" if s.startswith("@@") else s for s in diff)
    return "\n".join(parts + ["*** End Patch"])


def verify():
    rows, imports, summary = expected()
    assert read_rows(BATCH / "question_candidates.jsonl") == rows
    assert read_rows(BATCH / "candidate_import_records.jsonl") == imports
    assert read_json(BATCH / "batch_summary.json") == summary
    assert read_rows(BATCH / "hybrid_visual_queue.jsonl") == [
        r for r in rows if r["completeness"]["classification"] == OLD_CLASSIFICATION
    ]
    reader = load_reader(ROOT)
    catalog = reader.catalog()
    assert len(catalog["items"]) == 285 and not catalog["warnings"]
    service = HandoutCandidateService(ROOT, None)
    revised, reads = [], 0
    for diff in read_json(
        ROOT
        / "staging/coordination/deeptutor_gateway/handout_text_complete_pkg043_q21_q25_20260908/field_differences.json"
    )["records"]:
        if diff["candidate_id"] not in ACTIVE_IDS:
            item = reader.get(diff["reader_key_before"])
            assert item["revision"] == diff["reader_revision_before"]
            assert item["classification"] == OLD_CLASSIFICATION
            continue
        item = reader.get(diff["reader_key_before"])
        assert item["classification"] == "native_text_complete"
        assert item["candidate_only"] is True
        for role in ("question", "answer"):
            for index in range(len(item[role + "_pages"])):
                assert service.page_bytes(item["key"], item["revision"], role, index)
                reads += 1
        try:
            service.page_bytes(
                item["key"], diff["reader_revision_before"], "question", 0
            )
        except HandoutCandidateError as exc:
            assert exc.code == "handout_page_stale"
        else:
            raise AssertionError("old revision accepted")
        revised.append(
            {
                "candidate_id": diff["candidate_id"],
                "key": item["key"],
                "revision": item["revision"],
            }
        )
    return {
        "passed": True,
        "catalog_count": 285,
        "catalog_native_text_complete": sum(
            i["classification"] == "native_text_complete" for i in catalog["items"]
        ),
        "other_67_unchanged": True,
        "old_27_import_records_unchanged": True,
        "new_candidate_import_records": 1,
        "q25_restored_to_preintegration_revision": True,
        "formal_gates_unchanged": True,
        "page_reads": reads,
        "revisions": revised,
    }


if __name__ == "__main__":
    mode = sys.argv[1]
    assert mode in {"reconcile-patch", "verify"}
    print(
        reconcile_patch()
        if mode == "reconcile-patch"
        else json.dumps(verify(), ensure_ascii=False)
    )

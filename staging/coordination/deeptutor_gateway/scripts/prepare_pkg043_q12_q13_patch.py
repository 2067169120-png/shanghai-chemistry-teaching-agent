"""Emit a narrow apply_patch, or verify the live Q12/Q13 page-boundary update."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json

from prepare_pkg043_boundary_patch import BATCH, PRODUCT, ROOT, read_json, read_rows

from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    HandoutCandidateError,
    HandoutCandidateService,
    load_reader,
)

PACKET = (
    ROOT
    / "staging/coordination/deeptutor_gateway/handout_boundary_pkg043_q12_q13_20260908"
)
QA = ROOT / "runtime/deeptutor_shchem/qa/pkg043-q12-q13-integration-20260908"
IDS = ("PQ-a1a5ddf0221d6a0d", "PQ-822595038de5b27b")
FILES = ("question_candidates.jsonl", "hybrid_visual_queue.jsonl")


def accept():
    for name, record in read_json(PACKET / "file_hashes.json")["files"].items():
        data = (PACKET / name).read_bytes()
        assert (
            len(data) == record["bytes"]
            and hashlib.sha256(data).hexdigest() == record["sha256"]
        )
    candidates = read_rows(PACKET / "boundary_corrected_candidates.jsonl")
    assert [row["candidate_id"] for row in candidates] == list(IDS)
    for row in candidates:
        for anchor in (
            row["page_binding"]["pages"]
            + row["answer_alignment"]["solution_page_binding"]["pages"]
        ):
            path = (PRODUCT / anchor["path"]).resolve()
            assert path.is_relative_to(PRODUCT / "batches")
            assert hashlib.sha256(path.read_bytes()).hexdigest() == anchor["sha256"]
    return {row["candidate_id"]: row for row in candidates}


def expected(before):
    values = copy.deepcopy(before)
    q12 = values[IDS[0]]["page_binding"]
    assert [p["page"] for p in q12["pages"]] == [2, 3]
    q12["pages"] = q12["pages"][:1]
    q12["cross_page"] = False
    q12["reasons"].remove("cross_page_candidate_requires_visual_confirmation")
    q13 = values[IDS[1]]["answer_alignment"]["solution_page_binding"]
    assert [p["page"] for p in q13["pages"]] == [5]
    fourth_page = values[IDS[0]]["answer_alignment"]["solution_page_binding"]["pages"][
        0
    ]
    assert fourth_page["page"] == 4
    q13["pages"] = [copy.deepcopy(fourth_page)] + q13["pages"]
    q13["cross_page"] = True
    return values


def patch():
    packet = accept()
    before = {r["candidate_id"]: r for r in read_rows(BATCH / FILES[0])}
    assert len(before) == 68
    updated = expected(before)
    assert all(packet[key] == updated[key] for key in IDS)
    parts = ["*** Begin Patch"]
    for name in FILES:
        path = BATCH / name
        parts.append(f"*** Update File: {path.relative_to(ROOT).as_posix()}")
        for key in IDS:
            lines = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["candidate_id"] == key
            ]
            assert len(lines) == 1 and json.loads(lines[0]) == before[key]
            parts.extend(
                [
                    "@@",
                    "-" + lines[0],
                    "+"
                    + json.dumps(
                        packet[key],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ]
            )
    return "\n".join(parts + ["*** End Patch"])


def verify():
    packet = accept()
    before = {r["candidate_id"]: r for r in read_rows(QA / "batch-before" / FILES[0])}
    after_rows = read_rows(BATCH / FILES[0])
    after = {r["candidate_id"]: r for r in after_rows}
    assert after == expected(before)
    assert all(after[key] == packet[key] for key in IDS)
    assert read_rows(BATCH / FILES[1]) == [
        r
        for r in after_rows
        if r["completeness"]["classification"] == "hybrid_visual_required"
    ]
    reader = load_reader(ROOT)
    catalog = reader.catalog()
    assert len(catalog["items"]) == 285 and not catalog["warnings"]
    for row in read_json(PACKET / "field_differences.json")["records"]:
        item = reader.get(row["reader_key_after"])
        assert item["revision"] == row["reader_revision_after"]
        assert item["classification"] == "hybrid_visual_required"
        service = HandoutCandidateService(ROOT, None)
        for role in ("question", "answer"):
            for index, _anchor in enumerate(item[role + "_pages"]):
                assert service.page_bytes(item["key"], item["revision"], role, index)
        try:
            service.page_bytes(
                item["key"], row["reader_revision_before"], "question", 0
            )
        except HandoutCandidateError as exc:
            assert exc.code == "handout_page_stale"
        else:
            raise AssertionError("Old evidence revision was accepted")
    return {
        "status": "passed",
        "changed_candidates": ["12", "13"],
        "other_66_candidates_unchanged": True,
        "q12_question_pages": [2],
        "q12_answer_pages": [4],
        "q13_question_pages": [3],
        "q13_answer_pages": [4, 5],
        "catalog_count": 285,
        "keys_unchanged": True,
        "revisions_updated": True,
        "claims_and_text_unchanged": True,
        "all_five_page_reads_passed": True,
        "stale_page_reads_rejected": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("patch", "verify"))
    args = parser.parse_args()
    print(patch() if args.mode == "patch" else json.dumps(verify()))

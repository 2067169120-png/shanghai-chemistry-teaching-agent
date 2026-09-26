"""Emit or verify the narrow PKG043 Q21/Q25 text-completeness patch.

The ``patch`` mode is read-only: it prints an ``apply_patch`` payload and
never edits the live batch.  The caller must create an external backup before
applying that payload and refresh derived manifests afterwards.
"""

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
    / "staging/coordination/deeptutor_gateway/"
    "handout_text_complete_pkg043_q21_q25_20260908"
)
QA = ROOT / "runtime/deeptutor_shchem/qa/pkg043-text-complete-integration-20260908"
IDS = ("PQ-7f7a6a3c00972af7", "PQ-9844af38112b410b")
FILES = ("question_candidates.jsonl", "hybrid_visual_queue.jsonl")
OLD_CLASSIFICATION = "hybrid_visual_required"
OLD_REASONS = ["answer_page_binding_requires_visual_confirmation"]
NEW_CLASSIFICATION = "native_text_complete"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_closed_claims(row):
    claims = row.get("claims")
    assert isinstance(claims, dict)
    assert claims.get("candidate_only") is True
    assert all(value is False for key, value in claims.items() if key != "candidate_only")
    assert row.get("fastlane_candidate") is False
    assert row.get("workbench_import_status") == "blocked_pending_visual_completion"
    assert row.get("answer_alignment", {}).get("correctness_verified") is False


def _assert_page_hashes(row):
    anchors = row["page_binding"]["pages"] + row["answer_alignment"]["solution_page_binding"]["pages"]
    for anchor in anchors:
        path = (PRODUCT / anchor["path"]).resolve()
        assert path.is_relative_to(PRODUCT / "batches")
        assert path.is_file()
        assert _sha(path) == anchor["sha256"]


def accept_packet():
    """Validate packet hashes and return the two proposed candidate copies."""

    hashes = read_json(PACKET / "file_hashes.json")["files"]
    for name, record in hashes.items():
        path = PACKET / name
        assert path.is_file()
        assert path.stat().st_size == record["bytes"] and _sha(path) == record["sha256"]

    candidates = read_rows(PACKET / "native_text_complete_candidate_copies.jsonl")
    assert [row["candidate_id"] for row in candidates] == list(IDS)
    for row in candidates:
        assert row["completeness"]["classification"] == NEW_CLASSIFICATION
        assert row["completeness"]["reasons"] == []
        _assert_closed_claims(row)
        _assert_page_hashes(row)

    differences = read_json(PACKET / "field_differences.json")["records"]
    assert [row["candidate_id"] for row in differences] == list(IDS)
    expected_paths = {
        "/completeness/classification",
        "/completeness/reasons",
    }
    for row in differences:
        assert row["formal_candidate_field_change_count"] == 2
        assert {item["path"] for item in row["formal_candidate_field_changes"]} == expected_paths
        assert row["reader_key_before"] == row["reader_key_after"]
        assert row["reader_revision_before"] != row["reader_revision_after"]

    page_index = read_json(PACKET / "page_index.json")
    assert len(page_index["pages"]) == 9
    for page in page_index["pages"]:
        path = (PRODUCT / page["path"]).resolve()
        assert path.is_relative_to(PRODUCT / "batches")
        assert path.is_file()
        assert page["hash_matches"] is True
        assert page["actual_sha256"] == page["sha256"] == _sha(path)

    evidence = read_json(PACKET / "text_completeness_evidence.json")
    assert [row["candidate_id"] for row in evidence["records"]] == list(IDS)
    for row in evidence["records"]:
        assert row["non_text_dependency_counts"] == {"answer": 0, "question": 0}
        assert all(
            item["status"] == "complete"
            for group in (row["question_comparison"], row["answer_comparison"])
            for item in group
        )

    validation = read_json(PACKET / "validation_report.json")
    assert validation["overall_status"] == "passed"
    assert all(validation["checks"].values())
    assert validation["protected_input_hashes_equal"] is True
    return {row["candidate_id"]: row for row in candidates}


def _live_rows():
    rows = read_rows(BATCH / FILES[0])
    by_id = {row["candidate_id"]: row for row in rows}
    assert len(rows) == len(by_id) == 68
    return rows, by_id


def _assert_old_target(row):
    assert row["completeness"]["classification"] == OLD_CLASSIFICATION
    assert row["completeness"]["reasons"] == OLD_REASONS
    _assert_closed_claims(row)


def _expected_target(row):
    updated = copy.deepcopy(row)
    updated["completeness"]["classification"] = NEW_CLASSIFICATION
    updated["completeness"]["reasons"] = []
    return updated


def _packet_matches_live_targets(packet, live):
    for candidate_id in IDS:
        _assert_old_target(live[candidate_id])
        assert packet[candidate_id] == _expected_target(live[candidate_id])


def patch():
    """Return an apply_patch payload; do not mutate the live files."""

    packet = accept_packet()
    _rows, live = _live_rows()
    _packet_matches_live_targets(packet, live)

    queue = read_rows(BATCH / FILES[1])
    queue_by_id = {row["candidate_id"]: row for row in queue}
    assert len(queue) == len(queue_by_id)
    for candidate_id in IDS:
        assert candidate_id in queue_by_id
        assert queue_by_id[candidate_id] == live[candidate_id]

    parts = ["*** Begin Patch"]
    question_path = BATCH / FILES[0]
    parts.append(f"*** Update File: {question_path.relative_to(ROOT).as_posix()}")
    question_lines = question_path.read_text(encoding="utf-8").splitlines()
    for candidate_id in IDS:
        matches = [
            line
            for line in question_lines
            if json.loads(line)["candidate_id"] == candidate_id
        ]
        assert len(matches) == 1 and json.loads(matches[0]) == live[candidate_id]
        parts.extend(
            [
                "@@",
                "-" + matches[0],
                "+" + json.dumps(
                    packet[candidate_id],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ]
        )

    queue_path = BATCH / FILES[1]
    parts.append(f"*** Update File: {queue_path.relative_to(ROOT).as_posix()}")
    queue_lines = queue_path.read_text(encoding="utf-8").splitlines()
    for candidate_id in IDS:
        matches = [
            line
            for line in queue_lines
            if json.loads(line)["candidate_id"] == candidate_id
        ]
        assert len(matches) == 1 and json.loads(matches[0]) == live[candidate_id]
        parts.extend(["@@", "-" + matches[0]])
    return "\n".join(parts + ["*** End Patch"])


def verify():
    """Verify a caller-applied patch against its immutable QA backup."""

    packet = accept_packet()
    before_rows = read_rows(QA / "batch-before" / FILES[0])
    before = {row["candidate_id"]: row for row in before_rows}
    assert len(before_rows) == len(before) == 68
    after_rows, after = _live_rows()

    expected_rows = copy.deepcopy(before_rows)
    expected_by_id = {row["candidate_id"]: row for row in expected_rows}
    for candidate_id in IDS:
        assert candidate_id in before
        _assert_old_target(before[candidate_id])
        assert packet[candidate_id] == _expected_target(before[candidate_id])
        expected_by_id[candidate_id] = packet[candidate_id]
    assert after_rows == [expected_by_id[row["candidate_id"]] for row in before_rows]
    assert all(
        before_rows[index] == after_rows[index]
        for index in range(68)
        if before_rows[index]["candidate_id"] not in IDS
    )
    assert all(after[candidate_id] == packet[candidate_id] for candidate_id in IDS)

    queue = read_rows(BATCH / FILES[1])
    assert queue == [
        row
        for row in after_rows
        if row["completeness"]["classification"] == OLD_CLASSIFICATION
    ]
    assert all(candidate_id not in {row["candidate_id"] for row in queue} for candidate_id in IDS)

    reader = load_reader(ROOT)
    catalog = reader.catalog()
    assert len(catalog["items"]) == 285 and not catalog["warnings"]
    service = HandoutCandidateService(ROOT, None)
    page_reads = 0
    for difference in read_json(PACKET / "field_differences.json")["records"]:
        candidate_id = difference["candidate_id"]
        item = reader.get(difference["reader_key_after"])
        assert item["key"] == difference["reader_key_after"]
        assert item["revision"] == difference["reader_revision_after"]
        assert item["classification"] == NEW_CLASSIFICATION
        assert item["candidate_only"] is True
        for role in ("question", "answer"):
            for index, _page in enumerate(item[role + "_pages"]):
                data = service.page_bytes(item["key"], item["revision"], role, index)
                assert data
                page_reads += 1
        try:
            service.page_bytes(
                item["key"], difference["reader_revision_before"], "question", 0
            )
        except HandoutCandidateError as exc:
            assert exc.code == "handout_page_stale"
        else:
            raise AssertionError(f"old reader revision accepted for {candidate_id}")

    return {
        "status": "passed",
        "pkg043_count": 68,
        "changed_candidates": ["21", "25"],
        "other_66_candidates_unchanged": True,
        "queue_equals_remaining_hybrid": True,
        "catalog_count": 285,
        "reader_revisions_updated": True,
        "stale_reader_revisions_rejected": True,
        "page_reads": page_reads,
        "claims_and_text_unchanged": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("patch", "verify"))
    args = parser.parse_args()
    print(
        patch()
        if args.mode == "patch"
        else json.dumps(verify(), ensure_ascii=False)
    )

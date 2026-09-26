"""Read-only acceptance and patch emitter for the visually checked Q2 anchor.

Does not write product files. Apply emitted patch through apply_patch, with
an external backup first; refresh derived manifests after the application.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from integrations.deeptutor_shchem_v1.desktop_handout_candidates import load_reader

PRODUCT = ROOT / "sh-chem-db/kb/teaching_handout_question_slices_v1_2026-08-28"
BATCH = PRODUCT / "native_ooxml_v2/batches/NATIVE-BATCH-NV2W2-PKG043-A02"
PACKET = (
    ROOT
    / "staging/coordination/deeptutor_gateway/handout_boundary_pkg043_first4_20260908"
)
Q2 = "PQ-d65217f4a4a729bb"
TARGETS = ("question_candidates.jsonl", "hybrid_visual_queue.jsonl")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def accept_packet():
    hashes = read_json(PACKET / "file_hashes.json")["files"]
    for name, record in hashes.items():
        path = PACKET / name
        assert path.stat().st_size == record["bytes"] and sha(path) == record["sha256"]
    rows = read_rows(PACKET / "boundary_corrected_candidates.jsonl")
    assert len(rows) == 4
    assert {r["printed_question"]["printed_number"] for r in rows} == {
        "1",
        "2",
        "5",
        "10",
    }
    for row in rows:
        pages = (
            row["page_binding"]["pages"]
            + row["answer_alignment"]["solution_page_binding"]["pages"]
        )
        for page in pages:
            path = (PRODUCT / page["path"]).resolve()
            assert path.is_relative_to(PRODUCT / "batches")
            assert sha(path) == page["sha256"]
        assert row["completeness"]["classification"] == "hybrid_visual_required"
        assert not row["claims"]["human_reviewed"]
        assert not row["claims"]["chemistry_reviewed"]
    return rows


def expected_change(original):
    changed = copy.deepcopy(original)
    binding = changed["page_binding"]
    assert [page["page"] for page in binding["pages"]] == [1, 2]
    binding["pages"] = binding["pages"][:1]
    binding["cross_page"] = False
    binding["reasons"] = []
    binding["status"] = "bound_single_render_page"
    return changed


def make_patch():
    packet = accept_packet()
    live = {r["candidate_id"]: r for r in read_rows(BATCH / TARGETS[0])}
    corrected = next(row for row in packet if row["candidate_id"] == Q2)
    assert expected_change(live[Q2]) == corrected
    assert all(
        row == live[row["candidate_id"]] for row in packet if row["candidate_id"] != Q2
    )
    reader = load_reader(ROOT)
    catalog = reader.catalog()
    assert not catalog["warnings"] and len(catalog["items"]) == 285
    changes = ["*** Begin Patch"]
    for name in TARGETS:
        path = BATCH / name
        lines = path.read_text(encoding="utf-8").splitlines()
        matches = [line for line in lines if json.loads(line)["candidate_id"] == Q2]
        assert len(matches) == 1 and json.loads(matches[0]) == live[Q2]
        replacement = json.dumps(
            corrected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        changes.extend(
            [
                f"*** Update File: {path.relative_to(ROOT).as_posix()}",
                "@@",
                "-" + matches[0],
                "+" + replacement,
            ]
        )
    changes.append("*** End Patch")
    return "\n".join(changes)


def verify_after():
    packet = accept_packet()
    live = read_rows(BATCH / TARGETS[0])
    by_id = {r["candidate_id"]: r for r in live}
    assert len(live) == len(by_id) == 68
    assert all(row == by_id[row["candidate_id"]] for row in packet)
    queue = read_rows(BATCH / TARGETS[1])
    assert queue == [
        row
        for row in live
        if row["completeness"]["classification"] == "hybrid_visual_required"
    ]
    reader = load_reader(ROOT)
    catalog = reader.catalog()
    assert not catalog["warnings"] and len(catalog["items"]) == 285
    changes = read_json(PACKET / "field_differences.json")["records"]
    for change in changes:
        item = reader.get(change["reader_key_after"])
        assert item["revision"] == change["reader_revision_after"]
    return {
        "status": "passed",
        "catalog_count": 285,
        "pkg043_count": 68,
        "native_count": 27,
        "hybrid_count": len(queue),
        "changed_candidate_count": 1,
        "q2_question_pages": [1],
        "candidate_keys_preserved": True,
        "q2_revision_changed": True,
        "other_three_revisions_unchanged": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("patch", "verify"))
    args = parser.parse_args()
    print(
        make_patch()
        if args.mode == "patch"
        else json.dumps(verify_after(), ensure_ascii=False)
    )

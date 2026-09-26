"""Stage and verify the PKG043 Q21/Q25 candidate-layer import repair.

The text-completeness classification patch is already applied before this
follow-up.  This script emits a read-only ``apply_patch`` payload that links
the two candidates to the existing local fastlane/import-record contract.  It
does not write live files, manifests, or the packet.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

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
FILES = (
    "question_candidates.jsonl",
    "fastlane_candidates.jsonl",
    "candidate_import_records.jsonl",
    "hybrid_visual_queue.jsonl",
    "batch_summary.json",
)
IDS = ("PQ-7f7a6a3c00972af7", "PQ-9844af38112b410b")
OLD_CLASSIFICATION = "hybrid_visual_required"
NEW_CLASSIFICATION = "native_text_complete"
READY_STATUS = "candidate_ready_for_separate_import_adapter"
IMPORT_STATUS = "candidate_record_written_not_central_registered"
OLD_IMPORT_COUNT = 27
FINAL_IMPORT_COUNT = 29


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_import_builder() -> Any:
    script = (
        PRODUCT
        / "native_ooxml_v2/scripts/native_fastlane_pipeline.py"
    ).resolve()
    spec = importlib.util.spec_from_file_location(
        "_pkg043_native_fastlane_pipeline", script
    )
    if spec is None or spec.loader is None:
        raise AssertionError("native fastlane pipeline is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.candidate_import_record


def _packet_candidates() -> dict[str, dict[str, Any]]:
    hashes = read_json(PACKET / "file_hashes.json")["files"]
    for name, record in hashes.items():
        path = PACKET / name
        assert path.is_file()
        assert path.stat().st_size == record["bytes"]
        assert _sha(path) == record["sha256"]

    rows = read_rows(PACKET / "native_text_complete_candidate_copies.jsonl")
    assert [row["candidate_id"] for row in rows] == list(IDS)
    for row in rows:
        assert row["completeness"]["classification"] == NEW_CLASSIFICATION
        assert row["completeness"]["reasons"] == []
        assert row["fastlane_candidate"] is False
        assert row["workbench_import_status"] == "blocked_pending_visual_completion"
        assert row["claims"]["candidate_only"] is True
        assert all(
            value is False
            for key, value in row["claims"].items()
            if key != "candidate_only"
        )
        anchors = (
            row["page_binding"]["pages"]
            + row["answer_alignment"]["solution_page_binding"]["pages"]
        )
        for anchor in anchors:
            path = (PRODUCT / anchor["path"]).resolve()
            assert path.is_relative_to(PRODUCT / "batches") and path.is_file()
            assert _sha(path) == anchor["sha256"]
    return {row["candidate_id"]: row for row in rows}


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {row["candidate_id"]: row for row in rows}
    assert len(result) == len(rows)
    return result


def _changed_candidate(before: dict[str, Any], *, stage: bool) -> dict[str, Any]:
    result = copy.deepcopy(before)
    if stage:
        result["fastlane_candidate"] = True
        result["workbench_import_status"] = READY_STATUS
    else:
        result["completeness"]["classification"] = NEW_CLASSIFICATION
        result["completeness"]["reasons"] = []
        result["fastlane_candidate"] = True
        result["workbench_import_status"] = READY_STATUS
    return result


def _assert_closed_candidate(candidate: dict[str, Any]) -> None:
    claims = candidate["claims"]
    assert claims["candidate_only"] is True
    assert all(value is False for key, value in claims.items() if key != "candidate_only")
    assert candidate["answer_alignment"]["correctness_verified"] is False
    for atomic in candidate["printed_question"]["atomic_parts"]:
        assert atomic["claims"] == claims


def _assert_current_pre_stage(
    packet: dict[str, dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
]:
    before_dir = QA / "batch-before"
    before_questions = read_rows(before_dir / FILES[0])
    before_fastlane = read_rows(before_dir / FILES[1])
    before_imports = read_rows(before_dir / FILES[2])
    before_queue = read_rows(before_dir / FILES[3])
    before_summary = read_json(before_dir / FILES[4])
    current_questions = read_rows(BATCH / FILES[0])
    current_fastlane = read_rows(BATCH / FILES[1])
    current_imports = read_rows(BATCH / FILES[2])
    current_queue = read_rows(BATCH / FILES[3])
    current_summary = read_json(BATCH / FILES[4])

    before_by_id = _by_id(before_questions)
    current_by_id = _by_id(current_questions)
    assert len(before_questions) == len(current_questions) == 68
    assert current_fastlane == before_fastlane
    assert current_imports == before_imports
    assert len(before_imports) == len(before_fastlane) == OLD_IMPORT_COUNT
    assert current_summary["candidate_import_records_written"] == OLD_IMPORT_COUNT
    assert current_summary["fastlane_candidate_count"] == OLD_IMPORT_COUNT
    assert current_summary["classification_counts"] == {
        "hybrid_visual_required": 39,
        "native_text_complete": 29,
        "visual_only_required": 0,
    }
    assert current_summary["hybrid_visual_question_count"] == 39
    assert current_summary["blocker_reason_counts"][
        "answer_page_binding_requires_visual_confirmation"
    ] == 34
    assert current_summary["per_package"]["PKG-043"][
        "candidate_import_records_written"
    ] == OLD_IMPORT_COUNT
    assert current_summary["per_package"]["PKG-043"]["classification_counts"] == current_summary[
        "classification_counts"
    ]
    assert current_queue == [
        row
        for row in current_questions
        if row["completeness"]["classification"] == OLD_CLASSIFICATION
    ]
    assert len(current_queue) == 39

    for candidate_id in IDS:
        old = before_by_id[candidate_id]
        current = current_by_id[candidate_id]
        assert old["completeness"]["classification"] == OLD_CLASSIFICATION
        assert old["completeness"]["reasons"] == [
            "answer_page_binding_requires_visual_confirmation"
        ]
        assert current == packet[candidate_id]
        assert current["fastlane_candidate"] is False
        assert current["workbench_import_status"] == "blocked_pending_visual_completion"
        _assert_closed_candidate(current)

    for index, old in enumerate(before_questions):
        if old["candidate_id"] in IDS:
            continue
        assert current_questions[index] == old
    return (
        before_questions,
        before_fastlane,
        before_imports,
        before_queue,
        before_summary,
        current_summary,
    )


def _insert_by_sequence(
    original: list[dict[str, Any]], additions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = list(original) + list(additions)
    result.sort(
        key=lambda row: int(
            row.get("printed_question", {}).get("sequence_in_document", 0)
        )
    )
    return result


def _json_line(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _replace_line_hunk(
    parts: list[str], path: Path, old_line: str, new_line: str, *, context: list[str] = ()
) -> None:
    parts.append(f"*** Update File: {path.relative_to(ROOT).as_posix()}")
    parts.append("@@")
    parts.extend(" " + line for line in context)
    parts.extend(["-" + old_line, "+" + new_line])


def _insert_after_hunk(
    parts: list[str], path: Path, anchor: str, addition: str
) -> None:
    parts.append(f"*** Update File: {path.relative_to(ROOT).as_posix()}")
    parts.extend(["@@", "-" + anchor, "+" + anchor, "+" + addition])


def stage_patch() -> str:
    """Emit the follow-up stage patch without writing any file."""

    packet = _packet_candidates()
    (
        _before_questions,
        before_fastlane,
        before_imports,
        _before_queue,
        _before_summary,
        _current_summary,
    ) = _assert_current_pre_stage(packet)
    current_questions = read_rows(BATCH / FILES[0])
    current_by_id = _by_id(current_questions)
    updated = {candidate_id: _changed_candidate(current_by_id[candidate_id], stage=True) for candidate_id in IDS}
    import_builder = _load_import_builder()
    new_fastlane = [updated[candidate_id] for candidate_id in IDS]
    new_imports = [import_builder(updated[candidate_id]) for candidate_id in IDS]
    assert [row["import_record_id"] for row in new_imports] == list(IDS)
    assert _insert_by_sequence(before_fastlane, new_fastlane) == _insert_by_sequence(
        before_fastlane, new_fastlane
    )
    assert _insert_by_sequence(before_imports, new_imports) == _insert_by_sequence(
        before_imports, new_imports
    )

    parts = ["*** Begin Patch"]
    question_path = BATCH / FILES[0]
    question_lines = question_path.read_text(encoding="utf-8").splitlines()
    for candidate_id in IDS:
        matches = [
            line
            for line in question_lines
            if json.loads(line)["candidate_id"] == candidate_id
        ]
        assert len(matches) == 1 and json.loads(matches[0]) == current_by_id[candidate_id]
        _replace_line_hunk(parts, question_path, matches[0], _json_line(updated[candidate_id]))

    for filename, additions in (
        (FILES[1], new_fastlane),
        (FILES[2], new_imports),
    ):
        path = BATCH / filename
        lines = path.read_text(encoding="utf-8").splitlines()
        existing = read_rows(path)
        by_sequence = {
            int(row["printed_question"]["sequence_in_document"]): line
            for row, line in zip(existing, lines)
        }
        for addition in sorted(
            additions,
            key=lambda row: int(row["printed_question"]["sequence_in_document"]),
        ):
            sequence = int(addition["printed_question"]["sequence_in_document"])
            prior = max(key for key in by_sequence if key < sequence)
            _insert_after_hunk(parts, path, by_sequence[prior], _json_line(addition))

    summary_path = BATCH / FILES[4]
    summary_lines = summary_path.read_text(encoding="utf-8").splitlines()
    top_import_line = '  "candidate_import_records_written": 27,'
    top_fastlane_line = '  "fastlane_candidate_count": 27,'
    nested_import_line = '      "candidate_import_records_written": 27,'
    assert summary_lines.count(top_import_line) == 1
    assert summary_lines.count(top_fastlane_line) == 1
    assert summary_lines.count(nested_import_line) == 1
    _replace_line_hunk(
        parts,
        summary_path,
        top_import_line,
        '  "candidate_import_records_written": 29,',
        context=['  "batch_id": "NATIVE-BATCH-NV2W2-PKG043-A02",'],
    )
    _replace_line_hunk(
        parts,
        summary_path,
        top_fastlane_line,
        '  "fastlane_candidate_count": 29,',
        context=['  "duplicate_or_number_conflict_count": 0,'],
    )
    _replace_line_hunk(
        parts,
        summary_path,
        nested_import_line,
        '      "candidate_import_records_written": 29,',
        context=[
            '  "per_package": {',
            '    "PKG-043": {',
            '      "answer_pairs": 68,',
            '      "atomic_part_candidates": 85,',
        ],
    )
    return "\n".join(parts + ["*** End Patch"])


def _expected_summary(before: dict[str, Any]) -> dict[str, Any]:
    expected = copy.deepcopy(before)
    expected["blocker_reason_counts"][
        "answer_page_binding_requires_visual_confirmation"
    ] = 34
    expected["classification_counts"] = {
        "hybrid_visual_required": 39,
        "native_text_complete": 29,
        "visual_only_required": 0,
    }
    expected["hybrid_visual_question_count"] = 39
    expected["fastlane_candidate_count"] = FINAL_IMPORT_COUNT
    expected["candidate_import_records_written"] = FINAL_IMPORT_COUNT
    expected["per_package"]["PKG-043"]["classification_counts"] = copy.deepcopy(
        expected["classification_counts"]
    )
    expected["per_package"]["PKG-043"][
        "candidate_import_records_written"
    ] = FINAL_IMPORT_COUNT
    return expected


def verify() -> dict[str, Any]:
    """Verify the applied local candidate-layer repair against the QA backup."""

    packet = _packet_candidates()
    (
        before_questions,
        before_fastlane,
        before_imports,
        _before_queue,
        before_summary,
        _current_summary,
    ) = _assert_current_pre_stage(packet)
    current_questions = read_rows(BATCH / FILES[0])
    current_fastlane = read_rows(BATCH / FILES[1])
    current_imports = read_rows(BATCH / FILES[2])
    current_queue = read_rows(BATCH / FILES[3])
    current_summary = read_json(BATCH / FILES[4])
    before_by_id = _by_id(before_questions)
    current_by_id = _by_id(current_questions)
    import_builder = _load_import_builder()
    expected_questions = copy.deepcopy(before_questions)
    expected_by_id = _by_id(expected_questions)
    updated_targets: dict[str, dict[str, Any]] = {}
    for candidate_id in IDS:
        updated = _changed_candidate(before_by_id[candidate_id], stage=False)
        assert packet[candidate_id]["completeness"] == updated["completeness"]
        updated_targets[candidate_id] = updated
        expected_by_id[candidate_id] = updated
    assert current_questions == [expected_by_id[row["candidate_id"]] for row in before_questions]
    allowed_paths = {
        "/completeness/classification",
        "/completeness/reasons",
        "/fastlane_candidate",
        "/workbench_import_status",
    }
    for candidate_id in IDS:
        before = before_by_id[candidate_id]
        after = current_by_id[candidate_id]
        changed_paths = set(_diff_paths(before, after))
        assert changed_paths == allowed_paths
        _assert_closed_candidate(after)
        assert after["completeness"]["classification"] == NEW_CLASSIFICATION
        assert after["completeness"]["reasons"] == []
        assert after["fastlane_candidate"] is True
        assert after["workbench_import_status"] == READY_STATUS
    assert all(
        before_questions[index] == current_questions[index]
        for index in range(68)
        if before_questions[index]["candidate_id"] not in IDS
    )

    expected_fastlane = _insert_by_sequence(
        before_fastlane, [updated_targets[candidate_id] for candidate_id in IDS]
    )
    assert current_fastlane == expected_fastlane
    new_imports = [import_builder(updated_targets[candidate_id]) for candidate_id in IDS]
    expected_imports = _insert_by_sequence(before_imports, new_imports)
    assert current_imports == expected_imports
    old_import_ids = {row["import_record_id"] for row in before_imports}
    assert [
        row
        for row in current_imports
        if row["import_record_id"] in old_import_ids
    ] == before_imports
    assert [row["import_record_id"] for row in current_imports if row["import_record_id"] in IDS] == list(IDS)
    assert current_queue == [
        row
        for row in current_questions
        if row["completeness"]["classification"] == OLD_CLASSIFICATION
    ]
    assert len(current_queue) == 39
    assert current_summary == _expected_summary(before_summary)

    reader = load_reader(ROOT)
    catalog = reader.catalog()
    assert len(catalog["items"]) == 285 and not catalog["warnings"]
    service = HandoutCandidateService(ROOT, None)
    page_reads = 0
    actual_reader_revisions: dict[str, str] = {}
    packet_differences = read_json(PACKET / "field_differences.json")["records"]
    differences_by_id = {row["candidate_id"]: row for row in packet_differences}
    for candidate_id in IDS:
        item = reader.get(differences_by_id[candidate_id]["reader_key_after"])
        actual_reader_revisions[candidate_id] = item["revision"]
        assert item["revision"] != differences_by_id[candidate_id]["reader_revision_before"]
        assert item["classification"] == NEW_CLASSIFICATION
        for role in ("question", "answer"):
            for index, _page in enumerate(item[role + "_pages"]):
                data = service.page_bytes(item["key"], item["revision"], role, index)
                assert data
                page_reads += 1
        try:
            service.page_bytes(
                item["key"],
                differences_by_id[candidate_id]["reader_revision_before"],
                "question",
                0,
            )
        except HandoutCandidateError as exc:
            assert exc.code == "handout_page_stale"
        else:
            raise AssertionError(f"old reader revision accepted for {candidate_id}")

    return {
        "status": "passed",
        "pkg043_count": 68,
        "changed_candidates": ["21", "25"],
        "candidate_field_changes": sorted(allowed_paths),
        "other_66_candidates_unchanged": True,
        "old_import_records_unchanged": True,
        "new_import_records_match_mapping": True,
        "queue_count": len(current_queue),
        "summary_import_count": current_summary["candidate_import_records_written"],
        "summary_fastlane_count": current_summary["fastlane_candidate_count"],
        "reader_count": len(catalog["items"]),
        "reader_revisions": actual_reader_revisions,
        "stale_reader_revisions_rejected": True,
        "page_reads": page_reads,
        "formal_claims_unlocked": False,
    }


def _diff_paths(before: Any, after: Any, path: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        result: list[str] = []
        for key in sorted(set(before) | set(after)):
            current = f"{path}/{key}"
            if key not in before or key not in after:
                result.append(current)
            else:
                result.extend(_diff_paths(before[key], after[key], current))
        return result
    if isinstance(before, list) and isinstance(after, list):
        result = []
        for index in range(max(len(before), len(after))):
            current = f"{path}/{index}"
            if index >= len(before) or index >= len(after):
                result.append(current)
            else:
                result.extend(_diff_paths(before[index], after[index], current))
        return result
    return [] if before == after else [path or "/"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("stage_patch", "verify"))
    args = parser.parse_args()
    print(
        stage_patch()
        if args.mode == "stage_patch"
        else json.dumps(verify(), ensure_ascii=False)
    )

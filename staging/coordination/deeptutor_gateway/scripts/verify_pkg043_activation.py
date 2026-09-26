"""Read-only batch acceptance plus external receipts for the PKG043 switch.

Directory moves are deliberately not performed here. The maintenance command
uses checked literal PowerShell paths while no workbench is running.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    HandoutCandidateService, PRODUCT_PATH, load_reader,
)
from integrations.deeptutor_shchem_v1.desktop_handout_practice import NativeParagraphs

PRODUCT = ROOT / PRODUCT_PATH
NATIVE = PRODUCT / "native_ooxml_v2"
OLD = "NATIVE-BATCH-NV2W2-PKG043-A01"
NEW = "NATIVE-BATCH-NV2W2-PKG043-A02"
STAGED = NATIVE / "rebuild_staging/PKG043-A02" / NEW
RETIRED = NATIVE / "retired_batches" / OLD
QA = ROOT / "runtime/deeptutor_shchem/qa/pkg043-a02-activation-20260908"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root):
    return {p.relative_to(root).as_posix(): sha(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_receipt(name, value):
    QA.mkdir(parents=True, exist_ok=True)
    path = QA / name
    if path.exists():
        raise RuntimeError(f"Receipt already exists: {path.name}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_references(value, known):
    if isinstance(value, str):
        return {value} if value in known else set()
    if isinstance(value, dict):
        return set().union(*(collect_references(v, known) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(collect_references(v, known) for v in value))
    return set()


def inspect_batch(reader, path):
    items, info = reader._read_batch(path)
    native = NativeParagraphs(ROOT)
    role_counts = Counter()
    page_paths = {}
    for item in items:
        decorated = HandoutCandidateService._decorate(item, {})
        if decorated["practice_eligible"]:
            for role in ("question", "answer"):
                assert native.read(item, role)
                role_counts[role] += 1
        for role in ("question", "answer"):
            for page in item[f"{role}_pages"]:
                # Match the desktop reader's existing page contract.
                relative = page["path"]
                target = (PRODUCT / relative).resolve()
                assert target.is_relative_to(PRODUCT / "batches")
                assert sha(target) == page["sha256"]
                page_paths[relative] = page["sha256"]
    return items, {"batch": info, "readable_roles": dict(role_counts),
                   "page_count": len(page_paths), "page_sha256": page_paths}


def counts(catalog):
    return {"total": len(catalog["items"]), "batches": len(catalog["batches"]),
            "classifications": dict(Counter(i["classification"] for i in catalog["items"])),
            "practice_eligible": sum(HandoutCandidateService._decorate(i, {})["practice_eligible"] for i in catalog["items"]),
            "warnings": catalog["warnings"]}


def preflight():
    reader = load_reader(ROOT)
    before = reader.catalog()
    assert not before["warnings"]
    assert not (NATIVE / "batches" / NEW).exists() and not RETIRED.exists()
    old_items, _ = reader._read_batch(NATIVE / "batches" / OLD)
    new_items, new_summary = inspect_batch(reader, STAGED)
    old_questions = jsonl(NATIVE / "batches" / OLD / "question_candidates.jsonl")
    new_questions = jsonl(STAGED / "question_candidates.jsonl")
    old_ids = {q["candidate_id"] for q in old_questions}
    new_ids = {q["candidate_id"] for q in new_questions}
    removed = [q for q in old_questions if q["candidate_id"] not in new_ids]
    assert len(old_items) == 78 and len(new_items) == 68
    assert new_ids < old_ids and len(removed) == 10
    assert all("/w:tbl[2]/" in q["printed_question"]["start_xml_locator"] for q in removed)
    assert new_summary["readable_roles"] == {"question": 27, "answer": 27}
    assert sum(len(q["printed_question"]["atomic_parts"]) for q in new_questions) == 85
    old_by_id = {q["candidate_id"]: i for q, i in zip(old_questions, old_items, strict=True)}
    new_by_id = {q["candidate_id"]: i for q, i in zip(new_questions, new_items, strict=True)}
    old_keys = {i["key"] for i in old_items}
    state_path = Path(os.environ["LOCALAPPDATA"]) / "ShanghaiChem/DesktopWorkbench/desktop-state.v1.json"
    saved = read_json(state_path) if state_path.exists() else {}
    affected = collect_references(saved, old_keys)
    untouched = {i["key"]: i["revision"] for i in before["items"] if i["batch_id"] != OLD}
    result = {"phase": "preflight", "valid": True, "before": counts(before),
              "new_batch": new_summary, "removed_count": len(removed),
              "saved_old_reference_count": len(affected),
              "user_state_sha256": sha(state_path) if state_path.exists() else None,
              "other_items": untouched, "staging_tree": tree(STAGED),
              "old_tree": tree(NATIVE / "batches" / OLD),
              "source_hashes": sorted({i["source_document"]["sha256"] for i in old_items + new_items}),
              "key_mapping": [{"candidate_id": qid, "old_key": old_by_id[qid]["key"],
                               "new_key": new_by_id[qid]["key"]} for qid in sorted(new_ids)],
              "removed_candidate_ids": sorted(old_ids-new_ids),
              "answer_correctness_verified": False, "human_visual_review_completed": False}
    write_receipt("preflight.json", result)
    return {key: result[key] for key in ("phase", "valid", "before", "removed_count", "saved_old_reference_count")}


def after():
    prior = read_json(QA / "preflight.json")
    reader = load_reader(ROOT)
    catalog = reader.catalog()
    assert not catalog["warnings"]
    assert not (NATIVE / "batches" / OLD).exists()
    assert tree(RETIRED) == prior["old_tree"]
    assert tree(STAGED) == prior["staging_tree"] == tree(NATIVE / "batches" / NEW)
    assert {i["key"]: i["revision"] for i in catalog["items"] if i["batch_id"] != NEW} == prior["other_items"]
    new_items, summary = inspect_batch(reader, NATIVE / "batches" / NEW)
    assert len(new_items) == 68 and summary["readable_roles"] == {"question": 27, "answer": 27}
    actual = counts(catalog)
    assert actual["total"] == prior["before"]["total"] - 10
    assert actual["practice_eligible"] == prior["before"]["practice_eligible"]
    state_path = Path(os.environ["LOCALAPPDATA"]) / "ShanghaiChem/DesktopWorkbench/desktop-state.v1.json"
    assert (sha(state_path) if state_path.exists() else None) == prior["user_state_sha256"]
    result = {"phase": "activated", "valid": True, "catalog": actual, "new_batch": summary,
              "original_staging_preserved": True, "old_batch_retained": True,
              "other_items_unchanged": True, "user_state_unchanged": True,
              "saved_old_reference_count": prior["saved_old_reference_count"],
              "answer_correctness_verified": False}
    write_receipt("activated.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("preflight", "after"))
    args = parser.parse_args()
    print(json.dumps(preflight() if args.phase == "preflight" else after(), ensure_ascii=False, indent=2))

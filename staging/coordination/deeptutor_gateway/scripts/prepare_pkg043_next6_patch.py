"""Apply-patch emitter and independent verifier for the next-six page review."""

import argparse
import copy
import hashlib
import json

from prepare_pkg043_boundary_patch import BATCH, PRODUCT, ROOT, read_json, read_rows
from integrations.deeptutor_shchem_v1.desktop_handout_candidates import HandoutCandidateError, HandoutCandidateService, load_reader

PACKET = ROOT / "staging/coordination/deeptutor_gateway/handout_visual_pkg043_next6_20260908"
QA = ROOT / "runtime/deeptutor_shchem/qa/pkg043-next6-integration-20260908"
IDS = ("PQ-38e21fa583606a90", "PQ-790d55d019e3fc90", "PQ-5451479fced59ac6", "PQ-7f7a6a3c00972af7", "PQ-44c19b74250fae10", "PQ-9844af38112b410b")
CHANGED = (IDS[0], IDS[1], IDS[3])
FILES = ("question_candidates.jsonl", "hybrid_visual_queue.jsonl")


def accept():
    for name, record in read_json(PACKET / "file_hashes.json")["files"].items():
        data = (PACKET / name).read_bytes()
        assert len(data) == record["bytes"] and hashlib.sha256(data).hexdigest() == record["sha256"]
    for page in read_json(PACKET / "page_index.json")["pages"]:
        path = (PRODUCT / page["path"]).resolve()
        assert path.is_relative_to(PRODUCT / "batches")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == page["sha256"]
    rows = read_rows(PACKET / "reviewed_candidate_copies.jsonl")
    assert [r["candidate_id"] for r in rows] == list(IDS)
    return {r["candidate_id"]: r for r in rows}


def expected(before):
    result = copy.deepcopy(before)
    # Independently encode only the three visually confirmed boundary changes.
    for key, pages in ((IDS[0], [5, 6]), (IDS[3], [7, 8])):
        binding = result[key]["answer_alignment"]["solution_page_binding"]
        assert [p["page"] for p in binding["pages"]] == pages
        binding["pages"] = binding["pages"][:1]
        binding["cross_page"] = False
        binding["reasons"].remove("cross_page_candidate_requires_visual_confirmation")
        if key == IDS[3]:
            assert not binding["reasons"]
            binding["status"] = "bound_single_render_page"
    q18 = result[IDS[1]]
    assert [p["page"] for p in q18["page_binding"]["pages"]] == [4]
    q18["page_binding"]["pages"].insert(0, copy.deepcopy(result[IDS[0]]["page_binding"]["pages"][0]))
    q18["page_binding"]["cross_page"] = True
    solution = q18["answer_alignment"]["solution_page_binding"]
    assert [p["page"] for p in solution["pages"]] == [6]
    solution["pages"].append(copy.deepcopy(result[IDS[2]]["answer_alignment"]["solution_page_binding"]["pages"][0]))
    solution["cross_page"] = True
    return result


def patch():
    packet = accept()
    before = {r["candidate_id"]: r for r in read_rows(BATCH / FILES[0])}
    assert len(before) == 68
    after = expected(before)
    assert all(after[key] == packet[key] for key in IDS)
    parts = ["*** Begin Patch"]
    for name in FILES:
        path = BATCH / name
        parts.append("*** Update File: " + path.relative_to(ROOT).as_posix())
        for key in CHANGED:
            line, = [s for s in path.read_text(encoding="utf-8").splitlines() if json.loads(s)["candidate_id"] == key]
            assert json.loads(line) == before[key]
            parts.extend(["@@", "-" + line, "+" + json.dumps(after[key], ensure_ascii=False, sort_keys=True, separators=(",", ":"))])
    return "\n".join(parts + ["*** End Patch"])


def verify():
    packet = accept()
    before = {r["candidate_id"]: r for r in read_rows(QA / "batch-before" / FILES[0])}
    rows = read_rows(BATCH / FILES[0])
    after = {r["candidate_id"]: r for r in rows}
    assert after == expected(before)
    assert all(after[key] == packet[key] for key in IDS)
    assert read_rows(BATCH / FILES[1]) == [r for r in rows if r["completeness"]["classification"] == "hybrid_visual_required"]
    reader = load_reader(ROOT)
    catalog = reader.catalog()
    assert len(catalog["items"]) == 285 and not catalog["warnings"]
    service = HandoutCandidateService(ROOT, None)
    page_reads = 0
    for diff in read_json(PACKET / "field_differences.json")["records"]:
        item = reader.get(diff["reader_key_after"])
        assert item["revision"] == diff["reader_revision_after"]
        assert item["classification"] == "hybrid_visual_required"
        for role in ("question", "answer"):
            for index, _ in enumerate(item[role + "_pages"]):
                assert service.page_bytes(item["key"], item["revision"], role, index)
                page_reads += 1
        if diff["candidate_id"] in CHANGED:
            try:
                service.page_bytes(item["key"], diff["reader_revision_before"], "question", 0)
            except HandoutCandidateError as exc:
                assert exc.code == "handout_page_stale"
            else:
                raise AssertionError("stale revision accepted")
    return {"passed": True, "reviewed_candidates": 6, "changed_candidates": [15, 18, 21], "other_65_unchanged": True, "text_and_claims_unchanged": True, "page_reads": page_reads, "catalog_count": 285, "native_promotions": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("patch", "verify"))
    args = parser.parse_args()
    print(patch() if args.mode == "patch" else json.dumps(verify()))

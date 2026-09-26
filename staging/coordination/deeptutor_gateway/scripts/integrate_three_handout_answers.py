"""Emit reviewable patches for three source-checked answers and legacy bindings.

Source documents and question/answer text are never rewritten. The main agent
reviewed source pages 6–8 and solution pages 11–16 before selecting these IDs.
Q41 remains pending because its source explanation contains a missing angle.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from audit_legacy_render_bindings import NATIVE, PRODUCT, ROOT, TARGETS, pipeline

QA = ROOT / "runtime/deeptutor_shchem/qa/handout-three-more-20260909"
BATCH_ID = "NATIVE-BATCH-NV2W2-PKG043-A02"
SELECTED = {
    "PQ-d753eeeb2245445f": (35, [6], [11, 12]),
    "PQ-0b1ad0ec69130050": (38, [7], [12, 13]),
    "PQ-8fa0903e889fb956": (47, [8], [15, 16]),
}
BINDINGS = {
    "PKG-038": (
        "BATCH-003-PKG038",
        "a49e25fa0b4876397ec24fcc0df767873f4e8e56c21fb3069d4dcbdf64a52d6d",
        "aff873a742b67c50592a67e95ebd116df0d81c0ba82c9cbe112e9845fa09359f",
    ),
    "PKG-039": (
        "BATCH-004-PKG039",
        "eec0cebe3144b057c8f896e5faee607fdb06f1d581f685ca0c1a23b10ad1d9dc",
        "1d3cb17f6c216928f42aeaf3b70eb42737a40aa0b7d978949af85d72b0f474ec",
    ),
    "PKG-050": (
        "BATCH-001-PKG050",
        "c0a16b62f2214d7f9b25cba1cd3ec2aafe2195afea432825fb58ca0ed113d5da",
        "c41c7f8c5b8893512dc651523b3150b367352e4eb7e337b9741f673d9f6ecc05",
    ),
    "PKG-051": (
        "BATCH-002-PKG051",
        "917716db372f52d8a0f2dfe3b9bb56f26a3c3d79c5e53708c7dc1bdc51d8e091",
        "441cefea6e11582d5290fbab22385d0cb960d32e8d8f360262d46cc6c5ae532f",
    ),
    "PKG-096": (
        "BATCH-NV2W2-PKG096-A01",
        "727609549b7b367f0b26bf883f9795e6445fb9805842d22cef6884074598a1ec",
        "9cc6396fbaebf72541370f40f9cb39f57e2328d7557ca800e41f037e9ebbb6da",
    ),
}


def read(path):
    text = path.read_text(encoding="utf-8")
    return (
        [json.loads(line) for line in text.splitlines() if line.strip()]
        if path.suffix == ".jsonl"
        else json.loads(text)
    )


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def expected():
    module = pipeline()
    changes = {}
    for batch_id in TARGETS:
        summary = read(QA / batch_id / "batch_summary.json")
        bindings = []
        for package_id in summary["package_ids"]:
            render_id, render_hash, output_hash = BINDINGS[package_id]
            render_path = PRODUCT / "batches" / render_id / "render_manifest.json"
            assert digest(render_path) == render_hash
            assert digest(render_path.parent / "output_manifest.json") == output_hash
            bindings.append(
                {
                    "package_id": package_id,
                    "render_batch_id": render_id,
                    "render_manifest_path": render_path.relative_to(PRODUCT).as_posix(),
                    "render_manifest_sha256": render_hash,
                    "render_output_manifest_sha256": output_hash,
                }
            )
        summary["render_bindings"] = bindings
        changes[NATIVE / "batches" / batch_id / "batch_summary.json"] = summary

    rows = read(QA / BATCH_ID / "question_candidates.jsonl")
    imports = read(QA / BATCH_ID / "candidate_import_records.jsonl")
    assert len(rows) == 68 and len(imports) == 29
    for row in rows:
        if row["candidate_id"] not in SELECTED:
            continue
        number, question_pages, answer_pages = SELECTED[row["candidate_id"]]
        assert row["claims"] == module.CLOSED_GATES
        assert row["printed_question"]["visible_number_native"] == str(number)
        assert row["completeness"]["classification"] == "hybrid_visual_required"
        assert row["completeness"]["reasons"] == [
            "answer_page_binding_requires_visual_confirmation"
        ]
        answer = row["answer_alignment"]
        assert not row["native_dependencies"]["has_non_text_dependency"]
        assert not answer["solution_native_dependencies"]["has_non_text_dependency"]
        assert [p["page"] for p in row["page_binding"]["pages"]] == question_pages
        assert [
            p["page"] for p in answer["solution_page_binding"]["pages"]
        ] == answer_pages
        for anchor in (
            row["page_binding"]["pages"] + answer["solution_page_binding"]["pages"]
        ):
            assert digest(PRODUCT / anchor["path"]) == anchor["sha256"]
        assert (
            digest(module.INTAKE_ROOT / row["source_document"]["relative_path"])
            == row["source_document"]["sha256"]
        )
        solution = read(QA / BATCH_ID / "documents/PKG-043-solution.json")["source"]
        assert (
            digest(Path(solution["absolute_path"]))
            == answer["solution_source_sha256"]
            == solution["sha256"]
        )
        row["completeness"].update(classification="native_text_complete", reasons=[])
        row.update(
            fastlane_candidate=True,
            workbench_import_status="candidate_ready_for_separate_import_adapter",
        )
        imports.append(module.candidate_import_record(row))
    imports.sort(key=lambda r: r["printed_question"]["sequence_in_document"])
    fast = [r for r in rows if r["fastlane_candidate"]]
    hybrid = [
        r
        for r in rows
        if r["completeness"]["classification"] == "hybrid_visual_required"
    ]
    assert len(fast) == len(imports) == 32 and len(hybrid) == 36
    summary = read(QA / BATCH_ID / "batch_summary.json")
    counts = Counter(r["completeness"]["classification"] for r in rows)
    summary["classification_counts"] = {
        key: counts[key] for key in summary["classification_counts"]
    }
    summary["blocker_reason_counts"] = dict(
        sorted(
            Counter(
                reason for r in rows for reason in r["completeness"]["reasons"]
            ).items()
        )
    )
    summary.update(
        fastlane_candidate_count=32,
        candidate_import_records_written=32,
        hybrid_visual_question_count=36,
    )
    summary["per_package"]["PKG-043"]["classification_counts"] = copy.deepcopy(
        summary["classification_counts"]
    )
    summary["per_package"]["PKG-043"]["candidate_import_records_written"] = 32
    for name, value in {
        "question_candidates.jsonl": rows,
        "candidate_import_records.jsonl": imports,
        "fastlane_candidates.jsonl": fast,
        "hybrid_visual_queue.jsonl": hybrid,
        "batch_summary.json": summary,
    }.items():
        changes[NATIVE / "batches" / BATCH_ID / name] = value
    return changes


def encoded(path, value):
    if path.suffix == ".jsonl":
        return "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in value
        )
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def patch(file_filter=None):
    parts = ["*** Begin Patch"]
    for path, value in expected().items():
        if (
            file_filter
            and path.relative_to(NATIVE / "batches").as_posix() != file_filter
        ):
            continue
        relative = path.relative_to(NATIVE / "batches")
        assert read(path) in (read(QA / relative), value), (
            f"Concurrent data edit: {path}"
        )
        old, new = path.read_text(encoding="utf-8"), encoded(path, value)
        diff = list(
            difflib.unified_diff(old.splitlines(), new.splitlines(), n=1, lineterm="")
        )
        if diff:
            parts.append("*** Update File: " + str(path))
            parts.extend("@@" if line.startswith("@@") else line for line in diff[2:])
    parts.append("*** End Patch")
    return "\n".join(parts)


def verify():
    changes = expected()
    assert all(read(path) == value for path, value in changes.items())
    module = pipeline()
    module.write_json = lambda *_args, **_kwargs: None
    reports = [
        module.validate_batch(path.name)
        for path in sorted((NATIVE / "batches").iterdir())
        if path.is_dir()
    ]
    assert len(reports) == 7 and all(r["valid"] for r in reports), reports
    before = read(QA / BATCH_ID / "question_candidates.jsonl")
    after = read(NATIVE / "batches" / BATCH_ID / "question_candidates.jsonl")
    for old, new in zip(before, after, strict=True):
        restored = copy.deepcopy(new)
        if old["candidate_id"] in SELECTED:
            for key in (
                "completeness",
                "fastlane_candidate",
                "workbench_import_status",
            ):
                restored[key] = old[key]
        assert restored == old
    for batch_id in TARGETS:
        for name in ("question_candidates.jsonl", "candidate_import_records.jsonl"):
            assert digest(NATIVE / "batches" / batch_id / name) == digest(
                QA / batch_id / name
            )
    sys.path.insert(0, str(ROOT))
    from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
        HandoutCandidateService,
        load_reader,
    )
    from integrations.deeptutor_shchem_v1.desktop_handout_practice import (
        NativeParagraphs,
    )

    catalog = load_reader(ROOT).catalog()
    assert not catalog["warnings"] and len(catalog["items"]) == 285
    selected = [
        i
        for i in catalog["items"]
        if (i.get("editable_source") or {}).get("candidate_id") in SELECTED
    ]
    assert len(selected) == 3 and all(
        HandoutCandidateService._decorate(i, {})["practice_eligible"] for i in selected
    )
    assert (
        sum(i["classification"] == "native_text_complete" for i in catalog["items"])
        == 72
    )
    # Use one reader snapshot for each page call; no personal review state needed.
    service = HandoutCandidateService(ROOT, None)
    service._read = lambda key=None: (
        catalog if key is None else next(i for i in catalog["items"] if i["key"] == key)
    )
    page_reads = 0
    native_reads = 0
    native = NativeParagraphs(ROOT)
    for item in selected:
        for role in ("question", "answer"):
            assert native.read(item, role)
            native_reads += 1
            for index, page in enumerate(item[role + "_pages"]):
                assert (
                    hashlib.sha256(
                        service.page_bytes(item["key"], item["revision"], role, index)
                    ).hexdigest()
                    == page["sha256"]
                )
                page_reads += 1
    manifest_checks = []
    manifests = [NATIVE / "VERSION_MANIFEST.json", PRODUCT / "PRODUCT_MANIFEST.json"]
    manifests.extend(
        path / "output_manifest.json"
        for path in sorted((NATIVE / "batches").iterdir())
        if path.is_dir()
    )
    for manifest_path in manifests:
        manifest = read(manifest_path)
        listed = {entry["path"] for entry in manifest["outputs"]}
        actual = {
            p.relative_to(manifest_path.parent).as_posix()
            for p in manifest_path.parent.rglob("*")
            if p.is_file() and p != manifest_path
        }
        assert listed == actual
        assert len(listed) == manifest["output_count_excluding_self"]
        total_bytes = 0
        for entry in manifest["outputs"]:
            path = manifest_path.parent / entry["path"]
            assert (
                path.stat().st_size == entry["bytes"]
                and digest(path) == entry["sha256"]
            ), path
            total_bytes += entry["bytes"]
        assert total_bytes == manifest["total_bytes_excluding_self"]
        manifest_checks.append(
            {
                "path": manifest_path.relative_to(PRODUCT).as_posix(),
                "files": len(listed),
                "valid": True,
            }
        )
    return {
        "valid": True,
        "batch_validation": reports,
        "catalog_count": 285,
        "native_complete": 72,
        "selected_page_reads": page_reads,
        "native_question_answer_reads": native_reads,
        "manifests": manifest_checks,
        "q41_unchanged": True,
        "question_and_answer_text_unchanged": True,
        "formal_gates_changed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("patch", "verify"))
    parser.add_argument("--file")
    args = parser.parse_args()
    print(
        patch(args.file)
        if args.mode == "patch"
        else json.dumps(verify(), ensure_ascii=False, indent=2)
    )

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[4]
PRODUCT = ROOT / "sh-chem-db/kb/teaching_handout_question_slices_v1_2026-08-28"
NATIVE = PRODUCT / "native_ooxml_v2"
FIXTURES = (
    ROOT
    / "staging/coordination/deeptutor_gateway/handout_multipage_answer_import_20260909/fixtures"
)


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, NATIVE / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pipeline = load_module("multipage_production_pipeline", "native_fastlane_pipeline.py")
reader = load_module("multipage_production_reader", "handout_candidate_reader.py")


@pytest.fixture
def candidate():
    value = json.loads(
        (FIXTURES / "q25_reviewed_candidate_copy.json").read_text(encoding="utf-8")
    )
    # Local test projection of the reviewed text-completeness candidate.
    # The frozen packet deliberately does not activate its own import record.
    value["fastlane_candidate"] = True
    return value


def item(candidate, record):
    return reader._candidate_item(
        batch_id="TEST-Q25", candidate=candidate, import_record=record
    )


def test_real_q25_uses_production_schema_builder_and_reader(candidate):
    original = copy.deepcopy(candidate)
    record = pipeline.candidate_import_record(candidate)
    schema = json.loads(
        (NATIVE / "contracts/native_import_record.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(record)
    assert (
        schema["properties"]["printed_question"]["properties"]["page_anchors"][
            "maxItems"
        ]
        == 1
    )
    assert (
        schema["properties"]["reference_answer"]["properties"]["page_anchors"][
            "maxItems"
        ]
        == 8
    )
    result = item(candidate, record)
    assert [p["page"] for p in result["question_pages"]] == [5]
    assert [p["page"] for p in result["answer_pages"]] == [8, 9]
    assert candidate == original
    record["reference_answer"]["page_anchors"][0]["page"] = 99
    record["source_document"]["sha256"] = "0" * 64
    assert candidate == original


@pytest.mark.parametrize("value", [None, "8", True, 8.0, 0, -1, [], {}])
def test_bad_page_numbers_have_domain_error_not_sorting_crash(candidate, value):
    candidate["answer_alignment"]["solution_page_binding"]["pages"][0]["page"] = value
    with pytest.raises(pipeline.FastlaneError) as caught:
        pipeline.candidate_import_record(candidate)
    assert caught.value.code == "import_page_anchor_invalid"


@pytest.mark.parametrize(
    "case",
    ["reverse", "duplicate", "missing", "gap", "hash", "source", "role", "binding"],
)
def test_reader_rejects_multipage_provenance_faults(candidate, case):
    record = pipeline.candidate_import_record(candidate)
    pages = record["reference_answer"]["page_anchors"]
    if case == "reverse":
        pages.reverse()
    elif case == "duplicate":
        pages[1] = copy.deepcopy(pages[0])
    elif case == "missing":
        pages.pop()
    elif case == "gap":
        pages[1]["page"] = 10
    elif case == "hash":
        pages[0]["sha256"] = "0" * 64
    elif case == "source":
        record["reference_answer"]["source_sha256"] = "0" * 64
    elif case == "role":
        pages[0]["document_role"] = "question"
    else:
        candidate["answer_alignment"]["solution_page_binding"] = None
    with pytest.raises(reader.BatchIntegrityError):
        item(candidate, record)


def test_answer_page_limit_and_question_page_limit_stay_distinct(candidate):
    pages = candidate["answer_alignment"]["solution_page_binding"]["pages"]
    pages.extend(copy.deepcopy(pages[-1]) for _ in range(7))
    with pytest.raises(pipeline.FastlaneError) as caught:
        pipeline.candidate_import_record(candidate)
    assert caught.value.code == "import_page_count_invalid"
    candidate["answer_alignment"]["solution_page_binding"]["pages"] = pages[:2]
    candidate["page_binding"]["pages"].append(
        copy.deepcopy(candidate["page_binding"]["pages"][0])
    )
    with pytest.raises(pipeline.FastlaneError) as caught:
        pipeline.candidate_import_record(candidate)
    assert caught.value.code == "import_page_count_invalid"


@pytest.mark.parametrize(
    ("case", "error_prefix"),
    [
        ("valid", None),
        (
            "fastlane_missing",
            "derived_candidate_queue_mismatch:fastlane_candidates.jsonl",
        ),
        ("hybrid_extra", "derived_candidate_queue_mismatch:hybrid_visual_queue.jsonl"),
        ("reverse", "candidate_import_import_page_order_invalid:"),
        ("source", "candidate_import_answer_source_mismatch:"),
        ("schema", "candidate_import_schema_invalid:"),
        ("missing_page", "candidate_import_answer_pages_mismatch:"),
    ],
)
def test_batch_validator_on_isolated_real_batch(
    tmp_path, monkeypatch, case, error_prefix
):
    batch_id = "NATIVE-BATCH-NV2W2-PKG043-A02"
    source = NATIVE / "batches" / batch_id
    target = tmp_path / batch_id
    target.mkdir()
    for name in (
        "batch_summary.json",
        "question_candidates.jsonl",
        "candidate_import_records.jsonl",
        "fastlane_candidates.jsonl",
        "hybrid_visual_queue.jsonl",
    ):
        shutil.copyfile(source / name, target / name)
    monkeypatch.setattr(pipeline, "BATCH_ROOT", tmp_path)
    if case in {"fastlane_missing", "hybrid_extra"}:
        name = (
            "fastlane_candidates.jsonl"
            if case == "fastlane_missing"
            else "hybrid_visual_queue.jsonl"
        )
        rows = pipeline.load_jsonl(target / name)
        if case == "fastlane_missing":
            rows.pop()
        else:
            rows.append(copy.deepcopy(rows[0]))
        pipeline.write_jsonl(target / name, rows)
    elif case != "valid":
        path = target / "candidate_import_records.jsonl"
        rows = pipeline.load_jsonl(path)
        record = next(
            row for row in rows if row["import_record_id"] == "PQ-9844af38112b410b"
        )
        answer = record["reference_answer"]
        if case == "reverse":
            answer["page_anchors"].reverse()
        elif case == "source":
            answer["source_sha256"] = "0" * 64
        elif case == "schema":
            answer["page_anchors"] = None
        elif case == "missing_page":
            answer["page_anchors"].pop()
        pipeline.write_jsonl(path, rows)
    report = pipeline.validate_batch(batch_id)
    if error_prefix is None:
        assert report["valid"] is True, report["errors"]
    else:
        assert report["valid"] is False
        assert any(error.startswith(error_prefix) for error in report["errors"])

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.question_processing_progress import (
    ALLOWED_GAPS,
    QuestionProcessingProgressReader,
    filter_progress_response,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)


def browser_headers(server) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def recursive_keys(value) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value] + [
            key for child in value.values() for key in recursive_keys(child)
        ]
    if isinstance(value, list):
        return [key for child in value for key in recursive_keys(child)]
    return []


@pytest.fixture(scope="module")
def real_progress() -> dict[str, dict]:
    reader = QuestionProcessingProgressReader(SHCHEM_ROOT)
    return {
        scope: reader.list_progress(
            scope=scope,
            paper_id=None,
            theme_id=None,
            gap=None,
            limit=100,
            offset=0,
        )
        for scope in ("wave1", "master")
    }


def test_real_wave_and_master_counts_are_projected_not_embedded(real_progress):
    wave = real_progress["wave1"]
    master = real_progress["master"]

    assert wave["scope_summary"]["hierarchy_inventory"] == {
        "paper": 5,
        "theme_big_question": 25,
        "printed_question": 207,
        "atomic_part": 252,
    }
    assert wave["total"] == 25
    assert wave["scope_summary"]["atomic_total"] == 252
    assert wave["scope_summary"]["visual_scan"]["scanned_atomic"] == 252
    assert wave["scope_summary"]["candidate_parent_chain_overlay"] is None
    assert wave["scope_summary"]["answers"] == {
        "answer_unit_total": 252,
        "aligned": 181,
        "unaligned": 15,
        "absent": 56,
        "other_explicit_status": 0,
        "source_authority_counts": {
            "none": 56,
            "nonofficial_reference": 196,
        },
        "official_answer_count": 0,
        "independently_verified_count": 0,
        "authority_boundary": "source_reference_only_unverified_not_official",
    }

    summary = master["scope_summary"]
    assert summary["hierarchy_inventory"] == {
        "paper": 24,
        "theme_big_question": 68,
        "printed_question": 501,
        "atomic_part": 470,
    }
    assert master["total"] == 48
    assert summary["parent_chain"] == {
        "complete_atomic": 427,
        "pending_atomic": 43,
        "status": "partial",
    }
    assert summary["candidate_parent_chain_overlay"] == {
        "status": "machine_candidate_complete_pending_central_confirmation",
        "candidate_label_zh": "候选整理43/43完成",
        "central_label_zh": "中央原记录43条待确认",
        "primary_unit": "complete_theme_big_question",
        "candidate_completed_source_master_atomics": 43,
        "candidate_total_source_master_atomics": 43,
        "candidate_remaining_source_master_atomics": 0,
        "complete_theme_batches": 5,
        "printed_questions": 41,
        "effective_atomics": 56,
        "split_source_master_atomics": 10,
        "central_master_atomic_total": 470,
        "central_parent_chain_complete_atomics": 427,
        "central_parent_chain_pending_atomics": 43,
        "central_master_modified": False,
        "central_applied": False,
        "machine_candidate": True,
        "human_reviewed": False,
    }
    visual_scan = summary["visual_scan"]
    assert {
        field: visual_scan[field]
        for field in ("status", "scanned_atomic", "unscanned_atomic", "total_atomic")
    } == {
        "status": "partial_candidate_scan",
        "scanned_atomic": 397,
        "unscanned_atomic": 73,
        "total_atomic": 470,
    }
    assert visual_scan["coverage_kinds"] == {
        "exact_existing_visual_scan": 169,
        "direct_new_visual_scan": 214,
        "alias_existing_visual_scan": 14,
        "unscanned": 73,
    }
    assert visual_scan["human_visual_reviewed"] is False
    assert (
        visual_scan["scanned_atomic"] + visual_scan["unscanned_atomic"]
        == summary["hierarchy_inventory"]["atomic_part"]
    )
    assert {
        field: value["native"]["covered_atomic"]
        for field, value in summary["field_coverage"].items()
    } == {
        "item_type": 448,
        "K": 470,
        "A": 470,
        "C": 389,
        "R": 470,
        "RP": 437,
        "D": 156,
    }
    assert summary["ten_factor_difficulty"] == {
        "status": "partial_candidate_non_measured",
        "candidate_evidence_atomic": 397,
        "pending_atomic": 73,
        "total_atomic": 470,
        "factor_count_per_scanned_unit": 10,
        "measured_difficulty_atomic": 0,
        "measured_difficulty_verified": False,
    }
    assert summary["answers"]["aligned"] == 358
    assert summary["answers"]["unaligned"] == 13
    assert summary["answers"]["absent"] == 106
    assert master["unassigned_parent_chain"]["atomic_total"] == 43
    assert master["integrity"]["fixed_total_embedded"] is False


def test_complete_theme_is_the_unit_and_missing_fields_stay_honest(real_progress):
    for scope, response in real_progress.items():
        assert response["product"]["primary_unit"] == "complete_theme_big_question"
        assert all(
            item["primary_unit"] == "complete_theme_big_question"
            and item["standalone_choice_section_created"] is False
            and item["atomic_total"] >= 1
            for item in response["items"]
        )
        assert response["scope_summary"]["textbook_directory_mapping"] == {
            "field_availability": "unavailable",
            "status": "unavailable_not_in_product",
            "mapped_atomic": None,
            "unmapped_atomic": None,
            "total_atomic": response["scope_summary"]["atomic_total"],
            "note_zh": "当前产品没有题级教材目录映射字段；不能把知识标签反推成教材目录映射。",
        }
        assert all(
            item["textbook_directory_mapping"]["mapped_atomic"] is None
            and item["textbook_directory_mapping"]["unmapped_atomic"] is None
            for item in response["items"]
        ), scope

    master = real_progress["master"]
    uncertain = [
        item
        for item in master["items"]
        if item["paper"]["identity"]["region_or_school"]["status"]
        == "candidate_uncertain"
    ]
    assert uncertain
    assert any(
        "待核验" in str(item["paper"]["identity"]["region_or_school"]["value"])
        or "公众号" in str(item["paper"]["identity"]["region_or_school"]["value"])
        for item in uncertain
    )
    assert master["scope_summary"]["paper_identity"] == {
        "papers_with_theme_progress": 20,
        "all_identity_values_present": 18,
        "unknown_or_candidate_uncertain": 2,
        "papers_without_theme_progress": 4,
    }


def test_projection_has_no_question_answer_locator_or_student_leak(real_progress):
    for response in real_progress.values():
        keys = {key.casefold() for key in recursive_keys(response)}
        assert (
            not {
                "question_text",
                "stem_text",
                "answer_text",
                "reference_answer_text",
                "solution_path_zh",
                "source_url",
                "original_url",
                "local_path",
                "source_path",
                "file_path",
                "crop_path",
                "student_id",
                "student_profile_id",
                "upload_id",
                "attempt_id",
            }
            & keys
        )
        assert not any(
            key.endswith(("_path", "_url", "_sha256", "_hash", "_bindings"))
            for key in keys
        )
        encoded = json.dumps(response, ensure_ascii=False).casefold()
        for forbidden in (
            "http://",
            "https://",
            "file://",
            "c:\\users",
            "sh-chem-db/",
            "kb/",
            "staging/",
            "runtime/",
        ):
            assert forbidden not in encoded


def test_filters_pagination_and_repeated_requests_are_stable(real_progress):
    base = real_progress["master"]
    paper_id = base["items"][0]["paper"]["id"]
    first = filter_progress_response(
        base,
        scope="master",
        paper_id=paper_id,
        theme_id=None,
        gap="textbook_mapping",
        limit=2,
        offset=0,
    )
    second = filter_progress_response(
        base,
        scope="master",
        paper_id=paper_id,
        theme_id=None,
        gap="textbook_mapping",
        limit=2,
        offset=0,
    )
    assert first == second
    assert first["count"] <= 2
    assert first["total"] >= first["count"]
    assert all(item["paper"]["id"] == paper_id for item in first["items"])
    assert all(
        any(gap["category"] == "textbook_mapping" for gap in item["next_gaps"])
        for item in first["items"]
    )


def test_openapi_is_strict_and_validates_real_responses(real_progress):
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    operation = document["paths"]["/api/v1/kb/question-processing-progress"]
    assert set(operation) == {"get"}
    description = operation["get"]["description"].casefold()
    for phrase in (
        "read-only",
        "complete theme",
        "never fixed api totals",
        "unavailable",
        "question text",
        "answer text",
        "student data",
        "no live fallback",
    ):
        assert phrase in description
    validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/QuestionProcessingProgressEnvelope",
            "components": document["components"],
        }
    )
    for response in real_progress.values():
        envelope = {
            "contract_version": "shchem.gateway.v1",
            "request_id": "req-question-processing-progress",
            "data": response,
        }
        assert list(validator.iter_errors(envelope)) == []
    for name, schema in document["components"]["schemas"].items():
        if (
            name.startswith("QuestionProcessingProgress")
            and schema.get("type") == "object"
        ):
            assert schema.get("additionalProperties") is False, name


class _FrozenProgress:
    def __init__(self, values: dict[str, dict]):
        self.values = values

    def question_processing_progress(self, **kwargs):
        scope = kwargs["scope"]
        return filter_progress_response(self.values[scope], **kwargs)


class _NoLiveProgress:
    def list_progress(self, **kwargs):  # pragma: no cover - assertion path
        raise AssertionError("frozen mode must not call live progress")


class _OldFrozenRelease:
    pass


def test_http_is_zero_write_and_frozen_mode_never_falls_back_live(real_progress):
    with tempfile.TemporaryDirectory() as temp_name:
        state_root = Path(temp_name)
        config = build_config(state_root)
        with running_server(config) as server:
            server.service.frozen_browse = _FrozenProgress(real_progress)
            server.service.question_processing_progress_reader = _NoLiveProgress()
            path = "/api/v1/kb/question-processing-progress?scope=master&limit=1"
            status1, body1, _ = request(
                server, "GET", path, headers=browser_headers(server), timeout=10
            )
            status2, body2, _ = request(
                server, "GET", path, headers=browser_headers(server), timeout=10
            )
            assert status1 == status2 == 200
            assert body1["data"] == body2["data"]
            assert body1["data"]["count"] == 1
            assert not (state_root / "audit/gateway.jsonl").exists()

            server.service.frozen_browse = _OldFrozenRelease()
            status, body, _ = request(
                server, "GET", path, headers=browser_headers(server), timeout=10
            )
            assert status == 503
            assert body["error"]["code"] == "browse_snapshot_runtime_incompatible"


def test_http_rejects_missing_repeated_and_unsupported_queries(real_progress):
    with tempfile.TemporaryDirectory() as temp_name:
        config = build_config(Path(temp_name))
        with running_server(config) as server:
            server.service.frozen_browse = _FrozenProgress(real_progress)
            headers = browser_headers(server)
            paths = (
                "/api/v1/kb/question-processing-progress",
                "/api/v1/kb/question-processing-progress?scope=master&scope=wave1",
                "/api/v1/kb/question-processing-progress?scope=master&unknown=x",
                "/api/v1/kb/question-processing-progress?scope=master&gap=not-real",
                "/api/v1/kb/question-processing-progress?scope=master&limit=101",
            )
            for path in paths:
                status, _, _ = request(server, "GET", path, headers=headers, timeout=10)
                assert status == 400, path
            status, _, _ = request(
                server,
                "GET",
                "/api/v1/kb/question-processing-progress?scope=master",
                headers={
                    "Origin": "https://example.invalid",
                    "Sec-Fetch-Site": "cross-site",
                },
                timeout=10,
            )
            assert status == 403

    assert ALLOWED_GAPS == {
        "parent_chain",
        "visual_scan",
        "textbook_mapping",
        "classification",
        "difficulty",
        "identity",
        "source",
        "answer",
    }

"""A later uploaded theme retains its original order without claiming a full paper."""
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1 import intake_batches_v2 as core

spec = importlib.util.spec_from_file_location(
    "partial_scope_fixture", Path(__file__).with_name("test_desktop_visual_import_v2.py")
)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


def _candidate(position):
    request = support._lean_shard("question")
    raw = support._lean_question_fragment(request)
    raw["theme_fragments"][0]["sequence_in_paper"] = position
    raw["theme_fragments"][0]["theme_number"] = str(position)
    original = deepcopy(raw)
    fragment = core._validate_fragment(raw, request=request)
    records, batch_sha = support._lean_source_records([request])
    candidate = core._build_candidate(
        batch_id=request.batch_id, batch_sha256=batch_sha, source_records=records,
        requests=[request], fragments=[fragment],
    )
    assert raw == original
    return candidate


@pytest.mark.parametrize("position", [1, 2, 3, 4, 5])
def test_complete_theme_excerpt_retains_observed_source_position(position):
    candidate = _candidate(position)
    assert candidate["paper"]["source_scope"] == "uploaded_selection"
    theme = candidate["paper"]["theme_big_questions"][0]
    assert theme["sequence_in_paper"] == position and theme["theme_number"] == str(position)
    assert candidate["candidate_status"] == "candidate_only"
    assert not candidate["human_reviewed"] and not candidate["central_question_bank_write"]
    assert core.validate_candidate_v2(candidate) == candidate


@pytest.mark.parametrize("position", [0, -1, True, 1.0, None, "2"])
def test_invalid_candidate_positions_remain_rejected(position):
    candidate = _candidate(2)
    candidate["paper"]["theme_big_questions"][0]["sequence_in_paper"] = position
    with pytest.raises(core.IntakeBatchV2Error, match="source positions"):
        core.validate_candidate_v2(candidate)


def test_duplicate_source_positions_remain_rejected():
    candidate = _candidate(2)
    candidate["paper"]["theme_big_questions"].append(deepcopy(candidate["paper"]["theme_big_questions"][0]))
    with pytest.raises(core.IntakeBatchV2Error, match="source positions"):
        core.validate_candidate_v2(candidate)


def test_legacy_without_explicit_selection_scope_keeps_contiguous_contract():
    candidate = _candidate(2)
    del candidate["paper"]["source_scope"]
    with pytest.raises(core.IntakeBatchV2Error, match="source positions"):
        core.validate_candidate_v2(candidate)
    candidate["paper"]["theme_big_questions"][0]["sequence_in_paper"] = 1
    assert core.validate_candidate_v2(candidate) == candidate


@pytest.mark.parametrize("scope", [None, True, "complete_paper", "unknown", []])
def test_selection_scope_cannot_claim_paper_completeness(scope):
    candidate = _candidate(2)
    candidate["paper"]["source_scope"] = scope
    with pytest.raises(core.IntakeBatchV2Error, match="source scope"):
        core.validate_candidate_v2(candidate)

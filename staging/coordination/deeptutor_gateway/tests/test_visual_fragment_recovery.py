"""Actual-core recovery from frozen synthetic observations, without a provider."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from test_desktop_visual_import_v2 import (
    _lean_answer_fragment,
    _lean_question_fragment,
    _png_bytes,
)

from integrations.deeptutor_shchem_v1 import desktop_visual_import_v2 as bridge
from integrations.deeptutor_shchem_v1 import intake_batches_v2 as core

BATCH_ID = "DESKTOPBATCH-" + "f" * 32


def _hash(value):
    return core.sha256_bytes(core.canonical_json_bytes(value))


def _rename(value, sequence):
    if isinstance(value, dict):
        return {key: _rename(item, sequence) for key, item in value.items()}
    if isinstance(value, list):
        return [_rename(item, sequence) for item in value]
    if isinstance(value, str):
        return value.replace("LEAN-", f"RECOVERY-{sequence}-")
    return value


def _frozen_inputs(
    sequences=(2,), *, max_pages_per_shard=1, question_files=None, renderer=None
):
    questions = question_files or [
        core.IntakeBatchFile(
            filename=f"synthetic-question-{sequence}.png",
            mime_type="image/png",
            content=_png_bytes((20 + sequence, 70, 150)),
            source_file_id=f"SRC-RECOVERY-Q-{sequence}",
        )
        for sequence in sequences
    ]
    answers = [
        core.IntakeBatchFile(
            filename="synthetic-answer.png",
            mime_type="image/png",
            content=_png_bytes((70, 150, 20)),
            source_file_id="SRC-RECOVERY-A",
        )
    ]
    records, pages = core._prepare_sources(
        question_files=questions,
        answer_files=answers,
        handout_files=(),
        renderer=renderer,
    )
    requests = core._make_shards(
        pages, batch_id=BATCH_ID, max_pages_per_shard=max_pages_per_shard
    )
    fragments = {}
    question_number = 0
    for request in requests:
        if request.source_role == "answer":
            raw = _lean_answer_fragment(request)
        else:
            sequence = sequences[question_number]
            question_number += 1
            raw = _rename(_lean_question_fragment(request), sequence)
            raw["theme_fragments"][0]["sequence_in_paper"] = sequence
            raw["theme_fragments"][0]["theme_number"] = {
                2: "二",
                3: "三",
                4: "四",
                5: "五",
            }[sequence]
        fragments[request.shard_id] = raw
    return (
        {
            "question_files": questions,
            "answer_files": answers,
            "handout_files": (),
            "stored_fragments": fragments,
            "expected_fragment_sha256": {
                key: _hash(raw) for key, raw in fragments.items()
            },
            # This is frozen before any test mutation. Never recompute it from a
            # changed source, page, renderer, role, or ordering.
            "expected_source_subject_sha256": _hash(core._batch_subject(records)),
            "batch_id": BATCH_ID,
            "renderer": renderer,
            "max_pages_per_shard": max_pages_per_shard,
        },
        records,
        requests,
    )


@pytest.fixture(autouse=True)
def no_provider_calls(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail(
            "offline recovery attempted to invoke or construct a provider runner"
        )

    monkeypatch.setattr(core, "_invoke_provider", forbidden)
    monkeypatch.setattr(core.MultiFileVisualIntakeV2, "__init__", forbidden)


def _assert_gates(candidate):
    assert candidate["candidate_status"] == "candidate_only"
    assert candidate["requires_teacher_review"] is True
    for gate in (
        "human_reviewed",
        "retrieval_ready",
        "publication_allowed",
        "central_question_bank_write",
    ):
        assert candidate[gate] is False
    assert candidate["teacher_operations"] == []
    assert candidate["paper"]["source_scope"] == "uploaded_selection"
    assert candidate["input_contract"]["source_text_layer_supplied"] is False
    assert candidate["input_contract"]["fallback_allowed"] is False
    assert any(
        row["code"] == "stored_visual_fragments_recovered"
        for row in candidate["review_blockers"]
    )
    for theme in candidate["paper"]["theme_big_questions"]:
        for printed in theme["printed_questions"]:
            for atomic in printed["atomic_parts"]:
                assert atomic["cognitive_difficulty"]["human_verified"] is False
                assert atomic["cognitive_difficulty"]["student_data_used"] is False
                assert atomic["answer"]["independently_verified"] is False


@pytest.mark.parametrize("sequence", [2, 3, 4, 5])
def test_actual_core_recovers_later_theme_without_renumbering_or_gate_promotion(
    sequence,
):
    assert (
        Path(core.__file__)
        .resolve()
        .as_posix()
        .endswith("integrations/deeptutor_shchem_v1/intake_batches_v2.py")
    )
    assert core.recover_visual_candidate_from_fragments.__module__ == core.__name__
    inputs, records, requests = _frozen_inputs((sequence,))
    before_fragments = deepcopy(inputs["stored_fragments"])
    recovered = core.recover_visual_candidate_from_fragments(**inputs)
    assert core.validate_candidate_v2(recovered) == recovered
    assert inputs["stored_fragments"] == before_fragments
    assert recovered["batch"]["sources"] == records
    assert (
        recovered["batch"]["batch_sha256"] == inputs["expected_source_subject_sha256"]
    )
    assert [
        theme["sequence_in_paper"]
        for theme in recovered["paper"]["theme_big_questions"]
    ] == [sequence]
    assert [
        receipt["shard_id"] for receipt in recovered["batch"]["shard_receipts"]
    ] == [request.shard_id for request in requests]
    expected_evidence = [
        row for raw in before_fragments.values() for row in raw["evidence"]
    ]
    assert recovered["evidence"] == sorted(
        expected_evidence, key=lambda row: row["evidence_id"]
    )
    _assert_gates(recovered)


def test_multiple_original_themes_two_through_five_keep_positions_and_exact_receipts():
    inputs, _, requests = _frozen_inputs((2, 3, 4, 5))
    recovered = core.recover_visual_candidate_from_fragments(**inputs)
    assert [
        theme["sequence_in_paper"]
        for theme in recovered["paper"]["theme_big_questions"]
    ] == [2, 3, 4, 5]
    for request, receipt in zip(
        requests, recovered["batch"]["shard_receipts"], strict=True
    ):
        normalized = core._validate_fragment(
            inputs["stored_fragments"][request.shard_id], request=request
        )
        assert receipt["fragment_sha256"] == _hash(normalized)
        manifest = {
            "schema_version": core.INTAKE_BATCH_VISUAL_REQUEST_V2_SCHEMA_VERSION,
            "batch_id": request.batch_id,
            "shard_id": request.shard_id,
            "shard_index": request.shard_index,
            "source_role": request.source_role,
            "input_mode": core.DIRECT_PAGE_PIXEL_MODE,
            "pages": [page.public_manifest() for page in request.pages],
        }
        assert receipt["request_manifest_sha256"] == _hash(manifest)
    _assert_gates(recovered)


@pytest.mark.parametrize(
    "field,change",
    [
        ("stored_fragments", "missing"),
        ("stored_fragments", "extra"),
        ("expected_fragment_sha256", "missing"),
        ("expected_fragment_sha256", "extra"),
    ],
)
def test_missing_or_extra_shards_are_rejected(field, change):
    inputs, _, _ = _frozen_inputs()
    key = next(iter(inputs[field]))
    if change == "missing":
        del inputs[field][key]
    else:
        inputs[field][BATCH_ID + ":question:0999"] = deepcopy(inputs[field][key])
    with pytest.raises(core.IntakeBatchV2Error) as error:
        core.recover_visual_candidate_from_fragments(**inputs)
    assert error.value.code == "stored_visual_fragments_incomplete"


@pytest.mark.parametrize("change", ["fragment", "expected_hash"])
def test_stored_observation_hash_change_is_rejected(change):
    inputs, _, _ = _frozen_inputs()
    key = next(iter(inputs["stored_fragments"]))
    if change == "fragment":
        inputs["stored_fragments"][key]["warnings"].append("synthetic mutation")
    else:
        inputs["expected_fragment_sha256"][key] = "0" * 64
    with pytest.raises(core.IntakeBatchV2Error) as error:
        core.recover_visual_candidate_from_fragments(**inputs)
    assert error.value.code == "stored_visual_fragment_changed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_file_id", "SRC-OTHER"),
        ("source_role", "answer"),
        ("page_number", 2),
        ("page_sha256", "0" * 64),
    ],
)
def test_evidence_source_page_role_or_hash_mismatch_is_rejected_even_with_new_fragment_hash(
    field, value
):
    inputs, _, _ = _frozen_inputs()
    key = next(iter(inputs["stored_fragments"]))
    raw = inputs["stored_fragments"][key]
    raw["evidence"][0][field] = value
    inputs["expected_fragment_sha256"][key] = _hash(raw)
    with pytest.raises(core.IntakeBatchV2Error) as error:
        core.recover_visual_candidate_from_fragments(**inputs)
    assert error.value.code == "provider_output_evidence_invalid"


@pytest.mark.parametrize(
    "field,value",
    [("source_role", "answer"), ("shard_id", "BATCH-OTHER:question:0001")],
)
def test_fragment_shard_identity_and_role_must_match_current_request(field, value):
    inputs, _, _ = _frozen_inputs()
    key = next(iter(inputs["stored_fragments"]))
    raw = inputs["stored_fragments"][key]
    raw[field] = value
    inputs["expected_fragment_sha256"][key] = _hash(raw)
    with pytest.raises(core.IntakeBatchV2Error):
        core.recover_visual_candidate_from_fragments(**inputs)


@pytest.mark.parametrize("change", ["bytes", "id", "order", "role", "expected_hash"])
def test_current_source_scope_cannot_drift_from_frozen_manifest(change):
    inputs, _, _ = _frozen_inputs((2, 3))
    if change == "bytes":
        inputs["question_files"][0] = replace(
            inputs["question_files"][0], content=_png_bytes((180, 10, 10))
        )
    elif change == "id":
        inputs["question_files"][0] = replace(
            inputs["question_files"][0], source_file_id="SRC-NEW"
        )
    elif change == "order":
        inputs["question_files"].reverse()
    elif change == "role":
        inputs["handout_files"] = inputs["question_files"]
        inputs["question_files"] = []
    else:
        inputs["expected_source_subject_sha256"] = "0" * 64
    with pytest.raises(core.IntakeBatchV2Error) as error:
        core.recover_visual_candidate_from_fragments(**inputs)
    assert error.value.code == "stored_visual_sources_changed"


class _SyntheticRenderer:
    def __init__(self):
        self.page_count = 1
        self.recipe = "synthetic-recipe-1"

    def render(self, _source, *, source_role):
        assert source_role == "question"
        return [
            core.RenderedPixelPage(
                pixels=_png_bytes((50, 70, 90)),
                mime_type="image/png",
                width=24,
                height=32,
                render_recipe_sha256=core.sha256_bytes(self.recipe.encode()),
            )
            for _ in range(self.page_count)
        ]


@pytest.mark.parametrize(
    "change", ["source_bytes_same_pixels", "extra_page_same_shard", "render_recipe"]
)
def test_unobserved_page_or_source_changes_are_rejected_by_frozen_source_subject(
    change,
):
    renderer = _SyntheticRenderer()
    source = core.IntakeBatchFile(
        filename="synthetic.pdf",
        mime_type="application/pdf",
        content=b"synthetic-pdf-v1",
        source_file_id="SRC-SYNTHETIC-PDF",
    )
    inputs, _, _ = _frozen_inputs(
        question_files=[source], renderer=renderer, max_pages_per_shard=2
    )
    if change == "source_bytes_same_pixels":
        inputs["question_files"][0] = replace(source, content=b"synthetic-pdf-v2")
    elif change == "extra_page_same_shard":
        renderer.page_count = 2
    else:
        renderer.recipe = "synthetic-recipe-2"
    with pytest.raises(core.IntakeBatchV2Error) as error:
        core.recover_visual_candidate_from_fragments(**inputs)
    assert error.value.code == "stored_visual_sources_changed"


def _desktop_request(inputs):
    groups = {}
    for role in ("question", "answer", "handout"):
        groups[role + "_files"] = [
            bridge.DesktopSourceFile(
                role=role,
                order_index=index,
                filename=source.filename,
                mime_type=source.mime_type,
                content=source.content,
                group_id="synthetic-recovery",
                source_file_id=source.source_file_id,
            )
            for index, source in enumerate(inputs[role + "_files"], 1)
        ]
    return bridge.DesktopImportRequest.from_sources(
        **groups, batch_id=inputs["batch_id"], source_type="合成离线恢复"
    )


def _runner(inputs):
    return core.StoredVisualFragmentRecoveryV2(
        **{
            field: inputs[field]
            for field in (
                "stored_fragments",
                "expected_fragment_sha256",
                "expected_source_subject_sha256",
                "renderer",
                "max_pages_per_shard",
            )
        }
    )


def test_offline_runner_uses_bridge_real_archive_and_candidate_cas(tmp_path):
    inputs, _, _ = _frozen_inputs((2, 3, 4, 5))
    runner = _runner(inputs)
    assert not hasattr(runner, "provider")
    archive = tmp_path / "synthetic-archive"
    coordinator = bridge.DesktopImportCoordinatorV2(
        visual_runner=runner, archive_root=archive, max_pages_per_shard=1
    )
    result = coordinator.process(_desktop_request(inputs), visual_confirmation=True)
    assert result.visual_status == "completed"
    assert result.visual_candidate is not None and result.visual_revision_token
    _assert_gates(result.visual_candidate)
    cas = core.CandidateCAS.open(archive / "candidates" / BATCH_ID)
    snapshot = cas.snapshot()
    assert snapshot["candidate"] == result.visual_candidate
    assert snapshot["revision_token"] == result.visual_revision_token
    assert (archive / "batches" / (BATCH_ID + ".json")).is_file()
    for page in result.pixel_pages:
        raw = (archive / page["relative_path"]).read_bytes()
        assert core.sha256_bytes(raw) == page["page_sha256"]


def test_offline_runner_cannot_bypass_required_durable_archive():
    inputs, _, _ = _frozen_inputs()
    coordinator = bridge.DesktopImportCoordinatorV2(
        visual_runner=_runner(inputs), max_pages_per_shard=1
    )
    result = coordinator.process(_desktop_request(inputs), visual_confirmation=True)
    assert result.visual_status != "completed"
    assert result.visual_candidate is None
    assert any(row["code"] == "pixel_archive_required" for row in result.blockers)

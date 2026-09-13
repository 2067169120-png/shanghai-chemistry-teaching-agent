from __future__ import annotations

import hashlib
import json
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1 import paper_export_workbench as workbench
from integrations.deeptutor_shchem_v1.candidate_review import CandidateCropPayload
from integrations.deeptutor_shchem_v1.paper_export_renderer import ARTIFACT_FILENAMES
from integrations.deeptutor_shchem_v1.paper_export_workbench import (
    PaperExportJobManager,
    PaperExportWorkbenchError,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    build_config,
    request,
    running_server,
)

SNAPSHOT = "a" * 64
STALE_SNAPSHOT = "b" * 64
JOB_ID = "WBEXP-" + "c" * 32
ANSWER_A1 = "NONOFFICIAL-ANSWER-A1"
ANSWER_A2 = "NONOFFICIAL-ANSWER-A2"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _selection(
    *,
    unit: str = "theme",
    scope: str = "master",
    theme_id: str = "T1",
    target: str | None = None,
    snapshot: str = SNAPSHOT,
) -> dict[str, Any]:
    return {
        "scope": scope,
        "selection_unit": unit,
        "theme_id": theme_id,
        "target_atomic_id": target,
        "expected_data_snapshot_id": snapshot,
    }


def _request_payload(*, selections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "title_zh": "氧化还原主题练习",
        "subtitle_zh": "教师个人工作台导出",
        "duration_minutes": 20,
        "numbering_mode": "continuous_across_paper",
        "score_per_atomic": 2,
        "answer_space_lines": 3,
        "selections": selections or [_selection()],
    }


def _dependency(kind: str, prior: list[str]) -> dict[str, Any]:
    return {
        "kind": kind,
        "prior_atomic_part_ids": prior,
        "explicit_prior_edge_count": len(prior),
        "status": "validated_explicit",
    }


def _atomic(
    atomic_id: str,
    printed_id: str,
    sequence: int,
    *,
    prior: list[str] | None = None,
) -> dict[str, Any]:
    prior = prior or []
    return {
        "atomic_part_id": atomic_id,
        "printed_question_id": printed_id,
        "printed_question_number": str(sequence),
        "printed_sequence": sequence,
        "atomic_sequence_in_printed": 1,
        "item_type": "short_answer",
        "label_summary": {
            "primary_K": ["K11"],
            "A": ["A03"],
            "C": ["C03"],
            "R": ["R05"],
            "RP": ["RP01"],
        },
        "answer": {
            "availability": "present_part_aligned",
            "source_authority": "nonofficial",
            "has_quality_note": True,
        },
        "dependency": _dependency(
            "independent" if not prior else "one_prior_part", prior
        ),
        "detail_endpoint": f"/api/test/{atomic_id}",
        "alias_units": [],
    }


def _catalog(*, snapshot: str = SNAPSHOT) -> dict[str, Any]:
    rows = [
        _atomic("A1", "P1", 1),
        _atomic("A2", "P2", 2, prior=["A1"]),
    ]
    return {
        "data_snapshot_id": snapshot,
        "scope": "master",
        "schema_version": "shchem.theme-workbench.v1",
        "counts": {},
        "papers": [
            {
                "paper": {
                    "id": "PAPER-1",
                    "title": "合成测试来源卷",
                    "source_metadata": {},
                },
                "theme_groups": [
                    {
                        "theme": {
                            "id": "T1",
                            "title": "氧化还原反应",
                            "sequence": 1,
                            "sequence_status": "known_explicit",
                            "page_span": {
                                "start_page": 1,
                                "end_page": 1,
                                "page_numbers": [1],
                                "status": "explicit_visual_evidence_union",
                            },
                            "parent_chain_status": "complete",
                        },
                        "counts": {"atomic": 2},
                        "shared_context": {
                            "context_summary_zh": "同一反应过程中的连续两问。",
                            "context_status": "candidate_context",
                            "material_count": 0,
                            "materials": [],
                        },
                        "dependencies": {
                            "independent": 1,
                            "shared_material_only": 0,
                            "one_prior_part": 1,
                            "multiple_prior_parts": 0,
                            "per_alias_unit": 0,
                            "explicit_prior_edge_count": 1,
                            "blocked": 0,
                        },
                        "atomic_chain": rows,
                    }
                ],
            }
        ],
        "unassigned_pending_review": {
            "status": "none",
            "count": 0,
            "reason_zh": None,
            "atomic_chain": [],
        },
        "authority": {"publication_allowed": False},
        "integrity": {
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "complete_scope_coverage": True,
            "no_duplicate_atomic_parts": True,
            "explicit_order_only": True,
            "dependency_edges_validated": True,
            "answer_text_excluded": True,
            "pixel_reuse_allowed": False,
            "fail_closed": True,
        },
    }


def _details() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for atomic_id, answer in (("A1", ANSWER_A1), ("A2", ANSWER_A2)):
        result[atomic_id] = {
            "visible_summary_zh": f"{atomic_id} 的题面",
            "evidence_descriptors": [
                {
                    "evidence_role": "question",
                    "crop_id": f"CROP-{atomic_id}",
                }
            ],
            "reference_answer": {
                "availability": "present_part_aligned",
                "reference_answer_text": answer,
                "source_authority": "nonofficial_reference",
                "independently_verified": False,
            },
            "candidate_analysis": {
                "solution_path_zh": [f"{atomic_id} 的教师候选解析。"]
            },
            "risks_and_limits": {"common_errors_zh": ["注意守恒。"]},
            "source_boundary_zh": "本地非官方参考答案，按题对齐。",
        }
    return result


def _catalog_with_reused_material_id() -> dict[str, Any]:
    catalog = _catalog()
    first_paper = catalog["papers"][0]
    first_theme = first_paper["theme_groups"][0]
    first_theme["atomic_chain"] = [_atomic("A1", "P1", 1)]
    first_theme["counts"] = {"atomic": 1}
    first_theme["dependencies"] = {
        "independent": 1,
        "shared_material_only": 0,
        "one_prior_part": 0,
        "multiple_prior_parts": 0,
        "per_alias_unit": 0,
        "explicit_prior_edge_count": 0,
        "blocked": 0,
    }
    first_theme["shared_context"] = {
        "context_summary_zh": "第一份来源卷的共享装置图。",
        "context_status": "candidate_context",
        "material_count": 1,
        "materials": [
            {
                "material_id": "SHARED-MATERIAL",
                "type": "apparatus",
                "page": 1,
                "preview_allowed": True,
                "used_by_atomic_count": 1,
            }
        ],
    }

    second_paper = deepcopy(first_paper)
    second_paper["paper"] = {
        "id": "PAPER-2",
        "title": "另一份合成测试来源卷",
        "source_metadata": {},
    }
    second_theme = second_paper["theme_groups"][0]
    second_theme["theme"] = {
        **second_theme["theme"],
        "id": "T2",
        "title": "第二份来源卷的实验装置",
    }
    second_theme["atomic_chain"] = [_atomic("B1", "P2", 1)]
    second_theme["shared_context"] = {
        **second_theme["shared_context"],
        "context_summary_zh": "第二份来源卷的另一张共享装置图。",
    }
    catalog["papers"].append(second_paper)
    return catalog


def _cross_paper_details() -> dict[str, dict[str, Any]]:
    return {
        atomic_id: {
            "visible_summary_zh": f"{atomic_id} 的题面",
            "evidence_descriptors": [
                {
                    "evidence_role": "shared_material",
                    "crop_id": "SHARED-MATERIAL",
                },
                {
                    "evidence_role": "question",
                    "crop_id": f"QUESTION-{atomic_id}",
                },
            ],
            "reference_answer": {
                "availability": "present_part_aligned",
                "reference_answer_text": f"ANSWER-{atomic_id}",
                "source_authority": "nonofficial_reference",
                "independently_verified": False,
            },
            "source_boundary_zh": f"{atomic_id} 所属来源卷。",
        }
        for atomic_id in ("A1", "B1")
    }


def _catalog_with_selected_shared_material() -> dict[str, Any]:
    catalog = _catalog()
    theme = catalog["papers"][0]["theme_groups"][0]
    theme["shared_context"] = {
        "context_summary_zh": "选中小题使用的主题共享装置图。",
        "context_status": "candidate_context",
        "material_count": 1,
        "materials": [
            {
                "material_id": "SELECTED-SHARED-MATERIAL",
                "type": "apparatus",
                "page": 1,
                "preview_allowed": True,
                "used_by_atomic_count": 1,
            }
        ],
    }
    return catalog


def _selected_atomic_detail() -> dict[str, Any]:
    detail = deepcopy(_details()["A1"])
    detail["evidence_descriptors"].insert(
        0,
        {
            "evidence_role": "shared_material",
            "crop_id": "SELECTED-SHARED-MATERIAL",
        },
    )
    return detail


def _crop(atomic_id: str, crop_id: str) -> CandidateCropPayload:
    assert crop_id == f"CROP-{atomic_id}"
    data = f"PNG-FIXTURE-{atomic_id}".encode("ascii")
    return CandidateCropPayload(data=data, sha256=_sha256(data))


def _fake_render_factory(captured: dict[str, Any]):
    def fake_render(
        bundle: dict[str, Any],
        *,
        output_dir: Path,
        toolchain: Any,
        asset_root: Path | None = None,
    ) -> dict[str, Any]:
        del toolchain, asset_root
        captured["bundle"] = deepcopy(bundle)
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[dict[str, Any]] = []
        for artifact_id, filename in ARTIFACT_FILENAMES.items():
            audience, file_format = artifact_id.split("_", 1)
            data = f"{artifact_id}-fixture".encode("ascii")
            path = output_dir / filename
            path.write_bytes(data)
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "format": file_format,
                    "audience": audience,
                    "path": str(path),
                    "sha256": _sha256(data),
                    "size_bytes": len(data),
                    "page_count": 1,
                }
            )
        return {"machine_blocker_count": 0, "artifacts": artifacts}

    return fake_render


def _wait_for_terminal(
    manager: PaperExportJobManager, job_id: str, *, timeout: float = 20.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = manager.get(job_id)
        if value["status"] in {"completed", "failed"}:
            return value
        time.sleep(0.05)
    pytest.fail(f"export job did not finish within {timeout}s: {manager.get(job_id)}")


def _all_atomic_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        atomic
        for theme in plan["visible"]["theme_sections"]
        for printed in theme["printed_questions"]
        for atomic in printed["atomic_parts"]
    ]


@pytest.mark.parametrize(
    ("item_type", "requested_lines", "expected_lines"),
    [
        ("embedded_single_choice", 4, 0),
        ("embedded_indeterminate_choice", 4, 0),
        ("short_fill", 4, 1),
        ("chemical_equation_or_notation", 4, 1),
        ("reasoned_explanation", 4, 2),
        ("quantitative_calculation", 4, 3),
        ("organic_structure_or_route", 4, 3),
        ("experiment_operation_apparatus_plan", 4, 3),
        ("graph_read_draw_complete", 4, 3),
        ("short_fill", 0, 0),
        ("unknown", 4, 4),
    ],
)
def test_answer_space_lines_are_compacted_by_response_type(
    item_type: str,
    requested_lines: int,
    expected_lines: int,
) -> None:
    assert workbench._compact_answer_space_lines(
        item_type=item_type,
        requested_lines=requested_lines,
    ) == expected_lines


def test_solution_explanation_drops_repeated_answer_provenance_boilerplate() -> None:
    assert workbench._compact_solution_explanation(
        [
            "先依据守恒关系确定反应物比例。",
            "最终答案直接照录来源答案页，不作独立正确性核验。",
        ]
    ) == "先依据守恒关系确定反应物比例。"
    assert workbench._compact_solution_explanation(
        ["最终现象文本直接照录来源答案页，不作独立正确性核验。"]
    ) is None


def _capture_submitted_futures(
    manager: PaperExportJobManager, monkeypatch: pytest.MonkeyPatch
) -> list[Any]:
    futures: list[Any] = []
    original_submit = manager._executor.submit

    def submit(*args: Any, **kwargs: Any) -> Any:
        future = original_submit(*args, **kwargs)
        futures.append(future)
        return future

    monkeypatch.setattr(manager._executor, "submit", submit)
    return futures


def test_request_contract_rejects_unknown_fields_multiple_scopes_and_mixed_snapshots(
    tmp_path: Path,
) -> None:
    manager = PaperExportJobManager(tmp_path)
    callbacks = {
        "theme_catalog_loader": lambda scope: _catalog(),
        "detail_loader": lambda scope, node_id: _details()[node_id],
        "crop_loader": lambda scope, node_id, crop_id: _crop(node_id, crop_id),
    }
    try:
        unknown = _request_payload()
        unknown["client_reported_total_score"] = 99
        with pytest.raises(PaperExportWorkbenchError) as captured:
            manager.start(unknown, **callbacks)
        assert captured.value.code == "paper_export_request_invalid"

        with pytest.raises(PaperExportWorkbenchError) as captured:
            manager.start(
                _request_payload(
                    selections=[
                        _selection(scope="master"),
                        _selection(scope="wave1"),
                    ]
                ),
                **callbacks,
            )
        assert captured.value.code == "multiple_scopes_require_dedup_review"
        assert captured.value.status == 409

        with pytest.raises(PaperExportWorkbenchError) as captured:
            manager.start(
                _request_payload(
                    selections=[
                        _selection(snapshot=SNAPSHOT),
                        _selection(snapshot=STALE_SNAPSHOT),
                    ]
                ),
                **callbacks,
            )
        assert captured.value.code == "theme_snapshot_stale"
        assert list((tmp_path / "paper-export-workbench").glob("WBEXP-*")) == []
    finally:
        manager.shutdown()


def test_fractional_score_per_atomic_is_rejected_before_job_is_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(tmp_path)
    payload = _request_payload()
    payload["score_per_atomic"] = 0.5
    monkeypatch.setattr(workbench, "render_export_bundle", _fake_render_factory({}))
    monkeypatch.setattr(workbench, "_locate_toolchain", lambda: None)
    try:
        with pytest.raises(PaperExportWorkbenchError) as captured:
            manager.start(
                payload,
                theme_catalog_loader=lambda scope: _catalog(),
                detail_loader=lambda scope, node_id: _details()[node_id],
                crop_loader=lambda scope, node_id, crop_id: _crop(
                    node_id, crop_id
                ),
            )
        assert captured.value.code == "paper_export_request_invalid"
        assert captured.value.status == 400
        assert list((tmp_path / "paper-export-workbench").glob("WBEXP-*")) == []
    finally:
        manager.shutdown()


def test_more_than_twenty_expanded_themes_is_rejected_before_queueing(
    tmp_path: Path,
) -> None:
    manager = PaperExportJobManager(tmp_path)
    catalog_loaded = False

    def catalog_loader(scope: str) -> dict[str, Any]:
        nonlocal catalog_loaded
        catalog_loaded = True
        return _catalog()

    try:
        with pytest.raises(PaperExportWorkbenchError) as captured:
            manager.start(
                _request_payload(
                    selections=[
                        _selection(theme_id=f"T{index}") for index in range(1, 22)
                    ]
                ),
                theme_catalog_loader=catalog_loader,
                detail_loader=lambda scope, node_id: _details()[node_id],
                crop_loader=lambda scope, node_id, crop_id: _crop(
                    node_id, crop_id
                ),
            )
        assert captured.value.code == "paper_export_theme_limit_exceeded"
        assert captured.value.status == 400
        assert captured.value.details == {
            "observed_theme_count": 21,
            "maximum_theme_count": 20,
        }
        assert catalog_loaded is False
        assert list((tmp_path / "paper-export-workbench").glob("WBEXP-*")) == []
    finally:
        manager.shutdown()


def test_dependency_expansion_over_five_hundred_points_is_rejected_before_queueing(
    tmp_path: Path,
) -> None:
    manager = PaperExportJobManager(tmp_path)
    catalog = _catalog()
    group = catalog["papers"][0]["theme_groups"][0]
    rows = [
        _atomic(
            f"A{index}",
            f"P{index}",
            index,
            prior=[f"A{index - 1}"] if index > 1 else [],
        )
        for index in range(1, 27)
    ]
    group["atomic_chain"] = rows
    group["counts"] = {"atomic": len(rows)}
    group["dependencies"] = {
        "independent": 1,
        "shared_material_only": 0,
        "one_prior_part": len(rows) - 1,
        "multiple_prior_parts": 0,
        "per_alias_unit": 0,
        "explicit_prior_edge_count": len(rows) - 1,
        "blocked": 0,
    }
    payload = _request_payload(
        selections=[_selection(unit="dependency", target="A26")]
    )
    payload["score_per_atomic"] = 20

    try:
        with pytest.raises(PaperExportWorkbenchError) as captured:
            manager.start(
                payload,
                theme_catalog_loader=lambda scope: deepcopy(catalog),
                detail_loader=lambda scope, node_id: pytest.fail(
                    "oversized basket must fail before loading question details"
                ),
                crop_loader=lambda scope, node_id, crop_id: pytest.fail(
                    "oversized basket must fail before loading crops"
                ),
            )
        assert captured.value.code == "paper_export_total_score_limit_exceeded"
        assert captured.value.status == 400
        assert captured.value.details == {
            "observed_atomic_part_count": 26,
            "score_per_atomic": 20.0,
            "observed_total_score": 520,
            "maximum_total_score": 500,
        }
        assert list((tmp_path / "paper-export-workbench").glob("WBEXP-*")) == []
    finally:
        manager.shutdown()


def test_job_is_queued_then_completes_with_dependency_closure_and_no_student_answer_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(tmp_path)
    gate = threading.Event()
    entered = threading.Event()
    captured: dict[str, Any] = {}
    futures = _capture_submitted_futures(manager, monkeypatch)

    def catalog_loader(scope: str) -> dict[str, Any]:
        assert scope == "master"
        entered.set()
        assert gate.wait(timeout=3)
        return _catalog()

    monkeypatch.setattr(
        workbench, "render_export_bundle", _fake_render_factory(captured)
    )
    monkeypatch.setattr(workbench, "_locate_toolchain", lambda: None)
    try:
        started = manager.start(
            _request_payload(
                selections=[_selection(unit="dependency", target="A2")]
            ),
            theme_catalog_loader=catalog_loader,
            detail_loader=lambda scope, node_id: _details()[node_id],
            crop_loader=lambda scope, node_id, crop_id: _crop(node_id, crop_id),
        )
        assert started["status"] == "queued"
        assert started["progress"]["stage"] == "queued"
        assert "request" not in started and "private_paths" not in started
        assert entered.wait(timeout=2)
        assert manager.get(started["job_id"])["status"] in {"queued", "running"}

        gate.set()
        futures[0].result(timeout=10)
        completed = _wait_for_terminal(manager, started["job_id"])
        assert futures[0].exception() is None
        assert completed["status"] == "completed"
        assert completed["progress"] == {
            "stage": "completed",
            "percent": 100,
            "message_zh": "学生版与教师版 DOCX/PDF 已生成，可直接下载。",
        }
        assert completed["counts"]["atomic_part_count"] == 2
        assert completed["counts"]["total_score"] == 4.0
        assert {item["artifact_id"] for item in completed["artifacts"]} == set(
            ARTIFACT_FILENAMES
        )

        bundle = captured["bundle"]
        theme = bundle["blueprint"]["theme_bundles"][0]
        assert theme["requested_atomic_ids"] == ["A2"]
        assert theme["auto_added_dependency_ids"] == ["A1"]
        assert theme["final_atomic_ids"] == ["A1", "A2"]
        assert bundle["blueprint"]["integrity"][
            "dependency_closure_recomputed_server_side"
        ] is True

        student_rows = _all_atomic_rows(bundle["student_plan"])
        teacher_rows = _all_atomic_rows(bundle["teacher_plan"])
        assert all("teacher_notes" not in row for row in student_rows)
        assert all("teacher_notes" in row for row in teacher_rows)
        student_json = json.dumps(bundle["student_plan"], ensure_ascii=False)
        teacher_json = json.dumps(bundle["teacher_plan"], ensure_ascii=False)
        assert ANSWER_A1 not in student_json and ANSWER_A2 not in student_json
        assert ANSWER_A1 in teacher_json and ANSWER_A2 in teacher_json
        assert "参考答案（非官方，未独立核验）" in teacher_json
    finally:
        gate.set()
        manager.shutdown()


def test_job_state_replace_is_serialized_with_polling_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(tmp_path)
    entered_replace = threading.Event()
    original_atomic_write = workbench._atomic_write_json

    def observed_atomic_write(path: Path, value: dict[str, Any]) -> None:
        entered_replace.set()
        original_atomic_write(path, value)

    monkeypatch.setattr(workbench, "_atomic_write_json", observed_atomic_write)
    job_id = "WBEXP-" + "d" * 32
    writer = threading.Thread(
        target=manager._write_job,
        args=({"job_id": job_id, "status": "completed"},),
    )
    try:
        with manager._lock:
            writer.start()
            assert entered_replace.wait(timeout=0.1) is False
        assert entered_replace.wait(timeout=2)
        writer.join(timeout=2)
        assert writer.is_alive() is False
        persisted = json.loads(
            (
                tmp_path
                / "paper-export-workbench"
                / job_id
                / "job.json"
            ).read_text(encoding="utf-8")
        )
        assert persisted == {"job_id": job_id, "status": "completed"}
    finally:
        manager.shutdown()


@pytest.mark.parametrize(
    ("availability", "answer_text"),
    [
        ("present_part_aligned", ""),
        ("unknown", ANSWER_A1),
    ],
    ids=["aligned-without-text", "unknown-availability"],
)
def test_invalid_answer_contract_fails_closed_before_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    availability: str,
    answer_text: str,
) -> None:
    manager = PaperExportJobManager(tmp_path)
    futures = _capture_submitted_futures(manager, monkeypatch)
    rendered: dict[str, Any] = {}
    detail = deepcopy(_details()["A1"])
    detail["reference_answer"]["availability"] = availability
    detail["reference_answer"]["reference_answer_text"] = answer_text
    monkeypatch.setattr(
        workbench, "render_export_bundle", _fake_render_factory(rendered)
    )
    monkeypatch.setattr(workbench, "_locate_toolchain", lambda: None)
    try:
        started = manager.start(
            _request_payload(
                selections=[_selection(unit="atomic", target="A1")]
            ),
            theme_catalog_loader=lambda scope: _catalog(),
            detail_loader=lambda scope, node_id: deepcopy(detail),
            crop_loader=lambda scope, node_id, crop_id: _crop(node_id, crop_id),
        )
        futures[0].result(timeout=10)
        failed = _wait_for_terminal(manager, started["job_id"])
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "paper_export_answer_contract_invalid"
        assert failed["error"]["http_status"] == 409
        assert failed["error"]["details"]["atomic_part_id"] == "A1"
        assert rendered == {}
    finally:
        manager.shutdown()


def test_wave_detail_uses_model_candidate_analysis_and_paper_title_source_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(tmp_path)
    futures = _capture_submitted_futures(manager, monkeypatch)
    captured: dict[str, Any] = {}
    catalog = _catalog()
    catalog["scope"] = "wave1"
    detail = deepcopy(_details()["A1"])
    detail.pop("candidate_analysis")
    detail.pop("source_boundary_zh")
    detail["model_candidate_analysis"] = {
        "review_state_zh": "待复核候选分析",
        "solution_path_zh": ["WAVE-MODEL-CANDIDATE-SOLUTION"],
        "candidate_only": True,
        "correctness_verified": False,
    }

    def crop_loader(scope: str, node_id: str, crop_id: str) -> CandidateCropPayload:
        assert scope == "wave1"
        return _crop(node_id, crop_id)

    monkeypatch.setattr(
        workbench, "render_export_bundle", _fake_render_factory(captured)
    )
    monkeypatch.setattr(workbench, "_locate_toolchain", lambda: None)
    try:
        started = manager.start(
            _request_payload(
                selections=[
                    _selection(
                        unit="atomic",
                        scope="wave1",
                        target="A1",
                    )
                ]
            ),
            theme_catalog_loader=lambda scope: deepcopy(catalog),
            detail_loader=lambda scope, node_id: deepcopy(detail),
            crop_loader=crop_loader,
        )
        futures[0].result(timeout=10)
        completed = _wait_for_terminal(manager, started["job_id"])
        assert completed["status"] == "completed", completed

        student_plan = captured["bundle"]["student_plan"]
        teacher_plan = captured["bundle"]["teacher_plan"]
        teacher_notes = _all_atomic_rows(teacher_plan)[0]["teacher_notes"]
        assert teacher_notes["explanation_zh"] == "WAVE-MODEL-CANDIDATE-SOLUTION"
        assert teacher_notes["explanation_label"] == "ai_candidate"
        assert teacher_notes["source_label_zh"] == "题库来源卷：合成测试来源卷"
        assert "WAVE-MODEL-CANDIDATE-SOLUTION" not in json.dumps(
            student_plan, ensure_ascii=False
        )
    finally:
        manager.shutdown()


def test_single_atomic_export_does_not_load_unselected_unscanned_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(tmp_path)
    futures = _capture_submitted_futures(manager, monkeypatch)
    captured: dict[str, Any] = {}
    detail_calls: list[str] = []
    shared_bytes = b"SELECTED-ATOMIC-SHARED-MATERIAL"

    def detail_loader(scope: str, node_id: str) -> dict[str, Any]:
        assert scope == "master"
        detail_calls.append(node_id)
        if node_id == "A2":
            raise PaperExportWorkbenchError(
                "paper_export_visual_scan_unavailable",
                "未选中的 A2 尚未完成逐图扫描。",
                409,
            )
        return _selected_atomic_detail()

    def crop_loader(scope: str, node_id: str, crop_id: str) -> CandidateCropPayload:
        assert scope == "master"
        assert node_id == "A1"
        data = (
            shared_bytes
            if crop_id == "SELECTED-SHARED-MATERIAL"
            else b"SELECTED-ATOMIC-QUESTION"
        )
        return CandidateCropPayload(data=data, sha256=_sha256(data))

    monkeypatch.setattr(
        workbench, "render_export_bundle", _fake_render_factory(captured)
    )
    monkeypatch.setattr(workbench, "_locate_toolchain", lambda: None)
    try:
        started = manager.start(
            _request_payload(
                selections=[_selection(unit="atomic", target="A1")]
            ),
            theme_catalog_loader=lambda scope: _catalog_with_selected_shared_material(),
            detail_loader=detail_loader,
            crop_loader=crop_loader,
        )
        futures[0].result(timeout=10)
        completed = _wait_for_terminal(manager, started["job_id"])
        assert completed["status"] == "completed", completed
        assert detail_calls == ["A1"]
        assert completed["counts"]["atomic_part_count"] == 1

        theme = captured["bundle"]["blueprint"]["theme_bundles"][0]
        assert theme["final_atomic_ids"] == ["A1"]
        assert theme["shared_materials"][0]["used_by_atomic_ids"] == ["A1"]
        shared_material = captured["bundle"]["student_plan"]["visible"][
            "theme_sections"
        ][0]["shared_materials"][0]
        shared_ref = shared_material["content_blocks"][0]["asset_ref"]
        assert shared_ref == f"images/{_sha256(shared_bytes)}.png"
        assert shared_material["material_id"] == "SELECTED-SHARED-MATERIAL"
        assert shared_material["source_crop_id"] == "SELECTED-SHARED-MATERIAL"
        assert shared_material["source_sha256"] == _sha256(shared_bytes)
        assert shared_material["source_page"] == 1
        assert captured["bundle"]["blueprint"]["theme_bundles"][0][
            "integrity"
        ]["shared_material_order_basis"] == "source_page_order"
    finally:
        manager.shutdown()


def test_shared_membership_uses_explicit_noncontiguous_selected_details():
    blueprint = {
        "theme_bundles": [{
            "final_atomic_ids": ["A1", "A2", "A3"],
            "shared_materials": [
                {"material_id": "M1", "render_once_key": "key1", "used_by_atomic_count": 99},
                {"material_id": "M2", "render_once_key": "key2", "used_by_atomic_count": 99},
            ],
        }],
        "counts": {"shared_material_count": 2},
        "blockers": [],
        "status": "ready_for_content_resolution",
        "blueprint_digest": "before",
    }
    details = {
        node: {"evidence_descriptors": [{"crop_id": material, "evidence_role": "shared_material"}]}
        for node, material in (("A1", "M1"), ("A2", "M2"), ("A3", "M1"))
    }
    before = deepcopy(blueprint)
    result = workbench._bind_blueprint_shared_materials_to_selected_details(blueprint, details)
    materials = result["theme_bundles"][0]["shared_materials"]
    assert materials[0]["used_by_atomic_ids"] == ["A1", "A3"]
    assert materials[1]["used_by_atomic_ids"] == ["A2"]
    assert blueprint == before
    assert result["blueprint_digest"] != "before"


def test_same_material_id_in_two_papers_keeps_each_theme_bound_to_its_own_crop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(tmp_path)
    futures = _capture_submitted_futures(manager, monkeypatch)
    captured: dict[str, Any] = {}
    crop_calls: list[tuple[str, str]] = []
    shared_bytes = {
        "A1": b"FIRST-PAPER-SHARED-MATERIAL",
        "B1": b"SECOND-PAPER-SHARED-MATERIAL",
    }

    def crop_loader(scope: str, node_id: str, crop_id: str) -> CandidateCropPayload:
        assert scope == "master"
        crop_calls.append((node_id, crop_id))
        data = (
            shared_bytes[node_id]
            if crop_id == "SHARED-MATERIAL"
            else f"QUESTION-FIXTURE-{node_id}".encode("ascii")
        )
        return CandidateCropPayload(data=data, sha256=_sha256(data))

    monkeypatch.setattr(
        workbench, "render_export_bundle", _fake_render_factory(captured)
    )
    monkeypatch.setattr(workbench, "_locate_toolchain", lambda: None)
    try:
        started = manager.start(
            _request_payload(
                selections=[
                    _selection(theme_id="T1"),
                    _selection(theme_id="T2"),
                ]
            ),
            theme_catalog_loader=lambda scope: _catalog_with_reused_material_id(),
            detail_loader=lambda scope, node_id: _cross_paper_details()[node_id],
            crop_loader=crop_loader,
        )
        futures[0].result(timeout=10)
        completed = _wait_for_terminal(manager, started["job_id"])
        assert completed["status"] == "completed", completed

        theme_sections = captured["bundle"]["student_plan"]["visible"][
            "theme_sections"
        ]
        actual_refs = [
            section["shared_materials"][0]["content_blocks"][0]["asset_ref"]
            for section in theme_sections
        ]
        expected_refs = [
            f"images/{_sha256(shared_bytes['A1'])}.png",
            f"images/{_sha256(shared_bytes['B1'])}.png",
        ]
        assert actual_refs == expected_refs
        assert actual_refs[0] != actual_refs[1]
        assert ("A1", "SHARED-MATERIAL") in crop_calls
        assert ("B1", "SHARED-MATERIAL") in crop_calls
    finally:
        manager.shutdown()


def test_snapshot_drift_and_worker_exception_end_in_persisted_failed_state(
    tmp_path: Path,
) -> None:
    manager = PaperExportJobManager(tmp_path)
    try:
        drift = manager.start(
            _request_payload(),
            theme_catalog_loader=lambda scope: _catalog(snapshot=STALE_SNAPSHOT),
            detail_loader=lambda scope, node_id: _details()[node_id],
            crop_loader=lambda scope, node_id, crop_id: _crop(node_id, crop_id),
        )
        assert drift["status"] == "queued"
        failed = _wait_for_terminal(manager, drift["job_id"])
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "theme_snapshot_stale"
        assert failed["error"]["http_status"] == 409

        broken = manager.start(
            _request_payload(),
            theme_catalog_loader=lambda scope: (_ for _ in ()).throw(
                PaperExportWorkbenchError(
                    "fixture_catalog_failed", "测试目录读取失败。", 503
                )
            ),
            detail_loader=lambda scope, node_id: _details()[node_id],
            crop_loader=lambda scope, node_id, crop_id: _crop(node_id, crop_id),
        )
        assert broken["status"] == "queued"
        failed = _wait_for_terminal(manager, broken["job_id"])
        assert failed["status"] == "failed"
        assert failed["error"] == {
            "code": "fixture_catalog_failed",
            "message_zh": "测试目录读取失败。",
            "http_status": 503,
            "details": {},
        }
        persisted = json.loads(
            (
                tmp_path
                / "paper-export-workbench"
                / broken["job_id"]
                / "job.json"
            ).read_text(encoding="utf-8")
        )
        assert persisted["status"] == "failed"
    finally:
        manager.shutdown()


def test_corrupt_queued_job_record_is_recovered_as_diagnosable_failed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(tmp_path)
    release_worker = threading.Event()
    blocker = manager._executor.submit(lambda: release_worker.wait(timeout=10))
    futures = _capture_submitted_futures(manager, monkeypatch)
    try:
        started = manager.start(
            _request_payload(),
            theme_catalog_loader=lambda scope: _catalog(),
            detail_loader=lambda scope, node_id: _details()[node_id],
            crop_loader=lambda scope, node_id, crop_id: _crop(node_id, crop_id),
        )
        job_path = (
            tmp_path
            / "paper-export-workbench"
            / started["job_id"]
            / "job.json"
        )
        job_path.write_bytes(b"\xffnot-valid-utf8-or-json")
        release_worker.set()
        blocker.result(timeout=10)
        futures[0].result(timeout=10)

        failed = manager.get(started["job_id"])
        assert failed["status"] == "failed"
        assert failed["progress"] == {
            "stage": "failed",
            "percent": 100,
            "message_zh": "导出任务记录在后台启动时损坏；本次任务已停止，请重新提交。",
        }
        assert failed["error"] == {
            "code": "paper_export_job_corrupt",
            "message_zh": "导出任务记录在后台启动时损坏；本次任务已停止，请重新提交。",
            "http_status": 409,
            "details": {"recovery_action_zh": "请重新提交导出任务。"},
        }
        assert failed["counts"] == {}
        assert failed["artifacts"] == []
        assert failed["request_digest"] == started["request_digest"]

        persisted = json.loads(job_path.read_text(encoding="utf-8"))
        assert persisted["status"] == "failed"
        assert persisted["error"]["code"] == "paper_export_job_corrupt"
    finally:
        release_worker.set()
        manager.shutdown()


def test_four_artifact_paths_verify_mime_and_reject_hash_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(tmp_path)
    futures = _capture_submitted_futures(manager, monkeypatch)
    monkeypatch.setattr(
        workbench, "render_export_bundle", _fake_render_factory({})
    )
    monkeypatch.setattr(workbench, "_locate_toolchain", lambda: None)
    try:
        started = manager.start(
            _request_payload(),
            theme_catalog_loader=lambda scope: _catalog(),
            detail_loader=lambda scope, node_id: _details()[node_id],
            crop_loader=lambda scope, node_id, crop_id: _crop(node_id, crop_id),
        )
        futures[0].result(timeout=10)
        completed = _wait_for_terminal(manager, started["job_id"])
        assert completed["status"] == "completed", completed

        expected_types = {
            "student_docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "student_pdf": "application/pdf",
            "teacher_docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "teacher_pdf": "application/pdf",
        }
        paths: dict[str, Path] = {}
        for artifact_id, expected_type in expected_types.items():
            path, content_type = manager.artifact_path(started["job_id"], artifact_id)
            paths[artifact_id] = path
            assert path.name == ARTIFACT_FILENAMES[artifact_id]
            assert content_type == expected_type
            record = next(
                row
                for row in completed["artifacts"]
                if row["artifact_id"] == artifact_id
            )
            assert _sha256(path.read_bytes()) == record["sha256"]

        paths["teacher_pdf"].write_bytes(b"tampered-after-completion")
        with pytest.raises(PaperExportWorkbenchError) as captured:
            manager.artifact_path(started["job_id"], "teacher_pdf")
        assert captured.value.code == "paper_export_artifact_drift"
        assert captured.value.status == 409
    finally:
        manager.shutdown()


def _origin(server: Any) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def test_http_routes_return_202_status_and_download_all_four_artifacts_with_headers(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "http-artifacts"
    artifact_root.mkdir()
    artifact_paths: dict[str, Path] = {}
    artifact_types: dict[str, str] = {}
    for artifact_id, filename in ARTIFACT_FILENAMES.items():
        path = artifact_root / filename
        path.write_bytes(f"http-{artifact_id}".encode("ascii"))
        artifact_paths[artifact_id] = path
        artifact_types[artifact_id] = (
            "application/pdf"
            if artifact_id.endswith("_pdf")
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    with running_server(build_config(tmp_path / "gateway-state")) as server:
        server.service.paper_export_start = lambda principal, payload: {
            "job_id": JOB_ID,
            "status": "queued",
            "scope": "master",
        }
        server.service.paper_export_get = lambda principal, job_id: {
            "job_id": job_id,
            "status": "completed",
            "artifacts": [
                {
                    "artifact_id": artifact_id,
                    "download_path": (
                        f"/api/v1/prep/exports/{job_id}/artifacts/{artifact_id}"
                    ),
                }
                for artifact_id in ARTIFACT_FILENAMES
            ],
        }
        server.service.paper_export_artifact_bytes = (
            lambda principal, job_id, artifact_id: (
                artifact_paths[artifact_id].read_bytes(),
                artifact_types[artifact_id],
                artifact_paths[artifact_id].name,
            )
        )
        headers = _origin(server)
        status, body, response_headers = request(
            server,
            "POST",
            "/api/v1/prep/exports",
            token=TOKEN_A,
            payload=_request_payload(),
            headers=headers,
        )
        assert status == 202, body
        assert body["data"] == {
            "job_id": JOB_ID,
            "scope": "master",
            "status": "queued",
        }
        assert response_headers.get_content_type() == "application/json"

        status, body, _ = request(
            server,
            "GET",
            f"/api/v1/prep/exports/{JOB_ID}",
            token=TOKEN_A,
            headers=headers,
        )
        assert status == 200
        assert body["data"]["status"] == "completed"
        assert len(body["data"]["artifacts"]) == 4

        for artifact_id, path in artifact_paths.items():
            status, data, response_headers = request(
                server,
                "GET",
                f"/api/v1/prep/exports/{JOB_ID}/artifacts/{artifact_id}",
                token=TOKEN_A,
                headers=headers,
            )
            assert status == 200
            assert data == path.read_bytes()
            assert response_headers.get("Content-Type") == artifact_types[artifact_id]
            disposition = response_headers.get("Content-Disposition") or ""
            assert disposition.startswith("attachment;")
            assert "filename=" in disposition and "filename*=UTF-8''" in disposition
            expected_audience = "student" if artifact_id.startswith("student") else "teacher"
            expected_format = "docx" if artifact_id.endswith("docx") else "pdf"
            assert (
                f'filename="shchem-theme-practice-{expected_audience}.{expected_format}"'
                in disposition
            )
            assert response_headers.get("Cache-Control") == "no-store"


@pytest.mark.parametrize("scope", ["wave1", "master"])
def test_http_start_rejects_changed_catalog_even_when_client_keeps_old_snapshot(
    tmp_path: Path, scope: str
) -> None:
    current_catalog = _catalog(snapshot=STALE_SNAPSHOT)
    current_catalog["scope"] = scope
    with running_server(build_config(tmp_path / f"gateway-state-{scope}")) as server:
        server.service.workbench_registry = lambda principal: {
            "products": [
                {
                    "scope": scope,
                    "data_snapshot_id": SNAPSHOT,
                    "manifest_sha256": SNAPSHOT,
                }
            ]
        }
        server.service.theme_workbench_groups = (
            lambda principal, *, scope: deepcopy(current_catalog)
        )
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/prep/exports",
            token=TOKEN_A,
            payload=_request_payload(
                selections=[_selection(scope=scope, snapshot=SNAPSHOT)]
            ),
            headers=_origin(server),
        )
        assert status == 409
        assert body["error"]["code"] == "theme_snapshot_stale"
        assert list(
            (
                tmp_path
                / f"gateway-state-{scope}"
                / "paper-export-workbench"
            ).glob("WBEXP-*")
        ) == []


def test_http_start_rejects_stale_active_snapshot_before_queueing(tmp_path: Path) -> None:
    with running_server(build_config(tmp_path / "gateway-state")) as server:
        server.service._paper_export_scope_snapshot_id = (
            lambda principal, scope: SNAPSHOT
        )
        status, body, _ = request(
            server,
            "POST",
            "/api/v1/prep/exports",
            token=TOKEN_A,
            payload=_request_payload(
                selections=[_selection(snapshot=STALE_SNAPSHOT)]
            ),
            headers=_origin(server),
        )
        assert status == 409
        assert body["error"]["code"] == "theme_snapshot_stale"
        assert list(
            (tmp_path / "gateway-state" / "paper-export-workbench").glob("WBEXP-*")
        ) == []

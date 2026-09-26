from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1 import paper_export_workbench as workbench
from integrations.deeptutor_shchem_v1.candidate_review import CandidateCropPayload
from integrations.deeptutor_shchem_v1.paper_export_workbench import (
    PaperExportJobManager,
    PaperExportWorkbenchError,
)
from staging.coordination.deeptutor_gateway.tests.test_paper_export_openapi import (  # noqa: E501
    _contract,
    _validator,
)
from staging.coordination.deeptutor_gateway.tests.test_paper_export_workbench_api import (  # noqa: E501
    _all_atomic_rows,
    _atomic,
    _capture_submitted_futures,
    _catalog,
    _details,
    _fake_render_factory,
    _request_payload,
    _wait_for_terminal,
)


def _output_id(parent_id: str, source_node_id: str) -> str:
    digest = hashlib.sha256(f"{parent_id}\0{source_node_id}".encode()).hexdigest()
    return f"EXPALIAS-{digest[:32]}"


def _alias_catalog() -> dict[str, Any]:
    catalog = _catalog()
    theme = catalog["papers"][0]["theme_groups"][0]
    first = _atomic("WAVE-A1", "WAVE-P1", 1)
    second = _atomic("WAVE-A2", "WAVE-P1", 2, prior=["WAVE-A1"])
    first["atomic_sequence_in_printed"] = 1
    second["atomic_sequence_in_printed"] = 2
    parent = _atomic("MASTER-A", "MASTER-P1", 1)
    parent.update(
        {
            "label_summary": {"status": "blocked", "reason": "per_alias_unit"},
            "answer": {"availability": "per_alias_unit"},
            "dependency": {
                "kind": "per_alias_unit",
                "prior_atomic_part_ids": [],
                "explicit_prior_edge_count": 1,
                "status": "unitized_alias_no_merge",
            },
            "alias_units": [first, second],
        }
    )
    theme["atomic_chain"] = [parent]
    theme["counts"] = {"atomic": 1, "display_atomic_units": 2}
    theme["dependencies"] = {
        "independent": 0,
        "shared_material_only": 0,
        "one_prior_part": 0,
        "multiple_prior_parts": 0,
        "per_alias_unit": 1,
        "explicit_prior_edge_count": 1,
        "blocked": 0,
    }
    return catalog


def _settings() -> dict[str, dict[str, int]]:
    return {
        _output_id("MASTER-A", "WAVE-A1"): {
            "score": 3,
            "answer_space_lines": 4,
        },
        _output_id("MASTER-A", "WAVE-A2"): {
            "score": 7,
            "answer_space_lines": 6,
        },
    }


def _detail(node_id: str) -> dict[str, Any]:
    detail = deepcopy(_details()["A1"])
    detail["visible_summary_zh"] = f"{node_id} 的题面"
    detail["evidence_descriptors"] = [
        {"evidence_role": "question", "crop_id": f"CROP-{node_id}"}
    ]
    detail["reference_answer"]["reference_answer_text"] = f"ANSWER-{node_id}"
    return detail


def _crop(node_id: str, crop_id: str) -> CandidateCropPayload:
    assert crop_id == f"CROP-{node_id}"
    data = f"PNG-{node_id}".encode("ascii")
    return CandidateCropPayload(data=data, sha256=hashlib.sha256(data).hexdigest())


def test_atomic_settings_drive_alias_job_score_and_exact_answer_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PaperExportJobManager(tmp_path)
    futures = _capture_submitted_futures(manager, monkeypatch)
    captured: dict[str, Any] = {}
    payload = _request_payload()
    payload["atomic_settings"] = _settings()
    monkeypatch.setattr(
        workbench, "render_export_bundle", _fake_render_factory(captured)
    )
    monkeypatch.setattr(workbench, "_locate_toolchain", lambda: None)
    try:
        started = manager.start(
            payload,
            theme_catalog_loader=lambda scope: _alias_catalog(),
            detail_loader=lambda scope, node_id: _detail(node_id),
            crop_loader=lambda scope, node_id, crop_id: _crop(node_id, crop_id),
        )
        futures[0].result(timeout=10)
        completed = _wait_for_terminal(manager, started["job_id"])
        assert completed["status"] == "completed", completed
        assert completed["counts"]["total_score"] == 10.0

        expected_ids = list(_settings())
        assert (
            captured["bundle"]["blueprint"]["theme_bundles"][0]["final_atomic_ids"]
            == expected_ids
        )
        for plan_name in ("student_plan", "teacher_plan"):
            rows = _all_atomic_rows(captured["bundle"][plan_name])
            assert [row["score"] for row in rows] == [3.0, 7.0]
            assert [row["answer_space"] for row in rows] == [
                {"mode": "ruled_lines_exact", "lines": 4},
                {"mode": "ruled_lines_exact", "lines": 6},
            ]
    finally:
        manager.shutdown()


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_atomic_settings_require_exact_final_alias_ids(
    tmp_path: Path, mutation: str
) -> None:
    manager = PaperExportJobManager(tmp_path)
    payload = _request_payload()
    settings = _settings()
    if mutation == "missing":
        settings.pop(next(iter(settings)))
    else:
        settings["EXPALIAS-not-in-final-closure"] = {
            "score": 1,
            "answer_space_lines": 1,
        }
    payload["atomic_settings"] = settings
    try:
        with pytest.raises(PaperExportWorkbenchError) as caught:
            manager.start(
                payload,
                theme_catalog_loader=lambda scope: _alias_catalog(),
                detail_loader=lambda scope, node_id: _detail(node_id),
                crop_loader=lambda scope, node_id, crop_id: _crop(node_id, crop_id),
            )
        assert caught.value.code == "paper_export_atomic_settings_mismatch"
        assert caught.value.details[f"{mutation}_atomic_ids"]
    finally:
        manager.shutdown()


@pytest.mark.parametrize(
    "setting",
    [
        {"score": 0, "answer_space_lines": 3},
        {"score": 31, "answer_space_lines": 3},
        {"score": 3, "answer_space_lines": -1},
        {"score": 3, "answer_space_lines": 21},
        {"score": 3},
    ],
)
def test_invalid_atomic_setting_is_rejected_before_queue(
    tmp_path: Path, setting: dict[str, int]
) -> None:
    manager = PaperExportJobManager(tmp_path)
    payload = _request_payload()
    payload["atomic_settings"] = {"A1": setting}
    try:
        with pytest.raises(PaperExportWorkbenchError) as caught:
            manager.start(
                payload,
                theme_catalog_loader=lambda scope: _catalog(),
                detail_loader=lambda scope, node_id: _detail(node_id),
                crop_loader=lambda scope, node_id, crop_id: _crop(node_id, crop_id),
            )
        assert caught.value.code == "paper_export_request_invalid"
    finally:
        manager.shutdown()


def test_openapi_accepts_strict_atomic_settings_shape() -> None:
    request = _request_payload()
    request["atomic_settings"] = {
        "A1": {"score": 30, "answer_space_lines": 20},
        "A2": {"score": 1, "answer_space_lines": 0},
    }
    validator = _validator(_contract(), "PaperExportStartRequest")
    assert not list(validator.iter_errors(request))

    invalid = deepcopy(request)
    invalid["atomic_settings"]["A1"]["score"] = 31
    assert list(validator.iter_errors(invalid))


def test_legacy_uniform_settings_still_compact_answer_space(tmp_path: Path) -> None:
    resolver = workbench._WorkbenchContentResolver(
        scope="master",
        rows=[_atomic("A1", "P1", 1)],
        details={"A1": _detail("A1")},
        crop_loader=lambda node_id, crop_id: _crop(node_id, crop_id),
        asset_root=tmp_path / "assets",
        score_per_atomic=2.0,
        default_answer_lines=7,
    )
    content = resolver.resolve_atomic_part({"atomic_part_id": "A1"})
    assert content["score"] == 2.0
    assert content["answer_space_lines"] == 7
    assert "answer_space_explicit" not in content

from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1 import paper_export_workbench as workbench
from integrations.deeptutor_shchem_v1.candidate_review import CandidateCropPayload
from staging.coordination.deeptutor_gateway.tests.test_paper_export_atomic_settings import (
    _alias_catalog,
    _detail,
    _settings,
)
from staging.coordination.deeptutor_gateway.tests.test_paper_export_workbench_api import (
    ANSWER_A1,
    ANSWER_A2,
    _all_atomic_rows,
    _atomic,
    _capture_submitted_futures,
    _catalog,
    _details,
    _fake_render_factory,
    _request_payload,
    _selection,
)


def _png_payload(color: str = "white") -> CandidateCropPayload:
    stream = io.BytesIO()
    Image.new("RGB", (24, 12), color).save(stream, format="PNG")
    data = stream.getvalue()
    return CandidateCropPayload(data=data, sha256=hashlib.sha256(data).hexdigest())


@pytest.fixture(autouse=True)
def _no_render_or_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("The bundle builder must not render or locate an Office toolchain")

    monkeypatch.setattr(workbench, "render_export_bundle", forbidden)
    monkeypatch.setattr(workbench, "_locate_toolchain", forbidden)


def _build(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        "theme_catalog_loader": lambda scope: _catalog(),
        "detail_loader": lambda scope, node_id: _details()[node_id],
        "crop_loader": lambda scope, node_id, crop_id: _png_payload(),
        "asset_root": tmp_path / "assets",
        "bundle_id": "synthetic_core_bundle_v1",
    }
    payload = overrides.pop("payload", _request_payload())
    options.update(overrides)
    return workbench.build_core_export_bundle(payload, **options)


def test_builder_preserves_dependency_source_and_answer_boundaries(tmp_path: Path) -> None:
    payload = _request_payload(
        selections=[_selection(unit="dependency", target="A2")]
    )
    catalog = _catalog()
    details = _details()
    before = deepcopy((payload, catalog, details))
    calls: list[tuple[str, ...]] = []
    image = _png_payload()

    def load_catalog(scope: str) -> dict[str, Any]:
        calls.append(("catalog", scope))
        return catalog

    def load_detail(scope: str, node_id: str) -> dict[str, Any]:
        calls.append(("detail", scope, node_id))
        return details[node_id]

    def load_crop(scope: str, node_id: str, crop_id: str) -> CandidateCropPayload:
        calls.append(("crop", scope, node_id, crop_id))
        assert crop_id == f"CROP-{node_id}"
        return image

    bundle = _build(
        tmp_path,
        payload=payload,
        theme_catalog_loader=load_catalog,
        detail_loader=load_detail,
        crop_loader=load_crop,
    )
    assert (payload, catalog, details) == before
    assert bundle["bundle_id"] == "synthetic_core_bundle_v1"
    assert bundle["publication_allowed"] is False
    assert {
        "preset", "blueprint", "student_plan", "teacher_plan",
        "preflight_report", "render_request",
    } <= bundle.keys()
    theme = bundle["blueprint"]["theme_bundles"][0]
    assert theme["requested_atomic_ids"] == ["A2"]
    assert theme["auto_added_dependency_ids"] == ["A1"]
    assert theme["final_atomic_ids"] == ["A1", "A2"]
    assert bundle["blueprint"]["integrity"][
        "dependency_closure_recomputed_server_side"
    ] is True
    assert bundle["preset"]["per_paper"]["total_score"]["value"] == 4
    assert bundle["preset"]["student_version"]["show_item_scores"] is False
    student = json.dumps(bundle["student_plan"], ensure_ascii=False)
    teacher = json.dumps(bundle["teacher_plan"], ensure_ascii=False)
    assert ANSWER_A1 not in student and ANSWER_A2 not in student
    assert ANSWER_A1 in teacher and ANSWER_A2 in teacher
    assert "参考答案（非官方，未独立核验）" in teacher
    assert "本地非官方参考答案，按题对齐。" in teacher
    assert [call for call in calls if call[0] == "catalog"] == [("catalog", "master")]
    assert [call for call in calls if call[0] == "detail"] == [
        ("detail", "master", "A1"), ("detail", "master", "A2")
    ]
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert files == [tmp_path / "assets" / "images" / f"{image.sha256}.png"]
    assert files[0].read_bytes() == image.data


def test_alias_bundle_routes_exact_source_units_and_custom_scores(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    payload = _request_payload()
    payload["atomic_settings"] = _settings()
    payload["show_question_scores"] = True

    def load_detail(scope: str, node_id: str) -> dict[str, Any]:
        calls.append((scope, node_id))
        return _detail(node_id)

    bundle = _build(
        tmp_path,
        payload=payload,
        theme_catalog_loader=lambda scope: _alias_catalog(),
        detail_loader=load_detail,
    )
    assert calls == [("wave1", "WAVE-A1"), ("wave1", "WAVE-A2")]
    assert bundle["blueprint"]["theme_bundles"][0]["final_atomic_ids"] == list(_settings())
    assert bundle["preset"]["per_paper"]["total_score"]["value"] == 10
    assert bundle["preset"]["student_version"]["show_item_scores"] is True
    for name in ("student_plan", "teacher_plan"):
        rows = _all_atomic_rows(bundle[name])
        assert [row["score"] for row in rows] == [3.0, 7.0]
        assert [row["answer_space"] for row in rows] == [
            {"mode": "ruled_lines_exact", "lines": 4},
            {"mode": "ruled_lines_exact", "lines": 6},
        ]


def _answer_image_detail(image: CandidateCropPayload) -> dict[str, Any]:
    detail = _details()["A1"]
    detail["master_node_id"] = "A1"
    detail["reference_answer_images"] = [{
        "crop_id": "ANSWER-CROP-A1",
        "evidence_role": "answer",
        "access": "teacher_reference_answer_only",
        "display_mode": "inline_required",
        "sha256": image.sha256,
        "source_sha256": "c" * 64,
        "source_page": 1,
        "bytes": len(image.data),
        "width": 24,
        "height": 12,
        "presentation_revision_id": "synthetic-answer-r1",
        "caption_zh": "合成来源答案图",
    }]
    return detail


def test_answer_images_use_dedicated_route_and_stay_teacher_only(tmp_path: Path) -> None:
    answer_image = _png_payload("black")
    detail = _answer_image_detail(answer_image)
    before = deepcopy(detail)
    calls: list[tuple[str, str, str]] = []

    def answer_loader(scope: str, node_id: str, crop_id: str) -> CandidateCropPayload:
        calls.append((scope, node_id, crop_id))
        return answer_image

    bundle = _build(
        tmp_path,
        payload=_request_payload(selections=[_selection(unit="atomic", target="A1")]),
        detail_loader=lambda scope, node_id: detail,
        answer_crop_loader=answer_loader,
    )
    assert calls == [("master", "A1", "ANSWER-CROP-A1")]
    assert detail == before
    reference = f"images/{answer_image.sha256}.png"
    assert reference not in json.dumps(bundle["student_plan"])
    assert reference in json.dumps(bundle["teacher_plan"])
    assert (tmp_path / "assets" / reference).read_bytes() == answer_image.data


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("request", "paper_export_request_invalid"),
        ("snapshot", "theme_snapshot_stale"),
        ("integrity", "theme_snapshot_integrity_blocked"),
        ("question_crop", "paper_export_question_crop_missing"),
        ("answer", "paper_export_answer_contract_invalid"),
        ("settings", "paper_export_atomic_settings_mismatch"),
        ("answer_route", "paper_export_answer_image_loader_missing"),
    ],
)
def test_builder_retains_fail_closed_contracts(
    tmp_path: Path, mutation: str, code: str
) -> None:
    payload = _request_payload()
    catalog = _catalog()
    details = _details()
    if mutation == "request":
        payload["scope"] = "master"
    elif mutation == "snapshot":
        catalog["data_snapshot_id"] = "b" * 64
    elif mutation == "integrity":
        catalog["integrity"]["hash_verified_on_read"] = False
    elif mutation == "question_crop":
        details["A1"]["evidence_descriptors"] = []
    elif mutation == "answer":
        details["A1"]["reference_answer"]["availability"] = "absent"
    elif mutation == "settings":
        payload["atomic_settings"] = {"A1": {"score": 2, "answer_space_lines": 3}}
    elif mutation == "answer_route":
        details["A1"] = _answer_image_detail(_png_payload("black"))
    with pytest.raises(workbench.PaperExportWorkbenchError) as captured:
        _build(
            tmp_path, payload=payload,
            theme_catalog_loader=lambda scope: catalog,
            detail_loader=lambda scope, node_id: details[node_id],
        )
    assert captured.value.code == code
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("existing_asset", [False, True])
def test_builder_rejects_crop_hash_drift_without_overwriting(
    tmp_path: Path, existing_asset: bool
) -> None:
    image = _png_payload()
    target = tmp_path / "assets" / "images" / f"{image.sha256}.png"
    if existing_asset:
        target.parent.mkdir(parents=True)
        target.write_bytes(b"PREEXISTING-CORRUPT-ASSET")
        bad_image = image
    else:
        bad_image = CandidateCropPayload(data=image.data, sha256="f" * 64)
    with pytest.raises(workbench.PaperExportWorkbenchError) as captured:
        _build(tmp_path, crop_loader=lambda scope, node_id, crop_id: bad_image)
    assert captured.value.code == (
        "paper_export_crop_drift" if existing_asset else "paper_export_crop_invalid"
    )
    if existing_asset:
        assert target.read_bytes() == b"PREEXISTING-CORRUPT-ASSET"
    else:
        assert not list(tmp_path.rglob("*.png"))


def test_direct_builder_retains_dependency_expanded_score_limit(tmp_path: Path) -> None:
    catalog = _catalog()
    group = catalog["papers"][0]["theme_groups"][0]
    group["atomic_chain"] = [
        _atomic(f"A{i}", f"P{i}", i, prior=[f"A{i - 1}"] if i > 1 else [])
        for i in range(1, 27)
    ]
    payload = _request_payload(
        selections=[_selection(unit="dependency", target="A26")]
    )
    payload["score_per_atomic"] = 20
    with pytest.raises(workbench.PaperExportWorkbenchError) as captured:
        _build(tmp_path, payload=payload, theme_catalog_loader=lambda scope: catalog)
    assert captured.value.code == "paper_export_total_score_limit_exceeded"
    assert captured.value.details["observed_total_score"] == 520
    assert not list(tmp_path.rglob("*"))


def test_legacy_job_reuses_builder_with_unchanged_bundle_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_builder = workbench.build_core_export_bundle
    captured: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    stages: list[tuple[str, int]] = []
    manager = workbench.PaperExportJobManager(tmp_path / "synthetic-state")
    futures = _capture_submitted_futures(manager, monkeypatch)
    original_progress = manager._progress

    def spy_builder(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(deepcopy(payload))
        bundle = real_builder(payload, **kwargs)
        captured["built"] = deepcopy(bundle)
        return bundle

    def progress(job: dict[str, Any], stage: str, percent: int, message: str) -> None:
        stages.append((stage, percent))
        original_progress(job, stage, percent, message)

    monkeypatch.setattr(workbench, "build_core_export_bundle", spy_builder)
    monkeypatch.setattr(workbench, "render_export_bundle", _fake_render_factory(captured))
    monkeypatch.setattr(workbench, "_locate_toolchain", lambda: None)
    monkeypatch.setattr(manager, "_progress", progress)
    try:
        started = manager.start(
            _request_payload(),
            theme_catalog_loader=lambda scope: _catalog(),
            detail_loader=lambda scope, node_id: _details()[node_id],
            crop_loader=lambda scope, node_id, crop_id: _png_payload(),
        )
        futures[0].result(timeout=10)
        completed = manager.get(started["job_id"])
        assert completed["status"] == "completed", completed
        assert len(calls) == 1
        assert type(calls[0]["score_per_atomic"]) is int
        assert "scope" not in calls[0] and "data_snapshot_id" not in calls[0]
        assert captured["built"] == captured["bundle"]
        assert captured["bundle"]["bundle_id"] == f"paper_export_{started['job_id']}"
        assert stages == [
            ("loading_theme_catalog", 8), ("loading_question_assets", 22),
            ("rendering", 42),
        ]
        assert completed["counts"]["total_score"] == 4.0
        assert len(completed["artifacts"]) == 4
        assert completed["error"] is None
    finally:
        manager.shutdown()

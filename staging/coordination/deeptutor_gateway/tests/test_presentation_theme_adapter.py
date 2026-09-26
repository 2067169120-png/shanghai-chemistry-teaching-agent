from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.presentation_jobs import PresentationJobManager
from integrations.deeptutor_shchem_v1.presentation_theme_adapter import (
    THEME_REQUEST_SCHEMA_VERSION,
    build_presentation_input,
    materialize_theme_assets,
    validate_theme_request,
)
from integrations.deeptutor_shchem_v1.presentation_workbench import (
    PresentationWorkbenchError,
    compose_deck_json,
    validate_deck_json,
    validate_presentation_input,
)

SNAPSHOT = "a" * 64
OWNER = "teacher@example.invalid"
SESSION = "b" * 64
ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44"
    "ae426082"
)
SECOND_PIXEL_PNG = ONE_PIXEL_PNG[:50] + bytes([ONE_PIXEL_PNG[50] ^ 1]) + ONE_PIXEL_PNG[51:]


class _FakeToolchain:
    def validate(self) -> None:
        return None


def _request() -> dict:
    return {
        "schema_version": THEME_REQUEST_SCHEMA_VERSION,
        "scope": "wave1",
        "data_snapshot_id": SNAPSHOT,
        "theme_id": "THEME-REAL-01",
        "lesson_title": "电化学证据链",
        "grade": "高三",
        "duration_minutes": 45,
        "textbook_selection": {
            "book_title": "上海高中化学教材",
            "volume": "选择性必修1",
            "chapter": "第4章 氧化还原反应",
            "section": "4.4 金属的电化学腐蚀与防护",
            "publisher": "上海科技教育出版社",
            "evidence_note": "教师从本地教材目录明确选择本节作为主要落点。",
        },
        "lesson_goals": {
            "learning_objectives": ["能从共同材料中提取电极反应证据。"],
            "key_points": ["电极反应与装置现象"],
            "difficult_points": ["由现象组织完整因果链"],
            "prerequisites": ["氧化还原反应"],
            "lesson_emphasis": "先读共同材料，再按作答单元推进。",
        },
        "diagnosis": {
            "label": "教师填写的匿名班级薄弱点",
            "summary": "班级容易跳过共同材料直接下结论。",
            "common_error": "只写电极名称，没有引用可见现象。",
            "cause_hypothesis": "证据与结论之间缺少电极反应这一中间层。",
            "confidence": 0.65,
            "counterevidence": ["尚未绑定完整班级样本，结论只作备课候选。"],
        },
        "classroom_plan": {
            "teacher_questions": ["共同材料中哪条现象是直接证据？"],
            "anticipated_responses": ["先指出电极附近的可见变化。"],
            "homework": ["用证据—反应—结论三步重写本题答案。"],
        },
    }


def _catalog() -> dict:
    return {
        "data_snapshot_id": SNAPSHOT,
        "scope": "wave1",
        "papers": [
            {
                "paper": {
                    "id": "PAPER-REAL-01",
                    "title": "2026上海某区化学模拟试卷",
                    "source_metadata": {
                        "year": "2026",
                        "region": "上海某区",
                        "paper_type": "二模",
                        "source_tier": "page_verified_nonofficial_reference",
                    },
                },
                "theme_groups": [
                    {
                        "paper": {"id": "PAPER-REAL-01"},
                        "theme": {
                            "id": "THEME-REAL-01",
                            "title": "电化学与金属防护",
                            "sequence": 2,
                            "page_span": {"start_page": 3, "end_page": 4},
                        },
                        "shared_context": {
                            "context_summary_zh": "以金属防护装置为共同情境。",
                            "materials": [
                                {
                                    "material_id": "SHARED-CELL-01",
                                    "page": 3,
                                    "candidate_description_zh": "电化学装置共同材料",
                                }
                            ],
                        },
                        "atomic_chain": [
                            {
                                "atomic_part_id": "ATOM-01",
                                "printed_question_id": "PRINT-01",
                                "printed_question_number": "1",
                                "printed_sequence": 1,
                                "atomic_sequence_in_printed": 1,
                                "item_type": "short_fill",
                                "response_requirement_zh": "填写电极名称。",
                                "visible_summary_zh": "依据装置现象填写电极名称。",
                            },
                            {
                                "atomic_part_id": "ATOM-02",
                                "printed_question_id": "PRINT-01",
                                "printed_question_number": "1",
                                "printed_sequence": 1,
                                "atomic_sequence_in_printed": 2,
                                "item_type": "reasoned_explanation",
                                "response_requirement_zh": "解释金属受到保护的原因。",
                                "visible_summary_zh": "结合电极反应解释保护原因。",
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _details() -> dict:
    shared_hash = hashlib.sha256(ONE_PIXEL_PNG).hexdigest()
    return {
        "ATOM-01": {
            "response_requirement_zh": "填写发生氧化反应的电极名称。",
            "dependency": {
                "prior_atomic_part_ids": [],
                "shared_material_crop_ids": ["SHARED-CELL-01"],
            },
            "reference_answer": {
                "availability": "present_part_aligned",
                "reference_answer_text": "阳极",
                "source_authority": "nonofficial_reference",
                "quality_note": None,
            },
            "evidence_descriptors": [
                {
                    "crop_id": "SHARED-CELL-01",
                    "evidence_role": "shared_material",
                    "source_page": 3,
                    "width": 1,
                    "height": 1,
                    "sha256": shared_hash,
                }
            ],
        },
        "ATOM-02": {
            "response_requirement_zh": "从电子转移角度解释保护原因。",
            "dependency": {
                "prior_atomic_part_ids": ["ATOM-01"],
                "shared_material_crop_ids": ["SHARED-CELL-01"],
            },
            "reference_answer": {
                "availability": "absent",
                "reference_answer_text": None,
                "source_authority": "none",
                "quality_note": None,
            },
            "evidence_descriptors": [],
        },
    }


def _details_with_assets() -> dict:
    details = _details()
    shared_hash = hashlib.sha256(ONE_PIXEL_PNG).hexdigest()
    question_hash = hashlib.sha256(SECOND_PIXEL_PNG).hexdigest()
    shared = {
        "crop_id": "SHARED-CELL-01",
        "evidence_role": "question",
        "source_page": 3,
        "width": 1,
        "height": 1,
        "sha256": shared_hash,
    }
    details["ATOM-01"]["evidence_descriptors"] = [shared]
    details["ATOM-02"]["evidence_descriptors"] = [
        {**shared, "evidence_role": "shared_material"},
        {
            "crop_id": "QUESTION-PRINT-01",
            "evidence_role": "question",
            "source_page": 4,
            "width": 1,
            "height": 1,
            "sha256": question_hash,
        },
    ]
    return details


def test_real_theme_projection_passes_generic_input_and_deck_contracts(tmp_path: Path) -> None:
    shared_hash = hashlib.sha256(ONE_PIXEL_PNG).hexdigest()
    shared_path = tmp_path / f"{shared_hash}.png"
    shared_path.write_bytes(ONE_PIXEL_PNG)
    shared_binding = {
        "asset_id": f"PPTASSET-{shared_hash[:32]}",
        "role": "shared_material",
        "path": shared_path.name,
        "sha256": shared_hash,
        "source_page": 3,
        "source_bbox": [0, 0, 1, 1],
        "pixel_dimensions": [1, 1],
        "alt_text": "真实来源共同材料裁片",
        "source_ref": "PAPER-REAL-01 / SHARED-CELL-01",
        "rights_boundary": "本地个人备课候选",
        "publication_allowed": False,
    }
    value = build_presentation_input(
        _request(),
        theme_catalog=_catalog(),
        details_by_atomic=_details(),
        active_snapshot_id=SNAPSHOT,
        asset_bindings={"SHARED-CELL-01": shared_binding},
    )
    assert value["theme"]["theme_id"] == "THEME-REAL-01"
    assert value["theme"]["paper"]["year"] == 2026
    assert value["theme"]["human_reviewed"] is False
    assert value["diagnosis"]["synthetic"] is False
    assert value["diagnosis"]["evidence_status"] == "provisional_anonymized"
    assert value["theme"]["printed_questions"][0]["atomic_parts"][0][
        "answer_text"
    ] == "阳极"
    assert value["theme"]["printed_questions"][0]["atomic_parts"][1][
        "answer_status"
    ] == "absent"
    assert value["theme"]["printed_questions"][0]["atomic_parts"][1][
        "dependencies"
    ] == ["ATOM-01", "SHARED-CELL-01"]

    normalized = validate_presentation_input(value, asset_root=tmp_path)
    deck = compose_deck_json(normalized, asset_root=tmp_path)
    checked = validate_deck_json(deck)
    assert checked["theme_binding"]["theme_id"] == "THEME-REAL-01"
    visible = str(checked)
    assert "A-PAPER-ALPHA" not in visible
    assert "FX-T2" not in visible


def test_theme_detail_closure_and_snapshot_are_fail_closed() -> None:
    missing = _details()
    missing.pop("ATOM-02")
    with pytest.raises(PresentationWorkbenchError) as exc:
        build_presentation_input(
            _request(),
            theme_catalog=_catalog(),
            details_by_atomic=missing,
            active_snapshot_id=SNAPSHOT,
        )
    assert exc.value.code == "presentation_theme_detail_closure_invalid"

    request = _request()
    request["data_snapshot_id"] = "stale"
    with pytest.raises(PresentationWorkbenchError) as exc:
        build_presentation_input(
            request,
            theme_catalog=_catalog(),
            details_by_atomic=_details(),
            active_snapshot_id=SNAPSHOT,
        )
    assert exc.value.code == "presentation_theme_snapshot_invalid"

    request = _request()
    request["data_snapshot_id"] = "b" * 64
    with pytest.raises(PresentationWorkbenchError) as exc:
        build_presentation_input(
            request,
            theme_catalog=_catalog(),
            details_by_atomic=_details(),
            active_snapshot_id=SNAPSHOT,
        )
    assert exc.value.code == "presentation_theme_snapshot_stale"

    catalog = _catalog()
    catalog["data_snapshot_id"] = "c" * 64
    with pytest.raises(PresentationWorkbenchError) as exc:
        build_presentation_input(
            _request(),
            theme_catalog=catalog,
            details_by_atomic=_details(),
            active_snapshot_id=SNAPSHOT,
        )
    assert exc.value.code == "presentation_theme_catalog_snapshot_mismatch"


def test_teacher_request_cannot_submit_paths_hashes_authority_or_identifiers() -> None:
    normalized = validate_theme_request(_request())
    assert normalized["theme_id"] == "THEME-REAL-01"
    assert normalized["schema_version"] == THEME_REQUEST_SCHEMA_VERSION
    assert validate_theme_request(normalized) == normalized
    for field, value in (
        ("output_path", "C:/forbidden"),
        ("assets", []),
        ("answer_authority", "official"),
        ("student_name", "张某"),
    ):
        request = _request()
        request[field] = value
        with pytest.raises(PresentationWorkbenchError) as exc:
            validate_theme_request(request)
        assert exc.value.code == "presentation_theme_request_invalid"


def test_asset_bindings_are_server_materialized_and_referenced() -> None:
    details = _details()
    details["ATOM-01"]["evidence_descriptors"] = [
        {
            "crop_id": "SHARED-CELL-01",
            "evidence_role": "shared_material",
            "source_page": 3,
            "width": 20,
            "height": 10,
            "sha256": "b" * 64,
        }
    ]
    binding = {
        "asset_id": "PPTASSET-" + "b" * 32,
        "role": "shared_material",
        "path": f"{'b' * 64}.png",
        "sha256": "b" * 64,
        "source_page": 3,
        "source_bbox": [0, 0, 20, 10],
        "pixel_dimensions": [20, 10],
        "alt_text": "真实来源共同材料裁片",
        "source_ref": "PAPER-REAL-01 / SHARED-CELL-01",
        "rights_boundary": "本地个人备课候选",
        "publication_allowed": False,
    }
    value = build_presentation_input(
        _request(),
        theme_catalog=_catalog(),
        details_by_atomic=details,
        active_snapshot_id=SNAPSHOT,
        asset_bindings={"SHARED-CELL-01": binding},
    )
    assert value["theme"]["assets"] == [binding]
    assert value["theme"]["shared_materials"][0]["asset_ids"] == [binding["asset_id"]]


def test_build_requires_exact_nonconflicting_server_asset_binding_closure() -> None:
    details = _details_with_assets()
    with pytest.raises(PresentationWorkbenchError) as exc:
        build_presentation_input(
            _request(),
            theme_catalog=_catalog(),
            details_by_atomic=details,
            active_snapshot_id=SNAPSHOT,
        )
    assert exc.value.code == "presentation_theme_asset_binding_incomplete"
    assert set(exc.value.details["missing_crop_ids"]) == {
        "SHARED-CELL-01",
        "QUESTION-PRINT-01",
    }

    digest = "d" * 64
    duplicate_details = _details()
    duplicate_details["ATOM-01"]["evidence_descriptors"] = [
        {
            "crop_id": "CROP-A",
            "evidence_role": "question",
            "source_page": 3,
            "width": 20,
            "height": 10,
            "sha256": digest,
        }
    ]
    duplicate_details["ATOM-02"]["evidence_descriptors"] = [
        {
            "crop_id": "CROP-B",
            "evidence_role": "shared_material",
            "source_page": 3,
            "width": 20,
            "height": 10,
            "sha256": digest,
        }
    ]
    duplicate_details["ATOM-01"]["dependency"]["shared_material_crop_ids"] = []
    duplicate_details["ATOM-02"]["dependency"]["shared_material_crop_ids"] = [
        "CROP-B"
    ]

    def binding(crop_id: str, role: str) -> dict:
        return {
            "asset_id": f"PPTASSET-{digest[:32]}",
            "role": role,
            "path": f"{digest}.png",
            "sha256": digest,
            "source_page": 3,
            "source_bbox": [0, 0, 20, 10],
            "pixel_dimensions": [20, 10],
            "alt_text": "题面裁片",
            "source_ref": f"THEME-REAL-01 / {crop_id}",
            "rights_boundary": "本地个人备课候选",
            "publication_allowed": False,
        }

    with pytest.raises(PresentationWorkbenchError) as exc:
        build_presentation_input(
            _request(),
            theme_catalog=_catalog(),
            details_by_atomic=duplicate_details,
            active_snapshot_id=SNAPSHOT,
            asset_bindings={
                "CROP-A": binding("CROP-A", "question_crop"),
                "CROP-B": binding("CROP-B", "shared_material"),
            },
        )
    assert exc.value.code == "presentation_theme_asset_binding_ambiguous"


def test_server_loader_materializes_real_theme_crops_into_private_project_assets(
    tmp_path: Path,
) -> None:
    manager = PresentationJobManager(tmp_path / "state", _FakeToolchain())
    try:
        project = manager.create_project(OWNER, SESSION, {"title_zh": "真实主题题图"})
        details = _details_with_assets()
        payloads = {
            "SHARED-CELL-01": ONE_PIXEL_PNG,
            "QUESTION-PRINT-01": SECOND_PIXEL_PNG,
        }
        loader_calls: list[tuple[str, str]] = []

        def crop_loader(atomic_id: str, descriptor: dict) -> dict:
            crop_id = descriptor["crop_id"]
            loader_calls.append((atomic_id, crop_id))
            data = payloads[crop_id]
            return {
                "data": data,
                "content_type": "image/png",
                "sha256": hashlib.sha256(data).hexdigest(),
            }

        def asset_store(filename: str, data: bytes) -> dict:
            return manager.store_asset(
                OWNER,
                SESSION,
                project["project_id"],
                filename=filename,
                data=data,
            )

        bindings = materialize_theme_assets(
            theme_id="THEME-REAL-01",
            atomic_chain=_catalog()["papers"][0]["theme_groups"][0]["atomic_chain"],
            details_by_atomic=details,
            crop_loader=crop_loader,
            asset_store=asset_store,
        )
        assert loader_calls.count(("ATOM-01", "SHARED-CELL-01")) == 1
        assert loader_calls.count(("ATOM-02", "SHARED-CELL-01")) == 1
        assert bindings["SHARED-CELL-01"]["role"] == "shared_material"
        assert bindings["QUESTION-PRINT-01"]["role"] == "question_crop"
        for crop_id, binding in bindings.items():
            expected = hashlib.sha256(payloads[crop_id]).hexdigest()
            assert binding["path"] == f"{expected}.png"
            assert binding["sha256"] == expected
            assert binding["pixel_dimensions"] == [1, 1]
            assert binding["source_bbox"] == [0, 0, 1, 1]
            assert not Path(binding["path"]).is_absolute()
            assert "/" not in binding["path"] and "\\" not in binding["path"]

        value = build_presentation_input(
            _request(),
            theme_catalog=_catalog(),
            details_by_atomic=details,
            active_snapshot_id=SNAPSHOT,
            asset_bindings=bindings,
        )
        outline = manager.create_outline(
            OWNER, SESSION, project["project_id"], value
        )
        assert outline["presentation_input"]["theme"]["shared_materials"][0][
            "asset_ids"
        ] == [bindings["SHARED-CELL-01"]["asset_id"]]
        assert outline["presentation_input"]["theme"]["printed_questions"][0][
            "asset_ids"
        ] == [
            bindings["SHARED-CELL-01"]["asset_id"],
            bindings["QUESTION-PRINT-01"]["asset_id"],
        ]
        stored = list((tmp_path / "state").glob("tenants/*/projects/PPTPRJ-*/assets/*"))
        assert len(stored) == 2
        assert {path.name for path in stored} == {
            binding["path"] for binding in bindings.values()
        }
    finally:
        manager.shutdown()


def test_declared_shared_crop_must_be_explicit_shared_evidence_before_io() -> None:
    chain = _catalog()["papers"][0]["theme_groups"][0]["atomic_chain"]
    details = _details()
    details["ATOM-01"]["evidence_descriptors"][0]["evidence_role"] = "question"

    with pytest.raises(PresentationWorkbenchError) as exc:
        materialize_theme_assets(
            theme_id="THEME-REAL-01",
            atomic_chain=chain,
            details_by_atomic=details,
            crop_loader=lambda _atomic_id, _descriptor: pytest.fail(
                "shared closure must fail before loading crops"
            ),
            asset_store=lambda _filename, _data: pytest.fail(
                "shared closure must fail before storing assets"
            ),
        )
    assert exc.value.code == "presentation_theme_shared_evidence_missing"
    assert exc.value.details == {
        "atomic_part_id": "ATOM-01",
        "missing_crop_ids": ["SHARED-CELL-01"],
    }


def test_atomic_without_question_or_shared_reference_fails_before_io() -> None:
    chain = _catalog()["papers"][0]["theme_groups"][0]["atomic_chain"]
    details = _details()
    details["ATOM-02"]["dependency"]["shared_material_crop_ids"] = []
    details["ATOM-02"]["evidence_descriptors"] = []

    with pytest.raises(PresentationWorkbenchError) as exc:
        materialize_theme_assets(
            theme_id="THEME-REAL-01",
            atomic_chain=chain,
            details_by_atomic=details,
            crop_loader=lambda _atomic_id, _descriptor: pytest.fail(
                "atomic closure must fail before loading crops"
            ),
            asset_store=lambda _filename, _data: pytest.fail(
                "atomic closure must fail before storing assets"
            ),
        )
    assert exc.value.code == "presentation_theme_atomic_evidence_missing"
    assert exc.value.details == {"atomic_part_id": "ATOM-02"}


def test_materializer_rejects_loader_paths_hash_drift_and_store_dimension_drift() -> None:
    chain = _catalog()["papers"][0]["theme_groups"][0]["atomic_chain"]
    details = _details_with_assets()

    empty_details = _details()
    for detail in empty_details.values():
        detail["evidence_descriptors"] = []
    with pytest.raises(PresentationWorkbenchError) as exc:
        materialize_theme_assets(
            theme_id="THEME-REAL-01",
            atomic_chain=chain,
            details_by_atomic=empty_details,
            crop_loader=lambda _atomic_id, _descriptor: pytest.fail(
                "empty evidence must fail before loading crops"
            ),
            asset_store=lambda _filename, _data: pytest.fail(
                "empty evidence must fail before storing assets"
            ),
        )
    assert exc.value.code == "presentation_theme_shared_evidence_missing"

    def loader_with_path(_: str, descriptor: dict) -> dict:
        data = (
            ONE_PIXEL_PNG
            if descriptor["crop_id"] == "SHARED-CELL-01"
            else SECOND_PIXEL_PNG
        )
        return {
            "data": data,
            "content_type": "image/png",
            "sha256": hashlib.sha256(data).hexdigest(),
            "path": "C:/browser-must-not-submit-this.png",
        }

    with pytest.raises(PresentationWorkbenchError) as exc:
        materialize_theme_assets(
            theme_id="THEME-REAL-01",
            atomic_chain=chain,
            details_by_atomic=details,
            crop_loader=loader_with_path,
            asset_store=lambda _filename, _data: {},
        )
    assert exc.value.code == "presentation_theme_asset_loader_contract_invalid"

    def drift_loader(_: str, descriptor: dict) -> dict:
        return {
            "data": ONE_PIXEL_PNG,
            "content_type": "image/png",
            "sha256": descriptor["sha256"],
        }

    with pytest.raises(PresentationWorkbenchError) as exc:
        materialize_theme_assets(
            theme_id="THEME-REAL-01",
            atomic_chain=chain,
            details_by_atomic=details,
            crop_loader=drift_loader,
            asset_store=lambda _filename, _data: {},
        )
    assert exc.value.code == "presentation_theme_evidence_drift"

    payloads = {
        "SHARED-CELL-01": ONE_PIXEL_PNG,
        "QUESTION-PRINT-01": SECOND_PIXEL_PNG,
    }

    def valid_loader(_: str, descriptor: dict) -> dict:
        data = payloads[descriptor["crop_id"]]
        return {
            "data": data,
            "content_type": "image/png",
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def wrong_dimensions_store(filename: str, data: bytes) -> dict:
        digest = hashlib.sha256(data).hexdigest()
        return {
            "asset_id": f"PPTASSET-{digest[:32]}",
            "path": f"{digest}{Path(filename).suffix}",
            "sha256": digest,
            "size_bytes": len(data),
            "content_type": "image/png",
            "pixel_dimensions": [2, 1],
            "content_addressed": True,
            "immutable": True,
        }

    with pytest.raises(PresentationWorkbenchError) as exc:
        materialize_theme_assets(
            theme_id="THEME-REAL-01",
            atomic_chain=chain,
            details_by_atomic=details,
            crop_loader=valid_loader,
            asset_store=wrong_dimensions_store,
        )
    assert exc.value.code == "presentation_theme_asset_store_contract_invalid"

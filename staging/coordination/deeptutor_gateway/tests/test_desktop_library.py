from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1.candidate_review import CandidateCropPayload
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_library import (
    LibraryImage,
    image_descriptors,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths

RAW = b"fixture png bytes, not a real chemical image"
DIGEST = hashlib.sha256(RAW).hexdigest()


def test_reader_page_numbers_and_nonofficial_source_type_are_readable():
    item = {
        "source_metadata": {
            "year": 2026,
            "paper_type": "second_mock_nonofficial_attribution",
        },
        "theme": {
            "page_span": {"page_numbers": [1, 3], "start_page": 1, "end_page": 3}
        },
    }
    assert DesktopWorkbenchFacade._page_label(item) == "第 1、3 页"
    assert DesktopWorkbenchFacade._source_label(item) == "2026 · 二模（非官方归类）"
    item["theme"]["page_span"] = {"start_page": 1, "end_page": 3}
    assert DesktopWorkbenchFacade._page_label(item) == "页码待核验"


def atom(key="A1", number="7", sequence=1):
    return {
        "atomic_part_id": key,
        "printed_question_id": "PQ7",
        "printed_question_number": number,
        "printed_sequence": 7,
        "atomic_sequence_in_printed": sequence,
        "item_type": "fill_blank",
        "label_summary": {
            "status": "complete",
            "primary_K": "K01",
            "supporting_K": [],
            "A": [],
            "C": [],
            "R": [],
            "RP": [],
            "cognitive_prelabel": "D2",
        },
        "answer": {"availability": "absent", "source_authority": "none"},
        "visible_summary_zh": "合成测试题意，不是真实化学题。",
        "response_requirement_zh": "合成测试作答要求。",
        "dependency": {"kind": "independent", "prior_atomic_part_ids": []},
        "alias_units": [],
    }


def catalog(scope="master"):
    paper = {
        "id": "P1",
        "title": "合成测试来源卷",
        "source_metadata": {
            "year": "2025",
            "region": "上海",
            "paper_type": "合成测试",
            "source_tier": "school_exam",
            "attribution_status": "candidate",
        },
    }
    second = atom("A2", sequence=2)
    second["dependency"] = {"kind": "one_prior_part", "prior_atomic_part_ids": ["A1"]}
    return {
        "scope": scope,
        "counts": {"papers": 1, "theme_groups": 1, "atomic_parts": 2},
        "papers": [
            {
                "paper": paper,
                "theme_groups": [
                    {
                        "paper": paper,
                        "theme": {
                            "id": "T1",
                            "title": "合成主题",
                            "sequence": 1,
                            "page_span": {"pages": [1, 2]},
                        },
                        "shared_context": {
                            "context_summary_zh": "合成共享材料摘要",
                            "materials": [],
                        },
                        "dependencies": {},
                        "counts": {"atomic_total": 2},
                        "atomic_chain": [atom(), second],
                    }
                ],
            }
        ],
        "unassigned_atomic_parts": [],
    }


def scan(node):
    return {
        "node_id": node,
        "visible_summary_zh": "已读取的合成题意",
        "response_requirement_zh": "已读取的合成要求",
        "scan_classification": {"item_type": "fill_blank"},
        "cognitive_difficulty": {"cognitive_prelabel": "D2"},
        "candidate_analysis": {"solution_path_zh": ["模型候选步骤，绝不是来源答案。"]},
        "reference_answer": {
            "availability": "present_part_aligned",
            "source_authority": "nonofficial_reference",
            "reference_answer_text": "  来源答案原文\n第二行  ",
            "independently_verified": False,
        },
        "evidence_descriptors": [
            {
                "crop_id": node + "-Q",
                "evidence_role": "question",
                "sha256": DIGEST,
                "source_page": 1,
                "width": 500,
                "height": 120,
            },
            {
                "crop_id": "SHARED",
                "evidence_role": "shared_material",
                "sha256": DIGEST,
                "source_page": 1,
            },
            {
                "crop_id": "ANSWER",
                "evidence_role": "answer",
                "sha256": DIGEST,
                "source_page": 3,
            },
        ],
    }


@pytest.mark.parametrize(
    "role,label", [("question", "题面"), ("shared_material", "共同材料")]
)
def test_revised_crop_caption_requires_different_valid_archived_sha(role, label):
    descriptor = {
        "crop_id": "ORIGINAL-CROP-HANDLE",
        "evidence_role": role,
        "sha256": DIGEST,
        "source_page": 5,
        "width": 1080,
        "height": 60,
        "presentation_revision_id": "0.1.55-r1",
        "archived_crop_sha256": "0" * 64,
    }
    original = deepcopy(descriptor)
    images = image_descriptors(
        "master", "A1", {"evidence_descriptors": [descriptor]}, view_id="VIEW-1"
    )
    assert len(images) == 1
    assert images[0].caption_zh == f"{label} · 第 5 页 · 裁图已修订"
    assert images[0].crop_id == "ORIGINAL-CROP-HANDLE"
    assert images[0].sha256 == DIGEST
    assert images[0].view_id == "VIEW-1"
    assert (images[0].width, images[0].height) == (1080, 60)
    assert descriptor == original


@pytest.mark.parametrize(
    "revision_fields",
    [
        pytest.param(
            {"presentation_revision_id": "0.1.55-r1", "archived_crop_sha256": DIGEST},
            id="same-sha",
        ),
        pytest.param({"presentation_revision_id": "0.1.55-r1"}, id="missing-old-sha"),
        *[
            pytest.param(
                {
                    "presentation_revision_id": "0.1.55-r1",
                    "archived_crop_sha256": value,
                },
                id=name,
            )
            for name, value in (
                ("null-old-sha", None),
                ("empty-old-sha", ""),
                ("short-old-sha", "0" * 63),
                ("long-old-sha", "0" * 65),
                ("nonhex-old-sha", "g" * 64),
                ("uppercase-old-sha", "A" * 64),
                ("integer-old-sha", 123),
                ("list-old-sha", ["0" * 64]),
            )
        ],
        pytest.param({"archived_crop_sha256": "0" * 64}, id="missing-revision"),
        *[
            pytest.param(
                {"presentation_revision_id": value, "archived_crop_sha256": "0" * 64},
                id=name,
            )
            for name, value in (
                ("empty-revision", ""),
                ("null-revision", None),
                ("integer-revision", 123),
            )
        ],
    ],
)
def test_incomplete_or_unchanged_crop_revision_keeps_plain_caption(revision_fields):
    descriptor = {
        "crop_id": "ORIGINAL-CROP-HANDLE",
        "evidence_role": "question",
        "sha256": DIGEST,
        "source_page": 1,
        **revision_fields,
    }
    original = deepcopy(descriptor)
    images = image_descriptors("master", "A1", {"evidence_descriptors": [descriptor]})
    assert len(images) == 1
    assert images[0].caption_zh == "题面 · 第 1 页"
    assert images[0].sha256 == DIGEST
    assert images[0].crop_id == "ORIGINAL-CROP-HANDLE"
    assert descriptor == original


class Reader:
    def __init__(self):
        self.value = catalog()
        self.details = {
            "A1": scan("A1"),
            "A2": scan("A2"),
            "W1": scan("W1"),
            "W2": scan("W2"),
        }
        self.crop_calls = []
        self.fail = False
        self.bad_bytes = False
        self.crosswalk_state = "exact"

    def groups(self, scope):
        value = deepcopy(self.value)
        value["scope"] = scope
        return value

    def theme_groups(self):
        return self.groups("supplemental")

    def detail(self, node):
        if self.fail:
            error = RuntimeError("private path diagnostic")
            error.status = 404
            raise error
        return deepcopy(self.details[node])

    def atomic_detail(self, node):
        return {
            "node": {
                "crosswalk_summary": {
                    "state": self.crosswalk_state,
                    "wave1_node_ids": ["W1"],
                }
            }
        }

    def visual_scan_identity_index(self):
        return {
            "exact_master_to_wave1": (
                {"A1": "W1", "A2": "W1"} if self.crosswalk_state == "exact" else {}
            )
        }

    def question_crop(self, node, crop):
        self.crop_calls.append((node, crop))
        return CandidateCropPayload(b"wrong" if self.bad_bytes else RAW, DIGEST)


@pytest.fixture
def fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    paths = DesktopPaths.from_workspace(workspace, state_root=tmp_path / "personal")
    reader, wave = Reader(), Reader()
    facade = DesktopWorkbenchFacade(
        paths,
        theme_reader=reader,
        supplemental_reader=reader,
        wave_visual_reader=wave,
        wave_crop_reader=wave,
        master_workbench_reader=reader,
        master_direct_reader=reader,
        provider_store=SimpleNamespace(list_metadata=list),
    )
    return facade, reader, wave


def selected(facade, scope="master"):
    return facade.search_themes(scope=scope).cards[0]


@pytest.mark.parametrize("scope", ["master", "wave1", "supplemental"])
def test_real_search_snapshot_opens_detail_and_basket(fixture, scope):
    facade, reader, _ = fixture
    card = selected(facade, scope)
    assert card.data_snapshot_id == facade._paper_catalog_snapshot_id(
        reader.groups(scope)
    )
    detail = facade.library_theme_detail(card)
    assert len(detail.parts) == 2
    assert detail.parts[0].label_zh == "原卷第 7 题 · 作答单元 1"
    assert detail.parts[1].label_zh == "原卷第 7 题 · 作答单元 2"
    assert "原卷第 7 题 · 作答单元 1" in detail.parts[1].dependency_zh
    assert len(detail.shared_images) == 1
    assert [part.reference_answer_zh for part in detail.parts] == [
        "  来源答案原文\n第二行  "
    ] * 2
    assert "未独立核验" in detail.parts[0].answer_boundary_zh
    assert len(detail.parts[0].question_images) == 1
    assert facade.library_image(detail.parts[0].question_images[0]) == RAW
    assert facade.add_theme_to_basket(card) == 1
    _, selections = facade._paper_export_selections(
        {"selection_basis": {"basket": list(facade.basket())}}
    )
    assert selections[0]["expected_data_snapshot_id"] == card.data_snapshot_id


def test_supplied_catalog_snapshot_not_search_signature(fixture):
    facade, reader, _ = fixture
    reader.value["data_snapshot_id"] = "a" * 64
    assert selected(facade).data_snapshot_id == "a" * 64


def test_stale_card_cannot_open_new_theme(fixture):
    facade, reader, _ = fixture
    card = selected(facade)
    reader.value["papers"][0]["theme_groups"][0]["theme"]["title"] = "changed"
    with pytest.raises(DesktopFacadeError, match="题库已变化"):
        facade.library_theme_detail(card)


def test_unknown_theme_identity_rejected(fixture):
    facade, _, _ = fixture
    with pytest.raises(DesktopFacadeError, match="来源缺失"):
        facade.library_theme_detail(
            replace(selected(facade), source_identity_sha256="0" * 64)
        )


def test_personal_handout_scope_not_silently_reinterpreted(fixture):
    facade, _, _ = fixture
    with pytest.raises(DesktopFacadeError, match="范围尚未"):
        facade.library_theme_detail(
            replace(selected(facade), scope="personal_handouts")
        )


def test_master_exact_keeps_parent_labels_but_images_use_explicit_wave_binding(fixture):
    facade, reader, wave = fixture
    reader.fail = True
    detail = facade.library_theme_detail(selected(facade))
    image = detail.parts[0].question_images[0]
    assert detail.parts[0].key == "A1"
    assert (image.scope, image.node_id) == ("wave1", "W1")
    assert facade.library_image(image) == RAW
    assert wave.crop_calls == [("W1", "W1-Q")]
    assert not reader.crop_calls


def test_exact_image_lookup_never_loads_all_reference_answers(fixture, monkeypatch):
    facade, reader, _ = fixture
    reader.fail = True

    def forbidden(_node):
        raise AssertionError("exact preview must not rebuild all Wave answers")

    monkeypatch.setattr(reader, "atomic_detail", forbidden)
    detail = facade.library_theme_detail(selected(facade))
    assert facade.library_image(detail.parts[0].question_images[0]) == RAW


def test_exact_target_failure_does_not_borrow_another_image(fixture):
    facade, reader, wave = fixture
    reader.fail = wave.fail = True
    detail = facade.library_theme_detail(selected(facade))
    assert all(
        not part.question_images and not part.reference_answer_zh
        for part in detail.parts
    )
    assert "详情暂不可读" in detail.parts[0].availability_zh


@pytest.mark.parametrize("state", ["split", "anchor", "unmapped"])
def test_nonexact_master_never_borrows_pixels_or_answers(fixture, state):
    facade, reader, _ = fixture
    reader.fail, reader.crosswalk_state = True, state
    detail = facade.library_theme_detail(selected(facade))
    assert all(
        not part.question_images and not part.reference_answer_zh
        for part in detail.parts
    )
    assert detail.parts[0].summary_zh == "合成测试题意，不是真实化学题。"
    assert "尚无可读题面" in detail.parts[0].availability_zh


def test_alias_units_are_expanded_without_merging_answers(fixture):
    facade, reader, _ = fixture
    chain = reader.value["papers"][0]["theme_groups"][0]["atomic_chain"]
    chain[:] = [atom()]
    chain[0]["alias_units"] = [
        {"atomic_part_id": "W1", "atomic_sequence_in_printed": 1},
        {"atomic_part_id": "W2", "atomic_sequence_in_printed": 2},
    ]
    detail = facade.library_theme_detail(selected(facade))
    assert [p.key for p in detail.parts] == ["W1", "W2"]
    assert [p.question_images[0].node_id for p in detail.parts] == ["W1", "W2"]
    assert all("第 7 题" in p.label_zh for p in detail.parts)


def direct_group(fixture):
    facade, reader, _ = fixture
    chain = reader.value["papers"][0]["theme_groups"][0]["atomic_chain"]
    chain[:] = [atom()]
    chain[0]["alias_units"] = [
        {
            "atomic_part_id": unit_id,
            "atomic_sequence_in_printed": index,
            "source_scope": "master",
            "source_parent_atomic_id": "A1",
            "dependency": {"kind": "one_prior_part", "prior_atomic_part_ids": ["U1"]}
            if index == 2
            else {"kind": "independent", "prior_atomic_part_ids": []},
        }
        for index, unit_id in enumerate(("U1", "U2"), 1)
    ]
    reader.details["A1"]["minimal_atomic_units"] = [
        {
            "atomic_part_id": unit_id,
            "visible_summary_zh": f"分项摘要 {index}",
            "response_requirement_zh": f"分项要求 {index}",
            "dependency": {"prior_atomic_part_ids": ["U1"] if index == 2 else []},
            "classification": {"item_type": "fill_blank", "primary_K": f"K0{index}"},
            "cognitive_difficulty": {"cognitive_prelabel": f"D{index}"},
            "answer_boundary": {
                "availability": "present_part_aligned",
                "authority": "nonofficial_reference",
            },
            "reference_answer": {"reference_answer_text": f"分项答案 {index}"},
            "quality_notes": [f"分项备注 {index}"],
        }
        for index, unit_id in enumerate(("U1", "U2"), 1)
    ]
    return facade, reader


def test_direct_units_keep_parent_crop_and_exact_unit_answer_and_classification(
    fixture,
):
    facade, reader = direct_group(fixture)
    detail = facade.library_theme_detail(selected(facade))
    assert [p.key for p in detail.parts] == ["U1", "U2"]
    assert [p.summary_zh for p in detail.parts] == ["分项摘要 1", "分项摘要 2"]
    assert [p.reference_answer_zh for p in detail.parts] == ["分项答案 1", "分项答案 2"]
    assert all(
        not p.analysis_zh for p in detail.parts
    )  # Parent analysis is not unit evidence.
    assert detail.parts[0].classification_zh != detail.parts[1].classification_zh
    assert "原卷第 7 题 · 作答单元 1" in detail.parts[1].dependency_zh
    assert detail.parts[1].quality_notes_zh == ("分项备注 2",)
    image = detail.parts[1].question_images[0]
    assert (image.scope, image.node_id, image.crop_id) == ("master", "A1", "A1-Q")
    assert facade.library_image(image) == RAW
    assert reader.crop_calls == [("A1", "A1-Q")]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unaligned"])
def test_direct_unit_failure_never_exposes_grouped_answer(fixture, mutation):
    facade, reader = direct_group(fixture)
    units = reader.details["A1"]["minimal_atomic_units"]
    if mutation == "missing":
        units.pop()
    elif mutation == "duplicate":
        units.append(deepcopy(units[-1]))
    else:
        units[-1]["answer_boundary"]["availability"] = "present_unaligned"
    parts = facade.library_theme_detail(selected(facade)).parts
    assert parts[0].reference_answer_zh == "分项答案 1"
    assert not parts[1].reference_answer_zh
    assert not parts[1].analysis_zh
    if mutation != "unaligned":
        assert not parts[1].question_images
        assert "暂不可读" in parts[1].availability_zh


@pytest.mark.parametrize("availability", ["present_unaligned", "absent"])
def test_unaligned_and_absent_answers_never_show_body(fixture, availability):
    facade, reader, _ = fixture
    reader.details["A1"]["reference_answer"]["availability"] = availability
    part = facade.library_theme_detail(selected(facade)).parts[0]
    assert not part.reference_answer_zh
    assert part.analysis_zh == ("模型候选步骤，绝不是来源答案。",)


def test_missing_scan_does_not_remove_theme_or_leak_diagnostics(fixture):
    facade, reader, _ = fixture

    def fail(_):
        raise RuntimeError("C:/private/path secret")

    reader.detail = fail
    detail = facade.library_theme_detail(selected(facade))
    assert len(detail.parts) == 2
    assert "暂不可读" in detail.parts[0].availability_zh
    assert "private" not in repr(detail)


@pytest.mark.parametrize("mutation", ["role", "crop_id", "sha256", "node_id"])
def test_image_request_cannot_change_role_identity_or_hash(fixture, mutation):
    facade, _, _ = fixture
    image = facade.library_theme_detail(selected(facade)).parts[0].question_images[0]
    value = {
        "role": "answer",
        "crop_id": "ANSWER",
        "sha256": "a" * 64,
        "node_id": "not-a-node",
    }[mutation]
    with pytest.raises(DesktopFacadeError):
        facade.library_image(replace(image, **{mutation: value}))


def test_source_bytes_are_rechecked(fixture):
    facade, reader, _ = fixture
    image = facade.library_theme_detail(selected(facade)).parts[0].question_images[0]
    reader.bad_bytes = True
    with pytest.raises(DesktopFacadeError, match="校验失败"):
        facade.library_image(image)


def test_no_question_number_inferred_from_id(fixture):
    facade, reader, _ = fixture
    reader.value["papers"][0]["theme_groups"][0]["atomic_chain"][0][
        "printed_question_number"
    ] = None
    assert (
        "题号待核对" in facade.library_theme_detail(selected(facade)).parts[0].label_zh
    )


def test_answer_descriptors_are_never_projected(fixture):
    facade, _, _ = fixture
    detail = facade.library_theme_detail(selected(facade))
    assert all(i.role != "answer" for i in detail.shared_images)
    assert all(i.crop_id != "ANSWER" for p in detail.parts for i in p.question_images)
    with pytest.raises(DesktopFacadeError):
        facade.library_image(
            LibraryImage("master", "A1", "ANSWER", DIGEST, "answer", "答案")
        )


def test_expired_view_does_not_silently_read_current_source(fixture):
    facade, _, _ = fixture
    first = facade.library_theme_detail(selected(facade)).parts[0].question_images[0]
    facade.library_theme_detail(selected(facade))
    latest = facade.library_theme_detail(selected(facade)).parts[0].question_images[0]
    assert facade.library_image(latest) == RAW
    with pytest.raises(DesktopFacadeError, match="会话已结束"):
        facade.library_image(first)

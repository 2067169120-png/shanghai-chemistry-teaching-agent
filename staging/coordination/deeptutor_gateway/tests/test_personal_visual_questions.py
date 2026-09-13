from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1 import (
    desktop_personal_visual_questions as personal_visual,
)
from integrations.deeptutor_shchem_v1 import desktop_visual_import_v2 as bridge
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import (
    SELECTION_DRAFT,
    PersonalVisualQuestionError,
    PersonalVisualQuestionService,
    matches_personal_visual_filters,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_filters import (
    chapter_filter_id,
    section_filter_id,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_PATH = (
    Path(__file__).with_name("fixtures")
    / "desktop_visual_import_v2"
    / "synthetic_multifile_theme.json"
)
FACADE_TEST_PATH = Path(__file__).with_name("test_desktop_visual_import_facade.py")
SUPPORT_TEST_PATH = (
    WORKSPACE_ROOT
    / "parallel_outputs"
    / "intake_batches_v2"
    / "tests"
    / "test_intake_batches_v2.py"
)
BATCH_ID = "DESKTOPBATCH-" + "a" * 32


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_FACADE_TEST = _load_module(FACADE_TEST_PATH, "personal_visual_facade_fixture")
_SUPPORT_TEST = _load_module(SUPPORT_TEST_PATH, "personal_visual_intake_fixture")


def _write_synthetic_catalog(workspace: Path) -> None:
    taxonomy_root = workspace / "sh-chem-db" / "kb"
    taxonomy_root.mkdir(parents=True, exist_ok=True)
    (taxonomy_root / "knowledge_taxonomy.json").write_text(
        json.dumps(
            {
                "dimensions": {
                    "knowledge_points": [
                        {"id": "K-electrochemistry", "name": "电化学"},
                        {"id": "K-redox", "name": "氧化还原反应"},
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    directory_root = (
        taxonomy_root
        / "classification"
        / "supplemental_wechat_textbook_tagging_v1_2026-08-27"
    )
    directory_root.mkdir(parents=True, exist_ok=True)
    (directory_root / "textbook_directory_nodes.json").write_text(
        json.dumps({"nodes": []}, ensure_ascii=False), encoding="utf-8"
    )


def _source_rows(
    fixture: dict[str, Any],
) -> tuple[list[bridge.DesktopSourceFile], list[bridge.DesktopSourceFile]]:
    questions = [
        bridge.DesktopSourceFile(
            role="question",
            order_index=index,
            filename=row["filename"],
            mime_type=row["mime_type"],
            content=base64.b64decode(row["content_base64"], validate=True),
            group_id="fixture-paper",
            source_file_id=row["source_file_id"],
        )
        for index, row in enumerate(fixture["question_files"], 1)
    ]
    answers = [
        bridge.DesktopSourceFile(
            role="answer",
            order_index=index,
            filename=row["filename"],
            mime_type=row["mime_type"],
            content=base64.b64decode(row["content_base64"], validate=True),
            group_id="fixture-paper",
            source_file_id=row["source_file_id"],
        )
        for index, row in enumerate(fixture["answer_files"], 1)
    ]
    return questions, answers


@pytest.fixture
def imported_visual_batch(tmp_path: Path):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    workspace = tmp_path / "workspace"
    _write_synthetic_catalog(workspace)
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )
    facade = _FACADE_TEST._facade(
        paths, _FACADE_TEST.FakeProviderStore(configured=False)
    )
    questions, answers = _source_rows(fixture)
    provider = _SUPPORT_TEST.SyntheticStrictVisualProvider(fixture)
    coordinator = bridge.DesktopImportCoordinatorV2(
        visual_provider=provider,
        renderer=_FACADE_TEST.StaticV2Renderer(),
        archive_root=facade._visual_import_root,
        max_pages_per_shard=2,
    )
    result = coordinator.process(
        bridge.DesktopImportRequest.from_sources(
            question_files=questions,
            answer_files=answers,
            batch_id=BATCH_ID,
            source_type="合成个人视觉题",
        ),
        visual_confirmation=True,
    )
    assert result.visual_status == "completed"
    assert result.visual_candidate is not None
    receipt = facade._save_visual_import_descriptor(
        sources=(*questions, *answers), result=result
    )
    assert receipt.status == "candidate_ready_for_review"
    service = PersonalVisualQuestionService(facade)
    return {
        "fixture": fixture,
        "paths": paths,
        "facade": facade,
        "service": service,
        "provider": provider,
        "questions": questions,
        "answers": answers,
        "result": result,
    }


@pytest.fixture
def misaligned_visual_batch(tmp_path: Path):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    workspace = tmp_path / "workspace"
    _write_synthetic_catalog(workspace)
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )
    facade = _FACADE_TEST._facade(
        paths, _FACADE_TEST.FakeProviderStore(configured=False)
    )
    questions, answers = _source_rows(fixture)
    provider = _SUPPORT_TEST.SyntheticStrictVisualProvider(
        fixture, misalign_second_answer=True
    )
    coordinator = bridge.DesktopImportCoordinatorV2(
        visual_provider=provider,
        renderer=_FACADE_TEST.StaticV2Renderer(),
        archive_root=facade._visual_import_root,
        max_pages_per_shard=2,
    )
    result = coordinator.process(
        bridge.DesktopImportRequest.from_sources(
            question_files=questions,
            answer_files=answers,
            batch_id=BATCH_ID,
            source_type="合成个人视觉题",
        ),
        visual_confirmation=True,
    )
    facade._save_visual_import_descriptor(
        sources=(*questions, *answers), result=result
    )
    return {
        "fixture": fixture,
        "paths": paths,
        "facade": facade,
        "service": PersonalVisualQuestionService(facade),
        "provider": provider,
        "result": result,
    }


class _OptionRichVisualProvider(_SUPPORT_TEST.SyntheticStrictVisualProvider):
    """Add option-only evidence and chemistry to the real synthetic provider."""

    def analyze_shard(self, request: Any) -> dict[str, Any]:
        fragment = deepcopy(super().analyze_shard(request))
        if request.source_role != "question" or not any(
            page.source_file_id == "SRC-Q-C" for page in request.pages
        ):
            return fragment
        page = request.pages[0]
        evidence = {
            "evidence_id": "EV-OPTION-Q-C",
            "source_file_id": page.source_file_id,
            "source_role": "question",
            "page_number": page.page_number,
            "page_sha256": page.page_sha256,
            "bbox": {"x": 0.55, "y": 0.12, "width": 0.25, "height": 0.24},
        }
        fragment["evidence"].append(evidence)
        printed = fragment["theme_fragments"][0]["printed_questions"][0]
        option = printed["options"][0]
        option["chemical_expressions"] = [
            {
                "raw": "选项独立化学表达式",
                "normalized": r"\\ce{X}",
                "kind": "formula",
                "status": "observed",
                "evidence_refs": [evidence["evidence_id"]],
            }
        ]
        option["evidence_refs"] = [evidence["evidence_id"]]
        option["visual_object_refs"] = ["VISUAL-SYN-1"]
        atomic = printed["atomic_parts"][0]
        atomic["visual_object_refs"] = []
        return fragment


@pytest.fixture
def option_rich_visual_batch(tmp_path: Path):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    workspace = tmp_path / "workspace"
    _write_synthetic_catalog(workspace)
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )
    facade = _FACADE_TEST._facade(
        paths, _FACADE_TEST.FakeProviderStore(configured=False)
    )
    questions, answers = _source_rows(fixture)
    provider = _OptionRichVisualProvider(fixture)
    coordinator = bridge.DesktopImportCoordinatorV2(
        visual_provider=provider,
        renderer=_FACADE_TEST.StaticV2Renderer(),
        archive_root=facade._visual_import_root,
        max_pages_per_shard=2,
    )
    result = coordinator.process(
        bridge.DesktopImportRequest.from_sources(
            question_files=questions,
            answer_files=answers,
            batch_id=BATCH_ID,
            source_type="合成个人视觉题",
        ),
        visual_confirmation=True,
    )
    facade._save_visual_import_descriptor(
        sources=(*questions, *answers), result=result
    )
    return {
        "paths": paths,
        "facade": facade,
        "service": PersonalVisualQuestionService(facade),
        "provider": provider,
        "result": result,
    }


def _items(context: dict[str, Any]) -> list[dict[str, Any]]:
    result = context["service"].catalog(BATCH_ID)
    assert result["warnings"] == []
    return result["items"]


def _curriculum_projection(context, monkeypatch, refs, *, nodes=None):
    nodes = nodes if nodes is not None else [
        {"node_key": "SEC-A", "volume_id": "BOOK-A", "chapter_id": "CH-A",
         "volume_title": "合成甲册", "chapter_title": "甲章", "section_title": "甲节"},
        {"node_key": "SEC-B", "volume_id": "BOOK-B", "chapter_id": "CH-B",
         "volume_title": "合成乙册", "chapter_title": "乙章", "section_title": "乙节"},
    ]
    service = context["service"]
    snapshot, pages = service._batch(BATCH_ID)
    snapshot = deepcopy(snapshot)
    printed = snapshot["candidate"]["paper"]["theme_big_questions"][0]["printed_questions"]
    for question in printed:
        for atomic in question["atomic_parts"]:
            atomic["curriculum"].update(primary_chapter="unknown", secondary_chapters=[])
    printed[0]["atomic_parts"][0]["curriculum"].update(
        primary_chapter=refs[0] if refs else "unknown", secondary_chapters=refs[1:]
    )
    monkeypatch.setattr(personal_visual, "load_attribute_catalog", lambda _root: {
        "nodes": nodes, "knowledge_points": []
    })
    monkeypatch.setattr(service, "_batch", lambda _batch: (snapshot, pages))
    return service.catalog(BATCH_ID)


def test_curriculum_projection_preserves_paths_and_parent_qualified_options(
    imported_visual_batch, monkeypatch,
):
    context = imported_visual_batch
    before = deepcopy(context["facade"].state_store.snapshot())
    result = _curriculum_projection(context, monkeypatch, ["SEC-A", "SEC-B", "SEC-A"])
    row = result["items"][0]
    assert row["curriculum_paths"] == [
        {"volume_id": "BOOK-A", "chapter_id": "CH-A", "section_key": "SEC-A"},
        {"volume_id": "BOOK-B", "chapter_id": "CH-B", "section_key": "SEC-B"},
    ]
    assert row["facets"]["chapter"] == [
        chapter_filter_id("BOOK-A", "CH-A"), chapter_filter_id("BOOK-B", "CH-B")
    ]
    option = next(option for option in result["filter_options"]["section"]
                  if option["value"] == section_filter_id("BOOK-B", "CH-B", "SEC-B"))
    assert option == {
        "value": section_filter_id("BOOK-B", "CH-B", "SEC-B"),
        "label": "合成乙册 / 乙章 / 乙节", "volume_id": "BOOK-B",
        "chapter_id": "CH-B", "section_key": "SEC-B",
    }
    assert context["facade"].state_store.snapshot() == before


@pytest.mark.parametrize("selection, expected", [
    ({"book": {"BOOK-A", "BOOK-B"}, "chapter": {chapter_filter_id("BOOK-B", "CH-B")},
      "section": {section_filter_id("BOOK-B", "CH-B", "SEC-B")}}, True),
    ({"book": {"BOOK-A"}, "chapter": {chapter_filter_id("BOOK-B", "CH-B")}}, False),
    ({"chapter": {chapter_filter_id("BOOK-A", "CH-A")},
      "section": {section_filter_id("BOOK-B", "CH-B", "SEC-B")}}, False),
    ({"book": {"BOOK-A"}, "chapter": {"CH-A"}}, False),
    ({"book": {"BOOK-A"}, "grade": {"grade_12"}}, False),
    ({"book": {"BOOK-A"}, "grade": {"unknown"}, "source": {BATCH_ID}}, True),
    ({"book": {"nonexistent"}}, False),
])
def test_curriculum_multiselect_matches_one_path(
    imported_visual_batch, monkeypatch, selection, expected,
):
    row = _curriculum_projection(imported_visual_batch, monkeypatch, ["SEC-A", "SEC-B"])["items"][0]
    original = deepcopy(row)
    assert matches_personal_visual_filters(row, selection) is expected
    assert row == original


def test_bare_chapter_does_not_guess_book_or_section(imported_visual_batch, monkeypatch):
    nodes = [
        {"node_key": "SEC-A", "volume_id": "BOOK-A", "chapter_id": "CH-DUP"},
        {"node_key": "SEC-B", "volume_id": "BOOK-B", "chapter_id": "CH-DUP"},
        {"node_key": "SEC-C1", "volume_id": "BOOK-C", "chapter_id": "CH-C"},
        {"node_key": "SEC-C2", "volume_id": "BOOK-C", "chapter_id": "CH-C"},
    ]
    row = _curriculum_projection(imported_visual_batch, monkeypatch, ["CH-DUP", "CH-C"], nodes=nodes)["items"][0]
    assert row["curriculum_paths"] == [
        {"volume_id": "BOOK-C", "chapter_id": "CH-C", "section_key": "unknown"}
    ]
    assert row["facets"]["section"] == ["unknown"]
    assert matches_personal_visual_filters(row, {"book": {"BOOK-C"}, "section": {"unknown"}})
    assert not matches_personal_visual_filters(row, {"book": {"BOOK-A"}})


def test_explicit_section_keeps_duplicate_chapter_ids_in_their_own_books(imported_visual_batch, monkeypatch):
    nodes = [
        {"node_key": "SEC-A", "volume_id": "BOOK-A", "chapter_id": "CH-DUP"},
        {"node_key": "SEC-B", "volume_id": "BOOK-B", "chapter_id": "CH-DUP"},
    ]
    result = _curriculum_projection(imported_visual_batch, monkeypatch, ["SEC-A", "SEC-B"], nodes=nodes)
    row = result["items"][0]
    assert row["facets"]["chapter"] == [
        chapter_filter_id("BOOK-A", "CH-DUP"), chapter_filter_id("BOOK-B", "CH-DUP")
    ]
    assert not matches_personal_visual_filters(row, {
        "book": {"BOOK-A"}, "chapter": {chapter_filter_id("BOOK-B", "CH-DUP")}
    })
    assert matches_personal_visual_filters(row, {
        "book": {"BOOK-A"}, "chapter": {chapter_filter_id("BOOK-A", "CH-DUP")}
    })


def test_unknown_and_ambiguous_section_ids_remain_unmapped(imported_visual_batch, monkeypatch):
    nodes = [
        {"node_key": "SEC-CONFLICT", "volume_id": "BOOK-A", "chapter_id": "CH-A"},
        {"node_key": "SEC-CONFLICT", "volume_id": "BOOK-B", "chapter_id": "CH-B"},
        {"node_key": "SEC-BAD", "volume_id": "unknown", "chapter_id": "CH-BAD"},
    ]
    row = _curriculum_projection(imported_visual_batch, monkeypatch,
                                ["SEC-CONFLICT", "SEC-BAD", "missing"], nodes=nodes)["items"][0]
    assert row["curriculum_paths"] == []
    assert matches_personal_visual_filters(row, {"book": {"unknown"}, "chapter": {"unknown"}})
    assert not matches_personal_visual_filters(row, {"book": {"BOOK-A"}})


def test_legacy_facet_only_supports_non_curriculum_filters_without_inventing_paths():
    row = {"facets": {"book": ["BOOK-A"], "chapter": ["CH-A"],
                      "knowledge": ["K01", "K02"], "exam": ["second_mock"]}}
    assert matches_personal_visual_filters(row, {})
    assert matches_personal_visual_filters(row, {"knowledge": {"K02", "K03"}})
    assert matches_personal_visual_filters(row, {"knowledge": {"K01", "K02"}, "knowledge_mode": "all"})
    assert not matches_personal_visual_filters(row, {"knowledge": {"K01", "K03"}, "knowledge_mode": "all"})
    assert matches_personal_visual_filters(row, {"exam": {"second_mock"}, "knowledge": {"K01"}})
    assert not matches_personal_visual_filters(row, {"book": {"BOOK-A"}})
    assert not matches_personal_visual_filters(row, {"book": {"unknown"}})
    assert not matches_personal_visual_filters(row, {"book": "BOOK-A"})
    assert not matches_personal_visual_filters(row, {"knowledge_mode": "invalid"})
    assert not matches_personal_visual_filters({**row, "curriculum_paths": [{}]}, {"book": {"unknown"}})


def test_catalog_and_detail_project_cas_hierarchy_with_role_separated_images(
    imported_visual_batch: dict[str, Any],
) -> None:
    items = _items(imported_visual_batch)
    assert len(items) == 2
    assert [item["question_number"] for item in items] == ["1", "2"]
    assert items[0]["theme_title"] == "合成电化学主题"
    assert items[0]["selection_ready"] is True
    assert items[0]["shared_text"]
    assert "电极反应式" in items[0]["question_text"]
    assert "电子由铜电极流向银电极" in items[1]["question_text"]
    assert {image["role"] for image in items[0]["images"]} == {
        "shared_material",
        "question",
        "answer",
    }
    assert all(item["candidate_sha256"] for item in items)
    assert all(item["candidate_revision"] for item in items)
    assert imported_visual_batch["service"].detail(
        BATCH_ID, items[0]["key"], items[0]["revision"]
    ) == items[0]


def test_option_and_shared_visual_edges_keep_independent_evidence_and_chemistry(
    option_rich_visual_batch: dict[str, Any],
) -> None:
    items = _items(option_rich_visual_batch)
    second_question_images = {
        image["evidence_id"]
        for image in items[1]["images"]
        if image["role"] == "question"
    }
    shared_images = {
        image["evidence_id"]
        for image in items[0]["images"]
        if image["role"] == "shared_material"
    }
    assert "选项独立化学表达式" in items[1]["question_text"]
    assert "EV-OPTION-Q-C" in second_question_images
    assert "EV-SRC-Q-B" in second_question_images
    assert "EV-SRC-Q-B" in shared_images


def test_image_returns_exact_original_or_distinct_review_crop(
    imported_visual_batch: dict[str, Any],
) -> None:
    context = imported_visual_batch
    item = _items(context)[0]
    question_image = next(
        image for image in item["images"] if image["role"] == "question"
    )
    original = context["service"].image(
        BATCH_ID, item["key"], item["revision"], question_image["image_id"], original=True
    )
    cropped = context["service"].image(
        BATCH_ID, item["key"], item["revision"], question_image["image_id"]
    )
    source = next(
        row
        for row in context["questions"]
        if row.source_file_id == "SRC-Q-A"
    )
    assert original["bytes"] == source.content
    assert cropped["bytes"] != original["bytes"]
    with Image.open(io.BytesIO(original["bytes"])) as source_image:
        original_size = source_image.size
    with Image.open(io.BytesIO(cropped["bytes"])) as crop_image:
        assert crop_image.size[0] < original_size[0]
        assert crop_image.size[1] < original_size[1]
    second = _items(context)[1]
    second_image = next(
        image for image in second["images"] if image["role"] == "question"
    )
    second_crop = context["service"].image(
        BATCH_ID, second["key"], second["revision"], second_image["image_id"]
    )
    assert second_crop["bytes"] != cropped["bytes"]


def test_unaligned_answer_is_not_borrowed_by_another_question(
    misaligned_visual_batch: dict[str, Any],
) -> None:
    items = _items(misaligned_visual_batch)
    second = items[1]
    assert "参考答案待补充" in second["answer_text"]
    assert "Cu - 2e⁻" not in second["answer_text"]
    assert not [image for image in second["images"] if image["role"] == "answer"]
    assert any("未找到唯一对应答案" in warning for warning in second["warnings"])


def test_save_selection_survives_service_and_facade_restart(
    imported_visual_batch: dict[str, Any],
) -> None:
    context = imported_visual_batch
    item = _items(context)[1]
    context["service"].save_selection(
        [{"batch_id": BATCH_ID, "key": item["key"], "revision": item["revision"], "points": 3}]
    )
    restarted = _FACADE_TEST._facade(
        context["paths"], _FACADE_TEST.FakeProviderStore(configured=False)
    )
    catalog = PersonalVisualQuestionService(restarted).catalog()
    assert [row["key"] for row in catalog["selection"]] == [item["key"]]
    assert catalog["selection"][0]["points"] == 3


@pytest.mark.parametrize(
    "saved_selection",
    [
        None,
        [None],
        [{"batch_id": "DESKTOPBATCH-" + "b" * 32}],
    ],
)
def test_corrupt_saved_selection_is_ignored_without_catalog_crash(
    imported_visual_batch: dict[str, Any], saved_selection: Any
) -> None:
    context = imported_visual_batch
    item = _items(context)[0]
    if isinstance(saved_selection, list) and saved_selection and isinstance(
        saved_selection[0], dict
    ):
        saved_selection[0].update(
            {"key": item["key"], "revision": item["revision"]}
        )
    context["facade"].state_store.save_draft(
        SELECTION_DRAFT, {"selections": saved_selection}
    )
    result = context["service"].catalog()
    assert result["selection"] == []


def test_reference_selecting_one_question_carries_complete_theme_and_dedupes_images(
    imported_visual_batch: dict[str, Any],
) -> None:
    context = imported_visual_batch
    item = _items(context)[1]
    reference = context["service"].reference(
        [{"batch_id": BATCH_ID, "key": item["key"], "revision": item["revision"]}]
    )
    assert reference["source_kind"] == "personal_visual"
    assert reference["theme_count"] == 1
    assert reference["question_count"] == 2
    assert "第1题" in reference["materials"]
    assert "第2题" in reference["materials"]
    assert reference["include_images"] is True
    assets = reference["image_assets"]
    assert assets
    assert len({asset["sha256"] for asset in assets}) == len(assets)
    assert all(asset["sha256"] for asset in assets)


def test_import_reference_writes_only_verified_assets_and_is_idempotent(
    imported_visual_batch: dict[str, Any],
) -> None:
    context = imported_visual_batch
    item = _items(context)[0]
    reference = context["service"].reference(
        [{"batch_id": BATCH_ID, "key": item["key"], "revision": item["revision"]}]
    )
    imported = context["service"].import_reference(reference, [])
    image_root = context["paths"].task_root / "preparation-v1" / "images"
    assert imported["image_assets"]
    assert all(
        (image_root / (asset["sha256"] + ".image")).is_file()
        for asset in imported["image_assets"]
    )
    repeated = context["service"].import_reference(reference, imported["image_assets"])
    assert repeated["image_assets"] == imported["image_assets"]
    assert len(list(image_root.rglob("*.image"))) == len(imported["image_assets"])


def test_source_or_page_byte_change_is_rejected_without_partial_import(
    imported_visual_batch: dict[str, Any],
) -> None:
    context = imported_visual_batch
    item = _items(context)[0]
    question_image = next(
        image for image in item["images"] if image["role"] == "question"
    )
    page_path = context["paths"].state_root / "visual-import-v2" / "pages"
    candidate = bridge.CandidateCAS.open(
        context["paths"].state_root / "visual-import-v2" / "candidates" / BATCH_ID
    ).snapshot()["candidate"]
    page_hash = next(
        evidence["page_sha256"]
        for evidence in candidate["evidence"]
        if evidence["evidence_id"] == question_image["evidence_id"]
    )
    page_file = page_path / f"{page_hash}.png"
    original_page_bytes = page_file.read_bytes()
    page_file.write_bytes(original_page_bytes + b"tampered")
    with pytest.raises(PersonalVisualQuestionError, match="已经变化"):
        context["service"].image(
            BATCH_ID, item["key"], item["revision"], question_image["image_id"]
        )
    page_file.write_bytes(original_page_bytes)

    source_path = context["paths"].state_root / "visual-import-v2" / "sources"
    source_id = next(
        evidence["source_file_id"]
        for evidence in candidate["evidence"]
        if evidence["evidence_id"] == question_image["evidence_id"]
    )
    source = next(row for row in context["questions"] if row.source_file_id == source_id)
    source_file = source_path / f"{source.source_sha256}.png"
    original_source_bytes = source_file.read_bytes()
    source_file.write_bytes(original_source_bytes + b"tampered")
    with pytest.raises(PersonalVisualQuestionError, match="归档不完整或已变化"):
        context["service"].detail(BATCH_ID, item["key"], item["revision"])


def test_cas_revision_change_invalidates_old_selection_and_reference(
    imported_visual_batch: dict[str, Any],
) -> None:
    context = imported_visual_batch
    item = _items(context)[0]
    cas = context["result"].visual_revision_token
    assert cas
    cas_store = context["paths"].state_root / "visual-import-v2" / "candidates" / BATCH_ID
    reopened = bridge.CandidateCAS.open(cas_store)
    snapshot = reopened.snapshot()
    edited = reopened.edit_atomic_part(
        "ATOMIC-SYN-1",
        {"stem": "教师复核后的合成题干。"},
        expected_revision_token=snapshot["revision_token"],
        expected_candidate_sha256=snapshot["candidate_sha256"],
        actor_id="teacher-test",
        idempotency_key="personal-visual-cas-edit-1",
    )
    assert edited["revision_token"] != item["candidate_revision"]
    with pytest.raises(PersonalVisualQuestionError, match="已经变化"):
        context["service"].detail(BATCH_ID, item["key"], item["revision"])
    catalog = context["service"].catalog(BATCH_ID)
    assert catalog["items"][0]["revision"] != item["revision"]


def test_read_projection_does_not_call_provider_or_write_central_state(
    imported_visual_batch: dict[str, Any],
) -> None:
    context = imported_visual_batch
    provider_calls = len(context["provider"].requests)
    before = deepcopy(context["facade"].state_store.snapshot())
    items = _items(context)
    context["service"].detail(BATCH_ID, items[0]["key"], items[0]["revision"])
    context["service"].reference(
        [{"batch_id": BATCH_ID, "key": items[0]["key"], "revision": items[0]["revision"]}]
    )
    after = context["facade"].state_store.snapshot()
    assert len(context["provider"].requests) == provider_calls
    assert before == after
    assert context["result"].visual_candidate["central_question_bank_write"] is False


def test_cancelled_visual_coordinator_writes_no_candidate_or_central_state(
    tmp_path: Path,
) -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    workspace = tmp_path / "workspace"
    _write_synthetic_catalog(workspace)
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )
    facade = _FACADE_TEST._facade(
        paths, _FACADE_TEST.FakeProviderStore(configured=False)
    )
    questions, answers = _source_rows(fixture)
    provider = _SUPPORT_TEST.SyntheticStrictVisualProvider(fixture)
    coordinator = bridge.DesktopImportCoordinatorV2(
        visual_provider=provider,
        renderer=_FACADE_TEST.StaticV2Renderer(),
        archive_root=facade._visual_import_root,
        max_pages_per_shard=2,
    )
    request = bridge.DesktopImportRequest.from_sources(
        question_files=questions,
        answer_files=answers,
        batch_id=BATCH_ID,
        source_type="合成个人视觉题",
    )
    with pytest.raises(bridge.DesktopImportBridgeError, match="取消"):
        coordinator.process(request, visual_confirmation=True, should_cancel=lambda: True)
    assert provider.requests == []
    assert not (facade._visual_import_root / "candidates" / BATCH_ID).exists()
    assert facade.state_store.snapshot().get("drafts", {}) == {}


def test_missing_page_fails_before_any_reference_asset_is_written(
    imported_visual_batch: dict[str, Any],
) -> None:
    context = imported_visual_batch
    item = _items(context)[0]
    reference = context["service"].reference(
        [{"batch_id": BATCH_ID, "key": item["key"], "revision": item["revision"]}]
    )
    page_path = context["paths"].state_root / "visual-import-v2" / "pages"
    page_file = next(page_path.glob("*.png"))
    page_file.unlink()
    with pytest.raises(PersonalVisualQuestionError):
        context["service"].import_reference(reference, [])
    image_root = context["paths"].task_root / "preparation-v1" / "images"
    assert not image_root.exists() or not list(image_root.rglob("*"))


def test_mutated_reference_is_rejected_without_writing_assets(
    imported_visual_batch: dict[str, Any],
) -> None:
    context = imported_visual_batch
    item = _items(context)[0]
    reference = context["service"].reference(
        [{"batch_id": BATCH_ID, "key": item["key"], "revision": item["revision"]}]
    )
    mutated = deepcopy(reference)
    mutated["materials"] += "\n篡改"
    with pytest.raises(PersonalVisualQuestionError, match="已经变化"):
        context["service"].import_reference(mutated, [])
    image_root = context["paths"].task_root / "preparation-v1" / "images"
    assert not image_root.exists() or not list(image_root.rglob("*"))

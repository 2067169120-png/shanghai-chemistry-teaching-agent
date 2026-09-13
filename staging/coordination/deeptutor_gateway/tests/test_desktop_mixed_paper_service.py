"""Isolated native assembly tests: synthetic bytes, no provider or real state."""

from copy import deepcopy
from io import BytesIO
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace
from zipfile import ZipFile

from lxml import etree
from mixed_pagination_test_support import (
    paginate_and_read,
    synthetic_docx,
    synthetic_pagination,
)
import pytest
from test_desktop_visual_import_facade import (
    desktop_paths as desktop_paths,  # noqa: PLC0414
)

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import (
    REQUEST_SCHEMA,
    MixedPaperError,
    MixedPaperService,
    _digest,
    _sha,
    _word_key,
)
from integrations.deeptutor_shchem_v1.desktop_state import (
    DesktopStateError,
    DesktopStateStore,
)


class Words:
    def __init__(self):
        self.data = b"synthetic original package identity, not a real document"
        self.image = b"synthetic image bytes; decoder belongs to the source reader"
        self._lock = RLock()
        self._locations = {}
        self.row = {
            "key": "word-question-synthetic",
            "revision": "revision-1",
            "points": 2.5,
            "source_sha256": _sha(self.data),
            "source_id": _sha(self.data),
            "origin_block_start": 2,
            "batch_id": "synthetic-batch",
            "archive_source_id": "synthetic-source",
            "source_name": "合成讲义.docx",
            "title": "合成Word题",
            "chapter": "合成章节",
            "selection_ready": True,
            "export_ready": True,
            "boundary_status": "auto_detected",
            "warnings": [],
            "context_blocks": [{"index": 1, "text": "合成公共材料", "assets": []}],
            "question_blocks": [
                {
                    "index": 2,
                    "text": "合成Word题干",
                    "assets": [{"asset_id": "picture-1", "sha256": _sha(self.image)}],
                }
            ],
            "answer_blocks": [{"index": 3, "text": "合成Word答案", "assets": []}],
        }
        self.reader = SimpleNamespace(word_asset_bytes=self.asset)
        self.calls = []

    def asset(self, data, asset_id, *, expected_sha256, render_metafiles):
        assert data == self.data and asset_id == "picture-1"
        assert expected_sha256 == _sha(self.image) and render_metafiles is True
        return {"bytes": self.image, "mime_type": "image/png"}

    def _resolve(self, selections):
        self.calls.append(deepcopy(selections))
        result = []
        for selection in selections:
            if (
                selection["key"] != self.row["key"]
                or selection["revision"] != self.row["revision"]
            ):
                raise MixedPaperError("synthetic stale Word revision")
            result.append(
                {**deepcopy(self.row), "points": selection.get("points", 2.5)}
            )
        return result, {self.row["source_id"]: (SimpleNamespace(content=self.data),)}


def _catalog(scope):
    # Same raw IDs across two scopes intentionally exercise typed identities.
    return {
        "scope": scope,
        "data_snapshot_id": _digest(scope),
        "papers": [
            {
                "paper": {"id": "P1", "title": f"{scope}合成原卷"},
                "theme_groups": [
                    {
                        "theme": {"id": "T1", "title": f"{scope}完整主题"},
                        "atomic_chain": [
                            {"atomic_part_id": "A1"},
                            {"atomic_part_id": "A2"},
                        ],
                    }
                ],
            }
        ],
    }


def _core_item(scope):
    identity = _digest({"scope": scope, "paper": "P1", "theme": "T1"})
    return {
        "key": identity,
        "scope": scope,
        "title_zh": f"{scope}完整主题",
        "paper_title_zh": f"{scope}合成原卷",
        "source_identity_sha256": identity,
        "data_snapshot_id": _digest(scope),
    }


def _bundle(payload, **kwargs):
    assert len(payload["selections"]) == 1
    assert payload["selections"][0]["selection_unit"] == "theme"
    assert isinstance(kwargs["asset_root"], Path)
    scope = payload["selections"][0]["scope"]
    parts = []
    for label in ("A1", "A2"):
        parts.append(
            {
                "question_blocks": [
                    {
                        "block_type": "paragraph",
                        "text_zh": f"{scope}-{label}原题",
                        "asset_ref": None,
                    }
                ],
                "score": payload["score_per_atomic"],
                "answer_space": {"lines": payload["answer_space_lines"]},
                "teacher_notes": {
                    "answer_label_zh": "来源参考答案",
                    "source_reference_answer": {"text_zh": f"{scope}-{label}答案"},
                    "explanation_zh": "合成解析",
                    "pitfalls_zh": [],
                    "source_label_zh": "合成来源",
                },
            }
        )
    section = {
        "theme_score": 2 * payload["score_per_atomic"],
        "shared_materials": [
            {
                "render_once_key": "M1",
                "content_blocks": [
                    {
                        "block_type": "paragraph",
                        "text_zh": f"{scope}原卷共同材料",
                        "asset_ref": None,
                    }
                ],
            }
        ],
        "printed_questions": [{"atomic_parts": parts}],
    }
    blueprint = {
        "shared_materials": [
            {"render_once_key": "M1", "used_by_atomic_ids": ["A1", "A2"]}
        ],
        "printed_questions": [
            {"atomic_parts": [{"atomic_part_id": "A1"}, {"atomic_part_id": "A2"}]}
        ],
    }
    return {
        "blueprint": {"theme_bundles": [blueprint]},
        "preset": {
            "student_version": {"show_item_scores": payload["show_question_scores"]}
        },
        "student_plan": {"visible": {"theme_sections": [deepcopy(section)]}},
        "teacher_plan": {"visible": {"theme_sections": [deepcopy(section)]}},
    }


@pytest.fixture
def setup(tmp_path):
    state = DesktopStateStore(tmp_path / "private-state")
    words = Words()
    facade = SimpleNamespace(
        state_store=state,
        paths=SimpleNamespace(state_root=state.root),
        _word_questions=lambda: words,
        paper_theme_catalog=_catalog,
        _paper_catalog_snapshot_id=lambda catalog: catalog["data_snapshot_id"],
    )
    builder_calls, validated = [], []

    def builder(title, sections, **kwargs):
        builder_calls.append((title, sections, kwargs))
        return {
            "student_bytes": synthetic_docx("Synthetic student"),
            "teacher_bytes": synthetic_docx("Synthetic teacher"),
            "warnings": [],
        }

    service = MixedPaperService(
        facade,
        core_bundle_builder=_bundle,
        docx_builder=builder,
        word_validator=lambda rows: validated.append(deepcopy(rows)),
    )
    service._core_context = lambda: {
        "theme_catalog_loader": _catalog,
        "detail_loader": None,
        "crop_loader": None,
    }
    return service, state, words, builder_calls, validated


def _add_word(service, words):
    service.add_word_questions(
        [{key: words.row[key] for key in ("key", "revision", "points")}]
    )
    return _word_key(words.row["key"])


def _request(service, **changes):
    projection = service.projection()
    return {
        "schema_version": REQUEST_SCHEMA,
        "title": "合成统一练习",
        "subtitle": "",
        "mode": "daily_practice",
        "duration_minutes": 40,
        "show_question_scores": False,
        "basket_sha256": projection["basket_sha256"],
        "section_order": [item["key"] for item in projection["items"]],
        "settings_by_key": {},
        **changes,
    }


def test_atomic_batch_preserves_other_drafts_and_replaces_without_partial_write(
    tmp_path,
):
    state = DesktopStateStore(tmp_path)
    state.save_draft(
        "native-word-question-selection-v1", {"items": [{"key": "legacy"}]}
    )
    state.save_draft("paper-current", {"kind": "paper", "payload": {"title": "旧草稿"}})
    state.add_to_basket({"key": "old", "value": 1})
    state.add_many_to_basket([{"key": "new"}, {"key": "old", "value": 2}])
    assert state.basket() == [{"key": "new"}, {"key": "old", "value": 2}]
    assert state.snapshot()["drafts"]["paper-current"]["payload"]["title"] == "旧草稿"
    before = state.path.read_bytes()
    with pytest.raises(DesktopStateError):
        state.add_many_to_basket([{"key": "good"}, {"key": ""}])
    assert state.path.read_bytes() == before


def test_capacity_failure_never_discards_previous_entries(tmp_path):
    state = DesktopStateStore(tmp_path)
    state.add_many_to_basket([{"key": str(index)} for index in range(100)])
    before = state.path.read_bytes()
    with pytest.raises(DesktopStateError, match="未删减"):
        state.add_many_to_basket([{"key": "overflow"}])
    assert state.path.read_bytes() == before


def test_core_single_add_cannot_evict_word_from_full_shared_basket(setup):
    service, state, words, _, _ = setup
    _add_word(service, words)
    state.add_many_to_basket([{"key": f"old-{index}"} for index in range(99)])
    before = state.path.read_bytes()
    with pytest.raises(DesktopStateError, match="未删减"):
        state.add_to_basket(_core_item("master"))
    assert state.path.read_bytes() == before
    assert state.basket()[0]["item_kind"] == "word_question"


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{}],
        [{"key": "x"}, {"key": "x"}],
        [None],
        [{"key": "x", "api_key": "never-store"}],
    ],
)
def test_invalid_batch_does_not_create_state(tmp_path, items):
    state = DesktopStateStore(tmp_path)
    with pytest.raises(DesktopStateError):
        state.add_many_to_basket(items)
    assert not state.path.exists()


def test_word_addition_uses_typed_identity_and_preserves_legacy_selections(setup):
    service, state, words, _, _ = setup
    state.save_draft("native-word-question-selection-v1", {"items": ["legacy-marker"]})
    word_key = _add_word(service, words)
    row = state.basket()[0]
    assert row["key"] == word_key and row["item_kind"] == "word_question"
    assert "scope" not in row and "source_bytes" not in row
    assert row["word_selection"]["points"] == 2.5
    assert state.snapshot()["drafts"]["native-word-question-selection-v1"] == {
        "items": ["legacy-marker"]
    }
    assert words.data not in state.path.read_bytes()


def test_projection_keeps_cross_scope_identity_and_complete_core_theme(setup):
    service, state, words, _, _ = setup
    state.add_many_to_basket([_core_item("master"), _core_item("supplemental")])
    _add_word(service, words)
    before = state.path.read_bytes()
    projection = service.projection()
    assert [item["kind"] for item in projection["items"]] == [
        "core_theme",
        "core_theme",
        "word_question",
    ]
    assert projection["items"][0]["key"] != projection["items"][1]["key"]
    assert len(projection["items"][0]["content"]["atomic_chain"]) == 2
    assert (
        projection["items"][2]["content"]["context_blocks"][0]["text"] == "合成公共材料"
    )
    assert state.path.read_bytes() == before


def test_same_core_source_is_merged_at_first_occurrence(setup):
    service, state, _, _, _ = setup
    item = _core_item("master")
    state.add_many_to_basket([item, {**item, "key": "legacy-alternate-key"}])
    assert [row["key"] for row in service.projection()["items"]] == [item["key"]]


def test_legacy_opaque_hash_key_without_new_kind_is_still_verified(setup):
    service, state, _, _, _ = setup
    item = _core_item("master")
    del item["source_identity_sha256"]
    state.add_to_basket(item)
    before = state.path.read_bytes()
    assert (
        service.projection()["items"][0]["source_ref"]["source_identity_sha256"]
        == item["key"]
    )
    assert state.path.read_bytes() == before


def test_same_title_never_rebinds_unverifiable_legacy_source(setup):
    service, state, _, _, _ = setup
    item = _core_item("master")
    del item["source_identity_sha256"]
    item["key"] = "unknown-old-source-identity"
    state.add_to_basket(item)
    state.save_draft("paper-current", {"payload": {"title": "保留旧稿"}})
    before = state.path.read_bytes()
    with pytest.raises(MixedPaperError, match="不能按题名猜测"):
        service.projection()
    assert state.path.read_bytes() == before


def test_real_preview_order_images_score_and_docx_handoff(setup):
    service, state, words, calls, validated = setup
    core = _core_item("master")
    state.add_many_to_basket([core, _core_item("supplemental")])
    word_key = _add_word(service, words)
    state.save_draft(
        "paper-current", {"kind": "paper", "payload": {"title": "旧组卷保留"}}
    )
    request = _request(
        service,
        section_order=[word_key, core["key"]],
        settings_by_key={word_key: {"points": 7.5}},
    )
    preview = service.create_preview(request)
    assert len(validated) == 1 and validated[0][0]["source_bytes"] == words.data
    assert [section["kind"] for section in preview.preview_model["sections"]] == [
        "word_question",
        "core_theme",
    ]
    word, core_preview = preview.preview_model["sections"]
    assert word["points"] == 7.5
    assert "合成Word答案" not in str(word["student_blocks"])
    assert "合成Word答案" in str(word["teacher_blocks"])
    assert "合成公共材料" in str(word["student_blocks"])
    assert "master-A2原题" in str(core_preview["student_blocks"])
    assert "master-A2答案" in str(core_preview["teacher_blocks"])
    assert "本次练习分值" not in str(core_preview["student_blocks"])
    image = next(block for block in word["student_blocks"] if block["kind"] == "image")
    assert service.image(preview.preview_id, image["image_id"])["data"] == words.image
    with pytest.raises(MixedPaperError, match="先确认"):
        service.export(preview.preview_id, preview.preview_hash)
    preview = paginate_and_read(service, preview)
    service.approve(preview.preview_id, preview.preview_hash)
    exported = service.export(preview.preview_id, preview.preview_hash)
    assert exported["pdf_status"] == "generated"
    assert {item["artifact_id"] for item in exported["artifacts"]} == {
        "student_docx",
        "teacher_docx",
        "student_pdf",
        "teacher_pdf",
    }
    assert [item["kind"] for item in calls[0][1]] == ["word_question", "core_plan"]
    assert calls[0][1][0]["question"]["source_bytes"] == words.data
    assert calls[0][1][0]["question"]["points"] == 7.5
    assert (
        state.snapshot()["drafts"]["paper-current"]["payload"]["title"] == "旧组卷保留"
    )
    assert words.data not in state.path.read_bytes()


@pytest.mark.parametrize(
    "changes",
    [
        {"basket_sha256": "0" * 64},
        {"section_order": []},
        {"section_order": ["not-in-basket"]},
        {"settings_by_key": {"unselected": {"points": 4}}},
        {"duration_minutes": True},
        {"show_question_scores": "false"},
        {"title": ""},
        {"extra_source_bytes": "forbidden"},
    ],
)
def test_invalid_preview_is_rejected_before_writing(setup, changes):
    service, state, words, _, _ = setup
    _add_word(service, words)
    request = _request(service, **changes)
    before = state.path.read_bytes()
    with pytest.raises(MixedPaperError):
        service.create_preview(request)
    assert state.path.read_bytes() == before
    assert not service.root.exists()


@pytest.mark.parametrize("points", [True, 0, -1, 101, float("nan"), float("inf")])
def test_invalid_word_points_fail_before_preview(setup, points):
    service, _, words, _, _ = setup
    key = _add_word(service, words)
    with pytest.raises(MixedPaperError, match="分值"):
        service.create_preview(
            _request(service, settings_by_key={key: {"points": points}})
        )


@pytest.mark.parametrize(
    "mutation", ["basket", "word_revision", "word_text", "image", "snapshot"]
)
def test_approved_snapshot_cannot_export_changed_sources(setup, mutation):
    service, state, words, calls, _ = setup
    _add_word(service, words)
    preview = service.create_preview(_request(service))
    preview = paginate_and_read(service, preview)
    service.approve(preview.preview_id, preview.preview_hash)
    folder, record, snapshot = service._load(preview.preview_id)
    if mutation == "basket":
        state.add_many_to_basket([_core_item("master")])
    elif mutation == "word_revision":
        words.row["revision"] = "changed"
    elif mutation == "word_text":
        words.row["question_blocks"][0]["text"] = "changed synthetic stem"
    elif mutation == "image":
        (folder / next(iter(snapshot["images"].values()))["path"]).write_bytes(
            b"changed"
        )
    else:
        (folder / record["snapshot_file"]).write_bytes(b"{}")
    with pytest.raises(MixedPaperError):
        service.export(preview.preview_id, preview.preview_hash)
    assert len(calls) == 1  # Built once before pagination, never rebuilt at export.


def test_unreadable_original_picture_is_explicit_and_blocks_confirmation(setup):
    service, _, words, calls, _ = setup
    _add_word(service, words)

    def unavailable(*args, **kwargs):
        raise MixedPaperError("合成不支持的原图格式")

    words.reader.word_asset_bytes = unavailable
    preview = service.create_preview(_request(service))
    assert preview.blockers and "原图暂不能预览" in "".join(preview.blockers)
    with pytest.raises(MixedPaperError, match="缺口"):
        service.approve(preview.preview_id, preview.preview_hash)
    assert calls == []


def test_not_ready_word_blocks_whole_preview_before_student_content_is_built(setup):
    service, _, words, calls, validated = setup
    _add_word(service, words)
    words.row["export_ready"] = False
    with pytest.raises(MixedPaperError, match="Word 范围页"):
        service.create_preview(_request(service))
    assert not service.root.exists() and calls == [] and validated == []


def test_unrecognized_answer_does_not_claim_original_source_has_no_answer(setup):
    service, _, words, _, _ = setup
    _add_word(service, words)
    words.row["answer_blocks"] = []
    preview = service.create_preview(_request(service))
    text = str(preview.preview_model["sections"][0]["teacher_blocks"])
    assert "当前选定范围未识别到本题答案" in text
    assert "这不表示原文没有答案" in text
    assert "原资料未提供" not in text


def test_cross_question_word_answer_validation_precedes_preview_write(setup):
    service, _, words, _, _ = setup
    _add_word(service, words)

    def invalid(rows):
        raise MixedPaperError("synthetic cross-question answer overlap")

    service._word_validator = invalid
    with pytest.raises(MixedPaperError, match="overlap"):
        service.create_preview(_request(service))
    assert not service.root.exists()


def test_preview_image_handle_cannot_read_another_file(setup):
    service, _, words, _, _ = setup
    _add_word(service, words)
    preview = service.create_preview(_request(service))
    with pytest.raises(MixedPaperError):
        service.image(preview.preview_id, "../../other")
    with pytest.raises(MixedPaperError):
        service.image("mixed-preview-../../other", "image-any")


def test_new_preview_invalidates_older_approval(setup):
    service, _, words, calls, _ = setup
    _add_word(service, words)
    first = service.create_preview(_request(service))
    first = paginate_and_read(service, first)
    service.approve(first.preview_id, first.preview_hash)
    service.create_preview(_request(service))
    with pytest.raises(MixedPaperError):
        service.export(first.preview_id, first.preview_hash)
    assert len(calls) == 1


def test_facade_new_methods_delegate_without_constructing_provider(setup, monkeypatch):
    service, state, words, _, _ = setup
    facade = object.__new__(DesktopWorkbenchFacade)
    facade._state = state
    facade._reader_stop_event = Event()
    calls = []

    def dispatch(self, method, *args):
        calls.append(method)
        return getattr(service, method)(*args)

    monkeypatch.setattr(DesktopWorkbenchFacade, "_mixed_paper_call", dispatch)
    facade.add_word_questions_to_basket(
        [{key: words.row[key] for key in ("key", "revision", "points")}]
    )
    assert facade.paper_basket_projection()["items"]
    preview = facade.create_paper_preview(_request(service))
    image = next(
        block
        for block in preview.preview_model["sections"][0]["student_blocks"]
        if block["kind"] == "image"
    )
    facade.paper_preview_image(preview.preview_id, image["image_id"])
    service._pagination_builder = synthetic_pagination
    preview = facade.prepare_mixed_paper_pagination(preview.preview_id, preview.preview_hash)
    for document in preview.preview_model["pagination"]["documents"].values():
        for page in document["pages"]:
            facade.paper_preview_image(preview.preview_id, page["image_id"])
    facade.approve_paper_preview(preview.preview_id, preview.preview_hash)
    facade.export_paper_preview(
        preview.preview_id, preview.preview_hash, {"ignored": "untrusted"}
    )
    assert calls == [
        "add_word_questions",
        "projection",
        "create_preview",
        "image",
        "prepare_pagination",
        "image",
        "image",
        "approve",
        "export",
    ]


def test_full_native_word_core_bundle_preview_image_approve_and_docx_export(
    desktop_paths, tmp_path, monkeypatch
):
    from test_desktop_visual_import_facade import _png
    from test_word_question_images import _fixture

    from integrations.deeptutor_shchem_v1.candidate_review import CandidateCropPayload
    from staging.coordination.deeptutor_gateway.tests import (
        test_paper_export_workbench_api as core,
    )

    facade, choices, provider = _fixture(desktop_paths, tmp_path, answer_image=True)
    catalog = core._catalog()
    details = core._details()
    image_bytes = {"A1": _png("blue"), "A2": _png("red")}

    def loader(scope):
        value = deepcopy(catalog)
        value["scope"] = scope
        value["data_snapshot_id"] = _digest(scope)
        return value

    def crop(scope, node, crop_id):
        assert scope in {"master", "supplemental"}
        assert crop_id == f"CROP-{node}"
        data = image_bytes[node]
        return CandidateCropPayload(data=data, sha256=_sha(data))

    monkeypatch.setattr(facade, "paper_theme_catalog", loader)
    monkeypatch.setattr(
        MixedPaperService,
        "_core_context",
        lambda self: {
            "theme_catalog_loader": loader,
            "detail_loader": lambda scope, node: deepcopy(details[node]),
            "crop_loader": crop,
        },
    )
    for scope in ("master", "supplemental"):
        identity = _digest({"scope": scope, "paper": "PAPER-1", "theme": "T1"})
        facade.state_store.add_to_basket(
            {
                "key": identity,
                "scope": scope,
                "source_identity_sha256": identity,
                "data_snapshot_id": _digest(scope),
            }
        )
    facade.add_word_questions_to_basket(choices)
    service = MixedPaperService(facade)
    projection = facade.paper_basket_projection()
    word_key = projection["items"][-1]["key"]
    order = [projection["items"][0]["key"], word_key, projection["items"][1]["key"]]
    request = _request(
        service, section_order=order, settings_by_key={word_key: {"points": 6.5}}
    )
    original_sources = {path: path.read_bytes() for path in tmp_path.rglob("*.docx")}
    preview = facade.create_paper_preview(request)
    assert [section["kind"] for section in preview.preview_model["sections"]] == [
        "core_theme",
        "word_question",
        "core_theme",
    ]
    blocks = [
        block
        for section in preview.preview_model["sections"]
        for group in ("student_blocks", "teacher_blocks")
        for block in section[group]
    ]
    for block in blocks:
        if block["kind"] == "image":
            image = facade.paper_preview_image(preview.preview_id, block["image_id"])
            assert _sha(image["data"]) == image["sha256"]
    preview = paginate_and_read(service, preview)
    facade.approve_paper_preview(preview.preview_id, preview.preview_hash)
    exported = facade.export_paper_preview(preview.preview_id, preview.preview_hash)
    assert exported["pdf_status"] == "generated"
    assert {row["artifact_id"] for row in exported["artifacts"]} == {
        "student_docx",
        "teacher_docx",
        "student_pdf",
        "teacher_pdf",
    }
    for artifact in exported["artifacts"]:
        path = Path(artifact["path"])
        data = path.read_bytes()
        assert _sha(data) == artifact["sha256"]
        if artifact["artifact_id"].endswith("_pdf"):
            from pypdf import PdfReader

            assert len(PdfReader(BytesIO(data)).pages) == 1
            continue
        with ZipFile(BytesIO(data)) as package:
            document = etree.fromstring(package.read("word/document.xml"))
            text = "".join(document.xpath("//*[local-name()='t']/text()"))
            pictures = [
                package.read(name)
                for name in package.namelist()
                if name.startswith("word/media/")
            ]
            assert all(raw in pictures for raw in image_bytes.values())
            assert document.xpath("//*[local-name()='drawing']")
            # wp:docPr IDs are document-wide drawing identities. pic:cNvPr
            # is local to each picture subtree (python-docx uses id=0 there).
            ids = document.xpath("//*[local-name()='docPr']/@id")
            assert len(ids) == len(set(ids))
            if artifact["artifact_id"] == "student_docx":
                assert core.ANSWER_A1 not in text and "【答案】参考答案" not in text
                assert "6.5" not in text
            else:
                assert core.ANSWER_A1 in text and core.ANSWER_A2 in text
                assert "参考答案" in text and "6.5" in text
    assert all(path.read_bytes() == raw for path, raw in original_sources.items())
    assert provider.borrow_calls == 0
    folder, _, snapshot = service._load(preview.preview_id)
    core_section = next(
        row for row in snapshot["sections"] if row["kind"] == "core_plan"
    )
    original_asset = (
        folder / core_section["asset_root"] / next(iter(core_section["asset_files"]))
    )
    original_asset.write_bytes(b"tampered original plan asset, not its preview copy")
    with pytest.raises(MixedPaperError, match="资源"):
        service.export(preview.preview_id, preview.preview_hash)
    facade.shutdown()

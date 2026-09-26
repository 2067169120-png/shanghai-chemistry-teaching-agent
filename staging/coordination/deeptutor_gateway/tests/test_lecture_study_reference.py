"""Source-bound lesson guidance; every source and state store is temporary."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from test_word_question_attributes import metadata, question
from test_word_source_reference import _image_root, _import, _png, _reference
from test_word_source_reference import (
    desktop_paths as desktop_paths,  # noqa: PLC0414 -- pytest fixture re-export
)

from integrations.deeptutor_shchem_v1.desktop_lecture_library import INDEX_FILES
from integrations.deeptutor_shchem_v1.desktop_lecture_study import (
    lecture_study_reference,
    question_teaching_tags,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
    PreparationImageStore,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    CONCEPTS,
    PreparationSourceError,
    PreparationSourcesService,
    _digest,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    WordQuestionAttributeError,
    _seal,
    apply_teacher_edits,
    suggest_attributes,
)
from integrations.deeptutor_shchem_v1.desktop_word_source_reference import (
    WordSourceReferenceError,
    WordSourceReferenceService,
)


def _preview():
    return {
        "source_sha256": "a" * 64,
        "source_name": "合成原教案.docx",
        "revision": "b" * 64,
    }


def _save_card(path, card):
    path.write_text(json.dumps(card, ensure_ascii=False) + "\n", encoding="utf-8")


def _index(workspace, preview):
    folder = workspace / "knowledge" / "lectures"
    folder.mkdir(parents=True, exist_ok=True)
    card = {
        "id": "LECT-SYNTHETIC-1",
        "source_sha256": preview["source_sha256"],
        "source_name": preview["source_name"],
        "source_preview_revision": preview["revision"],
        "title": "合成备课研读索引",
        "human_reviewed": False,
        "review_status": "ai_distilled_pending_teacher_review",
        "knowledge": [
            {"summary": "合成知识：两段原文共同支持。", "block_indices": [2, 3]},
            {"summary": "合成跨段知识：不能仅选前三段。", "block_indices": [3, 4]},
        ],
        "methods": [{"summary": "合成方法：保留解题条件。", "block_indices": [3]}],
        "pitfalls": [{"summary": "合成易错：须核对原图条件。", "block_indices": [5]}],
    }
    for filename in INDEX_FILES:
        (folder / filename).write_text("", encoding="utf-8")
    path = folder / INDEX_FILES[0]
    _save_card(path, card)
    return path, card


def _pdf_bytes():
    """A small three-page PDF, only hash-read by this application contract."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>",
        *[
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Resources << >> >>"
            for _ in range(3)
        ],
    ]
    result = b"%PDF-1.4\n"
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n"
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    result += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return result


def _textbook(workspace):
    book = workspace / "books" / "synthetic-textbook.pdf"
    book.parent.mkdir(parents=True, exist_ok=True)
    book.write_bytes(_pdf_bytes())
    row = {
        "concept_id": "SYNTHETIC-CONCEPT-1",
        "title": "合成教材知识标题",
        "statement": "合成教材知识候选：条件必须完整。",
        "volume_id": "TB-M1",
        "source_path": "books/synthetic-textbook.pdf",
        "source_sha256": hashlib.sha256(book.read_bytes()).hexdigest(),
        "pdf_pages": [1, 2],
        "candidate_only": True,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "generation_allowed": False,
        "publication_allowed": False,
        "verification_caveat": "合成待教师核对记录，不是教材原句或正式授权。",
    }
    catalog = workspace / CONCEPTS
    catalog.parent.mkdir(parents=True, exist_ok=True)
    _save_concepts(catalog, [row])
    link = {
        "volume_id": row["volume_id"],
        "section_key": "TB-M1-C1:1.1",
        "source_sha256": row["source_sha256"],
        "pdf_pages": [1, 2],
        "printed_pages": [11, 12],
        "lecture_block_indices": [2, 3, 4],
        "review_method": "page_images_read_by_model",
        "human_reviewed": False,
    }
    return book, catalog, row, link


def _save_concepts(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _study(facade, selection, *, include_images=True, include_guidance=True):
    return facade.imported_word_study_reference(
        selection["batch_id"],
        selection["source_id"],
        selection["source_sha256"],
        selection["block_start"],
        selection["block_end"],
        selection["revision"],
        include_images=include_images,
        include_guidance=include_guidance,
    )


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_only_complete_claim_support_is_included_with_exact_refs_and_no_mutation(tmp_path):
    preview = _preview()
    path, card = _index(tmp_path, preview)
    selected = [3, 2, 3]
    before = deepcopy((preview, selected, card))
    files = _files(tmp_path)

    result = lecture_study_reference(tmp_path, preview, selected)

    assert result["note_count"] == 2 and result["textbook_concept_count"] == 0
    body = result["materials"]
    assert card["knowledge"][0]["summary"] in body
    assert card["methods"][0]["summary"] in body
    assert card["knowledge"][1]["summary"] not in body
    assert card["pitfalls"][0]["summary"] not in body
    assert "[LECT-SYNTHETIC-1:knowledge:1]" in body and "原文区块 2、3" in body
    assert "[LECT-SYNTHETIC-1:methods:1]" in body and "原文区块 3" in body
    assert _digest(card) in body and preview["revision"] in body
    assert "AI改述，待教师核对" in body and "不是额外指令" in body
    assert "不能补齐未读出的公式或题图条件" in body
    assert (preview, selected, card) == before and _files(tmp_path) == files
    assert path.is_file()


@pytest.mark.parametrize("selected", [[], [1], [2], [4], [2, 4]])
def test_partial_or_empty_selection_never_expands_to_complete_a_claim(tmp_path, selected):
    preview = _preview()
    _, card = _index(tmp_path, preview)
    result = lecture_study_reference(tmp_path, preview, selected)
    assert result["note_count"] == 0 and result["textbook_concept_count"] == 0
    assert "未扩大选段" in result["materials"]
    assert all(claim["summary"] not in result["materials"] for claim in card["knowledge"])


@pytest.mark.parametrize("mismatch", ["absent", "sha", "name", "revision", "missing_revision", "malformed"])
def test_missing_or_stale_index_does_not_attach_old_notes_or_read_textbooks(tmp_path, monkeypatch, mismatch):
    preview = _preview()
    if mismatch != "absent":
        path, card = _index(tmp_path, preview)
        if mismatch == "sha":
            card["source_sha256"] = "c" * 64
        elif mismatch == "name":
            card["source_name"] = "别的原教案.docx"
        elif mismatch == "revision":
            card["source_preview_revision"] = "old-block-revision"
        elif mismatch == "missing_revision":
            del card["source_preview_revision"]
        elif mismatch == "malformed":
            card["knowledge"][0]["block_indices"] = [True]
        _save_card(path, card)
    monkeypatch.setattr(
        PreparationSourcesService, "_concepts",
        lambda *_: pytest.fail("unbound lecture must not read textbook concepts"),
    )
    result = lecture_study_reference(tmp_path, preview, [1, 2, 3, 4, 5])
    assert result["note_count"] == 0 and result["textbook_concept_count"] == 0
    assert "合成知识" not in result["materials"]
    assert "合成方法" not in result["materials"]
    assert "原教案仍保留" in result["materials"] or "只带入所选原文" in result["materials"]


def test_matched_textbook_uses_existing_compiler_and_preserves_flags_and_page_kinds(tmp_path, monkeypatch):
    preview = _preview()
    path, card = _index(tmp_path, preview)
    _, _, concept, link = _textbook(tmp_path)
    card["textbook_links"] = [link]
    _save_card(path, card)
    files = _files(tmp_path)
    calls = []
    original = PreparationSourcesService.reference

    def compiled(reader, word_path, sha, start, end, concepts, **kwargs):
        calls.append((word_path, sha, start, end, deepcopy(concepts), kwargs))
        return original(reader, word_path, sha, start, end, concepts, **kwargs)

    monkeypatch.setattr(PreparationSourcesService, "reference", compiled)
    result = lecture_study_reference(tmp_path, preview, [2, 3])
    assert calls == [(None, None, 1, 1, [{"concept_id": concept["concept_id"], "revision": _digest(concept)}], {})]
    assert result["textbook_concept_count"] == 1
    body = result["materials"]
    assert concept["statement"] in body and concept["source_sha256"] in body
    assert _digest(concept) in body and concept["verification_caveat"] in body
    assert "PDF文件页序 1、2" in body and "印刷页码 11、12" in body
    assert "未自动附教材原页或教材原句" in body
    flags = json.loads(next(line.split("：", 1)[1] for line in body.splitlines() if line.startswith("原记录状态（不提升权限）：")))
    assert flags == {key: concept[key] for key in ("candidate_only", "human_reviewed", "teaching_use_allowed", "generation_allowed", "publication_allowed")}
    assert str(tmp_path) not in body and _files(tmp_path) == files


@pytest.mark.parametrize("changes", [
    {"volume_id": "TB-M2"}, {"source_sha256": "f" * 64},
    {"pdf_pages": [2, 3]}, {"pdf_pages": []}, {"pdf_pages": [True]},
    {"pdf_pages": [0]}, {"pdf_pages": [1.0]}, {"pdf_pages": "1,2"},
])
def test_concept_requires_same_volume_sha_and_entire_valid_page_range(tmp_path, monkeypatch, changes):
    preview = _preview()
    path, card = _index(tmp_path, preview)
    _, catalog, concept, link = _textbook(tmp_path)
    _save_concepts(catalog, [{**concept, **changes}])
    card["textbook_links"] = [link]
    _save_card(path, card)
    monkeypatch.setattr(
        PreparationSourcesService, "reference",
        lambda *_a, **_kw: pytest.fail("unmatched concept must not be compiled"),
    )
    result = lecture_study_reference(tmp_path, preview, [2, 3])
    assert result["note_count"] == 2 and result["textbook_concept_count"] == 0
    assert concept["statement"] not in result["materials"]
    assert "未扩大到其他教材页面" in result["materials"]


@pytest.mark.parametrize("support", [None, [2], [4], [5]])
def test_textbook_link_needs_an_entire_selected_claim_in_explicit_word_support(tmp_path, monkeypatch, support):
    preview = _preview()
    path, card = _index(tmp_path, preview)
    _, _, concept, link = _textbook(tmp_path)
    if support is None:
        del link["lecture_block_indices"]
    else:
        link["lecture_block_indices"] = support
    card["textbook_links"] = [link]
    _save_card(path, card)
    monkeypatch.setattr(
        PreparationSourcesService, "_concepts",
        lambda *_: pytest.fail("unrelated link must not read textbook concepts"),
    )
    result = lecture_study_reference(tmp_path, preview, [2, 3])
    assert result["note_count"] == 2 and result["textbook_concept_count"] == 0
    assert card["knowledge"][0]["summary"] in result["materials"]
    assert concept["statement"] not in result["materials"]


@pytest.mark.parametrize("change", ["missing_pdf", "changed_pdf", "missing_catalog", "broken_catalog"])
def test_missing_or_changed_textbook_cannot_be_replaced_by_lecture_summary(tmp_path, change):
    preview = _preview()
    path, card = _index(tmp_path, preview)
    book, catalog, _, link = _textbook(tmp_path)
    card["textbook_links"] = [link]
    _save_card(path, card)
    if change == "missing_pdf":
        book.unlink()
    elif change == "changed_pdf":
        book.write_bytes(_pdf_bytes() + b"% changed synthetic original\n")
    elif change == "missing_catalog":
        catalog.unlink()
    else:
        catalog.write_text("not-json", encoding="utf-8")
    with pytest.raises(PreparationSourceError, match="教材"):
        lecture_study_reference(tmp_path, preview, [2, 3])


def test_guidance_preserves_exact_original_blocks_tables_and_image_binding_without_writes(desktop_paths, tmp_path):
    facade, selected, preview, path, provider = _import(desktop_paths, tmp_path)
    index_path, card = _index(facade.paths.workspace_root, preview)
    before = deepcopy((selected, preview))
    original = path.read_bytes()
    state = facade.state_store.snapshot()
    legacy = _reference(facade, selected)
    ref = _study(facade, selected)
    assert ref["include_guidance"] is True
    assert ref["lecture_study"]["note_count"] == 4
    for key in legacy.keys() - {"materials"}:
        assert ref[key] == legacy[key]
    assert ref["materials"].startswith(legacy["materials"])
    assert ref["lecture_study"]["materials"] in ref["materials"]
    for block in preview["blocks"]:
        assert block["text"] in ref["materials"]
        assert f"[Word区块{block['index']}]" in ref["materials"]
    assert "第1行·第1—2列；横向合并" in ref["materials"]
    assert ref["materials"].index("原教案的最后一段不应丢失。") < ref["materials"].index("【讲义研读参考")
    assert len(ref["image_assets"]) == 2 and len(ref["image_references"]) == 3
    assert not _image_root(facade).exists()
    assert path.read_bytes() == original and (selected, preview) == before
    assert facade.state_store.snapshot() == state
    assert json.loads(index_path.read_text(encoding="utf-8")) == card
    assert provider.borrow_calls == 0


def test_guidance_selected_range_does_not_add_other_blocks_or_images(desktop_paths, tmp_path):
    facade, selected, preview, _, _ = _import(desktop_paths, tmp_path)
    _, card = _index(facade.paths.workspace_root, preview)
    selected.update(block_start=2, block_end=3)
    ref = _study(facade, selected)
    assert ref["source_selection"] == selected and ref["lecture_study"]["note_count"] == 2
    assert ref["image_assets"] == [] and ref["image_references"] == []
    assert "[Word区块2]" in ref["materials"] and "[Word区块3]" in ref["materials"]
    assert "[Word区块1]" not in ref["materials"] and "[Word区块4]" not in ref["materials"]
    assert "原教案的最后一段" not in ref["materials"]
    assert card["knowledge"][1]["summary"] not in ref["materials"]
    assert card["pitfalls"][0]["summary"] not in ref["materials"]


def test_confirm_recompiles_guidance_and_saves_original_pixels_only_after_confirmation(desktop_paths, tmp_path, monkeypatch):
    facade, selected, preview, source, provider = _import(desktop_paths, tmp_path)
    _index(facade.paths.workspace_root, preview)
    ref = _study(facade, selected)
    frozen = deepcopy(ref)
    original = source.read_bytes()
    calls = []
    compile_reference = WordSourceReferenceService._compile

    def counted(service, *args, **kwargs):
        calls.append(deepcopy(kwargs))
        return compile_reference(service, *args, **kwargs)

    monkeypatch.setattr(WordSourceReferenceService, "_compile", counted)
    result = facade.import_word_source_reference(ref, [])
    assert len(calls) == 1 and calls[0]["include_guidance"] is True
    assert result["materials"] == frozen["materials"]
    assert result["image_assets"] == frozen["image_assets"]
    assert ref == frozen and source.read_bytes() == original
    store = PreparationImageStore(_image_root(facade))
    assert store.load(result["image_assets"][0]) == _png((0, 0, 0))
    assert len(list(store.root.glob("*.image"))) == 2
    assert provider.borrow_calls == 0


@pytest.mark.parametrize("change", ["materials", "note_count", "concept_count", "guidance_text", "guidance_flag", "remove_guidance"])
def test_confirmation_rejects_modified_guidance_before_saving_images(desktop_paths, tmp_path, change):
    facade, selected, preview, _, _ = _import(desktop_paths, tmp_path)
    _index(facade.paths.workspace_root, preview)
    ref = _study(facade, selected)
    if change == "materials":
        ref["materials"] += "\n未预览内容"
    elif change == "note_count":
        ref["lecture_study"]["note_count"] += 1
    elif change == "concept_count":
        ref["lecture_study"]["textbook_concept_count"] += 1
    elif change == "guidance_text":
        ref["lecture_study"]["materials"] = "替换蒸馏"
    elif change == "guidance_flag":
        ref["include_guidance"] = False
    else:
        del ref["lecture_study"]
    with pytest.raises(WordSourceReferenceError):
        facade.import_word_source_reference(ref, [])
    assert not _image_root(facade).exists()


@pytest.mark.parametrize("change", ["summary", "refs", "revision", "delete", "added_index"])
def test_index_change_after_preview_requires_new_preview(desktop_paths, tmp_path, change):
    facade, selected, preview, _, _ = _import(desktop_paths, tmp_path)
    path, card = _index(facade.paths.workspace_root, preview)
    if change == "added_index":
        path.write_text("", encoding="utf-8")
    ref = _study(facade, selected)
    if change == "summary":
        card["knowledge"][0]["summary"] += "修订"
        _save_card(path, card)
    elif change == "refs":
        card["knowledge"][0]["block_indices"] = [2]
        _save_card(path, card)
    elif change == "revision":
        card["source_preview_revision"] = "old-revision"
        _save_card(path, card)
    elif change == "delete":
        path.unlink()
    else:
        _save_card(path, card)
    with pytest.raises(WordSourceReferenceError):
        facade.import_word_source_reference(ref, [])
    assert not _image_root(facade).exists()


@pytest.mark.parametrize("change", ["pdf_missing", "pdf_changed", "statement", "flags", "pages"])
def test_textbook_change_after_preview_cannot_silently_confirm_old_guidance(desktop_paths, tmp_path, change):
    facade, selected, preview, _, _ = _import(desktop_paths, tmp_path)
    path, card = _index(facade.paths.workspace_root, preview)
    book, catalog, concept, link = _textbook(facade.paths.workspace_root)
    card["textbook_links"] = [link]
    _save_card(path, card)
    ref = _study(facade, selected)
    assert ref["lecture_study"]["textbook_concept_count"] == 1
    if change == "pdf_missing":
        book.unlink()
    elif change == "pdf_changed":
        book.write_bytes(_pdf_bytes() + b"% changed original\n")
    else:
        if change == "statement":
            concept["statement"] += "修订"
        elif change == "flags":
            concept["human_reviewed"] = True
        else:
            concept["pdf_pages"] = [2, 3]
        _save_concepts(catalog, [concept])
    with pytest.raises(WordSourceReferenceError):
        facade.import_word_source_reference(ref, [])
    assert not _image_root(facade).exists()


def test_guidance_cannot_make_unread_object_importable_as_complete_picture(desktop_paths, tmp_path):
    facade, selected, preview, _, _ = _import(desktop_paths, tmp_path, missing=True)
    path, card = _index(facade.paths.workspace_root, preview)
    missing_block = next(block["index"] for block in preview["blocks"] if "未支持对象" in block["text"])
    card["knowledge"].append({"summary": "合成摘要声称可参考图中条件，但不是原图读取。", "block_indices": [missing_block]})
    _save_card(path, card)
    ref = _study(facade, selected)
    assert card["knowledge"][-1]["summary"] in ref["materials"]
    assert ref["image_issues"]
    assert "不能补齐未读出的公式或题图条件" in ref["materials"]
    with pytest.raises(WordSourceReferenceError, match="未能全部带入"):
        facade.import_word_source_reference(ref, [])
    assert not _image_root(facade).exists()


def test_explicit_text_only_keeps_unread_image_warning_and_never_decodes_images(desktop_paths, tmp_path, monkeypatch):
    facade, selected, preview, _, _ = _import(desktop_paths, tmp_path)
    _index(facade.paths.workspace_root, preview)
    monkeypatch.setattr(
        PreparationSourcesService, "word_asset_bytes",
        lambda *_a, **_kw: pytest.fail("explicit text-only must not decode images"),
    )
    ref = _study(facade, selected, include_images=False)
    assert ref["lecture_study"]["note_count"] == 4
    assert ref["image_assets"] == [] and ref["image_issues"] == []
    assert all(item["status"] == "not_included_text_only" for item in ref["image_references"])
    assert "本次已明确选择仅文字" in ref["materials"]
    assert "不能补齐未读出的公式或题图条件" in ref["materials"]
    assert facade.import_word_source_reference(ref, [])["image_assets"] == []


def test_legacy_default_and_explicit_no_guidance_do_not_read_lecture_index(desktop_paths, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_lecture_study as module

    facade, selected, _, _, _ = _import(desktop_paths, tmp_path)
    monkeypatch.setattr(module, "_index_cards", lambda *_: pytest.fail("legacy must not read guidance"))
    legacy = _reference(facade, selected)
    default = WordSourceReferenceService(facade).reference(selected)
    explicit = _study(facade, selected, include_guidance=False)
    assert explicit == default == legacy
    assert "lecture_study" not in legacy and "include_guidance" not in legacy
    assert facade.import_word_source_reference(legacy, [])["materials"] == legacy["materials"]


@pytest.mark.parametrize("value", [None, 0, 1, "yes", []])
def test_guidance_choice_requires_explicit_boolean_without_source_or_image_writes(desktop_paths, tmp_path, monkeypatch, value):
    facade, selected, _, _, _ = _import(desktop_paths, tmp_path)
    monkeypatch.setattr(facade, "_imported_word_source", lambda *_: pytest.fail("reject invalid option before reading source"))
    with pytest.raises(WordSourceReferenceError):
        _study(facade, selected, include_guidance=value)
    assert not _image_root(facade).exists()


def test_current_question_tags_preserve_values_statuses_and_unknown_exam_facts():
    item = question("配平氧化还原反应的方程式。")
    item["attributes"] = suggest_attributes(item, metadata())
    before = deepcopy(item)
    rendered = question_teaching_tags(item)
    row = item["attributes"]
    assert row["revision"] in rendered
    assert f"主考点：{row['primary_knowledge']['label']}（自动建议）" in rendered
    assert "适用年级：高三（使用定位）" in rendered
    lines = dict(line.split("：", 1) for line in rendered.splitlines() if "：" in line)
    assert all(lines[title].endswith("（未知）") for title in ("原考试类型", "原年份", "原地区", "原题年级"))
    assert "2026" not in rendered and "上海" not in rendered
    assert all(row["original_source"][field]["value"] == "unknown" for field in ("grade", "exam_type", "year", "region"))
    assert "适用年级是使用定位，不是原题年级" in rendered
    assert "自动建议不等于教师确认" in rendered
    assert "标签不能替代题面、共同材料或答案" in rendered
    assert item == before


def test_teacher_tag_status_and_note_remain_teacher_record_not_re_suggested():
    item = question("根据原文条件作答。")
    proposed = suggest_attributes(item, metadata())
    item["attributes"] = apply_teacher_edits(proposed, {"teacher_note": "合成教师备注：保持原有主考点。"})
    before = deepcopy(item)
    rendered = question_teaching_tags(item)
    row = item["attributes"]
    assert row["annotation_source"] == "teacher_modified"
    assert row["teacher_note"] in rendered and row["revision"] in rendered
    assert f"主考点：{row['primary_knowledge']['label']}（未知）" in rendered
    assert row["primary_knowledge"] == proposed["primary_knowledge"]
    assert item == before


@pytest.mark.parametrize("binding", ["source_sha256", "revision"])
def test_stale_question_tags_are_not_presented_as_current(binding):
    item = question("配平氧化还原反应。")
    item["attributes"] = suggest_attributes(item, metadata())
    item[binding] = "c" * 64
    rendered = question_teaching_tags(item)
    assert "未采用旧标签" in rendered and "K11" not in rendered


def test_missing_question_tags_do_not_guess_source_from_containing_lecture():
    item = question("根据材料作答。", chapter="2026年上海等级考原题")
    assert "不由题目所在讲义推断原考试出处" in question_teaching_tags(item)


def test_tampered_attribute_digest_is_rejected_instead_of_relabeling():
    item = question()
    item["attributes"] = suggest_attributes(item, metadata())
    item["attributes"]["teacher_note"] = "unsealed change"
    with pytest.raises(WordQuestionAttributeError):
        question_teaching_tags(item)
    item["attributes"] = _seal(item["attributes"])
    assert "unsealed change" in question_teaching_tags(item)

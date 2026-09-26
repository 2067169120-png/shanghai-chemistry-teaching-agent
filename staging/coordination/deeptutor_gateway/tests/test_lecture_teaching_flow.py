from __future__ import annotations

import json

import pytest
from test_lecture_library import fixture
from test_lecture_study_reference import _index, _preview, _save_card

from integrations.deeptutor_shchem_v1.desktop_lecture_library import (
    GROUP_LABELS,
    INDEX_FILES,
    lecture_catalog,
    search_lectures,
)
from integrations.deeptutor_shchem_v1.desktop_lecture_study import (
    lecture_study_reference,
)


def _save_library_card(folder, card):
    (folder / INDEX_FILES[0]).write_text(
        json.dumps(card, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def test_teaching_flow_group_label_is_public_and_old_cards_remain_indexable(tmp_path):
    assert GROUP_LABELS["teaching_flow"] == "讲练与笔记"
    facade, _, _ = fixture(tmp_path)

    result = lecture_catalog(facade)

    indexed = next(row for row in result["items"] if row["source_name"] == "电离教案.docx")
    assert indexed["indexed"] is True
    assert "讲练与笔记" not in indexed["preview"]


def test_teaching_flow_is_visible_in_preview_and_search(tmp_path):
    facade, folder, card = fixture(tmp_path)
    card["teaching_flow"] = [
        {"summary": "讲解后安排练习，并保留课堂笔记", "block_indices": [4]}
    ]
    _save_library_card(folder, card)

    result = lecture_catalog(facade)
    indexed = next(row for row in result["items"] if row["source_name"] == "电离教案.docx")

    assert indexed["indexed"] is True
    assert "讲练与笔记" in indexed["preview"]
    assert "讲解后安排练习，并保留课堂笔记" in indexed["preview"]
    assert search_lectures(result["items"], "讲练 笔记") == [indexed]


@pytest.mark.parametrize("missing_group", ["knowledge", "methods", "pitfalls"])
def test_existing_lecture_groups_remain_required(tmp_path, missing_group):
    facade, folder, card = fixture(tmp_path)
    del card[missing_group]
    card["teaching_flow"] = []
    _save_library_card(folder, card)

    result = lecture_catalog(facade)

    assert len(result["items"]) == 2
    assert not any(row["indexed"] for row in result["items"])
    assert len(result["warnings"]) == 1


@pytest.mark.parametrize(
    "teaching_flow",
    [
        None,
        {},
        "讲练",
        [{"summary": "", "block_indices": [2]}],
        [{"summary": "讲练", "block_indices": []}],
        [{"summary": "讲练", "block_indices": [True]}],
        [{"summary": "讲练", "block_indices": [0]}],
    ],
)
def test_malformed_teaching_flow_invalidates_only_index_file_and_keeps_originals(
    tmp_path, teaching_flow
):
    facade, folder, card = fixture(tmp_path)
    card["teaching_flow"] = teaching_flow
    _save_library_card(folder, card)

    result = lecture_catalog(facade)

    assert len(result["items"]) == 2
    assert len(result["warnings"]) == 1
    assert not any(row["indexed"] for row in result["items"])


def test_teaching_flow_reference_requires_complete_selected_support_and_counts_notes(
    tmp_path,
):
    preview = _preview()
    path, card = _index(tmp_path, preview)
    card["teaching_flow"] = [
        {"summary": "完整覆盖时纳入讲练笔记", "block_indices": [2, 3]},
        {"summary": "跨越未选区块时不纳入", "block_indices": [3, 4]},
        {"summary": "未选图块不能扩充选段", "block_indices": [5]},
    ]
    _save_card(path, card)

    result = lecture_study_reference(tmp_path, preview, [2, 3])

    assert result["note_count"] == 3
    assert "讲练与笔记" in result["materials"]
    assert "完整覆盖时纳入讲练笔记" in result["materials"]
    assert "跨越未选区块时不纳入" not in result["materials"]
    assert "未选图块不能扩充选段" not in result["materials"]


def test_old_study_card_without_teaching_flow_remains_compatible(tmp_path):
    preview = _preview()
    _index(tmp_path, preview)

    result = lecture_study_reference(tmp_path, preview, [2, 3])

    assert result["note_count"] == 2
    assert "合成知识：两段原文共同支持。" in result["materials"]
    assert "讲练与笔记 · 原文区块" not in result["materials"]


@pytest.mark.parametrize("mismatch", ["source_sha256", "source_name", "revision"])
def test_teaching_flow_is_not_attached_when_source_or_version_is_stale(
    tmp_path, mismatch
):
    preview = _preview()
    path, card = _index(tmp_path, preview)
    card["teaching_flow"] = [
        {"summary": "旧版本讲练笔记不得沿用", "block_indices": [2]}
    ]
    _save_card(path, card)
    if mismatch == "source_sha256":
        preview["source_sha256"] = "c" * 64
    elif mismatch == "source_name":
        preview["source_name"] = "另一份教案.docx"
    else:
        preview["revision"] = "d" * 64

    result = lecture_study_reference(tmp_path, preview, [2, 3, 4, 5])

    assert result["note_count"] == 0
    assert "旧版本讲练笔记不得沿用" not in result["materials"]

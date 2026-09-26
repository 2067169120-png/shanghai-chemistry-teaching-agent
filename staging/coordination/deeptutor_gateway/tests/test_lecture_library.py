from __future__ import annotations

import json
from types import SimpleNamespace

from integrations.deeptutor_shchem_v1.desktop_lecture_library import (
    INDEX_FILES,
    lecture_catalog,
    search_lectures,
)


def fixture(tmp_path, *, card_sha="a" * 64, name="电离教案.docx"):
    folder = tmp_path / "knowledge/lectures"
    folder.mkdir(parents=True)
    card = {
        "id": "LECT-1",
        "source_sha256": card_sha,
        "source_name": name,
        "title": "电离平衡",
        "human_reviewed": False,
        "review_status": "ai_distilled_pending_teacher_review",
        "knowledge": [{"summary": "原理和笔记线索", "block_indices": [2, 3]}],
        "methods": [{"summary": "电离常数的计算", "block_indices": [6]}],
        "pitfalls": [],
    }
    for filename in INDEX_FILES:
        (folder / filename).write_text(
            json.dumps(card, ensure_ascii=False) if filename == INDEX_FILES[0] else "",
            encoding="utf-8",
        )

    class Facade:
        paths = SimpleNamespace(workspace_root=tmp_path)

        def list_imported_word_batches(self):
            return [SimpleNamespace(batch_id="b1")]

        def imported_word_sources(self, batch):
            assert batch == "b1"
            return [
                {
                    "source_id": "s1",
                    "source_name": "电离教案.docx",
                    "source_sha256": "a" * 64,
                },
                {
                    "source_id": "s2",
                    "source_name": "原卷.docx",
                    "source_sha256": "b" * 64,
                },
            ]

        def imported_word_preview(self, *_):
            raise AssertionError("catalog must not read all original bytes")

    return Facade(), folder, card


def test_index_is_navigation_and_unindexed_originals_stay_visible(tmp_path):
    facade, _, _ = fixture(tmp_path)
    result = lecture_catalog(facade)
    assert result["warnings"] == []
    rows = result["items"]
    assert len(rows) == 2 and rows[0]["indexed"] and not rows[1]["indexed"]
    assert "待教师核对" in rows[0]["preview"] and "原文区块 2、3" in rows[0]["preview"]
    assert "materials" not in rows[0]
    assert search_lectures(rows, "电离 计算") == [rows[0]]
    assert search_lectures(rows, "") == rows
    assert search_lectures(rows, "不存在") == []


def test_sha_not_filename_controls_binding(tmp_path):
    facade, _, _ = fixture(tmp_path, card_sha="c" * 64)
    assert not any(row["indexed"] for row in lecture_catalog(facade)["items"])


def test_changed_source_name_does_not_attach_summary(tmp_path):
    facade, _, _ = fixture(tmp_path, name="别的教案.docx")
    assert not any(row["indexed"] for row in lecture_catalog(facade)["items"])


def test_malformed_card_fails_only_index_file_and_retains_originals(tmp_path):
    facade, folder, card = fixture(tmp_path)
    card["knowledge"][0]["block_indices"] = [True]
    (folder / INDEX_FILES[0]).write_text(json.dumps(card), encoding="utf-8")
    result = lecture_catalog(facade)
    assert len(result["items"]) == 2 and len(result["warnings"]) == 1
    assert not any(row["indexed"] for row in result["items"])


def test_missing_index_does_not_block_originals(tmp_path):
    facade, folder, _ = fixture(tmp_path)
    (folder / INDEX_FILES[0]).unlink()
    result = lecture_catalog(facade)
    assert len(result["warnings"]) == 1 and len(result["items"]) == 2


def test_broken_import_batch_is_visible_warning_not_global_failure(tmp_path):
    facade, _, _ = fixture(tmp_path)
    facade.imported_word_sources = lambda *_: (_ for _ in ()).throw(
        RuntimeError("private")
    )
    result = lecture_catalog(facade)
    assert not result["items"] and len(result["warnings"]) == 1
    assert "private" not in str(result)

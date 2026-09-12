import json

import pytest
from test_lecture_library import fixture

from integrations.deeptutor_shchem_v1.desktop_lecture_library import (
    INDEX_FILES,
    lecture_catalog,
)


def link():
    return {
        "volume_id": "TB-E1",
        "section_key": "TB-E1-C3:3.2",
        "source_sha256": "b" * 64,
        "pdf_pages": [70, 71],
        "printed_pages": [64, 65],
        "review_method": "page_images_read_by_model",
        "human_reviewed": False,
    }


def test_page_sequences_and_printed_pages_are_distinct_searchable_references(tmp_path):
    facade, folder, card = fixture(tmp_path)
    card["textbook_links"] = [link()]
    (folder / INDEX_FILES[0]).write_text(json.dumps(card), encoding="utf-8")
    result = lecture_catalog(facade)
    assert not result["warnings"]
    body = result["items"][0]["preview"]
    assert "PDF文件页序 70、71" in body
    assert "印刷页码 64、65" in body
    assert "尚未教师审核" in body


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("human_reviewed", True),
        ("pdf_pages", [True]),
        ("source_sha256", "wrong"),
        ("section_key", "TB-E2-C1:1.2"),
    ],
)
def test_malformed_link_cannot_attach_reference_and_original_still_available(
    tmp_path, key, value
):
    facade, folder, card = fixture(tmp_path)
    card["textbook_links"] = [{**link(), key: value}]
    (folder / INDEX_FILES[0]).write_text(json.dumps(card), encoding="utf-8")
    result = lecture_catalog(facade)
    assert len(result["items"]) == 2
    assert not result["items"][0]["indexed"]
    assert result["warnings"]

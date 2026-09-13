from __future__ import annotations

import hashlib
import json

import pytest

from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    CONCEPTS,
    PreparationSourceError,
    PreparationSourcesService,
)


@pytest.fixture
def source_service(tmp_path):
    book = tmp_path / "books" / "textbook.pdf"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"%PDF-fixture-source-book")
    row = {
        "concept_id": "C01",
        "title": "电解质",
        "statement": "在水溶液中或熔融状态能导电的化合物为电解质。",
        "volume_id": "TB-M1",
        "source_path": "books/textbook.pdf",
        "source_sha256": hashlib.sha256(book.read_bytes()).hexdigest(),
        "pdf_pages": [61, 62],
        "candidate_only": True,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "generation_allowed": False,
        "publication_allowed": False,
    }
    catalog = tmp_path / CONCEPTS
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return PreparationSourcesService(tmp_path)


def _chosen(service, concept_id="C01"):
    option = next(
        item for item in service.concept_options() if item["concept_id"] == concept_id
    )
    return {key: option[key] for key in ("concept_id", "revision")}


def _excerpt(service, **updates):
    chosen = _chosen(service)
    row = service._concepts()[chosen["concept_id"]]
    value = {
        "concept_id": chosen["concept_id"],
        "revision": chosen["revision"],
        "source_sha256": row["source_sha256"],
        "pdf_page": 61,
        "text": "教材原文第一行\n第二行  ",
        "confirmed": True,
    }
    value.update(updates)
    return value


def _reference(service, excerpt=None, concepts=None):
    chosen = concepts if concepts is not None else [_chosen(service)]
    kwargs = {}
    if excerpt is not None:
        kwargs["textbook_excerpts"] = [excerpt]
    return service.reference(None, None, 1, 1, chosen, **kwargs)


def test_reference_without_excerpts_keeps_legacy_materials(source_service):
    selected = _chosen(source_service)
    result = source_service.reference(None, None, 1, 1, [selected])
    assert (
        "蒸馏候选原文：在水溶液中或熔融状态能导电的化合物为电解质。"
        in result["materials"]
    )
    assert "教师确认的教材手工摘录" not in result["materials"]


def test_manual_excerpt_preserves_text_and_source_boundaries(source_service, tmp_path):
    book = tmp_path / "books" / "textbook.pdf"
    before = book.read_bytes()
    excerpt = _excerpt(source_service)
    result = _reference(source_service, excerpt)
    text = result["materials"]

    assert excerpt["text"] in text
    assert "教师确认的教材手工摘录" in text
    assert "教师手工提供" in text
    assert "软件未逐字核对" in text
    assert "摘录位置：PDF文件页序 61（不是教材纸面印刷页码）" in text
    assert source_service._concepts()["C01"]["source_sha256"] in text
    assert '"candidate_only": true' in text
    assert '"human_reviewed": false' in text
    assert '"teaching_use_allowed": false' in text
    assert '"generation_allowed": false' in text
    assert '"publication_allowed": false' in text
    assert book.read_bytes() == before
    assert str(tmp_path) not in text


@pytest.mark.parametrize(
    "updates, message",
    [
        ({"concept_id": "ORPHAN"}, "对应已选择"),
        ({"revision": "0" * 64}, "版本已变化"),
        ({"source_sha256": "0" * 64}, "教材文件已变化"),
        ({"pdf_page": 62}, None),
        ({"pdf_page": 60}, "来源范围"),
        ({"pdf_page": True}, "页序不正确"),
        ({"pdf_page": 0}, "页序不正确"),
        ({"confirmed": False}, "必须标记为已确认"),
        ({"confirmed": 1}, "必须标记为已确认"),
        ({"text": " \n\t"}, "不能是空白"),
        ({"text": "x" * 1201}, "不能超过1200字"),
    ],
)
def test_invalid_manual_excerpt_is_rejected(source_service, updates, message):
    excerpt = _excerpt(source_service, **updates)
    if message is None:
        result = _reference(source_service, excerpt)
        assert "PDF文件页序 62" in result["materials"]
    else:
        with pytest.raises(PreparationSourceError, match=message):
            _reference(source_service, excerpt)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.pop("text"),
        lambda value: value.update(extra="不允许"),
        lambda value: value.update(pdf_page="61"),
        lambda value: value.update(text=123),
        lambda value: value.update(concept_id=123),
    ],
)
def test_manual_excerpt_requires_exact_fields_and_types(source_service, mutator):
    excerpt = _excerpt(source_service)
    mutator(excerpt)
    with pytest.raises(PreparationSourceError):
        _reference(source_service, excerpt)


def test_duplicate_manual_excerpts_are_rejected(source_service):
    excerpt = _excerpt(source_service)
    with pytest.raises(PreparationSourceError, match="只能提供一条"):
        source_service.reference(
            None,
            None,
            1,
            1,
            [_chosen(source_service)],
            textbook_excerpts=[excerpt, dict(excerpt)],
        )


def test_more_than_thirty_manual_excerpts_are_rejected(source_service):
    excerpt = _excerpt(source_service)
    with pytest.raises(PreparationSourceError, match="最多提供30条"):
        source_service.reference(
            None,
            None,
            1,
            1,
            [_chosen(source_service)],
            textbook_excerpts=[
                dict(excerpt, concept_id=f"C{i:02d}") for i in range(31)
            ],
        )


def test_manual_excerpt_cannot_be_promoted_by_flags(source_service):
    text = _reference(source_service, _excerpt(source_service))["materials"]
    assert "教师确认的教材手工摘录" in text
    assert "不提升权限" in text
    assert '"teaching_use_allowed": false' in text
    assert '"generation_allowed": false' in text
    assert '"publication_allowed": false' in text


def test_changed_source_is_rejected_after_an_earlier_reference(
    source_service, tmp_path
):
    excerpt = _excerpt(source_service)
    first = _reference(source_service, excerpt)
    assert excerpt["text"] in first["materials"]

    book = tmp_path / "books" / "textbook.pdf"
    book.write_bytes(b"%PDF-changed-source-book")
    with pytest.raises(PreparationSourceError, match="原文件缺失或内容已变化"):
        _reference(source_service, excerpt)

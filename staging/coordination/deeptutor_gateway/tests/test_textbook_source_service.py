from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1 import (
    desktop_preparation_sources as source_module,
)
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    CONCEPTS,
    PreparationSourceError,
    PreparationSourcesService,
    _digest,
)

PDF_BYTES = b"%PDF-1.7\nfixture textbook page bytes\n%%EOF\n"


def _write_catalog(workspace: Path, row: dict[str, Any]) -> Path:
    catalog = workspace / CONCEPTS
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog


@pytest.fixture
def textbook_source_fixture(tmp_path: Path) -> dict[str, Any]:
    workspace = tmp_path / "workspace"
    book = workspace / "books" / "textbook.pdf"
    book.parent.mkdir(parents=True)
    book.write_bytes(PDF_BYTES)
    row = {
        "concept_id": "C-TEXTBOOK-01",
        "title": "电解质",
        "statement": "在水溶液中或熔融状态下导电的化合物。",
        "volume_id": "TB-M1",
        "chapter_id": "TB-M1-C1",
        "source_path": "books/textbook.pdf",
        "source_sha256": hashlib.sha256(PDF_BYTES).hexdigest(),
        "pdf_pages": [61, 62],
        "verification_caveat": "尚未经教师人工核验。",
        "candidate_only": True,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "generation_allowed": False,
        "publication_allowed": False,
    }
    catalog = _write_catalog(workspace, row)
    return {
        "workspace": workspace,
        "book": book,
        "catalog": catalog,
        "row": row,
        "service": PreparationSourcesService(workspace),
    }


def _option(fixture: dict[str, Any]) -> tuple[str, str]:
    option = fixture["service"].concept_options()[0]
    return option["concept_id"], option["revision"]


def _assert_no_absolute_path(value: Any, path: Path) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _assert_no_absolute_path(child, path)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_absolute_path(child, path)
    elif isinstance(value, str):
        assert str(path) not in value


def test_textbook_source_returns_verified_pdf_bytes_and_pages_without_paths(
    textbook_source_fixture: dict[str, Any],
) -> None:
    service = textbook_source_fixture["service"]
    workspace = textbook_source_fixture["workspace"]
    book = textbook_source_fixture["book"]
    catalog = textbook_source_fixture["catalog"]
    before_book = book.read_bytes()
    before_catalog = catalog.read_bytes()
    concept_id, revision = _option(textbook_source_fixture)

    result = service.textbook_source(concept_id, revision)

    assert result["concept_id"] == concept_id
    assert result["source_name"] == book.name
    assert result["source_sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
    assert result["pdf_pages"] == [61, 62]
    assert result["pdf_bytes"] == PDF_BYTES
    assert result["pdf_bytes"].startswith(b"%PDF-")
    _assert_no_absolute_path(result, workspace)
    assert book.read_bytes() == before_book
    assert catalog.read_bytes() == before_catalog

    # The page list is a response copy, not a mutable view of the catalog row.
    result["pdf_pages"].append(999)
    again = service.textbook_source(concept_id, revision)
    assert again["pdf_pages"] == [61, 62]


def test_textbook_source_reads_pdf_once_per_request_and_revalidates_next_request(
    textbook_source_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = textbook_source_fixture["service"]
    book = textbook_source_fixture["book"]
    concept_id, revision = _option(textbook_source_fixture)
    original_open = Path.open
    opens: list[Path] = []

    def counted_open(self: Path, *args: Any, **kwargs: Any):
        if self.resolve() == book.resolve():
            opens.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)

    first = service.textbook_source(concept_id, revision)
    assert len(opens) == 1
    second = service.textbook_source(concept_id, revision)

    assert first["pdf_bytes"] == second["pdf_bytes"] == PDF_BYTES
    assert len(opens) == 2
    # Reopening must catch changes; caching would hide a replaced textbook.
    book.write_bytes(b"%PDF-1.7\nchanged after first view\n%%EOF\n")
    with pytest.raises(PreparationSourceError, match="缺失、格式不支持或内容已变化"):
        service.textbook_source(concept_id, revision)


def test_textbook_source_rejects_stale_revision_without_reading_the_pdf(
    textbook_source_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = textbook_source_fixture["service"]
    book = textbook_source_fixture["book"]
    concept_id, old_revision = _option(textbook_source_fixture)
    row = dict(textbook_source_fixture["row"])
    row["statement"] = "教材目录已经更新。"
    _write_catalog(textbook_source_fixture["workspace"], row)
    original_open = Path.open
    opens: list[Path] = []

    def counted_open(self: Path, *args: Any, **kwargs: Any):
        if self.resolve() == book.resolve():
            opens.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)

    with pytest.raises(PreparationSourceError, match="已变化或缺失"):
        service.textbook_source(concept_id, old_revision)
    assert opens == []


def test_textbook_source_rejects_catalog_hash_mismatch(
    textbook_source_fixture: dict[str, Any],
) -> None:
    workspace = textbook_source_fixture["workspace"]
    row = dict(textbook_source_fixture["row"])
    row["source_sha256"] = "0" * 64
    _write_catalog(workspace, row)
    service = PreparationSourcesService(workspace)
    with pytest.raises(PreparationSourceError, match="缺失、格式不支持或内容已变化"):
        service.textbook_source(row["concept_id"], _digest(row))


def test_textbook_source_rejects_changed_pdf_even_with_old_catalog_revision(
    textbook_source_fixture: dict[str, Any],
) -> None:
    service = textbook_source_fixture["service"]
    book = textbook_source_fixture["book"]
    concept_id, revision = _option(textbook_source_fixture)
    book.write_bytes(b"%PDF-1.7\nchanged bytes\n%%EOF\n")

    with pytest.raises(PreparationSourceError, match="缺失、格式不支持或内容已变化"):
        service.textbook_source(concept_id, revision)


@pytest.mark.parametrize(
    "pages",
    [
        None,
        [],
        [0],
        [True],
        [1, 1],
    ],
    ids=["missing", "empty", "zero", "bool", "duplicate"],
)
def test_textbook_source_rejects_missing_or_invalid_page_sequences(
    textbook_source_fixture: dict[str, Any], pages: Any
) -> None:
    row = dict(textbook_source_fixture["row"])
    if pages is None:
        row.pop("pdf_pages")
    else:
        row["pdf_pages"] = pages
    _write_catalog(textbook_source_fixture["workspace"], row)
    service = PreparationSourcesService(textbook_source_fixture["workspace"])

    with pytest.raises(PreparationSourceError, match="有效PDF文件页序"):
        service.textbook_source(row["concept_id"], _digest(row))


@pytest.mark.parametrize("source_path", ["books/textbook.docx", "books/textbook"])
def test_textbook_source_rejects_non_pdf_source_paths(
    textbook_source_fixture: dict[str, Any], source_path: str
) -> None:
    workspace = textbook_source_fixture["workspace"]
    source = workspace / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(PDF_BYTES)
    row = dict(textbook_source_fixture["row"])
    row["source_path"] = source_path
    row["source_sha256"] = hashlib.sha256(PDF_BYTES).hexdigest()
    _write_catalog(workspace, row)
    service = PreparationSourcesService(workspace)

    with pytest.raises(PreparationSourceError, match="缺失、格式不支持或内容已变化"):
        service.textbook_source(row["concept_id"], _digest(row))


def test_textbook_source_rejects_pdf_without_pdf_signature(
    textbook_source_fixture: dict[str, Any],
) -> None:
    workspace = textbook_source_fixture["workspace"]
    source = workspace / "books/not-a-pdf.pdf"
    bad_bytes = b"plain text pretending to be a PDF"
    source.write_bytes(bad_bytes)
    row = dict(textbook_source_fixture["row"])
    row["source_path"] = "books/not-a-pdf.pdf"
    row["source_sha256"] = hashlib.sha256(bad_bytes).hexdigest()
    _write_catalog(workspace, row)
    service = PreparationSourcesService(workspace)

    with pytest.raises(PreparationSourceError, match="缺失、格式不支持或内容已变化"):
        service.textbook_source(row["concept_id"], _digest(row))


def test_textbook_source_rejects_file_outside_workspace(
    textbook_source_fixture: dict[str, Any],
) -> None:
    workspace = textbook_source_fixture["workspace"]
    outside = workspace.parent / "outside-book.pdf"
    outside.write_bytes(PDF_BYTES)
    row = dict(textbook_source_fixture["row"])
    row["source_path"] = str(outside)
    row["source_sha256"] = hashlib.sha256(PDF_BYTES).hexdigest()
    _write_catalog(workspace, row)
    service = PreparationSourcesService(workspace)

    with pytest.raises(PreparationSourceError, match="缺失、格式不支持或内容已变化"):
        service.textbook_source(row["concept_id"], _digest(row))


def test_textbook_source_enforces_preview_byte_limit(
    textbook_source_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = PreparationSourcesService(textbook_source_fixture["workspace"])
    concept_id, revision = _option(textbook_source_fixture)
    monkeypatch.setattr(source_module, "MAX_TEXTBOOK_PREVIEW_BYTES", len(PDF_BYTES) - 1)

    with pytest.raises(PreparationSourceError, match="超过256MB"):
        service.textbook_source(concept_id, revision)


def test_facade_textbook_source_is_local_only_and_provider_free(
    textbook_source_fixture: dict[str, Any],
) -> None:
    class ForbiddenProviderAccess:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"provider access is forbidden: {name}")

    facade = object.__new__(DesktopWorkbenchFacade)
    facade.paths = SimpleNamespace(workspace_root=textbook_source_fixture["workspace"])
    facade._providers = ForbiddenProviderAccess()
    concept_id, revision = _option(textbook_source_fixture)

    result = facade.preparation_textbook_source(concept_id, revision)

    assert result["pdf_bytes"] == PDF_BYTES
    assert result["pdf_pages"] == [61, 62]
    _assert_no_absolute_path(result, textbook_source_fixture["workspace"])

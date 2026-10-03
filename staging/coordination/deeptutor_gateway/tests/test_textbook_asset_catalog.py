from __future__ import annotations

import hashlib
import io
import json
import stat
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import curriculum_workbench as curriculum
from integrations.deeptutor_shchem_v1 import desktop_textbook_asset_catalog as catalog
from integrations.deeptutor_shchem_v1 import desktop_textbook_assets as metadata
from test_textbook_section_reader import blank_pdf, section_fixture


def candidate(f, **changes):
    row = {
        "visual_asset_id": "SYNTHETIC-ASSET-1", "label": "合成候选素材",
        "asset_type": "candidate_diagram", "description": "合成测试说明，待核对。",
        "volume_id": f.row["volume_id"], "chapter_id": f.row["chapter_id"],
        "section_key": f.row["section_key"], "supplement_node_key": None,
        "pdf_page": 9, "printed_page": 5, "source_sha256": f.row["source_sha256"],
        "anchor_type": "whole_page", "bbox": None, "cropped": False,
        "asset_localization_status": "whole_page_only_pending_bbox_review",
        "review_status": "candidate-only", "candidate_only": True,
        "human_reviewed": False, "retrieval_ready": False, "teaching_use_allowed": False,
        "generation_allowed": False, "publication_allowed": False,
        "page_image_path": "never-opened/image.png",
    }
    row.update(changes)
    return row


@pytest.fixture
def asset_fixture(section_fixture):
    f = section_fixture
    f.asset_path = f.root / metadata.CATALOG_RELATIVE_PATH

    def write(rows):
        f.asset_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    f.write_assets = write
    f.asset = candidate(f)
    write([f.asset])
    f.assets = catalog.TextbookAssetCatalogService(f.root)
    return f


def selection(f, visual_asset_id=None):
    rows = f.assets.options()["assets"]
    row = next(row for row in rows if visual_asset_id is None or row["visual_asset_id"] == visual_asset_id)
    return {key: row[key] for key in ("visual_asset_id", "revision")}


def replace_pdf(f, data):
    f.book.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    for row in f.registry["volumes"] + f.registry["nodes"]:
        if row["volume_id"] == f.row["volume_id"]:
            row["source_sha256"] = sha
    f.asset["source_sha256"] = sha
    f.activate()
    f.write_assets([f.asset])


def test_exact_anchor_and_all_gates_preserved_without_mutation(asset_fixture):
    f = asset_fixture
    before = {path: path.read_bytes() for path in (f.book, f.asset_path, f.catalog, f.registry_path)}
    option = selection(f)
    result = f.assets.source(**option)
    assert result["pdf_pages"] == [9]  # Not the concept's [7, 8, 10] or section's [7..15].
    assert result["printed_page"] == 5 and result["reading_mode"] == "asset"
    assert result["title"] == f.asset["label"] and result["statement"] == f.asset["description"]
    assert result["source_title"] == result["volume_title"] == "合成教材册 4"
    assert result["source_name"] == f.book.name and result["source_sha256"] == f.asset["source_sha256"]
    assert result["visual_asset_id"] == option["visual_asset_id"] and result["revision"] == option["revision"]
    assert result["pdf_bytes"] == before[f.book]
    expected = metadata._asset(f.asset)
    assert result["visual_assets"] == {"assets": [expected], "notices": []}
    assert expected["candidate_only"] is True and all(expected[key] is False for key in metadata._CLOSED_FLAGS)
    assert "concept_id" not in result and "materials" not in result
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("chapter,page,supplement", [
    ("TB-E2-C1", 5, "declared-introduction"),
    ("TB-E2-C1", 20, "declared-review"),
    (None, 128, "declared-appendix"),
])
def test_null_section_supplementary_and_appendix_scope_is_not_guessed(asset_fixture, chapter, page, supplement):
    f = asset_fixture
    f.asset.update(section_key=None, chapter_id=chapter, pdf_page=page, printed_page=None,
                   supplement_node_key=supplement, visual_asset_id="MISLEADING-TB-M1-C99-ID")
    f.write_assets([f.asset])
    listing = f.assets.options()
    assert listing["notices"] == [] and len(listing["assets"]) == 1
    row = listing["assets"][0]
    assert row["section_key"] is None and row["section_title"] is None
    assert row["chapter_id"] == chapter and row["supplement_node_key"] == supplement
    result = f.assets.source(**selection(f))
    assert result["pdf_pages"] == [page] and result["printed_page"] is None
    assert result["chapter_id"] == chapter and result["section_key"] is None
    if chapter is None:
        assert result["chapter_title"] is None


def test_query_is_bounded_and_listing_never_opens_a_pdf_or_image(asset_fixture, monkeypatch):
    f = asset_fixture
    original = Path.open
    opened = []

    def metadata_only(self, *args, **kwargs):
        opened.append(self)
        assert self in {f.asset_path, f.registry_path}
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", metadata_only)
    assert len(f.assets.options("合成教材册 4 候选")["assets"]) == 1
    assert f.assets.options("不存在的描述")["assets"] == []
    assert f.assets.options("x" * (catalog.MAX_QUERY + 1))["notices"]
    assert f.assets.options(None)["notices"]
    assert opened and set(opened) == {f.asset_path, f.registry_path}


def test_search_by_pdf_or_printed_page_preserves_unknown_printed_page(asset_fixture):
    f = asset_fixture
    f.write_assets([f.asset, candidate(
        f, visual_asset_id="SYNTHETIC-UNKNOWN-PRINTED", pdf_page=12, printed_page=None,
    )])
    before = f.asset_path.read_bytes()
    assert [row["visual_asset_id"] for row in f.assets.options("TB-E2 9")["assets"]] == [f.asset["visual_asset_id"]]
    assert [row["visual_asset_id"] for row in f.assets.options("TB-E2 5")["assets"]] == [f.asset["visual_asset_id"]]
    unknown = f.assets.options("TB-E2 12")["assets"]
    assert len(unknown) == 1 and unknown[0]["printed_page"] is None
    # Section page offsets cannot fill in an unregistered printed page.
    assert f.assets.options("12 8")["assets"] == []
    assert f.assets.options("None")["assets"] == []
    assert f.asset_path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["asset-text", "ignored-metadata", "activated-directory", "remove", "duplicate"])
def test_stale_request_rereads_catalog_and_directory(asset_fixture, mutation):
    f = asset_fixture
    chosen = selection(f)
    if mutation in {"asset-text", "ignored-metadata"}:
        f.asset["description" if mutation == "asset-text" else "page_image_path"] = "changed"
        f.write_assets([f.asset])
    elif mutation == "activated-directory":
        f.registry["title"] = "另一个明确激活的合成目录版本"
        f.activate()
    elif mutation == "remove":
        f.write_assets([])
    else:
        f.write_assets([f.asset, deepcopy(f.asset)])
    with pytest.raises(catalog.TextbookAssetCatalogError, match="刷新"):
        f.assets.source(**chosen)


def test_catalog_changed_during_pdf_read_is_refused(asset_fixture, monkeypatch):
    f = asset_fixture
    chosen = selection(f)
    original = catalog._read_pdf

    def changed_after_read(*args, **kwargs):
        data = original(*args, **kwargs)
        f.asset["label"] = "changed during read"
        f.write_assets([f.asset])
        return data

    monkeypatch.setattr(catalog, "_read_pdf", changed_after_read)
    with pytest.raises(catalog.TextbookAssetCatalogError, match="读取期间已变化"):
        f.assets.source(**chosen)


@pytest.mark.parametrize("changes", [{}, {"volume_id": "TB-M1"}, {"candidate_only": False}])
def test_duplicate_ids_including_invalid_twins_are_all_refused(asset_fixture, changes):
    f = asset_fixture
    chosen = selection(f)
    f.write_assets([f.asset, {**f.asset, **changes}, candidate(f, visual_asset_id="unique")])
    result = f.assets.options()
    assert [row["visual_asset_id"] for row in result["assets"]] == ["unique"]
    assert metadata.CONFLICT in result["notices"]
    with pytest.raises(catalog.TextbookAssetCatalogError):
        f.assets.source(**chosen)


@pytest.mark.parametrize("changes", [
    {"source_sha256": "f" * 64}, {"volume_id": "not-active"},
    {"volume_id": "TB-M1"}, {"chapter_id": "TB-M1-C1"}, {"chapter_id": None},
    {"section_key": "TB-E2-C1:1.2"}, {"section_key": "not-a-section"},
    {"pdf_page": 16}, {"printed_page": 99},
    {"supplement_node_key": "also-a-supplement"},
    {"section_key": None, "supplement_node_key": None},
    {"section_key": None, "supplement_node_key": "TB-E2-C1:1.1"},
])
def test_source_and_classification_mismatch_is_visible_not_certified(asset_fixture, changes):
    f = asset_fixture
    f.write_assets([{**f.asset, **changes}])
    result = f.assets.options()
    assert result["assets"] == [] and result["notices"]
    with pytest.raises(catalog.TextbookAssetCatalogError):
        f.assets.source(f.asset["visual_asset_id"], "a" * 64)


@pytest.mark.parametrize("key", ["human_reviewed", "retrieval_ready", "teaching_use_allowed", "generation_allowed", "publication_allowed"])
def test_open_permission_gate_is_not_an_available_option(asset_fixture, key):
    f = asset_fixture
    f.write_assets([{**f.asset, key: True}])
    result = f.assets.options()
    assert result["assets"] == [] and metadata.UNAVAILABLE in result["notices"]


@pytest.mark.parametrize("unsafe", ["../private.pdf", "C:/private.pdf", "books\\private.pdf", "books/a/../../private.pdf", "//host/share/private.pdf", "books/private.txt"])
def test_directory_source_paths_are_not_used_when_unsafe(asset_fixture, unsafe):
    f = asset_fixture
    for row in f.registry["volumes"] + f.registry["nodes"]:
        if row["volume_id"] == f.row["volume_id"]:
            row["source_path"] = unsafe
    f.activate()
    result = f.assets.options()
    assert result["assets"] == [] and any("位置" in text for text in result["notices"])


def test_image_and_asset_source_paths_ignored_while_only_activated_pdf_is_opened(asset_fixture, monkeypatch):
    f = asset_fixture
    f.asset.update(page_image_path=r"\\other-host\private.png", source_path="../private.pdf", model_input_allowed=True)
    f.write_assets([f.asset])
    chosen = selection(f)
    original = Path.open
    opened = []

    def allowed_only(self, *args, **kwargs):
        opened.append(self)
        assert self in {f.asset_path, f.registry_path, f.book}
        assert not args or "w" not in args[0] and "a" not in args[0] and "+" not in args[0]
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", allowed_only)
    result = f.assets.source(**chosen)
    assert f.book in opened
    assert not {"page_image_path", "source_path", "model_input_allowed"} & result.keys()
    assert not {"page_image_path", "source_path", "model_input_allowed"} & result["visual_assets"]["assets"][0].keys()


@pytest.mark.parametrize("kind", ["pdf", "source-parent", "catalog", "directory", "workspace"])
def test_symlink_file_parent_and_workspace_rejected(asset_fixture, tmp_path, kind):
    f = asset_fixture
    chosen = selection(f)
    if kind == "workspace":
        link = tmp_path / "workspace-link"
        destination, directory = f.root, True
    elif kind == "source-parent":
        link = f.book.parent
        destination, directory = f.root / "moved-books", True
        link.rename(destination)
    else:
        link = {"pdf": f.book, "catalog": f.asset_path, "directory": f.registry_path}[kind]
        destination, directory = link.with_suffix(link.suffix + ".real"), False
        link.rename(destination)
    try:
        link.symlink_to(destination, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    service = catalog.TextbookAssetCatalogService(link if kind == "workspace" else f.root)
    with pytest.raises(catalog.TextbookAssetCatalogError):
        service.source(**chosen)


def test_windows_source_reparse_point_is_rejected_before_pdf_open(asset_fixture, monkeypatch):
    f = asset_fixture
    chosen = selection(f)
    original_lstat, original_open = Path.lstat, Path.open

    def reparse(self, *args, **kwargs):
        info = original_lstat(self, *args, **kwargs)
        if self == f.book.parent:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        return info

    def guarded(self, *args, **kwargs):
        assert self != f.book
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse)
    monkeypatch.setattr(Path, "open", guarded)
    with pytest.raises(catalog.TextbookAssetCatalogError, match="链接"):
        f.assets.source(**chosen)


@pytest.mark.parametrize("failure", ["missing", "changed-sha", "header", "invalid-pdf", "too-few-pages", "too-large", "encrypted"])
def test_pdf_failures_never_return_unverified_bytes(asset_fixture, monkeypatch, failure):
    f = asset_fixture
    if failure == "header":
        replace_pdf(f, b"not a PDF but hash registered")
    elif failure == "invalid-pdf":
        replace_pdf(f, b"%PDF-1.7\nnot a page tree\n%%EOF")
    elif failure == "too-few-pages":
        replace_pdf(f, blank_pdf(8))
    elif failure == "encrypted":
        from pypdf import PdfWriter

        writer, output = PdfWriter(), io.BytesIO()
        writer.add_blank_page(600, 800)
        writer.encrypt("synthetic-password")
        writer.write(output)
        replace_pdf(f, output.getvalue())
    chosen = selection(f)
    if failure == "missing":
        f.book.unlink()
    elif failure == "changed-sha":
        f.book.write_bytes(blank_pdf(127))
    elif failure == "too-large":
        monkeypatch.setattr(catalog, "MAX_PDF_BYTES", f.book.stat().st_size - 1)
    with pytest.raises(catalog.TextbookAssetCatalogError) as raised:
        f.assets.source(**chosen)
    assert raised.value.code == "textbook_asset_catalog_unavailable" and raised.value.message_zh


def test_pdf_growth_after_stat_is_bounded(asset_fixture, monkeypatch):
    f = asset_fixture
    chosen = selection(f)
    monkeypatch.setattr(catalog, "MAX_PDF_BYTES", f.book.stat().st_size)
    original = Path.open

    def growing(self, *args, **kwargs):
        if self == f.book and args == ("rb",):
            with original(self, "ab") as stream:
                stream.write(b" ")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing)
    with pytest.raises(catalog.TextbookAssetCatalogError, match="大小限制"):
        f.assets.source(**chosen)


def test_inconsistent_actual_pdf_page_tree_is_refused(asset_fixture):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NumberObject, NameObject

    f = asset_fixture
    reader = PdfReader(io.BytesIO(blank_pdf(12)))
    writer, output = PdfWriter(clone_from=reader), io.BytesIO()
    writer._root_object["/Pages"][NameObject("/Count")] = NumberObject(10)
    writer.write(output)
    replace_pdf(f, output.getvalue())
    with pytest.raises(catalog.TextbookAssetCatalogError, match="页结构"):
        f.assets.source(**selection(f))


@pytest.mark.parametrize("failure", ["missing", "unactivated", "bad-protocol", "too-large", "duplicate-json-key"])
def test_active_directory_is_required_and_rechecked(asset_fixture, monkeypatch, failure):
    f = asset_fixture
    chosen = selection(f)
    if failure == "missing":
        f.registry_path.unlink()
    elif failure == "unactivated":
        f.registry_path.write_bytes(f.registry_path.read_bytes() + b" ")
    elif failure == "bad-protocol":
        f.registry["numbered_section_count"] = 59
        f.activate()
    elif failure == "too-large":
        monkeypatch.setattr(catalog, "MAX_DIRECTORY_BYTES", f.registry_path.stat().st_size - 1)
    else:
        raw = b'{"nodes": [], "nodes": []}'
        f.registry_path.write_bytes(raw)
        monkeypatch.setattr(curriculum, "EXPECTED_DIRECTORY_FILE_SHA256", hashlib.sha256(raw).hexdigest())
    result = f.assets.options()
    assert result["assets"] == [] and result["notices"]
    with pytest.raises(catalog.TextbookAssetCatalogError):
        f.assets.source(**chosen)


@pytest.mark.parametrize("failure", ["missing", "malformed", "duplicate-key", "nonfinite", "file-limit", "line-limit", "row-limit"])
def test_catalog_is_fixed_bounded_and_strict(asset_fixture, monkeypatch, failure):
    f = asset_fixture
    if failure == "missing":
        f.asset_path.unlink()
    elif failure in {"malformed", "duplicate-key", "nonfinite"}:
        f.asset_path.write_bytes({"malformed": b"{", "duplicate-key": b'{"id":1,"id":2}',
                                  "nonfinite": b'{"unknown":1e999}'}[failure])
    elif failure == "file-limit":
        monkeypatch.setattr(metadata, "MAX_FILE_BYTES", f.asset_path.stat().st_size - 1)
    elif failure == "line-limit":
        monkeypatch.setattr(metadata, "MAX_LINE_BYTES", 20)
    else:
        f.write_assets([f.asset, candidate(f, visual_asset_id="second")])
        monkeypatch.setattr(metadata, "MAX_ROWS", 1)
    result = f.assets.options()
    assert result["assets"] == [] and result["notices"]


@pytest.mark.parametrize("selection_value", [
    {"visual_asset_id": None, "revision": "a" * 64},
    {"visual_asset_id": "id", "revision": "stale"},
    {"visual_asset_id": "x" * 181, "revision": "a" * 64},
])
def test_invalid_selection_has_actionable_error_without_loading(asset_fixture, monkeypatch, selection_value):
    f = asset_fixture
    monkeypatch.setattr(f.assets, "_entries", lambda: pytest.fail("invalid selection should not read metadata"))
    with pytest.raises(catalog.TextbookAssetCatalogError, match="刷新目录后重选"):
        f.assets.source(**selection_value)

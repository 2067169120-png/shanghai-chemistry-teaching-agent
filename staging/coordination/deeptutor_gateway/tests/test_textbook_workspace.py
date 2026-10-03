import hashlib
import json

import pytest
from pypdf import PdfWriter

from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    PreparationSourceError,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import (
    TextbookWorkspaceService,
    _digest,
)


def pdf(path, pages=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(300, 420)
    writer.write(path)
    return path


@pytest.fixture
def service(tmp_path):
    return TextbookWorkspaceService(DesktopPaths.from_workspace(tmp_path / "workspace", state_root=tmp_path / "state"),
        DesktopStateStore(tmp_path / "state"))


def candidate(service, source, **changes):
    row = {"concept_id": "CON-1", "title": "待核对知识", "summary": "来自已有资料的候选摘要。",
        "candidate_only": True, "human_reviewed": False, "teaching_use_allowed": False,
        "generation_allowed": False, "publication_allowed": False,
        "curriculum": {"volume_id": "TB-M1", "section_key": "TB-M1-C1:1.1"},
        "source": {"sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "pdf_pages": [2]}}
    row.update(changes)
    file = service.paths.workspace_root / "knowledge" / "textbook" / "knowledge.jsonl"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return row, file


def test_duplicate_and_same_name_new_content_preserve_original_and_all_versions(service, tmp_path):
    first = pdf(tmp_path / "a" / "教材.pdf", 2)
    second = pdf(tmp_path / "b" / "教材.pdf", 3)
    original = first.read_bytes()
    initial = service.commit_books(service.preview_books([first]))
    assert initial["added"] == 1
    assert service.commit_books(service.preview_books([first]))["reused"] == 1
    assert service.commit_books(service.preview_books([second]))["added"] == 1
    assert len(service.books()) == 2
    assert first.read_bytes() == original
    assert service.read_book(initial["source_ids"][0], hashlib.sha256(original).hexdigest())["pdf_bytes"] == original
    assert all(book["human_reviewed"] is False for book in service.books())


def test_preview_binds_actual_bytes_and_failed_change_does_not_overwrite(service, tmp_path):
    book = pdf(tmp_path / "教材.pdf")
    preview = service.preview_books([book])
    pdf(book, 3)
    with pytest.raises(PreparationSourceError, match="发生变化"):
        service.commit_books(preview)
    assert service.books() == []
    saved = service.commit_books(service.preview_books([book]))
    archived = service.root / (saved["source_ids"][0].removeprefix("BOOK-") + ".pdf")
    archived.write_bytes(b"changed")
    with pytest.raises(PreparationSourceError):
        service.read_book(saved["source_ids"][0], saved["source_ids"][0].removeprefix("BOOK-"))
    with pytest.raises(PreparationSourceError, match="未覆盖"):
        service.commit_books(service.preview_books([book]))
    assert archived.read_bytes() == b"changed"


def test_candidate_import_and_hash_bound_original_page_never_promote_permissions(service, tmp_path):
    book = pdf(tmp_path / "教材.pdf")
    row, file = candidate(service, book)
    before = file.read_bytes()
    assert service.candidate_catalog()["imported"] is False
    result = service.import_candidates()
    assert service.import_candidates() == result
    assert file.read_bytes() == before
    with pytest.raises(PreparationSourceError, match="尚未导入"):
        service.read_candidate(row["concept_id"], _digest(row))
    service.commit_books(service.preview_books([book]))
    opened = service.read_candidate(row["concept_id"], _digest(row))
    assert opened["pdf_pages"] == [2]
    assert opened["pdf_bytes"] == book.read_bytes()
    assert service.candidate_catalog()["rows"][0]["teaching_use_allowed"] is False
    file.write_text("changed", encoding="utf-8")
    assert service.candidate_catalog()["rows"][0] == row


def test_invalid_candidate_permissions_and_page_range_are_rejected(service, tmp_path):
    book = pdf(tmp_path / "教材.pdf")
    candidate(service, book, teaching_use_allowed=True)
    with pytest.raises(PreparationSourceError, match="权限"):
        service.import_candidates()
    row, file = candidate(service, book)
    row["source"]["pdf_pages"] = [999]
    file.write_text(json.dumps(row), encoding="utf-8")
    service.commit_books(service.preview_books([book]))
    with pytest.raises(PreparationSourceError, match="页序"):
        service.read_candidate(row["concept_id"], _digest(row))


def test_native_section_route_requires_exact_original_concept_version(service, tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
    from integrations.deeptutor_shchem_v1.desktop_preparation_sources import CONCEPTS
    book = pdf(tmp_path / "教材.pdf")
    row, file = candidate(service, book)
    original = {"concept_id": row["concept_id"], "title": row["title"], "statement": "原摘要",
        "source_path": "books/original.pdf", "source_sha256": row["source"]["sha256"], "pdf_pages": [2]}
    row["source"]["concept_record_sha256"] = _digest(original)
    file.write_text(json.dumps(row), encoding="utf-8")
    native = service.paths.workspace_root / CONCEPTS
    native.parent.mkdir(parents=True, exist_ok=True)
    native.write_text(json.dumps(original), encoding="utf-8")
    facade = build_default_facade(service.paths)
    try:
        assert row["concept_id"] in facade.textbook_study_catalog()["native_options"]
        original["statement"] = "已经改变的摘要"
        native.write_text(json.dumps(original), encoding="utf-8")
        assert facade.textbook_study_catalog()["native_options"] == {}
        assert facade.textbook_study_catalog()["rows"][0] == row
    finally:
        facade.shutdown()


def test_partial_stop_is_idempotent_and_preserves_saved_books(service, tmp_path):
    books = [pdf(tmp_path / f"{i}.pdf", i + 1) for i in range(3)]
    progress = []
    preview = service.preview_books(books)
    result = service.commit_books(preview, progress_callback=progress.append, should_cancel=lambda: bool(progress))
    assert result["added"] == 1 and len(service.books()) == 1
    continued = service.commit_books(service.preview_books(books))
    assert continued["added"] == 2 and continued["reused"] == 1


def test_invalid_or_encrypted_pdf_does_not_create_archives(service, tmp_path):
    file = tmp_path / "invalid.pdf"
    file.write_bytes(b"%PDF-invalid")
    with pytest.raises(PreparationSourceError):
        service.preview_books([file])
    writer = PdfWriter()
    writer.add_blank_page(300, 420)
    writer.encrypt("password")
    writer.write(file)
    with pytest.raises(PreparationSourceError):
        service.preview_books([file])
    assert not service.root.exists()


def test_textbook_backup_restores_hashes_and_candidates_without_overwriting(service, tmp_path):
    book = pdf(tmp_path / "教材.pdf")
    row, _file = candidate(service, book)
    service.commit_books(service.preview_books([book]))
    service.import_candidates()
    bundle = tmp_path / "backup.zip"
    assert service.export_bundle(bundle) == {"books": 1, "files": 2}
    destination = tmp_path / "restored"
    assert service.restore_bundle(bundle, destination) == {"books": 1, "files": 2}
    from integrations.deeptutor_shchem_v1.desktop_backup import restored_profile
    assert restored_profile(destination) == destination.resolve()
    restored = TextbookWorkspaceService(DesktopPaths.from_workspace(service.paths.workspace_root, state_root=destination),
        DesktopStateStore(destination))
    assert restored.read_candidate(row["concept_id"], _digest(row))["pdf_bytes"] == book.read_bytes()
    before = (destination / "desktop-state.v1.json").read_bytes()
    with pytest.raises(PreparationSourceError, match="不会覆盖"):
        service.restore_bundle(bundle, destination)
    assert (destination / "desktop-state.v1.json").read_bytes() == before


def test_corrupt_backup_rejected_without_leaving_a_restore_directory(service, tmp_path):
    from zipfile import ZipFile
    book = pdf(tmp_path / "教材.pdf")
    service.commit_books(service.preview_books([book]))
    bundle = tmp_path / "backup.zip"
    service.export_bundle(bundle)
    with ZipFile(bundle) as archive:
        data = {name: archive.read(name) for name in archive.namelist()}
    name = next(name for name in data if name.endswith(".pdf"))
    data[name] = b"changed"
    with ZipFile(bundle, "w") as archive:
        for name, raw in data.items():
            archive.writestr(name, raw)
    destination = tmp_path / "restored"
    with pytest.raises(PreparationSourceError, match="校验失败"):
        service.restore_bundle(bundle, destination)
    assert not destination.exists()

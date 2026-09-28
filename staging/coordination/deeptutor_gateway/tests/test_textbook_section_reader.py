from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import curriculum_workbench as registry_module
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import CONCEPTS, PreparationSourceError, PreparationSourcesService
from integrations.deeptutor_shchem_v1.desktop_textbook_section_reader import TextbookSectionError, textbook_section_scope

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def section_fixture(tmp_path, monkeypatch):
    registry = json.loads((ROOT / "sh-chem-db" / registry_module.DIRECTORY_RELATIVE).read_text(encoding="utf-8"))
    row = next(json.loads(line) for line in (ROOT / CONCEPTS).read_text(encoding="utf-8").splitlines()
               if json.loads(line)["concept_id"] == "TB-E2-C1-S11-C01")
    book = tmp_path / row["source_path"]
    book.parent.mkdir(parents=True)
    book.write_bytes(b"%PDF-1.7\nsection fixture\n%%EOF")
    sha = hashlib.sha256(book.read_bytes()).hexdigest()
    row["source_sha256"] = sha
    for item in registry["volumes"] + registry["nodes"]:
        if item["volume_id"] == row["volume_id"]:
            item["source_sha256"] = sha
    catalog = tmp_path / CONCEPTS
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(row), encoding="utf-8")
    registry_path = tmp_path / "sh-chem-db" / registry_module.DIRECTORY_RELATIVE
    registry_path.parent.mkdir(parents=True)

    def activate():
        data = json.dumps(registry, ensure_ascii=False).encode("utf-8")
        registry_path.write_bytes(data)
        monkeypatch.setattr(registry_module, "EXPECTED_DIRECTORY_FILE_SHA256", hashlib.sha256(data).hexdigest())

    activate()
    return SimpleNamespace(root=tmp_path, row=row, registry=registry, registry_path=registry_path,
                           book=book, catalog=catalog, activate=activate, service=PreparationSourcesService(tmp_path))


def chosen(fixture):
    option = fixture.service.concept_options()[0]
    return {key: option[key] for key in ("concept_id", "revision")}


def test_section_reads_registry_range_without_changing_concept_or_reference(section_fixture):
    f = section_fixture
    before = [path.read_bytes() for path in (f.book, f.catalog, f.registry_path)]
    selection = chosen(f)
    concept = f.service.textbook_source(**selection)
    section = f.service.textbook_section_source(**selection)
    assert concept["pdf_pages"] == [7, 8, 10]
    assert section["pdf_pages"] == list(range(7, 16))
    assert section["printed_pages"] == list(range(3, 12))
    assert section["concept_pdf_pages"] == [7, 8, 10]
    assert section["reading_mode"] == "section" and section["section_key"] == "TB-E2-C1:1.1"
    assert section["pdf_bytes"] == concept["pdf_bytes"] == before[0]
    assert section["registry_sha256"] == hashlib.sha256(before[2]).hexdigest()
    assert f.service.concept_options()[0]["revision"] == selection["revision"]
    materials = f.service.reference(None, None, 1, 1, [selection])["materials"]
    assert "PDF文件页序：[7, 8, 10]" in materials
    with pytest.raises(PreparationSourceError, match="选择记录不正确"):
        f.service.reference(None, None, 1, 1, [section])
    with pytest.raises(PreparationSourceError, match="不在当前知识点来源范围"):
        f.service.reference(None, None, 1, 1, [selection], textbook_excerpts=[{
            **selection, "source_sha256": section["source_sha256"], "pdf_page": 15,
            "text": "整节阅读不能授权超出知识点的摘录", "confirmed": True,
        }])
    assert [path.read_bytes() for path in (f.book, f.catalog, f.registry_path)] == before


def test_section_rechecks_registry_each_request_and_concept_view_remains_available(section_fixture):
    f = section_fixture
    selection = chosen(f)
    f.service.textbook_section_source(**selection)
    f.registry_path.write_bytes(f.registry_path.read_bytes() + b" ")
    with pytest.raises(TextbookSectionError, match="目录未能核对"):
        f.service.textbook_section_source(**selection)
    assert f.service.textbook_source(**selection)["pdf_pages"] == [7, 8, 10]
    node = next(n for n in f.registry["nodes"] if n["node_key"] == f.row["section_key"])
    node["content_pdf_pages"], node["printed_pages"] = [7, 14], [3, 10]
    f.activate()  # An explicitly activated valid registry is re-read, not cached.
    assert f.service.textbook_section_source(**selection)["pdf_pages"] == list(range(7, 15))


@pytest.mark.parametrize("key,value", [
    ("section_key", "unknown"), ("volume_id", "TB-E1"), ("source_sha256", "0" * 64),
    ("chapter_id", "TB-E2-C9"), ("pdf_pages", [7, 20]),
])
def test_mismatched_concept_cannot_expand_to_a_section(section_fixture, key, value):
    f = section_fixture
    f.row[key] = value
    f.catalog.write_text(json.dumps(f.row), encoding="utf-8")
    with pytest.raises(TextbookSectionError):
        f.service.textbook_section_source(**chosen(f))


def test_stale_concept_and_changed_pdf_fail_before_returning_section(section_fixture):
    f = section_fixture
    selection = chosen(f)
    with pytest.raises(PreparationSourceError, match="已变化"):
        f.service.textbook_section_source(selection["concept_id"], "stale")
    f.book.write_bytes(b"%PDF-1.7\nchanged\n%%EOF")
    with pytest.raises(PreparationSourceError, match="内容已变化"):
        f.service.textbook_section_source(**selection)


@pytest.mark.parametrize("key,value", [
    ("content_pdf_pages", [7, True]), ("printed_pages", [3, 10]),
    ("source_root", "other_root"), ("status", "unverified"),
])
def test_registry_protocol_or_page_mapping_is_not_guessed(section_fixture, key, value):
    f = section_fixture
    node = next(n for n in f.registry["nodes"] if n["node_key"] == f.row["section_key"])
    node[key] = value
    f.activate()
    with pytest.raises(TextbookSectionError):
        f.service.textbook_section_source(**chosen(f))


@pytest.mark.parametrize("unsafe", ["../private.pdf", "C:/private.pdf", "books\\private.pdf"])
def test_unsafe_registry_source_paths_are_blocked(section_fixture, unsafe):
    f = section_fixture
    for item in f.registry["volumes"] + f.registry["nodes"]:
        if item["volume_id"] == f.row["volume_id"]:
            item["source_path"] = unsafe
    f.row["source_path"] = unsafe
    f.catalog.write_text(json.dumps(f.row), encoding="utf-8")
    f.activate()
    with pytest.raises(TextbookSectionError, match="位置"):
        f.service.textbook_section_source(**chosen(f))


def test_section_notes_do_not_define_or_extend_the_registry_pages(section_fixture):
    f = section_fixture
    folder = f.root / "knowledge/textbook"
    folder.mkdir(parents=True)
    (folder / "reading-notes-broken.json").write_text('{"pdf_pages":[1,9999]}', encoding="utf-8")
    assert f.service.textbook_section_source(**chosen(f))["pdf_pages"] == list(range(7, 16))


@pytest.mark.parametrize("raw", [b"{", b'{"nodes":[],"nodes":[]}', b"x" * (2 * 1024 * 1024 + 1)],
                         ids=["invalid-json", "duplicate-key", "oversized"])
def test_unreadable_or_invalid_directory_only_blocks_section(section_fixture, monkeypatch, raw):
    f = section_fixture
    f.registry_path.write_bytes(raw)
    monkeypatch.setattr(registry_module, "EXPECTED_DIRECTORY_FILE_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(TextbookSectionError):
        f.service.textbook_section_source(**chosen(f))
    assert f.service.textbook_source(**chosen(f))["pdf_pages"] == [7, 8, 10]


def test_facade_section_reader_has_no_state_or_provider_dependency(section_fixture):
    f = section_fixture
    facade = SimpleNamespace(paths=SimpleNamespace(workspace_root=f.root))
    source = DesktopWorkbenchFacade.preparation_textbook_section_source(facade, **chosen(f))
    assert source["pdf_pages"] == list(range(7, 16))


def test_all_real_concepts_have_explicit_scope_or_a_specific_block_reason(tmp_path):
    concepts = [json.loads(line) for line in (ROOT / CONCEPTS).read_text(encoding="utf-8").splitlines()]
    assert len(concepts) == 383
    # Worktree data may be a junction to the user's private workspace. Stage
    # identical directory bytes in a regular contained root for this check.
    registry = tmp_path / "sh-chem-db" / registry_module.DIRECTORY_RELATIVE
    registry.parent.mkdir(parents=True)
    registry.write_bytes((ROOT / "sh-chem-db" / registry_module.DIRECTORY_RELATIVE).read_bytes())
    scopes = {}
    blocked = {}
    for row in concepts:
        try:
            scope = textbook_section_scope(tmp_path, row)
        except TextbookSectionError as exc:
            blocked[row["concept_id"]] = str(exc)
        else:
            scopes[row["concept_id"]] = scope
            assert set(row["pdf_pages"]).issubset(scope["pdf_pages"])
    assert len(scopes) + len(blocked) == 383
    assert len(scopes) == 360 and len(blocked) == 23
    assert all("尚无明确的册与节" in reason for reason in blocked.values())
    assert scopes["TB-M1-C4-4.1-C01"]["pdf_pages"] == list(range(112, 124))
    assert scopes["TB-E2-C1-S11-C01"]["pdf_pages"] == list(range(7, 16))
    assert scopes["TB-E2-C1-S13-C03"]["pdf_pages"] == list(range(21, 30))


def ui_source():
    from pypdf import PdfWriter

    writer, buffer = PdfWriter(), io.BytesIO()
    for _ in range(5):
        writer.add_blank_page(600, 800)
    writer.write(buffer)
    return {"concept_id": "C1", "title": "所选概念", "source_name": "教材.pdf", "statement": "概念摘要",
            "pdf_bytes": buffer.getvalue(), "reading_mode": "section", "volume_title": "测试教材册",
            "section_number": "1.1", "section_title": "测试节", "pdf_pages": [2, 3, 4],
            "printed_pages": [10, 11, 12], "concept_pdf_pages": [3], "reading_hints": {"notes": [], "notices": []}}


def test_real_qt_section_navigation_and_excerpt_handlers_are_read_only():
    from PySide6.QtWidgets import QApplication
    from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog

    app = QApplication.instance() or QApplication([])

    class Bridge:
        def submit(self, _label, operation, **callbacks):
            self.operation, self.callbacks = operation, callbacks

    bridge = Bridge()
    dialog = TextbookSourceDialog(object(), {"concept_id": "C1", "revision": "r"},
                                  tasks=bridge, reading_mode="section")
    dialog.resize(420, 620)
    dialog.show()
    app.processEvents()
    bridge.callbacks["on_success"](ui_source())
    app.processEvents()
    assert dialog._loaded_pages and not dialog.excerpt_button.isVisible()
    assert not dialog.excerpt_button.isEnabled()
    assert "2—4" in dialog.heading.text() and "10—12" in dialog.heading.text()
    assert "测试教材册" in dialog.heading.text() and "测试节" in dialog.heading.text()
    assert dialog.pages.itemText(1) == "PDF第3页 · 印刷第11页"
    assert not dialog.previous.isEnabled() and dialog.next.isEnabled()
    dialog._jump(3)
    assert dialog.view.pageNavigator().currentPage() == 3
    assert not dialog.next.isEnabled() and dialog.previous.isEnabled()
    dialog._jump(4)
    assert dialog.view.pageNavigator().currentPage() == 3
    from PySide6.QtCore import QPointF
    dialog.view.pageNavigator().jump(0, QPointF())
    assert dialog.view.pageNavigator().currentPage() == 1
    dialog._edit_excerpt()
    dialog._excerpt_applied({"text": "must not become a selection"})
    assert dialog.excerpt_panel is None and dialog.excerpt is None and not dialog._closed
    assert dialog.next.mapTo(dialog, dialog.next.rect().bottomRight()).y() < dialog.height()
    dialog.reject()


def test_section_out_of_document_pages_fail_without_enabling_reader():
    from PySide6.QtWidgets import QApplication
    from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog

    app = QApplication.instance() or QApplication([])

    class Bridge:
        def submit(self, _label, _operation, **callbacks):
            self.callbacks = callbacks

    bridge = Bridge()
    dialog = TextbookSourceDialog(object(), {"concept_id": "C1", "revision": "r"},
                                  tasks=bridge, reading_mode="section")
    app.processEvents()
    source = ui_source()
    source["pdf_pages"] = [2, 3, 9]
    bridge.callbacks["on_success"](source)
    app.processEvents()
    assert not dialog._loaded_pages and not dialog.next.isEnabled()
    assert "本节目录页序超出教材范围" in dialog.status.text()
    assert dialog.excerpt is None
    dialog.reject()


def test_section_button_never_changes_selection_even_if_dialog_returns_accepted(monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import PreparationSourcesDialog
    import integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog as module

    app = QApplication.instance() or QApplication([])
    seen = []

    class Facade:
        def preparation_concept_options(self, _query):
            return [{"concept_id": "C1", "revision": "r", "title": "概念", "statement": "摘要"}]

        def preparation_textbook_source(self, *_args):
            raise AssertionError("not called by fixture")

        preparation_textbook_section_source = preparation_textbook_source

    class Preview:
        excerpt = {"text": "not authorized as a section action"}

        def __init__(self, _facade, concept, _parent, **kwargs):
            seen.append((concept, kwargs))

        def exec(self):
            return QDialog.DialogCode.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr(module, "TextbookSourceDialog", Preview)
    dialog = PreparationSourcesDialog(Facade())
    app.processEvents()
    dialog.concept_list.setCurrentRow(0)
    assert dialog.section_read_button.isEnabled()
    before = deepcopy(dialog.selected_concepts), deepcopy(dialog._textbook_excerpts)
    dialog.section_read_button.click()
    assert seen[0][1] == {"reading_mode": "section"}
    assert (dialog.selected_concepts, dialog._textbook_excerpts) == before == ([], {})
    dialog.reject()

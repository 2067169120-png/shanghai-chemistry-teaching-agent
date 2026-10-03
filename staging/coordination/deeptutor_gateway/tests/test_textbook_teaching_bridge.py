"""Handoff tests use synthetic identities, not private books or question text."""
import copy
import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_explorer_index import PersonalSearchIndex
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import (
    CONCEPTS,
    PreparationSourceError,
    _digest,
)
from integrations.deeptutor_shchem_v1.desktop_textbook_teaching_bridge import (
    preparation_selection,
    question_selection,
)
from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import (
    _digest as candidate_digest,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import (
    PreparationSourcesDialog,
)


@pytest.fixture
def linked(tmp_path):
    native = {"concept_id": "C1", "title": "合成测试概念", "statement": "仅用于接口回归",
        "source_path": "book.pdf", "source_sha256": "a" * 64,
        "volume_id": "V1", "chapter_id": "CH1", "section_key": "S1",
        "candidate_only": True, "human_reviewed": False}
    p = tmp_path / CONCEPTS
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(native, ensure_ascii=False) + "\n", encoding="utf-8")
    candidate = {"concept_id": "C1", "title": native["title"],
        "curriculum": {k: native[k] for k in ("volume_id", "chapter_id", "section_key")}}
    catalog = {"rows": [candidate], "native_options": {"C1": {"concept_id": "C1", "revision": _digest(native)}}}
    nodes = [{"node_key": key, "volume_id": "V1", "chapter_id": "CH1",
              "volume_title": "测试教材", "chapter_title": "测试章", "section_title": key}
             for key in ("S1", "S2")]
    taxonomy = tmp_path / "sh-chem-db/kb/knowledge_taxonomy.json"
    taxonomy.write_text(json.dumps({"dimensions": {"knowledge_points": []}}), encoding="utf-8")
    directory = tmp_path / "sh-chem-db/kb/classification/supplemental_wechat_textbook_tagging_v1_2026-08-27/textbook_directory_nodes.json"
    directory.parent.mkdir(parents=True)
    directory.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")
    facade = SimpleNamespace(paths=SimpleNamespace(workspace_root=tmp_path), textbook_study_catalog=lambda: copy.deepcopy(catalog))
    return facade, catalog, native, p, directory, nodes


def test_exact_section_filters_keep_other_same_chapter_questions_out(linked):
    facade, catalog, native, path, _directory, nodes = linked
    before = path.read_bytes()
    request = question_selection(facade, "C1", candidate_digest(catalog["rows"][0]))
    rows = []
    for key, section in (("right", "S1"), ("other-section", "S2"), ("stale", "S1")):
        row = {"key": key, "revision": "r1", "source_sha256": "b" * 64,
            "source_id": "source", "source_name": "合成来源", "title": key,
            "question_blocks": [], "context_blocks": [], "answer_blocks": [],
            "attributes": {"key": key, "source_sha256": "b" * 64,
                "question_revision": "old" if key == "stale" else "r1",
                "curriculum_status": "auto_suggested",
                "curriculum_candidates": [{"volume_id": "V1", "chapter_id": "CH1",
                    "section_key": section, "status": "auto_suggested"}]}}
        rows.append(row)
    index = PersonalSearchIndex({"items": rows, "attribute_catalog": {"nodes": nodes, "knowledge_points": []}}, "word_native")
    result = index.search(request["filters"], "")
    assert result["total"] == 1
    assert result["entries"][0]["key"] == "right"
    assert path.read_bytes() == before
    assert native["human_reviewed"] is False


@pytest.mark.parametrize("change", ["selection-revision", "missing-native", "changed-native", "shelf-parent"])
def test_stale_or_mismatched_bindings_do_not_create_handoffs(linked, change):
    facade, catalog, _native, path, _directory, _nodes = linked
    revision = candidate_digest(catalog["rows"][0])
    if change == "selection-revision":
        revision = "stale"
    elif change == "missing-native":
        catalog["native_options"].clear()
    elif change == "changed-native":
        row = json.loads(path.read_text(encoding="utf-8"))
        row["statement"] = "changed"
        path.write_text(json.dumps(row), encoding="utf-8")
    else:
        catalog["rows"][0]["curriculum"]["chapter_id"] = "WRONG"
        revision = candidate_digest(catalog["rows"][0])
    with pytest.raises(PreparationSourceError):
        preparation_selection(facade, "C1", revision)


@pytest.mark.parametrize("change", ["missing-node", "wrong-parent"])
def test_question_handoff_never_falls_back_to_whole_chapter(linked, change):
    facade, catalog, _native, _path, directory, nodes = linked
    nodes[0]["node_key" if change == "missing-node" else "chapter_id"] = "WRONG"
    directory.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")
    revision = candidate_digest(catalog["rows"][0])
    with pytest.raises(PreparationSourceError, match="未放宽"):
        question_selection(facade, "C1", revision)
    # Independent preparation can still use its source-bound native concept.
    assert preparation_selection(facade, "C1", revision)["concept_id"] == "C1"


def test_preselection_requires_exact_revision_and_explicit_preview():
    app = QApplication.instance() or QApplication([])
    facade = SimpleNamespace(preparation_concept_options=lambda _query: [
        {"concept_id": "C1", "revision": "r1", "title": "合成测试", "statement": "测试摘要"}])
    dialog = PreparationSourcesDialog(facade)
    try:
        assert not dialog.preselect_concepts([{"concept_id": "C1", "revision": "stale"}])
        assert dialog.selected_concepts == []
        assert dialog.preselect_concepts([{"concept_id": "C1", "revision": "r1"}])
        assert dialog.selected_concepts == [{"concept_id": "C1", "revision": "r1"}]
        assert dialog.reference is None
        assert not dialog.import_button.isEnabled()
        assert dialog.preview_button.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()


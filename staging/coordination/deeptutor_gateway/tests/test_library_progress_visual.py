"""C08 visual review using synthetic catalogues and the real Qt source editor."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_personal_visual_attributes_ui import CATALOG, Facade, _propose
from test_word_question_dialog import _Tasks

from integrations.deeptutor_shchem_v1.desktop_library_progress import (
    collect_library_progress, summarize_visual_catalog,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import _seal
from integrations.deeptutor_shchem_v1.desktop_workbench.library_progress_dialog import LibraryProgressDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_attributes_dialog import PersonalVisualAttributesDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import PersonalVisualQuestionDialog
from runtime.deeptutor_shchem.library_progress_qa import SyntheticFacade


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _complete():
    catalog = SyntheticFacade().personal_visual_questions()
    row = catalog["items"][1]
    attrs = row["attributes"]
    attrs["applicable_grades"].update(values=["grade_11"], status="teacher_proposed")
    for field, value in (("year", "2026"), ("region", "上海"), ("exam_type", "school_exam")):
        attrs["original_source"][field].update(value=value, status="teacher_proposed")
    attrs["curriculum_status"] = "teacher_proposed"
    attrs["curriculum_candidates"] = [{"section_key": "synthetic-section", "volume_id": "synthetic-volume",
        "chapter_id": "synthetic-chapter", "label": "合成教材节", "status": "teacher_proposed", "evidence": []}]
    row["attributes"] = _seal(attrs)
    return {**catalog, "items": [row]}


def test_visual_valid_fields_accept_saved_teacher_proposals_without_claiming_approval():
    catalog = _complete()
    before = deepcopy(catalog)
    result = summarize_visual_catalog(catalog)
    row = result["rows"][0]
    assert not row["todo"] and row["labels_complete_candidate"] and row["protected"]
    assert all(result["counts"][field] == 1 for field in ("primary", "curriculum", "grade", "exam", "provenance"))
    assert "teacher_reviewed" not in row and "score" not in row
    assert catalog == before


@pytest.mark.parametrize("field", ["key", "revision", "batch_id", "candidate_revision", "candidate_sha256"])
def test_visual_attributes_require_exact_source_binding_and_keep_teacher_protection(field):
    catalog = _complete()
    catalog["items"][0][field] += "-changed"
    row = summarize_visual_catalog(catalog)["rows"][0]
    assert row["protected"] and "stale" in row["todo_keys"]
    assert all(field in row["todo_keys"] for field in ("primary", "curriculum", "grade", "exam", "provenance"))


def test_visual_invalid_fields_parent_mismatch_and_supporting_tags_do_not_fill_primary():
    catalog = _complete()
    attrs = catalog["items"][0]["attributes"]
    attrs["supporting_knowledge"] = [deepcopy(attrs["primary_knowledge"])]
    attrs["primary_knowledge"].update(id="unknown", label="待标注", status="unknown")
    attrs["curriculum_candidates"][0]["chapter_id"] = "wrong-parent"
    attrs["applicable_grades"]["status"] = "unknown"
    attrs["original_source"]["exam_type"]["status"] = "unknown"
    catalog["items"][0]["attributes"] = _seal(attrs)
    row = summarize_visual_catalog(catalog)["rows"][0]
    assert all(field in row["todo_keys"] for field in ("primary", "curriculum", "grade", "exam"))
    assert "provenance" not in row["todo_keys"]
    catalog.pop("attribute_catalog")
    assert summarize_visual_catalog(catalog)["warnings"]


def test_visual_not_selectable_material_and_unknown_provenance_are_distinct():
    catalog = _complete()
    item = catalog["items"][0]
    item["selection_ready"] = False
    item["material_review_required"] = True
    item["source_name"] = "2025上海二模高三-不要推断.jpg"
    attrs = item["attributes"]
    for name in ("year", "region", "school", "exam_type"):
        attrs["original_source"][name].update(value="unknown", status="unknown")
    item["attributes"] = _seal(attrs)
    result = summarize_visual_catalog(catalog)
    assert result["not_selectable"] == 1 and result["counts"]["material_gaps"] == 1
    assert set(result["rows"][0]["todo_keys"]) == {"not_selectable", "material", "exam", "provenance"}
    assert "score" not in str(result) and "合成隐藏答案" not in str(result)


def test_true_theme_parent_and_cas_order_are_retained_not_inferred_from_number_or_key():
    catalog = SyntheticFacade().personal_visual_questions()
    catalog["items"] = list(reversed(catalog["items"]))
    catalog["items"][0]["question_number"] = "1"
    result = summarize_visual_catalog(catalog)
    assert result["themes"] == 3
    assert [(row["theme_sequence"], row["printed_sequence"]) for row in result["rows"]] == [
        (theme, question) for theme in (1, 2, 3) for question in range(1, 7)]
    item = catalog["items"][0]
    item.pop("theme_sequence")
    item.pop("theme_key")
    row = next(row for row in summarize_visual_catalog(catalog)["rows"] if row["key"] == item["key"])
    assert row["theme_sequence"] is None and row["theme_identity"] == ""
    assert "hierarchy" in row["todo_keys"]


def test_visual_catalog_duplicates_and_partial_failure_are_not_total_zero():
    catalog = _complete()
    catalog["items"] += [deepcopy(catalog["items"][0]), {}, None]
    catalog["unavailable_batches"] = 2
    result = summarize_visual_catalog(catalog)
    assert result["printed_questions"] == 1 and result["unavailable_batches"] == 2 and result["warnings"]
    facade = SimpleNamespace(word_question_catalog=lambda: {"items": [], "sources": []},
                             personal_visual_questions=lambda: {"wrong": []})
    report = collect_library_progress(facade)
    assert report["word"] is not None and report["visual"] is None


def test_service_exposes_cas_hierarchy_without_changing_existing_content_revision(monkeypatch, tmp_path):
    from integrations.deeptutor_shchem_v1 import desktop_personal_visual_questions as service_module
    from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import PersonalVisualQuestionService
    monkeypatch.setattr(service_module, "load_attribute_catalog", lambda _root: deepcopy(CATALOG))
    service = object.__new__(PersonalVisualQuestionService)
    service.facade = SimpleNamespace(paths=SimpleNamespace(workspace_root=tmp_path))
    service.attribute_store = SimpleNamespace(get_many=lambda _keys: {})
    service._effective_evidence = lambda *args: ({}, {})
    atomic = {"atomic_part_id": "a", "part_label": "（1）", "stem": "合成观察任务",
        "answer": {"status": "missing"}, "classification": {"primary_knowledge_K": ["K11"],
        "supporting_knowledge_K": []}, "curriculum": {"primary_chapter": "unknown", "secondary_chapters": []}}
    printed = {"printed_question_id": "printed-identity", "question_number": "甲", "sequence_in_theme": 7,
               "atomic_parts": [atomic], "stem": "合成印刷小题"}
    theme = {"theme_big_question_id": "theme-identity", "title": "合成主题", "context": "未关联共同材料",
             "sequence_in_paper": 3, "merge_status": "complete", "printed_questions": [printed],
             "shared_materials": [], "visual_objects": []}
    snapshot = {"candidate": {"paper": {"title": "合成来源", "paper_type": "unknown", "theme_big_questions": [theme]},
                              "unaligned_answer_candidates": []},
                "revision_token": "candidate-r1", "candidate_sha256": "c" * 64}
    row = service._rows("DESKTOPBATCH-" + "a" * 32, snapshot, {})[0]
    assert (row["theme_sequence"], row["printed_sequence"], row["question_number"]) == (3, 7, "甲")
    assert row["theme_big_question_id"] == "theme-identity" and row["printed_question_id"] == "printed-identity"
    assert row["sequence_source"] == "candidate_cas" and row["material_review_required"]
    # Source-side metadata is not newly mixed into the pre-existing hash.
    fields = ("batch_id", "key", "theme_key", "theme_title", "title", "source_name", "question_number",
              "question_text", "shared_text", "answer_text", "warnings", "images", "facets", "curriculum_paths",
              "selection_ready", "candidate_revision", "candidate_sha256", "teacher_reviewed")
    projection = {field: deepcopy(row[field]) for field in fields}
    projection["facets"].pop("teaching_use")
    assert service_module._digest(projection) == row["revision"]


def test_service_partial_read_failure_exposes_unknown_total(monkeypatch, tmp_path):
    from integrations.deeptutor_shchem_v1 import desktop_personal_visual_questions as service_module
    from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import PersonalVisualQuestionService
    monkeypatch.setattr(service_module, "load_attribute_catalog", lambda _root: deepcopy(CATALOG))
    service = object.__new__(PersonalVisualQuestionService)
    service.facade = SimpleNamespace(paths=SimpleNamespace(workspace_root=tmp_path))
    service.state = SimpleNamespace(snapshot=lambda: {"drafts": {"synthetic": {
        "kind": "desktop_visual_import_v2", "visual_status": "completed", "batch_id": "synthetic-unavailable"}}})
    def failed(_batch):
        raise OSError("private synthetic path")
    service._batch = failed
    catalog = service.catalog()
    assert catalog["unavailable_batches"] == 1 and not catalog["items"]
    assert catalog["attribute_catalog"] == CATALOG
    assert "private synthetic path" not in str(catalog)


class VisualFacade(Facade):
    def __init__(self):
        super().__init__()
        for index, row in enumerate(self.rows):
            row.update(candidate_revision="synthetic-candidate-r1", candidate_sha256="c" * 64,
                       theme_sequence=1, printed_sequence=1 if index < 2 else 2,
                       sequence_source="candidate_cas")
        self.rows[2].update(theme_key=self.rows[0]["theme_key"], theme_title=self.rows[0]["theme_title"])
        self.unavailable_batches = 0

    def word_question_catalog(self):
        return SyntheticFacade().word_question_catalog()

    def personal_visual_questions(self, batch_id=None):
        result = super().personal_visual_questions(batch_id)
        result["attribute_catalog"] = deepcopy(CATALOG)
        result["unavailable_batches"] = self.unavailable_batches
        for row in result["items"]:
            row["attributes"] = deepcopy(self.attributes[row["key"]])
        return result


@pytest.fixture
def visual_progress(qt_app):
    facade, tasks = VisualFacade(), _Tasks()
    dialog = LibraryProgressDialog(facade, tasks)
    tasks.flush()
    dialog.bank_tabs.setCurrentIndex(1)
    dialog.show()
    qt_app.processEvents()
    yield dialog, facade, tasks
    dialog.reject()
    tasks.flush()
    dialog.deleteLater()
    qt_app.processEvents()


def _select(dialog, key):
    dialog.table.selectRow(next(i for i, row in enumerate(dialog.records) if row["key"] == key))


def test_bank_and_theme_filters_keep_independent_identity_and_empty_scope(visual_progress):
    dialog, facade, tasks = visual_progress
    assert [row["key"] for row in dialog.records] == ["q-a", "q-c", "q-b"]
    dialog.theme_filter.setCurrentIndex(1)
    assert [row["key"] for row in dialog.records] == ["q-a", "q-c"]
    _select(dialog, "q-c")
    dialog.todo_filter.setCurrentIndex(dialog.todo_filter.findData("curriculum"))
    visual_theme = dialog.theme_filter.currentData()
    dialog.bank_tabs.setCurrentIndex(0)
    dialog.search.setText("材料中的关系")
    dialog._filter_rows()
    word_keys = [row["key"] for row in dialog.records]
    dialog.bank_tabs.setCurrentIndex(1)
    assert not dialog.search.text() and dialog.records[dialog.table.currentRow()]["key"] == "q-c"
    assert dialog.theme_filter.currentData() == visual_theme
    facade.rows = [row for row in facade.rows if row["key"] == "q-b"]
    dialog.refresh()
    tasks.flush()
    assert dialog.theme_filter.currentData() == visual_theme and not dialog.records
    assert "筛选" in dialog.empty_state.text() and not dialog.open_button.isEnabled()
    dialog.bank_tabs.setCurrentIndex(0)
    assert dialog.search.text() == "材料中的关系" and [row["key"] for row in dialog.records] == word_keys


@pytest.mark.parametrize("outcome", ["save", "cancel", "save_failure", "image_failure"])
def test_clickthrough_real_visual_editor_and_return_refresh(visual_progress, monkeypatch, outcome):
    dialog, facade, tasks = visual_progress
    _select(dialog, "q-c")
    dialog.todo_filter.setCurrentIndex(dialog.todo_filter.findData("exam"))
    calls = []
    before = deepcopy(facade.attributes)
    facade.fail_save = outcome == "save_failure"
    if outcome == "image_failure":
        def fail_image(*args, **kwargs):
            raise RuntimeError("合成原图暂不可读")
        monkeypatch.setattr(facade, "personal_visual_question_image", fail_image)

    def edit(editor):
        _propose(editor)
        if outcome == "cancel":
            editor.reject()
        else:
            editor._confirm()
        return editor.result()

    def browse(editor):
        tasks.flush()
        calls.append(editor._current_token)
        assert editor._current_token == (facade.rows[2]["batch_id"], "q-c", "rev-c")
        assert editor._current_detail["theme_key"] == facade.rows[0]["theme_key"]
        if outcome == "image_failure":
            assert not editor.attributes_button.isEnabled()
        else:
            editor.attributes_button.click()
            tasks.flush()
            if outcome == "save_failure":
                assert editor.status.objectName() == "StatusError"
        editor.reject()
        return editor.result()

    monkeypatch.setattr(PersonalVisualAttributesDialog, "exec", edit)
    monkeypatch.setattr(PersonalVisualQuestionDialog, "exec", browse)
    dialog.open_button.click()
    tasks.flush()
    assert len(calls) == 1 and dialog.todo_filter.currentData() == "exam"
    assert dialog.open_button.isEnabled() and dialog.refresh_button.isEnabled()
    if outcome == "save":
        assert [row["key"] for row in dialog.records] == ["q-a", "q-b"]
        assert dialog.records[dialog.table.currentRow()]["key"] == "q-b"
        row = next(row for row in dialog.report["visual"]["rows"] if row["key"] == "q-c")
        assert "exam" not in row["todo_keys"] and row["protected"]
        assert facade.attributes["q-a"] == before["q-a"]
    else:
        assert dialog.records[dialog.table.currentRow()]["key"] == "q-c"
        assert facade.attributes == before


@pytest.mark.parametrize("change", ["missing", "revision", "duplicate"])
def test_visual_deep_link_cannot_fall_back_to_a_different_question(qt_app, change):
    facade, tasks = VisualFacade(), _Tasks()
    target = deepcopy(facade.rows[2])
    if change == "missing":
        facade.rows.pop(2)
    elif change == "revision":
        facade.rows[2]["revision"] = "new-revision"
    else:
        facade.rows.append({**deepcopy(target), "revision": "another-version"})
    editor = PersonalVisualQuestionDialog(facade, tasks, batch_id=target["batch_id"],
        initial_question_key=target["key"], initial_revision=target["revision"])
    tasks.flush()
    assert editor._current_token is None and not editor.attributes_button.isEnabled()
    assert "未选中其他题" in editor.status.text() and not facade.image_calls
    editor._apply_filters()
    assert editor._current_token is None
    editor.reject()


def test_deep_link_refresh_removed_target_and_closed_callbacks_do_not_open_another(qt_app):
    facade, tasks = VisualFacade(), _Tasks()
    target = facade.rows[2]
    editor = PersonalVisualQuestionDialog(facade, tasks, batch_id=target["batch_id"],
        initial_question_key=target["key"], initial_revision=target["revision"])
    tasks.flush()
    assert editor._current_token[1] == "q-c"
    facade.rows.pop(2)
    editor._load_catalog()
    tasks.flush()
    assert editor._current_token is None and "未选中其他题" in editor.status.text()
    editor._load_catalog()
    pending = tasks.pending[-1]
    editor.reject()
    pending["on_success"](facade.personal_visual_questions())
    assert editor._current_token is None and not editor.attributes_button.isEnabled()


def test_deep_link_invalid_crop_receipt_never_selects_another_question(qt_app):
    facade, tasks = VisualFacade(), _Tasks()
    target = facade.rows[2]
    editor = PersonalVisualQuestionDialog(facade, tasks, batch_id=target["batch_id"],
        initial_question_key=target["key"], initial_revision=target["revision"])
    tasks.flush()
    token = editor._current_token
    assert token[1] == "q-c"
    editor._crop_saved(token, {"affected_question_keys": ["q-c"]}, {"batch_id": "wrong-batch"})
    assert editor._current_token is None and not editor.attributes_button.isEnabled()
    assert "无法完整核对" in editor.status.text()
    editor.reject()


def test_visual_failure_partial_count_retry_and_expired_progress_callbacks(visual_progress):
    dialog, facade, tasks = visual_progress
    dialog.todo_filter.setCurrentIndex(dialog.todo_filter.findData("exam"))
    old = deepcopy(dialog.report)
    facade.catalog_failure = True
    dialog.refresh()
    tasks.flush()
    assert dialog.visual_count.text() == "暂不可读" and not dialog.open_button.isEnabled()
    assert "private catalog path" not in dialog.warnings.text()
    assert dialog.report["word"] is not None and dialog.todo_filter.currentData() == "exam"
    facade.catalog_failure = False
    facade.unavailable_batches = 2
    dialog.refresh()
    tasks.flush()
    assert dialog.visual_count.text() == "已读 3 道" and "不是全库总量" in dialog.visual_summary.text()
    dialog.refresh()
    stale = tasks.pending[-1]
    dialog.refresh()
    fresh = tasks.pending[-1]
    fresh["on_success"](old)
    stale["on_failure"]("过期错误")
    assert dialog.open_button.isEnabled() and "过期错误" not in dialog.status.text()
    dialog.refresh()
    pending = tasks.pending[-1]
    dialog.cancel_button.click()
    pending["on_success"](old)
    assert not dialog.open_button.isEnabled() and "上次快照" in dialog.save.text()
    dialog.reject()
    pending["on_failure"]("关闭后的错误")
    assert not dialog.open_button.isEnabled() and "关闭后的错误" not in dialog.status.text()


def test_visual_500_plus_rows_search_and_return_at_same_scroll(qt_app, monkeypatch):
    facade = SyntheticFacade()
    base = facade.visual_rows[0]
    facade.visual_rows = [{**deepcopy(base), "key": f"large-{index}", "title": f"合成大量题-{index}",
                          "printed_sequence": index + 1} for index in range(655)]
    tasks = _Tasks()
    dialog = LibraryProgressDialog(facade, tasks)
    tasks.flush()
    dialog.bank_tabs.setCurrentIndex(1)
    dialog.show()
    qt_app.processEvents()
    assert dialog.table.rowCount() == 655 and dialog.table.height() <= 302
    _select(dialog, "large-501")
    dialog.table.verticalScrollBar().setValue(500)
    dialog.body_scroll.verticalScrollBar().setValue(150)
    before = (dialog.table.verticalScrollBar().value(), dialog.body_scroll.verticalScrollBar().value())
    def close(editor):
        editor.reject()
        return editor.result()
    monkeypatch.setattr(PersonalVisualQuestionDialog, "exec", close)
    QTest.keyClick(dialog.table, Qt.Key.Key_Return)
    tasks.flush()
    qt_app.processEvents()
    assert dialog.records[dialog.table.currentRow()]["key"] == "large-501"
    assert (dialog.table.verticalScrollBar().value(), dialog.body_scroll.verticalScrollBar().value()) == before
    dialog.search.setText("合成大量题-654")
    dialog._filter_rows()
    assert [row["key"] for row in dialog.records] == ["large-654"]
    dialog.reject()


@pytest.mark.parametrize("width,height", [(1080, 960), (420, 700), (360, 560)])
def test_visual_controls_fit_wide_and_narrow_and_are_reachable(visual_progress, qt_app, width, height):
    dialog, _, _ = visual_progress
    dialog.resize(width, height)
    for _ in range(5):
        qt_app.processEvents()
    assert dialog.width() == width and dialog.body_scroll.horizontalScrollBar().maximum() == 0
    for widget in (dialog.bank_tabs, dialog.search, dialog.source_filter, dialog.todo_filter,
                   dialog.protection_filter, dialog.theme_filter, dialog.table, dialog.open_button,
                   dialog.refresh_button, dialog.save, dialog.close_button):
        dialog.body_scroll.ensureWidgetVisible(widget, 0, 0)
        qt_app.processEvents()
        origin = widget.mapTo(dialog.body_scroll.viewport(), QPoint(0, 0))
        assert origin.x() >= 0 and origin.x() + widget.width() <= dialog.body_scroll.viewport().width()
        if widget is not dialog.table:
            assert origin.y() < dialog.body_scroll.viewport().height() and origin.y() + widget.height() > 0


def test_export_keeps_current_snapshot_and_local_privacy_message(visual_progress, monkeypatch, tmp_path):
    import json
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_progress_dialog import QFileDialog
    dialog, _, tasks = visual_progress
    path = tmp_path / "synthetic-progress.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), "JSON"))
    dialog.save.click()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["visual"]["rows"] and payload["word"]["rows"]
    assert "来源名称与题面摘要" in dialog.status.text() and "公开仓库" in dialog.status.text()
    dialog.refresh()
    dialog.cancel_button.click()
    dialog.save.click()
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    tasks.flush()

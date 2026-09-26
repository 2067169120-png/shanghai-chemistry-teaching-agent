from types import SimpleNamespace

from test_preparation_revision_ui import HeldTasks
from test_preparation_structure import grouped_candidate
from test_preparation_text_revision import revision_candidate

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_revision_dialog import (
    PreparationRevisionDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_structure_widget import (
    PreparationStructureWidget,
)

pytest_plugins = ("test_desktop_ui",)


def test_activity_selection_is_explicit_and_alignment_previews(qt_app):
    view = PreparationStructureWidget(grouped_candidate())
    view.link_button.click()
    assert not view.operations and "请选择" in view.status.text()
    view.activity.setCurrentIndex(1)
    view.link_button.click()
    view.align_button.click()
    assert [op["kind"] for op in view.operations] == [
        "link_slide_activity",
        "align_timing",
    ]
    assert "活动10分钟 / 明确关联PPT 10分钟" in view.preview.toPlainText()
    view.undo_button.click()
    assert len(view.operations) == 1
    view.clear_button.click()
    assert not view.operations
    view.close()


def test_table_notes_are_blank_and_do_not_contain_source_answers(qt_app):
    view = PreparationStructureWidget(revision_candidate())
    view.slide.setCurrentIndex(1)
    view.activity.setCurrentIndex(1)
    view.table_notes.click()
    operation = view.operations[0]
    assert operation["section"]["columns"] == ["维度", "甲", "乙"]
    assert operation["section"]["row_labels"] == ["定义", "条件"]
    assert "甲的定义" not in str(operation)
    view.close()


def test_structure_only_save_calls_local_endpoint_and_failure_keeps_operations(qt_app):
    calls, tasks = [], HeldTasks()
    facade = SimpleNamespace(
        revise_preparation=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or SimpleNamespace(status="completed")
        )
    )
    view = PreparationRevisionDialog(
        facade,
        tasks,
        {
            "task_id": "parent",
            "source_revision": "a" * 64,
            "candidate": grouped_candidate(),
        },
    )
    view.structure.activity.setCurrentIndex(1)
    view.structure.heading.setText("概念辨析")
    view.structure.prompt.setPlainText("写出条件与反例。")
    view.structure.add_notes.click()
    assert view.save_button.isEnabled() and not view.pending
    view._save()
    assert view.busy
    tasks.failure("private")
    assert view.structure.operations and view.save_button.isEnabled()
    view._save()
    tasks.success(tasks.operation())
    assert calls[0][0] == ("parent", "a" * 64, [])
    assert calls[0][1]["structure_edits"][0]["kind"] == "append_worksheet"
    assert not view.structure.operations
    view.close()

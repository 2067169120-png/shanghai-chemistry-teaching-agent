from copy import deepcopy
from types import SimpleNamespace

from PySide6.QtCore import Qt
from test_preparation_revision_ui import HeldTasks
from test_preparation_sequence import sequence_candidate
from test_preparation_structure import notes_operation
from test_preparation_text_revision import revision_candidate

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_revision_dialog import (
    PreparationRevisionDialog,
)

pytest_plugins = ("test_desktop_ui",)


def view_for(candidate):
    tasks = HeldTasks()
    view = PreparationRevisionDialog(
        SimpleNamespace(),
        tasks,
        {
            "task_id": "old",
            "source_revision": "a" * 64,
            "candidate": candidate,
        },
    )
    return view, tasks


def select(view, ids):
    listing = view.sequence.pages_list
    listing.clearSelection()
    for index in range(listing.count()):
        item = listing.item(index)
        item.setSelected(item.data(Qt.ItemDataRole.UserRole) in ids)


def close(view):
    view.pending.clear()
    view.structure.clear()
    view.close()


def test_move_contiguous_block_undo_and_protect_cover(qt_app):
    view, _ = view_for(sequence_candidate())
    sequence = view.sequence
    assert not sequence.up_button.isEnabled() and not sequence.move_button.isEnabled()
    select(view, ["S02", "S04"])
    assert not sequence.move_button.isEnabled() and "相邻" in sequence.status.text()
    select(view, ["S02", "S03"])
    assert sequence.down_button.isEnabled() and not sequence.up_button.isEnabled()
    sequence.down_button.click()
    assert [page["id"] for page in sequence.pages] == [
        "S01",
        "S04",
        "S02",
        "S03",
        "S05",
        "S06",
    ]
    assert sequence._selected_indexes() == [2, 3]
    assert "组内顺序" in view.structure.preview.toPlainText()
    sequence.up_button.click()
    assert [page["id"] for page in sequence.pages] == [
        "S01",
        "S02",
        "S03",
        "S04",
        "S05",
        "S06",
    ]
    sequence.undo_button.click()
    assert sequence.pages[1]["id"] == "S04"
    sequence.undo_button.click()
    assert sequence.pages[1]["id"] == "S02" and not view.structure.operations
    close(view)


def test_move_then_edit_uses_stable_id_and_preview_includes_pending_text(qt_app):
    view, _ = view_for(sequence_candidate())
    select(view, ["S02"])
    view.sequence.destination.setCurrentIndex(view.sequence.destination.findData(None))
    view.sequence.move_button.click()
    view.sequence.edit_button.click()
    assert view.tabs.currentWidget() is view.edit_page
    path = ("slides", 1, "title")
    view.editors[path].setPlainText("已调整标题")
    assert view.sequence.pages[-1]["title"] == "已调整标题"
    assert view.sequence.pages[-1]["id"] == "S02"
    assert (
        view.structure.slide.itemText(view.structure.slide.findData("S02"))
        == "6 · 已调整标题"
    )
    assert view.sequence.pages[1]["student_text"] == "页面3\n\n学生正文3"
    assert view.pending == {path: "已调整标题"}
    close(view)


def test_new_split_page_appears_and_can_move_without_losing_edited_body(qt_app):
    candidate = sequence_candidate()
    candidate["slides"][1]["image"] = {"asset_id": "test", "observation_prompt": "观察"}
    view, _ = view_for(candidate)
    view.structure.slide.setCurrentIndex(view.structure.slide.findData("S02"))
    view.structure.split_button.click()
    assert view.structure.slide.findData("SLOCAL001") >= 0
    select(view, ["SLOCAL001"])
    assert not view.sequence.edit_button.isEnabled()
    assert "先另存" in view.sequence.status.text()
    view.sequence.down_button.click()
    original = next(f for f in view.fields if f["path"] == ["slides", 1, "content", 0])
    view.group.setCurrentText(original["group"])
    view.editors[tuple(original["path"])].setPlainText("完整原题已修订")
    page = next(page for page in view.sequence.pages if page["id"] == "SLOCAL001")
    assert "完整原题已修订" in page["student_text"]
    assert view.structure.operations[1]["slide_ids"] == ["SLOCAL001"]
    close(view)


def test_worksheet_preview_edit_errors_recover_without_stale_data_or_lost_operations(
    qt_app,
):
    view, _ = view_for(revision_candidate())
    view.structure._queue(notes_operation())
    operation = deepcopy(view.structure.operations)
    path = ["activities", 0, "worksheet", "title"]
    field = next(f for f in view.fields if f["path"] == path)
    view.group.setCurrentText(field["group"])
    view.editors[tuple(path)].setPlainText("")
    assert view.structure.preview_error and not view.sequence.pages
    assert "未显示旧稿" in view.sequence.status.text()
    assert not view.sequence.move_button.isEnabled()
    view.editors[tuple(path)].setPlainText("修订后的笔记表")
    assert not view.structure.preview_error and view.sequence.pages
    assert view.structure.operations == operation
    select(view, ["S02"])
    assert "修订后的笔记表" in view.sequence.links_text.toPlainText()
    assert "甲的定义" in view.sequence.student_text.toPlainText()
    assert (
        "化学事实和例题数据使用前由教师复核"
        not in view.sequence.student_text.toPlainText()
    )
    close(view)


def test_narrow_layout_stacks_and_move_only_save_keeps_failure_state(qt_app):
    view, tasks = view_for(sequence_candidate())
    calls = []
    view.facade.revise_preparation = lambda *a, **kw: (
        calls.append((a, kw)) or {"status": "completed"}
    )
    view.show()
    view.resize(460, 800)
    qt_app.processEvents()
    assert view.sequence.splitter.orientation() == Qt.Orientation.Vertical
    assert (
        view.sequence.pages_list.geometry().bottom()
        < view.sequence.up_button.geometry().top()
    )
    assert view.sequence.scroll.verticalScrollBar().maximum() > 0
    select(view, ["S05"])
    view.sequence.up_button.click()
    view._save()
    assert view.busy
    tasks.failure("private")
    assert view.structure.operations and view.save_button.isEnabled()
    view._save()
    tasks.success(tasks.operation())
    assert calls[0][0] == ("old", "a" * 64, [])
    assert calls[0][1]["structure_edits"] == [
        {"kind": "move_slides", "slide_ids": ["S05"], "before_slide_id": "S04"}
    ]
    assert not view.structure.operations
    view.close()

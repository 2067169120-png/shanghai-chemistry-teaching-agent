from __future__ import annotations

from copy import deepcopy

import pytest


class Tasks:
    def submit(self, label, operation, *, on_success, on_failure):
        try:
            value = operation()
        except Exception as exc:
            on_failure(str(exc))
        else:
            on_success(value)


class Facade:
    def __init__(self):
        self.draft = None
        self.history = []
        self.export_calls = 0

    def handout_practice_state(self):
        return {"draft": deepcopy(self.draft), "history": deepcopy(self.history)}

    def save_handout_practice(self, title, selections, answer_lines):
        self.draft = {
            "title": title,
            "selections": deepcopy(selections),
            "answer_lines": answer_lines,
        }
        return self.draft

    def export_handout_practice(self, title, selections, answer_lines):
        self.export_calls += 1
        self.save_handout_practice(title, selections, answer_lines)
        result = {
            "export_id": "fixture-export",
            "title": title,
            "items": selections,
            "created_at": "2026-09-08T10:00:00Z",
        }
        self.history.insert(0, result)
        return result


@pytest.fixture
def fixture(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.handout_practice_dialog import (
        HandoutPracticeDialog,
    )

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    facade = Facade()
    catalog = [
        {
            "key": key,
            "revision": "a" * 64,
            "title": "原生题组 第" + key + "题",
            "source_name": "较长的化学讲义来源文件名称（原卷版）.docx",
        }
        for key in ("1", "2", "3")
    ]
    selection = [{"key": item["key"], "revision": item["revision"]} for item in catalog]
    dialog = HandoutPracticeDialog(facade, Tasks(), selection, catalog)
    yield app, facade, dialog, catalog
    dialog._busy = False
    dialog._dirty = False
    dialog.close()
    app.processEvents()


def test_order_remove_save_restore_and_export_history(fixture):
    app, facade, dialog, catalog = fixture
    dialog.items.setCurrentRow(0)
    dialog.down.click()
    assert [s["key"] for s in dialog._selections] == ["2", "1", "3"]
    dialog.remove.click()
    assert [s["key"] for s in dialog._selections] == ["2", "3"]
    dialog.title.setText("离子反应随堂练习")
    dialog.lines.setValue(4)
    dialog.save.click()
    assert not dialog._dirty and facade.draft["answer_lines"] == 4
    dialog.remove.click()
    dialog.restore.click()
    assert len(dialog._selections) == 2 and not dialog._dirty
    dialog.export.click()
    assert facade.export_calls == 1 and dialog.student.isEnabled()
    assert not dialog._busy and not dialog._dirty
    assert dialog.history.currentData() == "fixture-export"
    assert "Word" in dialog.status.text()
    from integrations.deeptutor_shchem_v1.desktop_workbench.handout_practice_dialog import (
        HandoutPracticeDialog,
    )

    reopened = HandoutPracticeDialog(facade, Tasks(), [], catalog)
    assert reopened.restore.isEnabled() and reopened.student.isEnabled()
    reopened.restore.click()
    assert reopened.title.text() == "离子反应随堂练习"
    assert len(reopened._selections) == 2
    reopened.close()


def test_dirty_edits_and_inflight_write_prevent_close(fixture, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    app, facade, dialog, catalog = fixture
    dialog.show()
    app.processEvents()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    assert not dialog.close() and not dialog._closed
    pending = []

    class Delayed:
        def submit(self, label, operation, **callbacks):
            pending.append((operation, callbacks))

    dialog.tasks = Delayed()
    dialog.export.click()
    assert dialog._busy and not dialog.editor.isEnabled()
    dialog.export.click()
    assert len(pending) == 1 and not dialog.close()
    pending[0][1]["on_failure"]("模拟磁盘不可写")
    assert not dialog._busy and dialog.editor.isEnabled() and dialog._dirty
    assert "磁盘" in dialog.status.text()


def test_narrow_dialog_fits_without_horizontal_overflow(fixture):
    app, _, dialog, _ = fixture
    dialog.resize(420, 900)
    dialog.show()
    app.processEvents()
    assert dialog.width() == 420
    for widget in (
        dialog.title,
        dialog.items,
        dialog.export,
        dialog.student,
        dialog.teacher,
        dialog.history,
    ):
        position = widget.mapTo(dialog, widget.rect().topLeft())
        assert position.x() >= 0 and position.x() + widget.width() <= dialog.width()
    assert dialog.items.horizontalScrollBar().maximum() == 0


def test_stale_draft_keeps_missing_items_visible_instead_of_silently_dropping(fixture):
    _, facade, dialog, _ = fixture
    dialog._saved_draft = {
        "title": "旧练习",
        "selections": [{"key": "missing", "revision": "a"}],
        "answer_lines": 2,
    }
    dialog._restore()
    assert len(dialog._selections) == 1
    assert "不可用" in dialog.items.item(0).text()

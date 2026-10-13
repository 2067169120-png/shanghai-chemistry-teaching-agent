"""Complete native entry paths, explicitly previewed on isolated profiles."""
from copy import deepcopy
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QDialog
from test_desktop_studio_ui import window, settle
from integrations.deeptutor_shchem_v1.desktop_lesson_probe import sample_payload
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_recovery import apply_editor_payload
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.teaching_template_dialogs import (
    ApplyTeachingTemplateDialog, SaveTeachingTemplateDialog, TeachingTemplateLibraryDialog)
from integrations.deeptutor_shchem_v1.desktop_teaching_templates import TeachingTemplateService, capture_structure


@pytest.fixture
def desk(window):
    win, app = window
    page = win.preparation_page
    apply_editor_payload(page, sample_payload())
    dialog = LessonDesignDialog(page); dialog.show(); settle(app)
    yield win, dialog, page, app
    dialog.reject(); dialog.deleteLater(); settle(app)


def test_save_preview_cancel_and_selected_capture_do_not_change_original_lesson(desk, monkeypatch):
    win, dialog, page, app = desk
    before = deepcopy(page._payload()); state_before = win.facade.state_store.snapshot()
    monkeypatch.setattr(SaveTeachingTemplateDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert not dialog.save_personal_teaching_template()
    assert win.facade.state_store.snapshot() == state_before and page._payload() == before
    def accepted(preview):
        assert "仅教师答案" not in preview.preview.toPlainText()
        preview.name.setText("我的问题导学结构")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(SaveTeachingTemplateDialog, "exec", accepted)
    dialog.save_structure_template.click(); settle(app)
    records = TeachingTemplateService(win.facade.state_store).search("问题导学")
    assert len(records) == 1 and records[0]["structure"]["nodes"]
    assert page._payload() == before


def test_stale_save_preview_preserves_teacher_edits_without_writing_template(desk, monkeypatch):
    win, dialog, page, app = desk
    def edit_during_preview(preview):
        dialog.fields["teacher_action"].setPlainText("教师在预览期间修改的活动")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(SaveTeachingTemplateDialog, "exec", edit_during_preview)
    assert not dialog.save_personal_teaching_template()
    assert TeachingTemplateService(win.facade.state_store).search() == []
    assert dialog.history.value["nodes"][0]["teacher_action"] == "教师在预览期间修改的活动"


def test_apply_preview_cancel_stale_and_single_undo_preserve_original_content(desk, monkeypatch):
    win, dialog, page, app = desk
    service = TeachingTemplateService(win.facade.state_store)
    record = service.save("同一课题复用结构", capture_structure(dialog.history.value))
    before = deepcopy(dialog.history.value); before_payload = deepcopy(page._payload())
    monkeypatch.setattr(ApplyTeachingTemplateDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert not dialog.apply_personal_teaching_template(record)
    assert dialog.history.value == before and page._payload() == before_payload
    def mutate_during_preview(preview):
        dialog.fields["teacher_action"].setPlainText("预览期间教师的新修改")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ApplyTeachingTemplateDialog, "exec", mutate_during_preview)
    assert not dialog.apply_personal_teaching_template(record)
    assert len(dialog.history.value["nodes"]) == len(before["nodes"])
    assert dialog.history.value["nodes"][0]["teacher_action"] == "预览期间教师的新修改"
    before = deepcopy(dialog.history.value)
    monkeypatch.setattr(ApplyTeachingTemplateDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    assert dialog.apply_personal_teaching_template(record)
    assert dialog.history.value["nodes"][:len(before["nodes"])] == before["nodes"]
    assert page.topic.text() == before_payload["topic"] and page.materials.toPlainText() == before_payload["materials"]
    dialog.travel(False); assert dialog.history.value == before


def test_apply_rechecks_live_template_revision_after_preview(desk, monkeypatch):
    win, dialog, page, app = desk
    service = TeachingTemplateService(win.facade.state_store)
    record = service.save("待应用", capture_structure(dialog.history.value))
    before = deepcopy(dialog.history.value)
    def rename_during_preview(preview):
        service.rename(record, "已被另一个窗口修改")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ApplyTeachingTemplateDialog, "exec", rename_during_preview)
    assert not dialog.apply_personal_teaching_template(record)
    assert dialog.history.value == before and "模板已变化" in dialog.status.text()


def test_library_widgets_search_rename_delete_restore_and_read(desk):
    win, dialog, page, app = desk
    service = TeachingTemplateService(win.facade.state_store)
    record = service.save("问题导学新授", capture_structure(dialog.history.value))
    service.save("单元复习", capture_structure(dialog.history.value))
    library = TeachingTemplateLibraryDialog(win.facade, dialog); library.show(); settle(app)
    library.query.setText("问题导学"); settle(app)
    assert library.items.count() == 1 and library.current_record["id"] == record["id"]
    library.name.setText("实验观察结构"); library.rename.click(); settle(app)
    assert service.load(record["id"])["name"] == "实验观察结构"
    library.query.setText("实验观察"); library.remove.click(); settle(app)
    assert library.items.count() == 0
    library.deleted.setChecked(True); settle(app)
    assert library.items.count() == 1 and library.remove.text() == "恢复这个模板"
    library.remove.click(); library.deleted.setChecked(False); settle(app)
    assert library.items.count() == 1 and library.current_record["structure"] == record["structure"]
    library.reject(); library.deleteLater()


def test_template_page_entry_applies_to_existing_preparation(desk, monkeypatch):
    win, dialog, page, app = desk
    record = TeachingTemplateService(win.facade.state_store).save("工作台入口结构", capture_structure(dialog.history.value))
    before = deepcopy(page._payload())
    dialog.reject()
    monkeypatch.setattr(ApplyTeachingTemplateDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(LessonDesignDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    def use_current(library):
        library.query.setText("工作台入口")
        assert library.current_record["id"] == record["id"] and library.apply.isEnabled()
        library.apply_current()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(TeachingTemplateLibraryDialog, "exec", use_current)
    win.navigate("templates"); win.template_page.my_templates.click(); settle(app)
    assert win.stack.currentWidget() is page
    after = page._payload()
    assert after["topic"] == before["topic"] and after["materials"] == before["materials"]
    assert after.get("image_assets", []) == before.get("image_assets", [])
    assert len(after["lesson_design"]["nodes"]) == 2 * len(before["lesson_design"]["nodes"])


def test_cancelled_template_in_empty_new_preparation_does_not_create_design(window, monkeypatch):
    win, app = window; page = win.preparation_page
    record = TeachingTemplateService(win.facade.state_store).save("空课复用", capture_structure(sample_payload()["lesson_design"]))
    assert page._lesson_design is None
    before = deepcopy(page._payload())
    monkeypatch.setattr(ApplyTeachingTemplateDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert not page.apply_personal_teaching_template(record)
    assert page._payload() == before and page._lesson_design is None


def test_real_nested_modal_route_from_my_templates_to_preview_and_lesson_editor(desk):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    win, old_dialog, page, app = desk
    TeachingTemplateService(win.facade.state_store).save("真实窗口路径", capture_structure(old_dialog.history.value))
    before = deepcopy(page._payload()); old_dialog.reject()
    seen = []; attempts = [0]
    def operate():
        attempts[0] += 1
        active = QApplication.activeModalWidget()
        if isinstance(active, TeachingTemplateLibraryDialog) and "library" not in seen:
            seen.append("library"); active.query.setText("真实窗口路径")
            QTimer.singleShot(25, operate); active.apply.click()
        elif isinstance(active, ApplyTeachingTemplateDialog):
            seen.append("preview"); QTimer.singleShot(25, operate); active.accept()
        elif isinstance(active, LessonDesignDialog):
            seen.append("editor"); active.return_button.click()
        elif attempts[0] < 50:
            QTimer.singleShot(25, operate)
        else:
            if active is not None: active.reject()
    win.navigate("templates"); QTimer.singleShot(25, operate)
    win.template_page.my_templates.click(); settle(app)
    assert seen == ["library", "preview", "editor"]
    assert win.stack.currentWidget() is page
    after = page._payload()
    assert after["topic"] == before["topic"] and after["materials"] == before["materials"]
    assert after.get("image_assets", []) == before.get("image_assets", [])
    assert after["lesson_design"]["nodes"][:len(before["lesson_design"]["nodes"])] == before["lesson_design"]["nodes"]

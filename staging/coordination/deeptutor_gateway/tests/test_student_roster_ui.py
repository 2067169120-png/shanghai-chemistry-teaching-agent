"""Actual local roster and exam dialogs with isolated synthetic storage."""
from copy import deepcopy
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QInputDialog, QLineEdit, QMessageBox

from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError, digest
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_student_roster import DesktopStudentRosterStore, new_member, new_roster
from integrations.deeptutor_shchem_v1.desktop_workbench.exam_dashboard import ExamDashboard
from integrations.deeptutor_shchem_v1.desktop_workbench.student_roster_dialog import StudentRosterDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from test_student_roster import private_service


def settle(app, predicate=lambda: True):
    deadline = time.monotonic() + 10
    for _ in range(3):
        app.processEvents()
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def roster_facade(tmp_path):
    profiles = [SimpleNamespace(student_id='anonymous-A', label_zh='匿名档案A', grade='高三')]
    store = DesktopStudentRosterStore(tmp_path / 'private-students', profiles=lambda: profiles)
    return SimpleNamespace(student_roster_store=lambda: store, student_profiles=lambda: tuple(profiles)), store


@pytest.fixture
def editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    facade, store = roster_facade(tmp_path)
    dialog = StudentRosterDialog(facade)
    dialog.show()
    settle(app)
    yield dialog, app, facade, store
    dialog.dirty = False
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def create_class(dialog, monkeypatch, label='教师本地班级'):
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: (label, True))
    dialog.new_button.click()
    assert dialog.draft is not None and dialog.dirty


def add_member(dialog, alias='教师本地别名'):
    dialog.alias.setText(alias)
    dialog.add_button.click()
    return deepcopy(dialog.draft['members'][-1])


def test_native_new_save_and_reopen_keeps_same_member_identity(editor, monkeypatch):
    dialog, app, facade, store = editor
    create_class(dialog, monkeypatch)
    member = add_member(dialog)
    assert not store.path.exists()
    dialog.save_button.click()
    settle(app)
    saved = store.load(dialog.draft['id'])
    assert dialog.saved and not dialog.dirty
    assert saved['members'][0]['id'] == member['id']
    reopened = StudentRosterDialog(facade)
    try:
        reopened.show()
        settle(app)
        assert reopened.rosters.currentData() == saved['id']
        assert reopened.members.item(0, 0).text() == member['local_alias']
        assert reopened.members.item(0, 1).text() == member['id']
        assert reopened.draft == saved
    finally:
        reopened.dirty = False
        reopened.close()
        reopened.deleteLater()


def test_same_alias_and_explicit_profile_choice_do_not_merge_or_infer_profile(editor, monkeypatch):
    dialog, _, _, store = editor
    create_class(dialog, monkeypatch)
    dialog.profile.setCurrentIndex(dialog.profile.findData('anonymous-A'))
    first = add_member(dialog, '同名学生')
    second = add_member(dialog, '同名学生')
    assert first['id'] != second['id'] and dialog.members.rowCount() == 2
    assert all(member['profile_student_id'] is None for member in dialog.draft['members'])
    dialog.members.selectRow(0)
    dialog.alias.setText('本地改名')
    dialog.profile.setCurrentIndex(dialog.profile.findData('anonymous-A'))
    dialog.edit_button.click()
    dialog.save_button.click()
    saved = store.load(dialog.draft['id'])
    assert saved['members'][0]['id'] == first['id']
    assert saved['members'][0]['local_alias'] == '本地改名'
    assert saved['members'][0]['profile_student_id'] == 'anonymous-A'
    assert saved['members'][1]['id'] == second['id'] and saved['members'][1]['profile_student_id'] is None


def test_unsaved_close_cancel_keeps_input_then_discard_creates_no_file(editor, monkeypatch):
    dialog, _, _, store = editor
    create_class(dialog, monkeypatch)
    add_member(dialog)
    before = deepcopy(dialog.draft)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.No)
    dialog.reject()
    assert dialog.isVisible() and dialog.dirty and dialog.draft == before
    assert not store.path.exists()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected and not dialog.isVisible()
    assert not store.path.exists()


def test_cancel_new_class_does_not_create_a_draft_or_write(editor, monkeypatch):
    dialog, _, _, store = editor
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('放弃的班级', False))
    dialog.new_button.click()
    assert dialog.draft is None and not dialog.dirty and not store.path.exists()


def test_roster_save_failure_retains_unsaved_input_and_existing_file(editor, monkeypatch):
    dialog, _, _, store = editor
    create_class(dialog, monkeypatch)
    add_member(dialog)
    dialog.save_button.click()
    before_file = store.path.read_bytes()
    dialog.members.selectRow(0)
    dialog.alias.setText('未成功保存的新别名')
    dialog.edit_button.click()
    before_draft = deepcopy(dialog.draft)
    def failed_save(*args, **kwargs):
        raise ExamError('合成保存失败：当前输入保留')
    monkeypatch.setattr(store, 'save', failed_save)
    dialog.save_button.click()
    assert dialog.isVisible() and dialog.dirty and dialog.draft == before_draft
    assert dialog.members.item(0, 0).text() == '未成功保存的新别名'
    assert '当前输入保留' in dialog.status.text() and store.path.read_bytes() == before_file


def test_stale_native_editor_cannot_overwrite_another_window_roster(editor, monkeypatch):
    dialog, _, _, store = editor
    create_class(dialog, monkeypatch)
    add_member(dialog)
    dialog.save_button.click()
    old = deepcopy(dialog.draft)
    newer = deepcopy(old)
    newer['label'] = '另一窗口的最新班级'
    other = DesktopStudentRosterStore(store.root)
    latest = other.save(newer, expected_revision=old['revision'])
    dialog.members.selectRow(0)
    dialog.alias.setText('旧窗口保留输入')
    dialog.edit_button.click()
    before = deepcopy(dialog.draft)
    dialog.save_button.click()
    assert dialog.dirty and dialog.draft == before
    assert '未覆盖' in dialog.status.text()
    assert store.load(old['id']) == latest


def test_cancel_roster_switch_retains_draft_and_original_selection(editor, monkeypatch):
    dialog, _, _, store = editor
    first = new_roster('已存班级甲')
    first['members'] = [new_member('甲成员')]
    first = store.save(first)
    second = new_roster('已存班级乙')
    second['members'] = [new_member('乙成员')]
    second = store.save(second)
    dialog.refresh(first['id'])
    dialog.members.selectRow(0)
    dialog.alias.setText('尚未保存的甲成员别名')
    dialog.edit_button.click()
    before = deepcopy(dialog.draft)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.No)
    dialog.rosters.setCurrentIndex(dialog.rosters.findData(second['id']))
    assert dialog.rosters.currentData() == first['id'] and dialog.draft == before and dialog.dirty
    assert store.load(first['id']) == first and store.load(second['id']) == second


@pytest.fixture
def dashboard(tmp_path):
    app = QApplication.instance() or QApplication([])
    facade, roster_store = roster_facade(tmp_path)
    facade.paths = SimpleNamespace(state_root=tmp_path / 'exam-state')
    facade.list_provider_profiles = lambda: ()
    facade.student_submissions = lambda **kwargs: ()
    roster = new_roster('教师确认名册')
    roster['members'] = [new_member('明确成员甲'), new_member('明确成员乙')]
    roster = roster_store.save(roster)
    original = example(tmp_path / 'synthetic.xlsx')[2]
    tasks = DesktopTaskBridge()
    desk = ExamDashboard(facade, tasks)
    desk.accept_exam(original)
    desk.show()
    settle(app)
    yield desk, app, roster, roster_store, original
    for child in desk.findChildren(QDialog):
        child.close()
    tasks.wait_for_done(5000)
    desk.dirty = False
    desk.close()
    desk.deleteLater()
    tasks.shutdown()
    app.processEvents()


def attach_through_button(desk, monkeypatch):
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args, **kwargs: (args[3][0], True))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('教师逐项确认本次班级与名册', True))
    desk.tabs.setCurrentIndex(2)
    desk.attach_roster_button.click()
    assert len(desk.exam['roster_snapshots']) == 1


def save_dialog(dialog):
    buttons = dialog.findChild(QDialogButtonBox)
    assert buttons is not None
    buttons.button(QDialogButtonBox.StandardButton.Save).click()


def choose_member(dialog, identity):
    index = next(i for i in range(1, dialog.member.count()) if dialog.member.itemData(i)['id'] == identity)
    dialog.member.setCurrentIndex(index)


def test_exam_toolbar_opens_real_local_roster_without_changing_exam(dashboard):
    desk, app, _, store, _ = dashboard
    before, disk = deepcopy(desk.bundle()), store.path.read_bytes()
    opened = []
    def close_modal():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, StudentRosterDialog)
        opened.append(dialog.rosters.count())
        dialog.reject()
    QTimer.singleShot(10, close_modal)
    desk.roster_button.click()
    settle(app)
    assert opened == [1] and desk.bundle() == before and store.path.read_bytes() == disk
    assert desk.empty_roster_button.isEnabled()


def test_explicit_binding_button_saves_and_reopens_frozen_member_in_same_exam(dashboard, monkeypatch):
    desk, app, roster, _, original = dashboard
    attach_through_button(desk, monkeypatch)
    desk.classes.setCurrentIndex(desk.classes.findData('合成班A'))
    assert desk.report['overall']['n'] == 0 and desk.report['overall']['unbound'] == 6
    desk.bind_student_button.click()
    settle(app)
    dialog = desk._identity_dialog
    dialog.row.setCurrentIndex(dialog.row.findData('S0001'))
    assert dialog.member.currentData() is None
    choose_member(dialog, roster['members'][0]['id'])
    before = desk.store.load(desk.exam['id'])
    save_dialog(dialog)
    assert dialog.isVisible() and '依据' in dialog.status.text()
    assert desk.store.load(desk.exam['id']) == before
    dialog.reason.setPlainText('逐行核对原表与明确选择的稳定成员')
    save_dialog(dialog)
    settle(app)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert desk.report['overall']['n'] == 1 and desk.report['overall']['enrolled'] == 2
    bound = next(value for value in desk.exam['students'] if value['id'] == 'S0001')
    assert bound['identity_binding']['member_id'] == roster['members'][0]['id']
    assert bound['scores'] == original['students'][0]['scores'] and bound['excel_row'] == original['students'][0]['excel_row']
    frozen = deepcopy(desk.bundle())
    desk.open_saved(desk.exam['id'])
    settle(app)
    assert desk.bundle() == frozen and desk.exam['roster_snapshots'][0]['roster'] == roster


def test_binding_dialog_cancel_keeps_exam_and_saved_bundle(dashboard, monkeypatch):
    desk, app, roster, _, _ = dashboard
    attach_through_button(desk, monkeypatch)
    before = deepcopy(desk.bundle())
    saved = desk.store.load(desk.exam['id'])
    desk.bind_student_button.click()
    settle(app)
    dialog = desk._identity_dialog
    dialog.row.setCurrentIndex(dialog.row.findData('S0001'))
    choose_member(dialog, roster['members'][0]['id'])
    dialog.reason.setPlainText('取消前的教师核对输入')
    dialog.reject()
    assert desk.bundle() == before and desk.store.load(desk.exam['id']) == saved


def test_binding_save_failure_keeps_input_and_window_without_claiming_saved(dashboard, monkeypatch):
    desk, app, roster, _, _ = dashboard
    attach_through_button(desk, monkeypatch)
    before = deepcopy(desk.bundle())
    saved = desk.store.load(desk.exam['id'])
    desk.bind_student_button.click()
    settle(app)
    dialog = desk._identity_dialog
    dialog.row.setCurrentIndex(dialog.row.findData('S0001'))
    choose_member(dialog, roster['members'][0]['id'])
    dialog.reason.setPlainText('保存失败也须保留的核对输入')
    def fail_save(*args, **kwargs):
        raise ExamError('合成考试保存失败，输入保留')
    monkeypatch.setattr(desk.store, 'save', fail_save)
    save_dialog(dialog)
    assert dialog.isVisible() and dialog.reason.toPlainText() == '保存失败也须保留的核对输入'
    assert '保存失败' in dialog.status.text()
    assert desk.bundle() == before and desk.store.load(desk.exam['id']) == saved
    dialog.reject()


def test_stale_confirmation_dialog_cannot_overwrite_another_exam_update(dashboard, monkeypatch):
    desk, app, roster, _, _ = dashboard
    attach_through_button(desk, monkeypatch)
    desk.bind_student_button.click()
    settle(app)
    dialog = desk._identity_dialog
    dialog.row.setCurrentIndex(dialog.row.findData('S0001'))
    choose_member(dialog, roster['members'][0]['id'])
    dialog.reason.setPlainText('旧确认窗口仍保留输入')
    latest = desk.store.load(desk.exam['id'])
    latest['notes'] = '另一窗口的最新说明'
    desk.store.save(latest, expected_revision=digest(desk.store.load(desk.exam['id'])))
    before = deepcopy(desk.bundle())
    save_dialog(dialog)
    assert dialog.isVisible() and desk.bundle() == before
    assert desk.store.load(desk.exam['id']) == latest
    assert '未覆盖' in dialog.status.text()
    dialog.reject()


def test_whole_class_attendance_requires_explicit_scope_and_keeps_other_class_scores(dashboard, monkeypatch):
    desk, app, _, _, original = dashboard
    attach_through_button(desk, monkeypatch)
    desk.classes.setCurrentIndex(desk.classes.findData('合成班A'))
    desk.attendance_button.click()
    settle(app)
    dialog = desk._attendance_dialog
    before = deepcopy(desk.bundle())
    dialog.status_choice.setCurrentIndex(dialog.status_choice.findData('absent'))
    dialog.reason.setPlainText('明确核对本次名册整班缺考')
    save_dialog(dialog)
    assert dialog.isVisible() and desk.bundle() == before and '范围' in dialog.message.text()
    dialog.target.setCurrentIndex(dialog.target.findData(None))
    save_dialog(dialog)
    settle(app)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert desk.report['overall']['absent'] == desk.report['overall']['enrolled'] == 2
    assert desk.report['overall']['n'] == 0
    class_b = [value for value in desk.exam['students'] if value['class'] == '合成班B']
    assert class_b == [value for value in original['students'] if value['class'] == '合成班B']
    assert all(value['total'] is None for value in desk.report['students'])


def test_empty_roster_exam_button_requires_inputs_then_saves_and_reopens_zero_population(dashboard, monkeypatch):
    desk, app, roster, _, original = dashboard
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args, **kwargs: (args[3][0], True))
    original_bundle = deepcopy(desk.bundle())
    desk.empty_roster_button.click()
    settle(app)
    dialog = desk._empty_roster_dialog
    save_dialog(dialog)
    assert dialog.isVisible() and desk.bundle() == original_bundle
    text_fields = [field for field in dialog.findChildren(QLineEdit) if field.parentWidget() is dialog]
    assert len(text_fields) == 2
    text_fields[0].setText('全班尚未录入的合成考试')
    text_fields[1].setText('教师明确选定已保存的本地名册')
    save_dialog(dialog)
    settle(app)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert desk.exam['id'] != original['id'] and desk.exam['roster_snapshots'][0]['roster'] == roster
    assert desk.exam['source']['source_sha256'] is None and desk.exam['questions'] == []
    overall = desk.report['overall']
    assert overall['enrolled'] == overall['not_entered'] == 2 and overall['n'] == 0
    assert overall['mean'] is overall['pass_rate'] is overall['high_rate'] is None
    frozen = deepcopy(desk.bundle())
    desk.open_saved(desk.exam['id'])
    assert desk.bundle() == frozen
    desk.empty_roster_button.click()
    settle(app)
    desk._empty_roster_dialog.reject()
    assert desk.bundle() == frozen


def test_capture_actual_roster_and_exam_identity_dialogs(dashboard, monkeypatch, tmp_path, request):
    """Human visual review captures are synthetic and stay in the E: run root."""
    desk, app, roster, _, _ = dashboard
    previous_font = QFont(app.font())
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    assert font_id >= 0, 'existing system Chinese font could not be loaded for visual review'
    families = QFontDatabase.applicationFontFamilies(font_id)
    assert families
    app.setFont(QFont(families[0], 10))
    request.addfinalizer(lambda: app.setFont(previous_font))
    output = Path(os.environ.get('SHCHEM_ROSTER_QA_ROOT', str(tmp_path / 'visual-qa')))
    output.mkdir(parents=True, exist_ok=True)
    editor = StudentRosterDialog(desk.facade, desk)
    try:
        editor.resize(760, 600)
        editor.show()
        settle(app)
        assert editor.grab().save(str(output / 'roster-dialog-760x600.png'))
    finally:
        editor.close()
        editor.deleteLater()
    attach_through_button(desk, monkeypatch)
    desk.classes.setCurrentIndex(desk.classes.findData('合成班A'))
    desk.apply_identity_binding('S0001', roster['members'][0]['id'], None,
                                '合成视觉核对：确认原成绩所属成员', expected=digest(desk.bundle()))
    desk.apply_attendance_status('S0001', 'exempt', '合成视觉核对：确认免考状态', expected=digest(desk.bundle()))
    desk.resize(1000, 720)
    desk.tabs.setCurrentIndex(2)
    settle(app)
    assert desk.grab().save(str(output / 'exam-roster-status-1000x720.png'))
    desk.bind_student_button.click()
    settle(app)
    binding = desk._identity_dialog
    binding.row.setCurrentIndex(binding.row.findData('S0001'))
    binding.reason.setPlainText('逐项确认稳定成员、匿名档案与原作答；取消保留当前考试。')
    binding.resize(680, 460)
    settle(app)
    assert binding.grab().save(str(output / 'exam-binding-dialog-680x460.png'))
    binding.reject()
    desk.attendance_button.click()
    settle(app)
    attendance = desk._attendance_dialog
    attendance.target.setCurrentIndex(attendance.target.findData(None))
    attendance.status_choice.setCurrentIndex(attendance.status_choice.findData('not_entered'))
    attendance.reason.setPlainText('合成整班状态核对：尚无本次成绩，未录入不记零分。')
    attendance.resize(560, 390)
    settle(app)
    assert attendance.grab().save(str(output / 'exam-attendance-dialog-560x390.png'))
    attendance.reject()


def test_student_analysis_page_opens_roster_and_shows_only_explicit_local_identity(private_service):
    from integrations.deeptutor_shchem_v1.desktop_workbench.student_page import StudentPage
    service, _, _ = private_service
    app = QApplication.instance() or QApplication([])
    profile = service.create_student_profile(grade='高三', retention_days=30, consent_recorded=True)
    draft = new_roster('本地分析班级')
    member = new_member('显式本地分析别名')
    member['profile_student_id'] = profile.student_id
    draft['members'] = [member]
    service.save_student_roster(draft)
    tasks = DesktopTaskBridge()
    page = StudentPage(service, tasks)
    try:
        page.show()
        settle(app, lambda: page._context_task_id is None and page.student_combo.count() == 1)
        page._refresh_local_identity()
        assert page.roster_button.isEnabled()
        assert '本地分析班级' in page.local_identity_label.text()
        assert '显式本地分析别名' in page.local_identity_label.text()
        assert page._selected_student().student_id == profile.student_id
        assert '显式本地分析别名' not in page.student_combo.currentText()
        profiles_before = service.student_profiles()
        opened = []
        def close_modal():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, StudentRosterDialog)
            opened.append(dialog.rosters.count())
            dialog.reject()
        QTimer.singleShot(10, close_modal)
        page.roster_button.click()
        settle(app)
        assert opened == [1] and page._selected_student().student_id == profile.student_id
        assert service.student_profiles() == profiles_before
    finally:
        tasks.shutdown()
        page.close()
        page.deleteLater()
        app.processEvents()

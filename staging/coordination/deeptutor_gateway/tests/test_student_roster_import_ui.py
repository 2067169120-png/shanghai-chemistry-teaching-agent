"""X05 real Qt local roster preview and explicit teacher decisions."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError
from integrations.deeptutor_shchem_v1.desktop_workbench.student_roster_import_dialog import StudentRosterImportDialog
from test_student_roster_import import HEADERS, PROFILE_A, csv_file, importer, xlsx_file


@pytest.fixture
def dialog(importer):
    store, roster, profiles = importer
    app = QApplication.instance() or QApplication([])
    facade = SimpleNamespace(student_roster_store=lambda: store,
                             student_profiles=lambda: tuple(profiles))
    editor = StudentRosterImportDialog(facade, roster)
    editor.show()
    app.processEvents()
    yield editor, app, store, roster
    editor.close()
    editor.deleteLater()
    app.processEvents()


def choose_data(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def table_text(editor, row, header):
    column = next(index for index in range(editor.table.columnCount())
                  if editor.table.horizontalHeaderItem(index).text() == header)
    return editor.table.item(row, column).text()


def map_and_preview(editor, path, *, sheet=None, header_row=1):
    editor.load_file(path)
    if sheet is not None:
        choose_data(editor.sheet, sheet)
    editor.header_row.setValue(header_row)
    for key, column in [('alias_col', 0), ('member_col', 1), ('profile_col', 2), ('external_col', 3)]:
        choose_data(editor.columns[key], column)
    editor.preview_button.click()
    assert editor.plan is not None
    return editor.plan


def decide(editor, number, action, target=None):
    choose_data(editor._actions[number], action)
    if target is not None:
        choose_data(editor._targets[number], target)


def confirm(editor, *, acknowledge=True):
    editor.reason.setPlainText('合成教师核对表头、稳定成员及逐行排除范围')
    editor.acknowledge.setChecked(acknowledge)
    editor.confirm.setChecked(True)


def test_native_file_choice_requires_explicit_alias_mapping_and_has_no_write(dialog, tmp_path, monkeypatch):
    editor, app, store, roster = dialog
    path = csv_file(tmp_path, [HEADERS, ['界面新合成成员', '', '', '00041']])
    original_file, before = path.read_bytes(), store.path.read_bytes()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args, **kwargs: (str(path), '名单'))
    editor.file_button.click()
    app.processEvents()
    assert editor.path.text() == str(path) and editor.book is not None
    assert editor.columns['alias_col'].currentData() is None
    assert editor.plan is None and not editor.save_button.isEnabled()
    editor.preview_button.click()
    assert editor.plan is None and '选择' in editor.status.text()
    assert store.path.read_bytes() == before and path.read_bytes() == original_file
    assert editor.roster == roster


def test_xlsx_worksheet_header_and_source_row_are_selected_in_real_controls(dialog, tmp_path):
    editor, _, store, _ = dialog
    path = xlsx_file(tmp_path / 'synthetic-ui.xlsx', [
        ('说明页', [['这张不是名单'], ['不导入说明']]),
        ('确认名单', [['标题'], HEADERS, ['正确工作表成员', '', '', '00012']]),
    ])
    before = store.path.read_bytes()
    plan = map_and_preview(editor, path, sheet='确认名单', header_row=2)
    assert len(plan['rows']) == editor.table.rowCount() == 1
    assert plan['config']['sheet'] == '确认名单' and plan['config']['header_row'] == 2
    assert table_text(editor, 0, '源记录／状态').startswith('3')
    assert table_text(editor, 0, '本地别名') == '正确工作表成员'
    assert table_text(editor, 0, '原表标识') == '00012'
    assert editor._actions[3].currentData() is None
    assert store.path.read_bytes() == before


def test_encoding_failure_can_be_recovered_only_by_explicit_re_read(dialog, tmp_path):
    editor, _, store, _ = dialog
    path = csv_file(tmp_path, [HEADERS, ['GB合成中文成员', '', '', '00008']], encoding='gb18030')
    before = store.path.read_bytes()
    editor.load_file(path)
    assert editor.book is None and editor.plan is None and '编码' in editor.status.text()
    choose_data(editor.encoding, 'gb18030')
    editor.reload_button.click()
    assert editor.book is not None
    for key, column in [('alias_col', 0), ('member_col', 1), ('profile_col', 2), ('external_col', 3)]:
        choose_data(editor.columns[key], column)
    editor.preview_button.click()
    assert table_text(editor, 0, '本地别名') == 'GB合成中文成员'
    assert store.path.read_bytes() == before


def test_unresolved_decisions_and_missing_confirmation_block_save(dialog, tmp_path):
    editor, _, store, _ = dialog
    path = csv_file(tmp_path, [HEADERS, ['明确处理前合成成员', '', '', '']])
    map_and_preview(editor, path)
    before = store.path.read_bytes()
    confirm(editor)
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None
    assert '明确' in editor.status.text() and store.path.read_bytes() == before
    decide(editor, 2, 'new')
    assert not editor.confirm.isChecked()
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None
    assert '确认' in editor.status.text() and store.path.read_bytes() == before


def test_warning_acknowledgement_and_distinct_new_member_ids(dialog, tmp_path):
    editor, _, store, roster = dialog
    path = csv_file(tmp_path, [HEADERS, ['已有同名成员', '', '', ''], ['已有同名成员', '', '', '']])
    map_and_preview(editor, path)
    assert all('重名' in table_text(editor, index, '错误／重复／重名核对') or '相同别名' in table_text(editor, index, '错误／重复／重名核对')
               for index in range(editor.table.rowCount()))
    decide(editor, 2, 'new')
    decide(editor, 3, 'new')
    before = store.path.read_bytes()
    confirm(editor, acknowledge=False)
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None
    assert '重名' in editor.status.text() and store.path.read_bytes() == before
    editor.acknowledge.setChecked(True)
    editor.save_button.click()
    assert editor.result() == QDialog.DialogCode.Accepted
    saved = store.load(roster['id'])
    assert saved == editor.saved_roster
    assert len(saved['members']) == 4
    assert len({member['id'] for member in saved['members']}) == 4
    assert saved['members'][:2] == roster['members']


def test_duplicate_id_error_is_visible_and_explicit_exclusion_preserves_owner(dialog, tmp_path):
    editor, _, store, roster = dialog
    identity = roster['members'][0]['id']
    path = csv_file(tmp_path, [HEADERS, ['明确保留合成改名', identity, '', '源甲'],
                               ['明确排除重复行', identity, '', '源乙']])
    map_and_preview(editor, path)
    assert '重复成员ID' in table_text(editor, 0, '错误／重复／重名核对')
    decide(editor, 2, 'update')
    decide(editor, 3, 'exclude')
    assert editor._targets[2].currentData() == identity
    confirm(editor)
    editor.save_button.click()
    assert editor.result() == QDialog.DialogCode.Accepted
    saved = store.load(roster['id'])
    assert saved['members'][0]['id'] == identity
    assert saved['members'][0]['local_alias'] == '明确保留合成改名'
    assert saved['members'][0]['profile_student_id'] == PROFILE_A
    assert saved['members'][1] == roster['members'][1]
    receipt = saved['import_history'][-1]
    assert [(row['source_row'], row['action']) for row in receipt['rows']] == [(2, 'update'), (3, 'exclude')]
    assert receipt['rows'][0]['external_id'] == '源甲'


def test_mapping_change_invalidates_preview_and_teacher_confirmation(dialog, tmp_path):
    editor, _, store, _ = dialog
    path = csv_file(tmp_path, [HEADERS, ['配置变化合成成员', '', '', '']])
    map_and_preview(editor, path)
    decide(editor, 2, 'new')
    confirm(editor)
    before = store.path.read_bytes()
    choose_data(editor.columns['member_col'], None)
    assert editor.plan is None and editor.table.rowCount() == 0
    assert not editor.confirm.isChecked() and not editor.acknowledge.isChecked()
    assert not editor.save_button.isEnabled()
    editor.save()
    assert store.path.read_bytes() == before and editor.saved_roster is None


def test_explicit_batch_actions_preserve_errors_and_owned_profile_conflicts_for_review(dialog, tmp_path):
    editor, _, store, roster = dialog
    path = csv_file(tmp_path, [HEADERS,
        ['已有同名成员', '', '', ''],
        ['按已声明UUID更新', roster['members'][1]['id'], '', ''],
        ['未知ID保持未决', 'f' * 32, '', ''],
        ['', '', '', '空别名保持未决'],
        ['已有档案归属需核对', '', PROFILE_A, ''],
    ])
    map_and_preview(editor, path)
    before = store.path.read_bytes()
    assert all(choice['action'] is None for choice in editor.decisions().values())
    assert all(target.count() == 1 for target in editor._targets.values())
    confirm(editor)
    editor.new_rows_button.click()
    assert not editor.confirm.isChecked()
    assert editor.decisions()[2] == {'action': 'new'}
    assert all(editor.decisions()[number]['action'] is None for number in [3, 4, 5, 6])
    editor.update_ids_button.click()
    assert editor.decisions()[3] == {'action': 'update', 'member_id': roster['members'][1]['id']}
    assert all(editor.decisions()[number]['action'] is None for number in [4, 5, 6])
    assert editor._targets[3].count() == 2
    confirm(editor)
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None
    assert store.path.read_bytes() == before


def test_batch_uuid_updates_keep_duplicate_id_conflict_until_teacher_excludes_one(dialog, tmp_path):
    editor, _, store, roster = dialog
    identity = roster['members'][1]['id']
    path = csv_file(tmp_path, [HEADERS, ['重复源甲', identity, '', ''], ['重复源乙', identity, '', '']])
    map_and_preview(editor, path)
    editor.update_ids_button.click()
    assert all(choice == {'action': 'update', 'member_id': identity} for choice in editor.decisions().values())
    confirm(editor)
    before = store.path.read_bytes()
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None
    assert '多条' in editor.status.text() or '重复' in editor.status.text()
    assert store.path.read_bytes() == before


def test_pagination_keeps_all_decisions_and_hidden_unresolved_rows_block_save(dialog, tmp_path):
    editor, _, store, _ = dialog
    path = csv_file(tmp_path, [HEADERS] + [[f'跨页合成成员{i:03}', '', '', ''] for i in range(105)])
    map_and_preview(editor, path)
    before = store.path.read_bytes()
    assert len(editor.plan['rows']) == len(editor.decisions()) == 105
    assert editor.page.maximum() == 2 and editor.table.rowCount() == 100
    assert len(editor._actions) == len(editor._targets) == 100
    assert all(target.count() == 1 for target in editor._targets.values())
    decide(editor, 2, 'new')
    editor.page.setValue(2)
    assert editor.table.rowCount() == len(editor._actions) == len(editor._targets) == 5
    decide(editor, 106, 'exclude')
    assert editor.decisions()[2]['action'] == 'new'
    assert editor.decisions()[106]['action'] == 'exclude'
    confirm(editor)
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None
    assert '第3' in editor.status.text() and store.path.read_bytes() == before
    editor.page.setValue(1)
    assert editor._actions[2].currentData() == 'new'
    assert editor.decisions()[106]['action'] == 'exclude'
    editor.new_rows_button.click()
    assert len(editor.decisions()) == 105
    assert all(choice['action'] == 'new' for choice in editor.decisions().values())
    assert not editor.confirm.isChecked() and store.path.read_bytes() == before


def test_encoding_or_delimiter_change_requires_re_read_and_clears_decisions(dialog, tmp_path):
    editor, _, store, _ = dialog
    path = csv_file(tmp_path, [HEADERS, ['重读合成成员', '', '', '']])
    map_and_preview(editor, path)
    decide(editor, 2, 'new')
    confirm(editor)
    before = store.path.read_bytes()
    choose_data(editor.delimiter, ';')
    assert editor.book is None and editor.plan is None and editor.decisions() == {}
    assert not editor.preview_button.isEnabled() and not editor.save_button.isEnabled()
    assert not editor.confirm.isChecked() and '重读' in editor.status.text()
    assert store.path.read_bytes() == before


def test_cancel_preview_and_cancel_file_picker_leave_library_and_source_unchanged(dialog, tmp_path, monkeypatch):
    editor, _, store, roster = dialog
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args, **kwargs: ('', ''))
    editor.file_button.click()
    assert editor.book is None and editor.plan is None
    path = csv_file(tmp_path, [HEADERS, ['取消时合成成员', '', '', '']])
    map_and_preview(editor, path)
    decide(editor, 2, 'new')
    confirm(editor)
    source, before = path.read_bytes(), store.path.read_bytes()
    editor.reject()
    assert editor.result() == QDialog.DialogCode.Rejected and not editor.isVisible()
    assert editor.saved_roster is None and editor.roster == roster
    assert store.path.read_bytes() == before and path.read_bytes() == source


def test_save_failure_keeps_visible_input_choices_and_saved_library(dialog, tmp_path, monkeypatch):
    editor, _, store, _ = dialog
    path = csv_file(tmp_path, [HEADERS, ['失败保留的合成输入', '', '', '']])
    map_and_preview(editor, path)
    decide(editor, 2, 'new')
    confirm(editor)
    before_plan, before_decisions = deepcopy(editor.plan), deepcopy(editor.decisions())
    before = store.path.read_bytes()
    calls = []
    def fail_save(*args, **kwargs):
        calls.append(kwargs)
        raise ExamError('合成保存失败，保留当前核对输入')
    monkeypatch.setattr(store, 'save', fail_save)
    editor.save_button.click()
    assert calls and calls[0]['expected_revision'] == editor.roster['revision']
    assert editor.isVisible() and editor.saved_roster is None
    assert '保存失败' in editor.status.text()
    assert editor.plan == before_plan and editor.decisions() == before_decisions
    assert editor.reason.toPlainText() == '合成教师核对表头、稳定成员及逐行排除范围'
    assert store.path.read_bytes() == before


def test_source_change_and_cas_failure_keep_inputs_without_accepting(dialog, tmp_path):
    editor, _, store, roster = dialog
    path = csv_file(tmp_path, [HEADERS, ['过期预览合成成员', '', '', '']])
    map_and_preview(editor, path)
    decide(editor, 2, 'new')
    confirm(editor)
    before_plan, before_decisions = deepcopy(editor.plan), deepcopy(editor.decisions())
    before = store.path.read_bytes()
    csv_file(tmp_path, [HEADERS, ['原文件已编辑', '', '', '']])
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None and '原文件已变化' in editor.status.text()
    assert store.path.read_bytes() == before
    assert editor.plan == before_plan and editor.decisions() == before_decisions
    map_and_preview(editor, path)
    decide(editor, 2, 'new')
    confirm(editor)
    latest = deepcopy(roster)
    latest['label'] = '另外窗口保存的最新名册'
    latest = store.save(latest, expected_revision=roster['revision'])
    current_plan, current_decisions = deepcopy(editor.plan), deepcopy(editor.decisions())
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None and '未覆盖' in editor.status.text()
    assert store.load(roster['id']) == latest
    assert editor.plan == current_plan and editor.decisions() == current_decisions


def test_capture_real_import_preview_with_errors_warnings_and_row_decisions(dialog, tmp_path, request):
    """Synthetic visual evidence for a separate human review."""
    editor, app, _, roster = dialog
    previous_font = QFont(app.font())
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    assert font_id >= 0
    families = QFontDatabase.applicationFontFamilies(font_id)
    assert families
    app.setFont(QFont(families[0], 10))
    request.addfinalizer(lambda: app.setFont(previous_font))
    path = csv_file(tmp_path, [HEADERS, ['已有同名成员', '', '', '00101'],
                               ['保留稳定ID及档案', roster['members'][0]['id'], '', '00102'],
                               ['未知ID须排除', 'f' * 32, '', '00103']])
    map_and_preview(editor, path)
    decide(editor, 2, 'new')
    decide(editor, 3, 'update')
    decide(editor, 4, 'exclude')
    editor.reason.setPlainText('合成视觉核对：重名生成新ID；明确稳定ID更新保留档案；未知ID明确排除。')
    output = Path(os.environ.get('SHCHEM_ROSTER_IMPORT_QA_ROOT', str(tmp_path / 'visual-qa')))
    output.mkdir(parents=True, exist_ok=True)
    for width, height in [(1080, 760), (680, 540)]:
        editor.resize(width, height)
        app.processEvents()
        assert editor.grab().save(str(output / f'roster-import-preview-{width}x{height}.png'))
        if width == 1080:
            bar = editor.table.horizontalScrollBar()
            bar.setValue(bar.maximum())
            app.processEvents()
            assert editor.grab().save(str(output / 'roster-import-preview-1080x760-decisions.png'))
            bar.setValue(bar.minimum())


def test_cas_recheck_keeps_teacher_choices_requires_new_confirmation_then_saves(dialog,tmp_path):
    editor,_,store,roster=dialog
    path=csv_file(tmp_path,[HEADERS,['重核后合成新成员','','','001']])
    map_and_preview(editor,path);decide(editor,2,'new');confirm(editor)
    reason=editor.reason.toPlainText();decisions=deepcopy(editor.decisions())
    latest=deepcopy(roster);latest['label']='另外窗口核对的最新班级'
    latest=store.save(latest,expected_revision=roster['revision'])
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None and store.load(roster['id'])==latest
    editor.recheck_button.click()
    assert editor.roster==latest and editor.plan['roster']==latest
    assert editor.decisions()==decisions and editor.reason.toPlainText()==reason
    assert not editor.confirm.isChecked() and not editor.acknowledge.isChecked()
    editor.save_button.click()
    assert editor.isVisible() and editor.saved_roster is None and store.load(roster['id'])==latest
    editor.confirm.setChecked(True);editor.acknowledge.setChecked(True);editor.save_button.click()
    assert editor.result()==QDialog.DialogCode.Accepted
    saved=store.load(roster['id']);assert saved['label']==latest['label'] and len(saved['members'])==3
    assert saved['import_history'][-1]['before_revision']==latest['revision']


def test_recheck_refuses_changed_source_or_unknown_durable_receipt_without_losing_inputs(dialog,tmp_path):
    import json
    editor,_,store,roster=dialog
    path=csv_file(tmp_path,[HEADERS,['核对保留合成成员','','','001']])
    map_and_preview(editor,path);decide(editor,2,'new');confirm(editor)
    before_plan=deepcopy(editor.plan);decisions=deepcopy(editor.decisions());reason=editor.reason.toPlainText()
    raw=path.read_bytes();csv_file(tmp_path,[HEADERS,['源文件新记录','','','002']])
    editor.recheck_button.click()
    assert '原文件已变化' in editor.status.text()
    assert editor.plan==before_plan and editor.decisions()==decisions and editor.reason.toPlainText()==reason
    path.write_bytes(raw)
    library=json.loads(store.path.read_text(encoding='utf-8'))
    library['rosters'][roster['id']]['import_history']=[{'schema':'unknown.receipt.v9','keep':'原始证据'}]
    store.path.write_text(json.dumps(library,ensure_ascii=False),encoding='utf-8');before=store.path.read_bytes()
    editor.recheck_button.click()
    assert editor.plan==before_plan and editor.decisions()==decisions and editor.reason.toPlainText()==reason
    assert store.path.read_bytes()==before and editor.saved_roster is None


@pytest.mark.parametrize('step',['save','recheck'])
def test_unavailable_profile_directory_keeps_teacher_choices_and_disk(dialog,tmp_path,monkeypatch,step):
    editor,_,store,_=dialog
    path=csv_file(tmp_path,[HEADERS,['档案暂时不可读取合成成员','','','001']])
    map_and_preview(editor,path);decide(editor,2,'new');confirm(editor)
    plan=deepcopy(editor.plan);choices=deepcopy(editor.decisions());reason=editor.reason.toPlainText();disk=store.path.read_bytes()
    def fail():raise OSError('synthetic-private-directory-unavailable')
    monkeypatch.setattr(store,'profiles',fail)
    (editor.save_button if step=='save' else editor.recheck_button).click()
    assert editor.isVisible() and editor.saved_roster is None and '目录无法读取' in editor.status.text()
    assert editor.plan==plan and editor.decisions()==choices and editor.reason.toPlainText()==reason
    assert store.path.read_bytes()==disk

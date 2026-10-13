"""Selected materials save as a real draft without a duplicate scope entry."""
from copy import deepcopy
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_question_explorer_ui import window
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_lesson_probe import sample_payload
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource, digest
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_recovery import apply_editor_payload
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog


@pytest.fixture
def desk(window):
    win,app=window;page=win.preparation_page
    apply_editor_payload(page,sample_payload())
    page.materials.clear()
    d=LessonDesignDialog(page);d.show();settle(app)
    yield d,app,page,win.facade
    d.reject();d.deleteLater();settle(app)


def material(title):
    return resource('personal_text',title,[block('text','personal','已明确选择的合成材料正文。')],
        {'source_path':'synthetic test input','source_sha256':'unknown','source_revision':'unknown'})


def save(d,app):
    QTest.mouseClick(d.save,Qt.MouseButton.LeftButton)
    settle(app,lambda:not d.busy)


def test_selected_materials_save_and_reopen_with_original_snapshots(desk):
    d,app,page,facade=desk
    d.composition_panel.add_result({'resources':[material('合成资料甲'),material('合成资料乙')]},digest(d.history.value))
    original=deepcopy(d.history.value['composition']['resources'])
    before={r['draft_id'] for r in facade.preparation_draft_options()}
    save(d,app)
    rows=[r for r in facade.preparation_draft_options() if r['draft_id'] not in before]
    assert len(rows)==1
    payload=facade.load_preparation_draft(rows[0]['draft_id'],rows[0]['revision'])['payload']
    assert '合成资料甲' in payload['materials'] and '合成资料乙' in payload['materials']
    assert payload['lesson_design']['composition']['resources']==original
    assert payload==page._payload()


def test_existing_teacher_materials_remain_when_saving_composition(desk):
    d,app,page,facade=desk
    page.materials.setPlainText('教师手写的资料范围与限制。')
    d.composition_panel.add_result({'resources':[material('不替换手写说明')]},digest(d.history.value))
    save(d,app)
    assert page.materials.toPlainText()=='教师手写的资料范围与限制。'
    row=facade.preparation_draft_options()[0]
    assert facade.load_preparation_draft(row['draft_id'],row['revision'])['payload']['materials']=='教师手写的资料范围与限制。'


def test_empty_materials_without_selected_sources_still_require_input(desk):
    d,app,page,facade=desk
    before=facade.preparation_draft_options()
    save(d,app)
    assert facade.preparation_draft_options()==before
    assert page.materials.toPlainText()==''
    assert '资料范围' in d.status.text()

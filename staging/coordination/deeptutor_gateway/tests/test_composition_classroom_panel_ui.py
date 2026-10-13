"""Drive real modal Qt selection and one course commit in a private image store.

The in-memory owner never constructs MainWindow, Facade, StateStore or Recovery.
Native dialogs, image publication, layout, history and source checks are real.
"""
from copy import deepcopy
from pathlib import Path
from threading import get_ident

import pytest
from PIL import Image
from PySide6.QtCore import QTimer, Qt, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QPushButton

from test_pending_material_edit import app, desk as safe_workspace, SafeDesignDialog
from test_question_answer_reveal import question, text, personal
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import edit_resource, set_student_arrangement
from integrations.deeptutor_shchem_v1.desktop_composition_render import layout_composition
from integrations.deeptutor_shchem_v1.desktop_composition_image_display import set_image_display
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
from integrations.deeptutor_shchem_v1.desktop_workbench.image_batch_workspace import ImageBatchWorkspace, ImageBatchPreview
from integrations.deeptutor_shchem_v1.desktop_workbench.question_answer_reveal_dialog import QuestionAnswerRevealDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import ResourceEditor


@pytest.fixture
def desk(safe_workspace):
    s = safe_workspace
    d = SafeDesignDialog(s.page); s.page._material_handoff_dialog = d
    d.resize(1480, 1000); d.show(); s.app.processEvents()
    yield d, d.composition_panel, s.app, s.page


def modal(cls, action, errors):
    def drive():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, cls), type(dialog)
            action(dialog)
        except BaseException as exc:
            errors.append(exc)
            if dialog is not None: dialog.reject()
    QTimer.singleShot(0, drive)


def choose_images(desk, monkeypatch, tmp_path, *, entry='课外／本机配图…', cancel=False):
    d, panel, app, page = desk
    paths = [tmp_path / 'first.png', tmp_path / 'second.png']
    for path, color in zip(paths, ('#41728e', '#6a9554')):
        Image.new('RGB', (360, 210), color).save(path)
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *_: (list(map(str, paths)), ''))
    errors = []; before = deepcopy(d.history.value); visited = []
    def drive(workspace):
        visited.append(workspace)
        workspace.local.click()
        assert workspace.list.count() == 2 and not workspace.confirm.isEnabled()
        workspace.list.setCurrentRow(1)
        workspace.title.setText('为什么第二张图会有这种现象？')
        workspace.purpose.setPlainText('观察并比较完整条件。')
        workspace.up.click()
        workspace.list.setCurrentRow(1); workspace.list.setCurrentRow(0)
        assert workspace.title.text() == '为什么第二张图会有这种现象？'
        if cancel: workspace.cancel.click(); return
        def preview(dialog):
            assert dialog.pages.count() == 2
            assert abs(dialog.page.original.width()/dialog.page.original.height() - 16/9) < .01
            for i in range(dialog.pages.count()): dialog.pages.setCurrentIndex(i)
            assert dialog.confirm.isEnabled()
            dialog.confirm.click()
        modal(ImageBatchPreview, preview, errors)
        workspace.preview_button.click(); d.tasks.finish(workspace._task_id)
        assert workspace.confirm.isEnabled(), workspace.status.text()
        assert d.history.value == before
        workspace.confirm.click()
    modal(ImageBatchWorkspace, drive, errors)
    next(b for b in panel.findChildren(QPushButton) if b.text() == entry).click()
    assert not errors, errors
    assert len(visited) == 1
    return paths


def finish_images(d):
    first = d._task; assert first in d.tasks.jobs
    d.tasks.finish(first)
    second = d._task; assert second is not None and second != first
    d.tasks.finish(second)


@pytest.mark.parametrize('entry', ['课外／本机配图…', '教材原页／切片…'])
def test_existing_entries_use_native_batch_preview_then_one_history_commit(desk, monkeypatch, tmp_path, entry):
    d, panel, app, page = desk
    panel.change_format('16:9')
    panel.add_result({'resources': [personal('已有课堂引言'), personal('已有课堂总结')]}, digest(d.history.value))
    panel.selected = panel.composition()['resources'][0]['id']; panel.refresh()
    panel.insertion_position.setCurrentIndex(panel.insertion_position.findData('after'))
    before = deepcopy(d.history.value); history_count = len(d.history.back)
    choose_images(desk, monkeypatch, tmp_path, entry=entry)
    assert d.history.value == before
    finish_images(d)
    after = deepcopy(d.history.value); rows = panel.composition()['resources']
    assert len(rows) == 4 and rows[1]['title'] == '为什么第二张图会有这种现象？'
    assert rows[-1]['title'] == '已有课堂总结'
    assert len(d.history.back) == history_count+1
    assert not panel._transaction_scopes and panel._image_batch_entry.scope is None
    image_ids = {b['value'] for r in rows[1:3] for b in r['blocks'] if b['kind'] == 'image'}
    saved = [a for a in page.image_assets_widget.assets() if a['asset_id'] in image_ids]
    assert len(saved) == 2 and all(d.facade.preparation_image_bytes(a) for a in saved)
    d.travel(False); assert d.history.value == before
    d.travel(True); assert d.history.value == after
    reopened = SafeDesignDialog(page)
    assert reopened.composition_panel.composition() == panel.composition()
    reopened.reject(); reopened.deleteLater(); app.processEvents()


def test_cancel_native_batch_leaves_history_form_and_store_unchanged(desk, monkeypatch, tmp_path):
    d, panel, app, page = desk
    before = deepcopy((d.history.value, d.history.back, d.history.forward, page._payload()))
    root = Path(d.facade.paths.task_root) / 'preparation-v1'
    files = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    choose_images(desk, monkeypatch, tmp_path, cancel=True)
    assert not d.tasks.jobs and panel._image_batch_entry.scope is None
    assert (d.history.value, d.history.back, d.history.forward, page._payload()) == before
    assert {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()} == files


@pytest.mark.parametrize('change', ['form_aba', 'assets_aba', 'target_aba', 'history_aba', 'position_aba', 'stop', 'close'])
def test_late_context_changes_revoke_entire_batch_even_when_restored(desk, monkeypatch, tmp_path, change):
    d, panel, app, page = desk
    choose_images(desk, monkeypatch, tmp_path)
    task = d._task; result = d.tasks.calculate(task)
    before = deepcopy((d.history.value, d.history.back, d.history.forward, page.image_assets_widget.assets()))
    scope = panel._image_batch_entry.scope
    if change == 'form_aba':
        original = page.topic.text(); page.topic.setText('后续备课'); page.topic.setText(original)
    elif change == 'assets_aba':
        original = page.image_assets_widget.assets(); page.image_assets_widget.set_assets([]); page.image_assets_widget.set_assets(original)
    elif change == 'target_aba':
        original = page._editor_draft_id; page._editor_draft_id = 'OTHER'; page.preparation_target_changed.emit()
        page._editor_draft_id = original; page.preparation_target_changed.emit()
    elif change == 'history_aba':
        d.history.forward.append(deepcopy(d.history.value)); d.sync_page()
        d.history.forward.clear(); d.sync_page()
    elif change == 'position_aba':
        panel.insertion_position.setCurrentIndex(3); panel.insertion_position.setCurrentIndex(0)
    elif change == 'stop': d._stopping = True; d.tasks.cancel(task)
    else: d.reject()
    assert scope.revoked.is_set() or change == 'stop'
    d.tasks.deliver(task, result)
    assert (d.history.value, d.history.back, d.history.forward, page.image_assets_widget.assets()) == before
    assert not panel._transaction_scopes and panel._image_batch_entry.scope is None
    existing = {a['asset_id'] for a in before[-1]}
    for asset in result['image_assets']:
        if asset['asset_id'] not in existing:
            assert not (Path(d.facade.paths.task_root)/'preparation-v1/images'/(asset['sha256']+'.image')).exists()


def test_final_source_recheck_rejects_changed_local_photo_without_partial_add(desk, monkeypatch, tmp_path):
    d, panel, app, page = desk
    paths = choose_images(desk, monkeypatch, tmp_path)
    before = deepcopy((d.history.value, page.image_assets_widget.assets()))
    d.tasks.finish(d._task)  # Publication exists, final recheck has not run.
    Image.new('RGB', (360, 210), 'yellow').save(paths[1])
    d.tasks.finish(d._task)
    assert (d.history.value, page.image_assets_widget.assets()) == before
    assert '预览后变化' in d.status.text()
    assert panel._image_batch_entry.scope is None


def test_worker_only_observes_event_and_commit_receipt_protects_images(desk, monkeypatch, tmp_path):
    d, panel, app, page = desk
    gui = get_ident(); payload = page._payload; assets = page.image_assets_widget.assets
    def gui_payload():
        assert get_ident() == gui
        return payload()
    def gui_assets():
        assert get_ident() == gui
        return assets()
    monkeypatch.setattr(page, '_payload', gui_payload)
    monkeypatch.setattr(page.image_assets_widget, 'assets', gui_assets)
    choose_images(desk, monkeypatch, tmp_path)
    d.tasks.finish(d._task)
    def sync_failure(): raise RuntimeError('合成提交后界面同步故障')
    monkeypatch.setattr(d, 'sync_page', sync_failure)
    d.tasks.finish(d._task)
    rows = d.history.value['composition']['resources']
    assert len(rows) == 2 and len(d.history.back) == 1
    for asset in assets(): assert d.facade.preparation_image_bytes(asset)
    assert panel._image_batch_entry.scope is None


def set_question(desk, *, picture=True):
    d, panel, app, page = desk
    value, item, answer_id, assets, data = question(picture=picture)
    if picture:
        value = set_image_display(value, item['id'],
            {b['id']: {'max_height_mm': 28} for b in item['blocks'] if b['kind'] == 'image'})
    store = PreparationImageStore(Path(d.facade.paths.task_root)/'preparation-v1/images')
    for asset in assets: store.import_bytes(data[asset['asset_id']], asset['caption'], asset['source'], asset['purpose'])
    page.image_assets_widget.set_assets(assets)
    plan = deepcopy(d.history.value); plan['composition'] = value
    d.history.put(plan); d.sync_page(); panel.selected = item['id']; panel.refresh()
    return value, item, answer_id, assets, data


def configure_in_dialog(desk, *, action='apply', changed=None):
    d, panel, app, page = desk
    errors = []; visits = []
    def drive(dialog):
        visits.append(dialog)
        assert not panel.click_reveal.isVisible() and panel.question_answer_button.isVisible()
        if changed is not None: changed(dialog)
        if action == 'clear': dialog.same_page.setChecked(False)
        else:
            chosen = next(dialog.answers.item(i) for i in range(dialog.answers.count())
                if '【答案】' in dialog.answers.item(i).text())
            chosen.setCheckState(Qt.CheckState.Checked)
            assert dialog.block_ids() == [chosen.data(Qt.ItemDataRole.UserRole)]
            assert dialog.apply_button.isEnabled(), dialog.status.text()
            assert '【答案】' not in text(dialog._layouts['before'])
            assert '【答案】' not in text(dialog._layouts['student'])
            assert '【答案】' in text(dialog._layouts['after'])
            assert 'TEACHER_REASON_PRIVATE' not in text(dialog._layouts['after'])
            assert 'TEACHER_REASON_PRIVATE' in text(dialog._layouts['teacher'])
            dialog.click_preview.click(); assert dialog.audience.currentData() == 'after'
            assert dialog.scene.items()
        assert dialog.apply_button.isEnabled(), dialog.status.text()
        if action == 'cancel': dialog.cancel_button.click()
        else: dialog.apply_button.click()
    modal(QuestionAnswerRevealDialog, drive, errors)
    panel.question_answer_button.click()
    assert not errors, errors
    assert len(visits) == 1


def test_native_exact_answer_commit_undo_reopen_and_print_roundtrip(desk):
    d, panel, app, page = desk
    original, item, answer_id, assets, data = set_question(desk)
    before = deepcopy(d.history.value); count = len(d.history.back)
    def initially_empty(dialog): assert not dialog.block_ids()
    configure_in_dialog(desk, changed=initially_empty)
    after = deepcopy(d.history.value); configured = panel.composition()['resources'][0]
    assert configured['question_answer_reveal']['answer_block_ids'] == [answer_id]
    assert configured['original'] == item['original'] and configured['blocks'] == item['blocks']
    assert len(d.history.back) == count+1
    d.travel(False); assert d.history.value == before
    d.travel(True); assert d.history.value == after
    reopened = SafeDesignDialog(page)
    try:
        reopened.resize(1480, 1000); reopened.show(); app.processEvents()
        reopened.composition_panel.selected = item['id']; reopened.composition_panel.refresh()
        reopened_desk = (reopened, reopened.composition_panel, app, page)
        count = len(reopened.history.back)
        configure_in_dialog(reopened_desk, action='cancel')
        assert reopened.history.value == after and len(reopened.history.back) == count
    finally: reopened.reject(); reopened.deleteLater(); app.processEvents()
    config = deepcopy(configured['question_answer_reveal'])
    panel.change_format('A4')
    assert panel.composition()['resources'][0]['question_answer_reveal'] == config
    assert '【答案】' not in text(layout_composition(panel.composition(), assets, data, include_reveals=True))
    panel.change_format('16:9')
    assert panel.composition()['resources'][0]['question_answer_reveal'] == config
    assert '【答案】AB' in text(layout_composition(panel.composition(), assets, data, include_reveals=True))
    assert panel.composition()['placements'][item['id']]['height'] == 166


def test_native_cancel_and_clear_preserve_original_and_clear_preserves_frames(desk):
    d, panel, app, page = desk
    original, item, answer_id, assets, data = set_question(desk)
    before = deepcopy((d.history.value, d.history.back, d.history.forward))
    configure_in_dialog(desk, action='cancel')
    assert (d.history.value, d.history.back, d.history.forward) == before
    configure_in_dialog(desk)
    placement = deepcopy(panel.composition()['placements'])
    configure_in_dialog(desk, action='clear')
    assert panel.composition()['placements'] == placement
    assert panel.composition()['resources'][0]['original'] == item['original']
    assert 'question_answer_reveal' not in panel.composition()['resources'][0]


@pytest.mark.parametrize('columns', [None, 1, 2])
def test_manual_geometry_and_drag_cannot_break_configured_question(desk, columns):
    d, panel, app, page = desk
    original, item, answer_id, assets, data = set_question(desk)
    configure_in_dialog(desk)
    panel.commit(set_student_arrangement(panel.composition(), columns))
    panel.student.setChecked(False)
    panel.geometry['height'].setValue(20)
    before = deepcopy(d.history.value)
    panel.geometry_apply.click()
    assert d.history.value == before and panel.status.text()
    # A competing frame is allowed to be on the page only outside this frame;
    # moving it into the configured question must also fail the same helper.
    other = personal('独立素材')
    value = panel.composition(); value['resources'].append(other)
    value['placements'][other['id']] = dict(page_id=value['pages'][0], x=0, y=0, width=10, height=5)
    panel.commit(value); before = deepcopy(d.history.value)
    panel.move_frame(other['id'], 0, 20, 20)
    assert d.history.value == before and '重叠' in panel.status.text()


def test_work_answer_edit_keeps_exact_original_binding_and_invalid_edit_does_not_commit(desk):
    d, panel, app, page = desk
    original, item, answer_id, assets, data = set_question(desk)
    configure_in_dialog(desk)
    errors = []
    def edit(editor):
        editor.locked.setChecked(False)
        editor.blocks.setCurrentIndex(editor.blocks.findData(answer_id))
        editor.body.set_source_text('【答案】AC')
        editor.apply_button.click()
    modal(ResourceEditor, edit, errors)
    panel.edit_button.click()
    assert not errors, errors
    assert panel.composition()['resources'][0]['original'] == item['original']
    assert '【答案】AC' in text(layout_composition(panel.composition(), assets, data, include_reveals=True))
    assert '【答案】' not in text(layout_composition(panel.composition(), assets, data))
    before = deepcopy(d.history.value)
    too_long = edit_resource(panel.composition(), item['id'], values={answer_id: '长答案'*100})
    with pytest.raises(ValueError): panel.commit(too_long)
    assert d.history.value == before


def test_native_no_answer_choice_enables_only_same_page_and_a4_clear_keeps_frames(desk):
    d, panel, app, page = desk
    original, item, answer_id, assets, data = set_question(desk)
    errors = []
    def only_page(dialog):
        assert not dialog.block_ids()
        assert dialog.same_page.isChecked() and dialog.apply_button.isEnabled()
        dialog.apply_button.click()
    modal(QuestionAnswerRevealDialog, only_page, errors)
    panel.question_answer_button.click()
    assert not errors, errors
    assert panel.composition()['resources'][0]['question_answer_reveal']['answer_block_ids'] == []
    assert '【答案】' not in text(layout_composition(panel.composition(), assets, data, include_reveals=True))
    panel.change_format('A4'); frames = deepcopy(panel.composition()['placements'])
    configure_in_dialog(desk, action='clear')
    assert panel.composition()['placements'] == frames
    assert 'question_answer_reveal' not in panel.composition()['resources'][0]


def test_native_question_form_aba_revokes_before_apply(desk):
    d, panel, app, page = desk
    set_question(desk); before = deepcopy((d.history.value, d.history.back, d.history.forward))
    errors = []
    def mutate(dialog):
        original = page.topic.text(); page.topic.setText('新课题'); page.topic.setText(original)
        dialog.apply()
        assert dialog.result_value is None
    modal(QuestionAnswerRevealDialog, mutate, errors)
    panel.question_answer_button.click()
    assert not errors, errors
    assert (d.history.value, d.history.back, d.history.forward) == before
    assert not panel._transaction_scopes

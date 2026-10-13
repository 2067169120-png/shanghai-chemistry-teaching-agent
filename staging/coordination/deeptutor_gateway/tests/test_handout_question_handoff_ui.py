"""An isolated lecture selection is additive and never consumes library history."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from test_word_question_dialog import qt_app as qt_app, _Facade, _Tasks, _check, _cleanup, _preparation_asset
from test_composition_ui import desk as desk, personal
from test_question_explorer_ui import window as window
from test_handout_images import lecture as lecture
from test_composition_resource_titles import question
from test_handout_question_handoff import scoped_lecture as scoped_lecture, desktop_paths as _question_paths

from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
from integrations.deeptutor_shchem_v1.desktop_handout_images import import_handout_image
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest


@pytest.fixture
def desktop_paths(tmp_path):
    # The actual Word archive is separate from the independently seeded UI.
    return _question_paths.__wrapped__(tmp_path / 'question-source')


def test_new_selection_session_never_reads_or_writes_library_history(qt_app):
    history = [{'key': 'Q2', 'revision': 'revision-Q2', 'points': 4}]
    facade, tasks = _Facade(saved=history), _Tasks()
    facade.word_question_saved_selection = lambda: pytest.fail('isolated picker read old history')
    facade.word_question_save_selection = lambda _: pytest.fail('isolated picker wrote old history')
    dialog = WordQuestionDialog(facade, tasks, required_source_id='A', persist_selection=False, handoff_mode='composition')
    tasks.flush(); assert dialog.selections == [] and dialog._selected == {}
    assert dialog.source_combo.count() == 1 and dialog.source_combo.currentData() == 'A'
    assert '本次勾选' in dialog.catalog_note.text()
    _check(dialog, 'Q1'); assert [r['key'] for r in dialog.selections] == ['Q1']
    dialog._persist_selection(); tasks.flush()
    dialog._clear_selection(); tasks.flush()
    assert facade.saved == history and not dialog.selections
    _cleanup(dialog, tasks)


def test_required_scope_confirms_only_this_source_and_keeps_other_history(qt_app, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
    history = [{'key': 'Q2', 'revision': 'revision-Q2', 'points': 4}]
    facade, tasks = _Facade(saved=history), _Tasks()
    dialog = WordQuestionDialog(facade, tasks, required_source_id='A', handoff_mode='composition')
    tasks.flush(); assert not dialog.selections and 'Q2' in dialog._selected
    assert '已选 0 题' in dialog.selection_count.text() and not dialog.preview_button.isEnabled()
    _check(dialog, 'Q1'); dialog._persist_selection(); tasks.flush()
    assert {r['key'] for r in facade.saved} == {'Q1', 'Q2'}
    dialog.preview_button.click(); tasks.finish('预览 Word 选题')
    calls = []
    def import_checked(f, selections, assets, *, expected_reference):
        calls.append(deepcopy(selections)); return {'resources': [question()], 'image_assets': []}
    monkeypatch.setattr(sources, 'import_questions', import_checked)
    dialog.composition_button.click(); tasks.finish('核对完整题并加入课件'); tasks.flush()
    assert calls and [r['key'] for r in calls[0]] == ['Q1']
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert {r['key'] for r in facade.saved} == {'Q1', 'Q2'}
    dialog.deleteLater()


def test_clear_required_scope_does_not_remove_other_saved_choices(qt_app):
    facade, tasks = _Facade(saved=[{'key': 'Q1', 'revision': 'revision-Q1'}, {'key': 'Q2', 'revision': 'revision-Q2'}]), _Tasks()
    dialog = WordQuestionDialog(facade, tasks, required_source_id='A')
    tasks.flush(); dialog._clear_selection(); dialog._persist_selection(); tasks.flush()
    assert [r['key'] for r in facade.saved] == ['Q2'] and not dialog.selections
    _cleanup(dialog, tasks)


@pytest.mark.parametrize('reason', ['cancel', 'late_selection', 'closed'])
def test_isolated_picker_does_not_deliver_cancelled_or_late_confirmation(qt_app, monkeypatch, reason):
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
    facade, tasks = _Facade(), _Tasks()
    dialog = WordQuestionDialog(facade, tasks, required_source_id='A', persist_selection=False, handoff_mode='composition')
    tasks.flush(); _check(dialog, 'Q1'); dialog.preview_button.click(); tasks.finish('预览 Word 选题')
    calls = []
    monkeypatch.setattr(sources, 'import_questions', lambda *a, **k: calls.append(True) or {'resources': [question()], 'image_assets': []})
    if reason == 'cancel': dialog.reject()
    else:
        dialog.composition_button.click()
        if reason == 'closed': dialog.reject()
        else: dialog._clear_selection()
        tasks.finish('核对完整题并加入课件', allow_cancelled=True)
    assert dialog.composition_result is None and not calls and not facade.saved
    _cleanup(dialog, tasks)


def setup_panel(desk, lecture, monkeypatch):
    d, panel, app, page = desk
    result = import_handout_image(lecture.facade, [], [], lecture.selection)
    monkeypatch.setattr(d.facade, 'preparation_image_bytes', lambda _: lecture.data, raising=False)
    panel.add_result(result, digest(d.history.value)); panel.refresh(); app.processEvents()
    assert panel.handout_question_button.isVisible()
    from integrations.deeptutor_shchem_v1 import desktop_handout_questions as handoff
    calls = []
    def scoped(_facade, binding): calls.append(deepcopy(binding)); return _Facade()
    monkeypatch.setattr(handoff, 'handout_question_facade', scoped)
    monkeypatch.setattr(d, 'run', lambda _label, operation, done, **kw: done(operation()))
    return d, panel, app, page, result, calls


def test_map_button_uses_independent_same_lecture_scope_and_cancel_preserves_work(desk, lecture, monkeypatch):
    d, panel, app, page, result, calls = setup_panel(desk, lecture, monkeypatch)
    before = deepcopy(d.history.value); before_assets = page.image_assets_widget.assets(); seen = []
    def cancel(dialog):
        seen.append((dialog._required_source_id, dialog._persist_choices, dialog._handoff_mode))
        dialog.reject(); return QDialog.DialogCode.Rejected
    monkeypatch.setattr(WordQuestionDialog, 'exec', cancel)
    panel.handout_question_button.click(); app.processEvents()
    assert calls == [lecture.row] and seen == [(lecture.row['source_sha256'], False, 'composition')]
    assert d.history.value == before and page.image_assets_widget.assets() == before_assets
    assert panel.composition()['resources'][0] == result['resources'][0]


@pytest.mark.parametrize('change', ['draft_before_open', 'assets_before_open', 'draft_during_pick', 'assets_during_pick', 'stopping'])
def test_late_scope_or_changed_lesson_does_not_add_questions(desk, lecture, monkeypatch, change):
    d, panel, app, page, _, _ = setup_panel(desk, lecture, monkeypatch)
    pending = []; monkeypatch.setattr(d, 'run', lambda _label, operation, done, **kw: pending.append((operation, done)))
    def changed():
        if change.startswith('draft'): panel.add_result({'resources': [personal('选择期间新增的课堂导语')]}, digest(d.history.value))
        elif change.startswith('assets'): page.image_assets_widget.set_assets(page.image_assets_widget.assets() + [_preparation_asset(777)])
        else: d._stopping = True
    opened = []
    def accept(dialog):
        opened.append(True); changed(); dialog.composition_result = {'resources': [question()], 'image_assets': []}
        dialog.reject(); return QDialog.DialogCode.Accepted
    monkeypatch.setattr(WordQuestionDialog, 'exec', accept)
    panel.add_handout_questions(); scoped = pending[0][0]()
    if change.endswith('before_open') or change == 'stopping':
        changed(); before = deepcopy(d.history.value); before_assets = page.image_assets_widget.assets()
        pending[0][1](scoped); assert not opened
    else:
        pending[0][1](scoped); before = deepcopy(d.history.value); before_assets = page.image_assets_widget.assets()
        assert opened == [True]
    assert not any(r['original']['kind'] == 'complete_question' for r in panel.composition()['resources'])
    assert d.history.value == before and page.image_assets_widget.assets() == before_assets
    d._stopping = False


def test_confirmed_question_uses_one_undo_and_duplicate_preserves_edit_and_map(desk, lecture, scoped_lecture, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_composition_sources import import_questions
    from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
    d, panel, app, page, _, _ = setup_panel(desk, lecture, monkeypatch)
    before = deepcopy(panel.composition()); initial_assets = page.image_assets_widget.assets()
    facade, scoped, _, choices, *_ = scoped_lecture
    incoming = import_questions(scoped, choices, [], expected_reference=scoped.word_question_reference(choices))
    imported = incoming['resources'][0]
    store = PreparationImageStore(facade.paths.task_root / 'preparation-v1/images')
    initial_ids = {a['asset_id'] for a in initial_assets}
    monkeypatch.setattr(d.facade, 'preparation_image_bytes',
                        lambda asset: lecture.data if asset['asset_id'] in initial_ids else store.load(asset))
    def accept(dialog):
        dialog.composition_result = deepcopy(incoming); dialog.reject(); return QDialog.DialogCode.Accepted
    monkeypatch.setattr(WordQuestionDialog, 'exec', accept)
    panel.add_handout_questions(); app.processEvents()
    assert len(panel.composition()['resources']) == 2 and panel.selected == imported['id']
    frame = panel.composition()['placements'][imported['id']]
    assert frame['page_id'] == panel.visible_page()
    assert panel.composition()['resources'][0] == before['resources'][0]
    d.travel(False); assert panel.composition() == before
    assert page.image_assets_widget.assets() == initial_assets + incoming['image_assets']
    assert (lecture.facade.paths.task_root / 'preparation-v1/images').exists()
    assert all(store.load(asset) for asset in incoming['image_assets'])
    d.travel(True); panel.selected = before['resources'][0]['id']; panel.refresh()
    edited = panel.composition(); edited['resources'][1]['title'] = '课堂自拟问题标题'; panel.commit(edited)
    saved = deepcopy(d.history.value); panel.selected = before['resources'][0]['id']; panel.refresh()
    panel.add_handout_questions(); assert d.history.value == saved
    assert '已有编辑保持' in d.status.text()

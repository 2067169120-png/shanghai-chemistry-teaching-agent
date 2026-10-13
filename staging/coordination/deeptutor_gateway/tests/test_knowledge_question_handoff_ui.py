"""Knowledge-card selections belong to this lesson and one source version."""
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QDialog
from test_composition_ui import desk as desk, personal
from test_question_explorer_ui import window as window
from test_composition_resource_titles import question
from test_word_question_dialog import qt_app as qt_app, _Facade, _Tasks, _cleanup, _preparation_asset
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource, block
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest


def setup_panel(desk, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_knowledge_questions as backend
    d, panel, app, page = desk
    card = resource('knowledge_note', '原教材概念标题', [block('text', 'knowledge', '完整概念与条件')],
        {'source_path': '合成教材.pdf', 'source_sha256': 'a'*64, 'source_revision': 'b'*64,
         'record_id': 'SYNTHETIC-NOTE', 'pdf_pages': [8], 'printed_pages': [4]})
    card['title'] = '教师自拟的问题标题'
    panel.add_result({'resources': [card]}, digest(d.history.value))
    panel.selected = card['id']; panel.refresh(); app.processEvents()
    assert panel.knowledge_question_button.isVisible()
    context = {'original_title': card['original']['title'], 'curriculum_context': None,
               'search_query': '原教材概念标题', 'binding_sha256': 'c'*64}
    calls = []
    monkeypatch.setattr(backend, 'knowledge_question_context',
        lambda facade, item: calls.append(('resolve', deepcopy(item))) or deepcopy(context))
    monkeypatch.setattr(backend, 'recheck_knowledge_question_context',
        lambda facade, item, value: calls.append(('recheck', deepcopy(item), deepcopy(value))) or True)
    monkeypatch.setattr(d, 'run', lambda _label, operation, done, **kw: done(operation()))
    monkeypatch.setattr(WordQuestionDialog, '_load_catalog', lambda self: None)
    return d, panel, app, page, card, context, calls


def test_cancel_uses_sealed_card_title_and_private_selection(desk, monkeypatch):
    d, panel, app, page, card, context, calls = setup_panel(desk, monkeypatch)
    before = deepcopy(d.history.value); assets = page.image_assets_widget.assets(); seen = []
    def cancel(dialog):
        seen.append((dialog._lesson_topic, dialog._persist_choices, dialog._section_context,
                     dialog._initial_search_query, dialog._handoff_mode))
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(WordQuestionDialog, 'exec', cancel)
    panel.knowledge_question_button.click(); app.processEvents()
    assert seen == [(card['original']['title'], False, None, context['search_query'], 'composition')]
    assert calls == [('resolve', card)] and d.history.value == before
    assert page.image_assets_widget.assets() == assets
    panel.add_result({'resources': [personal()]}, digest(d.history.value)); panel.refresh()
    assert not panel.knowledge_question_button.isVisible()


@pytest.mark.parametrize('change', ['draft_before_open', 'assets_before_open', 'stopping',
                                   'draft_during_pick', 'assets_during_pick'])
def test_changed_lesson_never_receives_late_questions(desk, monkeypatch, change):
    d, panel, app, page, card, context, calls = setup_panel(desk, monkeypatch)
    pending = []
    monkeypatch.setattr(d, 'run', lambda _label, operation, done, **kw: pending.append((operation, done)))
    def changed():
        if change.startswith('draft'):
            panel.add_result({'resources': [personal('选择期间新增的课堂导语')]}, digest(d.history.value))
        elif change.startswith('assets'):
            page.image_assets_widget.set_assets(page.image_assets_widget.assets()+[_preparation_asset(777)])
        else: d._stopping = True
    opened = []
    def accept(dialog):
        opened.append(True); changed()
        dialog.composition_result = {'resources': [question()], 'image_assets': []}
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(WordQuestionDialog, 'exec', accept)
    panel.add_knowledge_questions(); value = pending[0][0]()
    if change.endswith('before_open') or change == 'stopping': changed()
    pending[0][1](value)
    assert opened == ([] if change.endswith('before_open') or change == 'stopping' else [True])
    assert len(pending) == 1 and not any(r['original']['kind']=='complete_question' for r in panel.composition()['resources'])
    d._stopping = False


def test_source_recheck_precedes_one_add_undo_and_duplicate_keeps_edit(desk, monkeypatch):
    d, panel, app, page, card, context, calls = setup_panel(desk, monkeypatch)
    before = deepcopy(d.history.value); incoming = question()
    def accept(dialog):
        dialog.composition_result = {'resources': [deepcopy(incoming)], 'image_assets': []}
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(WordQuestionDialog, 'exec', accept)
    panel.add_knowledge_questions(); app.processEvents()
    assert calls[-1] == ('recheck', card, context)
    assert panel.composition()['resources'] == [card, incoming]
    changed = deepcopy(d.history.value)
    d.travel(False); assert d.history.value == before
    d.travel(True); assert d.history.value == changed
    edited = panel.composition(); edited['resources'][1]['title']='教师保留的例题标题'; panel.commit(edited)
    panel.selected=card['id']; panel.refresh(); saved=deepcopy(d.history.value)
    panel.add_knowledge_questions()
    assert d.history.value == saved and '已有编辑保持' in d.status.text()


@pytest.mark.parametrize('change', ['source', 'draft_after_import', 'closing_after_import'])
def test_final_recheck_or_late_callback_cannot_commit(desk, monkeypatch, change):
    from integrations.deeptutor_shchem_v1 import desktop_knowledge_questions as backend
    d, panel, app, page, card, context, calls = setup_panel(desk, monkeypatch)
    pending = []
    monkeypatch.setattr(d, 'run', lambda _label, operation, done, **kw: pending.append((operation, done)))
    def accept(dialog):
        dialog.composition_result={'resources':[question()], 'image_assets':[]}
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(WordQuestionDialog, 'exec', accept)
    if change=='source':
        def stale(*args): raise ValueError('知识卡原来源已变化')
        monkeypatch.setattr(backend,'recheck_knowledge_question_context',stale)
    panel.add_knowledge_questions(); pending[0][1](pending[0][0]())
    assert len(pending)==2
    before = deepcopy(d.history.value)
    payload = deepcopy(page._payload())
    assets = deepcopy(page.image_assets_widget.assets())
    epochs = (panel._insertion_epoch, panel._material_selection_epoch)
    if change=='source':
        with pytest.raises(ValueError,match='原来源已变化'):pending[1][0]()
    else:
        result=pending[1][0]()
        if change=='draft_after_import':panel.add_result({'resources':[personal()]},digest(d.history.value))
        else:d._close_when_finished=True
        before = deepcopy(d.history.value)
        payload = deepcopy(page._payload())
        assets = deepcopy(page.image_assets_widget.assets())
        epochs = (panel._insertion_epoch, panel._material_selection_epoch)
        message = '^选材窗口已关闭或停止，本批未加入。$' if change=='closing_after_import' else '选例题期间变化'
        with pytest.raises(ValueError,match=message):pending[1][1](result)
    assert d.history.value == before and page._payload() == payload
    assert page.image_assets_widget.assets() == assets
    assert (panel._insertion_epoch, panel._material_selection_epoch) == epochs
    assert panel.composition()['resources'][0] == card
    assert not any(r['original']['kind']=='complete_question' for r in panel.composition()['resources'])
    d._close_when_finished=False


def test_text_search_is_explicit_and_only_applied_on_first_catalog(qt_app):
    facade, tasks = _Facade(), _Tasks()
    facade.word_question_saved_selection=lambda:pytest.fail('read library history')
    dialog=WordQuestionDialog(facade,tasks,initial_search_query='合成检索',lesson_topic='原概念',
                              persist_selection=False,handoff_mode='composition')
    tasks.flush()
    assert dialog.search.text()=='合成检索' and dialog.selections==[]
    assert dialog.volume_filter.currentData() is None and dialog.section_filter.currentData() is None
    assert '本次勾选' in dialog.catalog_note.text()
    dialog.search.setText('教师修改的检索词'); dialog._load_catalog(); tasks.flush()
    assert dialog.search.text()=='教师修改的检索词'
    _cleanup(dialog,tasks)


@pytest.mark.parametrize('mode,target',[('reference','备课参考'),('composition','课件'),('both','当前备课')])
def test_private_scope_note_names_target_and_original_source(qt_app,mode,target):
    facade,tasks=_Facade(),_Tasks()
    dialog=WordQuestionDialog(facade,tasks,required_source_id='A',persist_selection=False,handoff_mode=mode)
    tasks.flush()
    assert target in dialog.catalog_note.text() and '原来源' in dialog.catalog_note.text()
    _cleanup(dialog,tasks)


def test_private_composition_collapses_filters_without_clearing_search(qt_app):
    facade,tasks=_Facade(),_Tasks()
    dialog=WordQuestionDialog(facade,tasks,lesson_topic='原卡标题',
                              persist_selection=False,handoff_mode='composition')
    tasks.flush();dialog.resize(420,780);dialog.show();qt_app.processEvents()
    assert not dialog.filter_host.isVisible() and dialog.filter_toggle.isVisible()
    assert dialog.private_topic_note.text()=='选题：原卡标题' and dialog.tabs.height()>=200
    dialog.filter_toggle.click();qt_app.processEvents()
    assert dialog.filter_host.isVisible()
    dialog.search.setText('教师输入的检索词')
    dialog.filter_toggle.click();qt_app.processEvents()
    assert not dialog.filter_host.isVisible() and dialog.search.text()=='教师输入的检索词'
    _cleanup(dialog,tasks)

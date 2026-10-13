"""Explicit relationships stay separate from lesson history and library choices."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog

from test_word_question_dialog import qt_app as qt_app, _Tasks, _Facade, _cleanup, _preparation_asset
from test_composition_ui import desk as desk, personal
from test_question_explorer_ui import window as window
from test_composition_resource_titles import question
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource, block
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_question_links_dialog import (
    KnowledgeQuestionLinksDialog, _KnowledgeRelationPicker)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog


def card():
    return resource('knowledge_note', '原教材具体知识', [block('text', 'knowledge', '完整知识条件与解释')],
        {'source_path': '合成教材.pdf', 'source_sha256': 'a'*64, 'source_revision': 'b'*64,
         'record_id': 'SYNTHETIC-NOTE', 'pdf_pages': [8], 'printed_pages': [4]})


class Links:
    def __init__(self, status='current', records=True):
        self.calls = []
        self.status = status
        self.fail_context = False
        self.card = card()
        self.facade = _Facade()
        self.knowledge = {'title': '原教材具体知识', 'id': 'SYNTHETIC-NOTE',
                          'original': self.card['original']}
        self.q = {'key': 'Q1', 'revision': 'r1', 'source_name': '合成讲义.docx',
                  'ranges': {'block_start': 2, 'block_end': 4}}
        self.record = {'id': 'KQL-synthetic', 'knowledge': self.knowledge, 'question': self.q,
                       'reason': '具体知识解释本题要求', 'removed': False, 'version': 1}
        self.records = [deepcopy(self.record)] if records else []
        self.version = 1

    def snapshot(self):
        return {'revision': self.version, 'content_sha256': str(self.version),
                'collection': {'records': deepcopy(self.records)}}

    def list(self, knowledge=None, assess=False):
        self.calls.append(('list', deepcopy(knowledge)))
        return [{**deepcopy(row), 'status': 'unknown'} for row in self.records]

    def reverse(self, q, assess=False):
        self.calls.append(('reverse', deepcopy(q)))
        return self.list()

    def knowledge_context(self, knowledge):
        self.calls.append(('knowledge', deepcopy(knowledge)))
        if self.fail_context:
            raise ValueError('原知识来源缺失')
        return {'endpoint': deepcopy(self.knowledge), 'card': deepcopy(self.card),
                'browser_context': {'curriculum_context': None, 'search_query': '原教材具体知识'}}

    def evaluate(self, identity):
        self.calls.append(('evaluate', identity))
        row = next(row for row in self.records if row['id'] == identity)
        return {**deepcopy(row), 'status': self.status, 'knowledge_status': self.status,
                'question_status': self.status}

    def resolve_selection(self, q, *, location=None):
        self.calls.append(('resolve', deepcopy(q), deepcopy(location)))
        return {'selection': {'key': 'Q1', 'revision': 'r1'}, 'endpoint': deepcopy(self.q),
                'row': deepcopy(self.facade.catalog['items'][0]), 'scoped_facade': self.facade,
                'preview': {}}

    def choices(self, resources=None):
        self.calls.append(('choices', deepcopy(resources)))
        return {'items': [{'title': '原知识', 'kind': 'reading_note',
                           'selection': {'note_id': 'SYNTHETIC-NOTE'}}], 'notices': []}

    def prepare(self, knowledge, q, reason=''):
        self.calls.append(('prepare', deepcopy(knowledge), deepcopy(q), reason))
        return {'knowledge': deepcopy(self.knowledge), 'question': deepcopy(self.q),
                'reason': reason, 'knowledge_card': deepcopy(self.card)}

    def save(self, prepared, *, expected, guard=None):
        if guard:
            guard()
        if expected != self.snapshot():
            raise ValueError('关系集合已变化')
        self.calls.append(('save', deepcopy(prepared)))
        row = {**deepcopy(self.record), 'reason': prepared['reason']}
        self.records = [row]
        self.version += 1
        return self.snapshot()

    def edit_reason(self, identity, reason, *, expected, guard=None):
        if guard:
            guard()
        assert expected == self.snapshot()
        self.calls.append(('edit', identity, reason))
        self.records[0]['reason'] = reason
        self.version += 1
        return self.snapshot()

    def remove(self, identity, *, expected, guard=None):
        if guard:
            guard()
        assert expected == self.snapshot()
        self.calls.append(('remove', identity))
        self.records[0]['removed'] = True
        self.version += 1
        return self.snapshot()

    def question_locations(self, q):
        return [{'label': '原批次甲', 'batch_id': 'A', 'archive_source_id': 'X'},
                {'label': '原批次乙', 'batch_id': 'B', 'archive_source_id': 'Y'}]


def opened(qt_app, *, service=None, reverse=False, guard=None, resources=None):
    service = service or Links()
    tasks = _Tasks()
    d = KnowledgeQuestionLinksDialog(service.facade, tasks, service=service, guard=guard,
        knowledge_resource=None if reverse else service.card,
        question_selection=service.q if reverse else None,
        allow_course_add=not reverse, course_resources=resources)
    d.show()
    qt_app.processEvents()
    tasks.flush()
    return d, tasks, service


def test_browsing_and_closing_do_not_save_relations_or_course(qt_app):
    d, tasks, links = opened(qt_app)
    before = links.snapshot()
    d.reason.setPlainText('尚未保存的教师说明')
    d.reject()
    tasks.flush()
    assert links.snapshot() == before and d.course_request is None
    assert not any(call[0] in {'save', 'edit', 'remove'} for call in links.calls)
    _cleanup(d, tasks)


@pytest.mark.parametrize('status', ['missing', 'stale'])
def test_missing_or_changed_relations_retain_snapshot_and_allow_metadata_maintenance(qt_app, status):
    links = Links(status)
    links.fail_context = True
    d, tasks, links = opened(qt_app, service=links)
    assert d.list.count() == 1 and '完整知识条件与解释' in d.detail.toPlainText()
    assert not d.course_button.isEnabled()
    d.reason.setPlainText('补充具体关联理由')
    d.save_button.click()
    tasks.flush()
    assert links.records[0]['reason'] == '补充具体关联理由'
    assert links.status == status and not d.course_button.isEnabled()
    d.remove_button.click()
    tasks.flush()
    assert links.records[0]['removed'] and d.list.count() == 0
    assert d.course_request is None
    _cleanup(d, tasks)


def test_new_relation_requires_explicit_reason_save(qt_app):
    d, tasks, links = opened(qt_app, service=Links(records=False))
    before = links.snapshot()
    d._prepare_relation(links.card, links.q)
    tasks.flush()
    assert links.snapshot() == before and not d.save_button.isEnabled()
    assert '完整知识条件与解释' in d.detail.toPlainText()
    d.reason.setPlainText('本题要求应用原知识的条件解释现象')
    assert d.save_button.isEnabled()
    d.save_button.click()
    tasks.flush()
    assert links.records[0]['reason'] == '本题要求应用原知识的条件解释现象'
    assert len([call for call in links.calls if call[0] == 'save']) == 1
    assert d.course_request is None
    _cleanup(d, tasks)


@pytest.mark.parametrize('change', ['source', 'store'])
def test_changed_source_or_store_rejects_staged_relation(qt_app, change):
    d, tasks, links = opened(qt_app, service=Links(records=False))
    d._prepare_relation(links.card, links.q)
    tasks.flush()
    d.reason.setPlainText('具体理由')
    before = links.snapshot()
    if change == 'source':
        links.q['revision'] = 'new'
    else:
        links.version += 1
    d.save_reason()
    tasks.flush()
    assert not any(call[0] == 'save' for call in links.calls)
    assert links.records == before['collection']['records']
    _cleanup(d, tasks)


def test_guard_and_close_discard_delayed_callbacks(qt_app):
    state = {'changed': False}
    def guard():
        if state['changed']:
            raise ValueError('本课已变化')
    d, tasks, links = opened(qt_app, guard=guard)
    before = links.snapshot()
    d.reason.setPlainText('保存前发生变化')
    d.save_reason()
    state['changed'] = True
    tasks.flush()
    assert links.snapshot() == before
    state['changed'] = False
    d.refresh()
    pending = tasks.pending[0]
    value = pending['operation']()
    d.reject()
    pending['on_success'](value)
    assert d._closed and d.course_request is None and links.snapshot() == before
    tasks.flush()
    _cleanup(d, tasks)


@pytest.mark.parametrize('status,removed', [('stale', False), ('missing', False), ('current', True)])
def test_invalid_or_removed_relation_never_returns_course_selection(qt_app, status, removed):
    links = Links(status)
    links.records[0]['removed'] = removed
    d, tasks, links = opened(qt_app, service=links)
    assert not d.course_button.isEnabled()
    d.choose_for_course()
    tasks.flush()
    assert d.course_request is None
    _cleanup(d, tasks)


def test_explicit_course_choice_returns_complete_reference_without_mutating_links(qt_app):
    d, tasks, links = opened(qt_app)
    before = links.snapshot()
    d.course_button.click()
    tasks.flush()
    assert d.course_request['link_id'] == 'KQL-synthetic'
    assert d.course_request['selection'] == {'key': 'Q1', 'revision': 'r1', 'points': 2}
    assert d.course_request['reference']['include_images'] is True
    assert links.snapshot() == before
    _cleanup(d, tasks)


def test_current_relation_for_another_knowledge_version_cannot_enter_selected_old_card(qt_app):
    links = Links()
    links.records[0]['knowledge']['id'] = 'another-version'
    d, tasks, links = opened(qt_app, service=links)
    assert d._assessed['status'] == 'current'
    assert not d.course_button.isEnabled()
    d.choose_for_course()
    tasks.flush()
    assert d.course_request is None
    _cleanup(d, tasks)


@pytest.mark.parametrize('width', [360, 420, 700])
def test_narrow_window_keeps_close_and_course_actions_reachable(qt_app, width):
    d, tasks, _ = opened(qt_app)
    d.resize(width, 800)
    qt_app.processEvents()
    assert d.width() == width and d.split.orientation() == Qt.Orientation.Vertical
    assert d.close_button.isVisible() and d.course_button.isVisible()
    assert d.rect().contains(d.close_button.geometry().center())
    assert d.close_button.mapTo(d, d.close_button.rect().center()).x() < d.width()
    d.resize(1120, 790)
    qt_app.processEvents()
    assert d.split.orientation() == Qt.Orientation.Horizontal
    _cleanup(d, tasks)


def test_reverse_candidate_uses_exact_handout_resource_and_cancel_keeps_store(qt_app, monkeypatch):
    fake_card = {'original': {'kind': 'handout_knowledge'}, 'title': '教师问题标题'}
    links = Links()
    links.choices = lambda resources=None: {'items': [{'kind': 'handout_section', 'title': '原讲义知识段',
        'card': deepcopy(fake_card), 'summary': '原内容'}], 'notices': []}
    d, tasks, links = opened(qt_app, service=links, reverse=True, resources=[fake_card])
    def choose(picker):
        picker.choose()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(_KnowledgeRelationPicker, 'exec', choose)
    before = links.snapshot()
    d.choose_endpoint()
    tasks.flush()
    assert d._new_knowledge == fake_card
    assert links.snapshot() == before and not d.course_button.isVisible()
    _cleanup(d, tasks)


def test_reader_reverse_entry_preserves_all_library_selections(qt_app, monkeypatch):
    facade, tasks = _Facade(), _Tasks()
    d = WordQuestionDialog(facade, tasks)
    tasks.flush()
    before = deepcopy(d._selected)
    seen = []
    def close(dialog):
        seen.append(deepcopy(dialog.question))
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(KnowledgeQuestionLinksDialog, 'exec', close)
    from integrations.deeptutor_shchem_v1 import desktop_knowledge_question_links as backend
    monkeypatch.setattr(backend, 'KnowledgeQuestionLinks', lambda _facade: Links())
    d._manage_knowledge_links()
    assert seen == [d._items[d._current_key]] and d._selected == before
    private = WordQuestionDialog(facade, tasks, allow_knowledge_links=False, persist_selection=False)
    tasks.flush()
    assert private.knowledge_links_button.isHidden()
    _cleanup(private, tasks)
    _cleanup(d, tasks)


def test_original_question_reader_has_private_selection_and_exact_source(qt_app, monkeypatch):
    d, tasks, links = opened(qt_app)
    before = links.snapshot()
    seen = []
    monkeypatch.setattr(WordQuestionDialog, '_load_catalog', lambda _d: None)
    def close(reader):
        seen.append((reader._initial_question_key, reader._required_source_id,
                     reader._persist_choices, reader._allow_knowledge_links))
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(WordQuestionDialog, 'exec', close)
    d.open_question()
    tasks.flush()
    assert seen == [('Q1', links.facade.catalog['items'][0]['source_id'], False, False)]
    assert links.snapshot() == before and d.course_request is None
    _cleanup(d, tasks)


def test_course_question_archive_choice_is_explicit_and_source_bound(qt_app, monkeypatch):
    links, tasks = Links(), _Tasks()
    d = KnowledgeQuestionLinksDialog(links.facade, tasks, service=links,
                                     question_selection=question())
    d.show()
    qt_app.processEvents()
    tasks.flush()
    before = links.snapshot()
    def choose(picker):
        candidates = picker.findChildren(type(d.list))
        assert len(candidates) == 1 and candidates[0].count() == 2
        candidates[0].setCurrentRow(1)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', choose)
    d.choose_question_location()
    tasks.flush()
    assert ('resolve', d._course_question_resource, {'batch_id': 'B', 'archive_source_id': 'Y'}) in links.calls
    assert links.snapshot() == before
    _cleanup(d, tasks)


def test_json_restore_requires_inspected_preview_and_explicit_conflict_policy(qt_app, monkeypatch, tmp_path):
    from integrations.deeptutor_shchem_v1 import desktop_knowledge_question_links_transfer as transfer
    d, tasks, links = opened(qt_app)
    path = str(tmp_path/'关联.json')
    calls = []
    plan = {'counts': {'new': 2, 'duplicates': 1, 'conflicts': 1},
            'conflicts': ['KQL-synthetic'], 'conflict_details': [{'current_reason': '当前理由',
            'incoming_reason': '文件理由'}], 'plan_sha256': 'synthetic'}
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (path, 'JSON'))
    monkeypatch.setattr(transfer, 'inspect_knowledge_question_links',
        lambda service, selected: calls.append(('inspect', selected)) or deepcopy(plan))
    def restore(service, selected, expected, *, conflict_policy, guard):
        guard()
        calls.append(('restore', selected, deepcopy(expected), conflict_policy))
        return {'result': service.snapshot()}
    monkeypatch.setattr(transfer, 'restore_knowledge_question_links', restore)
    assert not d.restore_button.isEnabled()
    d.inspect_relations()
    tasks.flush()
    assert calls == [('inspect', path)]
    assert '当前理由' in d.transfer_detail.toPlainText() and '文件理由' in d.transfer_detail.toPlainText()
    assert d.restore_button.isEnabled() and '保留当前冲突' in d.restore_button.text()
    d.restore_button.click()
    tasks.flush()
    assert calls[-1] == ('restore', path, plan, 'keep_current')
    assert d._plan is None and not d.restore_button.isEnabled()
    _cleanup(d, tasks)


def panel_setup(desk, monkeypatch):
    d, panel, app, page = desk
    source = card()
    panel.add_result({'resources': [source]}, digest(d.history.value))
    panel.selected = source['id']
    panel.refresh()
    service = Links()
    from integrations.deeptutor_shchem_v1 import desktop_knowledge_question_links as backend
    monkeypatch.setattr(backend, 'KnowledgeQuestionLinks', lambda _facade: service)
    monkeypatch.setattr(d, 'run', lambda _label, operation, done, **_kw: done(operation()))
    return d, panel, app, page, source, service


def test_panel_cancel_preserves_location_draft_and_images(desk, monkeypatch):
    d, panel, app, page, source, service = panel_setup(desk, monkeypatch)
    before = deepcopy(d.history.value)
    position = (panel.selected, panel.frame_index, panel._view_page_id)
    assets = page.image_assets_widget.assets()
    monkeypatch.setattr(KnowledgeQuestionLinksDialog, 'exec', lambda _dialog: QDialog.DialogCode.Rejected)
    panel.manage_knowledge_question_links()
    assert d.history.value == before and page.image_assets_widget.assets() == assets
    assert (panel.selected, panel.frame_index, panel._view_page_id) == position
    assert panel.knowledge_links_button.isVisible()


def test_panel_checked_course_import_is_one_undo_and_keeps_edited_duplicate(desk, monkeypatch):
    d, panel, app, page, source, service = panel_setup(desk, monkeypatch)
    before = deepcopy(d.history.value)
    def choose(dialog):
        dialog.course_request = {'link_id': 'KQL-synthetic', 'snapshot': service.snapshot(),
            'selection': {'key': 'Q1', 'revision': 'r1', 'points': 2}, 'reference': {'complete': True}}
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(KnowledgeQuestionLinksDialog, 'exec', choose)
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
    incoming = question()
    monkeypatch.setattr(sources, 'import_questions', lambda *args, **kw:
                        {'resources': [deepcopy(incoming)], 'image_assets': []})
    panel.manage_knowledge_question_links()
    changed = deepcopy(d.history.value)
    assert len(panel.composition()['resources']) == 2
    d.travel(False)
    assert d.history.value == before
    d.travel(True)
    assert d.history.value == changed
    value = panel.composition()
    value['resources'][1]['title'] = '教师已修改的课堂例题标题'
    panel.commit(value)
    panel.selected = source['id']
    panel.refresh()
    edited = deepcopy(d.history.value)
    panel.manage_knowledge_question_links()
    assert d.history.value == edited and '已有标题' in d.status.text()


@pytest.mark.parametrize('change', ['draft', 'assets', 'selected', 'closing', 'relation'])
def test_late_related_question_never_enters_changed_course(desk, monkeypatch, change):
    d, panel, app, page, source, service = panel_setup(desk, monkeypatch)
    pending = []
    monkeypatch.setattr(d, 'run', lambda _label, operation, done, **kw: pending.append((operation, done)))
    def choose(dialog):
        dialog.course_request = {'link_id': 'KQL-synthetic', 'snapshot': service.snapshot(),
            'selection': {'key': 'Q1', 'revision': 'r1', 'points': 2}, 'reference': {'complete': True}}
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(KnowledgeQuestionLinksDialog, 'exec', choose)
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
    monkeypatch.setattr(sources, 'import_questions', lambda *args, **kw:
                        {'resources': [question()], 'image_assets': []})
    panel.manage_knowledge_question_links()
    before = deepcopy(d.history.value)
    payload = deepcopy(page._payload())
    assets = deepcopy(page.image_assets_widget.assets())
    epochs = (panel._insertion_epoch, panel._material_selection_epoch)
    if change == 'relation':
        service.version += 1
        with pytest.raises(ValueError, match='关联在加入期间变化'):
            pending[0][0]()
    else:
        result = pending[0][0]()
        if change == 'draft':
            panel.add_result({'resources': [personal()]}, digest(d.history.value))
        elif change == 'assets':
            page.image_assets_widget.set_assets([_preparation_asset(778)])
        elif change == 'selected':
            panel.selected = None
        else:
            d._close_when_finished = True
        before = deepcopy(d.history.value)
        payload = deepcopy(page._payload())
        assets = deepcopy(page.image_assets_widget.assets())
        epochs = (panel._insertion_epoch, panel._material_selection_epoch)
        message = '^选材窗口已关闭或停止，本批未加入。$' if change == 'closing' else '关联期间变化'
        with pytest.raises(ValueError, match=message):
            pending[0][1](result)
    assert d.history.value == before and page._payload() == payload
    assert page.image_assets_widget.assets() == assets
    assert (panel._insertion_epoch, panel._material_selection_epoch) == epochs
    assert panel.composition()['resources'][0] == source
    assert not any(row['original']['kind'] == 'complete_question' for row in panel.composition()['resources'])
    d._close_when_finished = False

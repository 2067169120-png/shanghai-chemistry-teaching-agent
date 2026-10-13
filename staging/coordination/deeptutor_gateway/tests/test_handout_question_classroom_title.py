"""Question work titles use minimal Qt and private frozen native endpoints.

No full application, Facade, StateStore or Recovery constructor is used. The
compiler port emits authored, validated native-resource dictionaries; actual
Word source import is covered independently, not claimed by these fixtures.
"""
from copy import deepcopy
from hashlib import sha256
import os
import tempfile
from threading import RLock
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog

from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as backend
from integrations.deeptutor_shchem_v1.desktop_composition_sources import merge_question_result
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource, visible_blocks
from integrations.deeptutor_shchem_v1.desktop_workbench import handout_material_workspace as ui
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


TITLE = '三种纤维的结构与性能有什么关系？'
PRINTED_SOURCE = '（24-25高三上·上海·阶段练习）'
RAW_QUESTION = ('例1（双选）' + PRINTED_SOURCE
                + '乒乓球底板常用芳纶纤维、玻璃纤维、碳纤维等材料。下列说法正确的是')
CATALOG = '读取讲义选材资料目录'
OUTLINE = '读取当前讲义选材原文'
CONFIRM = '核对本次讲义选材来源'


def source(tmp_path, number=0, role='handout'):
    return {'schema': 'shchem.handout-knowledge-source.v1', 'batch_id': 'PRIVATE-BATCH',
        'source_id': f'PRIVATE-SOURCE-{number}', 'source_name': f'合成讲义{number}.docx',
        'source_path': str(tmp_path / f'合成讲义{number}.docx'),
        'source_sha256': sha256(f'source-{number}'.encode()).hexdigest(),
        'source_revision': sha256(f'preview-{number}'.encode()).hexdigest(), 'source_role': role}


def selection(binding, key='Q1', points=2):
    return {'key': key, 'revision': sha256(key.encode()).hexdigest(), 'points': points,
        'source_sha256': binding['source_sha256'], 'scoped_source_document': deepcopy(binding)}


def request(binding, *questions):
    return {'schema': backend.SCHEMA, 'source': deepcopy(binding), 'knowledge_requests': [],
        'image_selections': [], 'question_selections': list(deepcopy(questions)),
        'selection_order': [{'kind': 'question', 'index': i} for i in range(len(questions))]}


def native_question(chosen, *, answer_image=False):
    bound = chosen['scoped_source_document']
    rows = [block('text', 'shared_material', '完整共同材料与条件。', 1),
        block('text', 'question', chosen['key'] + '：原题题干和全部选项。', 2),
        block('text', 'answer', '【答案】全部答案与解析。', 3)]
    if answer_image: rows.append(block('image', 'answer', 'IMG-' + 'f' * 64, 3))
    item = resource('complete_question', '合成讲义 · 原文区块1—3', rows,
        {'source_path': bound['source_path'], 'source_sha256': bound['source_sha256'],
         'source_revision': bound['source_revision'], 'question_key': chosen['key'],
         'scope_review_sha256': sha256(chosen['key'].encode()).hexdigest(),
         'scope_status': 'complete_native_scope',
         'hierarchy': dict.fromkeys(('paper_id', 'theme_big_question_id', 'printed_question_id', 'atomic_part_id'), 'unknown')})
    item['title'] = '原题题干默认摘录'
    return item


@pytest.fixture
def bound(tmp_path):
    return source(tmp_path)


def test_legacy_and_optional_title_requests_copy_and_normalize_only_title(bound):
    old = request(bound, selection(bound)); before = deepcopy(old)
    assert backend.validate_material_request(old) == old and old == before
    extended = deepcopy(old); extended['question_selections'][0]['title'] = '  ' + TITLE + '  '
    before = deepcopy(extended); checked = backend.validate_material_request(extended)
    assert checked['question_selections'][0] == {**old['question_selections'][0], 'title': TITLE}
    assert extended == before


@pytest.mark.parametrize('title', ['', ' \t\n ', None, False, 7, [], '字' * 161])
def test_optional_question_title_rejects_empty_wrong_type_and_overlong(bound, title):
    chosen = {**selection(bound), 'title': title}
    with pytest.raises(ValueError, match='1至160字'):
        backend.validate_material_request(request(bound, chosen))


@pytest.mark.parametrize('extra', ['teacher_only', 'purpose', 'source_title'])
def test_question_metadata_does_not_accept_role_or_source_controls(bound, extra):
    chosen = {**selection(bound), 'title': TITLE, extra: True}
    with pytest.raises(ValueError, match='确切归档'):
        backend.validate_material_request(request(bound, chosen))


def test_overlay_follows_source_identity_and_preserves_original_answers_and_order(bound):
    q1, q2 = selection(bound), selection(bound, 'Q2')
    items = [native_question(q2, answer_image=True), native_question(q1)]
    incoming = {'resources': items, 'image_assets': []}; before = deepcopy(incoming)
    choices = [{**q1, 'title': TITLE}, {**q2, 'title': '同一个标题也不能改变来源身份'}]
    changed = backend._question_classroom_titles(incoming, choices)
    assert [r['id'] for r in changed['resources']] == [r['id'] for r in items]
    assert [r['title'] for r in changed['resources']] == [choices[1]['title'], TITLE]
    for old, new in zip(items, changed['resources']):
        assert new['original'] == old['original'] and new['original_sha256'] == old['original_sha256']
        assert new['blocks'] == old['blocks'] and new['locked'] == old['locked']
        assert visible_blocks(new, student=True) == visible_blocks(old, student=True)
    assert incoming == before
    assert backend._stamp(incoming) != backend._stamp(changed)
    mixed = request(bound, *choices)
    mixed['selection_order'].reverse()
    ordered = backend._ordered_additions(mixed, [], [], changed['resources'])
    assert [r['original']['evidence']['question_key'] for r in ordered] == ['Q2', 'Q1']


@pytest.mark.parametrize('mismatch', ['key', 'sha', 'revision', 'missing', 'duplicate'])
def test_overlay_rejects_unmatched_native_sources(bound, mismatch):
    chosen = {**selection(bound), 'title': TITLE}; item = native_question(chosen)
    incoming = {'resources': [item], 'image_assets': []}; choices = [deepcopy(chosen)]
    if mismatch == 'key': choices[0]['key'] = 'OTHER'
    elif mismatch == 'sha': choices[0]['source_sha256'] = 'f' * 64
    elif mismatch == 'revision': choices[0]['scoped_source_document']['source_revision'] = 'f' * 64
    elif mismatch == 'missing': incoming['resources'] = []
    else: choices.append(deepcopy(chosen))
    before = deepcopy(incoming)
    with pytest.raises(ValueError): backend._question_classroom_titles(incoming, choices)
    assert incoming == before


def test_new_title_does_not_replace_existing_edited_question(bound):
    chosen = selection(bound); old = native_question(chosen)
    old['title'] = '教师已改好的本课问题'
    old['blocks'][1]['value'] = '教师已经编辑的作品题干。'
    before = deepcopy(old)
    incoming = backend._question_classroom_titles(
        {'resources': [native_question(chosen)], 'image_assets': []}, [{**chosen, 'title': TITLE}])
    assert merge_question_result(incoming, [old], []) == {'resources': [], 'image_assets': []}
    assert old == before


@pytest.fixture
def frozen_native(tmp_path, monkeypatch, bound):
    # Real prepare/publish/merge/stamp; only the native source port is frozen.
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    state = {'changed': False, 'imports': [], 'references': [], 'compiled': []}
    facade = SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path / 'private-tasks'))
    words = SimpleNamespace(_lock=RLock())
    facade._word_questions = lambda: words
    def context(_facade, document):
        if state['changed'] or document != bound: raise ValueError('原讲义版本变化')
        return {'document_source': deepcopy(bound)}
    def reference(choices, *, include_images):
        assert include_images and all(set(c) == {'key', 'revision', 'points'} for c in choices)
        state['references'].append(deepcopy(choices))
        return {'selections': deepcopy(choices), 'include_images': True}
    facade.word_question_reference = reference
    def native(_facade, choices, assets, *, expected_reference):
        assert expected_reference == {'selections': choices, 'include_images': True}
        assert all(set(c) == {'key', 'revision', 'points'} for c in choices)
        state['imports'].append(deepcopy(choices))
        items = [native_question({**c, 'source_sha256': bound['source_sha256'],
                                  'scoped_source_document': deepcopy(bound)}) for c in choices]
        state['compiled'].append(deepcopy(items))
        return {'resources': items, 'image_assets': deepcopy(assets)}
    monkeypatch.setattr(backend, 'handout_source_context', context)
    monkeypatch.setattr(backend, 'HandoutQuestionFacade', lambda staged, binding: staged)
    monkeypatch.setattr(backend, 'import_questions', native)
    return SimpleNamespace(facade=facade, state=state, source=bound)


@pytest.mark.parametrize('with_title', [False, True])
def test_prepare_publish_recompiles_title_and_keeps_source_reference_contract(frozen_native, with_title):
    c = frozen_native; chosen = selection(c.source)
    if with_title: chosen['title'] = TITLE
    value = request(c.source, chosen)
    before = deepcopy(value)
    publication = backend.prepare_handout_materials(c.facade, value, [], [])
    first = publication.result['resources'][0]
    assert first['title'] == (TITLE if with_title else '原题题干默认摘录')
    assert first['original'] == c.state['compiled'][0][0]['original']
    assert first['original_sha256'] == c.state['compiled'][0][0]['original_sha256']
    published = publication.publish(); publication.recheck_published(); publication.accept()
    assert published == publication.result and value == before
    assert len(c.state['imports']) == 3  # prepare, publish, pending recheck
    assert all(set(row) == {'key', 'revision', 'points'} for call in c.state['references'] for row in call)


@pytest.mark.parametrize('mutation', ['result_title', 'request_title', 'source_changed'])
def test_prepared_title_or_source_change_cannot_pass_publication(frozen_native, mutation):
    c = frozen_native
    publication = backend.prepare_handout_materials(c.facade,
        request(c.source, {**selection(c.source), 'title': TITLE}), [], [])
    if mutation == 'result_title': publication.result['resources'][0]['title'] = '确认后被改动'
    elif mutation == 'request_title': publication.request['question_selections'][0]['title'] = '确认后被改动'
    else: c.state['changed'] = True
    with pytest.raises(ValueError): publication.publish()
    assert not publication._published
    publication.discard()


class DeferredTasks:
    def __init__(self): self.pending, self.cancelled, self.serial = [], [], 0
    def submit(self, label, operation, *, on_success, on_failure):
        self.serial += 1; identity = str(self.serial)
        self.pending.append(SimpleNamespace(id=identity, label=label, operation=operation,
                                            success=on_success, failure=on_failure))
        return identity
    def cancel(self, identity): self.cancelled.append(identity)
    def take(self, label):
        item = next(r for r in self.pending if r.label == label)
        self.pending.remove(item); return item
    def succeed(self, label):
        job = self.take(label); job.success(job.operation())


@pytest.fixture(scope='module')
def app():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    value = QApplication.instance() or QApplication([])
    value.setStyle('Fusion'); install_ui_font(value)
    return value


@pytest.fixture
def workspace(app, tmp_path, monkeypatch):
    sources = [source(tmp_path, i) for i in range(2)]
    catalog = [{k: v for k, v in s.items() if k not in {'source_path', 'source_revision'}} for s in sources]
    state = {'changed': False}
    def outline(_facade, selected):
        bound = next(s for s in sources if s['source_id'] == selected['source_id'])
        return {'source': deepcopy(bound), 'outline_revision': 'd' * 64, 'sections': [],
            'preview': {'source_name': bound['source_name'], 'source_sha256': bound['source_sha256'],
                'revision': bound['source_revision'], 'blocks': [{'index': 1, 'text': '完整合成原文。',
                    'label': '原文区块1', 'warnings': []}], 'assets': [], 'sections': [], 'warnings': []}}
    def context(_facade, bound):
        result = deepcopy(bound)
        if state['changed']: result['source_revision'] = 'f' * 64
        return {'document_source': result}
    monkeypatch.setattr(ui, 'handout_knowledge_sources', lambda _: {'sources': deepcopy(catalog), 'issues': []})
    monkeypatch.setattr(ui, 'handout_knowledge_outline', outline)
    monkeypatch.setattr(ui, 'handout_source_context', context)
    tasks = DeferredTasks(); dialog = ui.HandoutMaterialWorkspace(SimpleNamespace(), tasks)
    tasks.succeed(CATALOG); dialog.source.setCurrentIndex(1); tasks.succeed(OUTLINE)
    dialog._add_selections('question', [selection(sources[0]), selection(sources[0], 'Q2')],
                           {'Q1': '完整原题与答案', 'Q2': '第二完整题与答案'})
    dialog.show(); app.processEvents()
    yield SimpleNamespace(dialog=dialog, tasks=tasks, state=state, sources=sources, app=app)
    dialog.reject(); dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()


def open_title(c, index=0):
    c.dialog.selected.setCurrentRow(index); c.dialog.edit_button.click()
    assert isinstance(c.dialog._child, ui._MaterialMetadataDialog)
    return c.dialog._child


def test_native_editor_only_title_preserves_points_identity_and_mixed_position(workspace):
    c = workspace; before = deepcopy(c.dialog._selections)
    editor = open_title(c)
    assert editor.teacher_only is None and not editor.findChildren(QCheckBox)
    assert editor.title.text() == '完整原题与答案' and editor.title.maxLength() == 160
    editor.title.setText('  ' + TITLE + '  '); editor.confirm.click()
    rows = c.dialog._selections
    assert rows[0]['value'] == {**before[0]['value'], 'title': TITLE}
    assert rows[0]['key'] == before[0]['key'] and rows[1] == before[1]
    assert TITLE in c.dialog.selected.item(0).text() and '2 分' in c.dialog.selected.item(0).text()
    reopened = open_title(c); assert reopened.title.text() == TITLE
    reopened.cancel_button.click()
    c.dialog.confirm.click(); c.tasks.succeed(CONFIRM)
    assert c.dialog.request['question_selections'][0] == rows[0]['value']
    assert c.dialog.request['selection_order'] == [{'kind': 'question', 'index': 0}, {'kind': 'question', 'index': 1}]


def test_reselect_same_question_keeps_edited_title_and_accepts_points_update(workspace):
    c = workspace; editor = open_title(c); editor.title.setText(TITLE); editor.confirm.click()
    c.dialog._add_selections('question', [selection(c.sources[0], points=5)], {'Q1': '重新读取的原题标题'})
    row = c.dialog._selections[0]
    assert row['value'] == {**selection(c.sources[0], points=5), 'title': TITLE}
    assert TITLE in row['label'] and len(c.dialog._selections) == 2


def test_reselection_does_not_carry_title_to_another_exact_document(workspace):
    c = workspace; editor = open_title(c); editor.title.setText(TITLE); editor.confirm.click()
    c.dialog._add_selections('question', [selection(c.sources[1])], {'Q1': '另一来源'})
    assert 'title' not in c.dialog._selections[0]['value']


def test_cancel_and_blank_title_leave_pending_source_choices_untouched(workspace):
    c = workspace; before = deepcopy(c.dialog._selections); editor = open_title(c)
    editor.title.setText('  '); editor.confirm.click()
    assert c.dialog._child is editor and c.dialog._selections == before
    editor.title.setText(TITLE); editor.cancel_button.click()
    assert c.dialog._selections == before and c.dialog.request is None


def test_question_title_editor_has_no_answer_role_control_even_for_answer_source(workspace):
    c = workspace
    editor = ui._MaterialMetadataDialog(c.dialog._selections[0], answer_source=True, parent=c.dialog)
    assert editor.teacher_only is None and not editor.findChildren(QCheckBox)
    editor.title.setText(TITLE); editor.confirm.click()
    assert editor.metadata == {'title': TITLE}
    editor.deleteLater()


def test_native_question_default_prefills_clean_stem_and_saves_without_source_prefix(workspace):
    c = workspace
    c.dialog._add_selections('question', [selection(c.sources[0])], {'Q1': RAW_QUESTION})
    before = deepcopy(c.dialog._selections)
    editor = open_title(c)
    clean = RAW_QUESTION.replace(PRINTED_SOURCE, '', 1)
    assert editor.title.text() == clean and clean.startswith('例1（双选）')
    editor.confirm.click()  # Deliberately save the native initial value unchanged.
    assert c.dialog._selections[0]['value'] == {**before[0]['value'], 'title': clean}
    assert c.dialog._selections[0]['key'] == before[0]['key']
    assert c.dialog._selections[1] == before[1]
    c.dialog.confirm.click(); c.tasks.succeed(CONFIRM)
    assert c.dialog.request['question_selections'][0]['title'] == clean
    assert PRINTED_SOURCE not in c.dialog.request['question_selections'][0]['title']


def test_native_question_explicit_title_is_prefilled_and_saved_without_source_cleaning(workspace, monkeypatch):
    c = workspace; explicit = '例1（双选）' + PRINTED_SOURCE + '教师明确写好的引导问题'
    c.dialog._add_selections('question', [{**selection(c.sources[0]), 'title': explicit}], {'Q1': RAW_QUESTION})
    before = deepcopy(c.dialog._selections)
    def forbidden(_text): raise AssertionError('An edited title must not be cleaned again')
    monkeypatch.setattr(ui, 'printed_source_span', forbidden)
    editor = open_title(c)
    assert editor.title.text() == explicit
    editor.confirm.click()
    assert c.dialog._selections == before


@pytest.mark.parametrize('change', ['close', 'source_switch', 'outline_revision', 'selection_changed'])
def test_late_metadata_cannot_replace_changed_or_closed_workspace(workspace, change):
    c = workspace; editor = open_title(c); editor.title.setText(TITLE)
    if change == 'close': c.dialog.reject()
    elif change == 'source_switch': c.dialog.source.setCurrentIndex(2)
    elif change == 'outline_revision': c.dialog._outline['outline_revision'] = 'f' * 64
    else: c.dialog._selections[0]['value']['points'] = 9
    before = deepcopy(c.dialog._selections)
    editor.metadata = {'title': TITLE}; editor.finished.emit(QDialog.DialogCode.Accepted)
    assert c.dialog._selections == before and c.dialog.request is None


@pytest.mark.parametrize('change', ['source_changed', 'closed'])
def test_confirmation_still_checks_source_and_ignores_late_completion(workspace, change):
    c = workspace; editor = open_title(c); editor.title.setText(TITLE); editor.confirm.click()
    c.dialog.confirm.click(); job = c.tasks.take(CONFIRM)
    if change == 'source_changed': c.state['changed'] = True
    else: c.dialog.reject()
    job.success(job.operation())
    assert c.dialog.request is None
    if change == 'source_changed': assert c.dialog._source_stale


@pytest.mark.parametrize('extra', ['teacher_only', 'purpose'])
def test_late_question_metadata_cannot_smuggle_other_fields(workspace, extra):
    c = workspace; before = deepcopy(c.dialog._selections); editor = open_title(c)
    editor.metadata = {'title': TITLE, extra: True}
    editor.finished.emit(QDialog.DialogCode.Accepted)
    assert c.dialog._selections == before and '不完整' in c.dialog.status.text()

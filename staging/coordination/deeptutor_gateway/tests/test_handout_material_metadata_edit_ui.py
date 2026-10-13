"""In-memory classroom edits keep mixed selections and source bindings intact."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QDialog

from test_desktop_library_ui import _ManualBridge, qt_app
from test_desktop_studio_ui import settle
from test_handout_knowledge_picker_ui import _SyntheticLibrary
from integrations.deeptutor_shchem_v1.desktop_workbench import handout_material_workspace as module


CATALOG = '读取讲义选材资料目录'
OUTLINE = '读取当前讲义选材原文'
CONFIRM = '核对本次讲义选材来源'


@pytest.fixture
def material_edit(qt_app, monkeypatch):
    """Only authored dictionaries and controllable tasks; no profile or root window."""
    library = _SyntheticLibrary()
    bridge = _ManualBridge()
    context_calls = []
    def context(_facade, binding):
        context_calls.append(deepcopy(binding))
        outline = library.outlines[binding['source_id']]
        return {'document_source': deepcopy(outline['source']), 'images': [], 'contexts': {}}
    monkeypatch.setattr(module, 'handout_knowledge_sources', library.catalog)
    monkeypatch.setattr(module, 'handout_knowledge_outline', library.outline)
    monkeypatch.setattr(module, 'handout_source_context', context)
    dialog = module.HandoutMaterialWorkspace(SimpleNamespace(), bridge)
    dialog.show()
    bridge.succeed(bridge.take(CATALOG), run=True)
    dialog.source.setCurrentIndex(1)
    bridge.succeed(bridge.take(OUTLINE), run=True)
    settle(qt_app)
    case = SimpleNamespace(dialog=dialog, library=library, bridge=bridge,
                           context_calls=context_calls, app=qt_app)
    yield case
    dialog.reject()
    dialog.deleteLater()
    settle(qt_app)


def _knowledge(dialog, number=0):
    return {'source': deepcopy(dialog._document), 'outline_revision': dialog._outline['outline_revision'],
        'section': deepcopy(dialog._outline['sections'][number]),
        'title': '知识点之间怎样联系？', 'teacher_only': False}


def _image(dialog, purpose='联系知识主干', *, teacher_only=False, title='怎样读懂这张原图？'):
    return {'source': deepcopy(next(iter(dialog._picture_rows.values()))),
            'title': title, 'purpose': purpose, 'teacher_only': teacher_only}


def _question(dialog, points=2):
    return {'key': 'Q1', 'revision': 'revision-Q1', 'points': points,
        'source_sha256': dialog._document['source_sha256'], 'scoped_source_document': deepcopy(dialog._document)}


def _mixed(case):
    dialog = case.dialog
    dialog._add_selections('knowledge', [_knowledge(dialog)])
    dialog._add_selections('image', [_image(dialog)])
    dialog._add_selections('question', [_question(dialog)], {'Q1': '完整题面及来源答案'})
    dialog._add_selections('image', [_image(dialog, '小结时再读原图')])
    return deepcopy(dialog._selections)


def _edit(case, index):
    case.dialog.selected.setCurrentRow(index)
    assert case.dialog.edit_button.isEnabled()
    case.dialog.edit_button.click()
    assert isinstance(case.dialog._child, module._MaterialMetadataDialog)
    settle(case.app)
    return case.dialog._child


def test_knowledge_editor_prefills_and_replaces_only_classroom_fields(material_edit):
    case = material_edit
    before = _mixed(case)
    editor = _edit(case, 0)
    assert editor.title.text() == before[0]['value']['title']
    assert not editor.teacher_only.isChecked() and editor.teacher_only.isEnabled()
    assert not hasattr(editor, 'purpose') and not hasattr(editor, 'points')
    editor.title.setText('分子结构怎样解释性质？')
    editor.teacher_only.setChecked(True)
    editor.confirm.click()
    after = case.dialog._selections
    expected = {**before[0]['value'], 'title': '分子结构怎样解释性质？', 'teacher_only': True}
    assert after[0]['value'] == expected and after[0]['key'] == before[0]['key']
    assert after[1:] == before[1:] and case.dialog.selected.currentRow() == 0
    assert case.dialog._child is None and '原授课位置' in case.dialog.status.text()


def test_image_editor_preserves_occurrence_binding_and_same_picture_other_use(material_edit):
    case = material_edit
    before = _mixed(case)
    editor = _edit(case, 1)
    assert editor.title.text() == before[1]['value']['title']
    assert editor.purpose.toPlainText() == before[1]['value']['purpose']
    editor.title.setText('观察这条分支，能得到什么结论？')
    editor.purpose.setPlainText('引导学生比较两个条件，再记下结论')
    editor.teacher_only.setChecked(True)
    editor.confirm.click()
    after = case.dialog._selections
    assert after[1]['value'] == {**before[1]['value'], 'title': '观察这条分支，能得到什么结论？',
        'purpose': '引导学生比较两个条件，再记下结论', 'teacher_only': True}
    assert after[1]['value']['source'] == before[1]['value']['source']
    assert [after[i] for i in (0, 2, 3)] == [before[i] for i in (0, 2, 3)]
    assert after[1]['key'] != before[1]['key'] and len(after) == 4
    assert case.dialog.selected.currentRow() == 1
    assert '用途：引导学生比较' in case.dialog.selected.item(1).text()
    assert '用途：小结时再读' in case.dialog.selected.item(3).text()


@pytest.mark.parametrize('kind,index', [('knowledge', 0), ('image', 1)])
def test_cancel_discards_typed_changes_without_reading_source(material_edit, kind, index):
    case = material_edit
    before = _mixed(case)
    reads = (case.library.catalog_calls, deepcopy(case.library.outline_calls),
             deepcopy(case.context_calls), list(case.bridge.pending))
    editor = _edit(case, index)
    editor.title.setText('取消的修改')
    editor.teacher_only.setChecked(True)
    if kind == 'image':
        editor.purpose.setPlainText('取消的用途')
    editor.cancel_button.click()
    assert case.dialog._selections == before and case.dialog._child is None
    assert (case.library.catalog_calls, case.library.outline_calls,
            case.context_calls, case.bridge.pending) == reads


def test_complete_question_edits_only_classroom_title_in_mixed_queue(material_edit):
    case = material_edit
    before = _mixed(case)
    case.dialog.selected.setCurrentRow(2)
    assert case.dialog.edit_button.isEnabled()
    assert '题面与答案保持原设置' in case.dialog.edit_button.toolTip()
    editor = _edit(case, 2)
    assert editor.title.text() == '完整题面及来源答案' and editor.teacher_only is None
    editor.title.setText('三种纤维的结构与性能有什么关系？')
    editor.confirm.click()
    edited = deepcopy(case.dialog._selections[2])
    assert edited['value'] == {**before[2]['value'], 'title': '三种纤维的结构与性能有什么关系？'}
    assert edited['key'] == before[2]['key']
    assert '本次练习 2 分' in case.dialog.selected.item(2).text()
    editor = _edit(case, 1)
    editor.title.setText('通过这张原图能解释什么？')
    editor.confirm.click()
    assert case.dialog._selections[2] == edited
    case.dialog.selected.setCurrentRow(2)
    case.dialog.move_down_button.click()
    assert case.dialog._selections[3] == edited
    case.dialog.confirm.click()
    case.bridge.succeed(case.bridge.take(CONFIRM), run=True)
    assert case.dialog.request['question_selections'] == [edited['value']]
    assert case.dialog.request['selection_order'][-1] == {'kind': 'question', 'index': 0}


@pytest.mark.parametrize('kind', ['knowledge', 'image'])
def test_answer_source_scope_is_locked_in_editor_and_collection(material_edit, kind):
    case = material_edit
    source_id = case.dialog._document['source_id']
    case.library.sources[0]['source_role'] = 'answer'
    case.library.outlines[source_id]['source']['source_role'] = 'answer'
    # Rebind this authored fixture through the same source-read path.
    case.dialog._sources[0]['source_role'] = 'answer'
    case.dialog.filter_sources()
    case.dialog.source.setCurrentIndex(1)
    case.bridge.succeed(case.bridge.take(OUTLINE), run=True)
    value = _knowledge(case.dialog) if kind == 'knowledge' else _image(case.dialog)
    case.dialog._add_selections(kind, [value])
    editor = _edit(case, 0)
    assert editor.teacher_only.isChecked() and not editor.teacher_only.isEnabled()
    # An injected widget change cannot bypass the source-role lock at handoff.
    editor.teacher_only.setChecked(False)
    editor.confirm.click()
    assert case.dialog._selections[0]['value']['teacher_only'] is True
    assert case.dialog._selections[0]['value']['source'] == value['source']


@pytest.mark.parametrize('collision', ['purpose', 'scope'])
def test_edit_key_collision_reports_without_removing_either_selection(material_edit, collision):
    case = material_edit
    before = _mixed(case)
    if collision == 'scope':
        case.dialog._selections[3]['value']['purpose'] = before[1]['value']['purpose']
        case.dialog._selections[3]['value']['teacher_only'] = True
        case.dialog._selections[3]['key'] = ('image', before[1]['value']['source']['asset_id'],
                                            before[1]['value']['purpose'], True)
        before = deepcopy(case.dialog._selections)
    editor = _edit(case, 1)
    if collision == 'purpose':
        editor.purpose.setPlainText(before[3]['value']['purpose'])
    else:
        editor.teacher_only.setChecked(True)
    editor.confirm.click()
    assert case.dialog._selections == before and case.dialog.selected.count() == 4
    assert '相同用途和适用范围' in case.dialog.status.text()
    assert '未保存' in case.dialog.status.text()


def test_editing_at_capacity_never_adds_or_drops_a_material(material_edit):
    case = material_edit
    values = [_image(case.dialog, f'讲解用途{i}') for i in range(20)]
    case.dialog._add_selections('image', values)
    before = deepcopy(case.dialog._selections)
    editor = _edit(case, 10)
    editor.purpose.setPlainText('在原位置解释第二个条件')
    editor.confirm.click()
    assert len(case.dialog._selections) == 20
    assert case.dialog._selections[:10] == before[:10] and case.dialog._selections[11:] == before[11:]
    stable = deepcopy(case.dialog._selections)
    with pytest.raises(ValueError, match='最多20'):
        case.dialog._add_selections('image', [_image(case.dialog, '超出容量的新用途')])
    assert case.dialog._selections == stable


def test_blank_title_or_overlong_purpose_keeps_editor_and_queue_unchanged(material_edit):
    case = material_edit
    before = _mixed(case)
    editor = _edit(case, 1)
    editor.title.setText('   ')
    editor.confirm.click()
    assert case.dialog._child is editor and editor.metadata is None
    assert '引导问题' in editor.status.text() and case.dialog._selections == before
    editor.title.setText('怎样观察这幅原图？')
    editor.purpose.setPlainText('字' * 501)
    editor.confirm.click()
    assert case.dialog._child is editor and editor.metadata is None
    assert '500字' in editor.status.text() and case.dialog._selections == before


def test_queue_mutations_are_disabled_while_editing_and_programmatic_changes_are_rejected(material_edit):
    case = material_edit
    before = _mixed(case)
    editor = _edit(case, 1)
    assert not any(button.isEnabled() for button in (case.dialog.edit_button, case.dialog.move_up_button,
        case.dialog.move_down_button, case.dialog.remove_button, case.dialog.clear_button, case.dialog.confirm))
    case.dialog.move_selection(1)
    case.dialog.remove_selection()
    case.dialog.clear_selections()
    assert case.dialog._selections == before
    case.dialog._add_selections('knowledge', [{**before[0]['value'], 'title': '另一路修订的标题'}])
    external = deepcopy(case.dialog._selections)
    editor.title.setText('迟到的原图标题')
    editor.confirm.click()
    assert case.dialog._selections == external and '已变化' in case.dialog.status.text()


@pytest.mark.parametrize('change', ['source', 'outline', 'stop', 'close'])
def test_late_edit_cannot_write_after_source_or_lifetime_changed(material_edit, change):
    case = material_edit
    before = _mixed(case)
    editor = _edit(case, 1)
    editor.title.setText('不能迟到写入的标题')
    if change == 'source':
        case.dialog.source.setCurrentIndex(2)
        case.bridge.succeed(case.bridge.take(OUTLINE), run=True)
        expected = []
    elif change == 'outline':
        case.dialog._outline['outline_revision'] = 'f' * 64
        expected = before
    elif change == 'stop':
        case.dialog.stop_button.click()
        expected = before
    else:
        case.dialog.reject()
        expected = before
    editor.metadata = {'title': '不能迟到写入的标题', 'purpose': '迟到用途', 'teacher_only': False}
    editor.finished.emit(QDialog.DialogCode.Accepted)
    assert case.dialog._selections == expected and case.dialog.request is None


def test_source_failure_blocks_edit_until_reread_and_confirmation_is_read_once(material_edit):
    case = material_edit
    before = _mixed(case)
    case.dialog.selected.setCurrentRow(1)
    case.dialog.confirm.click()
    assert not case.dialog.edit_button.isEnabled()
    case.dialog.edit_selection()
    assert case.dialog._child is None
    changed = {**case.dialog._document, 'source_revision': 'f' * 64}
    case.bridge.succeed(case.bridge.take(CONFIRM), {'document_source': changed})
    assert case.dialog._selections == before and case.dialog._source_stale
    case.dialog.edit_selection()
    assert case.dialog._child is None and not case.dialog.edit_button.isEnabled()
    case.dialog.reread_button.click()
    case.bridge.succeed(case.bridge.take(OUTLINE), run=True)
    assert not case.dialog._source_stale and not case.dialog._selections
    _mixed(case)
    editor = _edit(case, 1)
    editor.title.setText('这两个条件如何影响结论？')
    editor.purpose.setPlainText('让学生画出知识分支')
    editor.confirm.click()
    edited = deepcopy(case.dialog._selections)
    assert not case.context_calls and not case.bridge.pending
    case.dialog.confirm.click()
    case.bridge.succeed(case.bridge.take(CONFIRM), run=True)
    assert len(case.context_calls) == 1
    assert case.dialog.request['selection_order'] == [{'kind': 'knowledge', 'index': 0},
        {'kind': 'image', 'index': 0}, {'kind': 'question', 'index': 0}, {'kind': 'image', 'index': 1}]
    assert case.dialog.request['image_selections'] == [edited[1]['value'], edited[3]['value']]
    assert case.dialog.request['knowledge_requests'] == [edited[0]['value']]
    assert case.dialog.request['question_selections'] == [edited[2]['value']]


def test_unknown_read_failure_preserves_retry_and_edit_without_clearing_queue(material_edit):
    case = material_edit
    before = _mixed(case)
    case.dialog.selected.setCurrentRow(1)
    case.dialog.confirm.click()
    case.bridge.fail(case.bridge.take(CONFIRM), '本次读取暂未完成，请重试。')
    assert case.dialog._selections == before and not case.dialog._source_stale
    assert case.dialog.confirm.isEnabled() and case.dialog.edit_button.isEnabled()
    editor = _edit(case, 1)
    editor.purpose.setPlainText('读取恢复后保留的新用途')
    editor.confirm.click()
    edited = deepcopy(case.dialog._selections)
    case.dialog.confirm.click()
    case.bridge.succeed(case.bridge.take(CONFIRM), run=True)
    assert case.dialog.request['image_selections'][0] == edited[1]['value']


@pytest.mark.parametrize('kind,index', [('knowledge', 0), ('image', 1)])
@pytest.mark.parametrize('width', [420, 1120])
def test_editor_and_selected_action_are_reachable_at_both_widths(material_edit, kind, index, width):
    case = material_edit
    _mixed(case)
    case.dialog.resize(width, 710)
    settle(case.app)
    case.dialog.content_scroll.ensureWidgetVisible(case.dialog.edit_button)
    settle(case.app)
    assert case.dialog.width() == width
    assert case.dialog.content_scroll.horizontalScrollBar().maximum() == 0
    point = case.dialog.edit_button.mapTo(case.dialog.content_scroll.viewport(), QPoint(0, 0))
    assert point.x() >= 0 and point.x() + case.dialog.edit_button.width() <= case.dialog.content_scroll.viewport().width()
    editor = _edit(case, index)
    editor.resize(width, 440)
    settle(case.app)
    assert editor.width() == width and editor.content_scroll.horizontalScrollBar().maximum() == 0
    for button in (editor.confirm, editor.cancel_button):
        point = button.mapTo(editor, QPoint(0, 0))
        assert 0 <= point.x() <= editor.width() - button.width()
        assert 0 <= point.y() <= editor.height() - button.height()
    fields = ('title', 'teacher_only') + (('purpose',) if kind == 'image' else ())
    for name in fields:
        field = getattr(editor, name)
        editor.content_scroll.ensureWidgetVisible(field)
        settle(case.app)
        point = field.mapTo(editor.content_scroll.viewport(), QPoint(0, 0))
        assert point.x() >= 0 and point.x() + field.width() <= editor.content_scroll.viewport().width()

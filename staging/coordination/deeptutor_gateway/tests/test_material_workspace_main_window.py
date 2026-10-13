"""The real default library and saved-knowledge pages reach the same lesson."""
from copy import deepcopy

import pytest
from test_question_explorer_ui import window as window


def test_preparation_toolbar_opens_actual_default_source_workspace(window, monkeypatch):
    win, _ = window
    page = win.preparation_page
    page.topic.setText('已有课程')
    page.materials.setPlainText('保留的教材说明')
    before = deepcopy(page._payload())
    opened = []
    monkeypatch.setattr(win.library_page, 'open_handout_materials',
                        lambda: opened.append(win.stack.currentWidget()))
    page.handout_requested.emit()
    assert opened == [win.library_page]
    assert win.library_page.__class__.__name__ == 'QuestionExplorerPage'
    assert page._payload() == before
    win.navigate('textbooks')
    page._lesson_busy = True
    try:
        page.handout_requested.emit()
        assert len(opened) == 1
        assert win.stack.currentWidget() is win.textbook_page
        assert page._payload() == before
    finally:
        page._lesson_busy = False


@pytest.mark.parametrize('accepted', [False, True])
def test_default_library_mixed_selection_routes_only_when_lesson_accepts(window, monkeypatch, accepted):
    win, _ = window
    page = win.preparation_page
    before = deepcopy(page._payload())
    request = {'schema': 'shchem.handout-material-request.v1', 'ui_routing_probe': True}
    seen = []
    def receive(value):
        seen.append(deepcopy(value))
        return accepted
    monkeypatch.setattr(page, 'import_handout_materials', receive)
    win.navigate('library')
    win.library_page.handout_materials_requested.emit(request)
    assert seen == [request]
    assert win.stack.currentWidget() is (page if accepted else win.library_page)
    assert page._payload() == before
    assert request == {'schema': 'shchem.handout-material-request.v1', 'ui_routing_probe': True}


@pytest.mark.parametrize('accepted', [False, True])
def test_saved_knowledge_relation_routes_to_checked_receiver(window, monkeypatch, accepted):
    win, _ = window
    page = win.preparation_page
    before = deepcopy(page._payload())
    request = {'link_id': 'routing-probe', 'knowledge_endpoint': {'id': 'routing-probe'}}
    seen = []
    def receive(value):
        seen.append(deepcopy(value))
        return accepted
    monkeypatch.setattr(page, 'import_linked_question', receive)
    win.navigate('textbooks')
    win.textbook_page.knowledge_question_requested.emit(request)
    assert seen == [request]
    assert win.stack.currentWidget() is (page if accepted else win.textbook_page)
    assert page._payload() == before

from copy import deepcopy
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QRect
from PySide6.QtWidgets import QMessageBox, QInputDialog
from test_question_explorer_ui import window
from test_desktop_studio_ui import settle


def test_sidebar_search_does_not_query_or_change_selected_filters(window, monkeypatch):
    win, app = window; p = win.library_page; tools = p.workspace_tools
    before = deepcopy(p.filters); calls = []
    monkeypatch.setattr(p, 'search', lambda *a, **k: calls.append(True))
    tools.tag_search.setText('不存在的目录'); tools.filter_tree()
    assert calls == [] and p.filters == before
    assert any(p.tree.topLevelItem(i).isHidden() for i in range(p.tree.topLevelItemCount()))
    tools.tag_search.clear(); tools.filter_tree()
    assert all(not p.tree.topLevelItem(i).isHidden() for i in range(p.tree.topLevelItemCount()))


def test_selected_tag_kept_visible_during_catalogue_search(window):
    win, app = window; p = win.library_page
    group = next(p.tree.topLevelItem(i) for i in range(p.tree.topLevelItemCount()) if p.tree.topLevelItem(i).childCount())
    item = group.child(0); item.setCheckState(0, Qt.CheckState.Checked)
    p._search_timer.stop()
    p.workspace_tools.tag_search.setText('not-a-matching-label'); p.workspace_tools.filter_tree()
    assert not group.isHidden() and not item.isHidden()


def test_restore_view_performs_one_search_and_preserves_basket(window, monkeypatch):
    win, app = window; p = win.library_page; t = p.workspace_tools
    p.add(p.cards[0]); settle(app, lambda: len(win.facade.basket()) == 1 and not p._adding)
    basket = deepcopy(win.facade.basket())
    value = {'lane': 'word_native', 'query': '', 'filters': {'knowledge': ['K09'], 'exam': ['second_mock']}, 'curriculum': {}, 'curriculum_label': ''}
    identity = t.store.save('化学平衡二模', value); t.reload_views(identity)
    original = p.search; calls = []
    def search(*a, **k): calls.append(True); return original(*a, **k)
    monkeypatch.setattr(p, 'search', search)
    t.restore_selected(t.views.currentIndex())
    settle(app, lambda: not p._loading and len(p.cards) == 2 and p.cards[0].ready)
    assert calls == [True] and p.filters['knowledge'] == {'K09'}
    assert win.facade.basket() == basket


def test_save_via_dialog_and_reopen_view_store(window, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_search_views import SearchViewStore
    win, app = window; p = win.library_page; t = p.workspace_tools
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('常用方案', True))
    t.save_current()
    assert t.views.currentText() == '常用方案'
    assert SearchViewStore(win.facade.state_store).list()[0]['conditions']['lane'] == 'word_native'


def test_cancelled_delete_preserves_view_and_questions(window, monkeypatch):
    win, app = window; t = win.library_page.workspace_tools
    identity = t.store.save('方案', t.conditions()); t.reload_views(identity)
    before = deepcopy(win.facade.state_store.snapshot())
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
    t.delete_selected()
    assert win.facade.state_store.snapshot() == before


def test_basket_rail_follows_existing_basket_not_duplicate_state(window):
    win, app = window; p = win.library_page; t = p.workspace_tools
    p.add(p.cards[0]); settle(app, lambda: not p._adding and t.basket_list.count() == 1)
    assert t.preview.isEnabled()
    win.facade.remove_basket_item(win.facade.basket()[0]['key']); p.refresh_basket()
    assert t.basket_list.count() == 0 and not t.preview.isEnabled()


def test_basket_rail_preview_uses_original_paper_page(window, monkeypatch):
    win, app = window; p = win.library_page
    p.add(p.cards[0]); settle(app, lambda: len(win.facade.basket()) == 1 and not p._adding)
    calls = []; monkeypatch.setattr(win.paper_page, 'request_layout_preview', lambda: calls.append(True))
    p.workspace_tools.preview.click()
    assert calls == [True] and win.stack.currentWidget() is win.paper_page


def test_narrow_layout_keeps_original_basket_and_reading_actions(window):
    win, app = window; p = win.library_page
    win.resize(800, 700); settle(app)
    assert not p.workspace_tools.rail.isVisible()
    assert p.basket_button.isVisible() and p.preview_button.isVisible()
    assert p.workspace_tools.views.isVisible() and p.workspace_tools.save_button.isVisible()
    for widget in (p.basket_button, p.preview_button, p.workspace_tools.save_button):
        assert p.rect().contains(QRect(widget.mapTo(p, widget.rect().topLeft()), widget.size()))


def test_ctrl_find_selects_keyword_without_running_query(window, monkeypatch):
    win, app = window; p = win.library_page; calls = []
    p.query.setText('平衡'); monkeypatch.setattr(p, 'search', lambda: calls.append(True))
    p.workspace_tools.focus_search(); settle(app)
    assert p.query.selectedText() == '平衡' and not calls

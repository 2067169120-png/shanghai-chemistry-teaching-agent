"""Native selection page interactions with isolated synthetic Word data."""
from pathlib import Path
import sys
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow
from integrations.deeptutor_shchem_v1.desktop_workbench.explorer_basket import ExplorerBasketDialog
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'runtime/deeptutor_shchem'))
from explorer_demo_data import seed_demo

@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    facade=seed_demo(tmp_path/'workspace',tmp_path/'state')
    win=TeacherWorkbenchWindow(facade);win.resize(1360,940);win.show();win.navigate("library")
    page=win.library_page
    page.scope.setCurrentIndex(page.scope.findData("word_native"))
    settle(app,lambda:not page._loading and bool(page.cards) and page.cards[0].ready)
    yield win,app
    win.close();win.deleteLater();app.processEvents()


def test_left_facets_right_cards_and_readable_question(window):
    win,app=window;p=win.library_page
    assert p.splitter.orientation()==Qt.Orientation.Horizontal
    assert p.sidebar.isVisible() and len(p.cards)==6
    assert p._reader is not None and p.cards[0].add.isEnabled()
    assert "平衡" in p.cards[0].entry['excerpt']


def test_filter_and_clear_preserve_cart(window):
    win,app=window;p=win.library_page
    p.cards[0].add.click();settle(app,lambda:len(win.facade.basket())==1 and not p._adding and p.preview_button.isEnabled())
    before=win.facade.basket()
    p.filters={'knowledge':{'K09'},'exam':{'second_mock'}};p.search()
    settle(app,lambda:not p._loading and len(p.cards)==2 and p.cards[0].ready)
    assert '2 条原题' in p.result_summary.text()
    assert p.chips.count()==2
    p.clear_filters();settle(app,lambda:not p._loading and len(p.cards)==6)
    assert win.facade.basket()==before


def test_cart_can_reorder_and_remove_only_selected_item(window):
    win,app=window;p=win.library_page
    for i in (0,1):
        if p._active_card is not p.cards[i]:p.expand(p.cards[i])
        settle(app,lambda:p.cards[i].ready)
        p.add(p.cards[i]);settle(app,lambda:len(win.facade.basket())==i+1 and not p._adding)
    original=[r['key'] for r in win.facade.basket()]
    dialog=ExplorerBasketDialog(win.facade,win);dialog.show()
    dialog.down.click()
    assert [r['key'] for r in win.facade.basket()]==original[::-1]
    dialog.remove.click();assert len(win.facade.basket())==1
    assert win.facade.basket()[0]['key']==original[1]
    dialog.close();dialog.deleteLater()


def test_preview_button_uses_real_composer_entry(window,monkeypatch):
    win,app=window;p=win.library_page
    p.add(p.cards[0]);settle(app,lambda:len(win.facade.basket())==1 and not p._adding and p.preview_button.isEnabled())
    calls=[]
    monkeypatch.setattr(win.paper_page,'request_layout_preview',lambda:calls.append('preview'))
    p.preview_button.click()
    assert calls==['preview'] and win.stack.currentWidget() is win.paper_page


def test_actual_basket_preview_request_contains_current_order(window,monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPanel
    win,app=window;p=win.library_page
    choices=[{"key":c.entry['payload']['key'],"revision":c.entry['payload']['revision']} for c in p.cards[:2]]
    win.facade.add_word_questions_to_basket(choices)
    win.facade.move_basket_item(win.facade.basket()[1]['key'],-1)
    calls=[]
    monkeypatch.setattr(MixedPaperPanel,'_preview_request',lambda panel:calls.append(panel.request()))
    win.preview_selected_paper();settle(app,lambda:bool(calls))
    assert calls[-1]['section_order']==[r['key'] for r in win.facade.basket()]


def test_narrow_page_keeps_filters_accessible(window):
    win,app=window;p=win.library_page
    win.resize(800,700);settle(app)
    assert p.filter_toggle.isVisible()
    p.filter_toggle.click();assert p.sidebar.isVisible()


def test_short_questions_leave_room_for_following_results(window):
    win, app = window
    page = win.library_page
    settle(app)
    assert page.page_heading.text() == "选题中心"
    assert page.page_heading.width() >= 120
    assert page.page_heading.height() <= 44
    assert page._reader.height() <= 240
    assert page.cards[0].height() < 520
    assert page.cards[1].geometry().top() < page.scroll.viewport().height()


def test_collapsed_facet_is_not_reopened_by_search(window):
    win, app = window
    page = win.library_page
    knowledge = next(page.tree.topLevelItem(i) for i in range(page.tree.topLevelItemCount())
                     if page.tree.topLevelItem(i).text(0) == "知识点")
    knowledge.setExpanded(False)
    page.search()
    settle(app, lambda: not page._loading and bool(page.cards) and page.cards[0].ready)
    knowledge = next(page.tree.topLevelItem(i) for i in range(page.tree.topLevelItemCount())
                     if page.tree.topLevelItem(i).text(0) == "知识点")
    assert not knowledge.isExpanded()


def test_curriculum_path_stays_open_when_facets_refresh(window):
    from PySide6.QtCore import QSignalBlocker
    win, _ = window
    page = win.library_page
    # Exercise menu rebuilding only; no synthetic catalogue is passed off as
    # an installed or official textbook, and no core search is invoked here.
    with QSignalBlocker(page.scope):
        page.scope.setCurrentIndex(page.scope.findData("master"))
    page._curriculum_catalog = {"volumes": [{"volume_id": "demo-volume", "volume_title": "合成教材册",
        "chapters": [{"chapter_id": "demo-chapter", "chapter_title": "合成章",
            "sections": [{"section_key": "demo-section", "section_title": "合成节"}]}]}]}
    page._render_tree()
    volume = page.tree.topLevelItem(0).child(0)
    chapter = volume.child(0)
    volume.setExpanded(True)
    chapter.setExpanded(True)
    page._render_tree()
    volume = page.tree.topLevelItem(0).child(0)
    assert volume.isExpanded() and volume.child(0).isExpanded()
    # A deliberately closed subtree must also remain closed.
    volume.child(0).setExpanded(False)
    page._render_tree()
    assert not page.tree.topLevelItem(0).child(0).child(0).isExpanded()


def test_real_page_changes_keep_selected_questions_and_status(window, monkeypatch):
    win, app = window
    page = win.library_page
    monkeypatch.setattr(page, "PAGE_SIZE", 2)
    page.search()
    settle(app, lambda: not page._loading and len(page.cards) == 2 and page.cards[0].ready)
    first_key = page.cards[0].entry["key"]
    page.cards[0].add.click()
    settle(app, lambda: len(win.facade.basket()) == 1
           and page.cards[0].add.text() == "已在题篮" and not page.cards[0].add.isEnabled())
    assert page.cards[0].add.text() == "已在题篮"
    page.next.click()
    settle(app, lambda: not page._loading and page._page == 1 and page.cards[0].ready)
    assert page.cards[0].entry["key"] != first_key
    page.cards[0].add.click()
    settle(app, lambda: len(win.facade.basket()) == 2
           and page.cards[0].add.text() == "已在题篮" and not page.cards[0].add.isEnabled())
    page.previous.click()
    settle(app, lambda: not page._loading and page._page == 0 and page.cards[0].ready)
    assert page.cards[0].entry["key"] == first_key
    assert page.cards[0].add.text() == "已在题篮" and not page.cards[0].add.isEnabled()
    page.cards[0].add.click()
    settle(app)
    assert len(win.facade.basket()) == 2

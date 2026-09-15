"""Teacher-visible lifecycle/cache contracts using real Qt, no model requests."""
import json
import threading
from pathlib import Path
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QDialog, QPushButton
from test_question_explorer_ui import window
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_workbench.question_explorer_page import QuestionExplorerPage
from integrations.deeptutor_shchem_v1.desktop_workbench.explorer_reader import PersonalQuestionReader
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_question_explorer import personal_results


def test_hidden_page_does_not_read_questions(window, monkeypatch):
    win,app=window; calls=[]
    monkeypatch.setattr(win.facade,'word_question_catalog',lambda:calls.append('read'))
    tasks=DesktopTaskBridge();page=QuestionExplorerPage(win.facade,tasks)
    try:
        settle(app)
        assert not calls and not page._started
        assert page.scope.currentData()=='word_native'  # earlier page's chosen lane
    finally:
        page.close();page.deleteLater();tasks.shutdown()


def test_lane_preference_survives_page_recreation(window):
    win,app=window;page=win.library_page
    win.navigate('home');page.scope.setCurrentIndex(page.scope.findData('visual_native'))
    copy=QuestionExplorerPage(win.facade,win.tasks)
    assert copy.scope.currentData()=='visual_native'
    copy.close();copy.deleteLater()


def test_page_changes_reuse_index_and_tree_items(window,monkeypatch):
    win,app=window;p=win.library_page
    monkeypatch.setattr(p,'PAGE_SIZE',2);p.search()
    settle(app,lambda:not p._loading and len(p.cards)==2 and p.cards[0].ready)
    original_item=p.tree.topLevelItem(0)
    old_index=p._sessions['word_native'].index
    p.next.click()
    settle(app,lambda:not p._loading and p._page==1 and p.cards[0].ready)
    assert p._sessions['word_native'].index is old_index
    assert p.load_metrics['query_cache_hit']
    assert p.tree.topLevelItem(0) is original_item


def test_import_invalidation_drops_old_snapshots_and_sees_new_rows(window,monkeypatch):
    from copy import deepcopy
    win,app=window;p=win.library_page
    catalog=deepcopy(p._catalogs['word_native']);catalog['items']=catalog['items'][:1]
    monkeypatch.setattr(win.facade,'word_question_catalog',lambda:catalog)
    p.invalidate_catalogs()
    settle(app,lambda:not p._loading and len(p.cards)==1 and p.cards[0].ready)
    assert p._sessions['word_native'].index.catalog is catalog


def test_main_import_exit_invalidates_even_after_cancel(window,monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog
    win,app=window;calls=[]
    monkeypatch.setattr(ImportDialog,'exec',lambda _:QDialog.DialogCode.Rejected)
    monkeypatch.setattr(win.library_page,'invalidate_catalogs',lambda:calls.append(True))
    win.open_import();settle(app)
    assert calls==[True]


def test_copy_timings_contains_no_query_or_source_text(window):
    win,app=window;p=win.library_page
    p.query.setText('a-secret-search-string');p.search()
    settle(app,lambda:not p._loading)
    p.copy_load_metrics();text=QApplication.clipboard().text();value=json.loads(text)
    assert value['schema']=='question-page-timing-v1'
    assert 'a-secret-search-string' not in text and 'source_name' not in text
    assert 'request_to_cards_ms' in value


def test_empty_results_offer_personal_bank_without_claiming_it_is_loaded(window):
    win,app=window;p=win.library_page
    p.query.setText('does-not-exist-195');p.search();settle(app,lambda:not p._loading)
    links=[b for b in p.findChildren(QPushButton) if b.accessibleName()=='切换到已导入图片题']
    assert links
    links[0].click();settle(app,lambda:not p._loading)
    assert p.scope.currentData()=='visual_native'


def test_cold_cancelled_search_is_shared_and_latest_result_wins(window,monkeypatch):
    win,app=window;p=win.library_page
    catalog=p._catalogs['word_native'];calls=[];entered=threading.Event();release=threading.Event()
    def slow():
        calls.append(True);entered.set();assert release.wait(5);return catalog
    monkeypatch.setattr(win.facade,'word_question_catalog',slow)
    try:
        p.query.clear();p.reload();settle(app,entered.is_set)
        p.query.setText('NO_MATCH_AT_ALL_195');p.search();release.set()
        settle(app,lambda:not p._loading)
        assert not p.cards and calls==[True]
    finally:release.set()


def test_multigraph_word_reads_once_per_tab_and_answer_stays_lazy(window,tmp_path,monkeypatch):
    from test_explorer_speed import make_images_fixture
    win,app=window
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    isolated = tmp_path/'batch-image-case'
    (isolated/'workspace/integrations/deeptutor_shchem_v1').mkdir(parents=True)
    (isolated/'workspace/sh-chem-db').mkdir()
    paths = DesktopPaths.from_workspace(isolated/'workspace',state_root=isolated/'state')
    facade,row,ids=make_images_fixture(paths,isolated,count=6,questions=12)
    service=facade._word_questions();original=service._resolve;calls=[]
    def counted(*a,**kw):calls.append(True);return original(*a,**kw)
    monkeypatch.setattr(service,'_resolve',counted)
    entry=next(e for e in personal_results(facade.word_question_catalog(),'word_native',{},'',0,100)['entries'] if e['key']==row['key'])
    reader=PersonalQuestionReader(entry,facade,win.tasks)
    try:
        reader.show();settle(app,lambda:reader.ready_to_select and bool(reader.load_metrics))
        assert len(calls)==1 and len(reader.required)==6
        assert reader.loaded_tabs=={0}
        assert reader.load_metrics[0]['completed']==6
    finally:reader.close();reader.deleteLater();facade.shutdown()


def test_generic_import_files_precede_historical_pack(window):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog
    win,app=window;dialog=ImportDialog(win.facade,win.tasks,win)
    try:
        dialog.show();settle(app)
        assert dialog.question_files.mapTo(dialog,dialog.question_files.rect().topLeft()).y() < dialog.source_help_card.mapTo(dialog,dialog.source_help_card.rect().topLeft()).y()
        assert '旧版' in dialog.corpus_button.text()
    finally:dialog.close();dialog.deleteLater()


def test_late_results_start_with_compact_card_actions(window):
    from PySide6.QtWidgets import QBoxLayout
    win,app=window;page=win.library_page
    win.resize(800,700);settle(app)
    page.scroll.setFixedWidth(300)
    page.search()
    settle(app,lambda:not page._loading and bool(page.cards) and page.cards[0].ready)
    assert all(c.actions.direction()==QBoxLayout.Direction.TopToBottom for c in page.cards)
    assert page.list_body.width() <= page.scroll.viewport().width()

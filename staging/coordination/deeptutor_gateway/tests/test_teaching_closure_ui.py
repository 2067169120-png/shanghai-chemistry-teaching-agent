import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QInputDialog
from datetime import date
from test_exam_dashboard_ui import desk
from test_question_explorer_ui import window
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup,link_basket,record_attempt


def sample_task(d):
    return create_followup(d.exam,'1',['S0001'],'按证据核对并复练',date.today().isoformat())


def test_followup_is_persisted_and_does_not_replace_existing_advice(desk):
    d,app,_=desk;task=sample_task(d)
    assert d.followup_panel.store(task)
    d.open_saved(d.exam['id'],task['id']);settle(app)
    assert d.followup_panel.current()==task and d.tabs.currentWidget() is d.followup_panel
    assert not d.followup_panel.export.isEnabled()


def test_followup_tab_leaves_existing_five_tab_positions_unchanged(desk):
    d,app,_=desk
    assert [d.tabs.tabText(i) for i in range(5)]==['总览','题目与知识点','学生得分与行动','试卷与API建议','数据核对']
    assert d.tabs.tabText(5)=='复练与复测'
    d.resize(800,700);d.tabs.setCurrentIndex(5);settle(app)
    assert d.followup_panel.text.height()>=150
    for w in (d.followup_panel.new,d.followup_panel.find,d.followup_panel.record):
        assert d.rect().contains(QRect(w.mapTo(d,w.rect().topLeft()),w.size()))


def test_new_task_uses_explicit_student_selection_and_confirmed_goal(desk,monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import exam_followup_panel as mod
    d,app,_=desk
    monkeypatch.setattr(mod,'checked_rows',lambda *a:['S0001'])
    answers=iter([('解释题目条件',True),(date.today().isoformat(),True)])
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**k:next(answers))
    d.followup_panel.new.click();settle(app)
    assert len(d.followups)==1 and d.followups[0]['targets'][0]['exam_student_id']=='S0001'
    assert d.store.load(d.exam['id'])['followups']==d.followups


def test_reopen_old_exam_without_followups_and_new_import_clear_stale_preview(desk):
    d,app,_=desk;identity=d.exam['id'];d.store.save({'exam':d.exam})
    d.open_saved(identity);settle(app)
    assert d.followups==[]
    d.followup_panel.preview=object();d.followup_panel.approved=True
    d.accept_exam({**d.exam,'id':'d'*32});settle(app)
    assert d.followup_panel.preview is None and not d.followup_panel.export.isEnabled()


def test_handoff_uses_existing_filtered_catalogue_and_keeps_current_basket(window):
    win,app=window;page=win.library_page
    page.cards[0].add.click();settle(app,lambda:not page._adding and len(win.facade.basket())==1)
    before=win.facade.basket()
    win.open_exam_practice({'exam_id':'a'*32,'task_id':'b'*32,'lane':'word_native','knowledge':'K09','label':'化学平衡'})
    settle(app,lambda:not page._loading and page.cards and page.cards[0].ready)
    assert page.filters=={'knowledge':{'K09'}} and len(page.cards)==4
    assert page.exam_context.isVisible() and page.exam_context_back.text()=='返回复练任务'
    assert win.facade.basket()==before


def test_linking_unrelated_basket_items_is_not_automatic(desk,monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import exam_followup_panel as mod
    d,app,_=desk;d.followup_panel.store(sample_task(d))
    d.facade.basket=lambda:[{'key':'a','title_zh':'不要这一题'},{'key':'b','title_zh':'所选完整主题'}]
    monkeypatch.setattr(mod,'checked_rows',lambda *a:['b'])
    d.followup_panel.link.click();settle(app)
    assert [r['key'] for r in d.followup_panel.current()['links']]==['b']

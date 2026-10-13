"""Saved exam practice enters unified Qt search without changing ownership."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QInputDialog

from test_desktop_studio_ui import settle
from test_exam_dashboard_ui import desk
from test_question_explorer_ui import window
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup
from integrations.deeptutor_shchem_v1.desktop_workbench import exam_followup_panel


def _request():
    return {"exam_id": "a" * 32, "task_id": "b" * 32,
            "sources": ["word_native", "visual_native"],
            "filters": {"knowledge": ["K09"], "knowledge_mode": "any"},
            "label": "化学平衡", "exam_student_ids": ["S0001"],
            "student_profile_ids": ["bound-profile"],
            "student_targets": [{"exam_student_id": "S0001",
                "identity_binding": {"profile_student_id": "bound-profile"}}]}


def _current_owner(win):
    owner = SimpleNamespace(student_id="current-different-profile", label_zh="合成当前档案")
    with QSignalBlocker(win.student_page.student_combo):
        win.student_page.student_combo.addItem(owner.label_zh, owner)
        win.student_page.student_combo.setCurrentIndex(win.student_page.student_combo.count() - 1)
    win.student_page._current_summary = SimpleNamespace(
        student_id=owner.student_id, submission_id="current-submission")
    return owner, win.student_page._current_summary


@pytest.mark.parametrize('cold', [False, True])
def test_real_qt_unified_handoff_preserves_task_and_active_student(window, monkeypatch, cold):
    win, app = window
    page = win.library_page
    owner, summary = _current_owner(win)
    basket = deepcopy(win.facade.basket())
    request = _request()
    searches = []
    search = page.search
    monkeypatch.setattr(page, "search", lambda *a, **k: (searches.append(True), search(*a, **k))[1])
    win.stack.setCurrentWidget(win.paper_page)
    monkeypatch.setattr(win.paper_page, "prepare_leave", lambda: True)
    if cold:page._started = False

    assert win.open_exam_practice(request) is True
    settle(app, lambda: not page._loading and bool(page.cards))

    assert searches == [True]
    assert page.scope.currentData() == "unified"
    assert page.selected_sources() == ("word_native", "visual_native")
    assert page.filters == {"knowledge": {"K09"}}
    assert page.practice_context == request and page.exam_context_request == request
    assert page.exam_context.isVisible()
    assert page._reader is None
    page.cards[0].open.click()
    settle(app, lambda: page.cards[0].ready)
    assert win.student_page._selected_student() is owner
    assert win.student_page._current_summary is summary
    assert win.facade.basket() == basket
    request["student_targets"][0]["identity_binding"]["profile_student_id"] = "mutated-caller"
    assert page.practice_context["student_targets"][0]["identity_binding"]["profile_student_id"] == "bound-profile"
    assert page.exam_context_request["student_profile_ids"] == ["bound-profile"]
    task_context = deepcopy(page.practice_context)
    page.cards[0].add.click()
    settle(app, lambda: not page._adding and len(win.facade.basket()) == len(basket) + 1)
    assert page.practice_context == task_context
    assert page.exam_context_request == task_context
    assert win.student_page._selected_student() is owner
    assert win.student_page._current_summary is summary
    returns = []
    monkeypatch.setattr(win, 'open_exam_dialog', lambda **kwargs: returns.append(kwargs))
    page.exam_context_back.click()
    assert returns == [{'identity': 'a' * 32, 'task_id': 'b' * 32}]


@pytest.mark.parametrize('payload', [None, [], {},
    {**_request(), 'label': ''}, {**_request(), 'exam_id': 7},
    {**_request(), 'sources': ['invented']},
    {**_request(), 'filters': {'knowledge': 'K09'}},
    {**_request(), 'filters': {'knowledge': ['K09'], 'knowledge_mode': 'automatic'}},
    {**_request(), 'filters': {'answer': ['K09']}}])
def test_malformed_handoff_has_no_navigation_or_destination_effects(window, monkeypatch, payload):
    win, app = window
    page = win.library_page
    owner, summary = _current_owner(win)
    before = (page.scope.currentData(), page.selected_sources(), deepcopy(page.filters),
              page.query.text(), deepcopy(page.practice_context), deepcopy(win.facade.basket()))
    navigation = []
    monkeypatch.setattr(win, 'navigate', lambda *a: navigation.append(a))

    assert win.open_exam_practice(payload) is False
    assert navigation == []
    assert (page.scope.currentData(), page.selected_sources(), page.filters,
            page.query.text(), page.practice_context, win.facade.basket()) == before
    assert not hasattr(page, 'exam_context')
    assert win.student_page._selected_student() is owner
    assert win.student_page._current_summary is summary


def test_stale_saved_mapping_is_refused_before_navigation(window, monkeypatch):
    win, app = window
    page = win.library_page
    owner, summary = _current_owner(win)
    payload = _request()
    payload['knowledge_mapping'] = {'version_vector': {'word_native': {'revision': 'older'}}}
    before = (page.scope.currentData(), page.selected_sources(), deepcopy(page.filters),
              page.query.text(), deepcopy(page.practice_context), deepcopy(win.facade.state_store.snapshot()))
    navigation, gates = [], []
    monkeypatch.setattr(win, 'navigate', lambda *a: navigation.append(a))
    monkeypatch.setattr(win.facade, 'unified_question_search_is_current',
                        lambda version: (gates.append(version), False)[1])

    assert win.open_exam_practice(payload) is False
    assert navigation == [] and gates == [payload['knowledge_mapping']['version_vector']]
    assert (page.scope.currentData(), page.selected_sources(), page.filters,
            page.query.text(), page.practice_context, win.facade.state_store.snapshot()) == before
    assert not hasattr(page, 'exam_context')
    assert win.student_page._selected_student() is owner
    assert win.student_page._current_summary is summary
    assert '重新核对知识条件' in win.statusBar().currentMessage()


@pytest.mark.parametrize("answers", [[False], [True, False]])
def test_real_qt_refused_navigation_keeps_previous_search_and_owner(window, monkeypatch, answers):
    win, app = window
    page = win.library_page
    owner, summary = _current_owner(win)
    page.query.setText("保留原查询")
    page.filters = {"knowledge": {"K03"}}
    page.curriculum = {"book": "原教材"}
    before = (page.scope.currentData(), page.selected_sources(), deepcopy(page.filters),
              deepcopy(page.curriculum), page.query.text(), page._loading, page._started,
              deepcopy(page.practice_context), deepcopy(win.facade.basket()))
    searches = []
    monkeypatch.setattr(page, "search", lambda *a, **k: searches.append(True))
    attempts = iter(answers)
    monkeypatch.setattr(win.paper_page, "prepare_leave", lambda: next(attempts, True))
    win.stack.setCurrentWidget(win.paper_page)

    assert win.open_exam_practice(_request()) is False
    assert win.stack.currentWidget() is win.paper_page
    assert (page.scope.currentData(), page.selected_sources(), page.filters,
            page.curriculum, page.query.text(), page._loading, page._started,
            page.practice_context, win.facade.basket()) == before
    assert not hasattr(page, "exam_context")
    assert not hasattr(page, "_handoff_navigation_pending")
    assert searches == []
    assert win.student_page._selected_student() is owner
    assert win.student_page._current_summary is summary


def _task(d):
    task = create_followup(d.exam, "1", ["S0001"], "按考试证据核对后复练", date.today().isoformat())
    task["targets"][0]["identity_binding"] = {"profile_student_id": "original-profile"}
    task["targets"][0]["reviewed_identity_binding"] = {"profile_student_id": "reviewed-profile"}
    assert d.followup_panel.store(task)
    return deepcopy(d.followup_panel.current())


def _catalog():
    return {"facets": {"knowledge": {"label_zh": "知识点", "values": [
        {"value": "unknown", "label_zh": "待核验", "sources": ["word_native"]},
        {"value": "K09", "label_zh": "化学平衡", "sources": ["master"]},
        {"value": "teacher-confirmed-local", "label_zh": "化学平衡",
         "sources": ["word_native"]}]}}}


def _select_sources(monkeypatch, d, catalog=None):
    calls = []
    def listing(**kwargs):
        calls.append(kwargs)
        return deepcopy(_catalog() if catalog is None else catalog)
    monkeypatch.setattr(d.facade, "search_unified_questions", listing, raising=False)
    monkeypatch.setattr(exam_followup_panel, "checked_rows", lambda *a, **k: ["master", "word_native"])
    return calls


def test_find_questions_saves_explicit_mapping_then_hands_off(desk, monkeypatch):
    d, app, _ = desk
    task = _task(d)
    catalogue = _catalog()
    catalogue['version_vector'] = {'master': {'revision': 'confirmed-current'}}
    calls = _select_sources(monkeypatch, d, catalogue)
    gates = []
    monkeypatch.setattr(d.facade, 'unified_question_search_is_current',
                        lambda version: (gates.append(version), True)[1], raising=False)
    seen = []
    def knowledge(*args, **kwargs):
        labels = args[3]
        seen.extend(labels)
        return next(label for label in labels if "teacher-confirmed-local" in label), True
    monkeypatch.setattr(QInputDialog, "getItem", knowledge)
    closed = []
    monkeypatch.setattr(d, "close", lambda: closed.append(True))

    d.followup_panel.find.click()
    settle(app, lambda: d._task is None and bool(closed))

    assert calls and calls[0]["lanes"] == ["master", "word_native"]
    assert calls[0]["limit"] == 1 and callable(calls[0]["cancelled"])
    assert all("unknown" not in label for label in seen)
    current = d.followup_panel.current()
    assert current["targets"] == task["targets"]
    assert current["library_filter"]["sources"] == ["master", "word_native"]
    assert current["library_filter"]["filters"] == {
        "knowledge": ["teacher-confirmed-local"], "knowledge_mode": "any"}
    assert gates == [catalogue['version_vector']]
    assert current['library_filter']['knowledge_mapping']['version_vector'] == catalogue['version_vector']
    assert d.store.load(d.exam["id"])["followups"][0] == current
    assert d.pending_handoff["exam_id"] == task["exam_id"]
    assert d.pending_handoff["task_id"] == task["id"]
    assert d.pending_handoff["student_profile_ids"] == ["reviewed-profile"]
    assert d.pending_handoff["exam_student_ids"] == ["S0001"]
    assert d.pending_handoff["student_targets"] == task["targets"]
    assert closed == [True]


@pytest.mark.parametrize("failure", ["false", "exception"])
def test_find_questions_failed_save_cannot_emit_handoff_or_close(desk, monkeypatch, failure):
    d, app, _ = desk
    _task(d)
    original = deepcopy(d.followups)
    saved = d.store.load(d.exam["id"])
    _select_sources(monkeypatch, d)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (a[3][0], True))
    def save():
        if failure == "exception":raise OSError("合成保存失败")
        return False
    monkeypatch.setattr(d, "save_current", save)
    closed = []
    monkeypatch.setattr(d, "close", lambda: closed.append(True))

    d.followup_panel.find.click()
    settle(app, lambda: d._task is None)

    assert d.followups == original and not d.dirty
    assert d.store.load(d.exam["id"]) == saved
    assert d.pending_handoff is None and closed == []


def test_find_questions_empty_effective_knowledge_keeps_task(desk, monkeypatch):
    d, app, _ = desk
    _task(d)
    original = deepcopy(d.followups)
    _select_sources(monkeypatch, d, {"facets": {"knowledge": {"values": [
        {"value": "unknown", "label_zh": "待核验"}]}}})
    prompts = []
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: prompts.append(a))

    d.followup_panel.find.click()
    settle(app, lambda: d._task is None)

    assert d.followups == original and d.pending_handoff is None
    assert not prompts and "没有有效知识条件" in d.status.text()


def test_find_questions_rechecks_task_after_teacher_mapping_dialog(desk, monkeypatch):
    d, app, _ = desk
    _task(d)
    saved = d.store.load(d.exam["id"])
    _select_sources(monkeypatch, d)
    def changed_during_choice(*args, **kwargs):
        d.followup_panel.current()["goal"] = "选择期间另行修改的目标"
        d.dirty = True
        return args[3][0], True
    monkeypatch.setattr(QInputDialog, "getItem", changed_during_choice)

    d.followup_panel.find.click()
    settle(app, lambda: d._task is None)

    assert d.followup_panel.current()["goal"] == "选择期间另行修改的目标"
    assert d.store.load(d.exam["id"]) == saved
    assert d.pending_handoff is None and "已变化" in d.status.text()


def test_find_questions_rejects_changed_student_binding_during_mapping(desk, monkeypatch):
    d, app, _ = desk
    _task(d)
    saved = d.store.load(d.exam['id'])
    _select_sources(monkeypatch, d)
    def changed_binding(*args, **kwargs):
        d.followup_panel.current()['targets'][0]['reviewed_identity_binding'] = {
            'profile_student_id': 'later-profile'}
        d.dirty = True
        return args[3][0], True
    monkeypatch.setattr(QInputDialog, 'getItem', changed_binding)

    d.followup_panel.find.click()
    settle(app, lambda: d._task is None)

    assert d.pending_handoff is None and '学生归属已变化' in d.status.text()
    assert d.store.load(d.exam['id']) == saved
    assert d.followup_panel.current()['targets'][0]['reviewed_identity_binding']['profile_student_id'] == 'later-profile'


def test_find_questions_rejects_stale_catalogue_after_mapping(desk, monkeypatch):
    d, app, _ = desk
    _task(d)
    saved = d.store.load(d.exam['id'])
    original = deepcopy(d.followups)
    catalogue = _catalog()
    catalogue['version_vector'] = {'master': {'revision': 'read-before-dialog'}}
    _select_sources(monkeypatch, d, catalogue)
    gates = []
    def stale(version):
        gates.append(version)
        return False
    monkeypatch.setattr(d.facade, 'unified_question_search_is_current', stale, raising=False)
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: (a[3][0], True))

    d.followup_panel.find.click()
    settle(app, lambda: d._task is None)

    assert gates == [catalogue['version_vector']]
    assert d.pending_handoff is None and '题库目录已变化' in d.status.text()
    assert d.followups == original and d.store.load(d.exam['id']) == saved

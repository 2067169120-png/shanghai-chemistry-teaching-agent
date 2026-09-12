from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLabel

from integrations.deeptutor_shchem_v1.desktop_library import (
    LibraryImage,
    LibraryPartDetail,
    LibraryThemeDetail,
)
from integrations.deeptutor_shchem_v1.desktop_workbench import student_page as module


class Tasks:
    """Only synthetic in-memory operations; tests choose callback ordering."""

    def __init__(self, immediate=False):
        self.immediate = immediate
        self.pending = []
        self.calls = []
        self.cancelled = []

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        key = f"task-{len(self.calls)}"
        self.calls.append(label)
        task = SimpleNamespace(key=key, label=label, operation=operation, success=on_success, failure=on_failure)
        if self.immediate:
            self.resolve(task)
        else:
            self.pending.append(task)
        return key

    def take(self, label):
        task = next(task for task in self.pending if task.label == label)
        self.pending.remove(task)
        return task

    def finish(self, label):
        self.resolve(self.take(label))

    @staticmethod
    def resolve(task):
        try:
            result = task.operation()
        except (RuntimeError, ValueError, TypeError) as exc:
            if task.failure:
                task.failure(str(getattr(exc, "message_zh", "合成读取失败")))
        else:
            if task.success:
                task.success(result)

    def cancel(self, key):
        self.cancelled.append(key)


def preview(student="opaque-student-a", submission="opaque-submission-a"):
    return {
        "student_id": student, "submission_id": submission, "revision": "opaque-revision", "preview_hash": "opaque-hash",
        "message_zh": "按本次已确认章节找到 1 道完整大题。",
        "notices_zh": ("本次推荐不写入长期学生标签。",),
        "diagnoses": ({"title_zh": "必修第一册 · 氧化还原反应", "status_zh": "教师已确认", "evidence_zh": "本次作答在电子转移表示中失分。"},),
        "recommendations": ({
            "key": "opaque-candidate", "title_zh": "氧化还原反应与实验应用", "source_zh": "合成教学样例 · 高一",
            "reason_zh": "教师确认的氧化还原反应章节与本题已核对标签相符。",
            "matched_question_count": 1, "atomic_total": 2, "shared_material_count": 1,
            "readiness_zh": "完整题面可预览；教师决定是否使用。",
        },),
    }


def detail(*, image=False):
    picture = LibraryImage("master", "opaque-node", "opaque-crop", "0" * 64, "stimulus", "合成共同材料题图")
    return LibraryThemeDetail(
        key="opaque-theme", scope="master", title_zh="氧化还原反应与实验应用", paper_title_zh="合成教学样例",
        source_zh="合成资料，不是真实试题", page_zh="第 1 页", context_zh="共同材料：已知物质的变化过程。",
        shared_images=(picture,) if image else (),
        parts=(
            LibraryPartDetail(key="opaque-atomic-1", label_zh="第 1 小题", summary_zh="判断氧化剂。", requirement_zh="根据共同材料作答", dependency_zh="依赖上述共同材料"),
            LibraryPartDetail(key="opaque-atomic-2", label_zh="第 2 小题", summary_zh="表示电子转移。", requirement_zh="保留反应条件", dependency_zh="依赖上述共同材料"),
        ),
    )


class Facade:
    def __init__(self):
        self.preview = preview()
        self.detail = detail()
        self.preview_calls = []
        self.detail_calls = []
        self.add_calls = []
        self.score_calls = []
        self.diagnosis_calls = []
        self.image_bytes = b"invalid synthetic image"
        self.review = {"student_id": "opaque-student-a", "submission_id": "opaque-submission-a", "revision": "opaque-revision", "review_complete": True, "items": ()}

    def student_submissions(self, **_kwargs):
        return ()

    def student_practice_preview(self, **kwargs):
        self.preview_calls.append(kwargs)
        return self.preview

    def student_practice_theme_detail(self, given, key):
        self.detail_calls.append((given, key))
        return self.detail

    def add_student_practice_to_basket(self, given, key):
        self.add_calls.append((given, key))
        return 3

    def student_analysis_review(self, **_kwargs):
        return self.review

    def record_student_score(self, **kwargs):
        self.score_calls.append(kwargs)
        return self.review

    def record_student_diagnosis(self, **kwargs):
        self.diagnosis_calls.append(kwargs)
        return self.review

    def library_image(self, _image):
        return self.image_bytes


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_page(qt_app, monkeypatch):
    pages = []
    # The test never enumerates real student profiles or provider metadata.
    monkeypatch.setattr(module.StudentPage, "_load_context", lambda self: None)

    def make(*, immediate=False, facade=None):
        facade = facade or Facade()
        tasks = Tasks(immediate)
        page = module.StudentPage(facade, tasks)
        page._context_loaded({"students": (
            {"student_id": "opaque-student-a", "label_zh": "匿名学生甲", "grade": "高一"},
            {"student_id": "opaque-student-b", "label_zh": "匿名学生乙", "grade": "高二"},
        ), "profiles": (), "sections": ()})
        page._current_summary = {"student_id": "opaque-student-a", "submission_id": "opaque-submission-a", "revision": "opaque-revision", "candidate_available": True}
        page._review_loaded(facade.review)
        page.resize(900, 880)
        page.show()
        qt_app.processEvents()
        pages.append(page)
        return page, facade, tasks

    yield make
    for page in pages:
        page.close()
        page.deleteLater()
    qt_app.processEvents()


def recommend(page, tasks):
    page.practice_panel.recommend_button.click()
    if not tasks.immediate:
        tasks.finish("按本次学情推荐练习")
    return page.practice_panel.cards["opaque-candidate"]


def open_detail(page, tasks, card):
    card.view_button.click()
    if not tasks.immediate:
        tasks.finish("查看推荐完整大题")
    dialog = page._practice_dialog
    assert dialog is not None
    dialog._check_preview()
    return dialog


@pytest.mark.parametrize("immediate", [False, True])
def test_explicit_recommend_preview_then_add_keeps_complete_theme(make_page, qt_app, immediate):
    page, facade, tasks = make_page(immediate=immediate)
    assert facade.preview_calls == []
    changed = []
    page.basket_changed.connect(changed.append)
    card = recommend(page, tasks)
    assert not card.add_button.isEnabled()
    page._add_practice_theme("opaque-candidate")
    assert facade.add_calls == []
    dialog = open_detail(page, tasks, card)
    assert dialog.detail is facade.detail
    assert len(dialog.detail.parts) == 2
    assert card.add_button.isEnabled()
    assert facade.add_calls == []
    card.add_button.click()
    if not immediate:
        tasks.finish("加入推荐完整大题")
    assert facade.add_calls == [(facade.preview, "opaque-candidate")]
    assert changed == [3]
    assert "题篮共 3 道大题" in card.status.text()
    assert page._practice_task_id is None
    assert page._practice_detail_task_id is None
    assert page._practice_add_task_id is None
    qt_app.processEvents()


def test_empty_and_waiting_recommendations_explain_next_action(make_page):
    page, facade, tasks = make_page()
    facade.preview = dict(preview(), recommendations=(), message_zh="请先完成本次教师评分和教材章节确认。")
    page._recommend_practice()
    tasks.finish("按本次学情推荐练习")
    assert not page.practice_panel.cards
    assert "先完成" in page.practice_panel.status.text()
    texts = "\n".join(label.text() for label in page.practice_panel.findChildren(QLabel))
    assert "没有可加入" in texts
    assert "长期学生标签" in texts


def test_unavailable_facade_does_not_crash_or_trigger_any_recommendation(make_page):
    facade = Facade()
    facade.student_practice_preview = None
    page, facade, _tasks = make_page(facade=facade)
    assert not page.practice_panel.recommend_button.isEnabled()
    assert "暂未提供" in page.practice_panel.status.text()
    page._recommend_practice()
    assert facade.preview_calls == []


@pytest.mark.parametrize("stage", ["recommend", "detail", "add"])
def test_operation_errors_do_not_unlock_or_silently_mutate(make_page, stage):
    page, facade, tasks = make_page()
    if stage == "recommend":
        page._recommend_practice()
        task = tasks.take("按本次学情推荐练习")
        task.failure("本机题库暂时不可读取")
        assert "未加入任何题目" in page.practice_panel.status.text()
        assert not page.practice_panel.cards
        assert page.practice_panel.recommend_button.isEnabled()
    elif stage == "detail":
        card = recommend(page, tasks)
        card.view_button.click()
        tasks.take("查看推荐完整大题").failure("完整材料读取失败")
        assert not card.add_button.isEnabled()
        assert "未解锁" in card.status.text()
    else:
        card = recommend(page, tasks)
        open_detail(page, tasks, card)
        card.add_button.click()
        tasks.take("加入推荐完整大题").failure("本次评分已经更新")
        assert not page.practice_panel.cards
        assert not page.practice_panel.recommend_button.isEnabled()
        assert "重新读取" in page.practice_panel.status.text()
    assert facade.add_calls == []


def test_detail_construction_failure_never_unlocks_add(make_page, monkeypatch):
    page, facade, tasks = make_page()
    card = recommend(page, tasks)

    def fail(*_args, **_kwargs):
        raise ValueError("not teacher-visible internal sentinel")

    monkeypatch.setattr(module, "StudentPracticeDetailDialog", fail)
    card.view_button.click()
    tasks.finish("查看推荐完整大题")
    assert not card.add_button.isEnabled()
    assert "暂时无法打开" in card.status.text()
    assert "sentinel" not in card.status.text()
    page._add_practice_theme("opaque-candidate")
    assert facade.add_calls == []


@pytest.mark.parametrize("valid", [False, True])
def test_required_shared_image_must_finish_and_decode_before_add(make_page, valid):
    page, facade, tasks = make_page()
    facade.detail = detail(image=True)
    if valid:
        image = QImage(40, 30, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(buffer, "PNG")
        facade.image_bytes = bytes(buffer.data())
    card = recommend(page, tasks)
    dialog = open_detail(page, tasks, card)
    assert not card.add_button.isEnabled()
    tasks.finish("读取合成共同材料题图")
    dialog._check_preview()
    assert card.add_button.isEnabled() is valid
    if not valid:
        assert "尚未成功显示" in card.status.text()


@pytest.mark.parametrize("stage", ["recommend", "detail", "add"])
def test_late_callbacks_cannot_repaint_new_student(make_page, stage):
    page, facade, tasks = make_page()
    changes = []
    page.basket_changed.connect(changes.append)
    if stage == "recommend":
        page._recommend_practice()
        old = tasks.take("按本次学情推荐练习")
        result = facade.preview
    else:
        card = recommend(page, tasks)
        if stage == "detail":
            card.view_button.click()
            old = tasks.take("查看推荐完整大题")
            result = facade.detail
        else:
            open_detail(page, tasks, card)
            card.add_button.click()
            old = tasks.take("加入推荐完整大题")
            result = 9
    page.student_combo.setCurrentIndex(1)
    old.success(result)
    old.failure("obsolete request failed")
    assert page._practice_preview is None
    assert page._practice_dialog is None
    assert not page.practice_panel.cards
    assert not changes


def test_late_recommendation_cannot_clear_replacement_task(make_page):
    page, facade, tasks = make_page()
    page._recommend_practice()
    old = tasks.take("按本次学情推荐练习")
    page._review_loaded(facade.review)
    page._recommend_practice()
    replacement = page._practice_task_id
    old.success(preview())
    assert page._practice_task_id == replacement
    assert not page.practice_panel.cards
    tasks.finish("按本次学情推荐练习")
    assert page.practice_panel.cards


@pytest.mark.parametrize("action", ["score", "diagnosis", "refresh", "submission", "edit", "close"])
def test_review_mutations_close_detail_and_invalidate_old_recommendation(make_page, action):
    page, facade, tasks = make_page()
    card = recommend(page, tasks)
    dialog = open_detail(page, tasks, card)
    if action == "score":
        page._record_score({"match_id": "opaque-match", "teacher_score": 1, "reason": "合成评分理由"})
    elif action == "diagnosis":
        page._record_diagnosis({"match_id": "opaque-match", "scoring_decision_id": "opaque-score", "decision": "edit", "result": "partial", "primary_error_type": "concept", "secondary_error_types": (), "curriculum_section_keys": ("opaque-section",), "teacher_note": "合成诊断"})
    elif action == "refresh":
        page._load_review()
    elif action == "submission":
        page._render_submission({"student_id": "opaque-student-a", "submission_id": "opaque-submission-b", "revision": "new", "matches": (), "pages": ()})
    elif action == "edit":
        page._practice_review_edited()
    else:
        page.close()
    assert page._practice_preview is None
    assert page._practice_dialog is None
    assert not dialog.isVisible()
    assert not page.practice_panel.cards
    page._add_practice_theme("opaque-candidate")
    assert facade.add_calls == []


def test_closed_image_preview_does_not_unlock_after_late_image(make_page):
    page, facade, tasks = make_page()
    facade.detail = detail(image=True)
    card = recommend(page, tasks)
    dialog = open_detail(page, tasks, card)
    image_task = tasks.take("读取合成共同材料题图")
    dialog.close()
    image_task.success(b"late invalid bytes")
    assert page._practice_dialog is None
    assert not card.add_button.isEnabled()


@pytest.mark.parametrize("width", [420, 900])
def test_plain_teacher_labels_fit_narrow_and_wide_views_without_opaque_keys(make_page, qt_app, width):
    page, facade, tasks = make_page()
    facade.preview = dict(preview(), recommendations=(dict(preview()["recommendations"][0], title_zh="<b>电子转移</b>与氧化还原反应的课堂练习" * 5),))
    card = recommend(page, tasks)
    page.resize(width, 900)
    for _ in range(8):
        qt_app.processEvents()
    assert page.scroll.widget().width() <= page.scroll.viewport().width()
    assert page.scroll.horizontalScrollBar().maximum() == 0
    assert card.width() <= page.practice_panel.width()
    labels = page.practice_panel.findChildren(QLabel)
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in labels)
    visible = "\n".join(label.text() for label in labels)
    assert "opaque-" not in visible
    assert "命中 1 个单元 · 完整大题 2 单元 · 共同材料 1 项" in visible
    assert "必修第一册" in visible
    assert "<b>电子转移</b>" in visible


def test_empty_detail_is_not_treated_as_completed_preview(make_page):
    page, facade, tasks = make_page()
    facade.detail = replace(facade.detail, parts=())
    card = recommend(page, tasks)
    open_detail(page, tasks, card)
    assert not card.add_button.isEnabled()


def test_user_edit_in_review_editor_requires_saved_decision_or_reload(make_page):
    page, facade, tasks = make_page()
    facade.review = dict(facade.review, items=({
        "label_zh": "第 1 小题", "maximum_score": 2, "latest_teacher_score": 1,
        "latest_scoring_decision_id": "opaque-score", "match_id": "opaque-match",
    },))
    page._review_loaded(facade.review)
    recommend(page, tasks)
    page.review_editors[0].score_edit.textEdited.emit("2")
    assert not page.practice_panel.cards
    assert page._practice_review_dirty
    assert not page.practice_panel.recommend_button.isEnabled()
    page._recommend_practice()
    assert len(facade.preview_calls) == 1
    page._load_review()
    tasks.finish("读取学生分析候选")
    assert not page._practice_review_dirty
    assert page.practice_panel.recommend_button.isEnabled()


def test_closing_detail_before_new_preview_cannot_clear_new_dialog(make_page, qt_app):
    page, _facade, tasks = make_page()
    card = recommend(page, tasks)
    first = open_detail(page, tasks, card)
    second = open_detail(page, tasks, card)
    assert first is not second
    qt_app.processEvents()
    assert page._practice_dialog is second
    assert second.isVisible()
    assert card.add_button.isEnabled()

from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

WORKSPACE = Path(__file__).resolve().parents[4]
UI_ROOT = WORKSPACE / "integrations" / "deeptutor_shchem_v1" / "desktop_workbench"


def test_ui_shell_keeps_five_primary_tasks_and_global_actions() -> None:
    from integrations.deeptutor_shchem_v1.desktop_facade import PRIMARY_NAVIGATION

    assert PRIMARY_NAVIGATION == ("首页", "题库", "组卷", "学生分析", "备课")
    assert len(PRIMARY_NAVIGATION) == 5
    assert "导入资料" not in PRIMARY_NAVIGATION
    assert "设置" not in PRIMARY_NAVIGATION


def test_ui_source_has_native_responsive_surfaces() -> None:
    main = (UI_ROOT / "main_window.py").read_text(encoding="utf-8")
    components = (UI_ROOT / "components.py").read_text(encoding="utf-8")
    home = (UI_ROOT / "home_page.py").read_text(encoding="utf-8")
    library = (UI_ROOT / "library_page.py").read_text(encoding="utf-8")
    dialogs = (UI_ROOT / "dialogs.py").read_text(encoding="utf-8")

    assert "QStackedWidget" in main and "QScrollArea" in components
    assert "QSizePolicy.Policy.Ignored" in components
    assert "QBoxLayout.Direction.TopToBottom" in home
    assert "QBoxLayout.Direction.TopToBottom" in library
    assert "我的讲义（Word）" in library
    assert "打开旧版一轮复习资料包" in dialogs
    assert "选择文件夹" in components
    # Keep this UI package a native local shell.  Build the strings in pieces
    # so this test itself does not become a forbidden-surface fixture.
    forbidden = (
        "http" + "_app",
        "local" + "host",
        "qweb" + "view",
        "qwebengine" + "view",
        "pass" + "code",
    )
    for path in UI_ROOT.glob("*.py"):
        source = path.read_text(encoding="utf-8").casefold()
        for value in forbidden:
            assert value not in source, f"{value!r} leaked into {path.name}"


def _qt_available() -> bool:
    return importlib.util.find_spec("PySide6") is not None


def test_desktop_instance_lock_blocks_second_writer_and_releases(tmp_path) -> None:
    if not _qt_available():
        pytest.skip("PySide6 is available only in the desktop runtime")
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        DesktopInstanceLockError,
        _acquire_desktop_instance_lock,
    )

    state_root = tmp_path / "desktop-state"
    first = _acquire_desktop_instance_lock(state_root)
    try:
        with pytest.raises(DesktopInstanceLockError) as duplicate:
            _acquire_desktop_instance_lock(state_root)
        assert duplicate.value.already_running is True
        assert "已经在运行" in str(duplicate.value)
    finally:
        first.unlock()

    reopened = _acquire_desktop_instance_lock(state_root)
    assert reopened.isLocked()
    reopened.unlock()


@pytest.fixture
def qt_app():
    if not _qt_available():
        pytest.skip("PySide6 is available only in the desktop runtime")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class _State:
    def snapshot(self) -> dict[str, object]:
        return {"basket": [], "drafts": {}}

    def window_state(self) -> dict[str, str]:
        return {}

    def save_window_state(self, **_kwargs: object) -> None:
        return None


class _Facade:
    """Small reader-shaped fixture used only for offscreen widget contracts."""

    def __init__(self) -> None:
        self.state_store = _State()
        self.saved_preparation_payloads: list[dict[str, object]] = []

    def load_desktop_registry(self, **_kwargs: object) -> object:
        product = lambda key: SimpleNamespace(
            product_id=key,
            label_zh=key,
            loaded=True,
            papers=12,
            themes=24,
            atomic_parts=96,
            pending_parent_review=0,
            message_zh="已读取",
        )
        return SimpleNamespace(
            products=tuple(product(key) for key in ("master", "wave1", "supplemental")),
            curriculum=SimpleNamespace(
                loaded=True,
                sections=18,
                volumes=2,
                chapters=6,
                message_zh="已读取",
            ),
        )

    def basket(self) -> list[dict[str, object]]:
        return []

    def search_themes(self, scope: str, query: str, limit: int = 40) -> object:
        card = SimpleNamespace(
            key="theme-fixture",
            title_zh="电化学与能量转化（示例主题）",
            paper_title_zh="上海化学练习卷",
            atomic_total=6,
            source_zh="上海来源 · 2025",
            page_zh="第 2、3 页",
            shared_context_zh="共享材料与前序结论保持在主题内。",
        )
        return SimpleNamespace(
            scope=scope,
            query=query,
            total_themes=1,
            matched_atomic_parts=6,
            cards=(card,),
            has_more=False,
        )

    def search_personal_handouts(
        self, query: str, state: str | None = None, limit: int = 40
    ) -> object:
        return self.search_themes("personal_handouts", query, limit)

    def paper_theme_catalog(self, _scope: str) -> dict[str, object]:
        return {}

    def create_import_draft(self, **_kwargs: object) -> object:
        return SimpleNamespace(message_zh="导入草稿已保存")

    def create_student_analysis_draft(self, **_kwargs: object) -> object:
        return SimpleNamespace(message_zh="学生分析草稿已保存")

    def student_profiles(self) -> tuple[object, ...]:
        return ()

    def student_analysis_profiles(self) -> tuple[object, ...]:
        return ()

    def student_curriculum_sections(self) -> tuple[object, ...]:
        return ()

    def student_submissions(
        self, *, student_id: str, limit: int = 10
    ) -> tuple[object, ...]:
        return ()

    def create_preparation_draft(self, payload: dict[str, object]) -> object:
        self.saved_preparation_payloads.append(payload)
        return SimpleNamespace(message_zh="备课草稿已保存")

    def preparation_availability(self) -> object:
        return SimpleNamespace(
            provider_ready=False,
            renderer_ready=False,
            message_zh="当前仅保存草稿，生成环境尚未配置。",
        )

    def list_provider_profiles(self) -> tuple[object, ...]:
        return ()

    def preparation_profiles(self) -> tuple[object, ...]:
        return ()

    def list_preparations(self, *, limit: int = 3) -> tuple[object, ...]:
        return ()

    def prepare_preparation(
        self, _payload: dict[str, object], _profile_id: str, _revision: str
    ) -> object:
        raise AssertionError("unavailable fixture must not prepare a model task")

    def generate_preparation(self, _task_id: str, **_kwargs: object) -> object:
        raise AssertionError("unavailable fixture must not invoke a model")

    def cancel_preparation(self, _task_id: str) -> object:
        return SimpleNamespace(status="cancel_requested")

    def get_preparation(self, _task_id: str) -> object:
        raise AssertionError("unavailable fixture has no preparation task")

    def retry_preparation(self, _task_id: str) -> object:
        raise AssertionError("unavailable fixture has no retryable preparation task")

    def preparation_artifact_path(self, _task_id: str, _artifact_id: str) -> Path:
        raise AssertionError("unavailable fixture has no artifacts")

    def run_one_round_review_corpus_import(self, **_kwargs: object) -> dict[str, int]:
        return {
            "documents_completed": 196,
            "quick_import_candidates": 120,
            "visual_completion_candidates": 76,
            "paired_question_candidates": 100,
            "documents_failed": 0,
        }


def _settle(app: object, rounds: int = 4) -> None:
    for _ in range(rounds):
        app.processEvents()  # type: ignore[attr-defined]


def _wait_until(app: object, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()  # type: ignore[attr-defined]
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()  # type: ignore[attr-defined]
    return bool(predicate())


def test_paper_preview_hides_student_scores_but_teacher_keeps_them(qt_app) -> None:
    from PySide6.QtWidgets import QLabel

    from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import (
        PaperPreviewDialog,
    )

    preview = {
        "schema_version": "shchem.desktop-paper-preview.v1",
        "mode": "daily_practice",
        "mode_label": "平时练习",
        "title": "分数显示测试",
        "show_question_scores": False,
        "stats": {
            "theme_count": 1,
            "question_count": 1,
            "total_score": 4,
            "total_time_minutes": 10,
        },
        "themes": [
            {
                "display_number": "第1题",
                "title": "氧化还原",
                "source": "本地测试来源",
                "chapter": "氧化还原反应",
                "score": 4,
                "time_minutes": 10,
                "difficulty": "中档",
                "question_count": 1,
                "shared_materials": [],
                "questions": [
                    {
                        "display_number": "第1题·第1问",
                        "response_type": "填空",
                        "score": 4,
                        "section": "氧化还原反应",
                        "difficulty": "中档",
                        "answer_space": 0,
                        "stem": "填写答案。",
                        "answer": "参考答案",
                        "answer_label": "参考答案可用",
                    }
                ],
            }
        ],
        "blockers": [],
    }
    dialog = PaperPreviewDialog(preview)
    try:
        student_text = dialog.meta.text() + "\n" + "\n".join(
            label.text() for label in dialog.paper_body.findChildren(QLabel)
        )
        assert "4 分" not in student_text
        assert "总分" not in student_text

        dialog.teacher_button.setChecked(True)
        teacher_text = dialog.meta.text() + "\n" + "\n".join(
            label.text() for label in dialog.paper_body.findChildren(QLabel)
        )
        assert "4 分" in teacher_text
        assert "参考答案" in teacher_text
    finally:
        dialog.close()


def test_paper_page_score_checkbox_is_independent_and_invalidates_preview(qt_app) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import (
        PaperPage,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )

    bridge = DesktopTaskBridge()
    page = PaperPage(_Facade(), bridge)
    try:
        assert page.show_question_scores.text() == "题面显示分数"
        assert page.show_question_scores.isChecked() is False
        assert (
            page.show_question_scores.toolTip()
            == "默认不显示；教师版评分答案始终保留分值。原图自带的分数不会被涂改。"
        )

        page.show_question_scores.setChecked(True)
        assert page.model.show_question_scores is True
        assert page.model.preview is None
    finally:
        page.close()
        bridge.shutdown(1000)


class _PreparationFacade(_Facade):
    def preparation_egress_preview(self, payload, profile_id, revision):
        assert profile_id == self.profile.profile_id
        assert revision == self.profile.revision
        return {
            "confirmation_text": "本页填写的备课文字发送给示例模型服务 / teacher-model；不发送图片像素；可能产生费用。",
            "local_only_operation": False,
        }

    def preparation_task_egress_preview(self, task_id):
        assert isinstance(task_id, str) and task_id
        return {
            "confirmation_text": "发送原任务冻结文字给示例模型服务 / teacher-model；不发送图片像素；可能产生费用。",
            "local_only_operation": False,
        }

    def __init__(self, artifact_root: Path) -> None:
        super().__init__()
        self.profile = SimpleNamespace(
            profile_id="profile-internal-secret",
            provider_name="示例模型服务",
            model_id="teacher-model",
            revision="revision-internal-secret",
        )
        self.prepare_calls: list[tuple[dict[str, object], str, str]] = []
        self.generate_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []
        self.get_calls: list[str] = []
        self.retry_calls: list[str] = []
        self.list_calls = 0
        self.events: list[str] = []
        self.artifact_calls: list[tuple[str, str]] = []
        self.block_generation = False
        self.complete_during_cancel = False
        self._release_generation = False
        self.prepare_result: object | None = None
        self.artifact_root = artifact_root
        self.completed = SimpleNamespace(
            task_id="PREP-INTERNAL-SECRET",
            status="completed",
            title_zh="氧化还原反应复习",
            output_kind="joint",
            created_at="2026-09-05T12:00:00Z",
            updated_at="2026-09-05T12:01:00Z",
            progress_percent=100,
            message_zh="候选文件已经生成。",
            artifact_ids=("pptx", "lesson_plan_docx", "preview_montage"),
            slide_count=12,
            retryable=False,
        )
        self.prepared = self._summary(
            status="prepared",
            progress_percent=0,
            message_zh="备课文字已经冻结，等待生成。",
        )
        self.cancel_requested = self._summary(
            status="cancel_requested",
            progress_percent=35,
            message_zh="停止请求已提交。",
        )
        self.cancelled = self._summary(
            status="cancelled",
            progress_percent=35,
            message_zh="任务已停止。",
            retryable=True,
        )
        self.current_summary = self.completed
        self.history: tuple[object, ...] = (self.completed,)

    def _summary(
        self,
        *,
        status: str,
        progress_percent: int,
        message_zh: str,
        retryable: bool = False,
    ) -> object:
        return SimpleNamespace(
            task_id="PREP-INTERNAL-SECRET",
            status=status,
            title_zh="氧化还原反应复习",
            output_kind="joint",
            created_at="2026-09-05T12:00:00Z",
            updated_at="2026-09-05T12:01:00Z",
            progress_percent=progress_percent,
            message_zh=message_zh,
            artifact_ids=(),
            slide_count=0,
            retryable=retryable,
        )

    def set_current(self, summary: object) -> None:
        self.current_summary = summary
        self.history = (summary,)

    def preparation_availability(self) -> object:
        return SimpleNamespace(
            provider_ready=True,
            renderer_ready=True,
            message_zh="模型与演示文稿运行环境已就绪。",
        )

    def preparation_profiles(self) -> tuple[object, ...]:
        return (self.profile,)

    def list_preparations(self, *, limit: int = 3) -> tuple[object, ...]:
        self.list_calls += 1
        return self.history[:limit]

    def prepare_preparation(
        self, payload: dict[str, object], profile_id: str, revision: str
    ) -> object:
        self.prepare_calls.append((payload, profile_id, revision))
        self.events.append("prepare")
        result = self.prepare_result or self.prepared
        self.set_current(result)
        return result

    def generate_preparation(
        self,
        task_id: str,
        *,
        teacher_confirmed: bool,
        progress_callback,
        should_cancel,
    ) -> object:
        self.generate_calls.append(
            {"task_id": task_id, "teacher_confirmed": teacher_confirmed}
        )
        self.events.append("generate")
        progress_callback(SimpleNamespace(percent=35, message_zh="正在编排课件…"))
        if self.complete_during_cancel:
            while not self._release_generation:
                time.sleep(0.005)
            self.set_current(self.completed)
            progress_callback(SimpleNamespace(percent=100, message_zh="候选已经生成。"))
            return self.completed
        if self.block_generation:
            while not should_cancel():
                time.sleep(0.005)
        if should_cancel():
            self.set_current(self.cancelled)
            return self.cancelled
        progress_callback(SimpleNamespace(percent=100, message_zh="候选已经生成。"))
        self.set_current(self.completed)
        return self.completed

    def cancel_preparation(self, task_id: str) -> object:
        self.cancel_calls.append(task_id)
        self.events.append("cancel")
        if self.complete_during_cancel:
            self.set_current(self.completed)
            self._release_generation = True
            raise RuntimeError("task completed before cancellation was committed")
        self.set_current(self.cancel_requested)
        return self.cancel_requested

    def get_preparation(self, task_id: str) -> object:
        self.get_calls.append(task_id)
        self.events.append("get")
        return self.current_summary

    def retry_preparation(self, task_id: str) -> object:
        self.retry_calls.append(task_id)
        self.events.append("retry")
        if not bool(getattr(self.current_summary, "retryable", False)):
            raise RuntimeError("task is not retryable")
        self.set_current(self.prepared)
        return self.prepared

    def preparation_artifact_path(self, task_id: str, artifact_id: str) -> Path:
        self.artifact_calls.append((task_id, artifact_id))
        suffix = (
            ".pptx"
            if artifact_id == "pptx"
            else ".docx"
            if artifact_id == "lesson_plan_docx"
            else ".png"
        )
        return self.artifact_root / f"teacher-candidate{suffix}"


def _fill_preparation_page(page: object) -> None:
    page.topic.setText("氧化还原反应")
    page.audience.setText("高二（3）班")
    page.route.setCurrentText("复习")
    page.lesson_count.setValue(2)
    page.lesson_minutes.setValue(40)
    page.objective.setPlainText("能用电子转移解释氧化还原反应。")
    page.materials.setPlainText("教材第三章与教师已选完整主题题。")
    page.learning_detail.setText("班级基础差异较大")
    page.template_detail.setText("黑白打印友好")
    page.strategy_detail.setText("课后分层练习")


def _history_button(page: object, label: str) -> object:
    for layout in page._history_row_layouts:
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is not None and getattr(widget, "text", lambda: "")() == label:
                return widget
    raise AssertionError(f"history action {label!r} was not rendered")


def test_preparation_page_is_teacher_simple_offline_first_and_narrow(
    qt_app, tmp_path
) -> None:
    from PySide6.QtWidgets import QBoxLayout, QLabel, QPushButton, QScrollArea

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    facade = _PreparationFacade(tmp_path)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    page.resize(420, 680)
    page._load_context()
    assert _wait_until(qt_app, page.generate_button.isEnabled)

    assert page.output_kind.currentText() == "PPT 与教案（推荐）"
    assert page.output_kind.currentData() == "joint"
    assert page.profile_single.isVisible()
    assert "示例模型服务 / teacher-model" in page.profile_single.text()
    assert not hasattr(page, "check_button")
    assert all(
        button.text() != "检查生成准备" for button in page.findChildren(QPushButton)
    )
    scroll = page.findChild(QScrollArea, "PageScroll")
    assert scroll is not None
    assert scroll.widget().width() <= scroll.viewport().width()
    assert page.actions.direction() == QBoxLayout.Direction.TopToBottom
    assert page.timing_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert page.output_layout.direction() == QBoxLayout.Direction.TopToBottom

    _fill_preparation_page(page)
    page.save_button.click()
    assert _wait_until(
        qt_app,
        lambda: (
            len(facade.saved_preparation_payloads) == 1 and page._save_task_id is None
        ),
    )
    payload = facade.saved_preparation_payloads[0]
    assert payload["output_kind"] == "joint"
    assert payload["lesson_timing"] == "2课时×40分钟"
    assert payload["advanced"] == {
        "learning_and_experiment": "班级基础差异较大",
        "template_and_delivery": "黑白打印友好",
        "homework_and_strategy": "课后分层练习",
    }
    assert not facade.prepare_calls and not facade.generate_calls
    assert "尚未调用模型" in page.status.text()

    visible_text = "\n".join(label.text() for label in page.findChildren(QLabel))
    assert "PREP-INTERNAL-SECRET" not in visible_text
    assert "profile-internal-secret" not in visible_text
    assert str(tmp_path) not in visible_text
    page.close()
    bridge.shutdown(1000)


def test_preparation_page_without_model_keeps_offline_save_available(qt_app) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    facade = _Facade()
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page._load_context()
    assert _wait_until(qt_app, lambda: page._context_task_id is None)
    assert page.save_button.isEnabled()
    assert not page.generate_button.isEnabled()
    assert "尚无支持结构化输出的可用模型" in page.profile_single.text()
    page.close()
    bridge.shutdown(1000)


def test_preparation_model_confirmation_no_does_not_prepare_or_generate(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    facade = _PreparationFacade(tmp_path)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page._load_context()
    assert _wait_until(qt_app, page.generate_button.isEnabled)
    _fill_preparation_page(page)
    confirmation: dict[str, str] = {}

    def decline_confirmation(*args, **_kwargs):
        confirmation["title"] = str(args[1])
        confirmation["message"] = str(args[2])
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(
        QMessageBox,
        "question",
        decline_confirmation,
    )

    page.generate_button.click()
    _settle(qt_app)
    assert not facade.prepare_calls
    assert not facade.generate_calls
    assert "没有调用模型" in page.status.text()
    assert confirmation["title"] == "确认调用模型"
    assert "示例模型服务 / teacher-model" in confirmation["message"]
    assert "本页填写的备课文字" in confirmation["message"]
    assert "不发送图片" in confirmation["message"]
    assert "可能产生费用" in confirmation["message"]
    page.close()
    bridge.shutdown(1000)


def test_main_generate_retries_reused_failure_after_single_confirmation(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    facade = _PreparationFacade(tmp_path)
    reused_failure = facade._summary(
        status="failed",
        progress_percent=35,
        message_zh="同内容任务的本地编排上次失败，可重试。",
        retryable=True,
    )
    facade.prepare_result = reused_failure
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    page._load_context()
    assert _wait_until(qt_app, page.generate_button.isEnabled)
    _fill_preparation_page(page)
    confirmations: list[str] = []

    def accept_confirmation(*args, **_kwargs):
        confirmations.append(str(args[1]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", accept_confirmation)
    page.generate_button.click()

    assert _wait_until(
        qt_app,
        lambda: page._generation_qt_task_id is None and page.result_card.isVisible(),
    )
    assert confirmations == ["确认调用模型"]
    assert len(facade.prepare_calls) == 1
    assert facade.retry_calls == ["PREP-INTERNAL-SECRET"]
    assert facade.generate_calls == [
        {"task_id": "PREP-INTERNAL-SECRET", "teacher_confirmed": True}
    ]
    assert facade.events[:3] == ["prepare", "retry", "generate"]
    page.close()
    bridge.shutdown(1000)


def test_main_generate_renders_reused_completion_without_generation(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    facade = _PreparationFacade(tmp_path)
    facade.prepare_result = facade.completed
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    page._load_context()
    assert _wait_until(qt_app, page.generate_button.isEnabled)
    _fill_preparation_page(page)
    confirmations: list[str] = []

    def accept_confirmation(*args, **_kwargs):
        confirmations.append(str(args[1]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", accept_confirmation)
    page.generate_button.click()

    assert _wait_until(qt_app, page.result_card.isVisible)
    assert confirmations == ["确认调用模型"]
    assert len(facade.prepare_calls) == 1
    assert not facade.retry_calls
    assert not facade.generate_calls
    assert facade.events == ["prepare"]
    assert page.progress.value() == 100
    assert "请检查内容和排版" in page.status.text()
    page.close()
    bridge.shutdown(1000)


def test_preparation_confirmed_generation_reports_candidate_and_opens_artifact(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QLabel, QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench import workflow_pages
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )

    facade = _PreparationFacade(tmp_path)
    bridge = DesktopTaskBridge()
    page = workflow_pages.PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    page._load_context()
    assert _wait_until(qt_app, page.generate_button.isEnabled)
    _fill_preparation_page(page)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    page.generate_button.click()
    assert _wait_until(
        qt_app,
        lambda: bool(facade.generate_calls) and page.result_card.isVisible(),
    )
    assert len(facade.prepare_calls) == 1
    payload, profile_id, revision = facade.prepare_calls[0]
    assert profile_id == "profile-internal-secret"
    assert revision == "revision-internal-secret"
    assert payload["advanced"] == {
        "learning_and_experiment": "班级基础差异较大",
        "template_and_delivery": "黑白打印友好",
        "homework_and_strategy": "课后分层练习",
    }
    assert facade.generate_calls == [
        {"task_id": "PREP-INTERNAL-SECRET", "teacher_confirmed": True}
    ]
    assert page.progress.value() == 100
    assert "请检查内容和排版" in page.status.text()
    assert "初稿" in page.result_summary.text()

    opened: list[str] = []
    monkeypatch.setattr(
        workflow_pages,
        "QDesktopServices",
        SimpleNamespace(
            openUrl=lambda url: opened.append(url.toLocalFile()) or True,
        ),
    )
    page.open_ppt_button.click()
    assert facade.artifact_calls[-1] == ("PREP-INTERNAL-SECRET", "pptx")
    assert opened and opened[-1].endswith("teacher-candidate.pptx")

    visible_text = "\n".join(label.text() for label in page.findChildren(QLabel))
    assert "PREP-INTERNAL-SECRET" not in visible_text
    assert "revision-internal-secret" not in visible_text
    assert str(tmp_path) not in visible_text
    page.close()
    bridge.shutdown(1000)


def test_preparation_stop_cancels_facade_and_qt_worker(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    class RecordingBridge(DesktopTaskBridge):
        def __init__(self) -> None:
            super().__init__()
            self.cancel_calls: list[str] = []

        def cancel(self, task_id: str) -> None:
            self.cancel_calls.append(task_id)
            super().cancel(task_id)

    facade = _PreparationFacade(tmp_path)
    facade.block_generation = True
    bridge = RecordingBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page._load_context()
    assert _wait_until(qt_app, page.generate_button.isEnabled)
    _fill_preparation_page(page)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    page.generate_button.click()
    assert _wait_until(qt_app, lambda: len(facade.generate_calls) == 1)
    qt_task_id = page._generation_qt_task_id
    assert qt_task_id
    page.stop_button.click()
    assert facade.cancel_calls == ["PREP-INTERNAL-SECRET"]
    assert bridge.cancel_calls == [qt_task_id]
    assert "停止请求已提交" in page.progress_message.text()
    assert _wait_until(
        qt_app,
        lambda: (
            page._generation_qt_task_id is None
            and bool(facade.get_calls)
            and facade.list_calls >= 2
        ),
    )
    assert "已停止" in page.status.text()
    assert not page.stop_button.isEnabled()
    assert page.task_action_button.text() == "重试生成"
    assert not page.task_action_button.isHidden()
    assert facade.current_summary is facade.cancelled
    assert facade.list_calls >= 2
    page.close()
    bridge.shutdown(1000)


def test_preparation_stop_completion_race_keeps_completed_result(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    class RecordingBridge(DesktopTaskBridge):
        def __init__(self) -> None:
            super().__init__()
            self.cancel_calls: list[str] = []

        def cancel(self, task_id: str) -> None:
            self.cancel_calls.append(task_id)
            super().cancel(task_id)

    facade = _PreparationFacade(tmp_path)
    facade.complete_during_cancel = True
    bridge = RecordingBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    page._load_context()
    assert _wait_until(qt_app, page.generate_button.isEnabled)
    _fill_preparation_page(page)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    page.generate_button.click()
    assert _wait_until(qt_app, lambda: len(facade.generate_calls) == 1)
    page.stop_button.click()

    assert facade.cancel_calls == ["PREP-INTERNAL-SECRET"]
    assert facade.get_calls == ["PREP-INTERNAL-SECRET"]
    assert bridge.cancel_calls == []
    assert _wait_until(
        qt_app,
        lambda: page._generation_qt_task_id is None and page.result_card.isVisible(),
    )
    assert facade.current_summary is facade.completed
    assert page.progress.value() == 100
    assert "请检查内容和排版" in page.status.text()
    assert "已停止" not in page.status.text()
    page.close()
    bridge.shutdown(1000)


def test_retryable_failed_history_retries_once_before_generation(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    facade = _PreparationFacade(tmp_path)
    facade.set_current(
        facade._summary(
            status="failed",
            progress_percent=35,
            message_zh="本地课件编排失败，可重试。",
            retryable=True,
        )
    )
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    page._load_context()
    assert _wait_until(
        qt_app,
        lambda: page._context_task_id is None and bool(page._history_row_layouts),
    )
    confirmation: dict[str, str] = {}

    def accept_confirmation(*args, **_kwargs):
        confirmation["title"] = str(args[1])
        confirmation["message"] = str(args[2])
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", accept_confirmation)
    _history_button(page, "重试生成").click()

    assert _wait_until(
        qt_app,
        lambda: page._generation_qt_task_id is None and page.result_card.isVisible(),
    )
    assert confirmation["title"] == "确认重新生成"
    assert "原任务冻结文字" in confirmation["message"]
    assert "不发送图片像素" in confirmation["message"]
    assert facade.retry_calls == ["PREP-INTERNAL-SECRET"]
    assert facade.generate_calls == [
        {"task_id": "PREP-INTERNAL-SECRET", "teacher_confirmed": True}
    ]
    assert facade.events[:2] == ["retry", "generate"]
    assert not facade.prepare_calls
    page.close()
    bridge.shutdown(1000)


def test_nonretryable_failed_history_only_shows_reason(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    facade = _PreparationFacade(tmp_path)
    failure = facade._summary(
        status="failed",
        progress_percent=35,
        message_zh="冻结候选无效，不能直接重试。",
        retryable=False,
    )
    facade.set_current(failure)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    page._load_context()
    assert _wait_until(
        qt_app,
        lambda: page._context_task_id is None and bool(page._history_row_layouts),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("nonretryable failures must not ask to generate")
        ),
    )

    _history_button(page, "查看原因").click()
    _settle(qt_app)

    assert facade.current_summary is failure
    assert not facade.retry_calls
    assert not facade.generate_calls
    assert not facade.prepare_calls
    assert page.task_action_button.isHidden()
    assert "不能直接重试" in page.status.text()
    page.close()
    bridge.shutdown(1000)


def test_prepared_history_starts_generation_without_repreparing(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    facade = _PreparationFacade(tmp_path)
    facade.set_current(facade.prepared)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    page._load_context()
    assert _wait_until(
        qt_app,
        lambda: page._context_task_id is None and bool(page._history_row_layouts),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    _history_button(page, "开始生成").click()

    assert _wait_until(
        qt_app,
        lambda: page._generation_qt_task_id is None and page.result_card.isVisible(),
    )
    assert facade.get_calls == ["PREP-INTERNAL-SECRET"]
    assert facade.generate_calls == [
        {"task_id": "PREP-INTERNAL-SECRET", "teacher_confirmed": True}
    ]
    assert facade.events[:2] == ["get", "generate"]
    assert not facade.retry_calls
    assert not facade.prepare_calls
    page.close()
    bridge.shutdown(1000)


def test_narrow_window_wraps_home_and_library_without_horizontal_overflow(
    qt_app,
) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QBoxLayout, QScrollArea

    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        TeacherWorkbenchWindow,
    )

    window = TeacherWorkbenchWindow(_Facade())
    from integrations.deeptutor_shchem_v1.desktop_version import DESKTOP_VERSION

    assert window.version_label.text() == f"v{DESKTOP_VERSION}"
    assert window.version_label.objectName() == "DesktopVersion"
    window.show()
    window.resize(360, 560)
    _settle(qt_app)
    home_scroll = window.home_page.findChild(QScrollArea, "PageScroll")
    assert home_scroll is not None
    assert home_scroll.widget().width() <= home_scroll.viewport().width()
    assert window.home_page.action_row.direction() == QBoxLayout.Direction.TopToBottom
    assert all(
        button.width() >= button.fontMetrics().horizontalAdvance(button.text())
        for button in window.nav_buttons
    )

    window.navigate("library")
    _settle(qt_app)
    library_scroll = window.library_page.findChild(QScrollArea, "PageScroll")
    assert library_scroll is not None
    assert library_scroll.widget().width() <= library_scroll.viewport().width()
    assert (
        window.library_page.search_row.direction() == QBoxLayout.Direction.TopToBottom
    )
    assert window.library_page.splitter.orientation() == Qt.Orientation.Vertical
    window.close()
    _settle(qt_app)


def test_import_and_settings_dialogs_keep_clear_chinese_actions_and_close_guard(
    qt_app,
) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import (
        ImportDialog,
        SettingsDialog,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )

    bridge = DesktopTaskBridge()
    import_dialog = ImportDialog(_Facade(), bridge)
    import_dialog.show()
    _settle(qt_app)
    assert "旧版" in import_dialog.corpus_button.text()
    assert "资料包" in import_dialog.corpus_button.text()
    assert import_dialog.save_button.text() == "预览并选择导入"
    assert any(
        button.text() == "选择文件夹"
        for button in import_dialog.files.findChildren(type(import_dialog.save_button))
    )
    import_dialog._active_task_id = "busy"
    import_dialog._corpus_task_id = "busy"
    import_dialog.reject()
    assert import_dialog.isVisible()
    import_dialog._corpus_failed("批量读取未完成")
    assert import_dialog.corpus_button.text() == "重试旧版一轮复习资料包"
    import_dialog._task_finished("busy")
    import_dialog.close()

    settings = SettingsDialog(_Facade(), bridge)
    settings.show()
    _settle(qt_app)
    assert settings.close_button is not None
    assert settings.close_button.text() == "关闭"
    assert settings.close_button.accessibleName() == "关闭设置窗口"
    settings.close()
    bridge.cancel_all()
    bridge.wait_for_done(1000)
    _settle(qt_app)


def test_task_bridge_shutdown_detaches_a_slow_worker_without_qt_teardown_noise(
    qt_app,
) -> None:
    """Closing the window must be safe even when a reader ignores cancellation briefly."""

    from PySide6.QtCore import QTimer

    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )

    bridge = DesktopTaskBridge()
    bridge.submit("慢速读取", lambda: (time.sleep(0.18), "完成")[1])
    # Give QThreadPool a chance to start the worker, then exercise the same
    # bounded shutdown path used by TeacherWorkbenchWindow.closeEvent.
    _settle(qt_app, rounds=2)
    bridge.shutdown(5)
    bridge.deleteLater()
    for _ in range(8):
        qt_app.processEvents()
        time.sleep(0.03)
    # A queued callback after QObject teardown would raise from Qt and fail
    # the test process; reaching this point is the contract we need.
    QTimer.singleShot(0, lambda: None)


class _StudentFacade(_Facade):
    """In-process student-analysis fake with opaque action bindings."""

    def __init__(self, artifact_root: Path) -> None:
        super().__init__()
        self.artifact_root = artifact_root
        self.student = SimpleNamespace(
            student_id="student-internal-secret",
            label_zh="匿名学生-A1B2C3",
            grade="高二",
            retention_days=180,
            created_at="2026-09-05 09:00",
        )
        self.profile = SimpleNamespace(
            profile_id="profile-internal-secret",
            provider_name="示例视觉服务",
            model_id="teacher-vision-model",
            capabilities=("vision", "structured_output"),
            key_saved=True,
            revision="profile-revision-internal-secret",
        )
        self.sections = (
            SimpleNamespace(
                section_key="section-internal-secret",
                display_label_zh="选择性必修 1 · 化学反应原理",
            ),
        )
        self.add_calls: list[dict[str, object]] = []
        self.confirm_calls: list[dict[str, object]] = []
        self.confirmation_calls: list[dict[str, object]] = []
        self.start_calls: list[dict[str, object]] = []
        self.poll_calls: list[tuple[str, str]] = []
        self.cancel_calls: list[dict[str, object]] = []
        self.score_calls: list[dict[str, object]] = []
        self.diagnosis_calls: list[dict[str, object]] = []
        self._pages: list[object] = []
        self._roles_seen: set[str] = set()
        self._revision_number = 0
        self.current: object | None = None
        self.poll_results: list[object] = []
        self.start_result: object | None = None
        self.review = self._review(blocked=True)

    def _revision(self) -> str:
        self._revision_number += 1
        return f"revision-internal-secret-{self._revision_number}"

    def _matches(self) -> tuple[object, ...]:
        question = [page for page in self._pages if page.role == "question_pages"]
        work = [page for page in self._pages if page.role == "student_work_pages"]
        reference = [
            page for page in self._pages if page.role == "reference_answer_pages"
        ]
        if not question or not work:
            return ()
        return (
            SimpleNamespace(
                match_id="match-internal-secret",
                label_zh="第 1 个小题匹配",
                question_number_hint="1(1)",
                maximum_score=3.0,
                question_page_sha256=question[0].sha256,
                student_work_page_sha256=work[0].sha256,
                reference_answer_page_sha256=(
                    reference[0].sha256 if reference else None
                ),
            ),
        )

    def _summary(
        self,
        *,
        status: str,
        status_zh: str,
        message_zh: str,
        candidate_available: bool = False,
        can_confirm_matching: bool = False,
        can_analyze: bool = False,
        can_retry: bool = False,
        can_cancel: bool = False,
    ) -> object:
        counts = {
            role: sum(1 for page in self._pages if page.role == role)
            for role, *_ in (
                ("question_pages",),
                ("reference_answer_pages",),
                ("student_work_pages",),
            )
        }
        return SimpleNamespace(
            student_id=self.student.student_id,
            submission_id="submission-internal-secret",
            revision=self._revision(),
            status=status,
            status_zh=status_zh,
            message_zh=message_zh,
            created_at="2026-09-05 10:00",
            updated_at="2026-09-05 10:01",
            files=(),
            pages=tuple(self._pages),
            matches=self._matches(),
            page_counts_by_role=counts,
            match_count=len(self._matches()),
            scoring_confirmed_count=0,
            diagnostic_confirmed_count=0,
            candidate_available=candidate_available,
            can_confirm_matching=can_confirm_matching,
            can_analyze=can_analyze,
            can_retry=can_retry,
            can_cancel=can_cancel,
        )

    def ready_summary(self) -> object:
        return self._summary(
            status="ready_for_analysis",
            status_zh="可以开始分析",
            message_zh="页面匹配已确认。",
            can_analyze=True,
        )

    def active_summary(self) -> object:
        return self._summary(
            status="analyzing",
            status_zh="视觉分析中",
            message_zh="正在处理已确认页面。",
            can_cancel=True,
        )

    def failed_summary(self) -> object:
        return self._summary(
            status="analysis_failed",
            status_zh="分析失败",
            message_zh="模型未返回可用候选。",
            can_retry=True,
        )

    def candidate_summary(self) -> object:
        return self._summary(
            status="awaiting_teacher_review",
            status_zh="等待教师复核",
            message_zh="视觉候选已生成。",
            candidate_available=True,
        )

    def _review(self, *, blocked: bool, scored: bool = False) -> object:
        item = SimpleNamespace(
            match_id="match-internal-secret",
            label_zh="第 1 小题",
            question_number="1(1)",
            maximum_score=3.0,
            suggested_score=0.0,
            suggested_score_withheld=blocked,
            confidence=0.35,
            observation_zh="作答区域边缘存在裁切，只能提供候选观察。",
            chemistry_observations_zh=("可见一处化学方程式书写区域。",),
            scoring_points_zh=("核对反应物、条件和配平。",),
            error_hypotheses_zh=("可能遗漏反应条件；需教师核对原页。",),
            blockers_zh=("学生作答页下缘裁切。",) if blocked else (),
            latest_teacher_score=1.0 if scored else None,
            latest_scoring_decision_id=(
                "scoring-decision-internal-secret" if scored else None
            ),
            latest_diagnostic_decision=None,
        )
        return SimpleNamespace(
            student_id=self.student.student_id,
            submission_id="submission-internal-secret",
            revision=self._revision(),
            status="awaiting_teacher_review",
            status_zh="等待教师复核",
            message_zh="请按当前临时匹配项依据可见作答记录教师决定。",
            items=(item,),
            candidate_blockers_zh=("页面裁切时不得直接判错。",) if blocked else (),
            scoring_confirmed_count=1 if scored else 0,
            diagnostic_confirmed_count=0,
            review_complete=False,
            candidate_only=True,
            long_term_update_allowed=False,
        )

    def student_profiles(self) -> tuple[object, ...]:
        return (self.student,)

    def create_student_profile(self, **kwargs: object) -> object:
        assert kwargs["consent_recorded"] is True
        return self.student

    def student_analysis_profiles(self) -> tuple[object, ...]:
        return (self.profile,)

    def student_curriculum_sections(self) -> tuple[object, ...]:
        return self.sections

    def create_student_submission(self, student_id: str) -> object:
        assert student_id == self.student.student_id
        self._pages.clear()
        self._roles_seen.clear()
        self.current = self._summary(
            status="awaiting_upload",
            status_zh="等待本机保存",
            message_zh="等待添加页面。",
        )
        return self.current

    def add_student_submission_files(self, **kwargs: object) -> object:
        role = str(kwargs["role"])
        paths = tuple(str(path) for path in kwargs["files"])
        self.add_calls.append({**kwargs, "files": paths})
        self._roles_seen.add(role)
        role_zh = {
            "question_pages": "题目页面",
            "reference_answer_pages": "参考答案页面",
            "student_work_pages": "学生作答页面",
        }[role]
        for path in paths:
            ordinal = 1 + sum(1 for page in self._pages if page.role == role)
            token = {
                "question_pages": "a",
                "reference_answer_pages": "b",
                "student_work_pages": "c",
            }[role]
            self._pages.append(
                SimpleNamespace(
                    file_id=f"file-internal-secret-{token}-{ordinal}",
                    role=role,
                    role_zh=role_zh,
                    ordinal=ordinal,
                    page_number=1,
                    label_zh=f"{role_zh} {ordinal}",
                    mime_type="image/png",
                    width=1200,
                    height=1600,
                    sha256=token * 64,
                )
            )
        if {"question_pages", "student_work_pages"}.issubset(self._roles_seen):
            self.current = self._summary(
                status="awaiting_matching_confirmation",
                status_zh="等待教师确认页面匹配",
                message_zh="本机页面已准备。",
                can_confirm_matching=True,
            )
        else:
            self.current = self._summary(
                status="awaiting_upload",
                status_zh="等待本机保存",
                message_zh="继续添加必需页面。",
            )
        return self.current

    def student_submissions(
        self, *, student_id: str, limit: int = 10
    ) -> tuple[object, ...]:
        assert student_id == self.student.student_id
        assert limit == 10
        return () if self.current is None else (self.current,)

    def student_submission(self, *, student_id: str, submission_id: str) -> object:
        self.poll_calls.append((student_id, submission_id))
        if self.poll_results:
            self.current = self.poll_results.pop(0)
        assert self.current is not None
        return self.current

    def confirm_student_matching(self, **kwargs: object) -> object:
        self.confirm_calls.append(dict(kwargs))
        self.current = self.ready_summary()
        return self.current

    def student_submission_page(self, **_kwargs: object) -> tuple[bytes, str]:
        if hasattr(self, "_egress_pixels"):
            return self._egress_pixels[_kwargs["page_sha256"]], "image/png"
        return (b"not-a-real-image", "image/png")

    def prepare_student_analysis_confirmation(self, **kwargs: object) -> object:
        import hashlib
        import io

        from PIL import Image

        self.confirmation_calls.append(dict(kwargs))
        pages = []
        self._egress_pixels = {}
        for number, original in enumerate(self._pages):
            stream = io.BytesIO()
            Image.new("RGB", (80, 100), (40 + number * 30, 80, 190)).save(stream, format="PNG")
            raw = stream.getvalue()
            sha = hashlib.sha256(raw).hexdigest()
            self._egress_pixels[sha] = raw
            pages.append(SimpleNamespace(**{**vars(original), "sha256": sha, "width": 80, "height": 100}))
        return SimpleNamespace(
            student_id=self.student.student_id,
            submission_id="submission-internal-secret",
            expected_revision="revision-internal-secret-confirmation",
            provider_profile_id=self.profile.profile_id,
            provider_revision=self.profile.revision,
            page_sha256=tuple(page.sha256 for page in pages),
            pages=tuple(pages),
            provider_label_zh="示例视觉服务 · teacher-vision-model",
            total_page_count=2,
            page_counts_by_role={
                "question_pages": 1,
                "reference_answer_pages": 0,
                "student_work_pages": 1,
            },
            student_label_zh=self.student.label_zh,
            retention_days=180,
            message_zh="确认后才会发送这次冻结的页面。",
        )

    def start_student_analysis(self, **kwargs: object) -> object:
        self.start_calls.append(dict(kwargs))
        self.current = self.start_result or self.candidate_summary()
        return self.current

    def cancel_student_analysis(self, **kwargs: object) -> object:
        self.cancel_calls.append(dict(kwargs))
        self.current = self._summary(
            status="cancelled",
            status_zh="已取消",
            message_zh="分析已取消，请新建一次分析。",
        )
        return self.current

    def student_analysis_review(self, **_kwargs: object) -> object:
        return self.review

    def record_student_score(self, **kwargs: object) -> object:
        self.score_calls.append(dict(kwargs))
        self.review = self._review(blocked=False, scored=True)
        return self.review

    def record_student_diagnosis(self, **kwargs: object) -> object:
        self.diagnosis_calls.append(dict(kwargs))
        return self.review


def _student_page(qt_app, tmp_path: Path, facade: _StudentFacade | None = None):
    from integrations.deeptutor_shchem_v1.desktop_workbench.student_page import (
        StudentPage,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )

    facade = facade or _StudentFacade(tmp_path)
    bridge = DesktopTaskBridge()
    page = StudentPage(facade, bridge)
    page.show()
    assert _wait_until(qt_app, lambda: page._context_task_id is None)
    return facade, bridge, page


class _StudentFacadeWithoutCurriculum(_StudentFacade):
    def student_curriculum_sections(self) -> tuple[object, ...]:
        from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError

        raise DesktopFacadeError(
            "curriculum_catalog_invalid", "当前教材目录没有可选章节。"
        )


class _ManualStudentBridge:
    """Deterministic bridge for exercising deliberately out-of-order callbacks."""

    def __init__(self) -> None:
        self._counter = 0
        self.pending: list[SimpleNamespace] = []

    def submit(
        self,
        label: str,
        operation,
        *,
        on_success=None,
        on_failure=None,
    ) -> str:
        self._counter += 1
        task_id = f"manual-task-{self._counter}"
        self.pending.append(
            SimpleNamespace(
                task_id=task_id,
                label=label,
                operation=operation,
                on_success=on_success,
                on_failure=on_failure,
            )
        )
        return task_id

    def take(self, label: str) -> SimpleNamespace:
        for index, task in enumerate(self.pending):
            if task.label == label:
                return self.pending.pop(index)
        raise AssertionError(f"No pending task named {label!r}")

    @staticmethod
    def succeed(task: SimpleNamespace, value=None, *, run: bool = False) -> None:
        result = task.operation() if run else value
        if task.on_success is not None:
            task.on_success(result)


def _manual_student_page(qt_app, tmp_path: Path):
    from integrations.deeptutor_shchem_v1.desktop_workbench.student_page import (
        StudentPage,
    )

    facade = _StudentFacade(tmp_path)
    bridge = _ManualStudentBridge()
    page = StudentPage(facade, bridge)
    page.show()
    _settle(qt_app)
    context = bridge.take("读取学生分析本机状态")
    bridge.succeed(context, run=True)
    recent = bridge.take("读取学生最近任务")
    bridge.succeed(recent, ())
    _settle(qt_app)
    return facade, bridge, page


def test_student_page_three_ordered_roles_and_responsive_widths(
    qt_app, tmp_path
) -> None:
    from PySide6.QtWidgets import QBoxLayout, QScrollArea

    facade, bridge, page = _student_page(qt_app, tmp_path)
    page.resize(420, 900)
    _settle(qt_app)
    scroll = page.findChild(QScrollArea, "PageScroll")
    assert scroll is not None
    assert scroll.widget().width() <= scroll.viewport().width()
    assert page.identity_row.direction() == QBoxLayout.Direction.TopToBottom
    assert tuple(page.file_panels) == (
        "question_pages",
        "reference_answer_pages",
        "student_work_pages",
    )
    assert "*.webp" in page.question_files._dialog_filter()
    assert "*.pptx" not in page.question_files._dialog_filter()
    assert "*.heic" not in page.question_files._dialog_filter()

    question_second = tmp_path / "question-02.png"
    question_first = tmp_path / "question-01.png"
    reference = tmp_path / "reference.webp"
    work = tmp_path / "student-work.pdf"
    for path in (question_second, question_first, reference, work):
        path.write_bytes(b"fixture")
    page.question_files.file_list._append_paths(
        [str(question_second), str(question_first)]
    )
    page.reference_files.file_list._append_paths([str(reference)])
    page.student_files.file_list._append_paths([str(work)])
    page.prepare_button.click()
    assert _wait_until(qt_app, lambda: len(facade.add_calls) == 3)
    assert [call["role"] for call in facade.add_calls] == [
        "question_pages",
        "reference_answer_pages",
        "student_work_pages",
    ]
    assert facade.add_calls[0]["files"] == (
        str(question_second.resolve()),
        str(question_first.resolve()),
    )
    assert _wait_until(qt_app, page.matching_card.isVisible)
    assert not page.question_files.isVisible()
    assert not page.reference_files.isVisible()
    assert not page.student_files.isVisible()

    page.resize(900, 900)
    _settle(qt_app)
    assert scroll.widget().width() <= scroll.viewport().width()
    assert page.identity_row.direction() == QBoxLayout.Direction.LeftToRight
    page.close()
    bridge.shutdown(1000)


def test_switching_anonymous_student_clears_unsubmitted_file_paths(
    qt_app, tmp_path
) -> None:
    facade, bridge, page = _student_page(qt_app, tmp_path)
    source = tmp_path / "must-not-cross-students.png"
    source.write_bytes(b"fixture")
    page.question_files.file_list._append_paths([str(source)])
    assert page.question_files.paths()
    second = SimpleNamespace(
        student_id="second-student-internal-secret",
        label_zh="匿名学生-D4E5F6",
        grade="高二",
        retention_days=180,
        created_at="2026-09-05 09:30",
    )
    facade.student = second
    page.student_combo.addItem("匿名学生-D4E5F6 · 高二", second)
    page.student_combo.setCurrentIndex(1)
    _settle(qt_app)
    assert page.question_files.paths() == []
    assert page.reference_files.paths() == []
    assert page.student_files.paths() == []
    page.close()
    bridge.shutdown(1000)


def test_student_switch_keeps_replacement_recent_request_when_old_result_arrives(
    qt_app, tmp_path
) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.student_page import (
        StudentPage,
    )

    facade = _StudentFacade(tmp_path)
    bridge = _ManualStudentBridge()
    page = StudentPage(facade, bridge)
    page.show()
    _settle(qt_app)
    context = bridge.take("读取学生分析本机状态")
    bridge.succeed(context, run=True)
    old_recent = bridge.take("读取学生最近任务")
    old_summary = facade._summary(
        status="analysis_failed",
        status_zh="旧学生任务",
        message_zh="不得显示到新学生视图。",
    )

    second = SimpleNamespace(
        student_id="second-student-internal-secret",
        label_zh="匿名学生-D4E5F6",
        grade="高二",
        retention_days=180,
        created_at="2026-09-05 09:30",
    )
    facade.student = second
    page.student_combo.addItem("匿名学生-D4E5F6 · 高二", second)
    page.student_combo.setCurrentIndex(1)
    new_recent = bridge.take("读取学生最近任务")
    assert page._recent_task_id == new_recent.task_id
    assert page._recent_rows == []

    bridge.succeed(old_recent, (old_summary,))
    assert page._recent_task_id == new_recent.task_id
    assert page._recent_rows == []
    assert "旧学生任务" not in page.recent_status.text()

    bridge.succeed(new_recent, ())
    assert page._recent_task_id is None
    assert "尚无分析任务" in page.recent_status.text()
    page.close()


@pytest.mark.parametrize("stale_kind", ["poll", "preview", "review_action", "open"])
def test_student_switch_discards_stale_submission_callbacks(
    qt_app, tmp_path, monkeypatch, stale_kind
) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.student_page import (
        PagePreviewDialog,
    )

    facade, bridge, page = _manual_student_page(qt_app, tmp_path)
    facade._pages = [
        SimpleNamespace(
            file_id="question-file-internal-secret",
            role="question_pages",
            role_zh="题目页面",
            ordinal=1,
            page_number=1,
            label_zh="题目页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="a" * 64,
        ),
        SimpleNamespace(
            file_id="work-file-internal-secret",
            role="student_work_pages",
            role_zh="学生作答页面",
            ordinal=1,
            page_number=1,
            label_zh="学生作答页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="c" * 64,
        ),
    ]
    old_candidate = facade.candidate_summary()
    old_scored_review = facade._review(blocked=False, scored=True)
    preview_calls: list[bool] = []
    monkeypatch.setattr(
        PagePreviewDialog,
        "exec",
        lambda _dialog: preview_calls.append(True),
    )

    if stale_kind == "poll":
        active = facade.active_summary()
        page._render_submission(active)
        page.poll_timer.stop()
        page._poll_submission()
        stale_task = bridge.take("刷新学生分析状态")
        stale_value = old_candidate
    elif stale_kind == "preview":
        ready = facade.ready_summary()
        page._render_submission(ready)
        page._preview_page(facade._pages[0])
        stale_task = bridge.take("读取学生页面预览")
        stale_value = (b"not-an-image", "image/png")
    elif stale_kind == "review_action":
        page._render_submission(old_candidate)
        review_task = bridge.take("读取学生分析候选")
        bridge.succeed(review_task, facade.review)
        page._record_score(
            {
                "match_id": "match-internal-secret",
                "teacher_score": 1.0,
                "reason": "教师核对可见作答后决定。",
            }
        )
        stale_task = bridge.take("记录学生教师评分")
        stale_value = old_scored_review
    else:
        page._open_recent(old_candidate)
        stale_task = bridge.take("打开学生分析任务")
        stale_value = old_candidate

    second = SimpleNamespace(
        student_id="second-student-internal-secret",
        label_zh="匿名学生-D4E5F6",
        grade="高二",
        retention_days=180,
        created_at="2026-09-05 09:30",
    )
    facade.student = second
    page.student_combo.addItem("匿名学生-D4E5F6 · 高二", second)
    page.student_combo.setCurrentIndex(1)
    replacement_recent = bridge.take("读取学生最近任务")
    assert page._current_summary is None

    bridge.succeed(stale_task, stale_value)
    assert page._current_summary is None
    assert page._current_review is None
    assert page.review_editors == []
    assert page._recent_task_id == replacement_recent.task_id
    assert preview_calls == []

    bridge.succeed(replacement_recent, ())
    page.close()


@pytest.mark.parametrize("dialog_result", ["decline", "unchecked_accept"])
def test_student_analysis_requires_both_explicit_confirmations(
    qt_app, tmp_path, monkeypatch, dialog_result
) -> None:
    from PySide6.QtWidgets import QDialog

    from integrations.deeptutor_shchem_v1.desktop_workbench.student_page import (
        AnalysisConfirmationDialog,
    )

    facade, bridge, page = _student_page(qt_app, tmp_path)
    facade._pages = [
        SimpleNamespace(
            file_id="question-file-internal-secret",
            role="question_pages",
            role_zh="题目页面",
            ordinal=1,
            page_number=1,
            label_zh="题目页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="a" * 64,
        ),
        SimpleNamespace(
            file_id="work-file-internal-secret",
            role="student_work_pages",
            role_zh="学生作答页面",
            ordinal=1,
            page_number=1,
            label_zh="学生作答页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="c" * 64,
        ),
    ]
    ready = facade.ready_summary()
    facade.current = ready
    page._render_submission(ready)
    assert page.analyze_button.isEnabled()

    def fake_exec(dialog: AnalysisConfirmationDialog):
        dialog.reject()
        if dialog_result == "decline":
            return QDialog.DialogCode.Rejected
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AnalysisConfirmationDialog, "exec", fake_exec)
    page.analyze_button.click()
    assert _wait_until(qt_app, lambda: len(facade.confirmation_calls) == 1)
    assert _wait_until(qt_app, lambda: "未发送任何页面" in page.model_status.text())
    _settle(qt_app)
    assert facade.start_calls == []
    assert "未发送任何页面" in page.model_status.text()
    page.close()
    bridge.shutdown(1000)


def test_student_candidate_keeps_teacher_score_blank_and_hides_opaque_values(
    qt_app, tmp_path
) -> None:
    from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

    facade, bridge, page = _student_page(qt_app, tmp_path)
    facade._pages = [
        SimpleNamespace(
            file_id="question-file-internal-secret",
            role="question_pages",
            role_zh="题目页面",
            ordinal=1,
            page_number=1,
            label_zh="题目页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="a" * 64,
        ),
        SimpleNamespace(
            file_id="work-file-internal-secret",
            role="student_work_pages",
            role_zh="学生作答页面",
            ordinal=1,
            page_number=1,
            label_zh="学生作答页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="c" * 64,
        ),
    ]
    candidate = facade.candidate_summary()
    facade.current = candidate
    page._render_submission(candidate)
    assert _wait_until(qt_app, lambda: len(page.review_editors) == 1)
    editor = page.review_editors[0]
    assert editor.score_edit.text() == ""
    assert "模型建议分未显示" in editor.suggestion_label.text()
    assert "不能据此判错" in editor.suggestion_label.text()
    assert not editor.record_diagnosis_button.isEnabled()

    visible_parts = [label.text() for label in page.findChildren(QLabel)]
    visible_parts.extend(button.text() for button in page.findChildren(QPushButton))
    for combo in page.findChildren(QComboBox):
        visible_parts.extend(combo.itemText(index) for index in range(combo.count()))
    visible_text = "\n".join(visible_parts)
    for opaque in (
        "student-internal-secret",
        "submission-internal-secret",
        "match-internal-secret",
        "profile-internal-secret",
        "profile-revision-internal-secret",
        "a" * 64,
        "c" * 64,
        str(tmp_path),
    ):
        assert opaque not in visible_text
    assert "候选" in visible_text
    assert "不会自动写入长期学情" in visible_text
    page.close()
    bridge.shutdown(1000)


def test_missing_curriculum_keeps_upload_and_analysis_available_but_closes_diagnosis(
    qt_app, tmp_path
) -> None:
    facade = _StudentFacadeWithoutCurriculum(tmp_path)
    facade, bridge, page = _student_page(qt_app, tmp_path, facade)
    assert page.student_combo.count() == 1
    assert page.prepare_button.isEnabled()
    assert page.curriculum_status.isVisible()
    assert "材料仍可保存和分析" in page.curriculum_status.text()
    assert "诊断记录已安全关闭" in page.curriculum_status.text()

    facade._pages = [
        SimpleNamespace(
            file_id="question-file-internal-secret",
            role="question_pages",
            role_zh="题目页面",
            ordinal=1,
            page_number=1,
            label_zh="题目页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="a" * 64,
        ),
        SimpleNamespace(
            file_id="work-file-internal-secret",
            role="student_work_pages",
            role_zh="学生作答页面",
            ordinal=1,
            page_number=1,
            label_zh="学生作答页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="c" * 64,
        ),
    ]
    facade.review = facade._review(blocked=False, scored=True)
    candidate = facade.candidate_summary()
    facade.current = candidate
    page._render_submission(candidate)
    assert _wait_until(qt_app, lambda: len(page.review_editors) == 1)
    editor = page.review_editors[0]
    assert editor.record_score_button.isEnabled()
    assert not editor.record_diagnosis_button.isEnabled()
    assert "教材目录不可用" in editor.diagnosis_help.text()
    page.close()
    bridge.shutdown(1000)


def test_student_polling_stops_on_failure_without_automatic_retry(
    qt_app, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QDialog

    from integrations.deeptutor_shchem_v1.desktop_workbench.student_page import (
        AnalysisConfirmationDialog,
    )

    facade, bridge, page = _student_page(qt_app, tmp_path)
    facade._pages = [
        SimpleNamespace(
            file_id="question-file-internal-secret",
            role="question_pages",
            role_zh="题目页面",
            ordinal=1,
            page_number=1,
            label_zh="题目页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="a" * 64,
        ),
        SimpleNamespace(
            file_id="work-file-internal-secret",
            role="student_work_pages",
            role_zh="学生作答页面",
            ordinal=1,
            page_number=1,
            label_zh="学生作答页面 1",
            mime_type="image/png",
            width=1200,
            height=1600,
            sha256="c" * 64,
        ),
    ]
    ready = facade.ready_summary()
    active = facade.active_summary()
    failed = facade.failed_summary()
    facade.current = ready
    facade.start_result = active
    facade.poll_results = [failed]
    page._render_submission(ready)

    def accept_with_both(dialog: AnalysisConfirmationDialog):
        assert _wait_until(qt_app, lambda: dialog._ready)
        assert dialog.image_preview.has_image
        dialog.identifiers_clear.setChecked(True)
        dialog.egress_confirmed.setChecked(True)
        dialog.accept()
        assert _wait_until(qt_app, lambda: dialog.result() == QDialog.DialogCode.Accepted)
        return dialog.result()

    monkeypatch.setattr(AnalysisConfirmationDialog, "exec", accept_with_both)
    page.analyze_button.click()
    assert _wait_until(qt_app, lambda: len(facade.start_calls) == 1)
    assert _wait_until(
        qt_app,
        lambda: page._current_summary is active and page.poll_timer.isActive(),
    )
    page.poll_timer.stop()
    page._poll_submission()
    assert _wait_until(qt_app, lambda: page._current_summary is failed)
    _settle(qt_app)
    assert len(facade.confirmation_calls) == 1
    assert len(facade.start_calls) == 1
    assert len(facade.poll_calls) == 1
    assert not page.poll_timer.isActive()
    assert "重试" in page.analyze_button.text()
    assert "系统不会自动重试" in page.progress_message.text()
    page.close()
    bridge.shutdown(1000)

"""Production handoff entries stop before modifying pages after paper refusal."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QLabel, QLineEdit, QMainWindow,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from integrations.deeptutor_shchem_v1.desktop_workbench import main_window
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    ALL_ROUTES, EXTRA_ROUTES, ROUTE_ORDER, TeacherWorkbenchWindow,
)


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class _Harness(QMainWindow):
    # Exercise the production implementations without creating a real profile,
    # task worker, import renderer, or host Office process.
    navigate = TeacherWorkbenchWindow.navigate
    _prepare_paper_navigation = TeacherWorkbenchWindow._prepare_paper_navigation

    def __init__(self):
        super().__init__()
        self.effects, self.leave_calls = [], []
        self.allow_leave = False
        self.stack = QStackedWidget(self)
        self.pages = {route: QWidget(self.stack) for route in ALL_ROUTES}
        for page in self.pages.values():
            self.stack.addWidget(page)
        for route, page in self.pages.items():
            setattr(self, f"{route}_page" if route != "textbooks" else "textbook_page", page)
        self.nav_buttons = [QPushButton(route, self) for route in ROUTE_ORDER]
        self.studio_nav_buttons = {route: QPushButton(route, self) for route in EXTRA_ROUTES}
        for button in [*self.nav_buttons, *self.studio_nav_buttons.values()]:
            button.setCheckable(True)
        self.top_title = QLabel("组卷", self)
        self.stack.setCurrentWidget(self.paper_page)
        self.paper_page.prepare_leave = self._leave
        self.paper_page.update_basket_count = self._record("basket_count")
        self.preparation_page.studio_busy = lambda: False
        self.preparation_page._payload = lambda: {"topic": "synthetic"}
        self.preparation_page.topic = QLineEdit(self.preparation_page)
        self.preparation_page.recovery = SimpleNamespace(new_blank=self._record("new_blank"))
        for name in (
            "_open_draft", "_restore_summary", "apply_studio_template",
            "append_classroom_feedback", "import_word_reference", "import_word_cards",
            "import_handout_materials", "import_linked_question", "import_textbook_image",
            "import_textbook_cards", "import_textbook_images",
            "import_textbook_crops_for_composition", "import_library_image",
            "import_library_reference", "import_paper_reference",
            "open_textbook_section_materials",
        ):
            setattr(self.preparation_page, name, self._record(name))
        for name in ("reference_appended", "reference_rejected", "cards_appended", "cards_rejected"):
            setattr(self.textbook_page, name, self._record(name))
        self.textbook_page._snapshot = {}
        self.library_page.cards = ["synthetic existing card"]
        self.library_page._loading = False
        QVBoxLayout(self.library_page)
        self.library_page.scope = QComboBox(self.library_page)
        self.library_page.scope.addItem("Word", "word_native")
        self.library_page.advanced_button = QPushButton("original", self.library_page)
        self.library_page.query = QLineEdit("existing query", self.library_page)
        self.library_page.filters = {"original": True}
        self.library_page.curriculum = {"original": True}
        for name in ("search", "open_handout_materials", "invalidate_catalogs", "open_word_library"):
            setattr(self.library_page, name, self._record(name))
        self.home_page.update_editor = self._record("update_editor")
        self.home_page.refresh = self._record("home_refresh")
        self.mywork_page.refresh = self._record("mywork_refresh")
        self.my_work_page = self.mywork_page
        self.tasks = SimpleNamespace()
        self.facade = SimpleNamespace()
        self._paper_reference_loading = True

    def _record(self, name):
        def call(*args, **kwargs):
            self.effects.append((name, args, kwargs))
            return True
        return call

    def _leave(self):
        self.leave_calls.append(self.allow_leave)
        return self.allow_leave

    def _refresh_textbook_preparation_context(self):
        self.effects.append(("refresh_context", (), {}))


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    win = _Harness()

    class TemplateDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, key, owner, *, topic=""):
            self.topic = SimpleNamespace(text=lambda: topic)
            self.audience = SimpleNamespace(text=lambda: "synthetic class")
            self.deleted = False
            win.last_dialog = self

        def exec(self):
            return self.DialogCode.Accepted

        def deleteLater(self):
            self.deleted = True

    monkeypatch.setattr(main_window, "TemplatePreviewDialog", TemplateDialog)
    yield win
    win.close()
    win.deleteLater()
    app.processEvents()


_ENTRIES = [
    ("new_preparation", (), "new_blank"),
    ("_open_textbook_preparation", (), "_open_draft"),
    ("_new_textbook_preparation", (), "new_blank"),
    ("open_template", ("synthetic-template", "synthetic-topic"), "apply_studio_template"),
    ("open_work_record", ({"kind": "draft", "value": {"draft_id": "synthetic-draft"}},), "_open_draft"),
    ("open_work_record", ({"kind": "summary", "value": {"title": "synthetic-history"}},), "_restore_summary"),
    ("append_classroom_feedback", ("synthetic feedback",), "append_classroom_feedback"),
    ("_word_to_preparation", ({"materials": "synthetic"},), "import_word_reference"),
    ("_word_cards_to_preparation", ({"cards": []},), "import_word_cards"),
    ("_open_handout_materials", (), "open_handout_materials"),
    ("_handout_materials_to_preparation", ({"synthetic": True},), "import_handout_materials"),
    ("_linked_question_to_preparation", ({"synthetic": True},), "import_linked_question"),
    ("_textbook_section_materials", ({"synthetic": True},), "open_textbook_section_materials"),
    ("_textbook_to_preparation", ({"synthetic": True},), "import_word_reference"),
    ("_textbook_image_to_preparation", ({"synthetic": True},), "import_textbook_image"),
    ("_textbook_cards_to_preparation", ({"synthetic": True},), "import_textbook_cards"),
    ("_textbook_images_to_preparation", ([{"synthetic": True}],), "import_textbook_images"),
    ("_textbook_crops_to_composition", ([{"synthetic": True}],), "import_textbook_crops_for_composition"),
    ("_library_image_to_preparation", ({"synthetic": True},), "import_library_image"),
    ("_library_reference_to_preparation", ({"synthetic": True},), "import_library_reference"),
    ("_paper_reference_ready", ({"synthetic": True},), "import_paper_reference"),
]


@pytest.mark.parametrize("name,args,effect", _ENTRIES)
def test_refused_paper_handoff_has_no_destination_side_effects(window, name, args, effect):
    result = getattr(TeacherWorkbenchWindow, name)(window, *args)
    assert not result
    assert window.leave_calls == [False]
    assert window.effects == []
    assert window.stack.currentWidget() is window.paper_page
    if name == "open_template":
        assert window.last_dialog.deleted
    if name == "_paper_reference_ready":
        assert not window._paper_reference_loading


@pytest.mark.parametrize("name,args,effect", _ENTRIES)
@pytest.mark.parametrize("start_route", ["paper", "library"])
def test_allowed_paper_and_existing_nonpaper_handoffs_continue(window, name, args, effect, start_route):
    window.allow_leave = start_route == "paper"
    window.stack.setCurrentWidget(window.pages[start_route])
    getattr(TeacherWorkbenchWindow, name)(window, *args)
    assert [row[0] for row in window.effects].count(effect) == 1
    if start_route == "library":
        assert window.leave_calls == []
    else:
        assert window.leave_calls and all(window.leave_calls)


def test_navigate_reports_refusal_and_keeps_paper_selected(window):
    window.nav_buttons[ROUTE_ORDER.index("preparation")].setChecked(True)
    assert window.navigate("preparation") is False
    assert window.stack.currentWidget() is window.paper_page
    assert window.nav_buttons[ROUTE_ORDER.index("paper")].isChecked()
    assert window.top_title.text() == "组卷"
    assert window.effects == []
    assert window.navigate("missing-route") is False
    assert window.leave_calls == [False]
    assert window.navigate("paper") is True
    assert window.leave_calls == [False]


def test_refused_exam_practice_keeps_library_filters_and_context_untouched(window):
    request = {"lane": "word_native", "knowledge": "synthetic-knowledge", "label": "synthetic",
               "exam_id": "synthetic-exam", "task_id": "synthetic-task"}
    TeacherWorkbenchWindow.open_exam_practice(window, request)
    assert not hasattr(window.library_page, "exam_context")
    assert window.library_page.filters == {"original": True}
    assert window.library_page.curriculum == {"original": True}
    assert window.library_page.query.text() == "existing query"
    assert window.library_page.advanced_button.text() == "original"
    assert window.effects == []
    assert window.stack.currentWidget() is window.paper_page


def test_nonpaper_exam_practice_retains_existing_handoff(window):
    window.stack.setCurrentWidget(window.preparation_page)
    request = {"lane": "word_native", "knowledge": "synthetic-knowledge", "label": "synthetic",
               "exam_id": "synthetic-exam", "task_id": "synthetic-task"}
    TeacherWorkbenchWindow.open_exam_practice(window, request)
    assert window.library_page.filters == {"knowledge": {"synthetic-knowledge"}}
    assert window.library_page.query.text() == ""
    assert window.stack.currentWidget() is window.library_page
    assert [row[0] for row in window.effects] == ["search"]
    assert window.leave_calls == []


@pytest.mark.parametrize("start_route", ["paper", "library"])
def test_exam_dialog_append_checks_paper_before_import(window, monkeypatch, start_route):
    from integrations.deeptutor_shchem_v1.desktop_workbench import exam_dashboard
    window.stack.setCurrentWidget(window.pages[start_route])

    class ExamDialog:
        def __init__(self, *args):
            self.preparation_requested = _Signal()
            self.status = QLabel()
            self.pending_handoff = None

        def exec(self):
            self.preparation_requested.emit("synthetic exam feedback")

        def deleteLater(self):
            self.status.deleteLater()

    monkeypatch.setattr(exam_dashboard, "ExamDashboard", ExamDialog)
    TeacherWorkbenchWindow.open_exam_dialog(window)
    assert [row[0] for row in window.effects] == ([] if start_route == "paper" else ["import_word_reference"])
    assert window.stack.currentWidget() is window.pages[start_route]


@pytest.mark.parametrize("mode", ["reference", "textbooks", "library", "cancel"])
@pytest.mark.parametrize("start_route", ["paper", "library"])
def test_import_dialog_checks_before_cache_and_handoff_changes(window, monkeypatch, mode, start_route):
    window.stack.setCurrentWidget(window.pages[start_route])

    class ImportDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, *args):
            self.basket_changed = _Signal()
            self.preparation_reference = {"synthetic": True} if mode == "reference" else None
            self.open_textbooks_requested = mode == "textbooks"
            self.open_word_library_requested = mode == "library"
            self.deleted = False
            window.last_dialog = self

        def exec(self):
            return self.DialogCode.Rejected if mode == "cancel" else self.DialogCode.Accepted

        def deleteLater(self):
            self.deleted = True

    monkeypatch.setattr(main_window, "ImportDialog", ImportDialog)
    TeacherWorkbenchWindow.open_import(window)
    assert window.last_dialog.deleted
    effects = [row[0] for row in window.effects]
    if start_route == "paper":
        assert effects == []
        assert window.stack.currentWidget() is window.paper_page
        assert window.leave_calls == [False]
    else:
        expected = {
            "reference": ["invalidate_catalogs", "import_word_reference"],
            "textbooks": ["invalidate_catalogs", "refresh_context"],
            "library": ["invalidate_catalogs", "open_word_library"],
            "cancel": ["invalidate_catalogs"],
        }
        assert effects == expected[mode]
        destination = {"reference": "preparation", "textbooks": "textbooks"}.get(mode, "library")
        assert window.stack.currentWidget() is window.pages[destination]
        assert window.leave_calls == []


@pytest.mark.parametrize("start_route", ["paper", "library"])
def test_personal_template_checks_paper_before_applying(window, monkeypatch, start_route):
    from integrations.deeptutor_shchem_v1.desktop_workbench import teaching_template_dialogs
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_templates import TemplatePage
    window.stack.setCurrentWidget(window.pages[start_route])
    window.preparation_page.apply_personal_teaching_template = window._record("apply_personal_template")
    outcomes = []

    class TemplateLibraryDialog:
        def __init__(self, facade, parent, *, apply_callback):
            self.apply_callback = apply_callback

        def exec(self):
            outcomes.append(self.apply_callback({"title": "synthetic personal template"}))

        def deleteLater(self):
            pass

    monkeypatch.setattr(teaching_template_dialogs, "TeachingTemplateLibraryDialog", TemplateLibraryDialog)
    owner = SimpleNamespace(window=lambda: window, facade=window.facade)
    TemplatePage.open_my_templates(owner)
    assert outcomes == [start_route != "paper"]
    assert [row[0] for row in window.effects] == ([] if start_route == "paper" else ["apply_personal_template"])
    assert window.stack.currentWidget() is window.pages["paper" if start_route == "paper" else "preparation"]


@pytest.mark.parametrize("start_route", ["paper", "library"])
def test_help_dialog_stays_open_when_navigation_is_refused(window, start_route):
    from integrations.deeptutor_shchem_v1.desktop_workbench.teacher_help import TeacherHelpDialog
    window.stack.setCurrentWidget(window.pages[start_route])
    accepted = []
    owner = SimpleNamespace(owner=window, current_scenario=SimpleNamespace(route="preparation"),
                            status=QLabel(), accept=lambda: accepted.append(True))
    TeacherHelpDialog.open_workspace(owner)
    assert accepted == ([] if start_route == "paper" else [True])
    assert window.stack.currentWidget() is window.pages["paper" if start_route == "paper" else "preparation"]


@pytest.mark.parametrize("refuse_after_picker", [False, True])
def test_section_picker_rechecks_paper_before_batch_and_releases_job(window, monkeypatch, refuse_after_picker):
    from test_forward_section_entry_ui import Page
    from integrations.deeptutor_shchem_v1 import desktop_section_materials as backend
    from integrations.deeptutor_shchem_v1.desktop_workbench import section_materials_dialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_preparation import StudioPreparationPage
    page = Page()
    page.open_textbook_section_materials = StudioPreparationPage.open_textbook_section_materials.__get__(page)
    batches = []
    page._import_material_batch = lambda *args, **kwargs: batches.append((args, kwargs)) or True
    window.preparation_page = page
    window.stack.setCurrentWidget(window.library_page)
    selection = {"book_key": "synthetic-book", "section_filter_key": "synthetic-section",
                 "selection": {"mode": "registered", "section_key": "synthetic-section", "revision": "synthetic"}}
    context = {"synthetic": True}
    monkeypatch.setattr(backend, "textbook_section_material_context", lambda *args: deepcopy(context))
    dialogs = []

    class Picker:
        requests = [{"kind": "knowledge", "context": context}]

        def __init__(self, *args, **kwargs):
            self.deleted = False
            dialogs.append(self)

        def exec(self):
            if refuse_after_picker:
                window.stack.setCurrentWidget(window.paper_page)
            return QDialog.DialogCode.Accepted

        def deleteLater(self):
            self.deleted = True

    monkeypatch.setattr(section_materials_dialog, "SectionMaterialsDialog", Picker)
    try:
        assert TeacherWorkbenchWindow._textbook_section_materials(window, selection)
        assert page.studio_busy()
        page.tasks.finish()
        assert not page.studio_busy()
        assert dialogs[0].deleted
        if refuse_after_picker:
            assert batches == []
            assert page._source_material_insertion is None
            assert window.stack.currentWidget() is window.paper_page
            assert window.leave_calls == [False]
        else:
            assert len(batches) == 1
            assert callable(batches[0][1]["before_handoff"])
            assert page._source_material_insertion == {"epoch": 0}
            assert window.stack.currentWidget() is window.pages["preparation"]
    finally:
        page.deleteLater()


@pytest.mark.parametrize("late_change", ["paper_pending", "teacher_edit"])
def test_published_batch_refusal_uses_existing_discard_and_cleanup(window, monkeypatch, tmp_path, late_change):
    from test_pending_material_edit import DeferredTasks, SafeDesignDialog, SafeStudioPage
    from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource
    from integrations.deeptutor_shchem_v1.desktop_workbench import lesson_design_dialog
    monkeypatch.setattr(lesson_design_dialog, "LessonDesignDialog", SafeDesignDialog)
    tasks = DeferredTasks()
    page = SafeStudioPage(SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path)), tasks, [])
    window.stack.setCurrentWidget(window.library_page)
    before = page._payload()
    source_root, image_root = tmp_path / "sources", tmp_path / "images"
    source_root.mkdir()
    image_root.mkdir()
    existing_source, existing_image = source_root / "keep.txt", image_root / "keep.image"
    existing_source.write_text("existing source", encoding="utf-8")
    existing_image.write_bytes(b"existing image")
    owned_source, owned_image = source_root / "batch.txt", image_root / "batch.image"
    result = {"resources": [resource("personal_text", "synthetic material",
        [block("text", "personal", "synthetic source text")],
        {"source_path": "synthetic", "source_sha256": "unknown", "source_revision": "unknown"})],
        "image_assets": []}

    class Publication:
        publishes = accepts = discards = 0

        def publish(self, guard):
            guard()
            self.publishes += 1
            owned_source.write_text("temporary source", encoding="utf-8")
            owned_image.write_bytes(b"temporary image")
            return deepcopy(result)

        def accept(self):
            self.accepts += 1

        def discard(self):
            self.discards += 1
            owned_source.unlink(missing_ok=True)
            owned_image.unlink(missing_ok=True)

    publication = Publication()
    try:
        assert page._import_material_batch({}, lambda *args: publication, "synthetic batch",
            before_handoff=lambda: window._prepare_paper_navigation("preparation"))
        dialog = page._material_handoff_dialog
        history = dialog.history
        original_history = deepcopy((history.value, history.back, history.forward))
        identity = dialog._task
        published = tasks.calculate(identity)
        assert owned_source.exists() and owned_image.exists()
        window.stack.setCurrentWidget(window.paper_page)
        if late_change == "teacher_edit":
            page.topic.setText("teacher's later edit")
        tasks.deliver(identity, published)
        assert publication.publishes == 1 and publication.accepts == 0
        assert publication.discards >= 1
        assert not owned_source.exists() and not owned_image.exists()
        assert existing_source.read_text(encoding="utf-8") == "existing source"
        assert existing_image.read_bytes() == b"existing image"
        assert (history.value, history.back, history.forward) == original_history
        assert page._material_handoff_dialog is None and not page.studio_busy()
        assert page.last_material_handoff["status"] == "not_added"
        if late_change == "paper_pending":
            assert page._payload() == before
            assert window.leave_calls == [False]
        else:
            after = page._payload()
            assert after.pop("topic") == "teacher's later edit"
            baseline = deepcopy(before)
            baseline.pop("topic")
            assert after == baseline
            assert window.leave_calls == []
        assert window.stack.currentWidget() is window.paper_page
    finally:
        if page._material_handoff_dialog is not None:
            QDialog.reject(page._material_handoff_dialog)
        publication.discard()
        page.deleteLater()

"""Synthetic-only tests of the personal-image whole-theme basket entry button."""
from copy import deepcopy

from PySide6.QtCore import Qt
import pytest
from test_personal_visual_questions_ui import (
    _Facade,  # noqa: F401
    _ImmediateTasks,
    _item,
)
from test_personal_visual_questions_ui import (
    qt_app as qt_app,
)

from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
    PersonalVisualQuestionDialog,
)


class BasketFacade(_Facade):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.basket_calls = []
        self.basket_result = 2
        self.basket_error = None

    def add_personal_visual_questions_to_basket(self, selections):
        self.basket_calls.append(deepcopy(selections))
        if self.basket_error:
            raise RuntimeError(self.basket_error)
        return self.basket_result


class DeferredBasketTasks(_ImmediateTasks):
    def __init__(self):
        super().__init__()
        self.pending = []

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        if label != "图片题整主题加入统一题篮":
            return super().submit(label, operation, on_success=on_success, on_failure=on_failure)
        self.serial += 1
        self.pending.append((operation, on_success, on_failure))
        return f"basket-{self.serial}"

    def finish(self):
        operation, success, failure = self.pending.pop(0)
        try:
            result = operation()
        except Exception as exc:  # Synthetic service error path.
            failure(str(exc))
        else:
            success(result)


def select(dialog, key):
    row = _item(dialog, key)
    dialog.question_list.setCurrentItem(row)
    row.setCheckState(Qt.CheckState.Checked)
    assert row.checkState() == Qt.CheckState.Checked


@pytest.mark.parametrize("capability", ["available", "missing", "not_callable"])
def test_whole_theme_button_is_visible_only_with_callable_facade(qt_app, capability):
    facade = BasketFacade() if capability == "available" else _Facade()
    if capability == "not_callable":
        facade.add_personal_visual_questions_to_basket = None
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    dialog.show()
    try:
        assert dialog.add_basket_button.isVisible() is (capability == "available")
        assert "完整主题" in dialog.add_basket_button.accessibleName()
        assert "全部小问和公共材料" in dialog.add_basket_button.toolTip()
    finally:
        dialog.reject()


def test_empty_selection_disables_entry_and_is_a_safe_noop(qt_app):
    facade = BasketFacade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    try:
        assert not dialog.selections
        assert "已选 0 题" in dialog.selection_count.text()
        assert not dialog.add_basket_button.isEnabled()
        status = dialog.status.text()
        dialog.add_basket_button.click()
        dialog._add_to_basket()
        assert facade.basket_calls == []
        assert facade.saved == [] and facade.reference_calls == []
        assert dialog.status.text() == status
    finally:
        dialog.reject()


def test_explicit_click_transmits_exact_batch_key_revision_identities(qt_app):
    facade = BasketFacade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    try:
        select(dialog, "q-a")
        select(dialog, "q-b")
        assert facade.basket_calls == []  # Checking is not a basket write.
        assert dialog.add_basket_button.isEnabled()
        expected = deepcopy(dialog.selections)
        dialog.add_basket_button.click()
        assert facade.basket_calls == [expected]
        assert [(row["batch_id"], row["key"], row["revision"]) for row in expected] == [
            ("batch-a", "q-a", "rev-a"), ("batch-b", "q-b", "rev-b"),
        ]
        assert all(set(row) == {"batch_id", "key", "revision", "points"} for row in expected)
        assert dialog.selections == expected
        assert "已按完整主题加入" in dialog.status.text()
        assert "题篮现有 2 项" in dialog.status.text()
        assert "已有主题不会重复加入" in dialog.status.text()
        assert "实际分页" in dialog.status.text()
        assert facade.saved == [] and facade.reference_calls == []
    finally:
        dialog.reject()


def test_hidden_selected_row_still_keeps_original_identity(qt_app):
    facade = BasketFacade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    try:
        select(dialog, "q-a")
        dialog.batch_combo.setCurrentIndex(dialog.batch_combo.findData("batch-b"))
        assert dialog.question_list.count() == 1
        select(dialog, "q-b")
        dialog.add_basket_button.click()
        assert {row["key"] for row in facade.basket_calls[0]} == {"q-a", "q-b"}
    finally:
        dialog.reject()


def test_call_failure_preserves_selection_and_shows_safe_retry_message(qt_app):
    facade = BasketFacade()
    facade.basket_error = "来源版本已变化，请刷新并重新核对后加入题篮。"
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    try:
        select(dialog, "q-a")
        expected = deepcopy(dialog.selections)
        dialog.add_basket_button.click()
        assert facade.basket_calls == [expected]
        assert dialog.selections == expected and not dialog._save_busy
        assert facade.basket_error in dialog.status.text()
        assert "已加入" not in dialog.status.text()
        assert dialog.add_basket_button.isEnabled()
        assert not dialog.saved and facade.saved == []
        facade.basket_error = None
        dialog.add_basket_button.click()
        assert len(facade.basket_calls) == 2
        assert "已按完整主题加入" in dialog.status.text()
    finally:
        dialog.reject()


def test_busy_add_is_single_flight_and_payload_is_frozen(qt_app):
    facade, tasks = BasketFacade(), DeferredBasketTasks()
    dialog = PersonalVisualQuestionDialog(facade, tasks)
    try:
        select(dialog, "q-a")
        expected = deepcopy(dialog.selections)
        dialog.add_basket_button.click()
        assert dialog._save_busy and not dialog.add_basket_button.isEnabled()
        dialog._add_to_basket()
        assert len(tasks.pending) == 1
        _item(dialog, "q-a").setCheckState(Qt.CheckState.Unchecked)
        assert not dialog.selections
        tasks.finish()
        assert facade.basket_calls == [expected]
        assert not dialog._save_busy
        assert not dialog.add_basket_button.isEnabled()
    finally:
        dialog.reject()


def test_required_image_failure_cannot_check_or_add_a_theme(qt_app):
    facade = BasketFacade(image_failure=True)
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    try:
        row = _item(dialog, "q-a")
        row.setCheckState(Qt.CheckState.Checked)
        assert row.checkState() == Qt.CheckState.Unchecked
        assert "必需原图均已显示" in dialog.status.text()
        assert not dialog.add_basket_button.isEnabled()
        dialog._add_to_basket()
        assert facade.basket_calls == []
    finally:
        dialog.reject()


def test_closed_dialog_does_not_apply_late_success_ui_state(qt_app):
    facade, tasks = BasketFacade(), DeferredBasketTasks()
    dialog = PersonalVisualQuestionDialog(facade, tasks)
    select(dialog, "q-a")
    dialog.add_basket_button.click()
    dialog.reject()
    status = dialog.status.text()
    tasks.finish()
    assert dialog._closed and dialog.status.text() == status
    before = len(facade.basket_calls)
    dialog._add_to_basket()
    assert len(facade.basket_calls) == before

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from test_desktop_ui import _Facade, _fill_preparation_page

from integrations.deeptutor_shchem_v1.desktop_blueprint_drafts import (
    BlueprintDraftError,
)
from integrations.deeptutor_shchem_v1.desktop_paper_preparation import (
    paper_preparation_reference,
)
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    TeacherWorkbenchWindow,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
    ComposerQuestion,
    ComposerTheme,
    PaperComposerModel,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_preparation_dialog import (
    PaperPreparationDialog,
)


def snapshot():
    return {
        "title": "教师编排名称",
        "themes": [
            {
                "title": "后移的大题乙",
                "source": "2025·上海·来源待核验",
                "chapter": "电解质的电离",
                "shared_text": "乙共享材料",
                "shared_materials": [
                    {"text": "图旁文字", "path": "C:/private/crop.png"}
                ],
                "questions": [
                    {
                        "stem": "教师改写后的题面乙",
                        "answer": "教师答案乙",
                        "analysis": "教师解析乙",
                        "options": ["A．选项甲", "B．选项乙"],
                        "source_question_number": "(2)",
                        "source_ref": {"path": "C:/private/source.pdf"},
                        "source_detail": {
                            "reference_answer": "来源答案乙",
                            "answer_boundary": "非官方，未独立核验",
                            "analysis": ["候选思路乙"],
                        },
                    }
                ],
            },
            {
                "title": "大题甲",
                "questions": [
                    {
                        "stem": "题目图片待展开（当前仅显示摘要）：甲",
                        "crop_available": True,
                    }
                ],
            },
        ],
    }


def test_reference_preserves_selection_order_current_edits_and_sources():
    value = snapshot()
    value["themes"][0]["source_detail"] = {
        "source_fields": [{"label": "年份", "value": "2025"}],
        "page": "第2页",
    }
    before = deepcopy(value)
    result = paper_preparation_reference(value, [1, 0])
    text = result["materials"]
    assert text.index("后移的大题乙") < text.index("大题甲")
    assert "教师改写后的题面乙" in text and "乙共享材料" in text
    assert "来源答案乙" in text and "非官方，未独立核验" in text
    assert "模型候选解路" in text and "候选思路乙" in text
    assert "(2)" in text and "B．选项乙" in text
    assert "C:/private" not in text
    assert "年份：2025" in text and "原卷页码：第2页" in text
    assert result["theme_count"] == 2 and result["question_count"] == 2
    assert result["warnings"] and "图片未导入" in text
    assert value == before
    assert paper_preparation_reference(value, [0])["theme_count"] == 1
    assert "大题甲" not in paper_preparation_reference(value, [0])["materials"]


def test_no_answers_means_no_current_or_source_answers_and_analysis():
    text = paper_preparation_reference(snapshot(), [0], include_answers=False)[
        "materials"
    ]
    for answer in ("教师答案乙", "来源答案乙", "教师解析乙", "候选思路乙"):
        assert answer not in text


@pytest.mark.parametrize("indices", [[], [True], [-1], [2]])
def test_invalid_selection_is_not_silently_imported(indices):
    with pytest.raises(BlueprintDraftError):
        paper_preparation_reference(snapshot(), indices)


def test_oversize_fails_without_truncating():
    value = snapshot()
    value["themes"][0]["shared_text"] = "正文" * 11000
    with pytest.raises(BlueprintDraftError, match="20000"):
        paper_preparation_reference(value, [0])


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("width", [420, 900])
def test_dialog_selection_answers_and_read_only_snapshot(app, width):
    value = snapshot()
    dialog = PaperPreparationDialog(value)
    dialog.resize(width, 760)
    dialog.show()
    app.processEvents()
    assert dialog.preview.isReadOnly()
    assert dialog.preview.horizontalScrollBar().maximum() == 0
    assert dialog.import_button.isEnabled()
    value["themes"][0]["questions"][0]["stem"] = "后来的修改"
    dialog.include_answers.setChecked(False)
    assert "后来的修改" not in dialog.preview.toPlainText()
    assert "教师答案乙" not in dialog.preview.toPlainText()
    dialog.themes.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert "教师改写后的题面乙" not in dialog.preview.toPlainText()
    dialog.themes.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert not dialog.import_button.isEnabled() and dialog.reference is None
    dialog.close()


@pytest.mark.parametrize("accept", [False, True])
def test_actual_window_route_preserves_form_and_current_paper(app, monkeypatch, accept):
    import integrations.deeptutor_shchem_v1.desktop_workbench.paper_preparation_dialog as module

    observed = []

    class Choice:
        DialogCode = QDialog.DialogCode

        def __init__(self, snapshot, *_args):
            observed.append(deepcopy(snapshot))
            self.reference = paper_preparation_reference(snapshot, [0])

        def exec(self):
            return self.DialogCode.Accepted if accept else self.DialogCode.Rejected

    monkeypatch.setattr(module, "PaperPreparationDialog", Choice)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    facade = _Facade()
    window = TeacherWorkbenchWindow(facade)
    page = window.preparation_page
    page._availability_timer.stop()
    _fill_preparation_page(page)
    original_form = page._payload()
    paper = window.paper_page._composer
    paper._catalog_loading = False
    paper.model.themes = [
        ComposerTheme(
            key="edited",
            title="当前大题",
            shared_text="当前共享材料",
            questions=[
                ComposerQuestion(
                    key="changed",
                    response_type="简答",
                    stem="只保留这一修改题面",
                    answer="本地修改答案",
                )
            ],
        )
    ]
    paper.title.setText("尚未失焦的名称")
    before = deepcopy(paper.model.draft_payload())
    window.navigate("home")
    paper.preparation_button.click()
    assert observed[0]["title"] == "尚未失焦的名称"
    assert "只保留这一修改题面" in observed[0]["themes"][0]["questions"][0]["stem"]
    assert paper.model.draft_payload() == before
    after = page._payload()
    for key in original_form:
        if key != "materials":
            assert after[key] == original_form[key]
    if accept:
        assert window.stack.currentWidget() == page
        assert after["materials"].startswith(original_form["materials"])
        assert "只保留这一修改题面" in _prompt(normalize_preparation_payload(after))
        paper.preparation_button.click()
        assert page._payload() == after  # duplicate is not appended
    else:
        assert after == original_form
        assert window.stack.currentWidget() == window.home_page
    assert facade.saved_preparation_payloads == []
    window.close()
    window.tasks.shutdown()


def test_empty_and_loading_paper_do_not_emit(app):
    window = TeacherWorkbenchWindow(_Facade())
    window.preparation_page._availability_timer.stop()
    paper = window.paper_page._composer
    seen = []
    paper.preparation_requested.connect(seen.append)
    paper.model = PaperComposerModel.from_basket([])
    paper.preparation_button.click()
    assert not seen and "先从题库" in paper.preview_state.text()
    paper._catalog_loading = True
    paper.preparation_button.click()
    assert not seen and "读取" in paper.preview_state.text()
    window.close()
    window.tasks.shutdown()


def test_reference_loading_is_async_and_failure_keeps_form(app, monkeypatch):
    facade = _Facade()
    calls = []
    facade.preparation_paper_source_snapshot = lambda value: (
        calls.append(value) or value
    )
    window = TeacherWorkbenchWindow(facade)
    page = window.preparation_page
    page._availability_timer.stop()
    _fill_preparation_page(page)
    before = page._payload()
    tasks = []
    monkeypatch.setattr(
        window.tasks,
        "submit",
        lambda label, operation, **callbacks: (
            tasks.append((operation, callbacks)) or "local-read"
        ),
    )
    window._paper_to_preparation(snapshot())
    window._paper_to_preparation(snapshot())
    assert len(tasks) == 1 and not calls
    assert window._paper_reference_loading
    tasks[0][1]["on_failure"]("raw-private-diagnostic")
    assert not window._paper_reference_loading and page._payload() == before
    assert "raw-private" not in window.statusBar().currentMessage()
    window.close()
    window.tasks.shutdown()


@pytest.mark.parametrize(
    "busy_field",
    ["_save_task_id", "_generation_qt_task_id", "_active_preparation_task_id"],
)
def test_busy_preparation_preserves_form(app, monkeypatch, busy_field):
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    window = TeacherWorkbenchWindow(_Facade())
    page = window.preparation_page
    page._availability_timer.stop()
    _fill_preparation_page(page)
    before = page._payload()
    setattr(page, busy_field, "active-task")
    assert not page.import_paper_reference(snapshot())
    assert page._payload() == before
    setattr(page, busy_field, None)
    window.close()
    window.tasks.shutdown()

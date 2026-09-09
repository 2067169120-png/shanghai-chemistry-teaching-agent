from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QMessageBox
from test_word_question_dialog import _Tasks

from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


@pytest.fixture
def qt_app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    return QApplication.instance() or QApplication([])


def asset(number):
    sha = f"{number:064x}"
    return {
        "asset_id": "IMG-" + sha,
        "sha256": sha,
        "caption": f"题图 {number}",
        "source": "演示讲义 · 例1",
        "purpose": "课堂观察",
        "width": 100,
        "height": 80,
        "content_type": "image/png",
    }


def reference(*images, include_images=True, issues=()):
    return {
        "materials": "所选完整 Word 题面与答案",
        "warnings": ["教师核对题目边界"],
        "selections": [{"key": "q1", "revision": "r1", "points": 2}],
        "include_images": include_images,
        "image_assets": list(images),
        "image_issues": list(issues),
    }


class Facade:
    def __init__(self):
        self.calls = []
        self.reads = 0
        self.fail_second = False
        self.fault = None

    def import_word_question_reference(self, selected, existing):
        self.calls.append(deepcopy((selected, existing)))
        result = deepcopy(existing)
        seen = {item["sha256"] for item in result}
        for item in selected["image_assets"]:
            self.reads += 1
            if self.fail_second and self.reads == 2:
                raise RuntimeError("private-source-diagnostic")
            if item["sha256"] not in seen:
                result.append(deepcopy(item))
                seen.add(item["sha256"])
        value = {
            "materials": selected["materials"],
            "warnings": list(selected["warnings"]),
            "image_assets": result,
        }
        if self.fault == "missing_image":
            value["image_assets"] = result[:-1]
        elif self.fault == "source_changed":
            value["materials"] += "来源已变"
        elif self.fault == "warning_lost":
            value["warnings"] = []
        return value


class PageTasks(_Tasks):
    task_cancelled = Signal(str, str)


def page_for(existing=()):
    facade, tasks = Facade(), PageTasks()
    page = PreparationPage(facade, tasks)
    page._availability_timer.stop()
    page.topic.setText("原课题")
    page.audience.setText("高二")
    page.objective.setPlainText("原目标")
    page.materials.setPlainText("原资料  \n不截断")
    page.image_assets_widget.set_assets_strict(list(existing))
    return page, facade, tasks


def test_batch_handoff_waits_then_commits_text_and_all_images(qt_app):
    page, facade, tasks = page_for([asset(1)])
    before = deepcopy(page._payload())
    observed = []
    page.image_assets_widget.assets_changed.connect(
        lambda _: observed.append(page._payload())
    )
    selected = reference(asset(2), asset(3))
    assert page.import_word_reference(selected)
    assert not page.isEnabled() and page._payload() == before
    assert "正在" in page.status.text() and not facade.calls
    tasks.finish("导入 Word 选题图文")
    after = page._payload()
    assert page.isEnabled() and page._library_image_task_id is None
    assert after["image_assets"] == [asset(1), asset(2), asset(3)]
    assert after["materials"].startswith(
        before["materials"] + "\n\n" + selected["materials"]
    )
    assert "教师核对题目边界" in after["materials"]
    for key in before.keys() - {"materials", "image_assets"}:
        assert after[key] == before[key]
    assert observed == [after]
    assert "是否交给 AI 读图以“图片用法”为准" in page.status.text()
    page.close()


@pytest.mark.parametrize(
    "fault", ["second_read", "missing_image", "source_changed", "warning_lost"]
)
def test_failed_batch_keeps_all_original_fields_and_images(qt_app, fault):
    page, facade, tasks = page_for([asset(1)])
    before = deepcopy(page._payload())
    facade.fail_second = fault == "second_read"
    facade.fault = fault
    assert page.import_word_reference(reference(asset(2), asset(3)))
    tasks.finish("导入 Word 选题图文")
    assert page.isEnabled() and page._library_image_task_id is None
    assert page._payload() == before
    assert "未能完整导入" in page.status.text()
    assert "private-source" not in page.status.text()
    page.close()


def test_capacity_rejected_before_background_reads_without_partial_text(qt_app):
    page, facade, tasks = page_for([asset(n) for n in range(11)])
    before = deepcopy(page._payload())
    assert not page.import_word_reference(reference(asset(20), asset(21)))
    assert page._payload() == before and not tasks.pending and not facade.calls
    assert "已有 11 张" in page.status.text()
    page.close()


def test_duplicate_sha_keeps_existing_metadata_and_uses_no_extra_slot(qt_app):
    existing = [asset(n) for n in range(11)]
    page, _, tasks = page_for(existing)
    repeated = {**asset(1), "caption": "同图另一道题"}
    assert page.import_word_reference(reference(repeated, asset(12)))
    tasks.finish("导入 Word 选题图文")
    assert page.image_assets_widget.assets() == existing + [asset(12)]
    page.close()


@pytest.mark.parametrize(
    "fault",
    [
        "unsupported",
        "issues",
        "missing_fields",
        "extra_private",
        "text_with_images",
        "bad_selection",
    ],
)
def test_malformed_or_unresolved_new_contract_never_falls_back_to_text(qt_app, fault):
    page, _, tasks = page_for()
    selected = reference(asset(1))
    if fault == "unsupported":
        selected["image_assets"][0]["content_type"] = "image/gif"
    elif fault == "issues":
        selected["image_issues"] = ["原图不可解码"]
    elif fault == "missing_fields":
        selected.pop("include_images")
    elif fault == "extra_private":
        selected["image_assets"][0]["path"] = "private-local-path"
    elif fault == "text_with_images":
        selected["include_images"] = False
    else:
        selected["selections"] = [{}]
    before = deepcopy(page._payload())
    assert not page.import_word_reference(selected)
    assert page._payload() == before and not tasks.pending
    page.close()


def test_explicit_text_only_rechecks_source_and_preserves_existing_images(qt_app):
    page, facade, tasks = page_for([asset(1)])
    selected = reference(include_images=False, issues=["原图未带入"])
    selected["warnings"].append("原图未带入")
    assert page.import_word_reference(selected)
    assert not facade.calls
    tasks.finish("导入 Word 选题图文")
    assert facade.calls[0][0]["include_images"] is False
    assert page.image_assets_widget.assets() == [asset(1)]
    assert "原图未带入" in page.materials.toPlainText()
    page.close()


def test_old_text_reference_remains_synchronous(qt_app):
    page, facade, tasks = page_for()
    assert page.import_word_reference({"materials": "旧 Word 资料", "warnings": []})
    assert "旧 Word 资料" in page.materials.toPlainText()
    assert not tasks.pending and not facade.calls
    page.close()


def test_closed_page_does_not_apply_late_batch(qt_app):
    page, _, tasks = page_for([asset(1)])
    before = deepcopy(page._payload())
    assert page.import_word_reference(reference(asset(2)))
    page.close()
    assert page.isEnabled() and page._library_image_task_id is None
    tasks.finish("导入 Word 选题图文", allow_cancelled=True)
    assert page._payload() == before
    page.show()
    assert page.import_word_reference(reference(asset(2)))
    tasks.finish("导入 Word 选题图文")
    assert page.image_assets_widget.assets() == [asset(1), asset(2)]
    page.close()


def test_external_form_change_before_reply_is_not_overwritten(qt_app):
    page, _, tasks = page_for()
    assert page.import_word_reference(reference(asset(1)))
    page.topic.setText("后来修改的课题")
    before = deepcopy(page._payload())
    tasks.finish("导入 Word 选题图文")
    assert page.isEnabled() and page._payload() == before
    page.close()


def test_material_limit_rejects_before_reading_any_image(qt_app):
    page, _, tasks = page_for()
    page.materials.setPlainText("原" * 20000)
    before = deepcopy(page._payload())
    assert not page.import_word_reference(reference(asset(1)))
    assert page._payload() == before and not tasks.pending
    page.close()


def test_submit_failure_restores_form(qt_app):
    page, _, _ = page_for()
    page.tasks = SimpleNamespace(
        submit=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError())
    )
    before = deepcopy(page._payload())
    assert not page.import_word_reference(reference(asset(1)))
    assert page.isEnabled() and page._payload() == before
    page.close()


@pytest.mark.parametrize("points", [1.5, True, float("nan"), float("inf"), 10**400])
def test_selection_points_follow_finite_positive_backend_contract(qt_app, points):
    page, _, tasks = page_for()
    selected = reference(asset(1))
    selected["selections"][0]["points"] = points
    before = deepcopy(page._payload())
    accepted = page.import_word_reference(selected)
    if type(points) is float and points == 1.5:
        assert accepted
        tasks.finish("导入 Word 选题图文")
        assert page.image_assets_widget.assets() == [asset(1)]
    else:
        assert not accepted and page._payload() == before and not tasks.pending
    page.close()

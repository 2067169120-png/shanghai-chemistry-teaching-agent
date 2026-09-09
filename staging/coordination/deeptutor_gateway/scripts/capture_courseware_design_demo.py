"""Capture real editable course-design widgets with synthetic, isolated inputs.

No user documents, original courseware images, model configuration, model calls,
or desktop surfaces are used. Existing output directories are never reused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
CAPTURE_ROOT = ROOT / "runtime/deeptutor_shchem/qa_0.1.56_courseware_ui_20260910_r1"
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", default="capture-r2")
    args = parser.parse_args()
    if not args.run_name.startswith("capture-") or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
        for character in args.run_name
    ):
        raise RuntimeError("Use a simple capture-prefixed run name")
    output = CAPTURE_ROOT / args.run_name
    if output.exists():
        raise RuntimeError("Refusing to reuse the owned capture output directory")
    output.mkdir(parents=True)
    synthetic_workspace = output / "synthetic-workspace"
    (synthetic_workspace / "sh-chem-db").mkdir(parents=True)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = "1"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QFont, QFontDatabase, QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox, QScrollArea

    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_preparation import (
        normalize_preparation_payload,
    )
    from integrations.deeptutor_shchem_v1.desktop_preparation_pedagogy import (
        teacher_design_starter,
    )
    from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    class EmptyProviders:
        borrow_invocation_context = None

        def __init__(self) -> None:
            self.metadata_reads = 0
            self.forbidden_accesses: list[str] = []

        def list_metadata(self):
            self.metadata_reads += 1
            return ()

        def __getattr__(self, name):
            self.forbidden_accesses.append(name)
            raise RuntimeError("Synthetic capture cannot access model configuration")

    app = create_application([])
    for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"):
        assert QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + name) >= 0
    app.setFont(QFont("Microsoft YaHei", 10))
    app.setCursorFlashTime(0)
    providers = EmptyProviders()
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(
            synthetic_workspace, state_root=output / "isolated-state"
        ),
        provider_store=providers,
    )
    tasks = DesktopTaskBridge()
    screenshots: list[dict[str, object]] = []
    checks: list[str] = []

    def settle() -> None:
        app.processEvents()
        QTest.qWait(100)
        app.processEvents()

    def source_hash(relative: str) -> dict[str, str]:
        path = ROOT / relative
        return {
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def capture(page, name: str, size: tuple[int, int], marker: str) -> None:
        page.resize(*size)
        settle()
        scroll = page.findChild(QScrollArea, "PageScroll")
        assert scroll is not None
        design_card = page.design_starter_button.parentWidget()
        design_top = design_card.mapTo(scroll.widget(), QPoint(0, 0)).y()
        scroll.verticalScrollBar().setValue(max(0, design_top - 22))
        editor = page.template_detail
        cursor = editor.textCursor()
        cursor.setPosition(editor.text().index(marker))
        editor.setTextCursor(cursor)
        editor.centerCursor()
        settle()
        # Move the requested paragraph to the first visible editor line. This
        # uses ordinary in-widget vertical scrolling, never image cropping.
        line_height = max(1, editor.fontMetrics().lineSpacing())
        line_offset = max(0, (editor.cursorRect().top() - 4) // line_height)
        editor.verticalScrollBar().setValue(
            editor.verticalScrollBar().value() + line_offset
        )
        while (
            editor.cursorRect().top() > 8
            and editor.verticalScrollBar().value()
            < editor.verticalScrollBar().maximum()
        ):
            editor.verticalScrollBar().setValue(editor.verticalScrollBar().value() + 1)
            app.processEvents()
        settle()
        pixmap = page.grab()
        assert (page.width(), page.height()) == size
        assert (pixmap.width(), pixmap.height()) == size
        assert pixmap.devicePixelRatio() == 1
        assert scroll.horizontalScrollBar().maximum() == 0
        assert editor.horizontalScrollBar().maximum() == 0
        button = page.design_starter_button
        assert button.mapTo(scroll.viewport(), QPoint(0, 0)).x() >= 0
        right_edge = button.mapTo(scroll.viewport(), QPoint(button.width(), 0)).x()
        assert right_edge <= scroll.viewport().width()
        text_width = button.fontMetrics().horizontalAdvance(button.text())
        assert text_width <= button.contentsRect().width()
        path = output / (name + ".png")
        assert pixmap.save(str(path))
        screenshots.append(
            {
                "file": str(path.resolve()),
                "width": pixmap.width(),
                "height": pixmap.height(),
                "topic": page.topic.text(),
                "editor_focus_marker": marker,
                "page_horizontal_scroll_maximum": 0,
                "editor_horizontal_scroll_maximum": 0,
                "editor_size": [editor.width(), editor.height()],
                "button_width": button.width(),
                "button_text_width": text_width,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )

    model_guard = RuntimeError("No model operation permitted during UI capture")
    with (
        patch.object(facade, "prepare_preparation", side_effect=model_guard) as prepare,
        patch.object(
            facade, "generate_preparation", side_effect=model_guard
        ) as generate,
    ):
        page = PreparationPage(facade, tasks)
        page._availability_timer.stop()
        # Resolve actual availability against the empty provider and the owned
        # isolated state; it cannot inspect any personal model configuration.
        page._context_ready((facade.preparation_availability(), (), ()))
        page.topic.setText("系统的内能")
        page.audience.setText("高二 · 约40人（合成演示）")
        page.lesson_count.setValue(2)
        page.objective.setPlainText("结合已有材料辨析概念条件，并整理课堂笔记表。")
        page.materials.setPlainText(
            "合成界面演示：尚未导入任何教材、教案、题目或图片。正式备课时请另行选择内容依据。"
        )
        before = deepcopy(page._payload())
        page.design_starter_button.click()
        starter = teacher_design_starter("新授", "系统的内能")
        assert page.template_detail.text() == starter
        assert not page.template_detail.isReadOnly()
        cursor = page.template_detail.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("\n教师调整（演示）：第二课时预留10分钟订正与补写笔记。")
        edited = page.template_detail.text()
        assert edited != starter and "教师调整（演示）" in edited
        payload = page._payload()
        assert (
            normalize_preparation_payload(payload)["advanced"]["template_and_delivery"]
            == edited
        )
        embedded, _ = json.JSONDecoder().raw_decode(
            _prompt(payload).split("教师备课简报 JSON：\n", 1)[1]
        )
        assert embedded == payload
        unchanged = deepcopy(payload)
        unchanged["advanced"]["template_and_delivery"] = before["advanced"][
            "template_and_delivery"
        ]
        assert unchanged == before
        checks.extend(
            [
                "real_button_fills_topic_and_route_starter",
                "editor_accepts_teacher_text",
                "teacher_text_survives_payload_normalization_and_actual_prompt",
                "other_fields_unchanged_by_insertion",
            ]
        )
        page.topic.setText("电离平衡常数")
        page.route.setCurrentText("复习")
        assert page.template_detail.text() == edited
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.No
        ) as question:
            page.design_starter_button.click()
            assert question.call_count == 1
            assert question.call_args.args[-1] == QMessageBox.StandardButton.No
        assert page.template_detail.text() == edited
        checks.extend(
            ["topic_route_change_preserves_edits", "replacement_defaults_to_no"]
        )
        confirmation_before = deepcopy(page._payload())
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ) as question:
            page.design_starter_button.click()
            assert question.call_count == 1
        assert page.template_detail.text() == teacher_design_starter(
            "复习", "电离平衡常数"
        )
        confirmation_after = deepcopy(page._payload())
        confirmation_after["advanced"]["template_and_delivery"] = confirmation_before[
            "advanced"
        ]["template_and_delivery"]
        assert confirmation_after == confirmation_before
        checks.append("confirmed_replacement_changes_design_only")

        # Exercise the real save button; all writes remain inside isolated-state.
        page.save_button.click()
        deadline = time.monotonic() + 10
        while page._save_task_id and time.monotonic() < deadline:
            settle()
        assert not page._save_task_id
        assert "备课草稿已保存" in page.status.text()
        saved_drafts = facade.preparation_draft_options()
        assert len(saved_drafts) == 1
        saved = saved_drafts[0]
        loaded = facade.load_preparation_draft(saved["draft_id"], saved["revision"])
        assert loaded["payload"] == page._payload()
        checks.append("real_save_button_persists_full_design_in_isolated_draft")
        page.resize(1000, 950)
        page.show()
        settle()

        for slug, topic in (("energy", "系统的内能"), ("ionization", "电离平衡常数")):
            page.topic.setText(topic)
            page.route.setCurrentText("新授")
            page.template_detail.clear()
            page.design_starter_button.click()
            assert page.template_detail.text() == teacher_design_starter("新授", topic)
            page.save_button.click()
            deadline = time.monotonic() + 10
            while page._save_task_id and time.monotonic() < deadline:
                settle()
            assert not page._save_task_id and "备课草稿已保存" in page.status.text()
            assert not page.generate_button.isEnabled()
            for label, dimensions in (("main", (1000, 950)), ("narrow", (420, 900))):
                capture(
                    page,
                    f"{slug}-{label}-reference",
                    dimensions,
                    "【与当前课题匹配的平台课例参考",
                )
                capture(page, f"{slug}-{label}-source", dimensions, "来源：国家中小学")
        assert not prepare.called and not generate.called
        assert not providers.forbidden_accesses
        checks.append("no_model_operations_or_personal_provider_access")
        page.close()
    tasks.shutdown()
    report = {
        "synthetic_only": True,
        "desktop_capture": False,
        "source_documents_or_images_loaded": 0,
        "model_operations": 0,
        "provider_metadata_reads_from_empty_store": providers.metadata_reads,
        "personal_provider_accesses": providers.forbidden_accesses,
        "isolated_state": str(facade.paths.state_root),
        "application_factory": "desktop_workbench.app.create_application",
        "stylesheet": "unchanged current product WORKBENCH_STYLE",
        "font": "Microsoft YaHei 10pt",
        "checks": checks,
        "code_snapshot": [
            source_hash(
                "integrations/deeptutor_shchem_v1/desktop_preparation_pedagogy.py"
            ),
            source_hash(
                "integrations/deeptutor_shchem_v1/desktop_workbench/workflow_pages.py"
            ),
            source_hash(
                "staging/coordination/deeptutor_gateway/tests/test_preparation_course_design.py"
            ),
        ],
        "screenshots": screenshots,
        "visual_inspection_status": "pending_separate_actual_image_view",
        "human_review": False,
        "publication_approved": False,
    }
    (output / "capture-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "checks_passed": len(checks),
                "screenshots": len(screenshots),
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

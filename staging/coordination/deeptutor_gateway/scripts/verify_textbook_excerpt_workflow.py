"""Real local source -> manual excerpt UI -> form/draft/mock request QA.

The assistant types a visual transcription to simulate the teacher controls.
This is not an actual teacher review or evidence of model output quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "staging/coordination/deeptutor_gateway/tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class NoProviderAccess:
    def __getattr__(self, name):
        raise AssertionError("Real provider access forbidden in local excerpt QA")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)

    from PySide6.QtCore import QSize, Qt, QTimer
    from PySide6.QtGui import QFontDatabase, QImage, QPainter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from test_desktop_preparation_provider import _candidate, _context, _Transport

    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_preparation import (
        normalize_preparation_payload,
    )
    from integrations.deeptutor_shchem_v1.desktop_preparation_provider import (
        CLASSROOM_NOTE_FINAL_CHECK,
        PREPARATION_PROMPT_REVISION,
        StructuredPreparationProvider,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        WORKBENCH_STYLE,
        install_font_fallbacks,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import (
        PreparationSourcesDialog,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import (
        TextbookSourceDialog,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    class OfflineFacade(DesktopWorkbenchFacade):
        def preparation_availability(self):
            return SimpleNamespace(
                provider_ready=False, renderer_ready=False, message_zh="离线QA"
            )

        def preparation_profiles(self):
            return ()

        def list_preparations(self, *, limit=3):
            return ()

    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    facade = OfflineFacade(
        DesktopPaths.from_workspace(ROOT, state_root=output / "isolated-state"),
        provider_store=NoProviderAccess(),
    )
    tasks = DesktopTaskBridge()
    page = PreparationPage(facade, tasks)
    page._availability_timer.stop()
    page.topic.setText("电解质与电离方程式")
    page.audience.setText("高二")
    page.objective.setPlainText("区分电离与导电，准确记录电离定义。")
    page.materials.setPlainText("已有备课备注保留。")
    before_form = page._payload()
    protected = [
        ROOT / "课本/沪科技化学必修第一册【高清教材】.pdf",
        ROOT / "sh-chem-db/kb/textbook_knowledge_map_v1_2026-08-28/concepts.jsonl",
        ROOT / "sh-chem-db/catalog.csv",
    ]
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    before_hashes = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    quote = "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
    failures, screenshots = [], []
    source_exec, viewer_exec = PreparationSourcesDialog.exec, TextbookSourceDialog.exec

    def run_viewer(viewer):
        def operate():
            try:
                deadline = time.monotonic() + 20
                while not viewer._loaded_pages and time.monotonic() < deadline:
                    QTest.qWait(30)
                assert viewer._loaded_pages, viewer.status.text()
                viewer.pages.setCurrentIndex(1)
                assert viewer.view.pageNavigator().currentPage() == 61
                viewer.excerpt_button.click()
                # QPdfDocument renders transparency; compose onto paper-white
                # for a standalone page preview, just as QPdfView does.
                rendered = viewer.document.render(61, QSize(1100, 1550))
                paper = QImage(rendered.size(), QImage.Format.Format_RGB32)
                paper.fill(Qt.GlobalColor.white)
                painter = QPainter(paper)
                painter.drawImage(0, 0, rendered)
                painter.end()
                assert paper.save(str(output / "textbook-pdf62-render.png"))
                editor = viewer.excerpt_panel
                assert not editor.text.toPlainText()
                editor.text.setPlainText(quote)
                assert not editor.use.isEnabled()
                for width in (900, 420):
                    viewer.resize(width, 850)
                    QTest.qWait(600)
                    viewer.view.verticalScrollBar().setValue(
                        int(viewer.view.verticalScrollBar().maximum() * 0.7)
                    )
                    QTest.qWait(200)
                    assert viewer.width() == width
                    name = f"excerpt-editor-{width}.png"
                    assert viewer.grab().save(str(output / name))
                    screenshots.append(name)
                editor.confirmed.setChecked(
                    True
                )  # Simulated confirmation, not human review.
                editor.use.click()
                assert viewer._closed and viewer.buffer.size() == 0
            except Exception as exc:  # noqa: BLE001 - close modal and report outside Qt
                failures.append(str(exc))
                viewer.reject()

        QTimer.singleShot(100, operate)
        return viewer_exec(viewer)

    def run_source(selector):
        def operate():
            try:
                selector.concept_search.setText("TB-M1-C2-S22-C06")
                selector.concept_list.setCurrentRow(0)
                selector.original_button.click()
                assert not failures, failures
                assert len(selector.selected_excerpts) == 1
                selector.preview_button.click()
                assert selector.reference is not None, selector.status.text()
                text = selector.reference["materials"]
                assert quote in text
                (output / "reference.txt").write_text(text, encoding="utf-8")
                for width in (900, 420):
                    selector.resize(width, 700)
                    QTest.qWait(100)
                    selector.body_scroll.ensureWidgetVisible(selector.preview)
                    selector.preview.verticalScrollBar().setValue(
                        selector.preview.verticalScrollBar().maximum()
                    )
                    QTest.qWait(100)
                    assert selector.width() == width
                    assert selector.body_scroll.horizontalScrollBar().maximum() == 0
                    name = f"excerpt-reference-{width}.png"
                    assert selector.grab().save(str(output / name))
                    screenshots.append(name)
                selector.import_button.click()
            except Exception as exc:  # noqa: BLE001 - close modal and report outside Qt
                failures.append(str(exc))
                selector.reject()

        QTimer.singleShot(0, operate)
        return source_exec(selector)

    try:
        TextbookSourceDialog.exec = run_viewer
        PreparationSourcesDialog.exec = run_source
        page.source_import_button.click()
        assert not failures, failures
        after_form = page._payload()
        assert quote in after_form["materials"]
        assert after_form["materials"].startswith(before_form["materials"])
        assert all(
            after_form[k] == v for k, v in before_form.items() if k != "materials"
        )
        payload = normalize_preparation_payload(after_form)
        receipt = facade.create_preparation_draft(after_form)
        option = next(
            o
            for o in facade.preparation_draft_options()
            if o["draft_id"] == receipt.draft_id
        )
        loaded = facade.load_preparation_draft(option["draft_id"], option["revision"])
        assert loaded["payload"]["materials"] == after_form["materials"]
        for api_style in ("responses", "chat_completions"):
            transport = _Transport(_candidate())
            StructuredPreparationProvider(
                _context(api_style=api_style), transport=transport
            ).generate(payload)
            body = json.loads(transport.requests[0].body)
            prompt = (
                body["input"][0]["content"][0]["text"]
                if api_style == "responses"
                else body["messages"][1]["content"]
            )
            sent, _ = json.JSONDecoder().raw_decode(
                prompt.split("教师备课简报 JSON：\n", 1)[1]
            )
            assert sent["materials"] == after_form["materials"]
            assert "摘录与蒸馏摘要冲突时不得混合拼接成原句" in prompt
            assert prompt.endswith(CLASSROOM_NOTE_FINAL_CHECK)
            assert PREPARATION_PROMPT_REVISION in prompt
        after_hashes = {str(p.relative_to(ROOT)): digest(p) for p in protected}
        assert before_hashes == after_hashes
        report = {
            "actual_modal_excerpt_to_reference_to_form": True,
            "draft_roundtrip_exact_materials": True,
            "mock_request_api_styles": ["responses", "chat_completions"],
            "request_preserves_entire_reference": True,
            "prompt_revision": PREPARATION_PROMPT_REVISION,
            "classroom_note_final_check_in_both_requests": True,
            "other_form_fields_preserved": True,
            "source_hashes_before": before_hashes,
            "source_hashes_after": after_hashes,
            "screenshots": screenshots,
            "model_calls": 0,
            "confirmation_actor": "assistant_simulated_UI_for_QA_not_human",
            "human_reviewed": False,
            "teaching_use_approved": False,
            "visual_inspection": "pending_separate_image_review",
        }
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        PreparationSourcesDialog.exec, TextbookSourceDialog.exec = (
            source_exec,
            viewer_exec,
        )
        page.close()
        tasks.shutdown(1000)
        facade.shutdown()


if __name__ == "__main__":
    main()

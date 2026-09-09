"""Exercise the native editor using real saved AI content in isolated QA state."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.blueprint_draft_dialog import (
    BlueprintDraftDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-state", type=Path, required=True)
    parser.add_argument("--preview-id", required=True)
    args = parser.parse_args()
    source_state = DesktopStateStore(args.source_state)
    before = source_state.snapshot()
    state = DesktopStateStore(
        Path(tempfile.mkdtemp(prefix="shchem-blueprint-draft-qa-"))
    )
    for key, value in before["drafts"].items():
        if key == args.preview_id or value.get("preview_id") == args.preview_id:
            state.save_draft(key, value)
    original = state.snapshot()
    paths = DesktopPaths.from_workspace(ROOT, state_root=state.root)
    facade = DesktopWorkbenchFacade(paths, state_store=state)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    output = (
        ROOT
        / "runtime/deeptutor_shchem/qa"
        / time.strftime("blueprint-drafts-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=False)
    dialog = None
    try:
        dialog = BlueprintDraftDialog(facade, args.preview_id)
        ai_index = next(
            i
            for i, s in enumerate(dialog._sources)
            if s["source_label"].startswith("AI修订")
        )
        dialog.source.setCurrentIndex(ai_index)
        original_id = dialog._source["source_id"]
        dialog.theme.setPlainText(
            dialog.theme.toPlainText() + "（本地编辑功能验收样例）"
        )
        dialog.purpose.setPlainText(
            dialog.purpose.toPlainText()
            + "\n自动化操作验收：仅检查编辑与保存，不构成化学判断。"
        )
        dialog.question.setCurrentIndex(7)
        dialog.fields["answer_outline"].setPlainText(
            "自动化功能验收占位：该段由测试脚本编辑，仅验证保存与重开，不是教师答案。"
        )
        dialog.note.setPlainText(
            "自动化QA：从已有AI修订另存，保留原稿；不代表教师审核。"
        )
        dialog.save_button.click()
        assert not dialog._dirty and dialog._source["source_id"].startswith(
            "BLUEPRINT-TEACHER-"
        )
        saved_id = dialog._source["source_id"]
        saved = state.snapshot()["drafts"][saved_id]
        assert saved["source_id"] == original_id
        assert saved["candidate"]["question_chain"][7]["answer_outline"].startswith(
            "自动化功能验收"
        )
        assert all(
            state.snapshot()["drafts"][key] == value
            for key, value in original["drafts"].items()
        )
        dialog.close()
        dialog = BlueprintDraftDialog(facade, args.preview_id)
        dialog.source.setCurrentIndex(dialog.source.findData(saved_id))
        dialog.question.setCurrentIndex(7)
        assert "自动化功能验收" in dialog.fields["answer_outline"].toPlainText()
        dialog.show()
        captures = []
        for width in (920, 420):
            dialog.resize(width, 820)
            for index, name in enumerate(
                ("general", "material", "question", "evidence", "source", "preview")
            ):
                dialog.tabs.setCurrentIndex(index)
                if name == "evidence":
                    dialog.evidence_pick.setCurrentIndex(24)
                    assert "E24" in dialog.evidence.toPlainText()
                for _ in range(4):
                    app.processEvents()
                assert dialog.width() == width
                if index < 3:
                    assert (
                        dialog.tabs.widget(index).horizontalScrollBar().maximum() == 0
                    )
                path = output / f"{name}-{width}.png"
                assert dialog.grab().save(str(path))
                captures.append(str(path))
            dialog.tabs.setCurrentIndex(2)
            scroll = dialog.tabs.widget(2).verticalScrollBar()
            scroll.setValue(scroll.maximum())
            app.processEvents()
            path = output / f"question-bottom-{width}.png"
            assert dialog.grab().save(str(path))
            captures.append(str(path))
            scroll.setValue(0)
        assert source_state.snapshot() == before
        report = {
            "status": "passed",
            "state_root": str(state.root),
            "source_state_unchanged": True,
            "saved_draft_id": saved_id,
            "source_id": original_id,
            "original_records_unchanged": True,
            "saved_and_reopened": True,
            "model_calls": 0,
            "teacher_content_approval": False,
            "captures": captures,
        }
        (output / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"output": str(output), **report}, ensure_ascii=False))
    finally:
        if dialog:
            dialog._dirty = False
            dialog.close()
        facade.shutdown()


if __name__ == "__main__":
    main()

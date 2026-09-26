"""Open a real saved blueprint's handout pages in an isolated native editor."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import (
    DesktopPaths,
    default_state_root,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.blueprint_draft_dialog import (
    BlueprintDraftDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)

SOURCE = Path("C:/Users/20671/AppData/Local/Temp/shchem-teacher-review-qa-brte5iwe")
PREVIEW = "BLUEPRINT-5f8082455b6e445fa35358d1db36362c"


def tree(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class LocalTasks:
    def submit(self, _label, operation, *, on_success, on_failure):
        try:
            result = operation()
        except Exception as exc:  # noqa: BLE001 - match the UI task error boundary
            on_failure(getattr(exc, "message_zh", "读取失败"))
        else:
            on_success(result)
        return "offline-page-qa"


def main():
    before_source, before_user = tree(SOURCE), tree(default_state_root())
    source = DesktopStateStore(SOURCE).snapshot()["drafts"][PREVIEW]
    paths = DesktopPaths.from_workspace(
        ROOT, state_root=tempfile.mkdtemp(prefix="shchem-blueprint-pages-qa-")
    )
    state = DesktopStateStore(paths.state_root)
    state.save_draft(PREVIEW, source)
    facade = DesktopWorkbenchFacade(paths, state_store=state)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    output = (
        paths.runtime_root
        / "qa"
        / time.strftime("blueprint-source-pages-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=False)
    captures = []
    dialog = BlueprintDraftDialog(facade, PREVIEW, tasks=LocalTasks())

    def capture(name):
        for _ in range(5):
            app.processEvents()
        path = output / (name + ".png")
        assert dialog.grab().save(str(path))
        captures.append(str(path))

    try:
        dialog.show()
        assert "E24" in dialog._source["handout_evidence_ids"]
        dialog.theme.setPlainText(
            source["result"]["candidate"]["theme_center"] + "\n自动化QA未保存编辑"
        )
        dialog.evidence_pick.setCurrentIndex(24)
        dialog.tabs.setCurrentWidget(dialog.evidence_panel)
        assert dialog.source_open.isEnabled()
        labels = [
            dialog.source_page.itemText(i) for i in range(dialog.source_page.count())
        ]
        assert len(labels) >= 2
        for width in (920, 420):
            dialog.resize(width, 900)
            capture(f"e24-evidence-{width}")
            assert dialog.width() == width
        for role in ("question", "answer"):
            index = next(
                i
                for i in range(dialog.source_page.count())
                if dialog.source_page.itemData(i)[1] == role
            )
            dialog.source_page.setCurrentIndex(index)
            dialog.source_open.click()
            viewer = dialog._source_zoom
            assert viewer is not None
            viewer.zoom.setValue(50)
            for _ in range(5):
                app.processEvents()
            path = output / f"e24-{role}-source.png"
            assert viewer.grab().save(str(path))
            captures.append(str(path))
            viewer.close()
            app.processEvents()
        assert dialog._dirty and "自动化QA未保存编辑" in dialog.theme.toPlainText()
        assert state.snapshot()["drafts"][PREVIEW] == source
        # Only mutate the isolated test copy to exercise a changed source.
        changed = deepcopy(source)
        changed["preview"]["handout_reference"]["local_provenance"][2]["revision"] = (
            "outdated-qa-revision"
        )
        state.save_draft(PREVIEW, changed)
        dialog._dirty = False
        dialog._reload_sources()
        dialog.evidence_pick.setCurrentIndex(24)
        assert not dialog.source_open.isEnabled()
        assert "已更新" in dialog.source_page_hint.text()
        capture("e24-outdated-source-420")
        assert "SO" in dialog.evidence.toPlainText()
        state.save_draft(PREVIEW, source)
    finally:
        dialog._dirty = False
        dialog.close()
        facade.shutdown()
    assert tree(SOURCE) == before_source and tree(default_state_root()) == before_user
    result = {
        "status": "passed",
        "preview_id": PREVIEW,
        "evidence_id": "E24",
        "page_labels": labels,
        "question_and_answer_images_opened": True,
        "unsaved_text_preserved": True,
        "changed_source_refused": True,
        "original_blueprint_unchanged": True,
        "real_user_state_unchanged": True,
        "source_qa_state_unchanged": True,
        "model_calls": 0,
        "state_root": str(paths.state_root),
        "captures": captures,
    }
    (output / "verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

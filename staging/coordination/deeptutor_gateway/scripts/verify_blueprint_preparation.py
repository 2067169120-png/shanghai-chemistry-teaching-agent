"""Exercise real saved blueprint -> native preparation form -> offline draft."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QScrollArea

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths, default_state_root
from integrations.deeptutor_shchem_v1.desktop_preparation import normalize_preparation_payload
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import WORKBENCH_STYLE, install_font_fallbacks
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import PreparationPage

SOURCE = Path("C:/Users/20671/AppData/Local/Temp/shchem-teacher-review-qa-brte5iwe")
PREVIEW = "BLUEPRINT-5f8082455b6e445fa35358d1db36362c"
TEACHER = "BLUEPRINT-TEACHER-f5ad961abaaa497b8c5459bc8aaa5c57"


def tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def main():
    before_source, before_user = tree(SOURCE), tree(default_state_root())
    records = DesktopStateStore(SOURCE).snapshot()["drafts"]
    paths = DesktopPaths.from_workspace(ROOT, state_root=tempfile.mkdtemp(prefix="shchem-blueprint-prep-qa-"))
    state = DesktopStateStore(paths.state_root)
    for key, value in records.items():
        if key == PREVIEW or value.get("preview_id") == PREVIEW:
            state.save_draft(key, value)
    before_records = state.snapshot()["drafts"]
    facade = DesktopWorkbenchFacade(paths, state_store=state)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    output = paths.runtime_root / "qa" / time.strftime("blueprint-preparation-%Y%m%d-%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.topic.setText("电解质与非电解质：概念辨析复习")
    page.audience.setText("高二·离线流程验收，不代表真实班级")
    page.objective.setPlainText("从资料中区分定义条件、实验观察与推理边界；本次仅验收参考传递。")
    page.materials.setPlainText("教师原有说明：课堂活动须先核验实验条件。")
    before = page._payload()
    captures, errors = [], []

    def capture(widget, name):
        for _ in range(5):
            app.processEvents()
        path = output / f"{name}.png"
        assert widget.grab().save(str(path))
        captures.append(str(path))

    def choose():
        dialog = app.activeModalWidget()
        try:
            assert dialog is not None
            index = next(i for i in range(dialog.source.count()) if dialog.source.itemData(i)["source_id"] == TEACHER)
            dialog.source.setCurrentIndex(index)
            assert dialog.reference is not None
            for width in (920, 420):
                dialog.resize(width, 780)
                capture(dialog, f"reference-{width}")
                assert dialog.width() == width
                assert dialog.preview.horizontalScrollBar().maximum() == 0
            dialog.preview.verticalScrollBar().setValue(dialog.preview.verticalScrollBar().maximum())
            capture(dialog, "reference-evidence-420")
            dialog.import_button.click()
        except Exception as exc:
            errors.append(type(exc).__name__ + ":" + str(exc))
            if dialog:
                dialog.reject()

    try:
        page.resize(920, 900)
        page.show()
        QTimer.singleShot(0, choose)
        page.blueprint_import_button.click()
        assert not errors, errors
        payload = page._payload()
        assert payload["materials"].startswith(before["materials"] + "\n\n")
        assert TEACHER in payload["materials"]
        for key in before:
            if key != "materials":
                assert before[key] == payload[key]
        normalized = normalize_preparation_payload(payload)
        prompt = _prompt(normalized)
        assert all(f"E{i}" in prompt for i in range(1, 26))
        assert "uncertainties" in prompt
        assert not normalized["publication_allowed"]
        for width in (920, 420):
            page.resize(width, 900)
            scroll = page.findChild(QScrollArea, "PageScroll")
            scroll.ensureWidgetVisible(page.blueprint_import_button)
            capture(page, f"imported-form-{width}")
            assert scroll.horizontalScrollBar().maximum() == 0
        page.save_button.click()
        deadline = time.monotonic() + 10
        while page._save_task_id and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert page._save_task_id is None
        after_records = state.snapshot()["drafts"]
        created = {key: value for key, value in after_records.items() if key not in before_records}
        assert len(created) == 1
        saved = next(iter(created.values()))
        assert saved["kind"] == "preparation" and saved["status"] == "draft"
        assert saved["core_fields"]["materials"] == payload["materials"]
        assert all(after_records[key] == value for key, value in before_records.items())
        scroll.ensureWidgetVisible(page.status)
        capture(page, "saved-draft-420")
        assert tree(SOURCE) == before_source and tree(default_state_root()) == before_user
        report = {
            "passed": True, "model_calls": 0, "source_state_unchanged": True,
            "user_state_unchanged": True, "original_blueprints_unchanged": True,
            "source_id": TEACHER, "materials_chars": len(payload["materials"]),
            "evidence_count": 25, "saved_draft_ids": list(created),
            "state_root": str(paths.state_root), "captures": captures,
            "scope": "real saved candidate, offline native transfer/save and provider prompt; not generated PPT or chemistry validation",
        }
        (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        page.close()
        bridge.shutdown()


if __name__ == "__main__":
    main()

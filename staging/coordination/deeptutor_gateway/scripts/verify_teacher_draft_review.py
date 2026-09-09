"""Isolated native teacher-draft/review round trip; live model only with --execute."""

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

from integrations.deeptutor_shchem_v1.desktop_blueprint_drafts import DRAFT_KIND
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
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderSettingsStore,
)


class LocalTasks:
    def submit(self, label, operation, *, on_success, on_failure):
        on_success(operation())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-state", type=Path, required=True)
    parser.add_argument("--preview-id", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    source_state = DesktopStateStore(args.source_state)
    before = source_state.snapshot()
    state = DesktopStateStore(
        Path(tempfile.mkdtemp(prefix="shchem-teacher-review-qa-"))
    )
    for key, record in before["drafts"].items():
        if key == args.preview_id or record.get("preview_id") == args.preview_id:
            state.save_draft(key, record)
    baseline = state.snapshot()["drafts"]
    output = (
        ROOT
        / "runtime/deeptutor_shchem/qa"
        / time.strftime("teacher-draft-review-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=False)
    paths = DesktopPaths.from_workspace(ROOT, state_root=state.root)
    providers = ModelProviderSettingsStore(
        DesktopPaths.from_workspace(ROOT).settings_root, project_root=ROOT
    )
    facade = DesktopWorkbenchFacade(paths, provider_store=providers, state_store=state)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    report = {
        "state_root": str(state.root),
        "output": str(output),
        "live_requested": args.execute,
        "captures": [],
    }
    dialog = None

    def capture(widget, label, widths):
        for width in widths:
            widget.resize(width, 820)
            for _ in range(4):
                app.processEvents()
            assert widget.width() == width
            target = output / f"{label}-{width}.png"
            assert widget.grab().save(str(target))
            report["captures"].append(str(target))

    def write(name, value):
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        profile = next(
            p
            for p in facade.preparation_profiles()
            if p.profile_id == "desktop-default"
        )
        dialog = BlueprintDraftDialog(
            facade, args.preview_id, tasks=LocalTasks(), profile=None
        )
        dialog.show()
        index = dialog.source.findData(args.source_id)
        assert index >= 0
        dialog.source.setCurrentIndex(index)
        dialog.unknowns.appendPlainText(
            "本轮追加待核对项：逐项确认E24的支持范围，以及A7气体组成与解答所用条件是否一致。"
            "这是一条待核验问题，不预设结论；仍须回到题面和来源核对。"
        )
        dialog.note.setPlainText(
            "自动化流程验收：在已有AI修订稿追加待核对项后另存；不是教师化学审核。"
        )
        assert not dialog.review_button.isEnabled()
        dialog.save_button.click()
        assert not dialog._dirty and dialog._source["source_kind"] == DRAFT_KIND
        saved_id = dialog.source.currentData()
        saved = state.snapshot()["drafts"][saved_id]
        revision = dialog._source["source_revision"]
        write("teacher-draft.json", saved)
        report.update(source_draft_id=saved_id, source_candidate_revision=revision)
        capture(dialog, "teacher-draft", (920, 420))
        dialog.review_button.click()
        child = dialog._review_dialog
        assert child.source_draft_id == saved_id and not child.start.isEnabled()
        assert "本轮追加待核对项" in child.original.toPlainText()
        capture(child, "review-before", (900, 420))
        print(json.dumps({"stage": "ready", **report}, ensure_ascii=False), flush=True)
        if args.execute:
            result = facade.review_prompt_blueprint(
                args.preview_id,
                revision,
                profile.profile_id,
                profile.revision,
                source_draft_id=saved_id,
                teacher_confirmed=True,
                focus="重点检查新增待核对项；逐一核对E24的实际支持范围，及A7前提和答案一致性。不要把可能的修订设想写成已观察的事实。",
                on_progress=lambda value: print(
                    json.dumps(
                        {"stage": value["stage"], "review_id": value["review_id"]}
                    ),
                    flush=True,
                ),
            )
            write("review-result.json", result)
            assert result["source_draft_id"] == saved_id
            assert result["source_candidate_revision"] == revision
            assert (
                not result["publication_allowed"]
                and not result["chemistry_correctness_verified"]
            )
            report.update(
                review_id=result["review_id"],
                latency_ms=result["latency_ms"],
                usage=result["usage"],
                issue_count=len(result["report"]["issues"]),
            )
            child.close()
            # Reopen through the actual editor entry: offline history must select the saved source.
            dialog.review_button.click()
            child = dialog._review_dialog
            assert child.history.count() == 2 and not child.start.isEnabled()
            child.history.setCurrentIndex(1)
            for i, name in enumerate(("source", "findings", "revision")):
                child.tabs.setCurrentIndex(i)
                capture(child, name, (900, 420))
            child.close()
            # The generated revision is now selectable as an editing basis; no original is overwritten.
            dialog.source.setCurrentIndex(dialog.source.findData(result["review_id"]))
            assert dialog._candidate == result["candidate"]
            assert not dialog.review_button.isEnabled()
            dialog.save_button.click()
            assert dialog._source["source_kind"] == DRAFT_KIND
            report["next_draft_id"] = dialog.source.currentData()
            capture(dialog, "next-editable-draft", (920, 420))
        assert source_state.snapshot() == before
        after = state.snapshot()["drafts"]
        assert all(after[key] == value for key, value in baseline.items())
        assert after[saved_id] == saved
        report.update(
            status="passed",
            source_state_unchanged=True,
            originals_unchanged=True,
            teacher_approval=False,
        )
    except Exception as exc:  # noqa: BLE001 - emit sanitized diagnostic only
        report.update(
            status="failed", error_code=getattr(exc, "code", type(exc).__name__)
        )
    finally:
        write("verification.json", report)
        print(
            json.dumps({"stage": "finished", **report}, ensure_ascii=False), flush=True
        )
        if dialog is not None:
            if dialog._review_dialog is not None:
                dialog._review_dialog.close()
            dialog._dirty = False
            dialog.close()
        facade.shutdown()
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

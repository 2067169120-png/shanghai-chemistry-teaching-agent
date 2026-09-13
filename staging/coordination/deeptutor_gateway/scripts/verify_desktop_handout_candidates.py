"""Real-source candidate browsing and isolated review persistence; no model calls."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    HandoutCandidateService,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.handout_candidate_dialog import (
    HandoutCandidateDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)


class LocalTasks:
    def submit(self, _label, operation, *, on_success, on_failure):
        on_success(operation())
        return "offline-real-source-qa"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-id", choices=("PKG-033", "PKG-043"), default="PKG-033"
    )
    args = parser.parse_args()
    expected_complete, expected_incomplete = {
        "PKG-033": (13, 71),
        "PKG-043": (27, 41),
    }[args.package_id]
    workspace = Path.cwd()
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tempfile.mkdtemp(prefix="shchem-handout-qa-")
    )
    facade = DesktopWorkbenchFacade(paths)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    dialog = None
    try:
        started = time.monotonic()
        dialog = HandoutCandidateDialog(facade, LocalTasks())
        loaded_seconds = round(time.monotonic() - started, 3)
        assert dialog._items and dialog._shown
        selected_package = dialog.package.findData(args.package_id)
        assert selected_package >= 0
        dialog.package.setCurrentIndex(selected_package)
        assert dialog.results.count() == expected_complete
        if args.package_id == "PKG-043":
            selected = [
                row for row in dialog._items if row["package_id"] == args.package_id
            ]
            assert len(selected) == 68
            assert {row["batch_id"] for row in selected} == {
                "NATIVE-BATCH-NV2W2-PKG043-A02"
            }
            assert not {
                "152",
                "193",
                "242",
                "298",
                "347",
                "390",
                "413",
                "431",
                "462",
                "497",
            }.intersection(str(row["printed_number"]) for row in selected)
            q49 = next(row for row in selected if str(row["printed_number"]) == "49")
            assert q49["classification"] == "hybrid_visual_required"
            assert not q49["practice_eligible"]
        item = dialog._detail
        assert item and item["question_text"] and item["answer_text"]
        dialog.show()
        output = (
            paths.runtime_root
            / "qa"
            / time.strftime("handout-candidates-%Y%m%d-%H%M%S")
        )
        output.mkdir(parents=True, exist_ok=False)
        captures = []
        for width in (1000, 420):
            dialog.resize(width, 900)
            for tab_index, name in ((0, "question"), (1, "answer"), (3, "review")):
                dialog.tabs.setCurrentIndex(tab_index)
                for _ in range(4):
                    app.processEvents()
                path = output / f"{name}-{width}.png"
                assert dialog.grab().save(str(path))
                captures.append(
                    {
                        "path": str(path),
                        "width": dialog.width(),
                        "height": dialog.height(),
                    }
                )
                assert dialog.width() == width, (
                    "Native dialog overflowed requested width"
                )
        dialog.tabs.setCurrentIndex(2)
        assert dialog.page_combo.count() >= 2
        dialog.page_combo.setCurrentIndex(0)
        dialog.page_button.click()
        assert dialog._zoom is not None
        dialog._zoom.zoom.setValue(50)
        for _ in range(5):
            app.processEvents()
        path = output / "source-page.png"
        assert dialog._zoom.grab().save(str(path))
        # This is an isolated software test, not a teacher's content acceptance.
        dialog.decision.setCurrentIndex(dialog.decision.findData("needs_correction"))
        dialog.note.setPlainText(
            "自动化功能测试：仅核验本机保存与重开，不代表内容需要修订或已通过教师审核。"
        )
        dialog.save_button.click()
        reopened = HandoutCandidateService(
            workspace, DesktopStateStore(paths.state_root)
        )
        detail = reopened.detail(item["key"])
        assert detail["review_state"] == "needs_correction"
        assert detail["review"]["bank_ingest_allowed"] is False
        assert "自动化功能测试" in detail["review"]["note"]
        copied = facade.handout_candidate_copy_text(item["key"], item["revision"])
        assert item["source_name"] in copied and "非官方" in copied
        dialog.state_filter.setCurrentIndex(dialog.state_filter.findData("incomplete"))
        assert (
            dialog.results.count() == expected_incomplete
            and "原生文字片段" in dialog.question.toPlainText()
        )
        result = {
            "status": "real_source_browse_and_isolated_review_passed",
            "catalog_items": len(dialog._items),
            "catalog_packages": len({row["package_id"] for row in dialog._items}),
            "catalog_complete": sum(
                row["classification"] == "native_text_complete" for row in dialog._items
            ),
            "catalog_warnings": dialog.warning.text(),
            "loaded_seconds": loaded_seconds,
            "selected_package": args.package_id,
            "selected_complete": expected_complete,
            "selected_incomplete": expected_incomplete,
            "source_page_verified": True,
            "isolated_review_reopened": True,
            "model_calls": 0,
            "formal_bank_writes": 0,
            "state_root": str(paths.state_root),
            "captures": captures,
            "source_page_capture": str(path),
        }
        (output / "verification.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        if dialog is not None:
            dialog._dirty = False
            dialog.close()
        facade.shutdown()


if __name__ == "__main__":
    main()

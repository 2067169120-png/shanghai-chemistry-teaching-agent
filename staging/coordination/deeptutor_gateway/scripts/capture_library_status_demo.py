"""Capture the native library status using synthetic cards, without source data."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", required=True, type=Path)
    args = parser.parse_args()
    if args.folder.exists():
        raise RuntimeError("Use a fresh capture folder")
    args.folder.mkdir(parents=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QTest

    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_page import (
        LibraryPage,
    )
    from staging.coordination.deeptutor_gateway.tests.test_desktop_library_ui import (
        _card,
        _Facade,
        _ManualBridge,
        _search_result,
    )

    app = create_application([])
    for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc", "arial.ttf", "segoeui.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + name)
    app.setFont(QFont("Microsoft YaHei", 10))
    facade, tasks = _Facade(), _ManualBridge()
    page = LibraryPage(facade, tasks)
    page.setWindowTitle("题库状态演示 · 合成数据")
    page.resize(1180, 820)
    page.show()
    page._apply_results(replace(
        _search_result(_card("A", "完整大题演示（合成资料）")),
        pending_atomic_parts=43, pending_matched_atomic_parts=0,
    ))
    tasks.succeed(tasks.pending.pop(0), facade.details["A"])
    QTest.qWait(150)
    assert page.grab().save(str(args.folder / "library-complete-themes.png"))
    page._apply_results(replace(
        _search_result(), pending_atomic_parts=43, pending_matched_atomic_parts=2,
    ))
    page.query.setText("待归属资料演示")
    QTest.qWait(100)
    assert page.grab().save(str(args.folder / "library-pending-notice.png"))
    assert page.results.count() == 0 and not page.detail_view_button.isEnabled()
    report = {
        "synthetic_only": True, "source_images_used": False,
        "model_called": False, "actual_pending_count_not_measured_by_this_demo": True,
        "pending_notice_visible": "本次匹配 2 个" in page.result_summary.text(),
        "phantom_theme_cards": page.results.count(),
    }
    (args.folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    page.close()
    app.processEvents()
    print(json.dumps(report))


if __name__ == "__main__":
    main()

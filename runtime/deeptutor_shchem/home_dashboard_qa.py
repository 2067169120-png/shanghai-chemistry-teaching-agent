"""Capture the native teacher desk using temporary, synthetic personal storage."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    from PySide6.QtWidgets import QScrollArea
    from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import typography_report

    app = create_application(["home-dashboard-qa"])
    captures = []

    def settle(predicate=lambda: True):
        end = time.monotonic() + 20
        for _ in range(6):
            app.processEvents()
            time.sleep(.03)
        while not predicate() and time.monotonic() < end:
            app.processEvents()
            time.sleep(.03)
        assert predicate(), "The native desk did not finish loading"

    with tempfile.TemporaryDirectory(prefix="shchem-home-ui-qa-") as temporary:
        facade = build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=temporary))
        window = TeacherWorkbenchWindow(facade)
        window.setWindowTitle("沪上化学智研台 · 合成界面验收")
        window.show()
        home = window.home_page

        def capture(name, width, height, *, scroll_to=None):
            window.resize(width, height)
            settle()
            scroll = home.findChild(QScrollArea, "PageScroll")
            scroll.verticalScrollBar().setValue(0)
            if scroll_to is not None:
                scroll.ensureWidgetVisible(scroll_to, 8, 8)
            settle()
            image = window.grab()
            path = output / (name + ".png")
            assert image.save(str(path))
            assert window.width() == width and window.height() == height
            captures.append({"path": str(path), "width": image.width(), "height": image.height(),
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

        try:
            settle(lambda: not home._loading)
            capture("home-empty-1366x768", 1366, 768)
            prep = window.preparation_page
            for title in ("化学平衡的建立与判据", "离子反应：从现象到证据", "电化学专题：装置与反应",
                          "有机物结构与性质", "实验复习：变量与结论"):
                payload = prep._payload()
                payload.update(topic=title + "（合成示例）", audience="高二 · 软件界面验收",
                               objective="合成界面数据，用于检查可读性和操作布局。",
                               materials="本例未引用真实试卷、教材或学生资料，未调用模型。")
                facade.create_preparation_draft(payload)
            facade.state_store.add_many_to_basket([
                {"key": f"qa-synthetic-{i}", "title_zh": title + "（合成示例）"}
                for i, title in enumerate(("化学平衡 · 课堂观察与推理", "实验设计 · 条件控制与结论",
                                           "反应原理 · 读图与解释", "物质转化 · 信息整理"))])
            home.update_editor({"topic": "化学平衡专题复习（合成示例）"})
            home.refresh()
            settle(lambda: not home._loading)
            home.table.selectRow(0)
            for width, height in ((1366, 768), (900, 700), (420, 700), (360, 560)):
                capture(f"home-{width}x{height}", width, height)
                if width <= 900:
                    capture(f"home-{width}x{height}-basket", width, height, scroll_to=home.preview_button)
            facade.load_desktop_registry = lambda **kw: SimpleNamespace(
                products=[], curriculum=SimpleNamespace(loaded=False, message_zh="合成目录状态，用于检查展开布局。"))
            home.diagnostics.toggle.click()
            settle(lambda: not home._registry_loading)
            capture("home-360x560-diagnostics", 360, 560, scroll_to=home.registry_refresh)
            report = {"scope": "Native Qt screenshots with synthetic temporary storage; no model, private student, export, or EXE checks.",
                      "typography": typography_report(home), "captures": captures}
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()
    print(json.dumps({"output": str(output), "captures": len(captures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Capture a synthetic import failure; no credentials, personal files or API."""

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QLabel

from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_visual_import_ui import (
    _Facade,
    _manual_task_bridge,
    _receipt,
    _visual_profile,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=800)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve prior evidence; choose a fresh path.")
    app = create_application([])
    install_font_fallbacks()
    message = (
        "服务方拒绝了识别请求。请核对接口格式、模型名称及图片/结构化输出支持；"
        "不能仅据此判断是密钥或多模态能力的问题。原件仍保留；修改配置后重新预览，"
        "不会自动重试。"
    )
    receipt = replace(_receipt(status="failed", visual_status="failed"), message_zh=message)
    facade = _Facade(profiles=(_visual_profile(),), resumable=(receipt,))
    tasks = _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)
    dialog.setWindowTitle("合成界面演示 · 未调用模型")
    dialog.findChild(QLabel, "PageSubtitle").setText("合成界面演示，不含真实资料；本截图没有调用模型。")
    dialog.resize(args.width, args.height)
    dialog.show()
    dialog.resume_button.click()
    for _ in range(30):
        app.processEvents()
    assert message in dialog.status.text()
    assert not facade.run_calls and not tasks.pending
    assert dialog.status.isVisible()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    assert dialog.grab().save(str(args.output))
    print(f"Synthetic UI capture: {args.output}")
    dialog.close()


if __name__ == "__main__":
    main()

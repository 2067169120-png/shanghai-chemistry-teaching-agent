"""Synthetic model-budget UI evidence; no personal profile, key or API."""

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, QTimer, Signal

from integrations.deeptutor_shchem_v1.desktop_facade import ProviderProfileSummary
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import SettingsDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)


class SyntheticFacade:
    def list_provider_profiles(self):
        return (
            ProviderProfileSummary(
                profile_id="desktop-default",
                provider_name="DeepSeek（合成演示，无 API 调用）",
                base_url="https://api.deepseek.com",
                model_id="deepseek-v4-flash-vision-exp",
                api_style="responses",
                capabilities=("text", "vision"),
                key_saved=False,
                revision="synthetic-budget-ui-only",
                max_input_tokens=64000,
                max_output_tokens=50000,
            ),
        )

    def save_provider_profile(self, _request):
        raise AssertionError("This fixture must never save a profile.")

    def test_provider_connection(self, *_args, **_kwargs):
        raise AssertionError("This fixture must never call a provider.")


class FixtureTasks(QObject):
    task_finished = Signal(str)

    def submit(self, _label, operation, *, on_success, on_failure):
        task_id = "synthetic-profile-load"

        def complete():
            on_success(operation())
            self.task_finished.emit(task_id)

        QTimer.singleShot(0, complete)
        return task_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=860)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve existing screenshot evidence; choose a fresh output.")
    app = create_application([])
    install_font_fallbacks()
    tasks = FixtureTasks()
    dialog = SettingsDialog(SyntheticFacade(), tasks)
    dialog.resize(args.width, args.height)
    dialog.show()
    for _ in range(100):
        app.processEvents()
        if dialog._active_task_id is None:
            break
    assert dialog._active_task_id is None
    assert dialog.max_input_tokens.text() == "64000"
    assert dialog.max_output_tokens.text() == "50000"
    assert "32,000" in dialog.budget_reference.text()
    assert not dialog.key_input.text()
    assert not dialog.test_button.isEnabled()
    dialog.status.setText(
        "纯合成界面演示：输出 50,000 高于参考 32,000，仍可编辑；"
        "不表示服务商支持该额度。未读取或保存真实配置，未调用 API。"
    )
    app.processEvents()
    for button in (dialog.save_button, dialog.test_button, dialog.close_button):
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    assert dialog.grab().save(str(args.output))
    print(f"Synthetic Qt capture: {args.output}")
    dialog.reject()


if __name__ == "__main__":
    main()

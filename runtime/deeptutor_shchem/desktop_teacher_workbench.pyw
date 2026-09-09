from __future__ import annotations

import ctypes
import os
import sys
import traceback
from pathlib import Path


def _show_startup_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "沪上化学智研台", 0x10)
    except Exception:
        pass


def _write_startup_error() -> None:
    """Keep a small local error log so a windowed build never fails silently."""

    try:
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            return
        target = Path(base) / "ShanghaiChem" / "DesktopWorkbench" / "startup-error.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    workspace_root = Path(__file__).resolve().parents[2]
    if str(workspace_root) not in sys.path:
        sys.path.insert(0, str(workspace_root))
    try:
        from integrations.deeptutor_shchem_v1.desktop_workbench import (
            run_desktop_workbench,
        )
    except ModuleNotFoundError as exc:
        _write_startup_error()
        if exc.name and exc.name.startswith("PySide6"):
            _show_startup_error(
                "尚未安装桌面界面依赖。请按 desktop_requirements.txt 准备独立环境后重试。"
            )
        else:
            _show_startup_error("桌面运行依赖不完整，应用无法启动。")
        return 2
    except Exception:
        _write_startup_error()
        _show_startup_error("桌面应用加载失败，请检查本地安装。")
        return 3
    try:
        return run_desktop_workbench()
    except Exception:
        _write_startup_error()
        _show_startup_error("桌面应用运行失败，请查看本地启动日志。")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

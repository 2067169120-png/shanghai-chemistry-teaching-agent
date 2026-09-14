from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from ..desktop_facade import build_default_facade
from ..desktop_paths import DesktopPathError, DesktopPaths
from ..desktop_version import DESKTOP_VERSION
from .main_window import WORKBENCH_STYLE, TeacherWorkbenchWindow


class DesktopInstanceLockError(RuntimeError):
    """A second desktop process or unusable local state lock."""

    def __init__(self, message: str, *, already_running: bool) -> None:
        super().__init__(message)
        self.already_running = already_running


def _acquire_desktop_instance_lock(state_root: Path) -> QLockFile:
    try:
        state_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise DesktopInstanceLockError(
            "无法准备个人数据目录，请检查当前用户的本地目录权限。",
            already_running=False,
        ) from None
    lock = QLockFile(str(state_root / ".desktop-workbench.lock"))
    if lock.tryLock(0):
        return lock
    if lock.error() == QLockFile.LockError.LockFailedError:
        raise DesktopInstanceLockError(
            "沪上化学智研台已经在运行，请切换到现有窗口。",
            already_running=True,
        )
    raise DesktopInstanceLockError(
        "无法取得个人数据目录的独占锁，请检查目录权限后重试。",
        already_running=False,
    )


def create_application(argv: list[str] | None = None) -> QApplication:
    application = QApplication.instance() or QApplication(argv or sys.argv)
    application.setApplicationName("沪上化学智研台")
    application.setApplicationDisplayName("沪上化学智研台")
    application.setOrganizationName("ShanghaiChem")
    application.setApplicationVersion(DESKTOP_VERSION)
    application.setQuitOnLastWindowClosed(True)
    # Qt's Fusion indicators remain legible under the shared stylesheet,
    # including combobox arrows and spin controls on Windows CI and desktops.
    application.setStyle("Fusion")
    application.setFont(QFont("Microsoft YaHei UI", 10))
    application.setStyleSheet(WORKBENCH_STYLE)
    return application


def run_desktop_workbench(
    argv: list[str] | None = None,
    *,
    workspace_root: str | Path | None = None,
) -> int:
    application = create_application(argv)
    try:
        paths = (
            DesktopPaths.from_workspace(workspace_root)
            if workspace_root is not None
            else DesktopPaths.discover(Path(__file__))
        )
    except DesktopPathError as exc:
        QMessageBox.critical(None, "无法启动", str(exc))
        return 2
    arguments = list(sys.argv if argv is None else argv)
    if "--personal-state" in arguments:
        try:
            from ..desktop_backup import restored_profile
            index = arguments.index("--personal-state")
            state_root = restored_profile(arguments[index + 1])
            paths = DesktopPaths.from_workspace(paths.workspace_root, state_root=state_root)
            # A restored window must not silently use the source checkout's old library.
            paths = replace(paths, shchem_root=state_root / "library" / "sh-chem-db")
        except (ValueError, IndexError, OSError):
            QMessageBox.critical(None, "无法打开恢复副本", "请选择通过本工作台校验恢复的独立目录。")
            return 2
    try:
        instance_lock = _acquire_desktop_instance_lock(paths.state_root)
    except DesktopInstanceLockError as exc:
        if exc.already_running:
            QMessageBox.information(None, "已经打开", str(exc))
            return 0
        QMessageBox.critical(None, "无法启动", str(exc))
        return 3
    try:
        try:
            facade = build_default_facade(paths)
        except Exception:  # noqa: BLE001 - native startup safety boundary
            QMessageBox.critical(
                None, "无法启动", "本地工作台初始化失败，请检查资料目录与个人数据目录。",
            )
            return 3
        window = TeacherWorkbenchWindow(facade)
        window.show()
        return application.exec()
    finally:
        instance_lock.unlock()


__all__ = ["create_application", "run_desktop_workbench"]

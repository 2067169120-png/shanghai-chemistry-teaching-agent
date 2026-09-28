"""A fresh Qt process and isolated empty workspace; never the teacher's state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from threading import Event
from time import monotonic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--close-after-ms", type=int, default=0)
    args = parser.parse_args()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow
    from integrations.deeptutor_shchem_v1.reader_cancellation import check_read_cancelled
    from .tests.no_office_plugin import block_office_activation

    started = monotonic()
    def mark(stage, **fields):
        print(json.dumps({"stage": stage, "seconds": monotonic()-started, **fields}), flush=True)

    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix="shchem-qt-exit-") as temporary, block_office_activation():
        root = Path(temporary)
        (root/"workspace/sh-chem-db").mkdir(parents=True)
        facade = build_default_facade(DesktopPaths.from_workspace(root/"workspace", state_root=root/"state"))
        window = TeacherWorkbenchWindow(facade)
        window.show()
        def reader():
            pause = Event()
            while True:
                check_read_cancelled()
                pause.wait(.005)
        window.tasks.submit("合成关闭验收读取", reader)
        def close():
            mark("close_requested")
            assert window.close(), "Synthetic untouched window unexpectedly refused close"
            mark("close_returned", active_threads=window.tasks._pool.activeThreadCount())
            app.quit()
        QTimer.singleShot(max(0, args.close_after_ms), close)
        QTimer.singleShot(15000, lambda: app.exit(3))
        code = app.exec()
        mark("event_loop_returned")
        return code


if __name__ == "__main__":
    raise SystemExit(main())

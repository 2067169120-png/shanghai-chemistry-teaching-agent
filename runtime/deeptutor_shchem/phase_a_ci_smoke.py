"""Windows source smoke: normal close and abrupt-process restart, never user data."""
from __future__ import annotations
import argparse
import faulthandler
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "staging/coordination/deeptutor_gateway/tests")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def run_child(mode, state, output):
    faulthandler.enable(all_threads=True)
    def mark(stage):
        with (output / (mode + "-stages.log")).open("a", encoding="utf-8") as handle:
            handle.write(stage + "\n")
            handle.flush()
    output.mkdir(parents=True, exist_ok=True)
    mark("imports")
    from PySide6.QtCore import qVersion
    from PIL import Image
    from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow, ALL_ROUTES
    from integrations.deeptutor_shchem_v1.desktop_version import DESKTOP_VERSION
    from test_phase_a_core import saved_state
    output.mkdir(parents=True, exist_ok=True)
    mark("create_application")
    app = create_application(["phase-a-smoke"])
    mark("application_created")
    errors = []
    def failure(kind, value, tb):
        errors.append(kind.__name__)
        traceback.print_exception(kind, value, tb)
    sys.excepthook = failure
    facade = build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=state))
    mark("create_window")
    window = TeacherWorkbenchWindow(facade)
    mark("window_created")
    window.setWindowTitle("沪上化学智研台 · 0.1.86合成测试")
    window.resize(1366, 920)
    window.show()
    page = window.preparation_page
    window.navigate("preparation")
    def settle(predicate=lambda: True):
        end = time.monotonic() + 20
        for _ in range(4):
            app.processEvents(); time.sleep(.04)
        while not predicate() and time.monotonic() < end:
            app.processEvents(); time.sleep(.04)
        assert predicate() and not errors, errors
    def capture(name):
        settle()
        assert window.grab().save(str(output / name))
    settle(lambda: not window.home_page._loading)
    mark("window_ready")
    expected_file = state / "expected-editor.json"
    if mode == "abrupt-write":
        page.apply_studio_template("concept", "合成恢复示例：动态平衡", "高二 · 合成演示")
        page.materials.setPlainText("合成材料；保留条件、SO₄²⁻和公共图。\n本例没有真实学生与教材数据。")
        page.lesson_count.setValue(2)
        page.lesson_minutes.setValue(45)
        page.objective.setPlainText("")  # incomplete inputs must still be recoverable
        image = state / "synthetic-reference.png"
        Image.new("RGB", (64, 40), "white").save(image)
        asset = facade.import_preparation_image(str(image), "合成参考图", "测试本机生成", "公共材料")
        page.image_assets_widget.set_assets([asset])
        mark("form_filled")
        expected_file.write_text(json.dumps(page._payload(), ensure_ascii=False), encoding="utf-8")
        page.recovery.tick()
        settle(lambda: not page.recovery._inflight and page.recovery.store.path.exists())
        assert not facade.state_store.snapshot()["drafts"]
        assert page.recovery.store.load()["payload"] == page._payload()
        mark("autosave_verified")
        (state / "ready-to-terminate").write_text("saved", encoding="ascii")
        # Parent terminates this live process, bypassing Qt and Python teardown.
        app.exec()
        raise RuntimeError("The parent should terminate this live editor")
    mark("check_recovered_form")
    expected = json.loads(expected_file.read_text(encoding="utf-8"))
    assert page._payload() == expected
    assert page.recovery.store.load()["payload"] == expected
    assert "已恢复" in page.recovery.status.text()
    if mode == "normal-reopen":
        capture("recovery-normal-reopen.png")
        window.close()
        mark("normal_reopen_verified")
        return
    capture("recovery-restored.png")
    saved_state(facade.paths.state_root, 501)
    window.navigate("mywork")
    settle(lambda: not window.my_work_page._loading)
    works = window.my_work_page
    assert works._total == 501 and works.results.count() == 25
    capture("work-search-all.png")
    works.query.setText("课题-0000")
    settle(lambda: not works._loading)
    assert works._total == 1
    capture("work-search-oldest.png")
    for route in ALL_ROUTES:
        window.navigate(route)
        settle()
        assert window.stack.currentWidget() is window.pages[route]
    # Final text is newer than the last autosave; close must flush it.
    page.materials.setPlainText(expected["materials"] + "\n正常退出前的最后修改。")
    expected_file.write_text(json.dumps(page._payload(), ensure_ascii=False), encoding="utf-8")
    assert window.close()
    mark("normal_close_flushed")
    report = {"version": DESKTOP_VERSION, "source_commit": os.environ.get("SHCHEM_SOURCE_SHA", "local"),
              "platform": platform.platform(), "python": platform.python_version(), "qt": qVersion(),
              "routes_opened": list(ALL_ROUTES), "uncaught_errors": errors,
              "drafts_searched": 501, "matched_oldest": 1,
              "scope": "Native Qt source with isolated synthetic records. Abrupt-process recovery and normal-close flush verified. No provider calls, original library changes, packaged EXE or classroom acceptance."}
    (output / "phase-a-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["abrupt-write", "recover-check", "normal-reopen"])
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    output = ROOT / "phase-a-qa"
    if args.mode:
        run_child(args.mode, args.state, output)
        return
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shchem-recovery-ci-") as name:
        for mode in ("abrupt-write", "recover-check", "normal-reopen"):
            log_path = output / (mode + "-process.log")
            with log_path.open("wb") as log:
                command = [sys.executable, "-X", "faulthandler", __file__, "--mode", mode, "--state", name]
                if mode == "abrupt-write":
                    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                    try:
                        end = time.monotonic() + 40
                        while not (Path(name) / "ready-to-terminate").exists() and process.poll() is None and time.monotonic() < end:
                            time.sleep(.05)
                        if process.poll() is not None or not (Path(name) / "ready-to-terminate").exists():
                            raise RuntimeError("Editor failed before checkpoint: " + log_path.read_text(encoding="utf-8", errors="replace")[-5000:])
                        process.kill()
                        process.wait(timeout=10)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=10)
                else:
                    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=100)
                    if result.returncode:
                        raise RuntimeError(mode + " failed: " + log_path.read_text(encoding="utf-8", errors="replace")[-5000:])
    report_path = output / "phase-a-smoke.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["normal_reopen_verified"] = True
    report["abrupt_restart_verified"] = True
    report["termination_method"] = "Parent process.kill after saved checkpoint, without child closeEvent or runtime teardown"
    report["screenshots"] = [{"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(output.glob("*.png"))]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

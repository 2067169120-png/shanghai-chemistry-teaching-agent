"""Task-bound instrumented Qt QA of a real restored course, never normal startup.

This module is a candidate until Root approves a new frozen package. It uses the
real default facade, production state/recovery/task bridge and actual widgets.
Its offscreen Qt interactions are evidence of instrumented packaged QA only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
from threading import Event, Thread

AUDIT = Path(r"E:\任务输出\上海高中化学等级考\2026-10-12-exe-full-function-audit")
DATA = Path(r"E:\项目\上海高中化学等级考")
INPUT = AUDIT / "private-userdata/workflows-actual-r1-v2/root4-restored"
SOURCE = DATA / "outputs/2026-10-11-direct-section-and-course-reuse/course-reuse/actual-native-v1/main-restored"
SCHEMA = "shchem.real-profile-workflow-qa.v1"
FIELDS = {"schema_version", "run_id", "workspace_root", "profile_root", "output_root",
          "restore_parent", "temp_root", "settings_base", "expected_exe_sha256",
          "initial_main_output_id", "initial_expected_materials", "initial_expected_assets",
          "run_source_first"}


def _path(value):
    if not isinstance(value, str) or not value:
        raise ValueError("QA路径必须是明确的本机绝对路径。")
    result = Path(value)
    if not result.is_absolute():
        raise ValueError("QA路径不能使用相对路径。")
    return result


def _plain(path):
    """Reject symlink/junction components and hardlinked existing files."""
    for item in (path, *path.parents):
        if item.exists():
            stat = item.lstat()
            if item.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                raise ValueError("QA路径不能通过符号链接或目录连接别名写入。")
            if item.is_file() and stat.st_nlink != 1:
                raise ValueError("QA不能写入或读取硬链接别名的状态文件。")


def validate_config(value):
    """Pure routing validation; never constructs Qt, a facade or a provider."""
    if not isinstance(value, dict) or set(value) != FIELDS or value["schema_version"] != SCHEMA:
        raise ValueError("QA配置字段或schema不正确。")
    run = value["run_id"]
    if not isinstance(run, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", run):
        raise ValueError("QA run_id不正确。")
    expected = {
        "workspace_root": DATA,
        "profile_root": INPUT,
        "output_root": AUDIT / ("packaged-workflows/qa-r2-" + run),
        "restore_parent": AUDIT / ("private-userdata/workflows-r2-qa-restores/" + run),
        "settings_base": AUDIT / ("private-userdata/workflows-r2-qa-settings/" + run),
        "temp_root": Path(r"E:\临时文件\UserTemp\shchem-exe-audit-20261012\workflow") / ("qa-r2-" + run),
    }
    paths = {}
    for name, canonical in expected.items():
        path = _path(value[name])
        if path != canonical or path.resolve() != canonical.resolve():
            raise ValueError("QA仅允许本任务明确归属的输入与新OWN命名空间。")
        _plain(path)
        paths[name] = path
    for name in ("output_root", "restore_parent", "settings_base", "temp_root"):
        if paths[name].exists():
            raise ValueError("QA新命名空间已存在，不能覆盖或混用历史运行。")
    digest = value["expected_exe_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("QA必须绑定真实新包的完整SHA256。")
    if (not isinstance(value["initial_main_output_id"], str)
            or not re.fullmatch(r"OUT-[0-9a-f]{32}", value["initial_main_output_id"])):
        raise ValueError("QA必须绑定已实证的新六文件版本。")
    if value["initial_expected_materials"] != 13 or type(value["initial_expected_materials"]) is not int:
        raise ValueError("本次真实主稿必须为13素材。")
    if value["initial_expected_assets"] != 11 or type(value["initial_expected_assets"]) is not int:
        raise ValueError("本次真实主稿必须为11资产。")
    if type(value["run_source_first"]) is not bool:
        raise ValueError("run_source_first必须是布尔值。")
    return paths


def _record(path):
    stat = path.stat()
    return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(),
            "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _load_config(path):
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise ValueError("QA配置有重复字段。")
            value[key] = item
        return value
    def constant(_):
        raise ValueError("QA配置不能使用非有限数字。")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def validate_execution_scope(value, *, source_mode=False, source_checkpoint=False):
    if type(source_mode) is not bool or type(source_checkpoint) is not bool:
        raise ValueError("执行模式不能使用模糊布尔值。")
    if source_checkpoint and not source_mode:
        raise ValueError("源模式检查点不能缩短真实冻结EXE验收。")
    if not source_mode and value["run_source_first"] is not True:
        raise ValueError("本任务正式冻结EXE必须执行完整SourceFirst，不允许跳过后PASS。")


def _origins(payload):
    return {row["id"]: {"original": row["original"], "original_sha256": row["original_sha256"],
            "block_ID_kind_roles": [(b["id"], b["kind"], b["role"]) for b in row["blocks"]]}
            for row in payload["lesson_design"]["composition"]["resources"]}


_B37_SOURCE_SHA = "01f91a44ebbd0376c857cdbf8c5a186764a36f4a011c327a724b037c7c4f9c33"
_B37_RAW = "2.结构(以\U00100c37CH_{2}—CH_{2}\U00100c3b为例)"
_B37_WORKING = "2.结构（以聚乙烯为例）：[—CH₂—CH₂—]ₙ，n表示聚合度。"


def _b37_working_contract(before, main_input, source_sha):
    """One explicit, previously saved teaching edit; never infer raw PUA text."""
    if source_sha != _B37_SOURCE_SHA:
        raise ValueError("B37工作修订只适用于本次已核讲义SHA。")
    resources = before["resources"]
    if len(resources) != 3 or [row["original"]["kind"] for row in resources] != [
            "textbook_image", "handout_knowledge", "complete_question"]:
        raise ValueError("B37工作修订必须位于确切三素材批次。")
    def selected(row):
        original = row["original"]
        if original["kind"] != "handout_knowledge":
            return False
        evidence = original["evidence"]
        section = evidence["section_snapshot"]["section"]
        return (evidence["source_sha256"] == source_sha
                and section["start"] == 34 and section["end"] == 47)
    choices = [(index, row) for index, row in enumerate(resources) if selected(row)]
    approved = [row for row in main_input["lesson_design"]["composition"]["resources"] if selected(row)]
    if len(choices) != 1 or len(approved) != 1:
        raise ValueError("待加入或已保存主稿中的34至47知识栏目不唯一。")
    index, resource = choices[0]
    blocks = [block for block in resource["blocks"] if block["kind"] == "text" and block["source_index"] == 37]
    saved = [block for block in approved[0]["blocks"] if block["kind"] == "text" and block["source_index"] == 37]
    if (len(blocks) != 1 or len(saved) != 1 or blocks[0]["value"] != _B37_RAW
            or saved[0]["value"] != _B37_WORKING or resource["locked"] is not True):
        raise ValueError("原B37或此前明确保存的教学修订不一致，未自动替换字形。")
    saved_original = [row for row in approved[0]["original"]["blocks"] if row["id"] == saved[0]["id"]]
    if (len(saved_original) != 1 or saved_original[0]["value"] != _B37_RAW
            or {key: value for key, value in saved[0].items() if key != "value"}
            != {key: value for key, value in saved_original[0].items() if key != "value"}):
        raise ValueError("已保存B37修订未保留确切原文、ID、角色或显示结构。")
    block = blocks[0]
    original_blocks = [row for row in resource["original"]["blocks"] if row["id"] == block["id"]]
    if len(original_blocks) != 1 or original_blocks[0] != block:
        raise ValueError("待编辑B37不是完整封存原区块的工作副本。")
    expected = deepcopy(before)
    expected["resources"][index]["locked"] = False
    next(row for row in expected["resources"][index]["blocks"] if row["id"] == block["id"])["value"] = _B37_WORKING
    return {"resource_index": index, "resource_id": resource["id"], "block_id": block["id"],
        "source_index": 37, "source_sha256": source_sha, "original_text": _B37_RAW,
        "working_text": _B37_WORKING, "original_sha256": resource["original_sha256"],
        "expected_after": expected}


def _check_b37_working_contract(contract, after):
    if after != contract["expected_after"]:
        raise ValueError("B37修订改变了原快照、角色、图片、排版或本批其他全文。")


def run_probe(config_path, *, source_mode=False, source_checkpoint=False):
    """source_mode exists only for explicitly labelled OWN candidate smoke."""
    config_path = Path(config_path)
    if not config_path.is_file() or config_path.stat().st_size > 64 * 1024:
        raise ValueError("QA配置文件缺失或过大。")
    if not config_path.resolve().is_relative_to((AUDIT / "packaged-workflows").resolve()):
        raise ValueError("QA配置也必须位于本任务OWN证据根。")
    _plain(config_path)
    value = _load_config(config_path)
    paths = validate_config(value)
    validate_execution_scope(value, source_mode=source_mode, source_checkpoint=source_checkpoint)
    frozen = bool(getattr(sys, "frozen", False))
    executable_sha = sha256(Path(sys.executable).read_bytes()).hexdigest()
    if not source_mode and (not frozen or executable_sha != value["expected_exe_sha256"]):
        raise ValueError("QA只能由SHA匹配的新冻结EXE执行；没有回退到普通启动。")
    output = paths["output_root"]
    for name in ("output_root", "restore_parent", "settings_base", "temp_root"):
        paths[name].mkdir(parents=True, exist_ok=False)
    os.environ["TEMP"] = os.environ["TMP"] = str(paths["temp_root"])
    os.environ["LOCALAPPDATA"] = str(paths["settings_base"])
    os.environ["SHCHEM_WORKSPACE_ROOT"] = str(paths["workspace_root"])
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QFileDialog
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    from .desktop_backup import restored_profile, plan_backup, inspect_backup
    from .desktop_facade import build_default_facade, DesktopWorkbenchFacade
    from .desktop_paths import DesktopPaths
    from .desktop_state import DesktopStateStore
    from .desktop_preparation_drafts import PreparationDraftService
    from .desktop_preparation_images import verify_image_bytes
    from .desktop_workbench.app import create_application, _acquire_desktop_instance_lock
    from .desktop_workbench.main_window import TeacherWorkbenchWindow
    from .desktop_workbench.lesson_design_dialog import LessonDesignDialog
    from .desktop_workbench.preparation_draft_dialog import PreparationDraftDialog
    from .desktop_workbench.backup_dialog import BackupDialog

    app = create_application(["instrumented-real-profile-workflow-qa"])
    errors, checks, windows, locks = [], {}, [], []
    stop_observer = Event()
    current_phase = {"value": "initializing"}
    old_hook = sys.excepthook

    def put(name, data):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def event(phase, **fields):
        current_phase["value"] = phase
        row = {"phase": phase, "time": time.time(), "monotonic": time.monotonic(), **fields}
        with (output / "phase-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(phase, flush=True)

    def capture(widget, name):
        path = output / (name + ".png")
        if not widget.grab().save(str(path)):
            raise ValueError("Qt QA截图未能保存。")
        put(name + ".json", {"evidence_scope": "instrumented_Qt_QA_widget_pixels",
            "window_title": widget.windowTitle(), "pid": os.getpid(), "image": _record(path)})

    def wait(label, predicate, *, ignore_errors=False):
        if errors and not ignore_errors:
            raise RuntimeError(errors[-1])
        tick = time.monotonic()
        while not predicate():
            app.processEvents()
            time.sleep(.01)
            if errors and not ignore_errors:
                raise RuntimeError(errors[-1])
            if time.monotonic() - tick >= 25:
                event("waiting", operation=label)
                tick = time.monotonic()
        for _ in range(3):
            app.processEvents()
            time.sleep(.01)
        if errors and not ignore_errors:
            raise RuntimeError(errors[-1])

    def click(button):
        if not button.isEnabled():
            raise ValueError("QA动作当前不可用：" + button.text())
        button.click()

    def modal(kind, operation):
        def find():
            active = app.activeModalWidget()
            if not isinstance(active, kind):
                if not errors:
                    QTimer.singleShot(10, find)
                return
            try:
                operation(active)
            except BaseException:
                errors.append(traceback.format_exc())
                capture(active, "failed-modal-" + kind.__name__)
                # Busy production dialogs can defer reject until cancellation
                # finishes. Re-attempt only this same owned dialog, never quit
                # Qt while its real reader/worker is still running.
                def finish_reject():
                    if active.isVisible():
                        active.reject()
                        if active.isVisible():
                            QTimer.singleShot(100, finish_reject)
                finish_reject()
        QTimer.singleShot(0, find)

    def directory_choice(directory):
        def choose(dialog):
            if dialog.fileMode() != QFileDialog.FileMode.Directory:
                raise ValueError("QA未出现预期的真实目录选择对话框。")
            dialog.setDirectory(str(directory))
            dialog.selectFile(str(directory))
            capture(dialog, "directory-choice-" + directory.name)
            dialog.accept()
        modal(QFileDialog, choose)

    def file_choice(path):
        def choose(dialog):
            dialog.setDirectory(str(path.parent))
            dialog.selectFile(str(path))
            capture(dialog, "file-choice-" + path.stem)
            dialog.accept()
        modal(QFileDialog, choose)

    def facade_for(profile):
        profile = restored_profile(profile)
        _plain(profile)
        lock = _acquire_desktop_instance_lock(profile)
        locks.append(lock)
        base = DesktopPaths.from_workspace(paths["workspace_root"])
        actual = replace(DesktopPaths.from_workspace(paths["workspace_root"], state_root=profile),
            shchem_root=profile / "library/sh-chem-db", settings_root=base.settings_root)
        facade = build_default_facade(actual)
        if type(facade) is not DesktopWorkbenchFacade or type(facade.state_store) is not DesktopStateStore:
            raise ValueError("QA必须使用真实默认facade与生产状态存储。")
        window = TeacherWorkbenchWindow(facade)
        def task_failed(identity, label, message):
            error = "生产异步任务失败：" + identity + " / " + label + " / " + message
            errors.append(error)
            event("owned-task-failed", task_id=identity, label=label, message=message)
        window.tasks.task_failed.connect(task_failed)
        window.tasks.task_cancelled_detail.connect(lambda identity, label, message:
            event("owned-task-cancelled", task_id=identity, label=label, message=message,
                  failed_scope_already_recorded=bool(errors)))
        windows.append(window)
        window.show()
        return facade, window

    def saved_open(page, identity, prefix):
        def choose(dialog):
            if not dialog.select_draft_id(identity):
                raise ValueError("真实草稿选择器未定位到所需ID。")
            capture(dialog, prefix + "-picker")
            click(dialog.load_button)
        modal(PreparationDraftDialog, choose)
        click(page.open_draft_button)
        if page._editor_draft_id != identity:
            raise ValueError("选择器未载入指定草稿。")
        capture(page, prefix + "-loaded")

    def saved_payload(facade, identity):
        option = facade.preparation_draft_option(identity)
        return facade.load_preparation_draft(identity, option["revision"])["payload"]

    def save_design(dialog, facade, prefix):
        before = set(facade.state_store.snapshot()["drafts"])
        click(dialog.save)
        wait(prefix + " save", lambda: not dialog.busy and not dialog._task)
        added = set(facade.state_store.snapshot()["drafts"]) - before
        if len(added) != 1:
            raise ValueError("正常保存没有形成唯一新草稿。")
        identity = added.pop()
        capture(dialog, prefix + "-saved")
        put(prefix + "-saved.json", {"draft_id": identity, "payload": saved_payload(facade, identity)})
        return identity

    def check_images(facade, payload):
        rows = []
        for asset in payload["image_assets"]:
            data = facade.preparation_image_bytes(asset)
            verify_image_bytes(asset, data)
            rows.append({"asset": asset, "bytes": len(data), "actual_sha256": sha256(data).hexdigest()})
        return rows

    def exception_hook(kind, exc, tb):
        errors.append("".join(traceback.format_exception(kind, exc, tb)))

    sys.excepthook = exception_hook
    put("process.json", {"pid": os.getpid(), "sys_executable": sys.executable,
        "actual_executable_sha256": executable_sha, "expected_package_sha256": value["expected_exe_sha256"],
        "frozen": frozen, "source_mode": source_mode, "package_binding_verified": frozen and not source_mode,
        "qt_platform": app.platformName(), "scope": "source_only_Qt_QA" if source_mode else "instrumented_packaged_Qt_QA",
        "config": value, "no_environment_dump": True, "external_model_boundary": "not_verified_external",
        "ordinary_manual_UI_claim": False, "Office_requested": False})
    result = {"status": "running", "checks": checks, "external_model_boundary": "not_verified_external"}
    code = 15
    protected = {str(p): _record(p) for p in (SOURCE / "desktop-state.v1.json", SOURCE / "restored-profile.json")}
    recovery_before = paths["profile_root"] / "recovery/preparation.v1.json"
    if not recovery_before.is_file():
        raise ValueError("真实生成后自动恢复稿缺失；没有填造替代payload。")
    put("input-recovery-preserved.json", json.loads(recovery_before.read_text(encoding="utf-8")))

    def observe_progress():
        while not stop_observer.wait(25):
            with (output / "progress-heartbeat.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": time.time(), "phase": current_phase["value"],
                    "pid": os.getpid(), "scope": "observation_only_no_timeout_restart"}) + "\n")
    observer = Thread(target=observe_progress, name="owned-qa-observer", daemon=True)
    observer.start()

    def drive():
        nonlocal code
        try:
            event("full-source-input-preflight")
            restored_profile(paths["profile_root"])
            for file in paths["profile_root"].rglob("*"):
                _plain(file)
            preflight = plan_backup(paths["profile_root"], include_images=True, include_tasks=True,
                                    include_source_dependencies=True)
            if preflight.summary["missing_files"] or preflight.summary["source_documents"] != 98:
                raise ValueError("真实98份完整来源/图片/成品预检未通过。")
            put("input-preflight.json", preflight.report())
            facade, window = facade_for(paths["profile_root"])
            page = window.preparation_page
            window.navigate("preparation")
            initial = deepcopy(page._payload())
            if (len(initial["lesson_design"]["composition"]["resources"]) != 13
                    or len(initial["image_assets"]) != 11
                    or initial["lesson_design"]["exports"][-1]["id"] != value["initial_main_output_id"]):
                raise ValueError("真实MainWindow没有自动恢复已生成13/11新六文件稿。")
            initial_origins = _origins(initial)
            put("initial-auto-recovered-payload.json", initial)
            put("initial-image-evidence.json", check_images(facade, initial))
            capture(window, "real-main-window")
            checks["real_default_factory_full_MainWindow"] = True
            checks["actual_generated_recovery_loaded"] = True
            main_saved = {}
            export_root = output / "primary-export"
            export_root.mkdir()

            def finish_main(dialog):
                if _origins(page._payload()) != initial_origins:
                    raise ValueError("打开设计改变了Original或角色。")
                main_saved["id"] = save_design(dialog, facade, "main")
                dialog.tabs.setCurrentIndex(2)
                if not dialog.classroom_notes.isChecked():
                    raise ValueError("真实课堂讲义选项未勾选。")
                dialog.outputs.setCurrentIndex(dialog.outputs.findData(value["initial_main_output_id"]))
                capture(dialog, "actual-existing-six-selected")
                directory_choice(export_root)
                click(dialog.export_primary)
                wait("primary three file export", lambda: not dialog.busy and not dialog._task)
                source = facade.paths.task_root / "preparation-v1/node-exports" / value["initial_main_output_id"]
                aliases = {"教案.docx": "lesson_plan.docx", "课堂讲义.docx": "student_worksheet.docx",
                           "课堂演示.pptx": "student_presentation.pptx"}
                copies = [p for p in export_root.iterdir() if p.is_dir()]
                if len(copies) != 1 or set(p.name for p in copies[0].iterdir()) != set(aliases):
                    raise ValueError("真实primary导出没有形成唯一三成品目录。")
                records = []
                for alias, native in aliases.items():
                    if sha256((copies[0] / alias).read_bytes()).hexdigest() != sha256((source / native).read_bytes()).hexdigest():
                        raise ValueError("primary导出字节与选中版本不一致。")
                    records.append(_record(copies[0] / alias))
                put("actual-primary-three-export.json", {"files": records, "real_button": True,
                    "real_Qt_FileDialog": True, "chooser_scope": "instrumented_non_native_Qt"})
                capture(dialog, "primary-export-finished")
                dialog.reject()

            event("save-generated-main-and-export-three")
            modal(LessonDesignDialog, finish_main)
            click(page.lesson_design_button)
            wait("main design return", lambda: not page.studio_busy())
            if errors:
                raise RuntimeError(errors[-1])
            main_payload = saved_payload(facade, main_saved["id"])
            saved_open(page, main_saved["id"], "main-normal-reopen")
            if page._payload() != main_payload or _origins(main_payload) != initial_origins:
                raise ValueError("新保存主稿chooser重开不一致。")
            checks["main_generated_version_formally_saved_and_reopened"] = True
            checks["primary_three_export_SHA_exact"] = True
            if source_checkpoint:
                result.update(status="SOURCE_ONLY_main_save_reopen_export_checkpoint_PASS",
                    main_saved_draft=main_saved["id"], package_binding_verified=False,
                    ordinary_manual_UI_claim=False, pending=["new SourceFirst", "full-source ZIP", "restore and second factory reopen"])
                code = 0
                event("source-only-checkpoint-complete")
                return
            source_saved = None
            source_payload = None
            if value["run_source_first"]:
                event("new-real-source-first")
                source_saved, source_payload = _source_first(app, window, facade, initial,
                    modal=modal, wait=wait, click=click, capture=capture, put=put, save_design=save_design,
                    saved_open=saved_open, check_images=check_images)
                checks["fresh_source_first_three_materials_seven_assets"] = True

            archive = output / "full-course-with-original-sources.zip"
            restored = {}

            def backup_and_restore(dialog):
                dialog.images.setChecked(True)
                dialog.outputs.setChecked(True)
                dialog.sources.setChecked(True)
                if Path(dialog.source_root.text()).resolve() != paths["profile_root"].resolve():
                    raise ValueError("备份来源目录不是本次OWN。")
                click(dialog.plan_button)
                wait("real backup plan", lambda: dialog._active is None)
                if dialog.plan is None:
                    raise ValueError("真实备份计划失败：" + dialog.status.text())
                if dialog.plan.summary["missing_files"] or dialog.plan.summary["source_documents"] != 98:
                    raise ValueError("含完整98来源备份计划未通过。")
                put("actual-backup-plan.json", dialog.plan.report())
                capture(dialog, "full-source-backup-plan")
                file_choice(archive)
                click(dialog.save_button)
                wait("real backup write", lambda: dialog._active is None)
                if not archive.is_file():
                    raise ValueError("真实备份写入失败：" + dialog.status.text())
                manifest = inspect_backup(archive)
                put("actual-backup-manifest.json", manifest)
                dialog.tabs.setCurrentIndex(1)
                file_choice(archive)
                click(dialog.inspect_button)
                wait("real backup inspect", lambda: dialog._active is None)
                if dialog.checked is None:
                    raise ValueError("真实备份检查失败：" + dialog.status.text())
                directory_choice(paths["restore_parent"])
                click(dialog.restore_button)
                wait("real backup restore", lambda: dialog._active is None)
                if dialog.restored_directory is None:
                    raise ValueError("真实恢复失败：" + dialog.status.text())
                restored["root"] = restored_profile(dialog.restored_directory)
                if not restored["root"].is_relative_to(paths["restore_parent"].resolve()):
                    raise ValueError("恢复结果离开本次OWN命名空间。")
                capture(dialog, "actual-production-restore-finished")
                put("actual-restore.json", {"root": str(restored["root"]), "marker": _record(restored["root"] / "restored-profile.json"),
                    "archive": _record(archive), "manual_new_Windows_window": False})
                dialog.reject()

            event("full-source-backup-and-production-restore")
            modal(BackupDialog, backup_and_restore)
            window.open_backup()
            if errors:
                raise RuntimeError(errors[-1])
            checks["full98_source_backup_and_actual_restore"] = True
            second_facade, second = facade_for(restored["root"])
            second.navigate("preparation")
            second_page = second.preparation_page
            saved_open(second_page, main_saved["id"], "restored-main")
            if second_page._payload() != main_payload or _origins(second_page._payload()) != initial_origins:
                raise ValueError("真实第二factory主稿重开不一致。")
            restored_images = check_images(second_facade, second_page._payload())
            put("restored-main-image-evidence.json", restored_images)
            for record in main_payload["lesson_design"]["exports"]:
                for file in record["files"]:
                    path = second_facade.paths.task_root / "preparation-v1/node-exports" / record["id"] / file["name"]
                    if sha256(path.read_bytes()).hexdigest() != file["sha256"]:
                        raise ValueError("恢复的成品字节不一致。")
            if source_saved is not None:
                saved_open(second_page, source_saved, "restored-source-first")
                if second_page._payload() != source_payload or _origins(second_page._payload()) != _origins(source_payload):
                    raise ValueError("恢复后SourceFirst稿字段或Original/roles不一致。")
                put("restored-source-first-image-evidence.json", check_images(second_facade, source_payload))
            checks["second_real_factory_chooser_reopen_full_payload_and_bytes"] = True
            if any(_record(Path(p)) != record for p, record in protected.items()):
                raise ValueError("只读ROOT4源状态或marker变化。")
            put("protected-ROOT4-state-after.json", protected)
            result.update(status="measured_chain_waiting_owned_shutdown", main_saved_draft=main_saved["id"],
                source_first_saved_draft=source_saved, restored_root=str(restored["root"]),
                ordinary_manual_UI_claim=False, chemistry_human_review=False, publication=False,
                same_machine_textbook_original_dependency=True)
            code = 0
            event("requested-QA-chain-measured-awaiting-shutdown")
        except BaseException:
            errors.append(traceback.format_exc())
            result.update(status="FAIL_partial_evidence_retained", errors=errors)
            event("failed", message=errors[-1])
        finally:
            put("final-report.json", result)
            # Wait only our real task bridges, and close only these QA windows.
            for window in reversed(windows):
                try:
                    wait("own task natural completion", lambda window=window: not window.tasks._workers,
                         ignore_errors=True)
                    closed = window.close()
                    if not closed or window.isVisible():
                        code = 15
                        errors.append("生产窗口拒绝关闭；保留实例锁等待安全关闭，不强制quit。")
                        result.update(status="FAIL_waiting_safe_owned_close", errors=errors)
                        put("final-report.json", result)
                        event("owned-close-refused", title=window.windowTitle())
                        # This is a safety wait, not a forced timeout restart.
                        # Retry only this owned window's production close path.
                        while window.isVisible():
                            app.processEvents()
                            time.sleep(.05)
                            window.close()
                        event("owned-close-eventually-accepted", title=window.windowTitle())
                    if window.tasks._workers or not window.tasks.is_shutting_down:
                        wait("own bridge remaining workers after accepted close",
                             lambda window=window: not window.tasks._workers, ignore_errors=True)
                        if not window.tasks.is_shutting_down:
                            raise ValueError("生产窗口未进入任务桥安全关闭状态。")
                except BaseException:
                    errors.append(traceback.format_exc())
                    code = 15
                    result.update(status="FAIL_waiting_verified_owned_shutdown", errors=errors)
                    put("final-report.json", result)
                    event("owned-shutdown-exception-retained", traceback=errors[-1])
                    # Keep this profile lock and Qt alive until production
                    # accepts a verified safe close. Never promote prior checks
                    # or force an unverified live worker out of the process.
                    while True:
                        try:
                            if not window.tasks._workers and window.close() and not window.isVisible() and window.tasks.is_shutting_down:
                                break
                        except BaseException:
                            pass
                        app.processEvents()
                        time.sleep(.1)
            if errors:
                code = 15
                result.update(status="FAIL_partial_evidence_retained", errors=errors)
                put("final-report.json", result)
            for lock in reversed(locks):
                lock.unlock()
            stop_observer.set()
            app.quit()

    QTimer.singleShot(0, drive)
    app.exec()
    stop_observer.set()
    observer.join(timeout=1)
    sys.excepthook = old_hook
    if errors:
        result.update(status="FAIL_partial_evidence_retained", errors=errors)
        put("final-report.json", result)
        return 15
    if not source_checkpoint:
        result.update(status="PASS_requested_instrumented_QA_scope" if not source_mode else "SOURCE_ONLY_requested_QA_scope_PASS",
                      package_binding_verified=frozen and not source_mode, ordinary_manual_UI_claim=False)
        put("final-report.json", result)
    return code


def _source_first(app, window, facade, main_input, *, modal, wait, click, capture, put,
                  save_design, saved_open, check_images):
    """Exact existing section/source UI route; no injected source adapters."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox, QDialogButtonBox
    from .desktop_workbench.section_materials_dialog import SectionMaterialsDialog
    from .desktop_workbench.textbook_preparation_crop_dialog import TextbookPreparationCropDialog
    from .desktop_workbench.handout_material_workspace import HandoutMaterialWorkspace
    from .desktop_workbench.handout_knowledge_picker import HandoutKnowledgePicker
    from .desktop_workbench.word_question_dialog import WordQuestionDialog
    from .desktop_workbench.pending_material_editor import PendingMaterialEditor
    from .desktop_workbench.lesson_design_dialog import LessonDesignDialog
    from .desktop_lesson_design import validate_design
    from .desktop_textbook_crop_reading import textbook_crop_reader
    from .desktop_handout_questions import HandoutQuestionFacade
    source_id = "SRC-HANDOUT-072-01f91a44ebbd0376"
    source_sha = "01f91a44ebbd0376c857cdbf8c5a186764a36f4a011c327a724b037c7c4f9c33"
    question_key = "word-question-9a95412ff322252f7106ae9b"
    box = {}
    def confirm_blank(dialog):
        if dialog.windowTitle() != "新建空白备课" or "已保存的正式草稿、图片文件和题篮不变" not in dialog.text():
            raise ValueError("新建备课出现非预期确认，不自动同意。")
        capture(dialog, "new-blank-real-confirmation")
        click(dialog.button(QMessageBox.StandardButton.Yes))
    modal(QMessageBox, confirm_blank)
    window.new_preparation()
    page = window.preparation_page
    blank = page._payload()
    if (blank.get("image_assets") or (blank.get("lesson_design") or {}).get("composition", {}).get("resources")
            or page._editor_draft_id is not None):
        raise ValueError("真实新建槽没有清空本次编辑器，拒绝把旧课当新SourceFirst。")
    page.topic.setText(main_input["topic"])
    page.audience.setText(main_input["audience"])
    page.route.setCurrentText(main_input["lesson_route"])
    page.lesson_count.setValue(2)
    page.lesson_minutes.setValue(40)
    page.objective.setPlainText(main_input["objective"])
    page.materials.setPlainText(main_input["materials"])
    window.navigate("textbooks")
    shelf = window.textbook_page
    shelf.refresh()
    wait("real textbook shelf", lambda: shelf._snapshot is not None and not shelf._loading)
    shelf.book.setCurrentIndex(shelf.book.findData("TB-E3"))

    def descendants(item):
        yield item
        for index in range(item.childCount()):
            yield from descendants(item.child(index))
    rows = [row for index in range(shelf.tree.topLevelItemCount())
            for row in descendants(shelf.tree.topLevelItem(index))]
    section = next(row for row in rows if row.data(0, Qt.ItemDataRole.UserRole) == "TB-E3-C4:4.2")
    shelf.tree.setCurrentItem(section)
    shelf.set_preparation_context(page._payload())

    def crop(dialog):
        wait("actual PDF95 source page", lambda: dialog._page is not None and not dialog._busy)
        if dialog._page["width"] != 1985 or dialog._page["height"] != 2807:
            raise ValueError("已核教材原页尺寸变化。")
        for key, number in (("left", 190), ("top", 1060), ("right", 1810), ("bottom", 2310)):
            dialog.edges[key].setValue(number)
        dialog.caption.setText("这三种缩聚产物分别来自哪些单体？")
        dialog.purpose.setText("观察完整表4.2的结构、名称与应用，保留整表概览。")
        click(dialog.preview_button)
        wait("actual crop preview", lambda: dialog._preview is not None and not dialog._busy)
        box["page"] = deepcopy(dialog._page)
        dialog.checked.setChecked(True)
        click(dialog.confirm)

    def knowledge(picker):
        wait("actual 34-47 knowledge outline", lambda: picker._outline is not None and not picker._jobs)
        item = next(picker.sections.item(i) for i in range(picker.sections.count())
            if picker.sections.item(i).data(Qt.ItemDataRole.UserRole)["start"] == 34
            and picker.sections.item(i).data(Qt.ItemDataRole.UserRole)["end"] == 47)
        picker.sections.setCurrentItem(item)
        picker.title.setText("从链节理解高分子的结构与分类")
        item.setCheckState(Qt.CheckState.Checked)
        click(picker.confirm)
        wait("actual knowledge confirmation", lambda: picker.requests is not None)

    def selected_lecture(workspace):
        wait("real lecture metadata", lambda: bool(workspace._sources) and not workspace._jobs)
        index = next(i for i in range(workspace.source.count())
            if isinstance(workspace.source.itemData(i), dict) and workspace.source.itemData(i).get("source_id") == source_id)
        workspace.source.setCurrentIndex(index)
        wait("explicit full lecture outline", lambda: workspace._document is not None and not workspace._jobs)
        if workspace._document["source_sha256"] != source_sha or len(workspace._outline["preview"]["blocks"]) != 567:
            raise ValueError("完整讲义来源或567原块变化。")
        modal(HandoutKnowledgePicker, knowledge)
        click(workspace.knowledge_button)
        wait("selected knowledge returned", lambda: workspace._child is None and len(workspace._selections) == 1)
        click(workspace.confirm)
        wait("real lecture material confirmation", lambda: workspace.request is not None)

    def question(dialog):
        wait("native source-only Word question catalog", lambda: dialog._loaded_once and not dialog._catalog_busy)
        if (type(dialog.facade) is not HandoutQuestionFacade or not dialog._selection_only
                or dialog._handoff_mode != "composition" or dialog._selection_source != box["document"]
                or dialog.source_combo.count() != 1 or len(dialog._items) != 24
                or dialog._persist_choices or dialog._allow_knowledge_links
                or {row["source_sha256"] for row in dialog._items.values()} != {source_sha}):
            raise ValueError("真实生产单讲义24题范围未通过。")
        click(dialog.all_questions_button)
        wait("exact full question item", lambda: any(dialog.question_list.item(i).data(Qt.ItemDataRole.UserRole) == question_key
                                                    for i in range(dialog.question_list.count())))
        item = next(dialog.question_list.item(i) for i in range(dialog.question_list.count())
                    if dialog.question_list.item(i).data(Qt.ItemDataRole.UserRole) == question_key)
        dialog.question_list.setCurrentItem(item)
        item.setCheckState(Qt.CheckState.Checked)
        click(dialog.preview_button)
        wait("complete question preview", lambda: dialog._preview_reference is not None and dialog.composition_button.isEnabled())
        row = dialog._items[question_key]
        if ([b["index"] for b in row["question_blocks"]] != list(range(350, 356))
                or [b["index"] for b in row["answer_blocks"]] != list(range(356, 362))
                or not all(any(b["text"].startswith(prefix) for b in row["question_blocks"]) for prefix in ("A．", "B．", "C．", "D．"))
                or not any(b["text"] == "【答案】AB" for b in row["answer_blocks"])):
            raise ValueError("原完整题答范围或选项变化。")
        put("actual-source-only-question-reference.json", dialog._preview_reference)
        preview_assets = dialog._preview_reference["image_assets"]
        if len(preview_assets) != 1 or preview_assets[0]["content_type"] != "image/png":
            raise ValueError("已核一张包含三结构的原PNG身份或MIME变化。")
        capture(dialog, "actual-source-only-24-question-preview")
        click(dialog.composition_button)
        wait("full question native selection result", lambda: dialog.selection_result is not None)

    def source_first(workspace):
        wait("source-first metadata only", lambda: bool(workspace._sources) and not workspace._jobs)
        if (not workspace._questions_only or workspace.source.currentData() is not None
                or workspace._document is not None or workspace._outline is not None
                or workspace._selections or workspace.questions_button.isEnabled()):
            raise ValueError("SourceFirst起始并非无自动来源读取。")
        capture(workspace, "source-first-unselected-metadata")
        index = next(i for i in range(workspace.source.count())
            if isinstance(workspace.source.itemData(i), dict) and workspace.source.itemData(i).get("source_id") == source_id)
        workspace.source.setCurrentIndex(index)
        wait("source-first explicit lecture", lambda: workspace._document is not None and not workspace._jobs)
        box["document"] = deepcopy(workspace._document)
        if box["document"]["source_sha256"] != source_sha or not Path(box["document"]["source_path"]).is_relative_to(facade.paths.state_root):
            raise ValueError("SourceFirst未从本次OWN真实归档读取。")
        modal(WordQuestionDialog, question)
        click(workspace.questions_button)
        wait("source-first question selected", lambda: workspace._child is None and len(workspace._selections) == 1 and not workspace._jobs)
        click(workspace.confirm)
        wait("source-first confirmed", lambda: workspace.request is not None)
        if workspace.request["knowledge_requests"] or workspace.request["image_selections"] or len(workspace.request["question_selections"]) != 1:
            raise ValueError("SourceFirst混入未选知识或图片。")

    def section_dialog(dialog):
        wait("real section without knowledge card", lambda: dialog._section_loaded and dialog._active is None)
        if dialog.knowledge_list.currentRow() != -1 or dialog._selection is not None or dialog._card is not None:
            raise ValueError("本节入口隐式选择了知识卡。")
        index = next(i for i in range(dialog.image_list.count())
            if dialog.image_list.item(i).data(Qt.ItemDataRole.UserRole)["selection"]["visual_asset_id"].startswith("SECTIONPAGE-")
            and dialog.image_list.item(i).data(Qt.ItemDataRole.UserRole).get("pdf_page") == 95)
        dialog.image_list.setCurrentRow(index)
        modal(TextbookPreparationCropDialog, crop)
        click(dialog.crop_button)
        wait("one crop queued", lambda: len(dialog._queued_requests) == 1 and dialog._active is None)
        modal(HandoutMaterialWorkspace, selected_lecture)
        click(dialog.browse_handouts_button)
        wait("knowledge network queued", lambda: len(dialog._queued_requests) == 2 and dialog._active is None)
        modal(HandoutMaterialWorkspace, source_first)
        click(dialog.browse_questions_button)
        wait("source-only question queued", lambda: len(dialog._queued_requests) == 3 and dialog._active is None)
        if any(row["selection_scope"] != "section" or {"knowledge_selection", "knowledge_card", "knowledge_original"} & row.keys()
               for row in dialog._queued_requests):
            raise ValueError("本节队列混入隐式知识锚点。")
        put("actual-three-material-requests.json", dialog._queued_requests)
        capture(dialog, "actual-three-material-queue")
        def confirm_pending(pending):
            # Use the existing teacher work-copy UI. The exact original PUA
            # stays sealed; reuse only the already saved B37 teaching edit.
            from .desktop_workbench.lesson_composition_panel import ResourceEditor
            capture(pending, "actual-pending-three-materials")
            contract = _b37_working_contract(deepcopy(pending.value), main_input, source_sha)
            pending.resources.setCurrentRow(contract["resource_index"])
            def edit_work_copy(editor):
                if editor.identity != contract["resource_id"] or not editor.locked.isChecked():
                    raise ValueError("待加入工作副本编辑器不是所选知识段。")
                click(editor.locked)
                index = editor.blocks.findData(contract["block_id"])
                if index < 0:
                    raise ValueError("工作正文选择器缺少精确B37区块ID。")
                editor.blocks.setCurrentIndex(index)
                if (editor.blocks.currentData() != contract["block_id"] or editor.body.isReadOnly()
                        or editor.body.source_text() != contract["original_text"]):
                    raise ValueError("未从精确原B37正常解锁工作正文。")
                editor.body.set_source_text(contract["working_text"])
                capture(editor, "actual-source-first-b37-working-edit")
                click(editor.apply_button)
                if editor.result_value is None:
                    raise ValueError("B37工作副本修订未应用。")
            modal(ResourceEditor, edit_work_copy)
            click(pending.edit_button)
            after = deepcopy(pending.value)
            _check_b37_working_contract(contract, after)
            box["expected_working_resources"] = deepcopy(after["resources"])
            box["b37_working_repair"] = {name: value for name, value in contract.items()
                if name not in {"expected_after", "resource_index"}}
            put("actual-source-first-b37-working-repair.json", {
                **box["b37_working_repair"],
                "only_working_b37_text_and_unlock_changed": True,
                "approved_text_source": "previously saved main_input working B37",
                "source_recheck": "pending subsequent native publication recheck"})
            capture(pending, "actual-pending-three-materials-after-working-edit")
            click(pending.buttons.button(QDialogButtonBox.StandardButton.Save))
            if pending.result_value != contract["expected_after"]:
                raise ValueError("待加入批次确认未提交精确B37工作修订。")
        modal(PendingMaterialEditor, confirm_pending)
        click(dialog.queue_confirm)

    modal(SectionMaterialsDialog, section_dialog)
    click(shelf.section_materials_button)
    wait("real three-material transaction", lambda: page.last_material_handoff is not None
         and page.last_material_handoff["status"] != "reading")
    if page.last_material_handoff["status"] != "added":
        raise ValueError("真实三素材事务未加入：" + str(page.last_material_handoff))
    wait("source transaction workers done", lambda: not window.tasks._workers)
    owner = page._material_handoff_dialog
    if not isinstance(owner, LessonDesignDialog):
        raise ValueError("真实事务未打开教学设计owner。")
    expected = deepcopy(owner.history.value)
    resources = expected["composition"]["resources"]
    assets = page.image_assets_widget.assets()
    if len(resources) != 3 or len(assets) != 7 or [r["original"]["kind"] for r in resources] != ["textbook_image", "handout_knowledge", "complete_question"]:
        raise ValueError("真实新SourceFirst不是3素材7资产。")
    if resources != box.get("expected_working_resources"):
        raise ValueError("原生来源复验和入课未保留精确唯一B37工作修订及全量Original。")
    if len(owner.history.back) != 1:
        raise ValueError("三素材不是一次历史事务。")
    before = deepcopy(owner.history.back[-1])
    if before["exports"] != [] or expected["exports"] != []:
        raise ValueError("本次SourceFirst入课前后不应已有成品记录。")
    owner.travel(False)
    if owner.history.value != before:
        raise ValueError("一次撤销未恢复完整入课前教学设计。")
    validate_design(owner.history.value)
    if owner.history.value.get("composition", {}).get("resources", []):
        raise ValueError("一次撤销未完整撤回三素材。")
    owner.travel(True)
    if owner.history.value != expected:
        raise ValueError("一次重做未恢复完整Original/roles/编排。")
    identity = save_design(owner, facade, "source-first")
    payload = deepcopy(page._payload())
    owner.reject()
    wait("source-first owner return", lambda: not page.studio_busy())
    window.navigate("preparation")
    saved_open(page, identity, "source-first-normal-reopen")
    if page._payload() != payload:
        raise ValueError("SourceFirst正常chooser重开不一致。")
    reader = textbook_crop_reader(facade, resources[0], assets)
    if reader["source"]["source_sha256"] != box["page"]["source_sha256"] or reader["source"]["pdf_pages"] != [95] or reader["crop_pixels_verified"] is not True:
        raise ValueError("新教材crop原PDF95像素回读未通过。")
    put("source-first-final-evidence.json", {"draft_id": identity, "resources": 3, "assets": 7,
        "production_scoped_facade_not_injected": True, "scoped_item_count": 24, "complete_lecture_blocks": 567,
        "knowledge_range": [34, 47], "question_range": [350, 355], "answer_range": [356, 361],
        "source_document": box["document"], "Original_and_block_ID_kind_roles": _origins(payload),
        "images": check_images(facade, payload), "crop_reader": {"binding": reader["binding"],
            "source_sha256": reader["source"]["source_sha256"], "pdf_pages": [95], "pixels_verified": True},
        "one_undo_redo_exact": True, "normal_picker_reopen_exact": True, "human_chemistry_review": False,
        "explicit_B37_working_edit": {**box["b37_working_repair"],
            "normal_ResourceEditor_used": True, "Original_and_roles_unchanged": True,
            "native_source_recheck_and_atomic_add_passed": True}})
    return identity, payload

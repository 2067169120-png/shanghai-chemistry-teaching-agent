"""Explicit local lesson backups. Restore only into a new personal-state directory.

No recursive copying of the user's state tree: student domains, model settings,
credentials, logs, executables and the original question bank are never selected.
Source documents are not inferred from names; missing lesson images are rebound
only after their original content hash and dimensions have been verified.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from .desktop_editor_recovery import PreparationRecoveryStore
from .desktop_preparation_drafts import PreparationDraftService
from .desktop_preparation_images import normalize_image_assets, verify_image_bytes
from .desktop_state import STATE_SCHEMA, DesktopStateStore, _reject_sensitive_fields, utc_now
from .desktop_work_organization import TERMINAL_TASKS, metadata

SCHEMA = "shchem.lesson-backup.v1"
RESTORED_SCHEMA = "shchem.restored-profile.v1"
MARKER = "restored-profile.json"
MAX_FILES = 20000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MANIFEST_LIMIT = 16 * 1024 * 1024
PREP = "tasks/preparation-v1/"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TASK = r"PREP-[0-9a-f]{32}"
_IMAGE = re.compile(r"tasks/preparation-v1/images/[0-9a-f]{64}\.image\Z")
_TASK_FILE = re.compile(rf"tasks/preparation-v1/tasks/{_TASK}\.json\Z")
_AUX_FILE = re.compile(rf"tasks/preparation-v1/(seeds|returned)/{_TASK}(?:\.attempt-[0-9]{{4}})?\.candidate\.json\Z")
_NODE_OUTPUT = re.compile(r"tasks/preparation-v1/node-exports/OUT-[0-9a-f]{32}/(?:lesson_presentation\.pptx|lesson_plan\.docx|student_worksheet\.docx)\Z")
_SUFFIXES = {".json", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf", ".docx", ".pptx", ".txt", ".md", ".html"}


class BackupError(ValueError):
    """Actionable message without exposing credentials or unrelated file paths."""
    def __init__(self, message):
        self.message_zh = message
        super().__init__(message)


class BackupCancelled(BackupError):
    pass


def _check_cancel(cancel):
    if cancel and cancel():
        raise BackupCancelled("操作已取消，原资料未改变。")


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _json(data):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise BackupError("备份JSON包含重复字段。")
            value[key] = item
        return value
    def constant(_value):
        raise BackupError("备份JSON含无效数字。")
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BackupError("备份JSON无法读取或格式不正确。") from exc


def _relative(name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or any(ord(c) < 32 for c in name) or name.startswith("/")
            or any(p in {"", ".", ".."} or p.endswith((".", " ")) for p in name.split("/"))):
        raise BackupError("备份包含不安全的相对路径。")
    for part in name.split("/"):
        if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
            raise BackupError("备份路径含Windows保留名称。")
    path = PurePosixPath(name)
    if path.as_posix() != name:
        raise BackupError("备份路径格式不正确。")
    return name


def _allowed(name):
    _relative(name)
    if name in {"desktop-state.v1.json", "recovery/preparation.v1.json"}:
        return True
    if _IMAGE.fullmatch(name) or _TASK_FILE.fullmatch(name) or _AUX_FILE.fullmatch(name) or _NODE_OUTPUT.fullmatch(name):
        return True
    parts = name.split("/")
    return (name.startswith(PREP + "artifacts/") and len(parts) >= 6
            and re.fullmatch(_TASK, parts[3]) is not None and Path(name).suffix.lower() in _SUFFIXES)


def _source(root, name):
    _relative(name)
    current = root
    for part in name.split("/"):
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise BackupError("备份来源包含链接目录或链接文件，未跟随。")
    if not current.resolve().is_relative_to(root.resolve()):
        raise BackupError("来源不在当前个人目录中。")
    return current


def _hash_file(path, cancel=None):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _check_cancel(cancel)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise BackupError("单个备份文件超过128MB，请单独保管该文件。")
            digest.update(chunk)
    return digest.hexdigest(), size


@dataclass(frozen=True)
class PlannedFile:
    name: str
    sha256: str
    size: int
    source: Path | None = None
    data: bytes | None = None


@dataclass(frozen=True)
class BackupPlan:
    files: tuple[PlannedFile, ...]
    summary: dict
    references: tuple[dict, ...]
    warnings: tuple[str, ...]

    def report(self):
        return {"summary": deepcopy(self.summary), "files": [
            {"path": f.name, "sha256": f.sha256, "bytes": f.size} for f in self.files],
            "references": deepcopy(list(self.references)), "warnings": list(self.warnings)}


def _task_reader(root):
    """Use existing read validators without initializing/recovering active tasks."""
    from .desktop_preparation import DesktopPreparationManager
    # Match the normal manager constructor: Windows TEMP may use 8.3 aliases.
    root = Path(root).resolve()
    reader = object.__new__(DesktopPreparationManager)
    reader.root = root / PREP
    reader.tasks_root = reader.root / "tasks"
    reader.seeds_root = reader.root / "seeds"
    reader.artifacts_root = reader.root / "artifacts"
    reader.returned_root = reader.root / "returned"
    return reader


def _assets(value):
    if isinstance(value, Mapping):
        if "image_assets" in value:
            for asset in normalize_image_assets(value["image_assets"]):
                yield asset
        for key, nested in value.items():
            if key != "image_assets":
                yield from _assets(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _assets(nested)


def _design_outputs(value):
    """Only bundles registered in included lesson records, never a directory walk."""
    from .desktop_lesson_design import SCHEMA as DESIGN_SCHEMA, validate_design
    if isinstance(value, Mapping):
        if value.get("schema_version") == DESIGN_SCHEMA:
            yield from validate_design(value)["exports"]
        else:
            for nested in value.values():
                yield from _design_outputs(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _design_outputs(nested)


def _node_output_totals(names):
    from .desktop_lesson_output import FILES
    bundles = {}
    for name in names:
        if _NODE_OUTPUT.fullmatch(name):
            folder, filename = name.rsplit("/", 1)
            bundles.setdefault(folder, set()).add(filename)
    return {"node_output_bundles": sum(files == set(FILES) for files in bundles.values()),
            "node_output_files": sum(len(files) for files in bundles.values())}


def plan_backup(state_root, *, include_images=False, include_tasks=False, cancel=None):
    """Freeze the selected records. Copy operations later recheck file versions."""
    root = Path(state_root).resolve()
    _source(root, "desktop-state.v1.json")
    snapshot = DesktopStateStore(root).snapshot()
    records, warnings, refs, images = {}, [], [], {}
    for identity, record in snapshot["drafts"].items():
        _check_cancel(cancel)
        if isinstance(record, dict) and record.get("kind") == "preparation":
            try:
                PreparationDraftService._payload(record)
            except ValueError as exc:
                raise BackupError(f"备课草稿 {identity} 无法完整读取；请先检查，未略过后报成功。") from exc
            records[identity] = deepcopy(record)
    # Other draft kinds include student analysis and private result envelopes.
    # Keep only known lesson data rather than blindly archiving the state JSON.
    selected = {"schema_version": STATE_SCHEMA, "window": {}, "drafts": records,
                "basket": deepcopy(snapshot["basket"]), "updated_at": snapshot.get("updated_at"),
                "work_organization": {}}
    if "scan_number_regions" in snapshot:
        from .desktop_number_regions import validated_region_library
        selected["scan_number_regions"] = validated_region_library(snapshot["scan_number_regions"])
    for identity, record in records.items():
        view = metadata(snapshot, "draft", identity)
        if view:
            selected["work_organization"]["draft:" + identity] = view
    files = {}
    def inline(name, data):
        files[name] = PlannedFile(name, hashlib.sha256(data).hexdigest(), len(data), data=data)
    def add(name, *, expected=None):
        if not _allowed(name):
            raise BackupError("所选文件不属于受支持的备课资料范围。")
        path = _source(root, name)
        if not path.is_file():
            refs.append({"kind": "missing_file", "path": name, "sha256": expected})
            return False
        digest, size = _hash_file(path, cancel)
        if expected and digest != expected:
            raise BackupError(f"文件 {Path(name).name} 与已登记内容不一致，请先核对。")
        files[name] = PlannedFile(name, digest, size, source=path)
        return True
    recovery_path = _source(root, "recovery/preparation.v1.json")
    recovery = None
    if recovery_path.exists():
        recovery = PreparationRecoveryStore(root).load()
        # Export the validated snapshot, not a second potentially newer read.
        inline("recovery/preparation.v1.json", _bytes(recovery))
    all_assets = [records, recovery]
    tasks, skipped_tasks = 0, 0
    reader = _task_reader(root)
    if reader.tasks_root.exists():
        _source(root, PREP + "tasks")
        for path in sorted(reader.tasks_root.glob("PREP-*.json")):
            _check_cancel(cancel)
            name = path.relative_to(root).as_posix()
            _source(root, name)
            try:
                task = reader._read_task(path.stem)
            except Exception as exc:
                if include_tasks:
                    raise BackupError(f"任务 {path.stem} 无法完整读取；请先检查。") from exc
                skipped_tasks += 1
                continue
            if not include_tasks or task["status"] not in TERMINAL_TASKS:
                skipped_tasks += 1
                continue
            # Export task bytes captured by the read validator. A task that is
            # restarted during copying causes a version check failure below.
            digest, size = _hash_file(path, cancel)
            if _json(path.read_bytes()) != task:
                raise BackupError("任务在整理清单时发生变化，请刷新后重试。")
            files[name] = PlannedFile(name, digest, size, source=path)
            tasks += 1
            view = metadata(snapshot, "task", path.stem)
            if view:
                selected["work_organization"]["task:" + path.stem] = view
            all_assets.append(task["payload"])
            for field, folder in (("candidate_seed_file", "seeds"), ("returned_candidate_file", "returned")):
                if task.get(field):
                    add(PREP + folder + "/" + task[field])
            if task["status"] == "completed":
                prefix = PREP + "artifacts/" + _relative(task["attempt_relpath"]) + "/"
                for artifact in task["artifacts"]:
                    add(prefix + _relative(artifact["relative_path"]), expected=artifact["sha256"])
    bundles = {}
    from .desktop_lesson_output import FILES
    for record in _design_outputs(all_assets):
        _check_cancel(cancel)
        old = bundles.get(record["id"])
        if old is not None and old != record:
            raise BackupError("同一教学成品有不同版本记录，请先核对草稿。")
        if {f["name"] for f in record["files"]} != set(FILES) or len(record["files"]) != len(FILES):
            raise BackupError("教学成品记录不含完整三类文件，请先核对。")
        bundles[record["id"]] = record
    for identity, record in bundles.items():
        if include_tasks:
            for file in record["files"]:
                add(PREP + "node-exports/" + identity + "/" + file["name"], expected=file["sha256"])
        else:
            refs.append({"kind": "lesson_output", "key": identity,
                         "reason": "未勾选已结束任务与成品；仅保存版本引用，未打包三类文件。"})
    if bundles:
        warnings.append("教学环节三类成品按已保存草稿/恢复副本中的版本选入；预览缓存不打包。含此扩展的备份请用0.1.101或后续支持版本恢复。")
    for asset in _assets(all_assets):
        old = images.get(asset["sha256"])
        if old and any(old[k] != asset[k] for k in ("width", "height", "content_type")):
            raise BackupError("同一图片身份的尺寸信息不一致。")
        images[asset["sha256"]] = asset
    for digest, asset in images.items():
        name = PREP + "images/" + digest + ".image"
        path = _source(root, name)
        available = path.is_file()
        if include_images and available:
            if path.stat().st_size > 10 * 1024 * 1024:
                raise BackupError("图片超过10MB，请先检查原副本。")
            verify_image_bytes(asset, path.read_bytes())
            add(name, expected=digest)
        else:
            refs.append({"kind": "lesson_image", "asset": asset,
                         "reason": "not_selected" if available else "missing"})
    for item in selected["basket"]:
        refs.append({"kind": "basket_reference", "key": item.get("key", ""),
                     "reason": "原题库不在本版备份范围；题篮保存身份，不等于原题已打包。"})
    omitted_drafts = len(snapshot["drafts"]) - len(records)
    if skipped_tasks:
        warnings.append(f"{skipped_tasks}份任务未选入或尚未结束；不会恢复/重试正在运行的请求。")
    if omitted_drafts:
        warnings.append(f"{omitted_drafts}份非备课草稿未包含（含其他业务草稿）；并非整个工作台备份。")
    warnings.append("不包括原题库、Word/公众号导入库、学生档案、模型设置与密钥。正文内教师自行填写的个人信息不会自动脱敏。")
    _reject_sensitive_fields(selected)
    inline("desktop-state.v1.json", _bytes(selected))
    if len(files) > MAX_FILES or sum(f.size for f in files.values()) > MAX_TOTAL_BYTES:
        raise BackupError("本次超过2GB或2万个文件，请分批保管成品。")
    summary = {"drafts": len(records), "tasks": tasks, "basket_references": len(selected["basket"]),
               "recovery": recovery is not None, "images_referenced": len(images),
               "images_included": sum(bool(_IMAGE.fullmatch(n)) for n in files),
               "files": len(files), "bytes": sum(f.size for f in files.values()),
               "include_images": bool(include_images), "include_tasks": bool(include_tasks),
               "missing_files": sum(r["kind"] == "missing_file" or r.get("reason") == "missing" for r in refs)}
    if bundles:
        summary.update(_node_output_totals(files))
    if "scan_number_regions" in selected:
        from .desktop_number_regions import region_totals
        summary.update(region_totals(selected["scan_number_regions"]))
        warnings.append("包含题号区域坐标，不包括对应的原题图；恢复后须使用相同原图才能载入位置。旧版程序可能不支持此扩展。")
    return BackupPlan(tuple(files.values()), summary, tuple(refs), tuple(warnings))


def _publish_file(temporary, destination):
    if os.name == "nt":
        # Windows rename is a no-clobber atomic move, including removable media.
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)


def create_backup(plan: BackupPlan, destination, *, cancel=None):
    destination = Path(destination).absolute()
    if destination.exists():
        raise BackupError("该备份文件已存在，请使用新文件名；没有覆盖旧备份。")
    if not destination.parent.is_dir():
        raise BackupError("请先选择已存在的保存目录。")
    manifest = {"schema_version": SCHEMA, "created_at": utc_now(), **plan.report()}
    from .desktop_version import DESKTOP_VERSION
    manifest["app_version"] = DESKTOP_VERSION
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".shchem-backup-", suffix=".tmp", dir=destination.parent)
        os.close(fd)
        temporary = Path(name)
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _bytes(manifest))
            for entry in plan.files:
                _check_cancel(cancel)
                digest, size = hashlib.sha256(), 0
                with archive.open("files/" + entry.name, "w") as out:
                    if entry.data is not None:
                        chunks = (entry.data,)
                        stream = None
                    else:
                        # Check links again after the user has reviewed the plan.
                        if entry.source.is_symlink():
                            raise BackupError("来源文件变为链接，请刷新清单。")
                        stream = entry.source.open("rb")
                        chunks = iter(lambda: stream.read(1024 * 1024), b"")
                    try:
                        for chunk in chunks:
                            _check_cancel(cancel)
                            size += len(chunk)
                            if size > entry.size:
                                raise BackupError("来源文件在备份期间变化，请刷新清单后重试。")
                            digest.update(chunk)
                            out.write(chunk)
                    finally:
                        if stream:
                            stream.close()
                if digest.hexdigest() != entry.sha256 or size != entry.size:
                    raise BackupError("来源文件在备份期间变化，请刷新清单后重试。")
        # Full verification happens before making the archive visible as final.
        inspect_backup(temporary, cancel=cancel)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        _check_cancel(cancel)
        # Hard-link publication is atomic and refuses an existing destination.
        # Both files are in the same directory/volume on supported Windows/Linux.
        _publish_file(temporary, destination)
        return {"path": str(destination), "manifest": manifest}
    except (OSError, BadZipFile) as exc:
        raise BackupError("备份未完成，请检查目录权限、磁盘空间和来源文件；旧备份未覆盖。") from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _manifest(archive):
    infos = archive.infolist()
    names = [i.filename for i in infos]
    if len(infos) > MAX_FILES + 1 or len({n.casefold() for n in names}) != len(names):
        raise BackupError("备份文件过多或包含重复/大小写冲突路径。")
    if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > MANIFEST_LIMIT:
        raise BackupError("备份缺少有效清单。")
    manifest = _json(archive.read("manifest.json"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
        raise BackupError("此备份版本不受支持；没有修改当前资料。")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_FILES:
        raise BackupError("备份文件清单不完整。")
    targets, total = set(), 0
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"}
                or not _allowed(row["path"]) or not isinstance(row["sha256"], str)
                or not _HASH.fullmatch(row["sha256"]) or type(row["bytes"]) is not int
                or not 0 <= row["bytes"] <= MAX_FILE_BYTES):
            raise BackupError("备份含未支持的文件类型、路径或校验字段。")
        name = "files/" + row["path"]
        if name.casefold() in targets or name not in names:
            raise BackupError("备份清单存在重复或缺失文件。")
        targets.add(name.casefold())
        info = archive.getinfo(name)
        mode = info.external_attr >> 16
        if info.is_dir() or (mode & 0o170000) not in (0, 0o100000) or info.flag_bits & 1 or info.file_size != row["bytes"]:
            raise BackupError("备份文件尺寸、类型或加密方式不受支持。")
        total += row["bytes"]
    if total > MAX_TOTAL_BYTES or {n.casefold() for n in names} != targets | {"manifest.json"}:
        raise BackupError("备份超出容量或包含清单以外的文件。")
    if "files/desktop-state.v1.json" not in names:
        raise BackupError("备份缺少个人作品状态。")
    state = _json(archive.read("files/desktop-state.v1.json"))
    if (not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA
            or set(state) - {"schema_version", "window", "drafts", "basket", "updated_at", "work_organization", "scan_number_regions"}
            or not isinstance(state.get("drafts"), dict) or not isinstance(state.get("basket"), list)
            or len(state["basket"]) > 100):
        raise BackupError("备份的作品状态格式不正确。")
    if "scan_number_regions" in state:
        from .desktop_number_regions import validated_region_library
        validated_region_library(state["scan_number_regions"])
    _reject_sensitive_fields(state)
    for identity, record in state["drafts"].items():
        PreparationDraftService._payload(record)
        metadata(state, "draft", identity)
    if not isinstance(manifest.get("references"), list) or not isinstance(manifest.get("warnings"), list):
        raise BackupError("备份缺少外部引用和范围说明。")
    if "scan_number_regions" in state:
        from .desktop_number_regions import region_totals
        manifest = deepcopy(manifest)
        manifest['summary'] = {**manifest.get('summary', {}), **region_totals(state['scan_number_regions'])}
    if "node_output_files" in manifest.get("summary", {}) or any(_NODE_OUTPUT.fullmatch(r["path"]) for r in manifest["files"]):
        manifest = deepcopy(manifest)
        manifest["summary"] = {**manifest.get("summary", {}), **_node_output_totals(r["path"] for r in manifest["files"])}
    return manifest


def inspect_backup(filename, *, cancel=None):
    try:
        with ZipFile(filename) as archive:
            manifest = _manifest(archive)
            for row in manifest["files"]:
                _check_cancel(cancel)
                digest, size = hashlib.sha256(), 0
                with archive.open("files/" + row["path"]) as stream:
                    while chunk := stream.read(1024 * 1024):
                        _check_cancel(cancel)
                        size += len(chunk)
                        if size > row["bytes"]:
                            raise BackupError("备份实际解压尺寸超出清单。")
                        digest.update(chunk)
                if size != row["bytes"] or digest.hexdigest() != row["sha256"]:
                    raise BackupError("备份校验不符，可能损坏；没有恢复任何文件。")
            # Do not trust claimed totals for the teacher's preview.
            manifest = deepcopy(manifest)
            state = _json(archive.read("files/desktop-state.v1.json"))
            manifest["summary"] = {**manifest.get("summary", {}), "drafts": len(state["drafts"]),
                "basket_references": len(state["basket"]), "files": len(manifest["files"]),
                "tasks": sum(bool(_TASK_FILE.fullmatch(r["path"])) for r in manifest["files"]),
                "bytes": sum(r["bytes"] for r in manifest["files"])}
            if "node_output_files" in manifest["summary"] or any(_NODE_OUTPUT.fullmatch(r["path"]) for r in manifest["files"]):
                manifest["summary"].update(_node_output_totals(r["path"] for r in manifest["files"]))
            from .desktop_number_regions import region_totals
            if "scan_number_regions" in state:
                manifest["summary"].update(region_totals(state["scan_number_regions"]))
            return manifest
    except BackupError:
        raise
    except (OSError, ValueError, TypeError, KeyError, BadZipFile, RuntimeError) as exc:
        raise BackupError("无法完整读取备份；请检查文件，当前资料未改变。") from exc


def restore_backup(filename, destination, *, expected_manifest: str, cancel=None):
    """A fresh profile, never merge into or replace an existing directory."""
    target = Path(destination).absolute()
    if target.exists() or target.is_symlink():
        raise BackupError("恢复目标已存在。请选择新的子目录，不覆盖或合并任何现有资料。")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise BackupError("请在正常的本地目录下选择新子目录。")
    temporary = None
    try:
        # Use one open file/ZipFile throughout inspection and extraction, so a
        # swapped pathname cannot substitute another archive after confirmation.
        with Path(filename).open("rb") as source, ZipFile(source) as archive:
            manifest = _manifest(archive)
            if hashlib.sha256(_bytes(manifest)).hexdigest() != expected_manifest:
                raise BackupError("备份清单已变化，请重新检查并确认。")
            temporary = Path(tempfile.mkdtemp(prefix=".shchem-restore-", dir=target.parent))
            for row in manifest["files"]:
                _check_cancel(cancel)
                output = temporary / row["path"]
                output.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with archive.open("files/" + row["path"]) as stream, output.open("xb") as out:
                    while chunk := stream.read(1024 * 1024):
                        _check_cancel(cancel)
                        size += len(chunk)
                        if size > row["bytes"]:
                            raise BackupError("备份大小与清单不符。")
                        digest.update(chunk)
                        out.write(chunk)
                if size != row["bytes"] or digest.hexdigest() != row["sha256"]:
                    raise BackupError("恢复文件校验失败，未接入当前工作台。")
            # Validate actual supported records and image decoding before commit.
            state = DesktopStateStore(temporary).snapshot()
            recovery = PreparationRecoveryStore(temporary).load()
            image_inputs = [state["drafts"], recovery]
            reader = _task_reader(temporary)
            for path in reader.tasks_root.glob("*.json"):
                task = reader._read_task(path.stem)
                if task["status"] not in TERMINAL_TASKS:
                    raise BackupError("备份含未结束任务，拒绝恢复以免续跑。")
                image_inputs.append(task["payload"])
            for asset in _assets(image_inputs):
                image = temporary / PREP / "images" / (asset["sha256"] + ".image")
                if image.is_file():
                    if image.stat().st_size > 10 * 1024 * 1024:
                        raise BackupError("备课图片超出支持范围。")
                    verify_image_bytes(asset, image.read_bytes())
            (temporary / MARKER).write_bytes(_bytes({"schema_version": RESTORED_SCHEMA,
                "created_at": utc_now(), "backup_manifest_sha256": expected_manifest,
                "references": manifest["references"], "warnings": manifest["warnings"]}))
            _check_cancel(cancel)
            # Windows rename refuses existing targets. Reserving the name first
            # makes the same no-clobber contract explicit on other platforms.
            target.mkdir(exist_ok=False)
            try:
                # Publish the marker last. No other launch accepts an incomplete
                # restore directory, and the current personal profile is untouched.
                for child in temporary.iterdir():
                    if child.name != MARKER:
                        os.replace(child, target / child.name)
                os.replace(temporary / MARKER, target / MARKER)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
            temporary.rmdir()
            temporary = None
        return {"directory": str(target), "drafts": len(state["drafts"]),
                "references": len(manifest["references"]), "current_state_unchanged": True}
    except BackupError:
        raise
    except (OSError, ValueError, KeyError, TypeError, BadZipFile) as exc:
        raise BackupError("恢复未完成；当前资料未改变，请检查备份或目标目录。") from exc
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)


def manifest_revision(manifest):
    return hashlib.sha256(_bytes(manifest)).hexdigest()


def restored_profile(path):
    root = Path(path).resolve()
    marker = _source(root, MARKER)
    if not marker.is_file() or marker.stat().st_size > MANIFEST_LIMIT:
        raise BackupError("请选择本工作台创建的独立恢复目录。")
    if _json(marker.read_bytes()).get("schema_version") != RESTORED_SCHEMA:
        raise BackupError("独立恢复目录格式不正确。")
    DesktopStateStore(root).snapshot()
    return root


def missing_lesson_images(state_root):
    root = Path(state_root).resolve()
    state = DesktopStateStore(root).snapshot()
    inputs = [[record for record in state["drafts"].values()
               if isinstance(record, dict) and record.get("kind") == "preparation"],
              PreparationRecoveryStore(root).load()]
    reader = _task_reader(root)
    for path in reader.tasks_root.glob("PREP-*.json"):
        task = reader._read_task(path.stem)
        inputs.append(task["payload"])
    images = {asset["sha256"]: asset for asset in _assets(inputs)}
    return [asset for digest, asset in images.items()
            if not _source(root, PREP + "images/" + digest + ".image").is_file()]


def reconnect_lesson_image(state_root, asset, source):
    """Restore identical bytes into the content store; never rewrite references."""
    from .desktop_preparation_images import MAX_IMAGE_BYTES
    source = Path(source)
    if not source.is_file() or source.stat().st_size > MAX_IMAGE_BYTES:
        raise BackupError("请选择不超过10MB的原始图片文件。")
    data = source.read_bytes()
    try:
        verify_image_bytes(asset, data)
    except ValueError as exc:
        raise BackupError("所选图片与原引用的内容/尺寸不一致。请选原文件，不按同名替换。") from exc
    root = Path(state_root).resolve()
    target = _source(root, PREP + "images/" + asset["sha256"] + ".image")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        verify_image_bytes(asset, target.read_bytes())
        return {"asset_id": asset["asset_id"], "already_present": True}
    fd, name = tempfile.mkstemp(prefix=".reconnect-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        _publish_file(temporary, target)
    except OSError as exc:
        raise BackupError("图片未能保存，原引用未改变。") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {"asset_id": asset["asset_id"], "already_present": False}


def summary_text(report):
    s = report["summary"]
    return (f"备课草稿 {s['drafts']} 份 · 已结束任务 {s['tasks']} 份 · 题篮引用 {s['basket_references']} 项\n"
            f"文件 {s['files']} 个 · 未压缩 {s['bytes'] / (1024 * 1024):.2f} MB\n"
            f"图片打包 {s.get('images_included', 0)} / 引用 {s.get('images_referenced', 0)} 张 · "
            f"恢复副本 {'包含' if s.get('recovery') else '无'}\n"
            f"教学环节成品 {s.get('node_output_bundles', 0)} 套完整文件 / {s.get('node_output_files', 0)} 个文件\n"
            f"已存题号位置 {s.get('number_region_images', 0)} 张图 / {s.get('number_regions', 0)} 个区域（不含原题图）\n\n" +
            "\n".join(report["warnings"]) + "\n\n外部引用/缺失项：\n" +
            ("\n".join((r.get("asset", {}).get("caption") or r.get("path") or r.get("key") or r["kind"]) +
                       " — " + r.get("reason", "文件缺失") for r in report["references"]) or "无"))

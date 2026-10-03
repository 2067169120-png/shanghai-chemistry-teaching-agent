"""Local business snapshots with strict archive boundaries and fresh-only restore.

Source files, identities and score chains retain their bytes. SQLite databases
use the online backup API, including committed WAL data. Provider credentials,
temporary work and executables are outside the selected domains.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from .desktop_backup import (
    MARKER,
    RESTORED_SCHEMA,
    BackupError,
    PlannedFile,
    _bytes,
    _check_cancel,
    _json,
    _publish_file,
    _relative,
    _source,
    manifest_revision,
)
from .desktop_state import DesktopStateStore, _reject_sensitive_fields, utc_now

SCHEMA = "shchem.business-backup.v1"
STATE_FILES = frozenset(
    {
        "desktop-state.v1.json",
        "personal-visual-crops.sqlite3",
        "personal-visual-question-attributes.sqlite3",
        "word-question-attributes.sqlite3",
    }
)
STATE_DIRS = (
    "drafts",
    "recovery",
    "tasks/preparation-v1",
    "paper-export-workbench",
    "student-visual-v1",
    "textbook-sources",
    "visual-import-v2",
    "word-handout-import",
    "word-question-previews",
    "exam-analyses",
)
CONTENT_DIRS = ("sh-chem-db", "knowledge/textbook", "knowledge/lectures")
CONTENT = "library/workspace/"
MAX_FILES = 100000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
MANIFEST_LIMIT = 32 * 1024 * 1024
EXCLUDED_SUFFIXES = {
    ".tmp",
    ".lock",
    ".log",
    ".pyc",
    ".py",
    ".pyw",
    ".exe",
    ".dll",
    ".bat",
    ".cmd",
    ".ps1",
}


def _excluded(name):
    if name.endswith(("-wal", "-shm", "-journal")):
        return True
    if any(
        part.casefold()
        in {"temporary", "__pycache__", "node_modules", "model-settings"}
        or part.startswith((".", "~"))
        for part in name.split("/")
    ):
        return True
    return Path(name).suffix.casefold() in EXCLUDED_SUFFIXES


def _allowed(name):
    _relative(name)
    if _excluded(name):
        return False
    if name in STATE_FILES:
        return True
    if any(name.startswith(prefix + "/") for prefix in STATE_DIRS):
        return True
    return name.startswith(CONTENT) and any(
        name[len(CONTENT) :].startswith(prefix + "/") for prefix in CONTENT_DIRS
    )


def _hash_file(path, cancel=None):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _check_cancel(cancel)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise BackupError(
                    "单个业务文件超过256MB，请拆分或单独保管后核对备份范围。"
                )
            digest.update(chunk)
    return digest.hexdigest(), size


def _check_source_sensitive_fields(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            folded = str(key).casefold()
            if any(
                part in folded
                for part in ("api_key", "secret_value", "credential_value")
            ):
                # Existing evidence uses these flags to document whether a key
                # was touched. Boolean flags and this exact boolean schema
                # describe the audit; string/object credential values block.
                if (
                    folded
                    in {
                        "api_key_persisted",
                        "weread_api_key_written",
                        "api_key_accessed",
                        "api_key_access_allowed",
                    }
                    and type(nested) is bool
                ):
                    continue
                if folded == "api_key_accessed" and nested in (
                    {"type": "boolean", "const": False},
                    {"const": False},
                ):
                    continue
                raise BackupError("来源记录含密钥值字段，请先排除配置资料再备份。")
            _check_source_sensitive_fields(nested)
    elif isinstance(value, list):
        for item in value:
            _check_source_sensitive_fields(item)


def _check_json_record(raw, *, source_record=False):
    if not source_record:
        _reject_sensitive_fields(_json(raw))
        return

    def checked_pairs(items):
        # Original evidence sometimes contains duplicate display keys. Keep
        # its bytes intact and inspect every occurrence for credentials,
        # including a sensitive value shadowed by a later duplicate key.
        for key, value in items:
            _check_source_sensitive_fields({key: value})
        return dict(items)

    def invalid_constant(_):
        raise BackupError("来源JSON包含非标准数值，请核对原记录。")

    try:
        json.loads(
            raw, object_pairs_hook=checked_pairs, parse_constant=invalid_constant
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BackupError("来源JSON无法安全核对，请保留原件并核对记录。") from exc


def _sqlite_copy(path, cancel=None):
    """Capture a committed transaction, never copy a live DB without its WAL."""
    with tempfile.TemporaryDirectory(prefix="shchem-sqlite-backup-") as folder:
        target = Path(folder) / "snapshot.sqlite3"
        try:
            with (
                closing(
                    sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
                ) as source,
                closing(sqlite3.connect(target)) as destination,
            ):
                source.backup(
                    destination,
                    pages=256,
                    sleep=0.01,
                    progress=lambda *_: _check_cancel(cancel),
                )
                if destination.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise BackupError("本地数据库完整性检查失败，请先核对原文件。")
            if target.stat().st_size > MAX_FILE_BYTES:
                raise BackupError("业务数据库超过256MB，请先整理归档。")
            return target.read_bytes()
        except sqlite3.Error as exc:
            raise BackupError(
                "本地业务数据库暂时无法备份，请结束正在保存的操作后重试。"
            ) from exc


def _domain(name):
    if name.startswith(CONTENT):
        return "教材与原题库"
    if name.startswith("student-visual-v1/"):
        return "学生原页与正式评分"
    if name.startswith("exam-analyses/"):
        return "考试分析与复测"
    if name.startswith("textbook-sources/"):
        return "教材原书与候选快照"
    if name.startswith(
        ("visual-import-v2/", "word-handout-import/", "word-question-previews/")
    ):
        return "本地导入与题库"
    if name.endswith(".sqlite3"):
        return "个人题目标签与裁切"
    return "名册、作品与工作台状态"


@dataclass(frozen=True)
class BusinessBackupPlan:
    files: tuple[PlannedFile, ...]
    origin_state: Path
    origin_content: Path | None
    source_names: tuple[tuple[str, str], ...]
    skipped: int
    kind: str = "business"

    def report(self):
        return {
            "summary": {
                "files": len(self.files),
                "bytes": sum(row.size for row in self.files),
                "domains": dict(Counter(_domain(row.name) for row in self.files)),
                "excluded_temporary_entries": self.skipped,
            },
            "files": [
                {"path": row.name, "sha256": row.sha256, "bytes": row.size}
                for row in self.files
            ],
            "references": [],
            "warnings": [
                "含学生原页、名册与教学资料，请在本机或自选的可信存储保管。",
                "本快照保留原评分链与资料字节；SQL数据库包含已提交数据。",
                "模型设置和密钥不包含。外部临时导出位置的文件可能仍需手动带走。",
                "复原到新目录后仍须核对教学来源、使用权限与图片质量；文件校验不等于内容审定。",
            ],
        }


def _inventory(state, content=None):
    rows, skipped = {}, 0

    def collect(root, prefix, output_prefix=""):
        nonlocal skipped
        path = _source(root, prefix)
        if not path.exists():
            return

        def visit(selected):
            nonlocal skipped
            name = output_prefix + selected.relative_to(root).as_posix()
            # Prune dependencies and temporary trees before inspecting or
            # descending into them. A node_modules junction is common on the
            # teacher's machine and is never a business source.
            if _excluded(name):
                skipped += 1
                return
            if selected.is_symlink() or (
                hasattr(selected, "is_junction") and selected.is_junction()
            ):
                raise BackupError(
                    "业务资料中有链接文件或目录，请先核对；没有跟随外部路径。"
                )
            if selected.is_dir():
                for child in selected.iterdir():
                    visit(child)
            elif selected.is_file() and _allowed(name):
                rows[name] = (root, selected.relative_to(root).as_posix())
            else:
                skipped += 1

        visit(path)

    for name in STATE_FILES:
        collect(state, name)
    for prefix in STATE_DIRS:
        collect(state, prefix)
    # An independent restored profile already carries its content library.
    if content:
        for prefix in CONTENT_DIRS:
            collect(content, prefix, CONTENT)
    return rows, skipped


def plan_business_backup(state_root, *, content_root=None, cancel=None):
    from .model_provider_settings import _assert_components_not_reparse

    state = Path(state_root).absolute()
    content = Path(content_root).absolute() if content_root else None
    _assert_components_not_reparse(state)
    if content:
        _assert_components_not_reparse(content)
    snapshot = DesktopStateStore(state).snapshot()
    _reject_sensitive_fields(snapshot)
    rows, skipped = _inventory(state, content)
    files = []
    for name, (root, relative) in sorted(rows.items()):
        _check_cancel(cancel)
        path = _source(root, relative)
        if path.suffix.casefold() in {".sqlite3", ".sqlite", ".db"}:
            raw = _sqlite_copy(path, cancel)
            files.append(
                PlannedFile(name, hashlib.sha256(raw).hexdigest(), len(raw), data=raw)
            )
        else:
            if path.suffix.casefold() == ".json":
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise BackupError("业务记录过大，请分批整理后备份。")
                _check_json_record(
                    path.read_bytes(), source_record=name.startswith(CONTENT)
                )
            digest, size = _hash_file(path, cancel)
            files.append(PlannedFile(name, digest, size, source=path))
    if "desktop-state.v1.json" not in rows:
        raw = _bytes(snapshot)
        files.append(
            PlannedFile(
                "desktop-state.v1.json",
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                data=raw,
            )
        )
    if len(files) > MAX_FILES or sum(row.size for row in files) > MAX_TOTAL_BYTES:
        raise BackupError("业务备份超过10万文件或16GB，请分目录归档后核对范围。")
    return BusinessBackupPlan(
        tuple(files),
        state,
        content,
        tuple(
            (name, str(_source(root, rel)))
            for name, (root, rel) in sorted(rows.items())
        ),
        skipped,
    )


def _manifest(archive):
    infos = archive.infolist()
    names = [row.filename for row in infos]
    if len(infos) > MAX_FILES + 1 or len({name.casefold() for name in names}) != len(
        names
    ):
        raise BackupError("业务备份路径重复、大小写冲突或文件过多。")
    if (
        "manifest.json" not in names
        or archive.getinfo("manifest.json").file_size > MANIFEST_LIMIT
    ):
        raise BackupError("业务备份缺少有效清单。")
    manifest = _json(archive.read("manifest.json"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
        raise BackupError("业务备份版本不受支持。")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise BackupError("业务备份文件清单不完整。")
    selected, total = set(), 0
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("path"), str)
            or not _allowed(row["path"])
            or not isinstance(row.get("sha256"), str)
            or len(row["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in row["sha256"])
            or type(row.get("bytes")) is not int
            or not 0 <= row["bytes"] <= MAX_FILE_BYTES
        ):
            raise BackupError("业务备份含未支持的路径、类型或校验值。")
        name = "files/" + row["path"]
        if name not in names or name.casefold() in selected:
            raise BackupError("业务备份存在重复或缺失文件。")
        selected.add(name.casefold())
        info = archive.getinfo(name)
        mode = info.external_attr >> 16
        if (
            info.is_dir()
            or mode & 0o170000 not in (0, 0o100000)
            or info.flag_bits & 1
            or info.file_size != row["bytes"]
        ):
            raise BackupError("业务备份文件尺寸、类型或加密方式不正确。")
        total += row["bytes"]
    if total > MAX_TOTAL_BYTES or {n.casefold() for n in names} != selected | {
        "manifest.json"
    }:
        raise BackupError("业务备份含清单以外的内容或超过容量。")
    if "files/desktop-state.v1.json" not in names:
        raise BackupError("业务备份缺少工作台状态。")
    if not isinstance(manifest.get("warnings"), list) or not isinstance(
        manifest.get("references"), list
    ):
        raise BackupError("业务备份缺少范围说明。")
    state = _json(archive.read("files/desktop-state.v1.json"))
    _reject_sensitive_fields(state)
    manifest["summary"] = {
        "files": len(rows),
        "bytes": total,
        "domains": dict(Counter(_domain(row["path"]) for row in rows)),
    }
    return manifest


def _stream(archive, row, out=None, cancel=None):
    digest, size = hashlib.sha256(), 0
    with archive.open("files/" + row["path"]) as stream:
        while chunk := stream.read(1024 * 1024):
            _check_cancel(cancel)
            size += len(chunk)
            if size > row["bytes"]:
                raise BackupError("业务备份实际解压大小超过清单。")
            digest.update(chunk)
            if out:
                out.write(chunk)
    if size != row["bytes"] or digest.hexdigest() != row["sha256"]:
        raise BackupError("业务备份文件校验失败，当前资料未改变。")


def inspect_business_backup(filename, *, cancel=None):
    try:
        with ZipFile(filename) as archive:
            manifest = _manifest(archive)
            for row in manifest["files"]:
                _stream(archive, row, cancel=cancel)
            return manifest
    except BackupError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, BadZipFile) as exc:
        raise BackupError("业务备份无法完整读取，请核对文件。") from exc


def create_business_backup(plan, destination, *, cancel=None):
    from .model_provider_settings import _assert_components_not_reparse

    target = Path(destination).absolute()
    _assert_components_not_reparse(target.parent)
    _source(target.parent, target.name)
    if target.exists() or not target.parent.is_dir():
        raise BackupError("请选择已有目录中的新备份文件名，未覆盖旧文件。")
    manifest = {"schema_version": SCHEMA, "created_at": utc_now(), **plan.report()}
    temporary = None
    try:
        fd, name = tempfile.mkstemp(
            prefix=".shchem-business-", suffix=".tmp", dir=target.parent
        )
        os.close(fd)
        temporary = Path(name)
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _bytes(manifest))
            for entry in plan.files:
                _check_cancel(cancel)
                if entry.data is not None:
                    archive.writestr("files/" + entry.name, entry.data)
                else:
                    root = (
                        plan.origin_content
                        if entry.name.startswith(CONTENT)
                        else plan.origin_state
                    )
                    relative = entry.name.removeprefix(CONTENT)
                    path = _source(root, relative)
                    with (
                        path.open("rb") as source,
                        archive.open("files/" + entry.name, "w") as out,
                    ):
                        size, digest = 0, hashlib.sha256()
                        while chunk := source.read(1024 * 1024):
                            _check_cancel(cancel)
                            size += len(chunk)
                            if size > entry.size:
                                raise BackupError(
                                    "来源文件在复制期间增长，请重新整理清单。"
                                )
                            digest.update(chunk)
                            out.write(chunk)
                        if size != entry.size or digest.hexdigest() != entry.sha256:
                            raise BackupError(
                                "来源文件在复制期间变化，请重新整理清单。"
                            )
        # Detect changes even to files copied early, and additions/deletions.
        current, _ = _inventory(plan.origin_state, plan.origin_content)
        if (
            tuple(
                (name, str(_source(root, rel)))
                for name, (root, rel) in sorted(current.items())
            )
            != plan.source_names
        ):
            raise BackupError("业务文件清单在备份期间变化，请重新整理清单。")
        for entry in plan.files:
            if entry.source and _hash_file(entry.source, cancel) != (
                entry.sha256,
                entry.size,
            ):
                raise BackupError("业务记录在备份期间变化，请结束保存后重新备份。")
        manifest = inspect_business_backup(temporary, cancel=cancel)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        _check_cancel(cancel)
        _publish_file(temporary, target)
        return {"path": str(target), "manifest": manifest}
    except (OSError, BadZipFile) as exc:
        raise BackupError("业务备份未完成，请核对磁盘与文件；原资料保留。") from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def restore_business_backup(filename, destination, *, expected_manifest, cancel=None):
    from .model_provider_settings import _assert_components_not_reparse

    target = Path(destination).absolute()
    _assert_components_not_reparse(target.parent)
    _source(target.parent, target.name)
    if target.exists() or not target.parent.is_dir():
        raise BackupError("请选择新的恢复子目录，不覆盖或合并原资料。")
    temporary = published = None
    try:
        with Path(filename).open("rb") as handle, ZipFile(handle) as archive:
            manifest = _manifest(archive)
            if manifest_revision(manifest) != expected_manifest:
                raise BackupError("业务备份清单已变化，请重新检查。")
            temporary = Path(
                tempfile.mkdtemp(prefix=".shchem-business-restore-", dir=target.parent)
            )
            for row in manifest["files"]:
                output = temporary / row["path"]
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("xb") as out:
                    _stream(archive, row, out, cancel)
            state = DesktopStateStore(temporary).snapshot()
            if "classroom_registry" in state:
                from .desktop_classroom_registry import ClassroomRegistry

                ClassroomRegistry._record(state)
            if "exam_mapping_profiles" in state:
                from .desktop_exam_mapping_profiles import ExamMappingProfiles

                ExamMappingProfiles._record(state)
            for path in (
                p
                for p in temporary.rglob("*")
                if p.suffix.casefold() in {".sqlite3", ".sqlite", ".db"}
            ):
                with closing(sqlite3.connect(path)) as database:
                    if database.execute("PRAGMA quick_check").fetchone() != ("ok",):
                        raise BackupError("恢复数据库完整性检查失败。")
            (temporary / MARKER).write_bytes(
                _bytes(
                    {
                        "schema_version": RESTORED_SCHEMA,
                        "business_schema": SCHEMA,
                        "created_at": utc_now(),
                        "backup_manifest_sha256": expected_manifest,
                        "references": manifest["references"],
                        "warnings": manifest["warnings"],
                    }
                )
            )
            _check_cancel(cancel)
            # Reserve a new directory; the launch marker is published last.
            target.mkdir(exist_ok=False)
            published = target
            for child in temporary.iterdir():
                if child.name != MARKER:
                    os.replace(child, target / child.name)
            os.replace(temporary / MARKER, target / MARKER)
            temporary.rmdir()
            temporary = None
            published = None
            return {
                "directory": str(target),
                "drafts": len(state["drafts"]),
                "references": 0,
                "current_state_unchanged": True,
                "business_scope": True,
            }
    except BackupError:
        raise
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, BadZipFile) as exc:
        raise BackupError("业务恢复未完成，当前工作台与原资料未改变。") from exc
    finally:
        if temporary:
            if temporary.resolve().parent != target.parent.resolve():
                raise BackupError("恢复临时目录不在指定位置，未执行清理。")
            shutil.rmtree(temporary, ignore_errors=True)
        if published:
            if published.resolve() != target.resolve():
                raise BackupError("恢复目录位置已变化，未执行清理。")
            shutil.rmtree(published, ignore_errors=True)

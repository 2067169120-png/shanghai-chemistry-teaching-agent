"""Small authenticated title-only recovery buffers, never formal drafts/history."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat as file_stat
import tempfile
from uuid import uuid4

from .desktop_state import DesktopStateError, _shared_lock, _state_file_lock

SCHEMA = "shchem.paper-title-recovery.v1"
MAX_BYTES = 8192
LIMITS = {"title": 120, "subtitle": 180}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


class PaperTitleRecovery:
    def __init__(self, root, scope):
        self.root = Path(root).resolve()
        self.profile, self.scope = os.path.normcase(str(self.root)), scope
        self.directory = self.root / "paper-title-recovery-v1"
        self.path = self.directory / (hashlib.sha256(scope.encode()).hexdigest() + ".json")
        self.key_path = self.root / ".paper-title-recovery.v1.key"
        self.token = None
        self._written_token = None
        self._stale = False
        self._invalidated = False
        self.initialized = False
        self.record = None

    def _safe_paths(self):
        # The configured root is resolved once to preserve supported C: -> E:
        # entry junctions. Descendant aliases are not part of that profile.
        if self.root.is_symlink() or self.root.is_junction() or self.root.resolve() != self.root:
            raise DesktopStateError("题名恢复缓冲配置目录发生链接变化，原文件保留。")
        for path, directory in ((self.directory, True), (self.path, False), (self.key_path, False)):
            if path.is_symlink() or path.is_junction():
                raise DesktopStateError("题名恢复缓冲路径含链接，原文件保留。")
            try:
                path.resolve().relative_to(self.root)
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            except ValueError as exc:
                raise DesktopStateError("题名恢复缓冲路径离开配置目录，原文件保留。") from exc
            if directory:
                valid = file_stat.S_ISDIR(metadata.st_mode)
            else:
                valid = file_stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
            if not valid:
                raise DesktopStateError("题名恢复缓冲或信任文件不是独立普通文件，原文件保留。")

    def _bytes(self):
        self._safe_paths()
        try:
            with self.path.open("rb") as handle:
                data = handle.read(MAX_BYTES + 1)
                stat = os.fstat(handle.fileno())
        except FileNotFoundError:
            return None, None
        if len(data) > MAX_BYTES:
            raise DesktopStateError("题名恢复缓冲超过短文本范围，原文件保留。")
        token = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns,
                 hashlib.sha256(data).hexdigest())
        return data, token

    def _key(self, create=False):
        with _shared_lock(self.key_path), _state_file_lock(self.key_path):
            self._safe_paths()
            if create:
                self.root.mkdir(parents=True, exist_ok=True)
                try:
                    descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(os.urandom(32)); handle.flush(); os.fsync(handle.fileno())
            key = self.key_path.read_bytes()
            if len(key) != 32:
                raise DesktopStateError("题名恢复缓冲信任文件损坏。")
            return key

    def _validated(self, data):
        record = json.loads(data)
        if (not isinstance(record, dict) or set(record) != {
                "schema_version", "profile", "scope", "binding", "revision", "texts", "signature"}
                or record["schema_version"] != SCHEMA or not isinstance(record["binding"], dict)
                or not isinstance(record["revision"], str) or len(record["revision"]) != 32
                or any(c not in "0123456789abcdef" for c in record["revision"])
                or not isinstance(record["texts"], dict) or not record["texts"]
                or set(record["texts"]) - set(LIMITS)
                or any(not isinstance(text, str) or len(text) > LIMITS[field]
                       for field, text in record["texts"].items())):
            raise DesktopStateError("题名恢复缓冲格式无法核验，原文件保留。")
        unsigned = {key: value for key, value in record.items() if key != "signature"}
        signature = hmac.new(self._key(), encoded(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(record["signature"], str) or not hmac.compare_digest(signature, record["signature"]):
            raise DesktopStateError("题名恢复缓冲未通过信任核验，原文件保留。")
        return record

    def read(self, binding):
        self.initialized = True
        try:
            data, token = self._bytes()
            self.token, self.record, self._stale = token, None, False
            self._invalidated = False
            if data is None:
                return {"status": "missing", "texts": {}}
            record = self._validated(data)
            self.record = deepcopy(record)
            own = record["profile"] == self.profile and record["scope"] == self.scope
            matched = own and record["binding"] == binding
            self._stale = not matched
            return {"status": "matched" if matched else "stale", "texts": deepcopy(record["texts"]),
                    "can_discard": own, "path": str(self.path)}
        except (OSError, ValueError, TypeError, KeyError, RecursionError, DesktopStateError):
            self._stale = True
            self._invalidated = True
            return {"status": "invalid", "texts": {}, "can_discard": False, "path": str(self.path)}

    def write(self, texts, binding):
        if (not isinstance(binding, dict) or not isinstance(texts, dict) or not texts or set(texts) - set(LIMITS)
                or any(not isinstance(text, str) or len(text) > LIMITS[field] for field, text in texts.items())):
            raise DesktopStateError("题名或范围说明超过短文本恢复范围。")
        if self._invalidated:
            raise DesktopStateError("题名恢复缓冲保存状态无法确认，请显式重新读取核对。")
        try:
            self._write(texts, binding)
        except (OSError, ValueError, TypeError, DesktopStateError):
            self._invalidated = True
            raise

    def _write(self, texts, binding):
        with _shared_lock(self.path), _state_file_lock(self.path):
            data, actual = self._bytes()
            if actual != self.token or (self._stale and self._written_token != self.token):
                raise DesktopStateError("题名恢复缓冲已有另一版本，请重新读取核对；原缓冲保留。")
            if data is not None:
                old = self._validated(data)
                if old["profile"] != self.profile or old["scope"] != self.scope:
                    raise DesktopStateError("不能覆盖另一配置或另一卷的题名恢复缓冲。")
            record = {"schema_version": SCHEMA, "profile": self.profile, "scope": self.scope,
                      "binding": deepcopy(binding), "revision": uuid4().hex, "texts": deepcopy(texts)}
            record["signature"] = hmac.new(self._key(create=True), encoded(record), hashlib.sha256).hexdigest()
            content = encoded(record)
            if len(content) > MAX_BYTES:
                raise DesktopStateError("题名恢复缓冲超过短文本范围。")
            self.directory.mkdir(parents=True, exist_ok=True)
            self._safe_paths()
            name = None
            try:
                descriptor, name = tempfile.mkstemp(prefix=".title-", suffix=".tmp", dir=self.directory)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content); handle.flush(); os.fsync(handle.fileno())
                if self._bytes()[1] != actual:
                    raise DesktopStateError("题名恢复缓冲在保存前发生变化，原缓冲保留。")
                self._safe_paths()
                os.replace(name, self.path)
                name = None
            finally:
                if name is not None:
                    Path(name).unlink(missing_ok=True)
            self.record = record
            self.token = self._bytes()[1]
            self._written_token, self._stale = self.token, False

    def clear(self, saved_texts=None, *, discard=False):
        with _shared_lock(self.path), _state_file_lock(self.path):
            data, actual = self._bytes()
            if data is None:
                self.token = self._written_token = None
                self.record, self._stale, self._invalidated = None, False, False
                return True
            if actual != self.token:
                return False
            record = self._validated(data)
            if record["profile"] != self.profile or record["scope"] != self.scope:
                return False
            if not discard and (not isinstance(saved_texts, dict) or any(
                    saved_texts.get(field) != text.strip() for field, text in record["texts"].items())):
                return False
            self._safe_paths()
            self.path.unlink()
            self.token = self._written_token = None
            self.record, self._stale, self._invalidated = None, False, False
            return True

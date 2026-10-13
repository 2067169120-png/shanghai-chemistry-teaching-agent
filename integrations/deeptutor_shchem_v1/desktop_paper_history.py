"""Profile-bound, bounded history envelopes; no caller supplies restore data.

The envelope and addressed state commit in the same desktop-state transaction.
The local trust key is deliberately outside that JSON and outside lesson backup
allowlists. Copying/importing a JSON history to another profile cannot authorize
restoring its rows or source identities.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path

from .desktop_state import DesktopStateError, DraftSnapshot

SCHEMA = "shchem.desktop-paper-history.v1"
BASKET_HISTORY_ID = "basket-edit-history.v1"
PAPER_HISTORY_ID = "paper-edit-history.v1"
HISTORY_LIMIT = 30
MAX_BYTES = 8 * 1024 * 1024
_KEY_FILE = ".paper-history-trust.v1.key"


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def target(snapshot):
    return {"revision": snapshot.revision, "content_sha256": snapshot.content_sha256}


def sources(rows):
    return {row["key"]: {"kind": row["kind"], "source_ref": deepcopy(row["source_ref"]),
                         "content_sha256": digest(row["content"]),
                         "dependency_sha256": row.get("dependency_sha256")} for row in rows}


def entry(state, action, selected, source_basis=None):
    result = {"state": deepcopy(state), "action": action, "selected": selected,
              "source_basis": deepcopy(source_basis)}
    _validate_entry(result)
    return result


def _validate_entry(value):
    if (not isinstance(value, dict) or set(value) != {"state", "action", "selected", "source_basis"}
            or not isinstance(value["action"], str) or not 1 <= len(value["action"]) <= 120
            or (value["source_basis"] is not None and not isinstance(value["source_basis"], dict))
            or (value["selected"] is not None and (not isinstance(value["selected"], str)
                                                  or not 1 <= len(value["selected"]) <= 512))):
        raise DesktopStateError("撤销操作信息无法核验，请重新读取。")


class PaperHistory:
    def __init__(self, store, identity, scope):
        self.store, self.identity, self.scope = store, identity, scope
        self.root = Path(store.root).resolve()
        self.profile = os.path.normcase(str(self.root))
        self.snapshot = None
        self.undo, self.redo = [], []
        self.reset = False

    def clear(self):
        self.snapshot = None
        self.undo, self.redo = [], []

    def _snapshot(self, value):
        raw = value["drafts"].get(self.identity)
        # Fingerprint damaged history too, without promoting it to a draft or
        # allowing it to block reading the valid addressed basket/paper.
        checksum = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        return DraftSnapshot(self.identity, deepcopy(raw),
            value.get("draft_revisions", {}).get(self.identity, "legacy:" + checksum), checksum)

    def _key(self, *, create=False):
        path = self.root / _KEY_FILE
        if self.root.is_symlink() or path.is_symlink():
            raise DesktopStateError("撤销记录信任文件无法核验，已保留当前状态。")
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(os.urandom(32))
                    handle.flush()
                    os.fsync(handle.fileno())
        try:
            value = path.read_bytes()
        except OSError as exc:
            raise DesktopStateError("撤销记录信任文件无法读取，已保留当前状态。") from exc
        if len(value) != 32:
            raise DesktopStateError("撤销记录信任文件损坏，已保留当前状态。")
        return value

    def read(self, value, current, basis, validator):
        self.snapshot = self._snapshot(value)
        self.undo, self.redo, self.reset = [], [], False
        raw = self.snapshot.record
        if raw is None:
            return
        try:
            if (not isinstance(raw, dict) or set(raw) != {"schema_version", "scope", "profile", "target", "basis", "undo", "redo", "signature"}
                    or raw["schema_version"] != SCHEMA or raw["scope"] != self.scope
                    or raw["profile"] != self.profile or raw["target"] != target(current)
                    or raw["basis"] != basis or not isinstance(raw["undo"], list)
                    or not isinstance(raw["redo"], list) or len(raw["undo"]) + len(raw["redo"]) > HISTORY_LIMIT
                    or len(encoded(raw)) > MAX_BYTES):
                raise ValueError("unbound history")
            unsigned = {key: row for key, row in raw.items() if key != "signature"}
            signature = hmac.new(self._key(), encoded(unsigned), hashlib.sha256).hexdigest()
            if not isinstance(raw["signature"], str) or not hmac.compare_digest(raw["signature"], signature):
                raise ValueError("untrusted history")
            for item in raw["undo"] + raw["redo"]:
                _validate_entry(item)
                validator(item["state"])
            self.undo, self.redo = deepcopy(raw["undo"]), deepcopy(raw["redo"])
        except (DesktopStateError, ValueError, TypeError, KeyError, OverflowError):
            # A corrupt or stale history is never used as restoration input and
            # never prevents the current saved basket/paper from being read.
            self.reset = True

    def guard(self, value):
        actual = self._snapshot(value)
        if self.snapshot is None or target(actual) != target(self.snapshot):
            raise DesktopStateError("撤销记录已被其他窗口更新，请重新读取；当前状态未覆盖。")

    def stage(self, value, undo, redo):
        self.guard(value)
        # Establish the trust anchor before committing either state or history.
        self._key(create=True)
        undo, redo = deepcopy(undo[-HISTORY_LIMIT:]), deepcopy(redo[-HISTORY_LIMIT:])
        while len(undo) + len(redo) > HISTORY_LIMIT:
            (undo if undo else redo).pop(0)
        while len(encoded({"undo": undo, "redo": redo})) > MAX_BYTES - 4096:
            (undo if undo else redo).pop(0)
        value["drafts"][self.identity] = {"pending": True, "undo": undo, "redo": redo}

    def finalize(self, value, current, basis):
        staged = value["drafts"][self.identity]
        raw = {"schema_version": SCHEMA, "scope": self.scope, "profile": self.profile,
               "target": target(current), "basis": deepcopy(basis),
               "undo": staged["undo"], "redo": staged["redo"]}
        while len(encoded(raw)) + 80 > MAX_BYTES and (raw["undo"] or raw["redo"]):
            (raw["undo"] if raw["undo"] else raw["redo"]).pop(0)
        raw["signature"] = hmac.new(self._key(), encoded(raw), hashlib.sha256).hexdigest()
        if len(encoded(raw)) > MAX_BYTES:
            raise DesktopStateError("撤销记录超过可保存范围，当前状态未覆盖。")
        value["drafts"][self.identity] = raw

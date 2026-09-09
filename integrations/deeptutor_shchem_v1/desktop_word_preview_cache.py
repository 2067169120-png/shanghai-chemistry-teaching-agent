"""Disposable compressed native previews, bound to source bytes and reader revision."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from .desktop_preparation_sources import _digest
from .word_native_text import NATIVE_WORD_TEXT_REVISION

CACHE_REVISION = "20260910-native-word-preview-cache-v1"
MAX_PREVIEW_BYTES = 48 * 1024 * 1024


class WordPreviewCache:
    """An optimization only; unreadable/stale cache entries are rebuilt from source."""

    def __init__(self, root):
        self.root = Path(root)

    def _path(self, source_sha256, source_name):
        identity = _digest([CACHE_REVISION, NATIVE_WORD_TEXT_REVISION, source_sha256, source_name])
        return self.root / (identity + ".json.gz")

    @staticmethod
    def _valid(preview, source_sha256, source_name):
        return (
            isinstance(preview, dict)
            and preview.get("source_sha256") == source_sha256
            and preview.get("source_name") == source_name
            and preview.get("extraction_revision") == NATIVE_WORD_TEXT_REVISION
            and preview.get("revision") == _digest({key: value for key, value in preview.items() if key != "revision"})
            and isinstance(preview.get("blocks"), list)
            and isinstance(preview.get("assets"), list)
            and isinstance(preview.get("sections"), list)
            and isinstance(preview.get("warnings"), list)
        )

    def load(self, source_bytes, source_name):
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        path = self._path(source_sha256, source_name)
        try:
            if self.root.is_symlink() or self.root.is_junction() or path.is_symlink() or not path.is_file():
                return None
            with gzip.open(path, "rb") as stream:
                raw = stream.read(MAX_PREVIEW_BYTES + 1)
            if len(raw) > MAX_PREVIEW_BYTES:
                return None
            record = json.loads(raw)
            if not isinstance(record, dict) or record.get("cache_revision") != CACHE_REVISION:
                return None
            preview = record.get("preview")
            return preview if self._valid(preview, source_sha256, source_name) else None
        except (OSError, EOFError, ValueError, TypeError, RecursionError):
            return None

    def save(self, source_bytes, source_name, preview):
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        if not self._valid(preview, source_sha256, source_name):
            raise ValueError("Native preview does not match its source")
        if self.root.is_symlink() or self.root.is_junction():
            return False
        raw = json.dumps({"cache_revision": CACHE_REVISION, "preview": preview}, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(raw) > MAX_PREVIEW_BYTES:
            return False
        target = self._path(source_sha256, source_name)
        temporary = self.root / (".preview-" + uuid4().hex + ".tmp")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                return False
            with temporary.open("xb") as stream:
                stream.write(gzip.compress(raw, compresslevel=5, mtime=0))
            os.replace(temporary, target)
            return True
        except OSError:
            return False
        finally:
            try:
                if temporary.is_file() and not temporary.is_symlink():
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A disposable cache must not block reading the source.

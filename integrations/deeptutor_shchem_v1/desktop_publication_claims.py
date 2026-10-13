"""Shared ownership of immutable lesson files across publication entry points."""
from hashlib import sha256
import os
from pathlib import Path

from .desktop_textbook_preparation_crops import _safe_state_path
from .desktop_word_source_reference import _COMMIT_LOCK

# Keep the path/token protocol shared with the native image-batch publisher.
_CLAIMS = {}
_OWNED = {}


def claim_file(path, raw, token, *, created=False, asset_id=None, file_identity=None):
    """Claim a checked file; only an exclusive creator can own its deletion."""
    path = Path(path)
    with _COMMIT_LOCK:
        _safe_state_path(path)
        key = os.path.normcase(str(path.absolute()))
        if created:
            if file_identity is None:
                raise ValueError('自有文件须保留独占创建时的暂存文件身份。')
            device, inode = file_identity
            _OWNED[key] = (path, device, inode, sha256(raw).hexdigest(), asset_id)
        _CLAIMS.setdefault(key, set()).add(token)
        return key


def release_file_claims(keys, token, *, accepted=False, referenced_assets=()):
    """The last unaccepted claimant cleans up; any accepted use is durable."""
    protected = {row['asset_id'] if isinstance(row, dict) else row for row in referenced_assets}
    with _COMMIT_LOCK:
        for key in keys:
            _CLAIMS.get(key, set()).discard(token)
            if not _CLAIMS.get(key): _CLAIMS.pop(key, None)
        for key in keys:
            owned = _OWNED.get(key)
            if owned is None: continue
            path, device, inode, expected, asset_id = owned
            if accepted or asset_id in protected:
                _OWNED.pop(key, None)
                continue
            if _CLAIMS.get(key): continue
            try:
                _safe_state_path(path)
                if path.is_file() and not path.is_symlink():
                    info = path.stat()
                    if ((info.st_dev, info.st_ino) == (device, inode)
                            and sha256(path.read_bytes()).hexdigest() == expected):
                        path.unlink()
            except (ValueError, OSError): pass  # External replacements remain untouched.
            _OWNED.pop(key, None)

"""Nonblocking process locks whose identity does not depend on TEMP or TMP."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import os
from pathlib import Path
import stat


def canonical_lock_identity(path):
    return os.path.normcase(str(Path(path).resolve()))


@contextmanager
def _windows_mutex(identity):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    wait = kernel.WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    release = kernel.ReleaseMutex
    release.argtypes = (wintypes.HANDLE,)
    release.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL

    name = "Global\\ShanghaiChem.State.v2." + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    handle = create(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    owned = False
    try:
        result = wait(handle, 0)
        # Abandonment transfers ownership. Store callers still re-read and
        # validate durable state and their expected revision before any write.
        if result in (0, 0x80):
            owned = True
        elif result == 0x102:
            raise BlockingIOError(errno.EWOULDBLOCK, "State is locked by another process")
        else:
            raise ctypes.WinError(ctypes.get_last_error())
        yield
    finally:
        try:
            if owned and not release(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            close(handle)


@contextmanager
def _posix_file_lock(identity):
    import fcntl

    # /tmp is the machine namespace, independent of either process's TMPDIR.
    directory = Path("/tmp") / f"shchem-state-locks-{os.getuid()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    path = directory / f"{digest}.lock"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if directory.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError(errno.EACCES, "State lock directory is not owned by this user")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise OSError(errno.EACCES, "State lock is not an independent user-owned file")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def process_state_lock(path):
    identity = canonical_lock_identity(path)
    with (_windows_mutex(identity) if os.name == "nt" else _posix_file_lock(identity)):
        yield

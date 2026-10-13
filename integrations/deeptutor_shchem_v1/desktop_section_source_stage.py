"""Exact source proof and memoization owned by one native compilation only."""
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import stat
import struct
import sys
from threading import RLock
from urllib.parse import parse_qs, unquote, urlsplit
from importlib import _bootstrap_external as _runtime_loader
import inspect as _runtime_inspect
import linecache as _runtime_linecache
import tokenize as _runtime_tokenize
import sysconfig
from types import ModuleType

from .desktop_lesson_composition import digest, validate_resource
from .desktop_textbook_preparation_crops import _safe_state_path
from .reader_cancellation import check_read_cancelled

_ACTIVE = ContextVar('shchem_section_source_reads', default=None)
_OBSERVING = ContextVar('shchem_section_source_observing', default=False)
_REPORTER = ContextVar('shchem_section_source_reporter', default=None)
_INSTALL_LOCK, _INSTALLED = RLock(), False

# These are the interpreter's configured code libraries, including the
# literal/physical pair for a linked venv. They never exempt a plain read.
_RUNTIME_CODE_ROOTS = tuple({(
    os.path.normcase(str(Path(value).absolute())),
    os.path.normcase(str(Path(value).resolve())))
    for key, value in sysconfig.get_paths().items()
    if key in {'stdlib', 'platstdlib', 'purelib', 'platlib'}
})


def _path_key(value):
    if not isinstance(value, (str, bytes, os.PathLike)):
        return None
    return os.path.normcase(os.path.abspath(os.fsdecode(value)))


def _trusted_runtime_code(value):
    key = _path_key(value)
    if key is None:
        return False
    if not any(key == literal or key.startswith(literal + os.sep)
               or key == resolved or key.startswith(resolved + os.sep)
               for literal, resolved in _RUNTIME_CODE_ROOTS):
        return False
    physical = os.path.normcase(str(Path(os.fsdecode(value)).resolve()))
    return any((key == literal or key.startswith(literal + os.sep)
                or key == resolved or key.startswith(resolved + os.sep))
               and (physical == resolved or physical.startswith(resolved + os.sep))
               for literal, resolved in _RUNTIME_CODE_ROOTS)


def _runtime_read(event, value):
    """Identify actual interpreter code I/O; paths alone grant no exemption."""
    key = _path_key(value)
    if key is None or not _trusted_runtime_code(value):
        return False
    frame = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename == __file__:
        frame = frame.f_back
    if frame is None:
        return False
    if event in {'os.listdir', 'os.scandir'}:
        finder = frame.f_locals.get('self')
        return (frame.f_code is _runtime_loader.FileFinder._fill_cache.__code__
            and type(finder) is _runtime_loader.FileFinder
            and _path_key(finder.path) == key
            and sys.path_importer_cache.get(finder.path) is finder)
    if frame.f_code is _runtime_loader.FileLoader.get_data.__code__:
        caller = frame.f_back
        if caller is None or caller.f_code is not _runtime_loader.SourceLoader.get_code.__code__:
            return False
        loader, fullname = caller.f_locals.get('self'), caller.f_locals.get('fullname')
        module = sys.modules.get(fullname)
        if not isinstance(module, ModuleType):
            return False
        spec = module.__dict__.get('__spec__')
        return (spec is not None and spec.loader is loader
            and _path_key(spec.origin) == _path_key(caller.f_locals.get('source_path'))
            and key in {_path_key(caller.f_locals.get('source_path')),
                        _path_key(caller.f_locals.get('bytecode_path'))})
    if frame.f_code is not _runtime_tokenize.open.__code__:
        return False
    caller = frame.f_back
    if caller is None or caller.f_code is not _runtime_linecache.updatecache.__code__:
        return False
    while caller is not None and caller.f_code is not _runtime_inspect.findsource.__code__:
        caller = caller.f_back
    if caller is None:
        return False
    module = caller.f_locals.get('module')
    if not isinstance(module, ModuleType):
        return False
    spec = module.__dict__.get('__spec__')
    loader = spec.loader if spec is not None else None
    return (type(loader) in {_runtime_loader.SourceFileLoader, _runtime_loader.SourcelessFileLoader}
        and _path_key(spec.origin) == key
        and _path_key(vars(loader).get('path')) == key)


def _runtime_data_alias(value):
    """Normalize one configured venv alias for a real package data reader.

    Package-declared sibling metadata remains a captured full-SHA file. A
    plain read or explicit material never receives this alias permission.
    """
    if not _trusted_runtime_code(value):
        return None
    frame = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename == __file__:
        frame = frame.f_back
    if frame is None:
        return None
    module = sys.modules.get(frame.f_globals.get('__name__'))
    if not isinstance(module, ModuleType) or frame.f_globals is not module.__dict__:
        return None
    spec = module.__dict__.get('__spec__')
    loader = spec.loader if spec is not None else None
    actor = frame.f_locals.get('self')
    actor_type = type(actor)
    actor_module = sys.modules.get(actor_type.__module__)
    actor_spec = actor_module.__dict__.get('__spec__') if isinstance(actor_module, ModuleType) else None
    actor_loader = actor_spec.loader if actor_spec is not None else None
    if (type(loader) is not _runtime_loader.SourceFileLoader
            or _path_key(spec.origin) != _path_key(frame.f_code.co_filename)
            or _path_key(vars(loader).get('path')) != _path_key(spec.origin)
            or not _trusted_runtime_code(spec.origin)
            or type(actor_loader) is not _runtime_loader.SourceFileLoader
            or _path_key(vars(actor_loader).get('path')) != _path_key(actor_spec.origin)
            or not _trusted_runtime_code(actor_spec.origin)
            or actor_module.__dict__.get(actor_type.__name__) is not actor_type
            or _path_key(getattr(actor, '_FILE', None)) != _path_key(value)
            or Path(os.fsdecode(value)).parent != Path(actor_spec.origin).parent):
        return None
    if not any(module.__dict__.get(cls.__name__) is cls
               and getattr(cls.__dict__.get(frame.f_code.co_name), '__code__', None) is frame.f_code
               for cls in actor_type.__mro__):
        return None
    key, actual = _path_key(value), Path(os.fsdecode(value)).resolve()
    for literal, physical in _RUNTIME_CODE_ROOTS:
        if literal != physical and key.startswith(literal + os.sep):
            expected = Path(physical) / Path(key).relative_to(Path(literal))
            # An extra reparse below the configured alias is not that alias.
            _safe_state_path(expected)
            if _path_key(actual) == _path_key(expected):
                return expected
    return None

if os.name == 'nt':
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in (
            'CreationTime', 'LastAccessTime', 'LastWriteTime', 'ChangeTime')] + [
                ('FileAttributes', wintypes.DWORD)]

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD]
    _kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL

    class _Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
            ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]

    _kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel.CreateEventW.restype = wintypes.HANDLE
    _kernel.ResetEvent.argtypes = [wintypes.HANDLE]
    _kernel.ResetEvent.restype = wintypes.BOOL
    _kernel.ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(_Overlapped), ctypes.c_void_p]
    _kernel.ReadDirectoryChangesW.restype = wintypes.BOOL
    _kernel.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped),
                                          ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    _kernel.GetOverlappedResult.restype = wintypes.BOOL
    _kernel.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
    _kernel.CancelIoEx.restype = wintypes.BOOL

    class _WindowsDirectoryWatch:
        """One notification issued by the stage's owned persistent observer."""
        def __init__(self, path):
            self.handle, self.event, self.armed = None, None, False
            self.buffer = (wintypes.DWORD * (64 * 1024 // 4))()
            self.overlapped = _Overlapped()
            try:
                handle = _kernel.CreateFileW(str(path), 1, 7, None, 3, 0x42000000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_last_error(), 'Cannot verify source directory changes')
                self.handle = handle
                self.event = _kernel.CreateEventW(None, True, False, None)
                if not self.event:
                    raise OSError(ctypes.get_last_error(), 'Cannot verify source directory changes')
                self.overlapped.hEvent = self.event
                self._arm()
            except BaseException:
                self.close()
                raise

        def _arm(self):
            _kernel.ResetEvent(self.event)
            self.overlapped = _Overlapped()
            self.overlapped.hEvent = self.event
            # Actual source filename changes, writes, size and attributes;
            # read access is excluded so native reading does not signal itself.
            if not _kernel.ReadDirectoryChangesW(self.handle, self.buffer, ctypes.sizeof(self.buffer),
                    False, 0x5F, None, ctypes.byref(self.overlapped), None):
                raise OSError(ctypes.get_last_error(), 'Cannot verify source directory changes')
            self.armed = True

        def check(self, filenames=(), *, membership=True):
            names = {name.casefold() for name in filenames}
            while True:
                transferred = wintypes.DWORD()
                if not _kernel.GetOverlappedResult(self.handle, ctypes.byref(self.overlapped),
                                                   ctypes.byref(transferred), False):
                    error = ctypes.get_last_error()
                    if error == 996:  # ERROR_IO_INCOMPLETE: watch remains pending.
                        return
                    raise OSError(error, 'Cannot verify source directory changes')
                if not transferred.value:
                    raise ValueError('选材来源变化通知无法完整核对，请重新选择。')
                raw = ctypes.string_at(ctypes.addressof(self.buffer), transferred.value)
                offset = 0
                while True:
                    if offset + 12 > len(raw):
                        raise ValueError('选材来源变化通知无法完整核对，请重新选择。')
                    step, action, length = struct.unpack_from('<III', raw, offset)
                    end = offset + 12 + length
                    if end > len(raw) or length % 2:
                        raise ValueError('选材来源变化通知无法完整核对，请重新选择。')
                    name = raw[offset + 12:end].decode('utf-16le').casefold()
                    if (name in names or membership and action in {1, 2, 4, 5}):
                        raise ValueError('选材来源文件或目录在本阶段变化，请重新选择。')
                    if not step:
                        break
                    if step < 12 or offset + step >= len(raw):
                        raise ValueError('选材来源变化通知无法完整核对，请重新选择。')
                    offset += step
                self._arm()

        def close(self):
            if self.handle is not None:
                if self.armed:
                    _kernel.CancelIoEx(self.handle, ctypes.byref(self.overlapped))
                    transferred = wintypes.DWORD()
                    # Retain buffer/OVERLAPPED until cancellation completes.
                    _kernel.GetOverlappedResult(self.handle, ctypes.byref(self.overlapped),
                                               ctypes.byref(transferred), True)
                    self.armed = False
                _kernel.CloseHandle(self.handle)
                self.handle = None
            if self.event is not None:
                _kernel.CloseHandle(self.event)
                self.event = None


def _change_time(fd):
    if os.name != 'nt':
        return os.fstat(fd).st_ctime_ns
    value = _FileBasicInfo()
    if not _kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(fd), 0,
                                               ctypes.byref(value), ctypes.sizeof(value)):
        raise OSError(ctypes.get_last_error(), 'Cannot verify file ChangeTime')
    return value.ChangeTime


def _directory_change_time(path):
    if os.name != 'nt':
        return path.stat().st_ctime_ns
    handle = _kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), 'Cannot verify directory ChangeTime')
    try:
        value = _FileBasicInfo()
        if not _kernel.GetFileInformationByHandleEx(handle, 0, ctypes.byref(value), ctypes.sizeof(value)):
            raise OSError(ctypes.get_last_error(), 'Cannot verify directory ChangeTime')
        return value.ChangeTime
    finally:
        _kernel.CloseHandle(handle)


def _audit(event, args):
    active = _ACTIVE.get()
    if active is None or _OBSERVING.get():
        return
    token = _OBSERVING.set(True)
    try:
        if event in {'open', 'os.scandir', 'os.listdir'} and not active.material_path(args[0]):
            if _runtime_read(event, args[0]):
                active.statistics['runtime_code_reads'] += 1
                return
        if event == 'open':
            name, mode, flags = args
            writing = (isinstance(mode, str) and any(char in mode for char in 'wax+')
                or bool(int(flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
            if not writing:
                normalized = None if active.material_path(name) else _runtime_data_alias(name)
                if normalized is not None:
                    active.statistics['runtime_data_alias_reads'] += 1
                active.observe_file(normalized if normalized is not None else name)
        elif event in {'os.scandir', 'os.listdir'}:
            active.observe_directory(args[0])
        elif event == 'sqlite3.connect' and isinstance(args[0], str):
            # SQLite opens the named readonly database in C, without Python's
            # open event. Record its actual mode=ro URI, not presumed stores.
            parsed = urlsplit(args[0])
            if parsed.scheme == 'file' and parse_qs(parsed.query).get('mode') == ['ro']:
                path = unquote(parsed.path)
                if parsed.netloc:
                    path = '//' + parsed.netloc + path
                elif os.name == 'nt' and len(path) > 2 and path[0] == '/' and path[2] == ':':
                    path = path[1:]
                active.observe_file(path)
    finally:
        _OBSERVING.reset(token)


def _install_observer():
    global _INSTALLED
    with _INSTALL_LOCK:
        if not _INSTALLED:
            sys.addaudithook(_audit)
            _INSTALLED = True


def register_staging_root(path):
    """Exclude a task root actually owned by a native StagingFacade."""
    active = _ACTIVE.get()
    if active is not None:
        active.exclude_root(path)


@contextmanager
def source_stage_reporter(callback):
    """Receive independent phase evidence in this caller's context only."""
    token = _REPORTER.set(callback)
    try:
        yield
    finally:
        _REPORTER.reset(token)


class SourceFileSet:
    """Capture actual read opens/enumerations, then hash every distinct file.

    No filename extension or presumed catalog list chooses the source set.
    Stat/ChangeTime can reject changes but never replace the full-byte SHA.
    Audit observation is inactive in every unrelated thread and context.
    """
    def __init__(self, *, excluded_roots=()):
        self.files, self.directories, self._watches, self._watch_files, self._lock = {}, {}, {}, {}, RLock()
        self._watch_executor = None
        self.excluded_roots = []
        self.protected_materials = set()
        for path in excluded_roots:
            self.exclude_root(path)
        self.statistics = {'readonly_opens': 0, 'opened_file_size_bytes': 0,
            'directory_enumerations': 0, 'source_bytes_hashed': 0, 'sha_passes': 0}
        self.statistics['runtime_code_reads'] = 0
        self.statistics['runtime_data_alias_reads'] = 0

    def protect_material(self, value):
        key = _path_key(value)
        if key is not None:
            self.protected_materials.add(key)
            # Explicit source evidence remains material through any alias.
            path = Path(os.fsdecode(value))
            if path.is_absolute():
                self.protected_materials.add(os.path.normcase(str(path.resolve())))

    def material_path(self, value):
        key = _path_key(value)
        if key in self.protected_materials or key in self.files or key in self.directories:
            return True
        if key is None or not self.protected_materials:
            return False
        return os.path.normcase(str(Path(os.fsdecode(value)).resolve())) in self.protected_materials

    def exclude_root(self, value):
        raw = Path(os.fsdecode(value)).absolute()
        _safe_state_path(raw)
        path = Path(os.path.abspath(str(raw)))
        if path != raw:
            _safe_state_path(path)
        self.excluded_roots.append(os.path.normcase(str(path.resolve())))

    def _source_path(self, value):
        if isinstance(value, int):
            # These native readers use named paths. A new descriptor-only
            # reader must expose its path before it can share this proof.
            raise ValueError('选材来源使用了无法绑定路径的文件描述符。')
        if value is None:
            value = os.curdir
        if not isinstance(value, (str, bytes, os.PathLike)):
            raise ValueError('选材来源路径无法核对。')
        raw = Path(os.fsdecode(value)).absolute()
        _safe_state_path(raw)
        path = Path(os.path.abspath(str(raw)))
        # Check before resolution/exclusion so a symlink inside an owned
        # staging tree cannot conceal an external original source.
        if path != raw:
            _safe_state_path(path)
        path = path.resolve()
        key = os.path.normcase(str(path))
        if any(key == root or key.startswith(root + os.sep) for root in self.excluded_roots):
            return None
        return path

    @staticmethod
    def _identity(info):
        # Windows Python 3.12 stat/fstat expose different legacy ctime
        # semantics. Use the native handle ChangeTime for change evidence.
        # Path stat also synthesizes execute bits for .exe/.com/.bat/.cmd;
        # descriptor stat has no filename and cannot synthesize those bits.
        # Keep the actual file type/read-write bits and native ChangeTime.
        mode = info.st_mode & ~0o111 if os.name == 'nt' else info.st_mode
        return (info.st_dev, info.st_ino, mode, info.st_size, info.st_mtime_ns)

    def _watch(self, key, path):
        if key not in self._watches:
            # Windows cancels pending notification I/O when its issuing
            # reader-pool thread exits. One owned thread issues/checks/rearms
            # all phase watches; it receives no native-reader ContextVars.
            if self._watch_executor is None:
                self._watch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='shchem-source-proof')
            self._watch_executor.submit(self._create_owned_watch, key, path).result()

    def _create_owned_watch(self, key, path):
        # Ownership transfers on the issuer thread before a caller can lose
        # the Future result to KeyboardInterrupt or another BaseException.
        self._watches[key] = None
        try:
            self._watches[key] = _WindowsDirectoryWatch(path)
        except BaseException:
            self._watches.pop(key, None)
            raise

    def _check_watches(self):
        for key, watch in self._watches.items():
            self._watch_executor.submit(watch.check, self._watch_files.get(key, ()),
                                        membership=key in self.directories).result()

    def _fingerprint(self, path):
        check_read_cancelled()
        _safe_state_path(path)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('选材来源不再是可核对的普通文件。')
        content, count = sha256(), 0
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            changed = _change_time(stream.fileno())
            if self._identity(opened) != self._identity(before):
                raise ValueError('选材来源在打开期间变化。')
            while chunk := stream.read(1024 * 1024):
                check_read_cancelled()
                content.update(chunk)
                count += len(chunk)
            after_read = os.fstat(stream.fileno())
            after_change = _change_time(stream.fileno())
            _safe_state_path(path)
            after = path.stat()
        if (self._identity(before) != self._identity(after_read)
                or self._identity(before) != self._identity(after)
                or changed != after_change or count != before.st_size):
            raise ValueError('选材来源在读取期间变化。')
        self.statistics['source_bytes_hashed'] += count
        self.statistics['sha_passes'] += 1
        return {'identity': self._identity(before), 'change_time': changed,
                'sha256': content.hexdigest()}

    def _directory_fingerprint(self, path):
        check_read_cancelled()
        _safe_state_path(path)
        before = path.stat()
        change_time = _directory_change_time(path)
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError('选材来源目录已经变化。')
        entries = []
        with os.scandir(path) as rows:
            for row in rows:
                check_read_cancelled()
                # Windows DirEntry.stat() has zero inode/device fields. The
                # actual path stat supplies the child identity needed here.
                info = Path(row.path).stat(follow_symlinks=False)
                # Child content is checked when actually read. Here prove
                # actual membership/type/identity, including excluded suffixes.
                entries.append((row.name, info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)))
        after = path.stat()
        if (self._identity(before) != self._identity(after)
                or change_time != _directory_change_time(path)):
            raise ValueError('选材来源目录在枚举期间变化。')
        return self._identity(before), change_time, tuple(sorted(entries))

    def observe_file(self, value):
        path = self._source_path(value)
        if path is None:
            return
        with self._lock:
            if not path.is_file():
                return
            self.statistics['readonly_opens'] += 1
            self.statistics['opened_file_size_bytes'] += path.stat().st_size
            key = os.path.normcase(str(path))
            if key not in self.files:
                if os.name == 'nt':
                    parent_key = os.path.normcase(str(path.parent))
                    self._watch(parent_key, path.parent)
                    self._watch_files.setdefault(parent_key, set()).add(path.name)
                self.files[key] = (path, self._fingerprint(path))

    def observe_directory(self, value):
        path = self._source_path(value)
        if path is None:
            return
        with self._lock:
            if not path.is_dir():
                return
            self.statistics['directory_enumerations'] += 1
            key = os.path.normcase(str(path))
            if key not in self.directories:
                if os.name == 'nt':
                    self._watch(key, path)
                self.directories[key] = (path, self._directory_fingerprint(path))

    @contextmanager
    def capture(self):
        _install_observer()
        token = _ACTIVE.set(self)
        try:
            yield
        finally:
            _ACTIVE.reset(token)

    def verify(self):
        token = _OBSERVING.set(True)
        try:
            with self._lock:
                self._check_watches()
                for path, before in self.directories.values():
                    if self._directory_fingerprint(path) != before:
                        raise ValueError('选材来源目录的实际文件集合或身份在本阶段变化。')
                for path, before in self.files.values():
                    if self._fingerprint(path) != before:
                        raise ValueError('选材来源的完整字节或文件身份在本阶段变化，请重新选择。')
                self._check_watches()
        except OSError as exc:
            raise ValueError('选材来源文件或目录已经缺失或无法完整读取，请重新选择。') from exc
        finally:
            _OBSERVING.reset(token)

    def close(self):
        with self._lock:
            executor = self._watch_executor
            if executor is not None:
                try:
                    # Queue cleanup after every in-flight creation/check. A
                    # main-thread registry scan could miss a late constructor.
                    executor.submit(self._close_owned_watches).result()
                finally:
                    # Cleanup tasks must run even if this caller is interrupted.
                    executor.shutdown(wait=True, cancel_futures=False)
                    self._watch_executor = None

    def _close_owned_watches(self):
        for watch in self._watches.values():
            if watch is not None:
                watch.close()
        self._watches.clear()
        self._watch_files.clear()

    def __del__(self):
        try:
            self.close()
        except (AttributeError, OSError, RuntimeError):
            pass


class SectionSourceStage:
    """Complete context/entry and typed knowledge/sealed Original once each."""
    def __init__(self, facade, item=None, resources=None, assets=None, *, excluded_roots=()):
        self.facade, self.item = facade, item
        self.resources, self.assets = deepcopy(resources or []), deepcopy(assets or [])
        self.contexts, self.knowledge = {}, {}
        self.reads, self._read_keys = {}, {}
        self._reads_enabled = True
        self.files = SourceFileSet(excluded_roots=excluded_roots)
        self.closed = False
        self.statistics = {'context_checks': 0, 'context_hits': 0, 'knowledge_checks': 0,
            'knowledge_hits': 0, 'native_context_tail': 0, 'native_knowledge_tail': 0,
            'read_checks': {}, 'read_hits': {}, 'native_read_tail': {}}

    def _open(self):
        check_read_cancelled()
        if self.closed:
            raise ValueError('选材来源核验阶段已经结束。')

    def read_memo(self, domain, scope, native_reader, *, sealed_identity=None):
        """Reuse a detached exact projection only until this compile's native tail.

        The request scope selects a sealed key; the complete original identity
        returned by the native reader is part of that key. Every new domain
        retains its own native reader and compares the complete result at finish.
        """
        self._open()
        if not self._reads_enabled:
            with self.files.capture():
                result = native_reader()
            check_read_cancelled()
            return deepcopy(result)
        scope_key = (domain, digest(scope))
        key = self._read_keys.get(scope_key)
        if key is None:
            with self.files.capture():
                value = native_reader()
            check_read_cancelled()
            identity = sealed_identity(value) if sealed_identity is not None else value
            key = (*scope_key, digest(identity))
            self.reads[key] = (deepcopy(value), native_reader)
            self._read_keys[scope_key] = key
            counts = self.statistics['read_checks']
        else:
            counts = self.statistics['read_hits']
        counts[domain] = counts.get(domain, 0) + 1
        result = deepcopy(self.reads[key][0])
        check_read_cancelled()
        return result

    def context(self, context, *, item=None, resources=None, assets=None):
        from .desktop_section_materials import recheck_section_material_context
        self._open()
        item = self.item if item is None else item
        resources = self.resources if resources is None else resources
        assets = self.assets if assets is None else assets
        if not isinstance(resources, list):
            raise ValueError('当前课堂资源列表无法核对，请重新打开。')
        entry = validate_resource(item) if item is not None else None
        entry_identity = ({name: entry[name] for name in ('id', 'original_sha256', 'original')}
                          if entry is not None else None)
        dependencies = {}
        if entry is not None and entry['original']['kind'] == 'textbook_image':
            from .desktop_preparation_images import normalize_image_assets
            linked = entry['original'].get('selected_from_knowledge')
            if linked is not None:
                dependencies['knowledge'] = [{name: validate_resource(row)[name]
                    for name in ('id', 'original_sha256', 'original')}
                    for row in resources if isinstance(row, dict) and row.get('id') == linked['resource_id']]
            image_ids = {row['value'] for row in entry['original']['blocks'] if row['kind'] == 'image'}
            dependencies['images'] = [row for row in normalize_image_assets(assets)
                                      if row['asset_id'] in image_ids]
        key = digest({'context': context, 'entry': entry_identity, 'dependencies': dependencies})
        if key not in self.contexts:
            with self.files.capture():
                recheck_section_material_context(self.facade, item, resources, assets, context)
            self.contexts[key] = (deepcopy(context), item, deepcopy(resources), deepcopy(assets))
            self.statistics['context_checks'] += 1
        else:
            self.statistics['context_hits'] += 1
        return True

    def import_knowledge(self, selection, original=None):
        from .desktop_composition_sources import import_knowledge_note
        self._open()
        if isinstance(original, dict) and isinstance(original.get('evidence'), dict):
            self.files.protect_material(original['evidence'].get('source_path'))
        key = digest({'selection': selection, 'sealed_original': original})
        if key not in self.knowledge:
            with self.files.capture():
                value = import_knowledge_note(self.facade, selection)
            self.knowledge[key] = (deepcopy(selection), deepcopy(value))
            self.statistics['knowledge_checks'] += 1
        else:
            self.statistics['knowledge_hits'] += 1
        return deepcopy(self.knowledge[key][1])

    def finish(self):
        from .desktop_composition_sources import import_knowledge_note
        from .desktop_knowledge_questions import _source_content
        from .desktop_section_materials import recheck_section_material_context
        try:
            self._open()
            self._reads_enabled = False
            self.files.verify()
            for (domain, _scope, _seal), (before, native_reader) in self.reads.items():
                with self.files.capture():
                    after = native_reader()
                counts = self.statistics['native_read_tail']
                counts[domain] = counts.get(domain, 0) + 1
                if after != before:
                    raise ValueError('选材来源的精确读取结果在本阶段变化，请重新选择。')
                check_read_cancelled()
            # Fresh native calls cover dynamic/in-memory ports and source
            # discovery as well as files, even when all stat values agree.
            for context, item, resources, assets in self.contexts.values():
                with self.files.capture():
                    recheck_section_material_context(self.facade, item, resources, assets, context)
                self.statistics['native_context_tail'] += 1
            for selection, before in self.knowledge.values():
                with self.files.capture():
                    after = import_knowledge_note(self.facade, selection)
                self.statistics['native_knowledge_tail'] += 1
                if _source_content(after['original']) != _source_content(before['original']):
                    raise ValueError('教材知识原文在本阶段变化，请重新选择。')
            self.files.verify()
            check_read_cancelled()
        finally:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._reads_enabled = False
        self.reads.clear()
        self._read_keys.clear()
        self.contexts.clear()
        self.knowledge.clear()
        self.files.close()
        self.statistics.update(self.files.statistics)
        self.statistics.update(source_files=len(self.files.files),
                               source_directories=len(self.files.directories))
        reporter = _REPORTER.get()
        if reporter:
            reporter(deepcopy(self.statistics))

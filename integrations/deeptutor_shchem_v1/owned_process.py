"""Bounded external commands with ownership established before execution.

Windows children start suspended and join a private Job Object before their
first instruction. No process is ever selected by name or by a later PID scan.
COM servers are special: they are not assumed to belong to the helper's tree.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

from .reader_cancellation import ReadCancelled, check_read_cancelled


_PENDING_RESOURCES = set()
_PENDING_LOCK = threading.Lock()


class ProcessTimeout(RuntimeError):
    code = "office_conversion_timeout"
    message_zh = "本次文档转换超时，未发布新的结果。"

    def __init__(self):
        super().__init__(self.message_zh)


if os.name == "nt":
    from ctypes import wintypes as w
    import msvcrt

    class _Startup(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
                    ("title", w.LPWSTR), ("x", w.DWORD), ("y", w.DWORD),
                    ("xs", w.DWORD), ("ys", w.DWORD), ("xc", w.DWORD),
                    ("yc", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                    ("show", w.WORD), ("reserved_count", w.WORD),
                    ("reserved_bytes", ctypes.POINTER(ctypes.c_byte)),
                    ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]

    class _Information(ctypes.Structure):
        _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE),
                    ("pid", w.DWORD), ("tid", w.DWORD)]

    class _StartupEx(ctypes.Structure):
        _fields_ = [("startup", _Startup), ("attributes", ctypes.c_void_p)]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", w.DWORD), ("min_ws", ctypes.c_size_t),
                    ("max_ws", ctypes.c_size_t), ("active", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("schedule", w.DWORD)]

    class _Io(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class _Limits(ctypes.Structure):
        _fields_ = [("basic", _BasicLimits), ("io", _Io),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    _win = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("CreateJobObjectW", [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        ("SetInformationJobObject", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        ("CreateProcessW", [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                            w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR,
                            ctypes.POINTER(_Startup), ctypes.POINTER(_Information)], w.BOOL),
        ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
        ("ResumeThread", [w.HANDLE], w.DWORD),
        ("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD),
        ("GetExitCodeProcess", [w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        ("TerminateJobObject", [w.HANDLE, w.UINT], w.BOOL),
        ("TerminateProcess", [w.HANDLE, w.UINT], w.BOOL),
        ("CloseHandle", [w.HANDLE], w.BOOL),
        ("InitializeProcThreadAttributeList", [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
        ("UpdateProcThreadAttribute", [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
        ("DeleteProcThreadAttributeList", [ctypes.c_void_p], None),
    ):
        function = getattr(_win, name)
        function.argtypes, function.restype = args, result

    def _checked(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())


class OwnedProcess:
    def __init__(self, command, *, env, stdout, stderr, contain=True):
        self.pid = None
        self.job = self.handle = None
        self.contain = contain
        if os.name != "nt":
            self.process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                            stdout=stdout, stderr=stderr, start_new_session=True)
            self.pid = self.process.pid
            return
        extended, info = _StartupEx(), _Information()
        startup = extended.startup
        startup.cb = ctypes.sizeof(extended)
        startup.flags, startup.show = 0x101, 0  # explicit std handles, hidden
        with open(os.devnull, "rb") as null:
            handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in (null, stdout, stderr)]
            startup.stdin, startup.stdout, startup.stderr = handles
            size = ctypes.c_size_t()
            _win.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            _checked(_win.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)))
            extended.attributes = ctypes.cast(attributes, ctypes.c_void_p)
            handle_list = (w.HANDLE * len(handles))(*handles)
            try:
                for handle in handles:
                    os.set_handle_inheritable(handle, True)
                _checked(_win.UpdateProcThreadAttribute(attributes, 0, 0x20002,
                    handle_list, ctypes.sizeof(handle_list), None, None))
                command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(x) for x in command]))
                environment = ctypes.create_unicode_buffer("\0".join(
                    f"{key}={value}" for key, value in sorted(env.items())) + "\0\0")
                _checked(_win.CreateProcessW(None, command_line, None, None, True,
                    0x08000000 | 0x00000004 | 0x00000400 | 0x00080000, environment, None,
                    ctypes.byref(startup), ctypes.byref(info)))
            finally:
                for handle in handles:
                    os.set_handle_inheritable(handle, False)
                _win.DeleteProcThreadAttributeList(attributes)
        self.handle, self.pid = info.process, info.pid
        try:
            if contain:
                self.job = _win.CreateJobObjectW(None, None)
                _checked(self.job)
                limits = _Limits()
                limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                _checked(_win.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
                _checked(_win.AssignProcessToJobObject(self.job, self.handle))
            if _win.ResumeThread(info.thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            _win.TerminateProcess(self.handle, 1)  # this exact, still-suspended child only
            self.close()
            raise
        finally:
            _win.CloseHandle(info.thread)

    def poll(self):
        if os.name != "nt":
            return self.process.poll()
        result = _win.WaitForSingleObject(self.handle, 0)
        if result == 0x102:
            return None
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = w.DWORD()
        _checked(_win.GetExitCodeProcess(self.handle, ctypes.byref(code)))
        return code.value

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def terminate_tree(self):
        if not self.contain:
            raise RuntimeError("Uncontained COM helper cannot be force terminated")
        if os.name == "nt":
            _checked(_win.TerminateJobObject(self.job, 1))
        else:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if not self.wait(5):
            raise RuntimeError("Owned converter did not exit after termination")

    def close(self):
        if os.name == "nt":
            for name in ("job", "handle"):
                handle = getattr(self, name)
                if handle:
                    _win.CloseHandle(handle)
                    setattr(self, name, None)


def run_owned(command, *, env=None, timeout=180, contain=True, cooperative_stop=None):
    """Run with bounded polling; a COM stop callback may defer unproven cleanup.

    Deferred helpers keep their resources until exit. They can never return a
    successful result to their abandoned caller, even if Office completes later.
    """
    check_read_cancelled()
    directory = tempfile.TemporaryDirectory(prefix="shchem-process-")
    stdout = open(Path(directory.name) / "stdout", "w+b")
    stderr = open(Path(directory.name) / "stderr", "w+b")
    process = None
    deferred = False

    def cleanup():
        if process is not None:
            process.close()
        stdout.close()
        stderr.close()
        directory.cleanup()

    def defer_cleanup():
        nonlocal deferred
        if deferred:
            return
        deferred = True
        # TemporaryDirectory otherwise tries to remove the helper's live log
        # files at interpreter exit. The reaper remains their only disposer.
        directory._finalizer.detach()
        retained = (process, stdout, stderr, directory)
        with _PENDING_LOCK:
            _PENDING_RESOURCES.add(retained)
        def reap():
            try:
                process.wait()
            except BaseException:
                # If even the held-handle wait fails, keep resources intact.
                # Closing a live COM helper's files is not a safe cleanup.
                return
            try:
                cleanup()
            finally:
                with _PENDING_LOCK:
                    _PENDING_RESOURCES.discard(retained)
        threading.Thread(target=reap, name="shchem-office-cleanup", daemon=True).start()

    try:
        process = OwnedProcess(command, env=dict(os.environ if env is None else env),
                               stdout=stdout, stderr=stderr, contain=contain)
        deadline = time.monotonic() + timeout
        try:
            while True:
                check_read_cancelled()
                code = process.poll()
                if code is not None:
                    break
                if time.monotonic() >= deadline:
                    raise ProcessTimeout()
                time.sleep(0.05)
            check_read_cancelled()
        except (ReadCancelled, ProcessTimeout) as error:
            if cooperative_stop is not None:
                try:
                    completed = cooperative_stop(process, error)
                except Exception:
                    completed = False
                    error.cleanup_error = "cooperative_stop_failed"
            else:
                process.terminate_tree()
                completed = True
            error.cleanup_complete = bool(completed)
            error.owned_pid = process.pid
            if not completed:
                error.message_zh = ("已停止等待，未发布本次结果；Word 尚未完成可核验的清理，"
                                    "未强制关闭可能被接管的实例。")
                defer_cleanup()
            raise
        def read(stream):
            stream.flush()
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            return stream.read().decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(list(command), code, read(stdout), read(stderr))
    except BaseException as error:
        if process is not None and not deferred:
            try:
                running = process.poll() is None
            except Exception:
                running = True
            if running:
                if contain:
                    process.terminate_tree()
                else:
                    # I/O/protocol failure is also an abandoned conversion.
                    # Request its cooperative finally but never kill COM.
                    if cooperative_stop is not None:
                        try:
                            cooperative_stop(process, error)
                        except Exception:
                            pass
                    error.cleanup_complete = False
                    error.message_zh = "本次结果已作废；Word 清理状态尚不能确认，诊断记录已保留。"
                    defer_cleanup()
        raise
    finally:
        if not deferred:
            cleanup()

"""Real isolated synthetic processes; never activate Office or touch user files."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from integrations.deeptutor_shchem_v1.owned_process import run_owned, ProcessTimeout
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, read_cancel_scope


def test_real_command_is_hidden_and_captures_output():
    result = run_owned([sys.executable, "-B", "-c", "print('synthetic owned process')"])
    assert result.returncode == 0
    assert "synthetic owned process" in result.stdout


def test_precancel_never_launches(tmp_path):
    target = tmp_path / "must-not-exist"
    event = threading.Event(); event.set()
    with pytest.raises(ReadCancelled), read_cancel_scope(event, check_boundaries=False):
        run_owned([sys.executable, "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).touch()", str(target)])
    assert not target.exists()


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_running_owned_tree_stops_and_unrelated_process_survives(tmp_path, stop):
    marker = tmp_path / "grandchild-alive"
    ready = tmp_path / "ready"
    child_code = "import sys,time;from pathlib import Path;p=Path(sys.argv[1]);\nwhile True:\n p.write_text(str(time.time()));time.sleep(.05)"
    parent_code = ("import subprocess,sys,time;from pathlib import Path;"
                   "subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]]);"
                   "Path(sys.argv[3]).touch();time.sleep(60)")
    unrelated = subprocess.Popen([sys.executable, "-B", "-c", "import time;time.sleep(60)"],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    event = threading.Event()
    def cancel():
        until = time.monotonic() + 8
        while not marker.exists() and time.monotonic() < until:
            time.sleep(.02)
        event.set()
    thread = threading.Thread(target=cancel)
    if stop == "cancel":
        thread.start()
    try:
        error_type = ReadCancelled if stop == "cancel" else ProcessTimeout
        with pytest.raises(error_type) as error, read_cancel_scope(event):
            run_owned([sys.executable, "-B", "-c", parent_code, child_code, str(marker), str(ready)],
                      timeout=10 if stop == "cancel" else 1)
        assert ready.exists() and marker.exists()
        assert error.value.cleanup_complete is True
        last = marker.read_bytes()
        time.sleep(.2)
        assert marker.read_bytes() == last
        assert unrelated.poll() is None
    finally:
        unrelated.terminate(); unrelated.wait(timeout=5)
        if thread.is_alive():
            thread.join(timeout=10)


def test_nested_shutdown_scope_cannot_mask_task_cancel():
    task, shutdown = threading.Event(), threading.Event()
    with pytest.raises(ReadCancelled), read_cancel_scope(task):
        with read_cancel_scope(shutdown):
            task.set()
            run_owned([sys.executable, "-c", "raise SystemExit(9)"])


@pytest.mark.skipif(os.name != "nt", reason="Windows handle inheritance contract")
def test_explicit_handle_list_does_not_inherit_unrelated_handle_during_parallel_launches(tmp_path):
    import ctypes
    import msvcrt
    from ctypes import wintypes
    barrier = threading.Barrier(5)
    check = ("import ctypes,sys;from ctypes import wintypes;"
             "k=ctypes.WinDLL('kernel32',use_last_error=True);"
             "k.GetFileType.argtypes=[wintypes.HANDLE];k.GetFileType.restype=wintypes.DWORD;"
             "assert k.GetFileType(int(sys.argv[1]))==0;print(sys.argv[2])")
    # This deliberately inheritable file is unrelated to all five tasks.
    with (tmp_path / "unrelated-handle").open("wb") as stream:
        handle = msvcrt.get_osfhandle(stream.fileno())
        os.set_handle_inheritable(handle, True)
        def task(index):
            barrier.wait(timeout=5)
            return run_owned([sys.executable, "-B", "-c", check, str(handle), str(index)])
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(task, range(5)))
            assert [r.returncode for r in results] == [0]*5
            assert [r.stdout.strip() for r in results] == list(map(str, range(5)))
        finally:
            os.set_handle_inheritable(handle, False)


def test_uncontained_helper_is_preserved_until_its_cooperative_exit(tmp_path):
    ready, done, stop = (tmp_path / name for name in ("ready", "done", "stop"))
    event = threading.Event()
    code = ("import sys,time;from pathlib import Path;"
            "ready,done,stop=map(Path,sys.argv[1:]);ready.touch();"
            "\nwhile not stop.exists(): time.sleep(.01)"
            "\ntime.sleep(.3);done.touch()")
    def cancel():
        until = time.monotonic()+5
        while not ready.exists() and time.monotonic()<until:time.sleep(.01)
        event.set()
    thread = threading.Thread(target=cancel);thread.start()
    def cooperative(process,error):
        stop.touch()
        return False  # Cleanup cannot yet be proved; do not kill this helper.
    try:
        with pytest.raises(ReadCancelled) as error, read_cancel_scope(event):
            run_owned([sys.executable,"-B","-c",code,str(ready),str(done),str(stop)],
                      contain=False,cooperative_stop=cooperative)
        assert error.value.cleanup_complete is False
        assert "尚未完成可核验" in error.value.message_zh
        until=time.monotonic()+5
        while not done.exists() and time.monotonic()<until:time.sleep(.02)
        assert done.exists(), "helper must finish its finally path instead of being killed"
    finally:
        stop.touch();thread.join(timeout=6)


def test_final_publication_linearizes_cancel_and_does_not_mask_failed_commit():
    from integrations.deeptutor_shchem_v1.reader_cancellation import TaskCancellationEvent, publication_guard, check_read_cancelled
    event=TaskCancellationEvent();shutdown=threading.Event()
    with read_cancel_scope(event),read_cancel_scope(shutdown):
        with publication_guard():
            shutdown.set()  # Publication has already won, even on window close.
        assert event.request_cancel() is False
        check_read_cancelled()
    event=TaskCancellationEvent()
    with read_cancel_scope(event,check_boundaries=False):
        with pytest.raises(ValueError),publication_guard():
            raise ValueError("synthetic failed write")
        assert event.request_cancel() is True
        with pytest.raises(ReadCancelled),publication_guard():
            pytest.fail("cancelled task published")


@pytest.mark.parametrize("failure",["stop_callback","poll"])
def test_uncontained_nonstandard_failures_do_not_close_live_helper_resources(tmp_path,monkeypatch,failure):
    from integrations.deeptutor_shchem_v1 import owned_process
    done=tmp_path/"late-helper-finished";event=threading.Event()
    code="import sys,time;from pathlib import Path;time.sleep(.3);print('late finally');Path(sys.argv[1]).touch()"
    if failure=="poll":
        original=owned_process.OwnedProcess.poll
        count=[0]
        def broken_once(self):
            if threading.current_thread() is threading.main_thread():
                count[0]+=1
                if count[0]==1:raise RuntimeError("synthetic wait failure")
            return original(self)
        monkeypatch.setattr(owned_process.OwnedProcess,"poll",broken_once)
    else:
        timer=threading.Timer(.05,event.set);timer.start()
    def bad_stop(*args):raise OSError("synthetic diagnostic write failure")
    with pytest.raises((ReadCancelled,RuntimeError)) as error,read_cancel_scope(event,check_boundaries=False):
        run_owned([sys.executable,"-B","-c",code,str(done)],contain=False,cooperative_stop=bad_stop)
    assert error.value.cleanup_complete is False
    until=time.monotonic()+5
    while not done.exists() and time.monotonic()<until:time.sleep(.02)
    assert done.exists()
    while owned_process._PENDING_RESOURCES and time.monotonic()<until:time.sleep(.02)
    assert not owned_process._PENDING_RESOURCES
    if failure=="stop_callback":timer.join(timeout=1)

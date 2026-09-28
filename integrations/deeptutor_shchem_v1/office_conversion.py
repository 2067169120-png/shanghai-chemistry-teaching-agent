"""Private Office conversions. Never attach cleanup to a teacher's instance."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from uuid import uuid4

from .owned_process import ProcessTimeout, run_owned
from .reader_cancellation import ReadCancelled, check_read_cancelled


class OfficeConversionError(RuntimeError):
    code = "office_conversion_failed"

    def __init__(self, message="本次文档转换未完成，未发布新的结果。", code=None):
        self.message_zh = message
        if code:
            self.code = code
        super().__init__(message)


_WORD_SCRIPT = r'''
$ErrorActionPreference='Stop'
$word=$null; $doc=$null; $owned=$false; $pidVerified=$false; $wordPid=[uint32]0
$ownedHandle=[IntPtr]::Zero; $sourceStream=$null
$phase='starting'
$cleanupStep='not_started'; $cleanupSteps=@(); $cleanupException=$null
$directory=[Environment]::GetEnvironmentVariable('SHCHEM_OFFICE_SESSION','Process')
$utf8=New-Object System.Text.UTF8Encoding($false)
$inputPath=[Environment]::GetEnvironmentVariable('SHCHEM_WORD_INPUT','Process')
$outputPath=[Environment]::GetEnvironmentVariable('SHCHEM_WORD_OUTPUT','Process')
$identity=[Environment]::GetEnvironmentVariable('SHCHEM_OFFICE_ID','Process')
$expectedSha=[Environment]::GetEnvironmentVariable('SHCHEM_WORD_SHA','Process')
$originalPath=[Environment]::GetEnvironmentVariable('SHCHEM_WORD_ORIGINAL','Process')
$targetPath=[Environment]::GetEnvironmentVariable('SHCHEM_WORD_TARGET','Process')
function Record($name,$value) {
  $value['session_id']=$identity
  $value['input_path']=$inputPath; $value['output_path']=$outputPath; $value['input_sha256']=$expectedSha
  $value['source_path']=$originalPath; $value['target_path']=$targetPath
  $temporary=Join-Path $directory ($name+'.tmp')
  [IO.File]::WriteAllText($temporary,($value|ConvertTo-Json -Depth 5 -Compress),$utf8)
  Move-Item -LiteralPath $temporary -Destination (Join-Path $directory ($name+'.json')) -Force
}
function Stage($value) {
  $script:phase=$value
  Record 'stage' @{stage=$value;pid=[int]$wordPid;pid_verified=$pidVerified}
}
function Cleanup-Stage($value) {
  $script:cleanupStep=$value
  $script:cleanupSteps+=@{step=$value;utc=[DateTime]::UtcNow.ToString('o')}
  try { Record 'cleanup-progress' @{steps=$script:cleanupSteps;pid=[int]$wordPid;pid_verified=$pidVerified} } catch {}
}
function Cleanup-Failure($failure) {
  $cause=$failure.Exception
  while ($null -ne $cause.InnerException) { $cause=$cause.InnerException }
  $script:cleanupException=@{step=$script:cleanupStep;hresult=$cause.HResult;error_type=$cause.GetType().FullName}
  try { Record 'cleanup-exception' $script:cleanupException } catch {}
}
function Check-Cancel {
  if (Test-Path -LiteralPath (Join-Path $directory 'cancel')) { throw 'cancelled' }
}
function Source-Hash {
  $sourceStream.Position=0
  $sha=[Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($sha.ComputeHash($sourceStream))).Replace('-','').ToLowerInvariant() }
  finally { $sha.Dispose(); $sourceStream.Position=0 }
}
function Private-Word($empty=$false) {
  try {
    if (-not $owned -or $word.Visible -or $word.UserControl) { return $false }
    if ($pidVerified) {
      if ($ownedHandle -eq [IntPtr]::Zero -or [ShChemConversionOwner]::WaitForSingleObject($ownedHandle,0) -ne 258) { return $false }
      if ([ShChemConversionOwner]::Creation($ownedHandle) -ne $startFileTime) { return $false }
    }
    if ($empty -or $null -eq $doc) { return ($word.Documents.Count -eq 0) }
    if (-not $pidVerified -or $word.Documents.Count -ne 1 -or -not $doc.ReadOnly -or $doc.FullName -ine $inputPath) { return $false }
    $pidNow=[uint32]0
    $thread=[ShChemConversionOwner]::GetWindowThreadProcessId([IntPtr]$doc.Windows.Item(1).Hwnd,[ref]$pidNow)
    return ($thread -ne 0 -and $pidNow -eq $wordPid)
  } catch { return $false }
}
try {
  Stage 'checking_input'
  Check-Cancel
  $sourceStream=[IO.File]::Open($inputPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
  if ((Source-Hash) -cne $expectedSha) { throw 'input_changed' }
  $prior=@(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  Record 'baseline' @{pids=$prior;pid_verified=$false}
  Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ShChemConversionOwner { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid); [DllImport("kernel32.dll")] public static extern IntPtr OpenProcess(uint rights, bool inherit, uint pid); [DllImport("kernel32.dll")] public static extern uint WaitForSingleObject(IntPtr handle,uint wait); [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle); [DllImport("kernel32.dll")] static extern bool GetProcessTimes(IntPtr handle,out long creation,out long exit,out long kernel,out long user); public static long Creation(IntPtr h) { long c,e,k,u; return GetProcessTimes(h,out c,out e,out k,out u) ? c : 0; } }'
  Check-Cancel
  $launched=[DateTime]::UtcNow
  Stage 'creating_singleuse_com'
  $word=New-Object -ComObject Word.Application
  # Word's Application does not expose Hwnd. SingleUse creation plus this
  # empty/private COM check is conditional identity, not yet a verified PID.
  Stage 'checking_empty_automation'
  if ($word.Visible -or $word.UserControl -or $word.Documents.Count -ne 0) { throw 'ownership_unproven' }
  $owned=$true
  Record 'automation' @{identity='singleuse_empty_com';pid_verified_before_open=$false}
  Check-Cancel
  if (-not (Private-Word $true)) { throw 'session_taken_over' }
  $word.AutomationSecurity=3
  $word.DisplayAlerts=0
  if (-not (Private-Word $true)) { throw 'session_taken_over' }
  Check-Cancel
  $filename=[object]$inputPath; $no=[object]$false; $yes=[object]$true
  Stage 'opening_private_readonly_copy'
  $doc=$word.Documents.OpenNoRepairDialog([ref]$filename,[ref]$no,[ref]$yes,[ref]$no)
  Stage 'checking_document_pid'
  $thread=[ShChemConversionOwner]::GetWindowThreadProcessId([IntPtr]$doc.Windows.Item(1).Hwnd,[ref]$wordPid)
  if ($thread -eq 0 -or $wordPid -eq 0 -or $prior -contains [int]$wordPid) { throw 'ownership_unproven' }
  $process=Get-Process -Id $wordPid -ErrorAction Stop
  $startFileTime=$process.StartTime.ToUniversalTime().ToFileTimeUtc()
  $ownedHandle=[ShChemConversionOwner]::OpenProcess(0x00101000,$false,$wordPid)
  if ($ownedHandle -eq [IntPtr]::Zero -or [ShChemConversionOwner]::Creation($ownedHandle) -ne $startFileTime) { throw 'ownership_unproven' }
  if ($process.StartTime.ToUniversalTime() -lt $launched) { throw 'ownership_unproven' }
  $pidVerified=$true
  Record 'owner' @{pid=[int]$wordPid;start_filetime=$startFileTime;private=$true;held_handle=$true}
  if (-not (Private-Word)) { throw 'session_taken_over' }
  Check-Cancel
  Stage 'converting'
  $doc.ExportAsFixedFormat($outputPath,17)
  Check-Cancel
  if (-not (Private-Word)) { throw 'session_taken_over' }
  if ((Source-Hash) -cne $expectedSha) { throw 'input_changed' }
  Record 'result' @{status='converted'}
} catch {
  $reason=$_.Exception.Message
  if ($reason -notin @('cancelled','ownership_unproven','session_taken_over','input_changed')) { $reason='conversion_failed' }
  $cause=$_.Exception
  while ($null -ne $cause.InnerException) { $cause=$cause.InnerException }
  Record 'error' @{code=$reason;stage=$phase;hresult=$cause.HResult;error_type=$cause.GetType().FullName;pid=[int]$wordPid;pid_verified=$pidVerified}
} finally {
  # BEGIN_PRIVATE_CLEANUP: exercised without Office by the PowerShell protocol test.
  $disposition='ownership_unproven_preserved'
  Cleanup-Stage 'checking_private_state'
  if ($owned) {
    $disposition='taken_over_or_unverified_preserved'
    if (Private-Word) {
      try {
        $saveChanges=[object]0
        if ($null -ne $doc) {
          Cleanup-Stage 'closing_document'
          $doc.Close([ref]$saveChanges)
          Cleanup-Stage 'document_close_returned'
        }
        Cleanup-Stage 'checking_empty_private_state'
        if (Private-Word $true) {
          Cleanup-Stage 'quitting_application'
          $word.Quit([ref]$saveChanges)
          Cleanup-Stage 'application_quit_returned'
          $disposition=if ($pidVerified) {'quit_requested'} else {'empty_automation_quit_unverified'}
        }
      } catch { Cleanup-Failure $_; $disposition='cleanup_unverified_preserved' }
    }
  }
  Cleanup-Stage 'releasing_com_references'
  foreach ($reference in @($doc,$word)) {
    if ($null -ne $reference -and [Runtime.InteropServices.Marshal]::IsComObject($reference)) {
      try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($reference) } catch {}
    }
  }
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
  if ($disposition -eq 'quit_requested') {
    Cleanup-Stage 'waiting_held_process_exit'
    $until=[DateTime]::UtcNow.AddSeconds(2)
    do {
      if ([ShChemConversionOwner]::WaitForSingleObject($ownedHandle,0) -eq 0) {
        $disposition='owned_instance_exited'; break
      }
      Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $until)
  }
  Cleanup-Stage 'cleanup_finished'
  try { Record 'cleanup' @{disposition=$disposition;pid=[int]$wordPid;steps=$cleanupSteps;exception=$cleanupException} }
  finally {
    if ($ownedHandle -ne [IntPtr]::Zero) { [void][ShChemConversionOwner]::CloseHandle($ownedHandle) }
    if ($null -ne $sourceStream) { $sourceStream.Dispose() }
  }
  # END_PRIVATE_CLEANUP
}
'''


def _record(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _observe_bound_process_exit(pid, start_filetime, *, timeout=30.0):
    """Observe one previously bound process identity; no terminate rights/calls."""
    import ctypes
    from ctypes import wintypes as w
    started = time.monotonic()
    result = {"pid": pid, "start_filetime": start_filetime, "verified": False,
              "wait_budget_seconds": timeout}
    def finished(**fields):
        return {**result, **fields, "elapsed_seconds": time.monotonic()-started}
    if os.name != "nt":
        return finished(method="unsupported_platform")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetProcessTimes.argtypes = [w.HANDLE, *[ctypes.POINTER(ctypes.c_uint64)]*4]
    kernel.GetProcessTimes.restype = w.BOOL
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x00101000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        return finished(verified=error == 87, method="pid_absent" if error == 87 else "open_unverified", native_error=error)
    try:
        times = [ctypes.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *map(ctypes.byref, times)):
            return finished(method="creation_unverified", native_error=ctypes.get_last_error())
        result["observed_start_filetime"] = times[0].value
        if times[0].value != start_filetime:
            return finished(verified=True, method="pid_reused")
        deadline = time.monotonic() + timeout
        while True:
            check_read_cancelled()
            status = kernel.WaitForSingleObject(handle, 50)
            if status == 0:
                return finished(verified=True, method="held_handle_exit")
            if status != 258:
                return finished(method="wait_unverified", native_error=ctypes.get_last_error())
            if time.monotonic() >= deadline:
                return finished(method="still_running")
    finally:
        kernel.CloseHandle(handle)


def word_pdf(source, target, *, timeout=180, exit_timeout=30.0, session_root=None):
    check_read_cancelled()
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists():
        raise OfficeConversionError("转换目标已存在，请使用新的分页目录；已有文件保留。")
    # Do not change persistent Word Options to suppress external-link updates.
    # Reject external OOXML relationships before asking Word to open a copy.
    from zipfile import ZipFile
    from xml.etree import ElementTree
    with ZipFile(source) as archive:
        types = archive.read("[Content_Types].xml").lower()
        if b"macroenabled" in types or any("vbaproject" in name.lower() for name in archive.namelist()):
            raise OfficeConversionError("文档包含宏内容，未启动 Word；请先提供无宏 DOCX。")
        for name in archive.namelist():
            if name.endswith(".rels"):
                if any(row.get("TargetMode", "").lower() == "external"
                       for row in ElementTree.fromstring(archive.read(name))):
                    raise OfficeConversionError("文档包含外部链接，未启动 Word；请先核对本地转换副本。")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    identity = uuid4().hex
    # The caller may delete its own temporary renderer directory immediately
    # after cancellation. Keep the COM stack, immutable copy and late output in
    # a separate retained session; it never writes into that caller's directory.
    sessions = Path(session_root) if session_root is not None else Path(tempfile.gettempdir()) / "shchem-office-sessions"
    session = sessions.resolve() / identity
    session.mkdir(parents=True, exist_ok=False)
    immutable_input, pending_output = session / "input.docx", session / "converted.pdf"
    shutil.copyfile(source, immutable_input)
    if hashlib.sha256(immutable_input.read_bytes()).hexdigest() != source_sha:
        raise OfficeConversionError("转换输入发生变化，未启动 Word。")
    env = {**os.environ, "SHCHEM_OFFICE_SESSION": str(session),
           "SHCHEM_OFFICE_ID": identity, "SHCHEM_WORD_INPUT": str(immutable_input),
           "SHCHEM_WORD_OUTPUT": str(pending_output), "SHCHEM_WORD_SHA": source_sha,
           "SHCHEM_WORD_ORIGINAL": str(source), "SHCHEM_WORD_TARGET": str(target)}
    expected = {"session_id": identity, "input_path": str(immutable_input),
                "output_path": str(pending_output), "input_sha256": source_sha,
                "source_path": str(source), "target_path": str(target)}
    (session / "request.json").write_text(json.dumps({**expected,
        "script_sha256": hashlib.sha256(_WORD_SCRIPT.encode("utf-8")).hexdigest(),
        "pid_verified_before_open": False}, ensure_ascii=False), encoding="utf-8")

    def record(name):
        value = _record(session / (name + ".json"))
        return value if all(value.get(key) == item for key, item in expected.items()) else {}

    def valid_owner():
        owner, automation = record("owner"), record("automation")
        positive = lambda value: type(value) is int and value > 0
        return (owner.get("held_handle") is True and owner.get("private") is True
                and positive(owner.get("pid")) and positive(owner.get("start_filetime"))
                and automation.get("identity") == "singleuse_empty_com"
                and automation.get("pid_verified_before_open") is False)

    def private_exited():
        cleanup = record("cleanup")
        owner = record("owner")
        if not (valid_owner() and type(cleanup.get("pid")) is int and cleanup["pid"] == owner["pid"]):
            return False
        if cleanup.get("disposition") == "owned_instance_exited":
            return True
        parent = record("parent_exit_verified")
        return (cleanup.get("disposition") == "quit_requested" and cleanup.get("exception") is None
                and parent.get("verified") is True and parent.get("pid") == owner["pid"]
                and parent.get("start_filetime") == owner["start_filetime"]
                and parent.get("method") in {"pid_absent", "pid_reused", "held_handle_exit"})

    def diagnose(error, *, exited=None):
        cleanup = record("cleanup")
        complete = private_exited()
        error.office_session = str(session)
        error.cleanup_complete = bool(getattr(error, "cleanup_complete", complete))
        if isinstance(error, ReadCancelled) and not error.cleanup_complete and record("automation"):
            error.message_zh = "已停止等待，未发布本次结果；Word 清理尚未核实，未强制关闭实例。"
        payload = {**expected, "status": "cancelled" if isinstance(error, ReadCancelled) else
                   "timed_out" if isinstance(error, ProcessTimeout) else "failed",
                   "result_discarded": True, "cleanup_complete": error.cleanup_complete,
                   "cleanup": cleanup.get("disposition", "pending_unverified_cleanup"),
                   "stage": "waiting_parent_exit" if record("parent_exit_started") else
                            record("error").get("stage", record("stage").get("stage", "before_helper")),
                   "error_code": getattr(error, "code", "office_conversion_failed")}
        if exited is not None:
            payload["helper_exited"] = exited
        elif "helper_exited" in record("caller"):
            payload["helper_exited"] = record("caller")["helper_exited"]
        try:
            (session / "caller.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            # A failed diagnostic write must never replace the original stop
            # or make another backend retry the abandoned conversion.
            error.diagnostic_write_failed = True
        return error.cleanup_complete

    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"

    def stop(process, error):
        (session / "cancel").touch()
        # Never terminate a helper while COM owns its stack: finally is the
        # only place that can freshly prove this instance has not been adopted.
        exited = process.wait(1.5)
        error.cleanup_complete = exited and private_exited()
        return diagnose(error, exited=exited)

    created_output_sha = None
    try:
        completed = run_owned([str(executable), "-STA", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                               base64.b64encode(_WORD_SCRIPT.encode("utf-16le")).decode("ascii")],
                              env=env, timeout=timeout, contain=False, cooperative_stop=stop)
        check_read_cancelled()
        error = record("error")
        if error:
            if error.get("code") == "cancelled":
                raise ReadCancelled()
            raise OfficeConversionError(
                "Word 实例归属或文档状态未通过核对，已保留其他文档；本次结果不可用。"
                if error.get("code") in {"ownership_unproven", "session_taken_over"} else
                "Word 未完成本次转换，请核对文档后重试。", error.get("code"))
        cleanup = record("cleanup")
        if (completed.returncode or record("result").get("status") != "converted"
                or not valid_owner()
                or hashlib.sha256(source.read_bytes()).hexdigest() != source_sha):
            raise OfficeConversionError()
        if (cleanup.get("disposition") == "quit_requested" and cleanup.get("exception") is None
                and type(cleanup.get("pid")) is int and cleanup["pid"] == record("owner")["pid"]):
            owner = record("owner")
            (session / "parent_exit_started.json").write_text(json.dumps({**expected,
                "pid": owner["pid"], "start_filetime": owner["start_filetime"],
                "wait_budget_seconds": exit_timeout}, ensure_ascii=False), encoding="utf-8")
            observed = _observe_bound_process_exit(owner["pid"], owner["start_filetime"], timeout=exit_timeout)
            (session / "parent_exit_verified.json").write_text(
                json.dumps({**expected, **observed}, ensure_ascii=False), encoding="utf-8")
        if not private_exited():
            raise OfficeConversionError("Word 转换后的实例状态无法确认，未发布本次结果；请核对保留的 Word 窗口。")
        if not pending_output.is_file() or not pending_output.read_bytes().startswith(b"%PDF-"):
            raise OfficeConversionError("Word 未生成有效 PDF。")
        check_read_cancelled()
        pdf_bytes = pending_output.read_bytes()
        with target.open("xb") as output:
            output.write(pdf_bytes)
        created_output_sha = hashlib.sha256(pdf_bytes).hexdigest()
        check_read_cancelled()
    except BaseException as exc:
        diagnose(exc)
        if created_output_sha is not None:
            try:
                if hashlib.sha256(target.read_bytes()).hexdigest() == created_output_sha:
                    target.unlink()
            except OSError:
                pass
        raise
    return "Word 私有实例只读转换，已核对该实例退出。"


def libreoffice_pdf(source, output, office, *, export_filter="writer_pdf_Export", timeout=180):
    check_read_cancelled()
    source, output = Path(source).resolve(), Path(output).resolve()
    pdf = output / (source.stem + ".pdf")
    if pdf.exists():
        raise OfficeConversionError("转换目标已存在，请使用新的分页目录；已有文件保留。")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="shchem-office-") as profile:
        result = run_owned([str(office), "-env:UserInstallation=" + Path(profile).as_uri(),
                            "--headless", "--norestore", "--convert-to", "pdf:" + export_filter,
                            "--outdir", str(output), str(source)], timeout=timeout)
    check_read_cancelled()
    if (result.returncode or hashlib.sha256(source.read_bytes()).hexdigest() != source_sha
            or not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-")):
        raise OfficeConversionError("LibreOffice 未完成本次文档转换，未发布新的结果。")
    return pdf

"""Verify an original DOCX against native Word ranges before selecting it.

The disposable, owned Word process stays hidden until a verified selection is
committed. The original is held read-only, never copied, edited or saved.
Only explicitly requested UI operations call this module; importing it does
not inspect or start Office. Private XML lives in a temporary local directory.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .desktop_preparation_sources import PreparationSourceError


MAX_XML_BYTES = 96 * 1024 * 1024
MAX_NATIVE_OBJECTS = 500
_SESSION_LOCK = threading.Lock()

# Literal script: paths and commands are data files/environment values, never
# interpolated into PowerShell. No OLE Object/Activate/Open/DoVerb, fields update,
# Save, InsertXML, bookmark insertion or application-wide process termination.
_SCRIPT = r'''
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$dir=[Environment]::GetEnvironmentVariable('SHCHEM_NATIVE_DIRECTORY','Process')
$utf8=New-Object System.Text.UTF8Encoding($false)
$word=$null; $doc=$null; $sourceStream=$null; $created=$false; $owned=$false; $handedOff=$false
$security=$null; $links=$null; $alerts=$null
$zero=[object]0
function Write-Json($name,$value) {
  [IO.File]::WriteAllText((Join-Path $dir ($name+'.tmp')),($value | ConvertTo-Json -Depth 12 -Compress),$utf8)
  [IO.File]::Move((Join-Path $dir ($name+'.tmp')),(Join-Path $dir ($name+'.json')))
}
function Check-Cancel {
  if (Test-Path -LiteralPath (Join-Path $dir 'cancel')) { throw 'cancelled' }
}
function Wait-Command($name) {
  $deadline=[DateTime]::UtcNow.AddSeconds(45)
  while (-not (Test-Path -LiteralPath (Join-Path $dir ($name+'.json')))) {
    Check-Cancel
    if ([DateTime]::UtcNow -gt $deadline) { throw 'command_timeout' }
    Start-Sleep -Milliseconds 50
  }
  Check-Cancel
  return ([IO.File]::ReadAllText((Join-Path $dir ($name+'.json')),$utf8) | ConvertFrom-Json)
}
function Source-Hash {
  $sourceStream.Position=0
  $sha=[Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($sha.ComputeHash($sourceStream))).Replace('-','').ToLowerInvariant() }
  finally { $sha.Dispose(); $sourceStream.Position=0 }
}
function Write-Xml($name,$xml) {
  if ($utf8.GetByteCount($xml) -gt 100663296) { throw 'xml_budget' }
  [IO.File]::WriteAllText((Join-Path $dir ($name+'.xml')),$xml,$utf8)
}
function Restore-Options {
  if ($null -ne $security) { $word.AutomationSecurity=$security }
  if ($null -ne $links) { $word.Options.UpdateLinksAtOpen=$links }
  if ($null -ne $alerts) { $word.DisplayAlerts=$alerts }
}
function Test-Private-Session($empty=$false) {
  try {
    if (-not $owned -or $handedOff -or $word.Visible -or $word.UserControl) { return $false }
    if ($empty -or $null -eq $doc) { return ($word.Documents.Count -eq 0) }
    if ($word.Documents.Count -ne 1 -or -not $doc.ReadOnly -or $doc.FullName -ine $request.path) { return $false }
    $currentPid=[uint32]0
    $currentThread=[ShChemWordOwner]::GetWindowThreadProcessId([IntPtr]$doc.Windows.Item(1).Hwnd,[ref]$currentPid)
    return ($currentThread -ne 0 -and $currentPid -eq $wordPid)
  } catch { return $false }
}
try {
  $request=[IO.File]::ReadAllText((Join-Path $dir 'request.json'),$utf8) | ConvertFrom-Json
  Check-Cancel
  $sourceStream=[IO.File]::Open($request.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
  if ((Source-Hash) -cne $request.sha256) { throw 'source_changed' }
  $priorIds=@(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  if ($priorIds.Count -ne 0) { throw 'word_already_running' }
  Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ShChemWordOwner { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint process); }'
  $launchStarted=[DateTime]::UtcNow
  $word=New-Object -ComObject Word.Application
  $created=$true
  $newIds=@(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  if ($newIds.Count -ne 1 -or $word.Documents.Count -ne 0 -or $word.Visible -or $word.UserControl) { throw 'unowned_word' }
  $wordPid=[uint32]$newIds[0]
  $process=Get-Process -Id $wordPid
  if ($process.StartTime.ToUniversalTime() -lt $launchStarted) { throw 'unowned_word' }
  $owned=$true
  Write-Json 'owner' @{ pid=[int]$wordPid; start_time=$process.StartTime.ToUniversalTime().ToString('o') }
  $security=$word.AutomationSecurity; $links=$word.Options.UpdateLinksAtOpen; $alerts=$word.DisplayAlerts
  $word.AutomationSecurity=3; $word.Options.UpdateLinksAtOpen=$false; $word.DisplayAlerts=0
  Check-Cancel
  $filename=[object]$request.path; $no=[object]$false; $yes=[object]$true
  # Explicit read-only and no recent-file addition. Empty, newly owned instance:
  # there is no existing document to revert. Avoid the Office/PS Missing binder.
  $doc=$word.Documents.OpenNoRepairDialog([ref]$filename,[ref]$no,[ref]$yes,[ref]$no)
  if (-not $doc.ReadOnly -or $doc.FullName -ine $request.path) { throw 'wrong_document' }
  $verifiedPid=[uint32]0
  $threadId=[ShChemWordOwner]::GetWindowThreadProcessId([IntPtr]$doc.Windows.Item(1).Hwnd,[ref]$verifiedPid)
  if ($threadId -eq 0 -or $verifiedPid -ne $wordPid) { throw 'wrong_word_process' }
  if (-not (Test-Private-Session)) { throw 'session_taken_over' }
  Check-Cancel
  $snapshot=$doc.WordOpenXML
  Write-Xml 'document' $snapshot
  $records=@()
  foreach ($kind in @('inline','math')) {
    if ($kind -eq 'inline') { $collection=$doc.InlineShapes } else { $collection=$doc.OMaths }
    if ($collection.Count -gt 500) { throw 'object_budget' }
    for ($i=1; $i -le $collection.Count; $i++) {
      Check-Cancel
      $item=$collection.Item($i); $range=$item.Range
      $records+=@{ kind=$kind; index=$i; type=[int]$item.Type; story=[int]$range.StoryType; start=[int]$range.Start; end=[int]$range.End }
      [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($range)
      [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($item)
    }
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($collection)
  }
  Write-Json 'opened' @{ version=$word.Version; build=$word.Build; read_only=$doc.ReadOnly; content_start=[int]$doc.Content.Start; content_end=[int]$doc.Content.End; records=$records }
  $command=Wait-Command 'inspect'
  if (-not (Test-Private-Session)) { throw 'session_taken_over' }
  if ($command.kind -eq 'inline') { $collection=$doc.InlineShapes }
  elseif ($command.kind -eq 'math') { $collection=$doc.OMaths }
  else { throw 'bad_kind' }
  if ($command.index -lt 1 -or $command.index -gt $collection.Count) { throw 'bad_index' }
  $item=$collection.Item([int]$command.index); $range=$item.Range
  $rangeXml=$range.WordOpenXML
  $start=[object][int]$range.Start; $end=[object][int]$range.End; $origin=[object][int]$doc.Content.Start
  Write-Xml 'range' $rangeXml
  $prefix=$doc.Range([ref]$origin,[ref]$start); Write-Xml 'before' $prefix.WordOpenXML
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($prefix)
  $prefix=$doc.Range([ref]$origin,[ref]$end); Write-Xml 'through' $prefix.WordOpenXML
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($prefix)
  Write-Json 'inspected' @{ kind=$command.kind; index=[int]$command.index; type=[int]$item.Type; story=[int]$range.StoryType; start=[int]$start; end=[int]$end }
  $commit=Wait-Command 'select'
  if (-not (Test-Private-Session)) { throw 'session_taken_over' }
  if ($commit.action -cne 'select' -or $doc.FullName -ine $request.path -or -not $doc.ReadOnly -or $word.Documents.Count -ne 1) { throw 'wrong_commit' }
  if ((Source-Hash) -cne $request.sha256) { throw 'document_changed' }
  if ($range.Start -ne $start -or $range.End -ne $end -or $range.StoryType -ne 1) { throw 'range_changed' }
  Check-Cancel
  $range.Select()
  $selection=$word.Selection.Range
  if ($selection.StoryType -ne 1 -or $selection.Start -ne $start -or $selection.End -ne $end) { throw 'selection_mismatch' }
  # Word may update rsids/display metadata while serializing. The caller must
  # verify these fresh snapshots semantically, not compare raw XML strings.
  Write-Xml 'selected-range' $selection.WordOpenXML
  Write-Xml 'selected-document' $doc.WordOpenXML
  $prefix=$doc.Range([ref]$origin,[ref]$start); Write-Xml 'selected-before' $prefix.WordOpenXML
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($prefix)
  $prefix=$doc.Range([ref]$origin,[ref]$end); Write-Xml 'selected-through' $prefix.WordOpenXML
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($prefix)
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($selection)
  Check-Cancel
  Write-Json 'selected' @{ status='selected'; selection_verified=$true; source_unchanged=((Source-Hash) -ceq $request.sha256); read_only=$doc.ReadOnly; story=1; start=[int]$start; end=[int]$end; visible=$false; version=$word.Version; build=$word.Build }
  if ($request.visible) {
    # Python validates the hidden selection before authorizing window handoff.
    $publish=Wait-Command 'publish'
    if ($publish.action -cne 'publish' -or -not (Test-Private-Session)) { throw 'wrong_publish' }
    if ((Source-Hash) -cne $request.sha256 -or $range.Start -ne $start -or $range.End -ne $end -or $word.Selection.Start -ne $start -or $word.Selection.End -ne $end) { throw 'document_changed' }
    Restore-Options
    Check-Cancel
    if (-not (Test-Private-Session)) { throw 'session_taken_over' }
    Check-Cancel
    # Publication is the handoff boundary. Later cancellation cannot close a
    # visible user session; no process is ever forcibly terminated.
    Write-Json 'publishing' @{ status='publishing' }
    $word.Visible=$true
    $handedOff=$true
    Write-Json 'published' @{ status='published'; visible=$true }
  }
} catch {
  if (-not (Test-Path -LiteralPath (Join-Path $dir 'error.json'))) {
    Write-Json 'error' @{ status='failed'; detail=$_.Exception.Message; handed_off=$handedOff }
  }
} finally {
  $disposition='not_owned'
  if ($handedOff) { $disposition='handed_off' }
  elseif ($null -ne $word -and $owned) {
    $disposition='preserved_after_takeover_or_unverified_cleanup'
    if (Test-Private-Session) {
      try {
        if ($null -ne $doc) { $doc.Close([ref]$zero) }
        if (Test-Private-Session $true) {
          Restore-Options
          if (Test-Private-Session $true) { $word.Quit([ref]$zero); $disposition='quit_requested' }
        }
      } catch {}
    }
  } elseif ($created -and $null -ne $word -and $null -eq $doc) {
    # A concurrent separate Word launch may defeat PID enumeration. The exact
    # newly created COM reference can still be closed if it remains empty and
    # hidden; never choose a process from the ambiguous global process list.
    $disposition='unverified_instance_preserved'
    try {
      if (-not $word.Visible -and -not $word.UserControl -and $word.Documents.Count -eq 0) {
        $word.Quit([ref]$zero); $disposition='empty_created_instance_quit_requested'
      }
    } catch {}
  }
  foreach ($com in @($range,$item,$collection,$doc,$word)) {
    if ($null -ne $com -and [Runtime.InteropServices.Marshal]::IsComObject($com)) {
      try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($com) } catch {}
    }
  }
  if ($null -ne $sourceStream) { $sourceStream.Dispose() }
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
  try { Write-Json 'cleanup' @{ disposition=$disposition } } catch {}
  # A timed-out caller may have exited. The helper then owns removal of its
  # private XML, after all COM and source handles are released. Verify the
  # exact temporary parent/name and reject reparse points before recursion.
  if (Test-Path -LiteralPath (Join-Path $dir 'abandoned')) {
    try {
      $absolute=[IO.Path]::GetFullPath($dir).TrimEnd([char]'\')
      $temporaryRoot=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([char]'\')
      $info=Get-Item -LiteralPath $absolute -Force
      if ($info.Parent.FullName -ieq $temporaryRoot -and $info.Name.StartsWith('shchem-word-location-') -and -not ($info.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Remove-Item -LiteralPath $absolute -Recurse -Force
      }
    } catch {}
  }
}
'''


class _WordSession:
    """Bounded file protocol; cancellation is checked even while Office runs."""

    def __init__(self, directory, request, cancelled):
        self.directory = Path(directory)
        self.cancelled = cancelled
        self.send("request", request)
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        self.process = subprocess.Popen(
            [str(executable), "-STA", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(_SCRIPT.encode("utf-16le")).decode("ascii")],
            env={**os.environ, "SHCHEM_NATIVE_DIRECTORY": str(self.directory)},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def send(self, name, data):
        temporary = self.directory / (name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.directory / (name + ".json"))

    def wait(self, name, timeout=45):
        deadline = time.monotonic() + timeout
        while True:
            if self.cancelled.is_set():
                raise PreparationSourceError("已取消 Word 定位。")
            if (self.directory / "error.json").exists():
                # COM diagnostics may include local paths or document text;
                # they are never returned to logs or the public UI.
                error = json.loads((self.directory / "error.json").read_text(encoding="utf-8-sig"))
                if error.get("detail") == "word_already_running":
                    raise PreparationSourceError("Word 正在运行或尚未退出，请关闭已打开的 Word 窗口并稍候再试。")
                raise PreparationSourceError("Word 未能完成原件定位。请关闭相关提示后重新核对；原文件未保存。")
            path = self.directory / (name + ".json")
            if path.exists():
                if path.stat().st_size > 1024 * 1024:
                    raise PreparationSourceError("Word 定位响应超过限制。")
                return json.loads(path.read_text(encoding="utf-8-sig"))
            if self.process.poll() is not None or time.monotonic() >= deadline:
                raise PreparationSourceError("Word 定位未及时完成，请核对本机 Word 后重试。")
            time.sleep(0.05)

    def xml(self, name):
        path = self.directory / (name + ".xml")
        if not path.is_file() or path.stat().st_size > MAX_XML_BYTES:
            raise PreparationSourceError("Word 原文快照不完整或超过限制。")
        return path.read_text(encoding="utf-8-sig")

    def close(self, on_exit):
        """Return True when cleanup ownership has moved to the reaper.

        Never kill a helper blocked inside COM: that would bypass its finally
        and orphan its hidden Word instance. Keep both cancellation files and
        the session lock alive until the helper has actually exited.
        """
        (self.directory / "cancel").touch()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            (self.directory / "abandoned").touch()
            def reap():
                try:
                    self.process.wait()
                finally:
                    on_exit()
            threading.Thread(target=reap, name="word-location-cleanup", daemon=True).start()
            return True
        return False


def _native_candidate(opened, plan):
    if (not isinstance(opened, dict) or opened.get("read_only") is not True
            or any(type(opened.get(key)) is not int for key in ("content_start", "content_end"))
            or opened["content_start"] != 0 or opened["content_end"] <= 0):
        raise PreparationSourceError("Word 文档身份或只读状态未通过核对。")
    records = opened.get("records")
    if not isinstance(records, list) or not 0 < len(records) <= MAX_NATIVE_OBJECTS:
        raise PreparationSourceError("Word 原生对象清单无法可靠核对。")
    for row in records:
        if not isinstance(row, dict) or any(type(row.get(key)) is not int for key in ("index", "type", "story", "start", "end")):
            raise PreparationSourceError("Word 原生范围信息不完整。")
        if (row.get("kind") not in {"inline", "math"} or row["story"] != 1
                or row["start"] < opened["content_start"] or row["end"] > opened["content_end"]
                or row["start"] >= row["end"] or row["index"] < 1
                or (row["kind"] == "inline" and row["type"] != 3)
                or (row["kind"] == "math" and row["type"] not in {0, 1})):
            raise PreparationSourceError("当前 Word 包含未支持的原生对象或范围。")
    records = sorted(records, key=lambda row: (row["start"], row["end"]))
    if any(left["end"] > right["start"] for left, right in zip(records, records[1:])):
        raise PreparationSourceError("Word 原生对象范围重叠，暂不能自动定位。")
    if len(records) != len(plan["objects"]):
        raise PreparationSourceError("Word 原生对象数量与原文不一致。")
    return records[plan["target_index"]]


def select_original(path, data, xml_locator, *, cancelled=None, revalidate=None, visible=True, session_factory=None):
    """Direct selection. ``visible=False`` is for controlled Office QA only."""
    from .desktop_native_word_mapping import build_source_plan, verify_document, verify_range

    cancelled = cancelled or threading.Event()
    if cancelled.is_set():
        raise PreparationSourceError("已取消 Word 定位。")
    path = Path(path).resolve(strict=True)
    digest = hashlib.sha256(data).hexdigest()
    if path.suffix.lower() != ".docx" or path.read_bytes() != data:
        raise PreparationSourceError("原 Word 文件已经变化，请重新打开原文。")
    try:
        plan = build_source_plan(data, xml_locator)
    except PreparationSourceError as exc:
        if "table_math_has_no_unique_range_identity" in str(exc):
            raise PreparationSourceError("表格内公式暂不能可靠地自动选中，请按单元格位置在原 Word 中核对。") from exc
        raise PreparationSourceError("这份 Word 含暂不支持自动定位的对象、字段或结构。请继续按段落、单元格和原图核对。") from exc
    if session_factory is None and sys.platform != "win32":
        raise PreparationSourceError("原件自动定位需要 Windows 版 Microsoft Word。")
    if not _SESSION_LOCK.acquire(blocking=False):
        raise PreparationSourceError("已有 Word 定位正在进行或前次 Word 仍在退出，请稍候；若有 Word 提示，请先处理。")
    directory = None
    deferred_cleanup = False
    def cleanup():
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        _SESSION_LOCK.release()
    try:
        directory = tempfile.mkdtemp(prefix="shchem-word-location-")
        if directory:
            session = None
            try:
                if cancelled.is_set():
                    raise PreparationSourceError("已取消 Word 定位。")
                session = (session_factory or _WordSession)(directory, {"path": str(path), "sha256": digest, "visible": bool(visible)}, cancelled)
                opened = session.wait("opened")
                document_xml = session.xml("document")
                document_evidence = verify_document(plan, document_xml)
                native = _native_candidate(opened, plan)
                session.send("inspect", {"kind": native["kind"], "index": native["index"]})
                inspected = session.wait("inspected")
                if inspected != native:
                    raise PreparationSourceError("Word 对象在核对期间发生变化。")
                range_evidence = verify_range(plan, document_xml, session.xml("range"), session.xml("before"), session.xml("through"), native["kind"], native["type"])
                if revalidate is not None:
                    revalidate()
                if cancelled.is_set():
                    raise PreparationSourceError("已取消 Word 定位。")
                if path.read_bytes() != data:
                    raise PreparationSourceError("原 Word 文件已经变化，请重新核对。")
                session.send("select", {"action": "select"})
                result = session.wait("selected")
                if (not isinstance(result, dict) or result.get("status") != "selected" or result.get("selection_verified") is not True
                        or result.get("source_unchanged") is not True or result.get("read_only") is not True
                        or any(result.get(key) != native[key] for key in ("story", "start", "end"))):
                    raise PreparationSourceError("Word 选区回读未通过，请按原文位置核对。")
                selected_document = session.xml("selected-document")
                document_evidence = verify_document(plan, selected_document)
                range_evidence = verify_range(plan, selected_document, session.xml("selected-range"),
                                              session.xml("selected-before"), session.xml("selected-through"),
                                              native["kind"], native["type"])
                if visible:
                    if revalidate is not None:
                        revalidate()
                    if cancelled.is_set():
                        raise PreparationSourceError("已取消 Word 定位。")
                    session.send("publish", {"action": "publish"})
                    published = session.wait("published")
                    if published != {"status": "published", "visible": True}:
                        raise PreparationSourceError("Word 窗口交接未完成，请核对本机 Word。")
                    result["visible"] = True
                return {**result, "source_sha256": digest, "document_evidence": document_evidence, "range_evidence": range_evidence}
            finally:
                if session is not None:
                    deferred_cleanup = session.close(on_exit=cleanup)
    finally:
        if not deferred_cleanup:
            cleanup()

"""Execute real helper control flow against in-memory PowerShell Word doubles.

No Office instance, COM activation, source document, registry entry, or protocol
file is used. The full helper is only parsed; the executed AST fragments are
limited to publication and private-instance Close/Quit decisions.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from integrations.deeptutor_shchem_v1 import desktop_native_word_selection as selection


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell control-flow regression")


_HARNESS = r'''
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::InputEncoding=New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$helper=[Console]::In.ReadToEnd()
$case=[Environment]::GetEnvironmentVariable('SHCHEM_LIFECYCLE_CASE','Process') | ConvertFrom-Json
$tokens=$null; $parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($helper,[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw 'helper_parse_error' }
$mains=@($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] })
if ($mains.Count -ne 1) { throw 'expected_one_top_level_try' }
$main=$mains[0]
$functions=@()
foreach ($name in @('Check-Cancel','Restore-Options','Test-Private-Session')) {
  $match=@($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq $name
  })
  if ($match.Count -ne 1) { throw ('missing_control_function_'+$name) }
  $functions+=@($match[0].Extent.Text)
}
$publishNodes=@($main.Body.Statements | Where-Object {
  $_ -is [Management.Automation.Language.IfStatementAst] -and
  $_.Clauses[0].Item1.Extent.Text.Trim() -ceq '$request.visible'
})
if ($publishNodes.Count -ne 1) { throw 'expected_one_publish_branch' }
$publish=$publishNodes[0].Extent.Text
$cleanupParts=@()
$foundRelease=$false
foreach ($statement in $main.Finally.Statements) {
  if ($statement -is [Management.Automation.Language.ForEachStatementAst] -and
      $statement.Variable.VariablePath.UserPath -ceq 'com') {
    $foundRelease=$true
    break
  }
  $cleanupParts+=@($statement.Extent.Text)
}
if (-not $foundRelease -or -not $cleanupParts.Count) { throw 'missing_cleanup_decision' }
$cleanup=$cleanupParts -join "`n"
# Fail closed if future refactoring moves live activation or OS side effects
# into the selected fragments. Never invoke the full parsed helper.
$executed=($functions+@($publish,$cleanup)) -join "`n"
if ($executed -match '(?i)-ComObject|OpenNoRepairDialog|Get-Process|Start-Process|Stop-Process|CoCreateInstance|GetActiveObject|Remove-Item|\[IO\.File\]|\[IO\.Directory\]') {
  throw 'unsafe_side_effect_in_selected_fragment'
}

if ($case.mode -ceq 'abandoned') {
  $abandonedNodes=@($main.Finally.Statements | Where-Object {
    $_ -is [Management.Automation.Language.IfStatementAst] -and
    $_.Clauses[0].Item1.Extent.Text -match "'abandoned'"
  })
  if ($abandonedNodes.Count -ne 1) { throw 'expected_one_abandoned_cleanup_branch' }
  $branch=$abandonedNodes[0]
  $commands=$branch.FindAll({param($node) $node -is [Management.Automation.Language.CommandAst]},$true)
  foreach ($command in $commands) {
    if ($command.GetCommandName() -notin @('Test-Path','Join-Path','Get-Item','Remove-Item')) {
      throw 'unmocked_command_in_abandoned_cleanup'
    }
  }
  if ($branch.Extent.Text -match '(?i)\[IO\.(File|Directory)\]|\.Delete\(') {
    throw 'unmocked_filesystem_method_in_abandoned_cleanup'
  }
  $parent=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([char]'\')
  if ($case.outside_root) { $parent='C:\synthetic-unrelated-parent' }
  $name='shchem-word-location-synthetic-only'
  if ($case.wrong_name) { $name='synthetic-unrelated-directory' }
  $dir=Join-Path $parent $name
  $attributes=[IO.FileAttributes]0
  if ($case.reparse) { $attributes=[IO.FileAttributes]::ReparsePoint }
  $script:directoryInfo=[pscustomobject]@{
    FullName=$dir;Name=$name;Parent=[pscustomobject]@{FullName=$parent};Attributes=$attributes
  }
  $script:removed=New-Object 'System.Collections.Generic.List[string]'
  function Test-Path { param($LiteralPath) return [bool]$case.marker }
  function Get-Item { param($LiteralPath,[switch]$Force) return $script:directoryInfo }
  function Remove-Item {
    param($LiteralPath,[switch]$Recurse,[switch]$Force)
    if (-not $Recurse -or -not $Force) { throw 'unexpected_cleanup_flags' }
    # Never dispatch to the real cmdlet. Capture the requested target only.
    $script:removed.Add($LiteralPath)
  }
  . ([scriptblock]::Create($branch.Extent.Text))
  [pscustomobject]@{directory=$dir;removed=@($script:removed)} | ConvertTo-Json -Compress
  exit
}

# This is an in-memory fake type, not the helper's user32 P/Invoke class.
class ShChemWordOwner {
  static [uint32] $ReportedPid=4242
  static [uint32] GetWindowThreadProcessId([IntPtr] $hwnd,[ref] $process) {
    $process.Value=[ShChemWordOwner]::ReportedPid
    return 1
  }
}
if ($case.wrong_pid) { [ShChemWordOwner]::ReportedPid=4343 }
$script:cancelled=$false
$script:closeCalls=0; $script:quitCalls=0; $script:otherQuitCalls=0
$script:visibleWrites=0; $script:wordVisible=[bool]$case.visible
$script:userControl=[bool]$case.user_control
$script:securityValue=3
$script:writes=New-Object 'System.Collections.Generic.List[object]'
$dir='synthetic-private-directory'
$created= -not [bool]$case.not_created
$owned= -not [bool]$case.ambiguous_pid
$handedOff=[bool]$case.handed_off
$zero=[object]0
$wordPid=[uint32]4242
$request=[pscustomobject]@{path='synthetic-original.docx';sha256='synthetic-sha';visible=$true}
$snapshot='synthetic-document-xml'; $rangeXml='synthetic-range-xml'
$start=[object]20; $end=[object]21
$security=1; $links=$true; $alerts=-1
$range=[pscustomobject]@{WordOpenXML=$rangeXml;Start=20;End=21;StoryType=1}
$documents=[pscustomobject]@{Count=1;Current=$null}
$word=[pscustomobject]@{
  Documents=$documents;Options=[pscustomobject]@{UpdateLinksAtOpen=$false};DisplayAlerts=0
  Selection=[pscustomobject]@{Start=20;End=21;StoryType=1}
}
if ($case.range_changed) { $range.Start=19 }
if ($case.selection_changed) { $word.Selection.End=22 }
$word | Add-Member -MemberType ScriptProperty -Name Visible -Value { $script:wordVisible } -SecondValue {
  param($value)
  $script:wordVisible=[bool]$value
  if ($value) { $script:visibleWrites++ }
}
$word | Add-Member -MemberType ScriptProperty -Name UserControl -Value { $script:userControl }
$word | Add-Member -MemberType ScriptProperty -Name AutomationSecurity -Value { $script:securityValue } -SecondValue {
  param($value)
  $script:securityValue=$value
  if ($case.cancel_during_restore) { $script:cancelled=$true }
  if ($case.takeover_during_restore) { $script:userControl=$true }
}
$word | Add-Member -MemberType ScriptMethod -Name Quit -Value {
  param($saveChanges)
  if ($saveChanges.Value -ne 0) { throw 'unexpected_save_request' }
  $script:quitCalls++
}
$otherWord=[pscustomobject]@{Visible=$true;UserControl=$true}
$otherWord | Add-Member -MemberType ScriptMethod -Name Quit -Value { $script:otherQuitCalls++ }
$windows=[pscustomobject]@{Window=[pscustomobject]@{Hwnd=123}}
$windows | Add-Member -MemberType ScriptMethod -Name Item -Value {
  param($index)
  if ($index -ne 1) { throw 'unexpected_window_index' }
  return $this.Window
}
$doc=[pscustomobject]@{
  ReadOnly=$true;FullName=$request.path;Windows=$windows;WordOpenXML=$snapshot
}
$doc | Add-Member -MemberType ScriptMethod -Name Close -Value {
  param($saveChanges)
  if ($saveChanges.Value -ne 0) { throw 'unexpected_save_request' }
  $script:closeCalls++
  $documents.Count=0
  $documents.Current=$null
  if ($case.takeover_during_close) { $script:userControl=$true; $script:wordVisible=$true }
}
$documents.Current=$doc
$documents | Add-Member -MemberType ScriptMethod -Name Item -Value {
  param($index)
  if ($index -ne 1 -or $this.Count -ne 1) { throw 'unexpected_document_index' }
  return $this.Current
}
if ($case.second_document) { $documents.Count=2 }
if ($case.not_readonly) { $doc.ReadOnly=$false }
if ($case.wrong_path) { $doc.FullName='another-synthetic.docx' }
if ($case.empty) { $doc=$null; $documents.Count=0; $documents.Current=$null }

# File protocol operations are replaced by memory-only functions. Their real
# callers remain extracted from _SCRIPT, including the real Check-Cancel body.
function Test-Path { param($LiteralPath) return $script:cancelled }
function Wait-Command { param($name) return [pscustomobject]@{action=$name} }
function Source-Hash {
  if ($case.source_changed) { return 'different-synthetic-sha' }
  return 'synthetic-sha'
}
function Write-Json { param($name,$value) $script:writes.Add([pscustomobject]@{name=$name;value=$value}) }
foreach ($definition in $functions) { . ([scriptblock]::Create($definition)) }
$failure=$null
if ($case.mode -ceq 'publish') {
  try { . ([scriptblock]::Create($publish)) } catch { $failure=$_.Exception.Message }
}
. ([scriptblock]::Create($cleanup))
[pscustomobject]@{
  failure=$failure;cancelled=$script:cancelled;visible=$script:wordVisible;handed_off=$handedOff
  close_calls=$script:closeCalls;quit_calls=$script:quitCalls;other_quit_calls=$script:otherQuitCalls
  visible_writes=$script:visibleWrites;disposition=$disposition
  writes=@($script:writes | ForEach-Object { $_.name })
} | ConvertTo-Json -Depth 5 -Compress
'''


def _run_flow(**case):
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not executable.is_file():
        pytest.skip("Windows PowerShell unavailable")
    result = subprocess.run(
        [str(executable), "-STA", "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(_HARNESS.encode("utf-16le")).decode("ascii")],
        input=selection._SCRIPT, text=True, encoding="utf-8", capture_output=True,
        # Windows CI can cold-start PowerShell well above 15 seconds. Keep a
        # bounded launch budget without changing the lifecycle assertions.
        env={**os.environ, "SHCHEM_LIFECYCLE_CASE": json.dumps(case)}, timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_cancel_during_real_restore_options_never_publishes():
    result = _run_flow(mode="publish", cancel_during_restore=True)
    assert result["cancelled"] is True and result["failure"] == "cancelled"
    assert not result["visible"] and not result["handed_off"]
    assert result["visible_writes"] == 0 and not result["writes"]
    assert result["close_calls"] == 1 and result["quit_calls"] == 1
    assert result["other_quit_calls"] == 0


def test_successful_publication_hands_off_without_closing_user_window():
    result = _run_flow(mode="publish")
    assert result["failure"] is None
    assert result["visible"] and result["handed_off"]
    assert result["writes"] == ["publishing", "published"]
    assert result["close_calls"] == result["quit_calls"] == 0
    assert result["disposition"] == "handed_off"


@pytest.mark.parametrize("mutation", ["source_changed", "range_changed", "selection_changed"])
def test_changed_source_or_live_range_never_publishes(mutation):
    result = _run_flow(mode="publish", **{mutation: True})
    assert result["failure"] == "document_changed"
    assert not result["writes"] and result["visible_writes"] == 0
    assert not result["visible"] and not result["handed_off"]


@pytest.mark.parametrize("case", [
    {"visible": True}, {"user_control": True}, {"second_document": True},
    {"not_readonly": True}, {"wrong_path": True}, {"wrong_pid": True},
])
def test_adopted_or_unverified_instance_never_closes_or_quits(case):
    result = _run_flow(mode="cleanup", **case)
    assert result["close_calls"] == result["quit_calls"] == 0
    assert result["other_quit_calls"] == 0
    assert result["disposition"] == "preserved_after_takeover_or_unverified_cleanup"


def test_private_readonly_instance_closes_only_its_document_then_quits():
    result = _run_flow(mode="cleanup")
    assert result["close_calls"] == result["quit_calls"] == 1
    assert result["other_quit_calls"] == 0
    assert result["disposition"] == "quit_requested"


@pytest.mark.parametrize("moment", ["close", "restore"])
def test_takeover_during_cleanup_prevents_later_quit(moment):
    result = _run_flow(mode="cleanup", **{f"takeover_during_{moment}": True})
    assert result["close_calls"] == 1 and result["quit_calls"] == 0
    assert result["other_quit_calls"] == 0


def test_takeover_during_publish_restore_never_exposes_or_closes_document():
    result = _run_flow(mode="publish", takeover_during_restore=True)
    assert result["failure"] == "session_taken_over"
    assert not result["writes"] and result["visible_writes"] == 0
    assert result["close_calls"] == result["quit_calls"] == 0


def test_ambiguous_pid_can_quit_only_exact_empty_created_com_reference():
    result = _run_flow(mode="cleanup", empty=True, ambiguous_pid=True)
    assert result["close_calls"] == 0 and result["quit_calls"] == 1
    assert result["other_quit_calls"] == 0
    assert result["disposition"] == "empty_created_instance_quit_requested"


@pytest.mark.parametrize("case", [
    {"visible": True}, {"user_control": True}, {"not_created": True},
    {"handed_off": True}, {"empty": False},
])
def test_ambiguous_pid_does_not_quit_unproven_or_adopted_reference(case):
    result = _run_flow(mode="cleanup", **{"empty": True, "ambiguous_pid": True, **case})
    assert result["close_calls"] == result["quit_calls"] == result["other_quit_calls"] == 0


def test_abandoned_cleanup_targets_only_its_named_direct_temporary_child():
    result = _run_flow(mode="abandoned", marker=True)
    assert result["removed"] == [result["directory"]]


@pytest.mark.parametrize("case", [
    {"marker": False}, {"outside_root": True}, {"wrong_name": True}, {"reparse": True},
])
def test_abandoned_cleanup_rejects_unowned_or_redirected_directory(case):
    result = _run_flow(mode="abandoned", **{"marker": True, **case})
    assert result["removed"] == []

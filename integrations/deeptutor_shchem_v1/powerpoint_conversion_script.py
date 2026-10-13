"""PowerPoint COM protocol: bind a new hidden PID before opening any document."""

POWERPOINT_SCRIPT = r'''
$ErrorActionPreference='Stop'
$app=$null; $doc=$null; $range=$null; $owned=$false; $pidVerified=$false; $officePid=[uint32]0
$ownedHandle=[IntPtr]::Zero; $sourceStream=$null
$phase='starting'; $cleanupStep='not_started'; $cleanupSteps=@(); $cleanupException=$null
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
function Stage($value) { $script:phase=$value; Record 'stage' @{stage=$value;pid=[int]$officePid;pid_verified=$pidVerified} }
function Cleanup-Stage($value) {
  $script:cleanupStep=$value; $script:cleanupSteps+=@{step=$value;utc=[DateTime]::UtcNow.ToString('o')}
  try { Record 'cleanup-progress' @{steps=$script:cleanupSteps;pid=[int]$officePid;pid_verified=$pidVerified} } catch {}
}
function Cleanup-Failure($failure) {
  $cause=$failure.Exception; while ($null -ne $cause.InnerException) { $cause=$cause.InnerException }
  $script:cleanupException=@{step=$script:cleanupStep;hresult=$cause.HResult;error_type=$cause.GetType().FullName}
  try { Record 'cleanup-exception' $script:cleanupException } catch {}
}
function Check-Cancel { if (Test-Path -LiteralPath (Join-Path $directory 'cancel')) { throw 'cancelled' } }
function Source-Hash {
  $sourceStream.Position=0; $sha=[Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($sha.ComputeHash($sourceStream))).Replace('-','').ToLowerInvariant() }
  finally { $sha.Dispose(); $sourceStream.Position=0 }
}
function Private-PowerPoint($empty=$false) {
  try {
    if (-not $owned -or -not $pidVerified -or $app.Visible -ne 0) { return $false }
    if ($ownedHandle -eq [IntPtr]::Zero -or [ShChemConversionOwner]::WaitForSingleObject($ownedHandle,0) -ne 258) { return $false }
    if ([ShChemConversionOwner]::Creation($ownedHandle) -ne $startFileTime) { return $false }
    $pidNow=[uint32]0
    $thread=[ShChemConversionOwner]::GetWindowThreadProcessId([IntPtr]$app.HWND,[ref]$pidNow)
    if ($thread -eq 0 -or $pidNow -ne $officePid) { return $false }
    if ($empty -or $null -eq $doc) { return ($app.Presentations.Count -eq 0) }
    return ($app.Presentations.Count -eq 1 -and $doc.ReadOnly -ne 0 -and $doc.FullName -ieq $inputPath)
  } catch { return $false }
}
try {
  Stage 'checking_input'; Check-Cancel
  $sourceStream=[IO.File]::Open($inputPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
  if ((Source-Hash) -cne $expectedSha) { throw 'input_changed' }
  $prior=@(Get-Process POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  Record 'baseline' @{pids=$prior;pid_verified=$false}
  # PowerPoint may reuse a running instance. Never activate COM in that case.
  if ($prior.Count -ne 0) { throw 'powerpoint_in_use' }
  Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ShChemConversionOwner { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid); [DllImport("kernel32.dll")] public static extern IntPtr OpenProcess(uint rights, bool inherit, uint pid); [DllImport("kernel32.dll")] public static extern uint WaitForSingleObject(IntPtr handle,uint wait); [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle); [DllImport("kernel32.dll")] static extern bool GetProcessTimes(IntPtr handle,out long creation,out long exit,out long kernel,out long user); public static long Creation(IntPtr h) { long c,e,k,u; return GetProcessTimes(h,out c,out e,out k,out u) ? c : 0; } }'
  Check-Cancel; $launched=[DateTime]::UtcNow
  Stage 'creating_com'; $app=New-Object -ComObject PowerPoint.Application
  Stage 'binding_new_hidden_pid_before_open'
  $thread=[ShChemConversionOwner]::GetWindowThreadProcessId([IntPtr]$app.HWND,[ref]$officePid)
  if ($thread -eq 0 -or $officePid -eq 0 -or $prior -contains [int]$officePid) { throw 'ownership_unproven' }
  $process=Get-Process -Id $officePid -ErrorAction Stop
  $startFileTime=$process.StartTime.ToUniversalTime().ToFileTimeUtc()
  if ($process.ProcessName -ine 'POWERPNT' -or $process.StartTime.ToUniversalTime() -lt $launched) { throw 'ownership_unproven' }
  $ownedHandle=[ShChemConversionOwner]::OpenProcess(0x00101000,$false,$officePid)
  if ($ownedHandle -eq [IntPtr]::Zero -or [ShChemConversionOwner]::Creation($ownedHandle) -ne $startFileTime) { throw 'ownership_unproven' }
  if ($app.Visible -ne 0 -or $app.Presentations.Count -ne 0) { throw 'ownership_unproven' }
  $pidVerified=$true; $owned=$true
  Record 'owner' @{pid=[int]$officePid;start_filetime=$startFileTime;private=$true;held_handle=$true}
  Record 'automation' @{identity='new_pid_empty_com';pid_verified_before_open=$true}
  if (-not (Private-PowerPoint $true)) { throw 'session_taken_over' }
  Check-Cancel; $app.AutomationSecurity=3
  if (-not (Private-PowerPoint $true)) { throw 'session_taken_over' }
  Stage 'opening_private_readonly_copy'; Check-Cancel
  $doc=$app.Presentations.Open($inputPath,-1,0,0)
  if (-not (Private-PowerPoint)) { throw 'session_taken_over' }
  Stage 'converting'; Check-Cancel
  # Pass an explicit all-slide PrintRange: PowerShell cannot marshal Nothing here.
  $range=$doc.PrintOptions.Ranges.Add(1,$doc.Slides.Count)
  $doc.ExportAsFixedFormat($outputPath,2,1,0,1,1,0,$range,4,'',$false,$true,$true,$true,$false)
  Check-Cancel
  if (-not (Private-PowerPoint)) { throw 'session_taken_over' }
  if ((Source-Hash) -cne $expectedSha) { throw 'input_changed' }
  Record 'result' @{status='converted'}
} catch {
  $reason=$_.Exception.Message
  if ($reason -notin @('cancelled','ownership_unproven','session_taken_over','input_changed','powerpoint_in_use')) { $reason='conversion_failed' }
  $cause=$_.Exception; while ($null -ne $cause.InnerException) { $cause=$cause.InnerException }
  Record 'error' @{code=$reason;stage=$phase;hresult=$cause.HResult;error_type=$cause.GetType().FullName;pid=[int]$officePid;pid_verified=$pidVerified}
} finally {
  # BEGIN_PRIVATE_CLEANUP: never quit an existing or teacher-adopted instance.
  $disposition='ownership_unproven_preserved'; Cleanup-Stage 'checking_private_state'
  if ($owned) {
    $disposition='taken_over_or_unverified_preserved'
    if (Private-PowerPoint) {
      try {
        if ($null -ne $doc) { Cleanup-Stage 'closing_document'; $doc.Close(); Cleanup-Stage 'document_close_returned' }
        Cleanup-Stage 'checking_empty_private_state'
        if (Private-PowerPoint $true) {
          Cleanup-Stage 'quitting_application'; $app.Quit(); Cleanup-Stage 'application_quit_returned'
          $disposition='quit_requested'
        }
      } catch { Cleanup-Failure $_; $disposition='cleanup_unverified_preserved' }
    }
  }
  Cleanup-Stage 'releasing_com_references'
  foreach ($reference in @($range,$doc,$app)) {
    if ($null -ne $reference -and [Runtime.InteropServices.Marshal]::IsComObject($reference)) {
      try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($reference) } catch {}
    }
  }
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
  if ($disposition -eq 'quit_requested') {
    Cleanup-Stage 'waiting_held_process_exit'; $until=[DateTime]::UtcNow.AddSeconds(2)
    do {
      if ([ShChemConversionOwner]::WaitForSingleObject($ownedHandle,0) -eq 0) { $disposition='owned_instance_exited'; break }
      Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $until)
  }
  Cleanup-Stage 'cleanup_finished'
  try { Record 'cleanup' @{disposition=$disposition;pid=[int]$officePid;steps=$cleanupSteps;exception=$cleanupException} }
  finally {
    if ($ownedHandle -ne [IntPtr]::Zero) { [void][ShChemConversionOwner]::CloseHandle($ownedHandle) }
    if ($null -ne $sourceStream) { $sourceStream.Dispose() }
  }
  # END_PRIVATE_CLEANUP
}
'''

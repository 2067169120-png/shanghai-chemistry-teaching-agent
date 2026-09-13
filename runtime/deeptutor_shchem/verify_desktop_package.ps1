param(
    [Parameter(Mandatory = $true)][string]$PackageDirectory,
    [Parameter(Mandatory = $true)][string]$WorkspaceDirectory,
    [string]$LauncherFile
)

$ErrorActionPreference = 'Stop'
$PackageRoot = (Resolve-Path -LiteralPath $PackageDirectory).Path
$WorkspaceRoot = (Resolve-Path -LiteralPath $WorkspaceDirectory).Path
# ASCII script text remains executable under Windows PowerShell 5.1 as well.
$AppName = -join @([char]0x6CAA, [char]0x4E0A, [char]0x5316, [char]0x5B66, [char]0x667A, [char]0x7814, [char]0x53F0)
$LauncherName = (-join @([char]0x542F, [char]0x52A8)) + $AppName + (-join @([char]0x684C, [char]0x9762, [char]0x7248)) + '.vbs'
$LauncherPath = Join-Path $PackageRoot $LauncherName
if ($LauncherFile) { $LauncherPath = (Resolve-Path -LiteralPath $LauncherFile).Path }
$ExePath = Join-Path (Join-Path $PackageRoot $AppName) ($AppName + '.exe')
if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf) -or -not (Test-Path -LiteralPath $LauncherPath -PathType Leaf)) {
    throw 'Package executable or launcher is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $WorkspaceRoot 'sh-chem-db') -PathType Container)) {
    throw 'Evidence workspace is missing.'
}
$Existing = @(Get-Process -Name $AppName -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $ExePath })
if ($Existing.Count) { throw 'The exact package executable is already running; no existing process was touched.' }
$QaRoot = Join-Path ([IO.Path]::GetTempPath()) ('shchem-package-start-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $QaRoot | Out-Null
$SavedLocalData = $env:LOCALAPPDATA
$SavedWorkspace = $env:SHCHEM_WORKSPACE_ROOT
$SavedQtPlatform = $env:QT_QPA_PLATFORM
$SavedSearchPath = $env:Path
$SavedPythonHome = $env:PYTHONHOME
$SavedPythonPath = $env:PYTHONPATH
$OwnedProcess = $null
$Result = $null
try {
    $env:LOCALAPPDATA = $QaRoot
    $env:SHCHEM_WORKSPACE_ROOT = $WorkspaceRoot
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME,Env:PYTHONPATH -ErrorAction SilentlyContinue
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
    $Timer = [Diagnostics.Stopwatch]::StartNew()
    $LauncherProcess = Start-Process -FilePath "$env:SystemRoot\System32\wscript.exe" -ArgumentList ('"' + $LauncherPath + '"') -WorkingDirectory $PackageRoot -WindowStyle Hidden -PassThru
    while ($Timer.Elapsed.TotalSeconds -lt 45) {
        $Candidates = @(Get-Process -Name $AppName -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $ExePath })
        if ($Candidates.Count -gt 1) { throw 'More than one package process appeared.' }
        if ($Candidates.Count -eq 1) {
            $OwnedProcess = $Candidates[0]
            $OwnedProcess.Refresh()
            if ($OwnedProcess.MainWindowHandle -ne 0 -and $OwnedProcess.MainWindowTitle -eq $AppName -and $OwnedProcess.Responding) { break }
        }
        Start-Sleep -Milliseconds 200
    }
    if (-not $OwnedProcess -or $OwnedProcess.MainWindowHandle -eq 0 -or $OwnedProcess.MainWindowTitle -ne $AppName -or -not $OwnedProcess.Responding) {
        throw 'Native main window did not become ready within 45 seconds.'
    }
    # Keep a handle while the VBS-owned child is alive, so its exit code remains
    # available after exit (Get-Process alone otherwise returns a null code).
    $null = $OwnedProcess.Handle
    $Result = [ordered]@{
        launcher = $LauncherPath
        executable = $ExePath
        executable_sha256 = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash
        launcher_pid = $LauncherProcess.Id
        application_pid = $OwnedProcess.Id
        title = $OwnedProcess.MainWindowTitle
        responding = $OwnedProcess.Responding
        window_handle_present = ($OwnedProcess.MainWindowHandle -ne 0)
        seconds = [Math]::Round($Timer.Elapsed.TotalSeconds, 2)
        isolated_local_app_data = $QaRoot
        closed = $false
        close_seconds = $null
        exit_code = $null
        minimal_environment = $true
    }
}
finally {
    if ($OwnedProcess -and -not $OwnedProcess.HasExited -and $OwnedProcess.Path -eq $ExePath) {
        $CloseTimer = [Diagnostics.Stopwatch]::StartNew()
        $null = $OwnedProcess.CloseMainWindow()
        # The window closes while initial read-only library workers unwind.
        $Closed = $OwnedProcess.WaitForExit(10000)
        if ($Result) {
            $Result.closed = $Closed
            $Result.close_seconds = [Math]::Round($CloseTimer.Elapsed.TotalSeconds, 2)
            if ($Closed) { $Result.exit_code = $OwnedProcess.ExitCode }
        }
    }
    $env:LOCALAPPDATA = $SavedLocalData
    $env:SHCHEM_WORKSPACE_ROOT = $SavedWorkspace
    $env:QT_QPA_PLATFORM = $SavedQtPlatform
    $env:Path = $SavedSearchPath
    $env:PYTHONHOME = $SavedPythonHome
    $env:PYTHONPATH = $SavedPythonPath
}
if ($Result) {
    $ErrorLogs = @(Get-ChildItem -LiteralPath $QaRoot -Filter 'startup-error.log' -File -Recurse -ErrorAction SilentlyContinue)
    $Result.startup_error_log_count = $ErrorLogs.Count
}
if ($Result) { $Result | ConvertTo-Json -Depth 3 }
if (-not $Result -or -not $Result.closed -or $Result.exit_code -ne 0) { throw 'Package verification did not complete cleanly; inspect the owned application process.' }
if ($Result.startup_error_log_count -ne 0) { throw 'Startup error log found in isolated app state.' }

$ErrorActionPreference = 'Stop'
$TaskWorkspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../../..')).Path
$TaskNative = (Resolve-Path -LiteralPath (Join-Path $TaskWorkspace 'sh-chem-db/kb/teaching_handout_question_slices_v1_2026-08-28/native_ooxml_v2')).Path
$TaskOld = Join-Path $TaskNative 'batches/NATIVE-BATCH-NV2W2-PKG043-A01'
$TaskStaged = Join-Path $TaskNative 'rebuild_staging/PKG043-A02/NATIVE-BATCH-NV2W2-PKG043-A02'
$TaskNew = Join-Path $TaskNative 'batches/NATIVE-BATCH-NV2W2-PKG043-A02'
$TaskPending = Join-Path $TaskNative 'activation_pending/NATIVE-BATCH-NV2W2-PKG043-A02'
$TaskRetired = Join-Path $TaskNative 'retired_batches/NATIVE-BATCH-NV2W2-PKG043-A01'
$TaskQa = Join-Path $TaskWorkspace 'runtime/deeptutor_shchem/qa/pkg043-a02-activation-20260908'
$TaskPython = 'C:/Users/20671/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'

# Resolve every recursive copy/move target, verify its scope and reject junctions.
foreach ($TaskTarget in @($TaskOld, $TaskStaged, $TaskNew, $TaskPending, $TaskRetired)) {
    $TaskAbsolute = [IO.Path]::GetFullPath($TaskTarget)
    if (-not $TaskAbsolute.StartsWith($TaskNative + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Batch target escapes the exact native library root.'
    }
    $TaskAncestor = $TaskAbsolute
    while ($TaskAncestor -and $TaskAncestor.Length -ge $TaskNative.Length) {
        if (Test-Path -LiteralPath $TaskAncestor) {
            if ((Get-Item -LiteralPath $TaskAncestor).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Refusing a reparse-point batch path.'
            }
        }
        $TaskAncestor = Split-Path -Parent $TaskAncestor
    }
}
if (-not (Test-Path -LiteralPath $TaskOld -PathType Container) -or -not (Test-Path -LiteralPath $TaskStaged -PathType Container)) {
    throw 'Expected old/staged batch is missing.'
}
foreach ($TaskTarget in @($TaskNew, $TaskPending, $TaskRetired)) {
    if (Test-Path -LiteralPath $TaskTarget) { throw 'Destination already exists; no overwrite is allowed.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $TaskQa 'preflight.json')) -or (Test-Path -LiteralPath (Join-Path $TaskQa 'activated.json'))) {
    throw 'Missing preflight or activation already recorded.'
}
$TaskAppName = -join @([char]0x6CAA, [char]0x4E0A, [char]0x5316, [char]0x5B66, [char]0x667A, [char]0x7814, [char]0x53F0)
$TaskApps = @(Get-Process -Name $TaskAppName -ErrorAction SilentlyContinue)
$TaskPythonApps = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match 'desktop_teacher_workbench\.pyw' })
if ($TaskApps.Count -or $TaskPythonApps.Count) { throw 'Please close the workbench first; no running process was touched.' }

foreach ($TaskManifest in @('VERSION_MANIFEST.json', '../PRODUCT_MANIFEST.json')) {
    $TaskManifestPath = (Resolve-Path -LiteralPath (Join-Path $TaskNative $TaskManifest)).Path
    $TaskBackup = Join-Path $TaskQa ((Split-Path -Leaf $TaskManifestPath) + '.before')
    if (Test-Path -LiteralPath $TaskBackup) { throw 'Manifest backup already exists.' }
    Copy-Item -LiteralPath $TaskManifestPath -Destination $TaskBackup
}
foreach ($TaskParent in @((Split-Path -Parent $TaskPending), (Split-Path -Parent $TaskRetired))) {
    if (-not (Test-Path -LiteralPath $TaskParent)) { New-Item -ItemType Directory -Path $TaskParent | Out-Null }
}
Copy-Item -LiteralPath $TaskStaged -Destination $TaskPending -Recurse
$TaskOldMoved = $false
$TaskNewMoved = $false
try {
    Move-Item -LiteralPath $TaskOld -Destination $TaskRetired
    $TaskOldMoved = $true
    Move-Item -LiteralPath $TaskPending -Destination $TaskNew
    $TaskNewMoved = $true
    & $TaskPython -B (Join-Path $PSScriptRoot 'verify_pkg043_activation.py') after
    if ($LASTEXITCODE -ne 0) { throw 'Post-switch acceptance failed.' }
}
catch {
    if ($TaskNewMoved) { Move-Item -LiteralPath $TaskNew -Destination $TaskPending }
    if ($TaskOldMoved) { Move-Item -LiteralPath $TaskRetired -Destination $TaskOld }
    throw
}

param([switch]$Apply)

# One-time, user-authorized cleanup. Planning is read-only except for the
# manifest. No sources/settings are eligible. Execution never follows links.
$ErrorActionPreference = 'Stop'
$TaskWorkspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..\..')).Path
$TaskRuntime = Join-Path $TaskWorkspace 'runtime\deeptutor_shchem'
$TaskQa = Join-Path $TaskRuntime 'qa\workspace-cleanup-20260909'
$TaskPlanPath = Join-Path $TaskQa 'cleanup-plan.json'
$TaskResultPath = Join-Path $TaskQa 'cleanup-result.json'
$TaskScreensPath = Join-Path $TaskQa 'screenshot-duplicates.json'
$TaskTempNames = @(
    'desktop-016-fresh-extract-20260905-232221-875',
    'desktop-015-fresh-extract-final', 'desktop-014-fresh-extract',
    'desktop_release_0.1.3_verify', 'desktop_release_0.1.2_verify',
    'desktop_release_0.1.1_verify', 'desktop-facade-real-render-smoke-20260905'
)
$TaskEmptyNames = @('({count', '({i', '({idx', '({name', '({src', '({title', '({w', '(a.textContent',
    "{if(a.textContent.includes('登录'))a.click()", "a.textContent.includes('登录'))", "r.name.includes('wx_search')")
$TaskScreenshotFolders = @('word-led-20260909-r1', 'word-led-20260909-r2',
    'word-studied-r6-office-20260909', 'word-studied-r7-office-20260909',
    'two-period-ionization-r1', 'two-period-ionization-r2', 'two-period-ionization-r3',
    'two-period-ionization-r4', 'office-layout-spacing-20260909-r1')

function Get-CheckedPath([string]$Candidate) {
    $resolved = (Resolve-Path -LiteralPath $Candidate).Path
    if (-not $resolved.StartsWith($TaskWorkspace + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Cleanup target escaped the workspace.'
    }
    $probe = Get-Item -LiteralPath $resolved -Force
    while ($probe.FullName -ne $TaskWorkspace) {
        if ($probe.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked path refused.' }
        $probe = Get-Item -LiteralPath (Split-Path -Parent $probe.FullName) -Force
    }
    return $resolved
}

function Test-Eligible([string]$Path, [string]$Kind) {
    $parent = Split-Path -Parent $Path
    $name = Split-Path -Leaf $Path
    switch ($Kind) {
        'historical_build' {
            if ($parent -ne $TaskRuntime) { return $false }
            if ($name -in @('desktop_build','desktop_dist','desktop_package','desktop_spec')) { return $true }
            if ($name -match '^desktop_(build|dist|package|spec)_0\.1\.(\d+)(?:-|$)') {
                if ([int]$Matches[2] -lt 41) { return $true }
            }
            return $name -in @('desktop_build_0.1.42-20260909-classroom-v21-r1',
                'desktop_dist_0.1.42-20260909-classroom-v21-r1', 'desktop_spec_0.1.42-20260909-classroom-v21-r1')
        }
        'historical_archive' {
            if ($parent -ne (Join-Path $TaskRuntime 'releases')) { return $false }
            return $name -match '^ShanghaiChem-Desktop-0\.1\.(\d+)-[\w.-]+\.(zip|sha256)$' -and [int]$Matches[1] -lt 41
        }
        'old_extract' { return $parent -eq (Join-Path $TaskWorkspace 'tmp') -and $name -in $TaskTempNames }
        'empty_shell_residue' { return $parent -eq $TaskWorkspace -and $name -in $TaskEmptyNames }
        'identical_screenshot' {
            if ($name -notmatch '^(slide|page)-\d+\.png$') { return $false }
            foreach ($folder in $TaskScreenshotFolders) {
                if ($Path.StartsWith((Join-Path $TaskRuntime ('qa\' + $folder)) + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
            }
            return $false
        }
    }
    return $false
}

function Get-Stats([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        $all = @(Get-ChildItem -LiteralPath $Path -Recurse -Force)
        if ($all | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) { throw 'Linked descendant refused.' }
        $files = @($all | Where-Object { -not $_.PSIsContainer })
    } else { $files = @($item) }
    return [ordered]@{ files=$files.Count; bytes=[long](($files | Measure-Object -Property Length -Sum).Sum) }
}

function Save-Json($Value, [string]$Path) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 9), [Text.UTF8Encoding]::new($false))
}

if (-not $Apply) {
    if (Test-Path -LiteralPath $TaskPlanPath) { throw 'Plan already exists; review it instead of replacing it.' }
    New-Item -ItemType Directory -Path $TaskQa -Force | Out-Null
    $targets = [Collections.Generic.List[object]]::new()
    $candidates = @()
    $candidates += @(Get-ChildItem -LiteralPath $TaskRuntime -Directory | ForEach-Object { [pscustomobject]@{path=$_.FullName; kind='historical_build'} })
    $candidates += @(Get-ChildItem -LiteralPath (Join-Path $TaskRuntime 'releases') -File | ForEach-Object { [pscustomobject]@{path=$_.FullName; kind='historical_archive'} })
    foreach ($name in $TaskTempNames) { $candidates += [pscustomobject]@{path=(Join-Path $TaskWorkspace ('tmp\' + $name)); kind='old_extract'} }
    foreach ($name in $TaskEmptyNames) { $candidates += [pscustomobject]@{path=(Join-Path $TaskWorkspace $name); kind='empty_shell_residue'} }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate.path) -or -not (Test-Eligible $candidate.path $candidate.kind)) { continue }
        $path = Get-CheckedPath $candidate.path
        $stats = Get-Stats $path
        if ($candidate.kind -eq 'empty_shell_residue' -and ($stats.bytes -ne 0 -or $stats.files -ne 1)) { throw 'Nonempty residue refused.' }
        $targets.Add([pscustomobject]@{path=$path; kind=$candidate.kind; files=$stats.files; bytes=$stats.bytes})
    }
    $duplicates = Get-Content -Raw -LiteralPath $TaskScreensPath | ConvertFrom-Json
    foreach ($row in $duplicates.candidates) {
        $path = Get-CheckedPath $row.absolute_path
        $keep = Get-CheckedPath $row.keep_path
        if (-not (Test-Eligible $path 'identical_screenshot') -or (Test-Eligible $keep 'identical_screenshot')) { throw 'Invalid screenshot mapping.' }
        if ((Get-FileHash -LiteralPath $path).Hash -ne $row.sha256 -or (Get-FileHash -LiteralPath $keep).Hash -ne $row.sha256) { throw 'Screenshot hash mismatch.' }
        $targets.Add([pscustomobject]@{path=$path; kind='identical_screenshot'; files=1; bytes=$row.bytes; sha256=$row.sha256; keep_path=$keep})
    }
    $protected = @('sh-chem-db\catalog.csv',
        '课本\沪科技化学必修第一册【高清教材】.pdf',
        'sh-chem-db\.intake\2026-07-30-user-teaching-pack\expanded\PKG-032\第04讲 离子反应和离子方程式（复习讲义）（上海专用）（解析版）.docx',
        'outputs\备课\2026-09-09-电解质的电离-课堂投影版-r11\课堂版\lesson_presentation.pptx',
        'runtime\deeptutor_shchem\releases\ShanghaiChem-Desktop-0.1.42-win64.zip',
        'runtime\deeptutor_shchem\releases\ShanghaiChem-Desktop-0.1.41-win64.zip',
        'runtime\deeptutor_shchem\desktop_package_0.1.42\沪上化学智研台\沪上化学智研台.exe',
        'runtime\deeptutor_shchem\desktop_package_0.1.41\沪上化学智研台\沪上化学智研台.exe')
    $protected += @(Get-ChildItem -LiteralPath (Join-Path $TaskWorkspace 'outputs\备课\2026-09-09-电解质的电离-讲义精读生成材料\assets') -File | ForEach-Object { $_.FullName.Substring($TaskWorkspace.Length + 1) })
    $fingerprints = @($protected | ForEach-Object {
        $path = Get-CheckedPath (Join-Path $TaskWorkspace $_)
        [ordered]@{path=$path; sha256=(Get-FileHash -LiteralPath $path).Hash}
    })
    $plan = [ordered]@{created_at=(Get-Date -Format o); workspace=$TaskWorkspace; protected=$fingerprints; targets=@($targets.ToArray());
        total_files=[long](($targets | Measure-Object files -Sum).Sum); total_bytes=[long](($targets | Measure-Object bytes -Sum).Sum);
        policy='Keep source material, source code, personal settings, current 0.1.42 plus 0.1.41 rollback and all unique screenshots. Duplicate screenshot bytes remain at keep_path.'}
    Save-Json $plan $TaskPlanPath
    $targets | Group-Object kind | ForEach-Object { [pscustomobject]@{kind=$_.Name; targets=$_.Count; bytes=[long](($_.Group | Measure-Object bytes -Sum).Sum)} } | ConvertTo-Json
    exit 0
}

if (Test-Path -LiteralPath $TaskResultPath) { throw 'Execution record already exists; no repeat deletion.' }
$plan = Get-Content -Raw -LiteralPath $TaskPlanPath | ConvertFrom-Json
if ($plan.workspace -ne $TaskWorkspace) { throw 'Wrong workspace in plan.' }
if (($plan.targets | Measure-Object bytes -Sum).Sum -ne $plan.total_bytes -or ($plan.targets | Measure-Object files -Sum).Sum -ne $plan.total_files) { throw 'Plan totals do not match targets.' }
$activePaths = @(Get-Process | Where-Object Path | Select-Object -ExpandProperty Path)
# Validate every final absolute path, byte count and protected fingerprint
# before the first destructive action. Abort the whole batch on drift.
foreach ($row in $plan.protected) {
    if ((Get-FileHash -LiteralPath (Get-CheckedPath $row.path)).Hash -ne $row.sha256) { throw 'Protected artifact changed.' }
}
foreach ($row in $plan.targets) {
    $path = Get-CheckedPath $row.path
    if (-not (Test-Eligible $path $row.kind)) { throw 'Ineligible target.' }
    foreach ($active in $activePaths) { if ($active -eq $path -or $active.StartsWith($path + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'A target executable is active; stop for user direction.' } }
    $stats = Get-Stats $path
    if ($stats.files -ne $row.files -or $stats.bytes -ne $row.bytes) { throw 'Target changed since planning.' }
    if ($row.kind -eq 'identical_screenshot') {
        $keep = Get-CheckedPath $row.keep_path
        if ((Get-FileHash -LiteralPath $path).Hash -ne $row.sha256 -or (Get-FileHash -LiteralPath $keep).Hash -ne $row.sha256) { throw 'Screenshot changed.' }
        foreach ($other in $plan.targets) { if ($keep -eq $other.path -or $keep.StartsWith($other.path + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Retained screenshot would be removed.' } }
    }
}
$result = [ordered]@{started_at=(Get-Date -Format o); plan_sha256=(Get-FileHash -LiteralPath $TaskPlanPath).Hash; completed=$false; deleted=@(); protected_unchanged=$false}
Save-Json $result $TaskResultPath
try {
    foreach ($row in $plan.targets) {
        $path = Get-CheckedPath $row.path
        Remove-Item -LiteralPath $path -Recurse -Force
        $result.deleted += $row
        Save-Json $result $TaskResultPath
    }
    foreach ($row in $plan.protected) {
        if ((Get-FileHash -LiteralPath $row.path).Hash -ne $row.sha256) { throw 'Post-cleanup protected hash mismatch.' }
    }
    $result.protected_unchanged = $true
    $result.completed = $true
} finally {
    $result['finished_at'] = Get-Date -Format o
    $result['deleted_bytes'] = [long](($result.deleted | Measure-Object bytes -Sum).Sum)
    $result['deleted_files'] = [long](($result.deleted | Measure-Object files -Sum).Sum)
    Save-Json $result $TaskResultPath
}
[pscustomobject]@{completed=$result.completed; deleted_files=$result.deleted_files; deleted_bytes=$result.deleted_bytes; protected_unchanged=$result.protected_unchanged; result=$TaskResultPath} | ConvertTo-Json

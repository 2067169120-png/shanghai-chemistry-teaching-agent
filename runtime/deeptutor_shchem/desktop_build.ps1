param(
    [string]$Python = "python",
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$BuildTag,
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$RuntimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = (Resolve-Path (Join-Path $RuntimeRoot "..\..")).Path
$EntryPoint = Join-Path $RuntimeRoot "desktop_teacher_workbench.pyw"
$DistRoot = Join-Path $RuntimeRoot "desktop_dist"
$WorkRoot = Join-Path $RuntimeRoot "desktop_build"
$SpecRoot = Join-Path $RuntimeRoot "desktop_spec"
if ($BuildTag) {
    # A named build keeps previously delivered binaries and build evidence intact.
    $DistRoot = Join-Path $RuntimeRoot "desktop_dist_$BuildTag"
    $WorkRoot = Join-Path $RuntimeRoot "desktop_build_$BuildTag"
    $SpecRoot = Join-Path $RuntimeRoot "desktop_spec_$BuildTag"
}

# Rebuilds must use a new tag; do not remove an installed trial or shared cache.
foreach ($BuildOutputPath in @($DistRoot, $WorkRoot, $SpecRoot)) {
    if (Test-Path -LiteralPath $BuildOutputPath) {
        throw "Build output already exists. Choose a fresh -BuildTag; existing files were not changed."
    }
}

$OriginalSearchPath = $env:Path
$BuildSearchPathEntries = @(
    $OriginalSearchPath -split [IO.Path]::PathSeparator |
        Where-Object {
            $entry = $_.Trim()
            $entry -and $entry -notmatch '(?i)[\\/]\.cache[\\/]codex-runtimes[\\/].*[\\/]dependencies[\\/]native([\\/]|$)'
        }
)

try {
    # Codex's bundled Poppler/libheif directories contain private ICU DLLs.
    # Leaving them on PATH lets PyInstaller mistake those DLLs for Qt's
    # Windows-system ICU dependency, producing a package that fails in QtGui.
    $env:Path = [string]::Join([IO.Path]::PathSeparator, $BuildSearchPathEntries)

    & $Python -c "import PySide6, PyInstaller, jsonschema, PIL, docx, pptx, pypdf; from PySide6 import QtPdf, QtPdfWidgets"
    if ($LASTEXITCODE -ne 0) {
        throw "桌面构建依赖不可用。请先在项目专用环境中安装 desktop_requirements.txt。"
    }

    $Arguments = @(
        "-m", "PyInstaller",
        "--windowed",
        "--noupx",
        "--name", "沪上化学智研台",
        "--paths", $WorkspaceRoot,
        "--distpath", $DistRoot,
        "--workpath", $WorkRoot,
        "--specpath", $SpecRoot,
        "--exclude-module", "integrations.deeptutor_shchem_v1.service",
        "--exclude-module", "integrations.deeptutor_shchem_v1.http_app",
        "--exclude-module", "integrations.deeptutor_shchem_v1.launcher",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets"
    )
    if ($OneFile) {
        $Arguments += "--onefile"
    }
    $Arguments += $EntryPoint

    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "桌面应用构建失败。"
    }
}
finally {
    $env:Path = $OriginalSearchPath
}

if (-not $OneFile) {
    $InternalRoot = Join-Path $DistRoot "沪上化学智研台\_internal"
    $UnexpectedIcu = @(
        Get-ChildItem -LiteralPath $InternalRoot -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "icuuc.dll" -or $_.Name -like "icudt*.dll" }
    )
    if ($UnexpectedIcu.Count -gt 0) {
        throw "桌面包误收集了外部 ICU DLL，请检查构建进程 PATH。"
    }
}

Write-Host "构建完成：$DistRoot"

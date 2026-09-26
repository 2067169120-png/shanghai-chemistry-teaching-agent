param(
    [Parameter(Mandatory=$true)][string]$PptxFile,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$taskPptx = (Resolve-Path -LiteralPath $PptxFile).Path
$taskOutput = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $taskOutput) { throw 'Output already exists.' }
if (Get-Process -Name POWERPNT -ErrorAction SilentlyContinue) { throw 'Existing PowerPoint process left untouched.' }
$taskBefore = (Get-FileHash -LiteralPath $taskPptx -Algorithm SHA256).Hash
New-Item -ItemType Directory -Path $taskOutput | Out-Null
$taskPowerPoint = $null
$taskPresentation = $null
try {
    $taskPowerPoint = New-Object -ComObject PowerPoint.Application
    $taskPowerPoint.AutomationSecurity = 3
    $taskPresentation = $taskPowerPoint.Presentations.Open($taskPptx, -1, 0, 0)
    $taskSlideCount = $taskPresentation.Slides.Count
    for ($taskIndex = 1; $taskIndex -le $taskSlideCount; $taskIndex++) {
        $taskPresentation.Slides.Item($taskIndex).Export((Join-Path $taskOutput ('slide-{0:D2}.png' -f $taskIndex)), 'PNG', 1600, 900)
    }
} finally {
    if ($null -ne $taskPresentation) { $taskPresentation.Close() }
    if ($null -ne $taskPowerPoint) { $taskPowerPoint.Quit() }
}
if ((Get-FileHash -LiteralPath $taskPptx -Algorithm SHA256).Hash -ne $taskBefore) { throw 'Read-only source changed.' }
[pscustomobject]@{
    powerpoint_slides=$taskSlideCount
    source_artifacts_unchanged=$true
    output_directory=$taskOutput
    visual_inspection_pending=$true
} | ConvertTo-Json

param(
    [Parameter(Mandatory=$true)][string]$PptxPath,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$taskSource = (Resolve-Path -LiteralPath $PptxPath).Path
$taskOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $taskOutput) { throw 'Use a new QA output directory.' }
if (Get-Process -Name POWERPNT -ErrorAction SilentlyContinue) {
    throw 'Existing PowerPoint process detected; leave user application untouched.'
}
$taskBefore = (Get-FileHash -LiteralPath $taskSource).Hash
New-Item -ItemType Directory -Path $taskOutput | Out-Null
$taskApp = $null
$taskDeck = $null
try {
    $taskApp = New-Object -ComObject PowerPoint.Application
    $taskApp.AutomationSecurity = 3
    $taskDeck = $taskApp.Presentations.Open($taskSource, -1, 0, 0)
    $taskSlideCount = $taskDeck.Slides.Count
    for ($taskIndex = 1; $taskIndex -le $taskSlideCount; $taskIndex++) {
        $taskDeck.Slides.Item($taskIndex).Export(
            (Join-Path $taskOutput ('slide-{0:D2}.png' -f $taskIndex)), 'PNG', 1600, 900
        )
    }
    $taskDeck.SaveAs((Join-Path $taskOutput 'powerpoint.pdf'), 32)
} finally {
    if ($null -ne $taskDeck) { $taskDeck.Close() }
    if ($null -ne $taskApp) { $taskApp.Quit() }
}
if ($taskBefore -ne (Get-FileHash -LiteralPath $taskSource).Hash) {
    throw 'Read-only source hash changed.'
}
[pscustomobject]@{
    powerpoint_slides=$taskSlideCount
    source_artifact_unchanged=$true
    output_directory=$taskOutput
    visual_inspection_pending=$true
} | ConvertTo-Json

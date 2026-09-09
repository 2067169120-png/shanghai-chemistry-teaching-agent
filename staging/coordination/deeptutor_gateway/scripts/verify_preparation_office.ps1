param(
    [Parameter(Mandatory=$true)][string]$ArtifactDirectory,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$taskSource = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
$taskOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $taskOutput) { throw 'QA output already exists; use a new directory.' }
if (Get-Process -Name WINWORD,POWERPNT -ErrorAction SilentlyContinue) {
    throw 'Existing Office process detected; leave user applications untouched.'
}
$taskDocx = Join-Path $taskSource 'lesson_plan.docx'
$taskPptx = Join-Path $taskSource 'lesson_presentation.pptx'
$taskWorksheet = Join-Path $taskSource 'student_worksheet.docx'
foreach ($taskFile in @($taskDocx, $taskPptx)) {
    if (-not (Test-Path -LiteralPath $taskFile -PathType Leaf)) { throw 'Missing preparation artifact.' }
}
$taskInputs = @($taskDocx, $taskPptx)
if (Test-Path -LiteralPath $taskWorksheet -PathType Leaf) { $taskInputs += $taskWorksheet }
$taskBefore = @($taskInputs | ForEach-Object { (Get-FileHash -LiteralPath $_).Hash })
New-Item -ItemType Directory -Path $taskOutput | Out-Null
$taskWord = $null
$taskDocument = $null
$taskWorksheetPages = $null
try {
    $taskWord = New-Object -ComObject Word.Application
    $taskWord.Visible = $false
    $taskWord.DisplayAlerts = 0
    $taskWord.AutomationSecurity = 3
    $taskDocument = $taskWord.Documents.Open($taskDocx, $false, $true, $false)
    $taskDocument.ExportAsFixedFormat((Join-Path $taskOutput 'word.pdf'), 17)
    $taskWordPages = $taskDocument.ComputeStatistics(2)
    $taskDocument.Close(0)
    $taskDocument = $null
    if (Test-Path -LiteralPath $taskWorksheet -PathType Leaf) {
        $taskDocument = $taskWord.Documents.Open($taskWorksheet, $false, $true, $false)
        $taskDocument.ExportAsFixedFormat((Join-Path $taskOutput 'worksheet.pdf'), 17)
        $taskWorksheetPages = $taskDocument.ComputeStatistics(2)
    }
} finally {
    if ($null -ne $taskDocument) { $taskDocument.Close(0) }
    if ($null -ne $taskWord) { $taskWord.Quit() }
}
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
    $taskPresentation.SaveAs((Join-Path $taskOutput 'powerpoint.pdf'), 32)
} finally {
    if ($null -ne $taskPresentation) { $taskPresentation.Close() }
    if ($null -ne $taskPowerPoint) { $taskPowerPoint.Quit() }
}
$taskAfter = @($taskInputs | ForEach-Object { (Get-FileHash -LiteralPath $_).Hash })
if (($taskBefore -join ',') -ne ($taskAfter -join ',')) { throw 'Read-only source hash changed.' }
[pscustomobject]@{
    word_pages=$taskWordPages
    worksheet_pages=$taskWorksheetPages
    powerpoint_slides=$taskSlideCount
    source_artifacts_unchanged=$true
    output_directory=$taskOutput
    visual_inspection_pending=$true
} | ConvertTo-Json

param(
    [Parameter(Mandatory=$true)][string]$PptxFile,
    [Parameter(Mandatory=$true)][string]$ReportFile
)
$ErrorActionPreference = 'Stop'
$taskPptx = (Resolve-Path -LiteralPath $PptxFile).Path
if (Test-Path -LiteralPath $ReportFile) { throw 'Report already exists' }
if (Get-Process -Name POWERPNT -ErrorAction SilentlyContinue) { throw 'Existing PowerPoint left untouched' }
$taskHash = (Get-FileHash -LiteralPath $taskPptx -Algorithm SHA256).Hash
$taskOffice = $null
$taskDeck = $null
$taskRows = @()
try {
    $taskOffice = New-Object -ComObject PowerPoint.Application
    $taskOffice.AutomationSecurity = 3
    $taskDeck = $taskOffice.Presentations.Open($taskPptx, -1, 0, 0)
    foreach ($taskSlide in $taskDeck.Slides) {
        foreach ($taskShape in $taskSlide.Shapes) {
            if ($taskShape.HasTextFrame -ne -1 -or $taskShape.TextFrame.HasText -ne -1) { continue }
            $taskText = $taskShape.TextFrame2.TextRange
            $taskRows += [pscustomobject]@{
                slide = $taskSlide.SlideIndex
                shape = $taskShape.Id
                text = $taskText.Text
                font = $taskText.Font.Name
                font_size = $taskText.Font.Size
                left = $taskShape.Left
                top = $taskShape.Top
                width = $taskShape.Width
                height = $taskShape.Height
                bound_left = $taskText.BoundLeft
                bound_top = $taskText.BoundTop
                bound_width = $taskText.BoundWidth
                bound_height = $taskText.BoundHeight
            }
        }
    }
} finally {
    if ($null -ne $taskDeck) { $taskDeck.Close() }
    if ($null -ne $taskOffice) { $taskOffice.Quit() }
}
if ((Get-FileHash -LiteralPath $taskPptx -Algorithm SHA256).Hash -ne $taskHash) { throw 'Source changed' }
$taskReport = [ordered]@{ source=$taskPptx; sha256=$taskHash; unchanged=$true; measurements=$taskRows }
[IO.File]::WriteAllText([IO.Path]::GetFullPath($ReportFile), ($taskReport | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
$taskOver = @($taskRows | Where-Object { $_.bound_width -gt $_.width + 1 -or $_.bound_height -gt $_.height + 1 })
[pscustomobject]@{ text_boxes=$taskRows.Count; overflow_boxes=$taskOver.Count; report=$ReportFile } | ConvertTo-Json

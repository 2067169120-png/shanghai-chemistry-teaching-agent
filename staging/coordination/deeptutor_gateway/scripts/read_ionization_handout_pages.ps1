param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$taskSource = (Resolve-Path -LiteralPath 'sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-032/第04讲 离子反应和离子方程式（复习讲义）（上海专用）（解析版）.docx').Path
$taskOutput = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $taskOutput) { throw 'Use a new source-study directory.' }
if (Get-Process -Name WINWORD -ErrorAction SilentlyContinue) { throw 'User Word process exists; leave it untouched.' }
$taskHash = (Get-FileHash -LiteralPath $taskSource).Hash
New-Item -ItemType Directory -Path $taskOutput | Out-Null
$taskWord = $null
$taskDocument = $null
try {
    $taskWord = New-Object -ComObject Word.Application
    $taskWord.Visible = $false
    $taskWord.DisplayAlerts = 0
    $taskWord.AutomationSecurity = 3
    $taskDocument = $taskWord.Documents.Open($taskSource,$false,$true,$false)
    $taskPages = $taskDocument.ComputeStatistics(2)
    $taskDocument.ExportAsFixedFormat((Join-Path $taskOutput 'source.pdf'),17)
} finally {
    if ($null -ne $taskDocument) { $taskDocument.Close(0) }
    if ($null -ne $taskWord) { $taskWord.Quit() }
}
if ((Get-FileHash -LiteralPath $taskSource).Hash -ne $taskHash) { throw 'Source changed.' }
[pscustomobject]@{source=$taskSource;sha256=$taskHash;pages=$taskPages;read_only=$true;source_unchanged=$true;pdf=(Join-Path $taskOutput 'source.pdf')} | ConvertTo-Json

param(
    [Parameter(Mandatory=$true)][string]$ArtifactDirectory,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$PopplerPath,
    [switch]$UseBodyRange
)
$ErrorActionPreference = 'Stop'
$taskSource = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
$taskOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $taskOutput) { throw 'Use a new QA output directory.' }
if (Get-Process -Name WINWORD -ErrorAction SilentlyContinue) {
    throw 'Existing Word process detected; leave user documents untouched.'
}
if (-not (Test-Path -LiteralPath $PopplerPath -PathType Leaf)) { throw 'Bundled Poppler missing.' }
$taskInputs = @('lesson_plan.docx', 'student_worksheet.docx')
foreach ($taskName in $taskInputs) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskSource $taskName) -PathType Leaf)) {
        throw 'Missing preparation document.'
    }
}
New-Item -ItemType Directory -Path $taskOutput | Out-Null
$taskWord = $null
$taskDocument = $null
$taskResults = @()
try {
    $taskWord = New-Object -ComObject Word.Application
    $taskWord.Visible = $false
    $taskWord.DisplayAlerts = 0
    $taskWord.AutomationSecurity = 3
    foreach ($taskName in $taskInputs) {
        $taskInput = Join-Path $taskSource $taskName
        $taskHash = (Get-FileHash -LiteralPath $taskInput).Hash
        $taskStem = [System.IO.Path]::GetFileNameWithoutExtension($taskName)
        $taskPdf = Join-Path $taskOutput ($taskStem + '.pdf')
        $taskDocument = $taskWord.Documents.Open($taskInput, $false, $true, $false)
        if ($UseBodyRange) {
            if (-not $taskDocument.ReadOnly -or $taskDocument.ProtectionType -ne -1) {
                throw 'Body-range QA requires the unprotected read-only generated document.'
            }
            foreach ($taskSection in $taskDocument.Sections) {
                foreach ($taskCollection in @($taskSection.Headers, $taskSection.Footers)) {
                    foreach ($taskStory in $taskCollection) {
                        if ($taskStory.Exists -and ($taskStory.Range.Text.Trim().Length -gt 0 -or $taskStory.Range.Fields.Count -gt 0 -or $taskStory.Shapes.Count -gt 0)) {
                            throw 'Headers or footers require full-document export; body-only QA would omit them.'
                        }
                    }
                }
            }
            # Explicit opt-in fallback for generated body-only documents. Keep
            # information-rights settings; do not change or save the DOCX.
            $taskDocument.Content.ExportAsFixedFormat($taskPdf, 17, $false, 0, $false, 0, $false, $true, 0, $false, $true, $false)
        } else {
            $taskDocument.ExportAsFixedFormat($taskPdf, 17)
        }
        $taskPages = $taskDocument.ComputeStatistics(2)
        $taskDocument.Close(0)
        $taskDocument = $null
        if ($taskHash -ne (Get-FileHash -LiteralPath $taskInput).Hash) { throw 'Source document changed.' }
        & $PopplerPath -r 120 -png $taskPdf (Join-Path $taskOutput $taskStem)
        if ($LASTEXITCODE -ne 0) { throw 'Poppler PNG export failed.' }
        $taskResults += [pscustomobject]@{
            document=$taskName; pages=$taskPages; source_sha256=$taskHash;
            source_unchanged=$true; visual_inspection_pending=$true;
            body_range_export=[bool]$UseBodyRange
        }
    }
} finally {
    if ($null -ne $taskDocument) { $taskDocument.Close(0) }
    if ($null -ne $taskWord) { $taskWord.Quit() }
}
$taskResults | ConvertTo-Json

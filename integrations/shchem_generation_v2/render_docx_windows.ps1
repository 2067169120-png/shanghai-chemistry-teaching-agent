param(
    [Parameter(Mandatory = $true)][string]$InputDocx,
    [Parameter(Mandatory = $true)][string]$OutputPdf
)

$ErrorActionPreference = "Stop"

$resolvedInput = (Resolve-Path -LiteralPath $InputDocx).Path
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPdf)
$outputParent = [System.IO.Path]::GetDirectoryName($resolvedOutput)
if (-not [System.IO.Directory]::Exists($outputParent)) {
    [System.IO.Directory]::CreateDirectory($outputParent) | Out-Null
}

$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($resolvedInput, $false, $true)
    $document.Repaginate()
    $document.ExportAsFixedFormat($resolvedOutput, 17, $false, 0, 0, 1, 1, 0, $true, $true, 1, $true, $true, $false)
}
finally {
    if ($null -ne $document) {
        $document.Close($false)
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
    }
    if ($null -ne $word) {
        $word.Quit()
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

if (-not (Test-Path -LiteralPath $resolvedOutput)) {
    throw "Word PDF export did not create $resolvedOutput"
}

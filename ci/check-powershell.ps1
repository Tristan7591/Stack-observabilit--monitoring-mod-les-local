$ErrorActionPreference = 'Stop'
$scripts = @(git ls-files -- '*.ps1')
if ($LASTEXITCODE -ne 0 -or $scripts.Count -eq 0) { throw 'No tracked PowerShell scripts found.' }

foreach ($file in $scripts) {
    $tokens = $null
    $parseErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$parseErrors)
    foreach ($parseError in $parseErrors) {
        Write-Host "Invalid PowerShell in $file at line $($parseError.Extent.StartLineNumber)"
    }
    if ($parseErrors.Count) { throw 'PowerShell parsing failed.' }
}
Write-Host "Parsed $($scripts.Count) PowerShell scripts."

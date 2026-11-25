[CmdletBinding()]
param(
    [string]$ConfigPath,
    [uri]$OtlpEndpoint = 'http://127.0.0.1:4318',
    [switch]$Disable
)

$ErrorActionPreference = 'Stop'

if (!$Disable -and (!$OtlpEndpoint.IsAbsoluteUri -or $OtlpEndpoint.Scheme -ne 'http' -or
    $OtlpEndpoint.Host -ne '127.0.0.1' -or $OtlpEndpoint.UserInfo -or
    $OtlpEndpoint.AbsolutePath -ne '/' -or $OtlpEndpoint.Query -or $OtlpEndpoint.Fragment)) {
    throw 'Use a local base URL of the form http://127.0.0.1:4318.'
}
if (!$ConfigPath) {
    $codexDirectory = $env:CODEX_HOME
    if (!$codexDirectory) { $codexDirectory = Join-Path $env:USERPROFILE '.codex' }
    $ConfigPath = Join-Path $codexDirectory 'config.toml'
}
$ConfigPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ConfigPath)

$encoding = New-Object System.Text.UTF8Encoding($false, $true)
$exists = [System.IO.File]::Exists($ConfigPath)
$original = if ($exists) { [System.IO.File]::ReadAllText($ConfigPath, $encoding) } else { '' }
$newLine = if ($original.Contains("`r`n")) { "`r`n" } else { "`n" }
$begin = '# BEGIN codex-observability managed OTEL'
$end = '# END codex-observability managed OTEL'
$managedPattern = '(?ms)^' + [regex]::Escape($begin) + '\r?\n.*?^' + [regex]::Escape($end) + '(?:\r?\n|\z)'
$managed = [regex]::Matches($original, $managedPattern)
if ($managed.Count -gt 1 -or ($managed.Count -eq 0 -and ($original.Contains($begin) -or $original.Contains($end)))) {
    throw "Unexpected managed markers in $ConfigPath. Check the existing OpenTelemetry section."
}

if ($Disable) {
    if ($managed.Count -eq 0) {
        Write-Host "No managed OpenTelemetry settings in $ConfigPath."
        return
    }
    $updated = $original.Remove($managed[0].Index, $managed[0].Length)
} else {
    $outside = [regex]::Replace($original, $managedPattern, '')
    if ($outside -match '(?m)^\s*(?:\[{1,2}\s*["'']?otel["'']?\s*[.\]]|["'']?otel["'']?\s*(?:\.|=))') {
        throw "Existing unmanaged OTEL configuration in $ConfigPath. Merge the endpoints from README.md into that configuration."
    }
    $endpoint = $OtlpEndpoint.GetLeftPart([System.UriPartial]::Authority)
    $block = @(
        $begin
        '[otel]'
        'environment = "local"'
        'log_user_prompt = false'
        'log_agent_responses = false'
        'log_guardian_assessments = false'
        'log_sse_deltas = false'
        ('exporter = { otlp-http = { endpoint = "' + $endpoint + '/v1/logs", protocol = "binary" } }')
        ('trace_exporter = { otlp-http = { endpoint = "' + $endpoint + '/v1/traces", protocol = "binary" } }')
        ('metrics_exporter = { otlp-http = { endpoint = "' + $endpoint + '/v1/metrics", protocol = "binary" } }')
        ''
        '[otel.tool_result]'
        'max_bytes = 0'
        $end
        ''
    ) -join $newLine
    if ($managed.Count -eq 1) {
        $updated = $original.Remove($managed[0].Index, $managed[0].Length).Insert($managed[0].Index, $block)
    } else {
        $separator = if ($original.Length -gt 0 -and $original.EndsWith("`n")) { $newLine } elseif ($original.Length -gt 0) { $newLine + $newLine } else { '' }
        $updated = $original + $separator + $block
    }
}

if ($original -ceq $updated) {
    Write-Host "OpenTelemetry settings already active in $ConfigPath."
    return
}

$null = [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($ConfigPath))
$id = [guid]::NewGuid().ToString('N')
$stagingPath = "$ConfigPath.codex-observability.$id.tmp"
try {
    [System.IO.File]::WriteAllText($stagingPath, $updated, $encoding)
    if ($exists) {
        if ([System.IO.File]::ReadAllText($ConfigPath, $encoding) -cne $original) {
            throw 'Codex configuration changed during installation. Run this script again.'
        }
        $backupPath = "$ConfigPath.codex-observability.$id.bak"
        [System.IO.File]::Replace($stagingPath, $ConfigPath, $backupPath)
        Write-Host "Previous configuration saved to $backupPath."
    } else {
        [System.IO.File]::Move($stagingPath, $ConfigPath)
    }
} finally {
    if ([System.IO.File]::Exists($stagingPath)) { [System.IO.File]::Delete($stagingPath) }
}
Write-Host "OpenTelemetry settings updated in $ConfigPath. Restart Codex to reload the settings."

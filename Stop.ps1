[CmdletBinding()]
param([switch]$RemoveContainers)

$ErrorActionPreference = 'Stop'
$runtimePath = $env:CODEX_OBSERVABILITY_RUNTIME_DIR
if (!$runtimePath) { $runtimePath = Join-Path $env:LOCALAPPDATA 'CodexObservability' }
$composeArguments = @('compose', '--project-directory', $PSScriptRoot, '--file', (Join-Path $PSScriptRoot 'compose.yaml'))
$statePath = Join-Path $runtimePath 'host-collector.json'
$metricsPath = Join-Path $runtimePath 'metrics\host.prom'

function Stop-HostMetrics {
    if (!(Test-Path -LiteralPath $statePath -PathType Leaf)) { return }

    $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $collector = Get-Process -Id ([int]$saved.processId) -ErrorAction SilentlyContinue
    if ($collector -and $collector.StartTime.ToUniversalTime().Ticks.ToString() -eq $saved.startedUtcTicks) {
        try {
            Stop-Process -InputObject $collector
            if (!$collector.WaitForExit(10000)) { throw "The Windows collector PID $($collector.Id) is still running." }
        } catch {
            if (!$collector.HasExited) { throw }
        }
        Write-Host "Windows collector stopped (PID $($collector.Id))."
    }

    Remove-Item -LiteralPath $statePath
    if (Test-Path -LiteralPath $metricsPath -PathType Leaf) {
        Remove-Item -LiteralPath $metricsPath
    }
}

[System.IO.Directory]::CreateDirectory($runtimePath) | Out-Null
try {
    $lock = [System.IO.File]::Open((Join-Path $runtimePath 'lifecycle.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    throw "Could not acquire the lifecycle lock in $runtimePath. $($_.Exception.Message)"
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $docker = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (!$docker) { throw 'Docker CLI is unavailable. Could not stop the Compose project.' }

    $engineAddress = $env:DOCKER_HOST
    if (!$engineAddress -or $env:DOCKER_CONTEXT) {
        $engineAddress = & $docker.Source context inspect --format '{{.Endpoints.docker.Host}}'
        if ($LASTEXITCODE -ne 0) { throw 'Could not read the Docker context.' }
    }
    if ($engineAddress -notmatch '^(npipe|unix)://') {
        throw "Select the local Docker Desktop context used to install the stack before running Stop.ps1."
    }

    $composeAction = if ($RemoveContainers) { 'down' } else { 'stop' }
    & $docker.Source @composeArguments $composeAction
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose $composeAction failed." }
} finally {
    try {
        Stop-HostMetrics
    } finally {
        Pop-Location
        $lock.Dispose()
    }
}

[CmdletBinding()]
param(
    [switch]$SkipCodexTelemetry,
    [switch]$SkipHostCollector,
    [ValidateRange(1, 300)][int]$CollectorIntervalSeconds = 2
)

$ErrorActionPreference = 'Stop'
$runtimePath = $env:CODEX_OBSERVABILITY_RUNTIME_DIR
if (!$runtimePath) { $runtimePath = Join-Path $env:LOCALAPPDATA 'CodexObservability' }
$metricsDirectory = Join-Path $runtimePath 'metrics'
$composeArguments = @('compose', '--project-directory', $PSScriptRoot, '--file', (Join-Path $PSScriptRoot 'compose.yaml'))
$collectorPath = Join-Path $PSScriptRoot 'scripts\Start-HostCollector.ps1'
$telemetryPath = Join-Path $PSScriptRoot 'Enable-CodexTelemetry.ps1'
$statePath = Join-Path $runtimePath 'host-collector.json'
$metricsPath = Join-Path $metricsDirectory 'host.prom'
$grafanaCredentialPath = Join-Path $runtimePath 'grafana-admin-password.txt'
$collectorErrors = Join-Path $runtimePath 'host-collector.stderr.log'
$collectorOutput = Join-Path $runtimePath 'host-collector.stdout.log'
$grafanaUrl = 'http://127.0.0.1:3000'
$otlpUrl = 'http://127.0.0.1:4318'

function Get-DockerEngineType {
    $ErrorActionPreference = 'Continue'
    $engineType = & $docker.Source info --format '{{.OSType}}' 2>$null
    if ($LASTEXITCODE -eq 0) {
        return [string]$engineType
    }
}

function Start-LocalDocker {
    $ErrorActionPreference = 'Continue'
    & $docker.Source desktop start --detach 2>$null | Out-Host
    if ($LASTEXITCODE -eq 0) { return }

    $desktopDirectory = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $docker.Source))
    $desktopExe = Join-Path $desktopDirectory 'Docker Desktop.exe'
    if (Test-Path -LiteralPath $desktopExe -PathType Leaf) {
        Start-Process -FilePath $desktopExe -WindowStyle Hidden -ErrorAction Stop
    }
}

function Wait-LocalEndpoint {
    param([string]$Name, [string]$Uri, [switch]$SendOtlp)

    $deadline = (Get-Date).AddSeconds(45)
    do {
        try {
            $request = @{ Uri = $Uri; UseBasicParsing = $true; TimeoutSec = 3 }
            if ($SendOtlp) {
                $request.Method = 'Post'
                $request.ContentType = 'application/json'
                $request.Body = '{}'
            }
            Invoke-WebRequest @request | Out-Null
            return
        } catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Seconds 1
        }
    } while ((Get-Date) -lt $deadline)

    throw "$Name unavailable at $Uri. $lastError"
}

function Initialize-GrafanaCredential {
    if (Test-Path -LiteralPath $grafanaCredentialPath -PathType Leaf) {
        if ((Get-Item -LiteralPath $grafanaCredentialPath).Length -eq 0) {
            throw "Grafana credential file is empty at $grafanaCredentialPath. Restore its original contents."
        }
        return
    }

    $randomBytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($randomBytes)
    } finally {
        $generator.Dispose()
    }
    $password = [Convert]::ToBase64String($randomBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [System.IO.File]::WriteAllText($grafanaCredentialPath, $password, [System.Text.Encoding]::ASCII)
}

function Start-HostMetrics {
    $collector = $null
    $collectorStartedAt = $null
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        try {
            $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            $previous = Get-Process -Id ([int]$saved.processId) -ErrorAction SilentlyContinue
            if ($previous -and $previous.StartTime.ToUniversalTime().Ticks.ToString() -eq $saved.startedUtcTicks) {
                $collector = $previous
            }
        } catch {
            Write-Verbose "Could not read the previous collector state. $($_.Exception.Message)"
        }
    }

    if ($collector) {
        Write-Host "Windows collector already running (PID $($collector.Id))."
        $freshnessSeconds = [Math]::Max([int]$saved.intervalSeconds * 3, 90)
    } else {
        $shellName = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }
        $collectorStartedAt = [DateTime]::UtcNow
        $arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -OutputPath "{1}" -IntervalSeconds {2}' -f $collectorPath, $metricsPath, $CollectorIntervalSeconds
        $collector = Start-Process -FilePath (Join-Path $PSHOME $shellName) -ArgumentList $arguments `
            -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $collectorOutput -RedirectStandardError $collectorErrors

        try {
            @{
                processId = $collector.Id
                startedUtcTicks = $collector.StartTime.ToUniversalTime().Ticks.ToString()
                intervalSeconds = $CollectorIntervalSeconds
            } | ConvertTo-Json -Compress | Set-Content -LiteralPath $statePath -Encoding Ascii
        } catch {
            if (!$collector.HasExited) { Stop-Process -InputObject $collector -ErrorAction SilentlyContinue }
            throw
        }
        Write-Host "Windows collector started (PID $($collector.Id))."
        $freshnessSeconds = [Math]::Max($CollectorIntervalSeconds * 3, 90)
    }

    $deadline = (Get-Date).AddSeconds(45)
    do {
        $collector.Refresh()
        if ($collector.HasExited) { break }
        $metricsFile = Get-Item -LiteralPath $metricsPath -ErrorAction SilentlyContinue
        if ($metricsFile -and $metricsFile.Length -gt 0 -and
            $metricsFile.LastWriteTimeUtc -ge [DateTime]::UtcNow.AddSeconds(-$freshnessSeconds) -and
            (!$collectorStartedAt -or $metricsFile.LastWriteTimeUtc -ge $collectorStartedAt)) {
            return
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    throw "The Windows collector did not publish a fresh sample to $metricsPath. See $collectorErrors."
}

if (!$SkipHostCollector -and !(Test-Path -LiteralPath $collectorPath -PathType Leaf)) {
    throw "Windows collector script missing at $collectorPath."
}
if (!$SkipCodexTelemetry -and !(Test-Path -LiteralPath $telemetryPath -PathType Leaf)) {
    throw "Codex telemetry script missing at $telemetryPath."
}
$docker = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$docker) { throw 'Docker CLI is required. Install Docker Desktop with Docker Compose.' }

[System.IO.Directory]::CreateDirectory($runtimePath) | Out-Null
try {
    $lock = [System.IO.File]::Open((Join-Path $runtimePath 'lifecycle.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    throw "Could not acquire the lifecycle lock in $runtimePath. $($_.Exception.Message)"
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $engineAddress = $env:DOCKER_HOST
    if (!$engineAddress -or $env:DOCKER_CONTEXT) {
        $engineAddress = & $docker.Source context inspect --format '{{.Endpoints.docker.Host}}'
        if ($LASTEXITCODE -ne 0) { throw 'Could not read the Docker context.' }
    }
    if ($engineAddress -notmatch '^(npipe|unix)://') {
        throw "The selected Docker engine uses $engineAddress. Select a local Docker Desktop context for the Windows bind mount and loopback endpoints."
    }
    & $docker.Source compose version | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required.' }

    $engineType = Get-DockerEngineType
    if (!$engineType) {
        Write-Host 'Starting Docker Desktop and waiting for the engine.'
        Start-LocalDocker
        $deadline = (Get-Date).AddSeconds(90)
        do {
            Start-Sleep -Seconds 2
            $engineType = Get-DockerEngineType
        } while (!$engineType -and (Get-Date) -lt $deadline)
    }
    if (!$engineType) { throw 'Docker Desktop did not make its engine available. Open Docker Desktop and check its startup status.' }
    if ($engineType.Trim() -ne 'linux') { throw 'Select Linux containers in Docker Desktop and run Install.ps1 again.' }

    [System.IO.Directory]::CreateDirectory($metricsDirectory) | Out-Null
    Initialize-GrafanaCredential

    & $docker.Source @composeArguments config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose configuration validation failed.' }
    & $docker.Source @composeArguments up -d --wait --wait-timeout 120
    if ($LASTEXITCODE -ne 0) {
        & $docker.Source @composeArguments ps --all | Out-Host
        throw 'Docker Compose could not start the stack. Run docker compose logs --tail 80 from this directory for details.'
    }

    Wait-LocalEndpoint -Name Grafana -Uri "$grafanaUrl/api/health"
    Wait-LocalEndpoint -Name OTLP -Uri "$otlpUrl/v1/metrics" -SendOtlp
    if (!$SkipHostCollector) { Start-HostMetrics }
    if (!$SkipCodexTelemetry) { & $telemetryPath }

    Write-Host "Grafana is ready at $grafanaUrl."
    Write-Host "Grafana username admin. Password file $grafanaCredentialPath."
    Write-Host "The OTLP HTTP receiver is ready at $otlpUrl."
} finally {
    Pop-Location
    $lock.Dispose()
}

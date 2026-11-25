[CmdletBinding()]
param(
    [ValidateRange(5, 300)][int]$TimeoutSeconds = 45,
    [switch]$RequireHostMetrics,
    [ValidateRange(5, 7200)][int]$MaxHostAgeSeconds = 45
)

$ErrorActionPreference = 'Stop'
$docker = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$docker) { throw 'The Docker CLI is required to query the backends.' }

$composeArguments = @('compose', '--project-directory', $PSScriptRoot, '--file', (Join-Path $PSScriptRoot 'compose.yaml'))
$grafanaUrl = 'http://127.0.0.1:3000'
$otlpUrl = 'http://127.0.0.1:4318'

function Send-OtlpSignal {
    param([string]$Signal, [hashtable]$Payload)

    $json = $Payload | ConvertTo-Json -Depth 16 -Compress
    $response = Invoke-RestMethod -Uri "$otlpUrl/v1/$Signal" -Method Post -ContentType 'application/json' `
        -Body $json -UseBasicParsing -TimeoutSec 5
    $partial = $response.partialSuccess
    if ($partial.errorMessage -or [int64]$partial.rejectedDataPoints -gt 0 -or
        [int64]$partial.rejectedLogRecords -gt 0 -or [int64]$partial.rejectedSpans -gt 0) {
        throw "The Collector reported a partial OTLP $Signal ingestion failure."
    }
}

function Get-BackendResponse {
    param([string]$Uri)

    # The backends are reachable only from the Compose network.
    $ErrorActionPreference = 'Continue'
    $response = & $docker.Source @composeArguments exec -T prometheus wget -q -T 3 -O - `
        '--header=Accept: application/json' $Uri 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Backend query failed for $Uri (Docker/wget exit code $LASTEXITCODE)."
    }
    return ($response -join "`n")
}

function Get-PrometheusResult {
    param([string]$Expression)

    $query = [Uri]::EscapeDataString($Expression)
    $response = Get-BackendResponse "http://127.0.0.1:9090/api/v1/query?query=$query" | ConvertFrom-Json
    if ($response.status -ne 'success') { throw 'Prometheus did not return a successful query.' }
    return $response.data.result
}

$health = Invoke-RestMethod -Uri "$grafanaUrl/api/health" -UseBasicParsing -TimeoutSec 5
if ($health.database -ne 'ok') { throw 'Grafana did not report a healthy database.' }
Write-Host 'OK  Grafana responds and its database is healthy.'

$callId = [Guid]::NewGuid().ToString('N')
$traceId = [Guid]::NewGuid().ToString('N')
$spanId = [Guid]::NewGuid().ToString('N').Substring(0, 16)
$gaugeValue = Get-Random -Minimum 100000000 -Maximum 1000000000
$nowNanos = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() * [int64]1000000
$startedNanos = $nowNanos - [int64]1000000
$resource = @{ attributes = @(@{ key = 'service.name'; value = @{ stringValue = 'codex-smoke' } }) }
$scope = @{ name = 'codex-observability-smoke' }
$eventName = 'codex.smoke'
$spanName = 'codex.observability.smoke'
$privacyProbe = "synthetic-private-data-$callId"

Send-OtlpSignal metrics @{
    resourceMetrics = @(@{
        resource = $resource
        scopeMetrics = @(@{
            scope = $scope
            metrics = @(@{
                name = 'codex_observability_smoke'
                gauge = @{ dataPoints = @(@{ timeUnixNano = [string]$nowNanos; asInt = [string]$gaugeValue }) }
            })
        })
    })
}

Send-OtlpSignal logs @{
    resourceLogs = @(@{
        resource = $resource
        scopeLogs = @(@{
            scope = $scope
            logRecords = @(@{
                timeUnixNano = [string]$nowNanos
                observedTimeUnixNano = [string]$nowNanos
                severityNumber = 9
                severityText = 'INFO'
                traceId = $traceId
                spanId = $spanId
                body = @{ stringValue = $privacyProbe }
                attributes = @(
                    @{ key = 'event.name'; value = @{ stringValue = $eventName } },
                    @{ key = 'call_id'; value = @{ stringValue = $callId } },
                    @{ key = 'arguments'; value = @{ stringValue = $privacyProbe } }
                )
            })
        })
    })
}

Send-OtlpSignal traces @{
    resourceSpans = @(@{
        resource = $resource
        scopeSpans = @(@{
            scope = $scope
            spans = @(@{
                traceId = $traceId
                spanId = $spanId
                name = $spanName
                kind = 1
                startTimeUnixNano = [string]$startedNanos
                endTimeUnixNano = [string]$nowNanos
                status = @{ code = 1 }
                attributes = @(@{ key = 'prompt'; value = @{ stringValue = $privacyProbe } })
            })
        })
    })
}
Write-Host 'OK  Collector accepted all three synthetic OTLP signals.'

$lokiQuery = '{{service_name="codex-smoke"}} |= "{0}" | call_id="{1}"' -f $eventName, $callId
$encodedLokiQuery = [Uri]::EscapeDataString($lokiQuery)
$lokiStart = $nowNanos - [int64]60000000000
$lokiUrl = "http://loki:3100/loki/api/v1/query_range?query=$encodedLokiQuery&start=$lokiStart&limit=10"
$tempoUrl = "http://tempo:3200/api/traces/$traceId"
$spanNamePattern = '"name"\s*:\s*"' + [regex]::Escape($spanName) + '"'

$checks = [ordered]@{
    'Prometheus metric' = {
        $points = @(Get-PrometheusResult 'codex_observability_smoke')
        return @($points | Where-Object { [double]$_.value[1] -eq $gaugeValue }).Count -gt 0
    }
    'Loki log' = {
        $rawResponse = Get-BackendResponse $lokiUrl
        if ($rawResponse.Contains($privacyProbe)) { throw 'Loki stored a synthetic field that should be stripped.' }
        $response = $rawResponse | ConvertFrom-Json
        return $response.status -eq 'success' -and @($response.data.result).Count -gt 0
    }
    'Tempo trace' = {
        $response = Get-BackendResponse $tempoUrl
        if ($response.Contains($privacyProbe)) { throw 'Tempo stored a synthetic field that should be stripped.' }
        return $response -match $spanNamePattern
    }
}

if ($RequireHostMetrics) {
    $checks['Windows collector freshness'] = {
        $expression = 'abs(time() - codex_host_collector_last_success_timestamp_seconds) <= {0}' -f $MaxHostAgeSeconds
        return @(Get-PrometheusResult $expression).Count -gt 0
    }
    $checks['Windows process count'] = {
        return @(Get-PrometheusResult 'codex_host_process_count > 0').Count -gt 0
    }
}

$failures = [ordered]@{}
foreach ($name in $checks.Keys) { $failures[$name] = 'Expected sample has not appeared.' }
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
do {
    foreach ($name in @($failures.Keys)) {
        try {
            if (& $checks[$name]) {
                $failures.Remove($name)
                Write-Host "OK  $name confirmed."
            }
        } catch {
            $failures[$name] = $_.Exception.Message
        }
    }
    if ($failures.Count -gt 0) { Start-Sleep -Seconds 2 }
} while ($failures.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline)

if ($failures.Count -gt 0) {
    $details = foreach ($name in $failures.Keys) { "$name - $($failures[$name])" }
    throw ("Observability checks failed.`n" + ($details -join "`n"))
}

Write-Host "Synthetic trace ID $traceId"

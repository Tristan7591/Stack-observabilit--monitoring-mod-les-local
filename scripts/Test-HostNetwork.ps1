[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$probe = Get-Process -Id $PID
$probeRoot = Split-Path -LiteralPath $probe.Path
$probeName = $probe.ProcessName.ToLowerInvariant()
$temp = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path
$fixtureName = 'codex-host-network-' + [guid]::NewGuid().ToString('N')
$fixtureRoot = Join-Path $temp $fixtureName
$null = [IO.Directory]::CreateDirectory($fixtureRoot)
$metrics = Join-Path $fixtureRoot 'host.prom'
$collector = Join-Path $PSScriptRoot 'Start-HostCollector.ps1'
$windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$sockets = [Collections.Generic.List[object]]::new()

try {
    foreach ($address in @([Net.IPAddress]::Loopback, [Net.IPAddress]::IPv6Loopback)) {
        $listener = [Net.Sockets.TcpListener]::new($address, 0)
        $sockets.Add($listener)
        $listener.Start()
        $client = [Net.Sockets.TcpClient]::new($address.AddressFamily)
        $sockets.Add($client)
        $client.Connect($address, $listener.LocalEndpoint.Port)
        $sockets.Add($listener.AcceptTcpClient())
    }

    # Seed the collector with the directory containing this PowerShell process.
    & $windowsPowerShell -NoLogo -NoProfile -NonInteractive -File $collector `
        -OutputPath $metrics -CodexHome $probeRoot -Once
    if ($LASTEXITCODE -ne 0) { throw 'Windows collector fixture failed.' }

    $series = @(Select-String -LiteralPath $metrics -Pattern '^codex_host_(process_)?tcp_' | ForEach-Object Line)
    $tcpLabels = 'family="ipv[46]",state="[a-z0-9_]+",category="(?:loopback|tls_port|other)"'
    $processLabels = 'process="(?:[^"\\]|\\.)*",pid="\d+",parent_pid="\d+",'
    foreach ($line in $series) {
        if ($line -notmatch '^codex_host_tcp_snapshot_success\{family="ipv[46]"\} [01]$' -and
            $line -notmatch ('^codex_host_tcp_connections\{' + $tcpLabels + '\} \d+$') -and
            $line -notmatch ('^codex_host_process_tcp_connections\{' + $processLabels + $tcpLabels + '\} \d+$')) {
            throw 'The TCP collector exported an unexpected label or metric.'
        }
    }

    $prefix = 'codex_host_process_tcp_connections{process="' + $probeName + '",pid="' + $PID + '",parent_pid="'
    $own = @($series | Where-Object { $_.StartsWith($prefix) })
    foreach ($family in @('ipv4', 'ipv6')) {
        if (('codex_host_tcp_snapshot_success{family="' + $family + '"} 1') -notin $series) {
            throw "The $family TCP table was unavailable."
        }
        foreach ($state in @('listen', 'established')) {
            $suffix = '",family="' + $family + '",state="' + $state + '",category="loopback"} '
            $count = $own | Where-Object { $_.Contains($suffix) } | ForEach-Object { [int]($_.Split(' ')[-1]) }
            $minimum = if ($state -eq 'listen') { 1 } else { 2 }
            if (!$count -or $count -lt $minimum) { throw "The $family $state fixture was not found for the expected PID." }
        }
    }
    Write-Output 'Windows TCP IPv4 and IPv6 smoke checks passed.'
} finally {
    foreach ($socket in $sockets) {
        if ($socket -is [Net.Sockets.TcpListener]) { $socket.Stop() }
        else { $socket.Dispose() }
    }
    $target = (Resolve-Path -LiteralPath $fixtureRoot).Path
    if ((Split-Path -Parent $target) -eq $temp -and (Split-Path -Leaf $target) -eq $fixtureName) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

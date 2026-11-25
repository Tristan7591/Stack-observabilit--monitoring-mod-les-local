[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $OutputPath,
    [ValidateRange(1, 300)]
    [int] $IntervalSeconds = 5,
    [ValidateRange(10, 86400)]
    [int] $CacheIntervalSeconds = 300,
    [string] $CodexHome = $env:CODEX_HOME,
    [switch] $Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This collector requires Windows.'
}
if ([string]::IsNullOrWhiteSpace($CodexHome)) {
    $CodexHome = Join-Path $env:USERPROFILE '.codex'
}
$CodexHome = [IO.Path]::GetFullPath($CodexHome)
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$desktopRoot = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex'
$runtimeCache = Join-Path $env:USERPROFILE '.cache\codex-runtimes'
$browserRoot = Join-Path $env:APPDATA 'Codex\web\Codex'
$outputDirectory = [IO.Path]::GetDirectoryName($OutputPath)
$temporaryPath = Join-Path $outputDirectory ('.host-{0}.tmp' -f $PID)
$null = [IO.Directory]::CreateDirectory($outputDirectory)

if (-not ('CodexObservability.HostProbe' -as [type])) {
    # These Win32 queries avoid the WMI service and localized performance counter names.
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;

namespace CodexObservability {
    public static class HostProbe {
        private const uint SnapshotProcesses = 2;
        private const uint QueryLimitedInformation = 0x1000;
        private const int ErrorNoMoreFiles = 18;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct ProcessEntry {
            public uint Size, Usage, Pid;
            public IntPtr DefaultHeap;
            public uint Module, Threads, Parent;
            public int BasePriority;
            public uint Flags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
            public string ExeName;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessMemory {
            public uint Size, PageFaults;
            public UIntPtr PeakWorkingSet, WorkingSet, PeakPagedPool, PagedPool,
                PeakNonpagedPool, NonpagedPool, Pagefile, PeakPagefile, PrivateUsage;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters {
            public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct MemoryStatus {
            public uint Size, Load;
            public ulong TotalPhysical, AvailablePhysical, TotalPagefile, AvailablePagefile,
                TotalVirtual, AvailableVirtual, AvailableExtendedVirtual;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint pid);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool Process32FirstW(IntPtr snapshot, ref ProcessEntry entry);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool Process32NextW(IntPtr snapshot, ref ProcessEntry entry);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint desiredAccess, bool inheritHandle, uint pid);
        [DllImport("kernel32.dll")]
        private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool QueryFullProcessImageNameW(IntPtr process, int flags, StringBuilder name, ref int size);
        [DllImport("kernel32.dll")]
        private static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll")]
        private static extern bool GetProcessHandleCount(IntPtr process, out uint handles);
        [DllImport("kernel32.dll")]
        private static extern bool GetProcessIoCounters(IntPtr process, out IoCounters counters);
        [DllImport("psapi.dll")]
        private static extern bool GetProcessMemoryInfo(IntPtr process, ref ProcessMemory memory, uint size);
        [DllImport("kernel32.dll")]
        private static extern bool GetSystemTimes(out long idle, out long kernel, out long user);
        [DllImport("kernel32.dll")]
        private static extern bool GlobalMemoryStatusEx(ref MemoryStatus status);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool MoveFileExW(string source, string destination, uint flags);

        public sealed class Sample {
            public uint Pid, Parent, Threads;
            public string Name;
            public long Created;
            public double? CpuSeconds;
            public uint? Handles;
            public ulong? WorkingSet, PrivateBytes, ReadBytes, WriteBytes;
        }

        private sealed class ProcessRecord {
            public Sample Sample;
            public IntPtr Handle;
            public bool Selected;
        }

        private static bool IsUnder(string path, string root) {
            return !String.IsNullOrEmpty(root) && path.StartsWith(root, StringComparison.OrdinalIgnoreCase)
                && path.Length > root.Length && path[root.Length] == '\\';
        }

        public static List<Sample> Collect(string desktopRoot, string runtimeRoot, string codexHome,
            uint collectorPid, Dictionary<uint, long> previousFamily) {
            var records = new Dictionary<uint, ProcessRecord>();
            var snapshot = CreateToolhelp32Snapshot(SnapshotProcesses, 0);
            if (snapshot == new IntPtr(-1)) throw new Win32Exception(Marshal.GetLastWin32Error());

            try {
                var entry = new ProcessEntry { Size = (uint)Marshal.SizeOf(typeof(ProcessEntry)) };
                if (!Process32FirstW(snapshot, ref entry)) throw new Win32Exception(Marshal.GetLastWin32Error());
                var pathBuffer = new StringBuilder(32768);
                do {
                    if (entry.Pid == 0 || entry.Pid == collectorPid) continue;
                    var sample = new Sample {
                        Pid = entry.Pid, Parent = entry.Parent, Threads = entry.Threads,
                        Name = Path.GetFileNameWithoutExtension(entry.ExeName).ToLowerInvariant()
                    };
                    var record = new ProcessRecord { Sample = sample };
                    records.Add(entry.Pid, record);

                    record.Handle = OpenProcess(QueryLimitedInformation, false, entry.Pid);
                    string path = String.Empty;
                    if (record.Handle != IntPtr.Zero) {
                        pathBuffer.Clear();
                        int pathSize = pathBuffer.Capacity;
                        if (QueryFullProcessImageNameW(record.Handle, 0, pathBuffer, ref pathSize)) path = pathBuffer.ToString();
                        long created, exited, kernel, user;
                        if (GetProcessTimes(record.Handle, out created, out exited, out kernel, out user)) {
                            sample.Created = created;
                            sample.CpuSeconds = (kernel + user) / 10000000.0;
                        }
                    }

                    long previouslyCreated;
                    record.Selected = (sample.Created != 0 && previousFamily.TryGetValue(sample.Pid, out previouslyCreated)
                            && sample.Created == previouslyCreated)
                        || sample.Name == "codex" || sample.Name.StartsWith("codex-", StringComparison.Ordinal)
                        || IsUnder(path, desktopRoot) || IsUnder(path, runtimeRoot) || IsUnder(path, codexHome)
                        || (sample.Name == "chatgpt" && path.IndexOf("\\WindowsApps\\OpenAI.Codex_", StringComparison.OrdinalIgnoreCase) >= 0);
                } while (Process32NextW(snapshot, ref entry));
                int snapshotError = Marshal.GetLastWin32Error();
                if (snapshotError != ErrorNoMoreFiles) throw new Win32Exception(snapshotError);

                // A live child may outlast a parent. Creation times distinguish a recycled PID.
                bool changed;
                do {
                    changed = false;
                    foreach (var record in records.Values) {
                        ProcessRecord parent;
                        var sample = record.Sample;
                        if (!record.Selected && records.TryGetValue(sample.Parent, out parent) && parent.Selected
                            && (parent.Sample.Created == 0 || sample.Created == 0 || parent.Sample.Created <= sample.Created)) {
                            record.Selected = true;
                            changed = true;
                        }
                    }
                } while (changed);

                var selected = new List<Sample>();
                foreach (var record in records.Values) {
                    if (!record.Selected) continue;
                    var sample = record.Sample;
                    if (record.Handle != IntPtr.Zero) {
                        uint handles;
                        if (GetProcessHandleCount(record.Handle, out handles)) sample.Handles = handles;
                        var memory = new ProcessMemory { Size = (uint)Marshal.SizeOf(typeof(ProcessMemory)) };
                        if (GetProcessMemoryInfo(record.Handle, ref memory, memory.Size)) {
                            sample.WorkingSet = memory.WorkingSet.ToUInt64();
                            sample.PrivateBytes = memory.PrivateUsage.ToUInt64();
                        }
                        IoCounters io;
                        if (GetProcessIoCounters(record.Handle, out io)) {
                            sample.ReadBytes = io.ReadBytes;
                            sample.WriteBytes = io.WriteBytes;
                        }
                    }
                    if (!sample.WorkingSet.HasValue) {
                        // The OS-wide process list can still report memory for a protected service.
                        try {
                            using (var process = Process.GetProcessById((int)sample.Pid)) {
                                sample.WorkingSet = (ulong)process.WorkingSet64;
                                sample.PrivateBytes = (ulong)process.PrivateMemorySize64;
                            }
                        } catch (ArgumentException) {
                        } catch (InvalidOperationException) {
                        } catch (Win32Exception) { }
                    }
                    selected.Add(sample);
                }
                return selected;
            } finally {
                CloseHandle(snapshot);
                foreach (var record in records.Values) {
                    if (record.Handle != IntPtr.Zero) CloseHandle(record.Handle);
                }
            }
        }

        public static long[] CpuTimes() {
            long idle, kernel, user;
            return GetSystemTimes(out idle, out kernel, out user) ? new long[] { idle, kernel + user } : null;
        }

        public static ulong[] PhysicalMemory() {
            var status = new MemoryStatus { Size = (uint)Marshal.SizeOf(typeof(MemoryStatus)) };
            return GlobalMemoryStatusEx(ref status) ? new ulong[] { status.TotalPhysical, status.AvailablePhysical } : null;
        }

        public static void ReplaceFile(string source, string destination) {
            const uint ReplaceExisting = 1;
            if (!MoveFileExW(source, destination, ReplaceExisting)) throw new Win32Exception(Marshal.GetLastWin32Error());
        }

        public sealed class CacheSample {
            public long Bytes, Files;
            public bool Complete = true;
        }

        private static void CountCache(string root, CacheSample sample, Stopwatch clock) {
            var pending = new Stack<string>();
            pending.Push(root);
            while (pending.Count > 0) {
                if (clock.Elapsed.TotalSeconds > 30) {
                    sample.Complete = false;
                    return;
                }
                var path = pending.Pop();
                try {
                    var attributes = File.GetAttributes(path);
                    if ((attributes & FileAttributes.ReparsePoint) != 0) {
                        if (String.Equals(path, root, StringComparison.OrdinalIgnoreCase)) sample.Complete = false;
                        continue;
                    }
                    if ((attributes & FileAttributes.Directory) != 0) {
                        foreach (var entry in Directory.EnumerateFileSystemEntries(path)) pending.Push(entry);
                    } else {
                        sample.Bytes += new FileInfo(path).Length;
                        sample.Files++;
                    }
                } catch (FileNotFoundException) {
                } catch (DirectoryNotFoundException) {
                } catch (IOException) {
                    sample.Complete = false;
                } catch (UnauthorizedAccessException) {
                    sample.Complete = false;
                }
            }
        }

        public static Task<Dictionary<string, CacheSample>> ScanCaches(Dictionary<string, string[]> roots) {
            return Task.Factory.StartNew(() => {
                var result = new Dictionary<string, CacheSample>();
                foreach (var cache in roots) {
                    var sample = new CacheSample();
                    var clock = Stopwatch.StartNew();
                    foreach (var root in cache.Value) CountCache(root, sample, clock);
                    result.Add(cache.Key, sample);
                }
                return result;
            });
        }
    }
}
'@
}

$cacheRoots = [Collections.Generic.Dictionary[string, string[]]]::new()
$cacheRoots['codex'] = @((Join-Path $CodexHome 'cache'))
$cacheRoots['models'] = @((Join-Path $CodexHome 'models_cache.json'))
$cacheRoots['plugins'] = @((Join-Path $CodexHome 'plugins\cache'))
$cacheRoots['desktop_runtimes'] = @((Join-Path $desktopRoot 'runtimes'))
$cacheRoots['runtime_downloads'] = @($runtimeCache)
$cacheRoots['browser'] = @(
    foreach ($basename in @('component_crx_cache', 'extensions_crx_cache', 'GPUPersistentCache', 'GrShaderCache', 'ShaderCache', 'storage-cache')) {
        Join-Path $browserRoot $basename
    }
    foreach ($profile in @('Default', 'codex-browser-app')) {
        foreach ($basename in @('AutofillAiModelCache', 'Cache', 'Code Cache', 'DawnGraphiteCache', 'DawnWebGPUCache', 'GPUCache', 'optimization_guide_hint_cache_store')) {
            Join-Path (Join-Path $browserRoot $profile) $basename
        }
    }
)
$cacheRoots['desktop_package'] = @(
    foreach ($package in @(Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Packages') -Filter 'OpenAI.Codex_*' -Directory -ErrorAction SilentlyContinue)) {
        if (($package.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
            Join-Path $package.FullName 'LocalCache'
            Join-Path $package.FullName 'AC\INetCache'
        }
    }
)
$volumes = @(
    @($env:WINDIR, $CodexHome, $env:LOCALAPPDATA, $env:APPDATA, $runtimeCache, $OutputPath) |
        ForEach-Object { [IO.Path]::GetPathRoot($_) } |
        Where-Object { $_ -match '^[A-Za-z]:\\$' } |
        Sort-Object -Unique
)

$metricTypes = [ordered]@{
    codex_host_collector_last_success_timestamp_seconds = 'gauge'
    codex_host_process_count                            = 'gauge'
    codex_host_process_cpu_usage_percent                = 'gauge'
    codex_host_process_cpu_seconds_total                = 'counter'
    codex_host_process_working_set_bytes                = 'gauge'
    codex_host_process_private_bytes                    = 'gauge'
    codex_host_process_threads                          = 'gauge'
    codex_host_process_handles                          = 'gauge'
    codex_host_process_io_read_bytes_total               = 'counter'
    codex_host_process_io_write_bytes_total              = 'counter'
    codex_host_cpu_usage_percent                        = 'gauge'
    codex_host_logical_processors                       = 'gauge'
    codex_host_memory_total_bytes                       = 'gauge'
    codex_host_memory_available_bytes                   = 'gauge'
    codex_host_volume_size_bytes                        = 'gauge'
    codex_host_volume_free_bytes                        = 'gauge'
    codex_host_cache_bytes                              = 'gauge'
    codex_host_cache_files                              = 'gauge'
    codex_host_cache_scan_success                       = 'gauge'
    codex_host_cache_last_scan_timestamp_seconds        = 'gauge'
}
$invariant = [Globalization.CultureInfo]::InvariantCulture
$utf8 = [Text.UTF8Encoding]::new($false)

function Add-Metric {
    param([string] $Name, [AllowNull()] $Value, [string] $Labels = '')

    if ($null -ne $Value) {
        $line = '{0}{1} {2}' -f $Name, $Labels, $Value.ToString($invariant)
        $metricLines[$Name].Add($line)
    }
}

function Escape-LabelValue {
    param([string] $Value)

    $Value.Replace('\', '\\').Replace('"', '\"').Replace("`n", '\n').Replace("`r", '')
}

$previousFamily = [Collections.Generic.Dictionary[uint32, long]]::new()
$previousCpu = @{}
$previousTime = $null
$previousSystemCpu = $null
$lastCache = @{}
$lastCacheStatus = $null
$lastCacheTimestamp = 0L
$lastCacheStart = [DateTimeOffset]::MinValue
$cacheTask = $null
$cacheTaskStarted = $null

try {
    do {
        try {
            $now = [DateTimeOffset]::UtcNow
            if ($null -eq $cacheTask -and ($now - $lastCacheStart).TotalSeconds -ge $CacheIntervalSeconds) {
                $lastCacheStart = $now
                $cacheTask = [CodexObservability.HostProbe]::ScanCaches($cacheRoots)
                $cacheTaskStarted = $now
            }
            if ($null -ne $cacheTask -and ($Once -or $cacheTask.IsCompleted)) {
                try {
                    $result = $cacheTask.GetAwaiter().GetResult()
                    $lastCacheTimestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
                    $lastCacheStatus = $result
                    foreach ($name in $result.Keys) {
                        if ($result[$name].Complete) { $lastCache[$name] = $result[$name] }
                    }
                } catch {
                    $lastCacheStatus = $null
                    $lastCacheTimestamp = $cacheTaskStarted.ToUnixTimeSeconds()
                    Write-Warning 'The cache scan failed. Cached values are retained from the last successful scan.'
                } finally {
                    $cacheTask = $null
                }
            }

            $metricLines = @{}
            foreach ($name in $metricTypes.Keys) {
                $metricLines[$name] = [Collections.Generic.List[string]]::new()
            }
            $captured = [Diagnostics.Stopwatch]::GetTimestamp()
            $processes = [CodexObservability.HostProbe]::Collect($desktopRoot, $runtimeCache, $CodexHome.TrimEnd('\'), [uint32]$PID, $previousFamily)
            Add-Metric codex_host_process_count $processes.Count
            $currentCpu = @{}
            $previousFamily.Clear()
            foreach ($process in $processes) {
                $label = '{{process="{0}",pid="{1}",parent_pid="{2}"}}' -f (Escape-LabelValue $process.Name), $process.Pid, $process.Parent
                if ($process.Created -ne 0) { $previousFamily[$process.Pid] = $process.Created }
                if ($null -ne $process.CpuSeconds) {
                    $key = '{0}-{1}' -f $process.Pid, $process.Created
                    $currentCpu[$key] = $process.CpuSeconds
                    if ($null -ne $previousTime -and $previousCpu.ContainsKey($key)) {
                        $elapsed = ($captured - $previousTime) / [Diagnostics.Stopwatch]::Frequency
                        $percent = 100.0 * ($process.CpuSeconds - $previousCpu[$key]) / $elapsed
                        Add-Metric codex_host_process_cpu_usage_percent ([Math]::Max(0.0, $percent)) $label
                    }
                }
                Add-Metric codex_host_process_cpu_seconds_total $process.CpuSeconds $label
                Add-Metric codex_host_process_working_set_bytes $process.WorkingSet $label
                Add-Metric codex_host_process_private_bytes $process.PrivateBytes $label
                Add-Metric codex_host_process_threads $process.Threads $label
                Add-Metric codex_host_process_handles $process.Handles $label
                Add-Metric codex_host_process_io_read_bytes_total $process.ReadBytes $label
                Add-Metric codex_host_process_io_write_bytes_total $process.WriteBytes $label
            }
            $previousCpu = $currentCpu
            $previousTime = $captured

            $systemCpu = [CodexObservability.HostProbe]::CpuTimes()
            if ($null -ne $systemCpu -and $null -ne $previousSystemCpu) {
                $total = $systemCpu[1] - $previousSystemCpu[1]
                if ($total -gt 0) {
                    $idle = $systemCpu[0] - $previousSystemCpu[0]
                    Add-Metric codex_host_cpu_usage_percent (100.0 * (1.0 - $idle / $total))
                }
            }
            $previousSystemCpu = $systemCpu
            Add-Metric codex_host_logical_processors ([Environment]::ProcessorCount)
            $memory = [CodexObservability.HostProbe]::PhysicalMemory()
            if ($null -ne $memory) {
                Add-Metric codex_host_memory_total_bytes $memory[0]
                Add-Metric codex_host_memory_available_bytes $memory[1]
            }

            foreach ($volume in $volumes) {
                try {
                    $drive = [IO.DriveInfo]::new($volume)
                    if ($drive.IsReady) {
                        $label = '{{volume="{0}"}}' -f $volume.TrimEnd('\').ToUpperInvariant()
                        Add-Metric codex_host_volume_size_bytes $drive.TotalSize $label
                        Add-Metric codex_host_volume_free_bytes $drive.TotalFreeSpace $label
                    }
                } catch [IO.IOException] {
                } catch [UnauthorizedAccessException] { }
            }

            if ($lastCacheTimestamp -gt 0) {
                Add-Metric codex_host_cache_last_scan_timestamp_seconds $lastCacheTimestamp
                foreach ($name in $cacheRoots.Keys) {
                    $label = '{{cache="{0}"}}' -f $name
                    $succeeded = $null -ne $lastCacheStatus -and $lastCacheStatus[$name].Complete
                    Add-Metric codex_host_cache_scan_success ([int]$succeeded) $label
                    if ($lastCache.ContainsKey($name)) {
                        Add-Metric codex_host_cache_bytes $lastCache[$name].Bytes $label
                        Add-Metric codex_host_cache_files $lastCache[$name].Files $label
                    }
                }
            }

            Add-Metric codex_host_collector_last_success_timestamp_seconds ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
            $payload = [Text.StringBuilder]::new()
            foreach ($name in $metricTypes.Keys) {
                if ($metricLines[$name].Count -eq 0) { continue }
                $null = $payload.Append("# TYPE $name $($metricTypes[$name])`n")
                foreach ($line in $metricLines[$name]) { $null = $payload.Append($line).Append("`n") }
            }
            [IO.File]::WriteAllText($temporaryPath, $payload.ToString(), $utf8)
            for ($attempt = 1; $attempt -le 4; $attempt++) {
                try {
                    [CodexObservability.HostProbe]::ReplaceFile($temporaryPath, $OutputPath)
                    break
                } catch {
                    if ($attempt -eq 4) { throw }
                    Start-Sleep -Milliseconds 50
                }
            }
        } catch {
            if ($Once) { throw }
            Write-Warning ('Windows process sample failed ({0}). The previous metrics file remains available.' -f $_.Exception.GetType().Name)
        }
        if (-not $Once) { Start-Sleep -Seconds $IntervalSeconds }
    } while (-not $Once)
} finally {
    if ([IO.File]::Exists($temporaryPath)) { [IO.File]::Delete($temporaryPath) }
}

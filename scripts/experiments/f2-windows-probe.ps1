param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Fixture
)

$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public sealed class F2ProbeJob : IDisposable {
    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimit {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimit {
        public BasicLimit BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct BasicAccounting {
        public long TotalUserTime, TotalKernelTime, ThisPeriodTotalUserTime, ThisPeriodTotalKernelTime;
        public uint TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminatedProcesses;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimit info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateJobObject(IntPtr job, uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool QueryInformationJobObject(IntPtr job, int kind, out BasicAccounting info, uint length, IntPtr returnedLength);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    private IntPtr handle;
    public F2ProbeJob() {
        handle = CreateJobObject(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        var limits = new ExtendedLimit();
        limits.BasicLimitInformation.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(typeof(ExtendedLimit)))) {
            int error = Marshal.GetLastWin32Error();
            CloseHandle(handle);
            handle = IntPtr.Zero;
            throw new Win32Exception(error);
        }
    }
    public void Assign(System.Diagnostics.Process process) {
        if (!AssignProcessToJobObject(handle, process.Handle))
            throw new Win32Exception(Marshal.GetLastWin32Error());
    }
    public void Dispose() {
        if (handle == IntPtr.Zero) return;
        try {
            if (!TerminateJobObject(handle, 1)) throw new Win32Exception(Marshal.GetLastWin32Error());
            var deadline = DateTime.UtcNow.AddSeconds(10);
            while (true) {
                BasicAccounting accounting;
                if (!QueryInformationJobObject(handle, 1, out accounting,
                    (uint)Marshal.SizeOf(typeof(BasicAccounting)), IntPtr.Zero))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                if (accounting.ActiveProcesses == 0) break;
                if (DateTime.UtcNow >= deadline)
                    throw new TimeoutException("Job still has active processes after termination");
                System.Threading.Thread.Sleep(50);
            }
        } finally {
            CloseHandle(handle);
            handle = IntPtr.Zero;
        }
    }
}
'@

function Get-ProbeOutputSize([string]$Directory) {
    $size = 0L
    foreach ($name in @('cli.stdout', 'cli.stderr', 'child.stdout', 'child.stderr')) {
        $file = Join-Path $Directory $name
        if (Test-Path $file) { $size += (Get-Item $file).Length }
    }
    return $size
}

function Get-ProbeErrorExcerpt([string]$Path) {
    if (-not (Test-Path $Path)) { return '' }
    $reader = [IO.StreamReader]::new($Path, [Text.Encoding]::UTF8, $true)
    try {
        $chars = New-Object char[] 2048
        $count = $reader.Read($chars, 0, $chars.Length)
        return [string]::new($chars, 0, $count)
    } finally { $reader.Dispose() }
}

function Assert-ProbeUserCanConnect([pscredential]$Credential, [string]$Workspace,
                                    [string]$Scratch, [string]$Address) {
    $code = @'
$client = [Net.Sockets.TcpClient]::new()
try {
    $connection = $client.BeginConnect('ADDRESS_PLACEHOLDER', 443, $null, $null)
    if (-not $connection.AsyncWaitHandle.WaitOne(5000)) { exit 2 }
    $client.EndConnect($connection)
    exit 0
} catch { exit 1 } finally { $client.Dispose() }
'@
    $code = $code.Replace('ADDRESS_PLACEHOLDER', $Address)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $probe = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
      -ArgumentList "-NoProfile -NonInteractive -EncodedCommand $encoded" `
      -Credential $Credential -LoadUserProfile -WorkingDirectory $Workspace -WindowStyle Hidden `
      -RedirectStandardOutput (Join-Path $Scratch 'baseline.stdout') `
      -RedirectStandardError (Join-Path $Scratch 'baseline.stderr') -PassThru
    try {
        if (-not $probe.WaitForExit(10000)) { throw 'Network positive control timed out' }
        if ($probe.ExitCode -ne 0) { throw 'Probe user cannot reach control endpoint before firewall rule' }
    } finally {
        if (-not $probe.HasExited) { & taskkill /PID $probe.Id /T /F | Out-Null }
        $probe.Dispose()
    }
}

function Assert-RunnerCanConnect([string]$Address) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.BeginConnect($Address, 443, $null, $null)
        if (-not $connection.AsyncWaitHandle.WaitOne(5000)) {
            throw 'Runner connectivity timed out after scoped firewall rule'
        }
        $client.EndConnect($connection)
    } finally { $client.Dispose() }
}

$expectedCommit = '5be723be1b5c34cae2abe6fea5718f0407f91760'
$expectedTree = '42e4195294ae1825c9025ccfe0a48d256814ce5d'
$actualCommit = (& git -C $Source rev-parse HEAD).Trim()
$actualTree = (& git -C $Source rev-parse 'HEAD^{tree}').Trim()
if ($actualCommit -ne $expectedCommit -or $actualTree -ne $expectedTree) {
    throw "MyHarness source identity mismatch: commit=$actualCommit tree=$actualTree"
}
foreach ($notice in @('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md')) {
    if (-not (Test-Path (Join-Path $Source $notice))) { throw "Missing MyHarness $notice" }
}
$node = (Get-Command node -ErrorAction Stop).Source
if ((& $node --version).Trim() -ne 'v22.22.1') { throw 'Unexpected Node version' }
$networkAddress = [Net.Dns]::GetHostAddresses('github.com') |
  Where-Object { $_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork } |
  Select-Object -First 1 -ExpandProperty IPAddressToString
if (-not $networkAddress) { throw 'No IPv4 address resolved for network positive control' }
Copy-Item -LiteralPath $Fixture -Destination (Join-Path $Source 'f2-faux.ts')

$root = Join-Path $env:RUNNER_TEMP "f2-windows-$($env:GITHUB_RUN_ID)"
$workspace = Join-Path $root 'workspace'
$scratch = Join-Path $root 'scratch'
$outside = Join-Path $root 'outside'
New-Item -ItemType Directory -Force -Path $workspace, $scratch, $outside | Out-Null
Set-Content -Encoding Ascii (Join-Path $workspace 'fixture.txt') 'read-only workspace'
$sentinel = Join-Path $outside 'sentinel.txt'
Set-Content -Encoding Ascii $sentinel 'outside-private-sentinel'
$sentinelHash = (Get-FileHash $sentinel -Algorithm SHA256).Hash
$userName = "f2p$($env:GITHUB_RUN_ID)"
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$random = New-Object byte[] 32
$rng.GetBytes($random)
$rng.Dispose()
$password = ConvertTo-SecureString ([Convert]::ToBase64String($random)) -AsPlainText -Force
$credential = [pscredential]::new("$($env:COMPUTERNAME)\$userName", $password)
$firewallRule = "f2-windows-probe-$($env:GITHUB_RUN_ID)"
$createdUser = $false
$createdRule = $false
$process = $null
$job = $null
$jobAssigned = $false
try {
    New-LocalUser -Name $userName -Password $password -PasswordNeverExpires | Out-Null
    $createdUser = $true
    $sid = (Get-LocalUser -Name $userName).SID.Value
    foreach ($path in @($Source, $workspace)) {
        & icacls $path /deny "*$($sid):(OI)(CI)W" /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to deny writes under $path" }
    }
    & icacls $outside /deny "*$($sid):(OI)(CI)R" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to deny outside sentinel reads' }
    & icacls $scratch /grant "*$($sid):(OI)(CI)M" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to grant probe scratch access' }

    Assert-ProbeUserCanConnect $credential $workspace $scratch $networkAddress

    $userFilter = "D:(A;;CC;;;$sid)"
    New-NetFirewallRule -Name $firewallRule -DisplayName $firewallRule -Direction Outbound `
      -Profile Any -Action Block -RemoteAddress Any -LocalUser $userFilter | Out-Null
    $createdRule = $true
    if ((Get-NetFirewallRule -Name $firewallRule -ErrorAction Stop).Enabled -ne 'True') {
        throw 'Outbound firewall rule is not enabled'
    }
    $actualFilter = (Get-NetFirewallRule -Name $firewallRule |
      Get-NetFirewallSecurityFilter).LocalUser
    if ($actualFilter -notmatch [regex]::Escape($sid)) {
        throw "Firewall rule did not retain the probe user SID: $actualFilter"
    }
    Assert-RunnerCanConnect $networkAddress

    $data = @{ node = $node; source = $Source; workspace = $workspace;
               scratch = $scratch; sentinel = $sentinel;
               networkAddress = $networkAddress } | ConvertTo-Json -Compress
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($data))
    $child = Join-Path $PSScriptRoot 'f2-windows-probe-child.ps1'
    $invocation = "& '$child' -Payload '$payload'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($invocation))
    $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
      -ArgumentList "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded" `
      -Credential $credential -LoadUserProfile -WorkingDirectory $workspace -WindowStyle Hidden `
      -RedirectStandardOutput (Join-Path $scratch 'child.stdout') `
      -RedirectStandardError (Join-Path $scratch 'child.stderr') -PassThru
    $job = [F2ProbeJob]::new()
    $job.Assign($process)
    $jobAssigned = $true
    Set-Content -Encoding Ascii (Join-Path $scratch 'go') 'attached'

    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while (-not $process.HasExited) {
        $size = Get-ProbeOutputSize $scratch
        if ($size -gt 131072) { throw "Probe output exceeded 128 KiB ($size bytes)" }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Probe exceeded 45 seconds' }
        Start-Sleep -Milliseconds 250
        $process.Refresh()
    }
    $size = Get-ProbeOutputSize $scratch
    if ($size -gt 131072) { throw "Probe output exceeded 128 KiB ($size bytes)" }
    $resultFile = Join-Path $scratch 'result.json'
    if (-not (Test-Path $resultFile)) {
        $errorText = Get-ProbeErrorExcerpt (Join-Path $scratch 'child.stderr')
        throw "Probe child exited $($process.ExitCode) without result: $errorText"
    }
    if ((Get-Item $resultFile).Length -gt 8192) { throw 'Probe result exceeded 8 KiB' }
    $result = Get-Content $resultFile -Raw | ConvertFrom-Json
    if ($process.ExitCode -ne 0 -or $result.exitCode -ne 0) {
        $errorText = Get-ProbeErrorExcerpt (Join-Path $scratch 'cli.stderr')
        throw "MyHarness CLI exited $($result.exitCode): $errorText"
    }
    $lines = @(Get-Content (Join-Path $scratch 'cli.stdout') | Where-Object { $_.Trim() })
    if ($lines.Count -lt 2) { throw 'Expected JSONL session header and events' }
    $events = @($lines | ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
    if ($events[0].type -ne 'session' -or $events[0].version -ne 3) {
        throw 'Invalid MyHarness JSONL session header'
    }
    if (-not (($events | ConvertTo-Json -Depth 30) -match 'F2_FIXTURE_SUCCESS')) {
        throw 'Fixed Provider response did not appear in JSONL'
    }
    if ($events.type -contains 'tool_execution_start') { throw 'Unexpected tool execution event' }
    if ((Get-FileHash $sentinel -Algorithm SHA256).Hash -ne $sentinelHash) {
        throw 'Outside sentinel changed'
    }
    Write-Host "F2 probe passed: commit=$actualCommit tree=$actualTree"
    Write-Host "Boundary: sourceReadOnly=$($result.sourceWriteDenied) workspaceReadOnly=$($result.workspaceWriteDenied) outsideHidden=$($result.outsideReadDenied) networkDenied=$($result.networkDenied)"
    Write-Host "JSONL: $($events.Count) events; stdout=$($result.stdoutBytes) bytes; stderr=$($result.stderrBytes) bytes"
    Write-Host "Sanitized environment keys: $($result.environmentKeys -join ',')"
    Write-Host "Process: child exit=$($process.ExitCode)"
} finally {
    $treeStopped = $false
    if ($jobAssigned) {
        try { $job.Dispose(); $treeStopped = $true }
        catch { throw "Job termination failed; retaining firewall and user: $_" }
    } elseif ($process) {
        & taskkill /PID $process.Id /T /F | Out-Null
        [void]$process.WaitForExit(5000)
        $treeStopped = $process.HasExited
        if ($job) { $job.Dispose() }
    } else { $treeStopped = $true }
    if ($treeStopped) {
        if ($createdRule) { Remove-NetFirewallRule -Name $firewallRule -ErrorAction SilentlyContinue }
        if ($createdUser) { Remove-LocalUser -Name $userName -ErrorAction SilentlyContinue }
    }
}

param([Parameter(Mandatory = $true)][string]$Payload)

$ErrorActionPreference = 'Stop'
$data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Payload)) | ConvertFrom-Json

# This script runs as a fresh standard user, before loading any MyHarness code.
# Replace the inherited environment before launching Node.
$systemRoot = $env:SystemRoot
Get-ChildItem Env: | ForEach-Object { Remove-Item "Env:$($_.Name)" -ErrorAction SilentlyContinue }
$env:SystemRoot = $systemRoot
$env:WINDIR = $systemRoot
$env:ComSpec = Join-Path $systemRoot 'System32\cmd.exe'
$env:PATH = "$(Split-Path $data.node);$systemRoot\System32;$systemRoot"
$env:PATHEXT = '.COM;.EXE;.BAT;.CMD'
$env:HOME = $data.scratch
$env:USERPROFILE = $data.scratch
$env:APPDATA = Join-Path $data.scratch 'AppData\Roaming'
$env:LOCALAPPDATA = Join-Path $data.scratch 'AppData\Local'
$env:TEMP = $data.scratch
$env:TMP = $data.scratch
$env:CI = 'true'
New-Item -ItemType Directory -Force -Path $env:APPDATA, $env:LOCALAPPDATA | Out-Null

# The parent attaches this PowerShell process to a kill-on-close Job Object
# before releasing this gate. Node and its descendants inherit that job.
$go = Join-Path $data.scratch 'go'
$goDeadline = [DateTime]::UtcNow.AddSeconds(15)
while (-not (Test-Path $go)) {
    if ([DateTime]::UtcNow -ge $goDeadline) { throw 'Parent did not release the job gate' }
    Start-Sleep -Milliseconds 100
}

$sourceWriteDenied = $false
try { [IO.File]::WriteAllText((Join-Path $data.source 'f2-illegal-write'), 'probe') }
catch [UnauthorizedAccessException] { $sourceWriteDenied = $true }
$workspaceWriteDenied = $false
try { [IO.File]::WriteAllText((Join-Path $data.workspace 'f2-illegal-write'), 'probe') }
catch [UnauthorizedAccessException] { $workspaceWriteDenied = $true }
$outsideReadDenied = $false
try { [void][IO.File]::ReadAllText($data.sentinel) }
catch [UnauthorizedAccessException] { $outsideReadDenied = $true }

if (-not ($sourceWriteDenied -and $workspaceWriteDenied -and $outsideReadDenied)) {
    throw 'One or more OS containment probes failed before MyHarness startup.'
}

$cli = Join-Path $data.source 'packages\coding-agent\dist\cli.js'
$fixture = Join-Path $data.source 'f2-faux.ts'
$stdout = Join-Path $data.scratch 'cli.stdout'
$stderr = Join-Path $data.scratch 'cli.stderr'
Push-Location $data.workspace
try {
    $ErrorActionPreference = 'Continue'
    & $data.node $cli --mode json --no-session --no-tools --no-extensions --no-skills `
      --no-prompt-templates --no-themes --no-context-files --offline --approve `
      --extension $fixture --provider f2-faux --model faux-1 'Return the fixed fixture.' `
      1> $stdout 2> $stderr
    $exitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = 'Stop'
    Pop-Location
}

$result = [ordered]@{
    exitCode = $exitCode
    sourceWriteDenied = $sourceWriteDenied
    workspaceWriteDenied = $workspaceWriteDenied
    outsideReadDenied = $outsideReadDenied
    environmentKeys = @((Get-ChildItem Env:).Name | Sort-Object)
    stdoutBytes = (Get-Item $stdout).Length
    stderrBytes = (Get-Item $stderr).Length
}
$result | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $data.scratch 'result.json')
if ($exitCode -ne 0) { exit $exitCode }

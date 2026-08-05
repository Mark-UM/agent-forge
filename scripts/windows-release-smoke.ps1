param(
    [string]$Python = "python",
    [switch]$SkipDependencyInstall,
    [switch]$SkipBrowserInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateFile = Join-Path $ProjectRoot "_runtime\supervisor\state.json"
$Services = "scheduler,browser"
$Started = $false

function Invoke-AgentForgePython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE: $Python $($Arguments -join ' ')"
    }
}

Push-Location $ProjectRoot
try {
    $env:PYTHONUTF8 = "1"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:AGENT_FORGE_BROWSER_HEADLESS = "1"
    $env:AGENT_FORGE_SUPERVISOR_START_TIMEOUT = "90"
    $env:AGENT_FORGE_SUPERVISOR_STOP_TIMEOUT = "20"

    Invoke-AgentForgePython -Arguments @("--version")
    Invoke-AgentForgePython -Arguments @(
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
    )

    if (-not $SkipDependencyInstall) {
        Invoke-AgentForgePython -Arguments @(
            "-m", "modules.bootstrap.dependencies", "install"
        )
    }

    Invoke-AgentForgePython -Arguments @(
        "-m", "modules.bootstrap.dependencies", "check", "--json"
    )

    if (-not $SkipBrowserInstall) {
        Invoke-AgentForgePython -Arguments @(
            "-m", "modules.bootstrap.dependencies", "install-browser", "chromium"
        )
    }

    # A stale state file must not make the smoke test adopt unrelated processes.
    # On a clean runner stop is a no-op; locally it only stops processes recorded
    # as owned by this project.
    if (Test-Path $StateFile) {
        Invoke-AgentForgePython -Arguments @(
            "-m", "modules.runtime.supervisor", "stop", "--json"
        )
    }

    Invoke-AgentForgePython -Arguments @(
        "-m", "modules.runtime.supervisor", "start",
        "--services", $Services,
        "--json"
    )
    $Started = $true

    $StatusOutput = & $Python -m modules.runtime.supervisor status `
        --services $Services --json | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "Supervisor status command failed with exit code $LASTEXITCODE"
    }
    $Status = $StatusOutput | ConvertFrom-Json
    if (-not $Status.healthy) {
        throw "Supervisor reported an unhealthy Windows service set: $StatusOutput"
    }

    foreach ($Name in @("scheduler", "browser")) {
        $Service = $Status.services.$Name
        if ($null -eq $Service -or -not $Service.healthy) {
            throw "Required service '$Name' did not pass authenticated health"
        }
        if (-not $Service.port_open) {
            throw "Required service '$Name' did not open its loopback port"
        }
    }

    Write-Host "Windows release smoke: PASS"
    Write-Host $StatusOutput
}
finally {
    if ($Started -or (Test-Path $StateFile)) {
        & $Python -m modules.runtime.supervisor stop --json
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Supervisor cleanup exited with code $LASTEXITCODE"
        }
    }
    Pop-Location
}

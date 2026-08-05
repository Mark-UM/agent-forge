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
$ServicePorts = @(9223, 9225)
$Started = $false
$PrimaryFailure = $null
$CleanupFailure = $null
$StatusOutput = $null

function Invoke-AgentForgePython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Python command failed with exit code {0}: {1} {2}" -f `
                $LASTEXITCODE, $Python, ($Arguments -join " ")
        )
    }
}

function Stop-AgentForgeManagedServices {
    if (-not (Test-Path $StateFile)) {
        return
    }

    & $Python -m modules.runtime.supervisor stop --json
    if ($LASTEXITCODE -eq 0) {
        return
    }

    # Older Supervisor builds can fail while probing a stale Windows PID with
    # os.kill(pid, 0). Fall back only for processes that this project's state
    # file explicitly marks as managed and whose command line identifies one of
    # the two Agent Forge daemon modules. Never terminate an unknown process.
    Write-Warning (
        "Supervisor stop exited with code {0}; attempting bounded managed-process cleanup" -f `
            $LASTEXITCODE
    )

    try {
        $State = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    }
    catch {
        throw "Supervisor state could not be parsed after stop failed: $($_.Exception.Message)"
    }

    if ($null -eq $State.services) {
        Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
        return
    }

    $AllowedModules = @(
        "modules.browser.daemon",
        "modules.scheduler.secure_daemon"
    )

    foreach ($Property in $State.services.PSObject.Properties) {
        $Service = $Property.Value
        if ($null -eq $Service) {
            continue
        }
        if (-not [bool]$Service.managed -or [bool]$Service.adopted) {
            continue
        }
        if ($null -eq $Service.pid) {
            continue
        }

        $ProcessId = [int]$Service.pid
        $Process = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $ProcessId" `
            -ErrorAction SilentlyContinue
        if ($null -eq $Process) {
            continue
        }

        $CommandLine = [string]$Process.CommandLine
        $Recognized = $false
        foreach ($ModuleName in $AllowedModules) {
            if ($CommandLine -like "*$ModuleName*") {
                $Recognized = $true
                break
            }
        }
        if (-not $Recognized) {
            throw (
                "Refusing to terminate PID {0}: the recorded process is not an Agent Forge daemon" -f `
                    $ProcessId
            )
        }

        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        Wait-Process -Id $ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    }

    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
}

function Assert-AgentForgePortsFree {
    foreach ($Port in $ServicePorts) {
        $Listeners = @(
            Get-NetTCPConnection `
                -State Listen `
                -LocalPort $Port `
                -ErrorAction SilentlyContinue
        )
        if ($Listeners.Count -gt 0) {
            $Owners = ($Listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ","
            throw (
                "Loopback port {0} is already in use by PID(s) {1}. Stop the existing Agent Forge service before reinstalling vendor dependencies." -f `
                    $Port, $Owners
            )
        }
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

    # A previous local run may still be importing native extensions from
    # vendor/python-libs. Stop owned daemons before pip tries to replace those
    # files; Windows will otherwise reject deletion of a loaded .pyd DLL.
    Stop-AgentForgeManagedServices
    Assert-AgentForgePortsFree

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
}
catch {
    $PrimaryFailure = $_
}
finally {
    if ($Started -or (Test-Path $StateFile)) {
        try {
            Stop-AgentForgeManagedServices
        }
        catch {
            $CleanupFailure = $_
        }
    }
    Pop-Location
}

if ($null -ne $PrimaryFailure) {
    if ($null -ne $CleanupFailure) {
        throw (
            "Windows release smoke failed: {0} Cleanup also failed: {1}" -f `
                $PrimaryFailure.Exception.Message,
                $CleanupFailure.Exception.Message
        )
    }
    throw $PrimaryFailure
}
if ($null -ne $CleanupFailure) {
    throw $CleanupFailure
}

Write-Host "Windows release smoke: PASS"
Write-Host $StatusOutput

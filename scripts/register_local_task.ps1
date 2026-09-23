<#
.SYNOPSIS
    Registers (or re-registers) the local fast-lane poller in Task Scheduler.

.DESCRIPTION
    Creates a task that runs scripts/local_check.ps1 every minute while the
    user is logged on, including when the session is locked. Runs under the
    current user so it inherits the NTFY_TOPIC user environment variable.

    Deliberately does NOT use "run whether user is logged on or not", because
    that mode requires storing the account password with the task.

    Re-running this script replaces any existing registration, so it is safe to
    run again after changing the interval.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_local_task.ps1
    powershell -ExecutionPolicy Bypass -File scripts\register_local_task.ps1 -IntervalMinutes 2
#>

param(
    # Defaults to 1 minute, the shortest interval Task Scheduler supports.
    #
    # Burgundy 512GB was observed in stock three times on 2026-09-23, for 4,
    # 2 and 2 minutes. A 2-minute poll gives a 2-minute window roughly one
    # chance; 1 minute gives it two. Burgundy consistently sold out faster
    # than any other finish (2-4 min, against 14-16 for black), so the
    # contended finish is exactly the one the interval has to be sized for.
    #
    # Not going below this deliberately. Sub-minute polling needs a sleep
    # loop to get around Task Scheduler's floor, and at ~1440 requests a day
    # this is already at the upper end of what passes for human browsing.
    # Being rate-limited by Apple would cost far more detection than the
    # extra poll would buy.
    [int]$IntervalMinutes = 1,
    [string]$TaskName = 'AppleStockWatcher'
)

$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'local_check.ps1'
if (-not (Test-Path $script)) { throw "missing $script" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""

# Repeat indefinitely, starting a minute from now so the first run is prompt.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false } catch {}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Polls Apple UAE for watched iPhone stock and pushes an ntfy alert. Fast lane complementing the GitHub Actions cron.' | Out-Null

Write-Host "Registered '$TaskName', every $IntervalMinutes minute(s) while logged on."
Write-Host "Log: $env:TEMP\applewatch-local.log"
Write-Host "Run now:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Stop it:  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"

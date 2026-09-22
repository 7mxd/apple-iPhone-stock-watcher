<#
.SYNOPSIS
    Registers (or re-registers) the local fast-lane poller in Task Scheduler.

.DESCRIPTION
    Creates a task that runs scripts/local_check.ps1 every 5 minutes while the
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
    [int]$IntervalMinutes = 5,
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

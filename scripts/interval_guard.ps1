<#
.SYNOPSIS
    Watchdog for the polling interval. Reverts to the safe interval if the
    faster one starts drawing rate limits.

.DESCRIPTION
    Polling faster than every 2 minutes has repeatedly drawn HTTP 541 from
    Apple. 90 seconds is worth testing, but an unattended experiment that
    fails at 01:00 would otherwise leave the watcher degraded until someone
    noticed in the morning.

    This runs periodically and enforces one rule:

        if the interval is faster than SAFE_SECONDS
        and any poll has failed in the recent window
        then go back to SAFE_SECONDS and stop experimenting

    Fail-safe by construction: any error, any unreadable log, any ambiguity
    leaves the interval alone rather than guessing. It only ever moves the
    interval in the safe direction, never faster.

.NOTES
    Decisions are logged to $env:TEMP\applewatch-guard.log.
    Remove with: Unregister-ScheduledTask -TaskName AppleStockWatcherGuard -Confirm:$false
#>

$ErrorActionPreference = 'Continue'

$SafeSeconds  = 120
$RecentPolls  = 20      # how many recent polls to judge on
$PollLog      = Join-Path $env:TEMP 'applewatch-local.log'
$GuardLog     = Join-Path $env:TEMP 'applewatch-guard.log'
$TaskName     = 'AppleStockWatcher'
$Register     = Join-Path $PSScriptRoot 'register_local_task.ps1'

function Write-Guard([string]$Message) {
    Add-Content -Path $GuardLog -Encoding utf8 `
        -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $interval = $task.Triggers[0].Repetition.Interval   # ISO 8601, e.g. PT1M30S
    $current = [System.Xml.XmlConvert]::ToTimeSpan($interval).TotalSeconds

    if ($current -ge $SafeSeconds) {
        # Already at or slower than the safe interval; nothing to police.
        exit 0
    }

    if (-not (Test-Path $PollLog)) {
        Write-Guard "poll log missing; leaving interval at ${current}s"
        exit 0
    }

    $recent = @(Get-Content $PollLog | Where-Object { $_ -match 'check exit' } |
                Select-Object -Last $RecentPolls)
    if ($recent.Count -lt $RecentPolls) {
        # Not enough evidence yet to judge the experiment.
        exit 0
    }

    $failures = @($recent | Where-Object { $_ -match 'exit=1' }).Count
    if ($failures -gt 0) {
        Write-Guard ("REVERT: {0} failure(s) in last {1} polls at {2}s -> {3}s" -f `
                     $failures, $RecentPolls, $current, $SafeSeconds)
        & powershell -NoProfile -ExecutionPolicy Bypass -File $Register -IntervalSeconds $SafeSeconds | Out-Null
        Write-Guard "reverted to ${SafeSeconds}s"
    } else {
        Write-Guard ("holding at {0}s, {1} recent polls all clean" -f $current, $RecentPolls)
    }
}
catch {
    # Never let the watchdog itself change behaviour by failing.
    Write-Guard ("guard error, leaving interval untouched: {0}" -f $_.Exception.GetType().Name)
    exit 0
}

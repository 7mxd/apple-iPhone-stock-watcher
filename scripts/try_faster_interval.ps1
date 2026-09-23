<#
.SYNOPSIS
    Starts the faster-polling trial, but only if the current baseline is clean.

.DESCRIPTION
    Polling faster than every 2 minutes has drawn HTTP 541 from Apple more
    than once. 90 seconds is still worth testing, because the earlier
    attempt was contaminated: it ran inside an active penalty window, and
    retry amplification was inflating the real request rate at the time.
    Both of those are now fixed, so the question is genuinely open.

    This script refuses to begin the trial unless the recent polls are
    clean. Starting a faster-polling experiment while already being
    throttled would measure the penalty, not the interval, which is exactly
    the mistake that produced the earlier bogus "90s is worse than 60s"
    conclusion.

    scripts/interval_guard.ps1 polices the trial once it starts and reverts
    to 120s at the first sign of failures.

.NOTES
    Decisions are logged to $env:TEMP\applewatch-guard.log.
#>

param(
    [int]$TrialSeconds  = 90,
    [int]$RequiredClean = 20
)

$ErrorActionPreference = 'Continue'

$PollLog  = Join-Path $env:TEMP 'applewatch-local.log'
$GuardLog = Join-Path $env:TEMP 'applewatch-guard.log'
$Register = Join-Path $PSScriptRoot 'register_local_task.ps1'

function Write-Guard([string]$Message) {
    Add-Content -Path $GuardLog -Encoding utf8 `
        -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

try {
    if (-not (Test-Path $PollLog)) {
        Write-Guard "TRIAL ABORTED: no poll log to judge the baseline from"
        exit 0
    }

    $recent = @(Get-Content $PollLog | Where-Object { $_ -match 'check exit' } |
                Select-Object -Last $RequiredClean)

    if ($recent.Count -lt $RequiredClean) {
        Write-Guard ("TRIAL ABORTED: only {0} polls available, need {1}" -f $recent.Count, $RequiredClean)
        exit 0
    }

    $failures = @($recent | Where-Object { $_ -match 'exit=1' }).Count
    if ($failures -gt 0) {
        Write-Guard ("TRIAL ABORTED: {0} failure(s) in last {1} polls, baseline not clean" -f $failures, $RequiredClean)
        exit 0
    }

    Write-Guard ("TRIAL START: {0} clean polls, trying {1}s" -f $RequiredClean, $TrialSeconds)
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Register -IntervalSeconds $TrialSeconds | Out-Null
    Write-Guard "now polling every ${TrialSeconds}s, guard will revert on any failure"
}
catch {
    Write-Guard ("TRIAL ABORTED: {0}" -f $_.Exception.GetType().Name)
    exit 0
}

<#
.SYNOPSIS
    Fast-lane local poller, complementing the GitHub Actions cron.

.DESCRIPTION
    GitHub throttles scheduled workflows hard: a "*/5 * * * *" cron was observed
    firing roughly every 3.7 hours, about 3% of the configured rate. That blind
    spot is wide enough to straddle a restock window entirely.

    This script runs the same checker locally on a short interval while the
    machine is awake. The cloud cron stays in place as the overnight safety net.

    Both runners share state through the committed state.json, so whichever
    fires first records the transition and the other stays quiet. If the sync
    fails, the worst case is a duplicate alert, never a missed one.

    Guiding rule: a git problem must never stop the stock check from running.
    Every git step is best-effort; only the check itself is load-bearing.

    NTFY_TOPIC must be set as a user environment variable. It is deliberately
    never stored in this repository, which is public.

.NOTES
    Register via scripts/register_local_task.ps1. Logs to $env:TEMP, not the repo.
#>

$ErrorActionPreference = 'Continue'

$Repo   = Split-Path -Parent $PSScriptRoot
$Python = 'C:\ProgramData\anaconda3\python.exe'
$Git    = 'C:\Program Files\Git\cmd\git.exe'
$Log    = Join-Path $env:TEMP 'applewatch-local.log'

function Write-Log {
    param([string]$Message)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $Log -Value "$stamp  $Message" -Encoding utf8
}

# Keep the log from growing without bound.
if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 1MB)) {
    $tail = Get-Content $Log -Tail 500
    Set-Content -Path $Log -Value $tail -Encoding utf8
}

if (-not $env:NTFY_TOPIC) {
    Write-Log 'SKIP: NTFY_TOPIC is not set for this user; nothing to do.'
    exit 2
}

Set-Location $Repo

# Best-effort sync. --autostash protects any uncommitted work in the tree.
& $Git pull --rebase --autostash --quiet 2>&1 | Out-Null
if (-not $?) { Write-Log 'WARN: git pull failed; continuing with local state.' }

# The load-bearing step. Runs regardless of how the sync went.
$output = & $Python -m applewatch 2>&1
$code = $LASTEXITCODE
Write-Log "check exit=$code :: $($output -join ' | ')"

# Publish the new state so the cloud runner does not re-alert on the same change.
$dirty = & $Git status --porcelain state.json
if ($dirty) {
    & $Git add state.json 2>&1 | Out-Null
    & $Git commit -m 'chore: update availability state (local) [skip ci]' 2>&1 | Out-Null
    & $Git pull --rebase --autostash --quiet 2>&1 | Out-Null
    & $Git push --quiet 2>&1 | Out-Null
    if ($?) { Write-Log 'state changed: committed and pushed.' }
    else    { Write-Log 'WARN: state committed locally but push failed; next run retries.' }
}

exit $code

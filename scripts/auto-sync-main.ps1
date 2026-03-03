param(
    [int]$IntervalSeconds = 60,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'

function Get-BranchName {
    (git branch --show-current 2>$null).Trim()
}

function Get-Head {
    param([string]$Ref)
    (git rev-parse --verify $Ref 2>$null).Trim()
}

function Sync-Main {
    $current = Get-BranchName
    if ($current -ne 'main') {
        Write-Host "[auto-sync] Switching to main from '$current'..." -ForegroundColor Yellow
        git switch main | Out-Host
    }

    git fetch origin main --quiet

    $local = Get-Head 'main'
    $remote = Get-Head 'origin/main'

    if (-not $local -or -not $remote) {
        Write-Host "[auto-sync] Unable to read local/remote HEAD." -ForegroundColor Red
        return
    }

    if ($local -eq $remote) {
        Write-Host "[auto-sync] Up to date ($local)" -ForegroundColor DarkGray
        return
    }

    Write-Host "[auto-sync] New remote commit detected. Pulling..." -ForegroundColor Cyan
    git pull --ff-only origin main | Out-Host
    $updated = Get-Head 'main'
    Write-Host "[auto-sync] Synced to $updated" -ForegroundColor Green
}

Write-Host "[auto-sync] Watching origin/main every $IntervalSeconds seconds. Ctrl+C to stop." -ForegroundColor Magenta

if ($Once) {
    Sync-Main
    exit 0
}

while ($true) {
    try {
        Sync-Main
    }
    catch {
        Write-Host "[auto-sync] Error: $($_.Exception.Message)" -ForegroundColor Red
    }

    Start-Sleep -Seconds $IntervalSeconds
}

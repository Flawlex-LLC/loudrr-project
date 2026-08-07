# Loudrr-FastAPI Tear-Down
# Run:  .\scripts\dev-stop.ps1   (from repo root)
#
# Kills anything bound to the dev ports (8000, 3000) and stops the
# docker-compose stack (postgres + redis). Leaves Docker Desktop itself
# running; close that manually if you want a fully clean state.

# This script lives in <repo>/scripts/, so the project root is one level up.
$projectRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $projectRoot "backend"

Write-Host "Stopping Loudrr-FastAPI dev stack..." -ForegroundColor Yellow

# 1. Kill the backend/frontend processes by port
foreach ($port in @(8000, 3000)) {
    $pids = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    if ($pids) {
        Write-Host "  Killing PID(s) on :$port -> $($pids -join ',')" -ForegroundColor DarkYellow
        foreach ($p in $pids) {
            try { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue } catch {}
        }
    } else {
        Write-Host "  Port $port already free." -ForegroundColor DarkGray
    }
}

# 2. Kill arq worker processes (they hold a Redis connection, not a TCP port,
#    so we identify them by process command-line)
$arqProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "arq\s+app\.tasks\.worker" }
if ($arqProcs) {
    foreach ($p in $arqProcs) {
        Write-Host "  Killing arq worker PID $($p.ProcessId)" -ForegroundColor DarkYellow
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    }
} else {
    Write-Host "  No arq worker running." -ForegroundColor DarkGray
}

# 3. Stop docker-compose services (leaves volumes intact)
Write-Host "  Stopping docker-compose stack..." -ForegroundColor DarkYellow
Push-Location $backend
docker compose stop 2>&1 | Out-String | Write-Host
Pop-Location

Write-Host ""
Write-Host "All dev services stopped." -ForegroundColor Green
Write-Host "Postgres volume preserved — re-run .\dev.ps1 to bring everything back."

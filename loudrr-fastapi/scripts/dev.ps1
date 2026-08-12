# Loudrr-FastAPI Local Development Startup
# Run:  .\scripts\dev.ps1   (from repo root)
#   or: cd scripts; .\dev.ps1
#
# Mirrors the Django reference's dev.ps1 (projects/loudrr/dev.ps1) but adapted
# for the FastAPI stack: uvicorn + arq worker instead of Django + django-q2.
#
# Opens 7 tabs in one Windows Terminal window:
#   1. Postgres  (docker compose, port 5432)
#   2. Redis     (docker compose, port 6379)
#   3. uvicorn   (FastAPI backend, port 8000)
#   4. arq       (worker - outbox drain, daily credit reset, post expiry crons)
#   5. Next.js   (frontend, port 3000)
#   6. cloudflared (tunnel: dev-api.loudrr.com -> :8000, dev-app.loudrr.com -> :3000)
#   7. shell     (interactive shell for ad-hoc queries / curl)
#
# If Windows Terminal isn't installed, falls back to separate PowerShell windows.
# Tear down with:  .\scripts\dev-stop.ps1

# This script lives in <repo>/scripts/, so the project root is one level up.
$projectRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $projectRoot "backend"
$frontend = Join-Path $projectRoot "frontend"
$venv = Join-Path $projectRoot ".venv\Scripts\Activate.ps1"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

Write-Host "Starting Loudrr-FastAPI Development Environment..." -ForegroundColor Cyan

# --- 0. Docker Desktop ---
Write-Host ""
Write-Host "[0/6] Checking Docker Desktop..." -ForegroundColor Yellow
$dockerOk = $false
try { docker info *>$null; if ($LASTEXITCODE -eq 0) { $dockerOk = $true } } catch {}
if (-not $dockerOk) {
    Write-Host "  Docker daemon not reachable. Launching Docker Desktop..." -ForegroundColor Yellow
    $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerExe) {
        Start-Process $dockerExe
    } else {
        Write-Host "  Docker Desktop not found at $dockerExe" -ForegroundColor Red
        Read-Host "Press Enter to exit"; exit 1
    }
    Write-Host "  Waiting for Docker daemon (max 90s)..." -ForegroundColor Yellow
    $waited = 0
    while ($waited -lt 90) {
        Start-Sleep -Seconds 3; $waited += 3
        try { docker info *>$null; if ($LASTEXITCODE -eq 0) { $dockerOk = $true; break } } catch {}
    }
    if (-not $dockerOk) {
        Write-Host "  Docker still not ready after 90s. Aborting." -ForegroundColor Red
        Read-Host "Press Enter to exit"; exit 1
    }
    Write-Host "  Docker is up." -ForegroundColor Green
}

# --- 1. Bring docker-compose services up in detached mode FIRST ---
Write-Host ""
Write-Host "[1/6] Bringing up Postgres + Redis via docker-compose..." -ForegroundColor Yellow
Push-Location $backend
docker compose up -d db redis 2>&1 | Out-String | Write-Host
Pop-Location

# --- 2. Define the tabs ---
# Each tab runs a long-lived command inside its own pwsh process. We pre-write
# each command as a .ps1 file in $env:TEMP so Windows Terminal's `;`-as-arg-
# separator parser doesn't choke on embedded semicolons (same trick as Django's
# dev.ps1).
$tabs = @(
    @{ Title = "Postgres :5432";  Cmd = "cd '$backend'; docker compose logs -f db" }
    @{ Title = "Redis :6379";     Cmd = "cd '$backend'; docker compose logs -f redis" }
    @{ Title = "FastAPI :8000";   Cmd = "cd '$backend'; & '$python' -m uvicorn app.main:app --port 8000 --reload" }
    @{ Title = "arq worker";      Cmd = "cd '$backend'; & '$python' -m arq app.tasks.worker.WorkerSettings" }
    @{ Title = "Next.js :3000";   Cmd = "cd '$frontend'; & 'C:\Program Files\nodejs\npm.cmd' run dev" }
    # cloudflared: exposes localhost through gyaway.loudrr.com hostnames so Telegram
    # (which can't dial 127.0.0.1) and any live tester can hit the app. Tunnel
    # `loudrr-dev` + config live at ~/.cloudflared/{config.yml, cert.pem, <uuid>.json};
    # ingress maps dev-api.loudrr.com -> :8000 and dev-app.loudrr.com -> :3000.
    @{ Title = "cloudflared";     Cmd = "cloudflared tunnel run loudrr-dev" }
    @{ Title = "shell";           Cmd = "cd '$projectRoot'; & '$venv'" }
)

# --- 3. Launch the tabs (Windows Terminal preferred, fallback to separate windows) ---
$wt = Get-Command wt.exe -ErrorAction SilentlyContinue
if ($wt -and (Get-Item $wt.Source).Length -eq 0) { $wt = $null }  # 0-byte App Execution Alias
if (-not $wt) {
    $pkg = Get-AppxPackage -Name Microsoft.WindowsTerminal -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pkg) {
        $candidate = Join-Path $pkg.InstallLocation "wt.exe"
        if (Test-Path $candidate) { $wt = @{ Source = $candidate } }
    }
}

if ($wt) {
    Write-Host ""
    Write-Host "Launching all 6 services as tabs in one Windows Terminal window..." -ForegroundColor Yellow
    $scriptDir = Join-Path $env:TEMP "loudrr-fastapi-dev-tabs"
    if (-not (Test-Path $scriptDir)) { New-Item -ItemType Directory -Path $scriptDir | Out-Null }

    $parts = @()
    for ($i = 0; $i -lt $tabs.Count; $i++) {
        $t = $tabs[$i]
        $scriptPath = Join-Path $scriptDir ("tab_{0:D2}.ps1" -f $i)
        Set-Content -Path $scriptPath -Value $t.Cmd -Encoding UTF8
        $title = $t.Title -replace '"', '\"'
        $parts += "new-tab --title `"$title`" --suppressApplicationTitle powershell -NoExit -ExecutionPolicy Bypass -File `"$scriptPath`""
    }
    $wtLine = ($parts -join " ; ")
    $batContent = "@echo off`r`n`"$($wt.Source)`" -w 0 $wtLine`r`n"
    $batPath = Join-Path $env:TEMP "loudrr-fastapi-dev-wt.bat"
    Set-Content -Path $batPath -Value $batContent -Encoding ASCII
    Start-Process cmd.exe -ArgumentList "/c", $batPath
} else {
    Write-Host ""
    Write-Host "Windows Terminal not found. Opening separate PowerShell windows..." -ForegroundColor Yellow
    foreach ($t in $tabs) {
        $wrapped = "`$Host.UI.RawUI.WindowTitle = '$($t.Title)'; $($t.Cmd)"
        Start-Process powershell -ArgumentList "-NoExit", "-Command", $wrapped
        Start-Sleep -Milliseconds 500
    }
}

Write-Host ""
Write-Host "All services starting!" -ForegroundColor Green
Write-Host ""
Write-Host "URLs (local):" -ForegroundColor Cyan
Write-Host "  Backend:           http://localhost:8000"
Write-Host "  API docs:          http://localhost:8000/docs"
Write-Host "  SQLAdmin panel:    http://localhost:8000/admin   (set ADMIN_PASSWORD in backend/.env)"
Write-Host "  Frontend:          http://localhost:3000"
Write-Host "  Admin dashboard:   http://localhost:3000/admin"
Write-Host "  Mini-app:          http://localhost:3000/app"
Write-Host ""
Write-Host "URLs (via Cloudflare tunnel - live from anywhere, incl. Telegram):" -ForegroundColor Cyan
Write-Host "  Backend:           https://dev-api.loudrr.com"
Write-Host "  Frontend:          https://dev-app.loudrr.com"
Write-Host "  Mini-app:          https://dev-app.loudrr.com/app  (set MINIAPP_URL to this in .env for real bot buttons)"
Write-Host ""
Write-Host "Notes:" -ForegroundColor Cyan
Write-Host "  - arq worker runs cron jobs: outbox drain (every minute), daily credit reset"
Write-Host "    (midnight UTC), post expiry (hourly), retry failed (hourly), cleanup (daily)."
Write-Host "  - For on-demand jobs (verification batches, tweetscout fetches) to route through"
Write-Host "    arq instead of FastAPI BackgroundTasks, set USE_TASK_QUEUE=True in backend/.env."
Write-Host ""
Write-Host "Press any key to close this launcher window..."
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')

# dev-all.ps1 - one-shot local launcher for the ENTIRE monorepo.
#
# Opens tabs for every service in one Windows Terminal window:
#   1. Postgres          (loudrr-fastapi/backend docker-compose, port 5432)
#   2. Redis             (same compose, port 6379)
#   3. FastAPI :8000     (loudrr-fastapi/backend uvicorn)
#   4. arq worker        (loudrr-fastapi/backend arq)
#   5. Next.js :3000     (loudrr-fastapi/frontend npm run dev)
#   6. Analytics :8001   (loudrr-analytics-service uvicorn)  - off by default; uncomment $spawnAnalytics
#   7. cloudflared       (tunnel: dev-api / dev-app)
#   8. shell             (interactive; venv sourced from loudrr-fastapi/.venv)
#
# The per-service dev.ps1 (loudrr-fastapi/scripts/dev.ps1) is still there and
# still works if you want to launch ONLY the fastapi stack. This top-level
# launcher is for when you're touching multiple services at once.
#
# Analytics is off by default because it has its OWN postgres on port 5432
# that clashes with the backend's postgres. Enable only if you're actively
# working on the analytics side; toggle the flag below.

$spawnAnalytics = $false   # set $true only when the backend's DB is stopped

$projectRoot = Split-Path -Parent $PSScriptRoot
$fastapiDir  = Join-Path $projectRoot 'loudrr-fastapi'
$backend     = Join-Path $fastapiDir 'backend'
$frontend    = Join-Path $fastapiDir 'frontend'
$analytics   = Join-Path $projectRoot 'loudrr-analytics-service'
$venv        = Join-Path $fastapiDir '.venv\Scripts\Activate.ps1'
$python      = Join-Path $fastapiDir '.venv\Scripts\python.exe'
$analyticsPy = Join-Path $analytics  '.venv\Scripts\python.exe'

Write-Host "Starting Loudrr monorepo dev environment..." -ForegroundColor Cyan

# --- 0. Docker Desktop ---
Write-Host ""
Write-Host "[0] Checking Docker Desktop..." -ForegroundColor Yellow
$dockerOk = $false
try { docker info *>$null; if ($LASTEXITCODE -eq 0) { $dockerOk = $true } } catch {}
if (-not $dockerOk) {
    $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerExe) {
        Start-Process $dockerExe
        Write-Host "  Waiting for Docker daemon..." -ForegroundColor Yellow
        $waited = 0
        while ($waited -lt 90) {
            Start-Sleep -Seconds 3; $waited += 3
            try { docker info *>$null; if ($LASTEXITCODE -eq 0) { $dockerOk = $true; break } } catch {}
        }
    }
    if (-not $dockerOk) {
        Write-Host "  Docker not ready. Aborting." -ForegroundColor Red
        Read-Host "Press Enter to exit"; exit 1
    }
}

# --- 1. Start backend's Postgres + Redis ---
Write-Host ""
Write-Host "[1] Postgres + Redis..." -ForegroundColor Yellow
Push-Location $backend
docker compose up -d db redis 2>&1 | Out-String | Write-Host
Pop-Location

# --- 2. Compose the tab list ---
$tabs = @(
    @{ Title = "Postgres :5432";  Cmd = "cd '$backend'; docker compose logs -f db" }
    @{ Title = "Redis :6379";     Cmd = "cd '$backend'; docker compose logs -f redis" }
    @{ Title = "FastAPI :8000";   Cmd = "cd '$backend'; & '$python' -m uvicorn app.main:app --port 8000 --reload" }
    @{ Title = "arq worker";      Cmd = "cd '$backend'; & '$python' -m arq app.tasks.worker.WorkerSettings" }
    @{ Title = "Next.js :3000";   Cmd = "cd '$frontend'; & 'C:\Program Files\nodejs\npm.cmd' run dev" }
)

if ($spawnAnalytics) {
    if (Test-Path $analyticsPy) {
        $tabs += @{ Title = "Analytics :8001"; Cmd = "cd '$analytics'; & '$analyticsPy' -m uvicorn app.main:app --port 8001 --reload" }
    } else {
        Write-Host "  Analytics venv missing (expected at $analyticsPy). Skipping analytics tab." -ForegroundColor Yellow
    }
}

$tabs += @{ Title = "cloudflared";     Cmd = "cloudflared tunnel run loudrr-dev" }
$tabs += @{ Title = "shell";           Cmd = "cd '$projectRoot'; & '$venv'" }

# --- 3. Launch tabs (Windows Terminal preferred, fallback to separate windows) ---
$wt = Get-Command wt.exe -ErrorAction SilentlyContinue
if ($wt -and (Get-Item $wt.Source).Length -eq 0) { $wt = $null }
if (-not $wt) {
    $pkg = Get-AppxPackage -Name Microsoft.WindowsTerminal -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pkg) {
        $candidate = Join-Path $pkg.InstallLocation "wt.exe"
        if (Test-Path $candidate) { $wt = @{ Source = $candidate } }
    }
}

if ($wt) {
    Write-Host ""
    Write-Host "Launching $($tabs.Count) tabs in one Windows Terminal window..." -ForegroundColor Yellow
    $scriptDir = Join-Path $env:TEMP "loudrr-monorepo-dev-tabs"
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
    $batPath = Join-Path $env:TEMP "loudrr-monorepo-dev-wt.bat"
    Set-Content -Path $batPath -Value $batContent -Encoding ASCII
    Start-Process cmd.exe -ArgumentList "/c", $batPath
} else {
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
Write-Host "  SQLAdmin:          http://localhost:8000/admin"
Write-Host "  Frontend:          http://localhost:3000"
Write-Host "  Admin dashboard:   http://localhost:3000/admin"
Write-Host "  Mini-app:          http://localhost:3000/app"
if ($spawnAnalytics) { Write-Host "  Analytics:         http://localhost:8001" }
Write-Host ""
Write-Host "URLs (via Cloudflare tunnel - live from anywhere, incl. Telegram):" -ForegroundColor Cyan
Write-Host "  Backend:           https://dev-api.loudrr.com"
Write-Host "  Frontend:          https://dev-app.loudrr.com"
Write-Host ""
Write-Host "Press any key to close this launcher window..."
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')

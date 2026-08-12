# finish-cleanup.ps1 - the FINAL two ops that Claude couldn't do from its
# active session (Windows blocks renaming/deleting a dir a process is inside).
#
# WHAT THIS DOES:
#   1. Deletes the now-empty C:\Users\mamoo\projects\loudrr-fastapi\ dir
#      (Claude already migrated .venv, .mypy_cache, coming-soon/.git out).
#   2. Renames the Claude Code memory dirs so the historical memory
#      (transcripts, agents, saved skills) is discoverable when you open
#      the new paths as workspaces:
#        c--Users-mamoo-projects-loudrr-fastapi
#          -> c--Users-mamoo-projects-loudrr-project
#        c--Users-mamoo-projects-loudrr-analytics-service
#          -> c--Users-mamoo-projects-loudrr-project-loudrr-analytics-service
#
# THE TWO STEPS ARE INDEPENDENT: if step 1 fails (dir locked by an editor
# or an active Claude Code session), step 2 still runs.
#
# BEFORE YOU RUN:
#   1. Close every Claude Code window / VS Code / editor / terminal pointed
#      at loudrr-fastapi. (Windows blocks deleting an in-use dir.)
#   2. Open a fresh PowerShell window from the Start menu.
#   3. cd C:\ (don't stand inside any target dir).
#   4. Run:
#        powershell -ExecutionPolicy Bypass -File 'C:\Users\mamoo\projects\loudrr-project\scripts\finish-cleanup.ps1'

# Continue on non-fatal errors so both steps get a chance to run.
$ErrorActionPreference = 'Continue'

$oldFastapi = 'C:\Users\mamoo\projects\loudrr-fastapi'
$claudeDir  = "$env:USERPROFILE\.claude\projects"

$mem_fastapi_old   = Join-Path $claudeDir 'c--Users-mamoo-projects-loudrr-fastapi'
$mem_analytics_old = Join-Path $claudeDir 'c--Users-mamoo-projects-loudrr-analytics-service'
$mem_project_new   = Join-Path $claudeDir 'c--Users-mamoo-projects-loudrr-project'
$mem_nested_new    = Join-Path $claudeDir 'c--Users-mamoo-projects-loudrr-project-loudrr-analytics-service'


function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }

# Track outcomes so the final report is honest even if a step fails.
$results = @{ delete = 'skipped'; rename_fastapi_mem = 'skipped'; rename_analytics_mem = 'skipped' }


# ---- Safety: refuse to run if standing inside a target dir ----
$here = (Get-Location).Path
foreach ($d in @($oldFastapi, $mem_fastapi_old, $mem_analytics_old)) {
    if ($here.StartsWith($d, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Host "!!  You're inside $d - cd OUT (e.g. cd C:\) and rerun." -ForegroundColor Red
        exit 1
    }
}


# ---- 1. Delete old loudrr-fastapi dir ----
Info "Step 1: Delete old $oldFastapi ..."
if (-not (Test-Path $oldFastapi)) {
    Warn "  already gone - nothing to delete"
    $results.delete = 'already_gone'
} else {
    try {
        Remove-Item -LiteralPath $oldFastapi -Recurse -Force -ErrorAction Stop
        Ok  "  deleted $oldFastapi"
        $results.delete = 'ok'
    } catch {
        Warn "  DELETE FAILED: $($_.Exception.Message)"
        Warn "  Something still has an open handle on the dir (an editor,"
        Warn "  Claude Code session, terminal cwd'd inside, or Explorer)."
        Warn "  Step 2 (memory rename) will still run below."
        $results.delete = 'failed_locked'
    }
}


# ---- 2. Rename Claude Code memory dirs (independent of step 1) ----
Write-Host ""
Info "Step 2: Rename Claude Code memory dirs ..."

if (Test-Path $mem_fastapi_old) {
    try {
        if (Test-Path $mem_project_new) {
            Warn "  $mem_project_new already exists - merging old memory into it as _legacy_from_loudrr_fastapi/"
            $legacy = Join-Path $mem_project_new '_legacy_from_loudrr_fastapi'
            Move-Item -LiteralPath $mem_fastapi_old -Destination $legacy -ErrorAction Stop
            Ok  "  merged fastapi memory as legacy"
            $results.rename_fastapi_mem = 'merged_as_legacy'
        } else {
            Rename-Item -LiteralPath $mem_fastapi_old -NewName 'c--Users-mamoo-projects-loudrr-project' -ErrorAction Stop
            Ok  "  renamed loudrr-fastapi memory -> loudrr-project memory"
            $results.rename_fastapi_mem = 'ok'
        }
    } catch {
        Warn "  RENAME FAILED: $($_.Exception.Message)"
        Warn "  A Claude Code session probably still has its transcript file open."
        Warn "  Close ALL Claude Code windows and rerun."
        $results.rename_fastapi_mem = 'failed_locked'
    }
} else {
    Warn "  no loudrr-fastapi memory dir at $mem_fastapi_old (probably already renamed)"
    $results.rename_fastapi_mem = 'already_gone'
}

if (Test-Path $mem_analytics_old) {
    try {
        if (Test-Path $mem_nested_new) {
            Warn "  $mem_nested_new already exists - merging as _legacy_from_top_level/"
            $legacy = Join-Path $mem_nested_new '_legacy_from_top_level'
            Move-Item -LiteralPath $mem_analytics_old -Destination $legacy -ErrorAction Stop
            Ok  "  merged analytics memory as legacy"
            $results.rename_analytics_mem = 'merged_as_legacy'
        } else {
            Rename-Item -LiteralPath $mem_analytics_old -NewName 'c--Users-mamoo-projects-loudrr-project-loudrr-analytics-service' -ErrorAction Stop
            Ok  "  renamed loudrr-analytics-service memory -> loudrr-project-loudrr-analytics-service memory"
            $results.rename_analytics_mem = 'ok'
        }
    } catch {
        Warn "  RENAME FAILED: $($_.Exception.Message)"
        $results.rename_analytics_mem = 'failed_locked'
    }
} else {
    Warn "  no loudrr-analytics-service memory dir at $mem_analytics_old (probably already renamed)"
    $results.rename_analytics_mem = 'already_gone'
}


# ---- 3. Final report ----
Write-Host ""
Write-Host "=== RESULTS ===" -ForegroundColor Cyan
Write-Host "  delete old loudrr-fastapi/  : $($results.delete)"
Write-Host "  rename fastapi memory dir   : $($results.rename_fastapi_mem)"
Write-Host "  rename analytics memory dir : $($results.rename_analytics_mem)"

Write-Host ""
Write-Host "=== C:\Users\mamoo\projects\ ===" -ForegroundColor Cyan
Get-ChildItem 'C:\Users\mamoo\projects\' -Directory | Where-Object { $_.Name -like 'loudrr*' } | Format-Table Name

Write-Host "=== Claude memory dirs ($claudeDir) ===" -ForegroundColor Cyan
Get-ChildItem $claudeDir -Directory | Where-Object { $_.Name -like '*loudrr*' } | Format-Table Name

Write-Host ""
$anyLocked = ($results.delete -eq 'failed_locked') -or `
             ($results.rename_fastapi_mem -eq 'failed_locked') -or `
             ($results.rename_analytics_mem -eq 'failed_locked')
if ($anyLocked) {
    Write-Host "One or more steps failed because something held an open handle." -ForegroundColor Yellow
    Write-Host "To finish: close ALL Claude Code windows, VS Code, terminals, Explorer" -ForegroundColor Yellow
    Write-Host "windows pointed at loudrr-fastapi or the memory dirs, then rerun this script." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Which processes might be holding it? Run this in an admin PowerShell:" -ForegroundColor Yellow
    Write-Host "  Get-Process | Where-Object { `$_.Path -like '*loudrr*' -or (`$_.Modules 2>`$null | Where-Object { `$_.FileName -like '*loudrr*' }) }" -ForegroundColor DarkGray
} else {
    Write-Host "DONE. loudrr-project is the only project dir; Claude memory follows." -ForegroundColor Green
    Write-Host "Open Claude Code in C:\Users\mamoo\projects\loudrr-project" -ForegroundColor Green
    Write-Host "(or in the loudrr-analytics-service subdir for that workspace)."
}

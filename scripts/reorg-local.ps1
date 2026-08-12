# reorg-local.ps1 - one-shot local reorganization to match the GitHub monorepo layout.
#
# WHY THIS EXISTS:
# When Claude built the monorepo, it created a FRESH `loudrr-project/` dir
# alongside `loudrr-fastapi/` and `loudrr-analytics-service/` - GitHub is
# already correct, but the local tree still has three parallel folders and
# Claude Code's memory is keyed to the ORIGINAL folder paths. This script
# folds everything into ONE `loudrr-project/` dir + moves Claude memory dirs
# so opening either `loudrr-project/` or `loudrr-project/loudrr-analytics-service/`
# in a fresh Claude session picks up the historical memory.
#
# =============================================================================
# BEFORE YOU RUN:
#   1. CLOSE Claude Code / VS Code / any editor pointed at the old dirs
#      (Windows blocks renaming an in-use directory)
#   2. Open a NEW PowerShell window (not one that was cd'd into any of the
#      three project dirs - start it fresh from Start menu)
#   3. cd into ANY dir OUTSIDE the three project dirs, e.g. `cd C:\`
#   4. Run:   powershell -ExecutionPolicy Bypass -File .\reorg-local.ps1
#      (from the fresh loudrr-project/scripts/ folder, or absolute path)
# =============================================================================

$ErrorActionPreference = 'Stop'

$projects  = 'C:\Users\mamoo\projects'
$claudeDir = "$env:USERPROFILE\.claude\projects"

$old_fastapi   = Join-Path $projects 'loudrr-fastapi'
$old_analytics = Join-Path $projects 'loudrr-analytics-service'
$fresh_monorepo = Join-Path $projects 'loudrr-project'

# Claude memory dir encodings (colon -> dash, backslash -> dash)
$mem_fastapi_old   = Join-Path $claudeDir 'c--Users-mamoo-projects-loudrr-fastapi'
$mem_analytics_old = Join-Path $claudeDir 'c--Users-mamoo-projects-loudrr-analytics-service'
$mem_project_new   = Join-Path $claudeDir 'c--Users-mamoo-projects-loudrr-project'
$mem_nested_new    = Join-Path $claudeDir 'c--Users-mamoo-projects-loudrr-project-loudrr-analytics-service'


function Info($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg)  { Write-Host "!!  $msg" -ForegroundColor Yellow }
function Ok($msg)    { Write-Host "OK  $msg" -ForegroundColor Green }
function Fatal($msg) { Write-Host "!!  $msg" -ForegroundColor Red; exit 1 }


# =============================================================================
# 0. Sanity checks - refuse to run in unsafe conditions
# =============================================================================
Info "Sanity checks..."
if (-not (Test-Path $old_fastapi))    { Fatal "$old_fastapi doesn't exist. Already reorganized?" }
if (-not (Test-Path $old_analytics))  { Fatal "$old_analytics doesn't exist. Already reorganized?" }
if (-not (Test-Path $fresh_monorepo)) { Fatal "$fresh_monorepo (fresh copy) doesn't exist. Wrong state." }

# Refuse to run if the current process is inside any of the target dirs.
$here = (Get-Location).Path
foreach ($d in @($old_fastapi, $old_analytics, $fresh_monorepo)) {
    if ($here.StartsWith($d, [StringComparison]::OrdinalIgnoreCase)) {
        Fatal "You're standing inside $d - cd OUT (e.g. cd C:\) and rerun."
    }
}
Ok "Not standing in any target dir."

# Warn about locked files inside old_fastapi (best-effort - Windows doesn't
# expose "is file open" cleanly, but we can heuristically check for common
# lock files like node_modules/.package-lock.json being writable).
try {
    $probe = Join-Path $old_fastapi '.reorg_probe'
    New-Item -ItemType File -Path $probe -Force | Out-Null
    Remove-Item $probe -Force
    Ok "Old loudrr-fastapi is writable."
} catch {
    Fatal "Can't write to $old_fastapi - a process still has it locked. Close all editors + Claude Code windows and retry."
}


# =============================================================================
# 1. Save the fresh monorepo's TOP-LEVEL FILES (README, docs, script, .gitignore)
#    to a temp dir. We'll fold them into the reorganized layout after renaming.
# =============================================================================
Info "Snapshotting top-level files from the fresh monorepo..."
$snapshot = Join-Path $env:TEMP "loudrr-reorg-snapshot-$(Get-Random)"
New-Item -ItemType Directory -Path $snapshot | Out-Null

$topFiles = @(
    'README.md',
    'MIGRATION.md',
    'COOLIFY_DEPLOY.md',
    '.gitignore'
)
foreach ($f in $topFiles) {
    $src = Join-Path $fresh_monorepo $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $snapshot $f) -Force }
}
# Also save the scripts/ dir contents from the fresh monorepo (coolify-migrate.sh, reorg-local.ps1 itself)
$srcScripts = Join-Path $fresh_monorepo 'scripts'
if (Test-Path $srcScripts) {
    Copy-Item $srcScripts (Join-Path $snapshot 'scripts_top_level') -Recurse -Force
}
# And the fresh .git - it has the correct remote + monorepo commits (init, docs, coolify)
$srcGit = Join-Path $fresh_monorepo '.git'
if (Test-Path $srcGit) {
    Copy-Item $srcGit (Join-Path $snapshot 'freshgit') -Recurse -Force
}
Ok "Snapshot at $snapshot"


# =============================================================================
# 2. Delete the fresh monorepo (its useful bits are now in the snapshot)
# =============================================================================
Info "Deleting the fresh (memory-less) $fresh_monorepo..."
try {
    Remove-Item -Recurse -Force $fresh_monorepo
    Ok "Deleted."
} catch {
    Fatal "Delete failed: $_"
}


# =============================================================================
# 3. Rename the REAL loudrr-fastapi dir to loudrr-project
#    This preserves .venv, .claude/*, .git (with all local commits), everything.
# =============================================================================
Info "Renaming $old_fastapi -> loudrr-project (preserves .venv, .git, .claude, etc.)..."
try {
    Rename-Item -Path $old_fastapi -NewName 'loudrr-project'
    Ok "Renamed. Old .venv / .git / .claude preserved inside."
} catch {
    Fatal "Rename failed (probably a file lock): $_"
}


# =============================================================================
# 4. Restructure inside loudrr-project/
#    Everything currently at root becomes contents of loudrr-project/loudrr-fastapi/
#    EXCEPT: .git, .venv, .claude, coming-soon (top-level files handled later)
# =============================================================================
Info "Reorganizing internals..."
Set-Location $fresh_monorepo   # $fresh_monorepo is now the renamed dir

$serviceDir = Join-Path $fresh_monorepo 'loudrr-fastapi'
New-Item -ItemType Directory -Path $serviceDir -Force | Out-Null

# Move backend/, frontend/, scripts/, docs/ into loudrr-fastapi/ subdir.
# We're going to overwrite the fresh .git afterwards - for now, plain
# Move-Item. Rename detection isn't preserved either way (fresh .git is
# stateless w.r.t. the flat tree).
$dirsToMove = @('backend', 'frontend', 'scripts', 'docs')
foreach ($d in $dirsToMove) {
    if (Test-Path (Join-Path $fresh_monorepo $d)) {
        Move-Item -Path (Join-Path $fresh_monorepo $d) -Destination $serviceDir
        Ok "moved $d -> loudrr-fastapi/$d"
    }
}

# Move the fastapi .venv INTO loudrr-fastapi/ so dev.ps1's $projectRoot lookup
# (which resolves .venv relative to itself) finds it. Directly moving a venv
# on the same filesystem doesn't break `python.exe` (which is what dev.ps1
# uses), only Activate scripts - but those bake absolute paths anyway and
# get overwritten by pip/python on next use.
$venvSrc = Join-Path $fresh_monorepo '.venv'
$venvDst = Join-Path $serviceDir '.venv'
if (Test-Path $venvSrc) {
    Info "Moving fastapi .venv (~200MB) into loudrr-fastapi/ ..."
    Move-Item -Path $venvSrc -Destination $venvDst
    Ok "moved .venv -> loudrr-fastapi/.venv"
} else {
    Warn "no .venv found at $venvSrc (recreate with: py -m venv loudrr-fastapi\\.venv)"
}

# The OLD loudrr-fastapi/README.md and .gitignore are inside serviceDir now, along
# with the code - that's the service-level README. The top-level (monorepo) README
# comes from the snapshot in step 6.
if (Test-Path (Join-Path $fresh_monorepo 'README.md'))  { Move-Item (Join-Path $fresh_monorepo 'README.md')  (Join-Path $serviceDir 'README.md')  -Force }
if (Test-Path (Join-Path $fresh_monorepo '.gitignore')) { Move-Item (Join-Path $fresh_monorepo '.gitignore') (Join-Path $serviceDir '.gitignore') -Force }

# Move project-scratch dirs from fastapi tooling INTO the service subdir
# (they're specifically from running the fastapi tests / typecheck).
# NOTE: .claude/ INTENTIONALLY stays at loudrr-project/ root so opening the
# monorepo as a Claude Code workspace keeps its agent/skills/plugins config
# discoverable at the workspace root.
$scratchToMove = @('.mypy_cache', '.pytest_cache', '.ruff_cache', '.hypothesis')
foreach ($s in $scratchToMove) {
    $src = Join-Path $fresh_monorepo $s
    if (Test-Path $src) {
        Move-Item -Path $src -Destination (Join-Path $serviceDir $s)
        Ok "moved $s -> loudrr-fastapi/$s"
    }
}


# =============================================================================
# 5. Move loudrr-analytics-service into loudrr-project/ as a sibling of
#    loudrr-fastapi/, deleting its own .git so the parent repo owns everything.
# =============================================================================
Info "Moving loudrr-analytics-service into monorepo..."
$analyticsDest = Join-Path $fresh_monorepo 'loudrr-analytics-service'
Move-Item -Path $old_analytics -Destination $analyticsDest
Ok "moved."

$nestedGit = Join-Path $analyticsDest '.git'
if (Test-Path $nestedGit) {
    Remove-Item -Recurse -Force $nestedGit
    Ok "deleted nested .git (parent repo takes ownership)"
}


# =============================================================================
# 6. Fold in the snapshot: top-level README/MIGRATION/COOLIFY_DEPLOY/.gitignore
#    + top-level scripts + the correct .git (fresh monorepo commits + remote)
# =============================================================================
Info "Restoring top-level monorepo files + .git..."

# Delete the OLD .git first (points at the flat structure). The snapshot .git
# is fresh and matches GitHub main.
$oldGit = Join-Path $fresh_monorepo '.git'
if (Test-Path $oldGit) {
    Remove-Item -Recurse -Force $oldGit
    Ok "removed old flat-tree .git (local history preserved on archive/pre-monorepo-fastapi branch)"
}
Copy-Item (Join-Path $snapshot 'freshgit') $oldGit -Recurse -Force
Ok "restored fresh monorepo .git (has main = c01e8cf, matches GitHub)"

foreach ($f in $topFiles) {
    $src = Join-Path $snapshot $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $fresh_monorepo $f) -Force }
}
Ok "restored top-level README / MIGRATION.md / COOLIFY_DEPLOY.md / .gitignore"

# Top-level scripts (coolify-migrate.sh, reorg-local.ps1 itself)
$topScriptsDest = Join-Path $fresh_monorepo 'scripts'
if (Test-Path (Join-Path $snapshot 'scripts_top_level')) {
    # loudrr-fastapi/scripts already exists (dev.ps1 etc.), so the top-level
    # scripts go to /scripts/ (parallel).
    if (-not (Test-Path $topScriptsDest)) {
        New-Item -ItemType Directory -Path $topScriptsDest | Out-Null
    }
    Get-ChildItem (Join-Path $snapshot 'scripts_top_level') | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $topScriptsDest $_.Name) -Recurse -Force
    }
    Ok "restored top-level /scripts (coolify-migrate.sh + this reorg script)"
}


# =============================================================================
# 7. Rename Claude Code memory dirs to match the new paths
# =============================================================================
Info "Renaming Claude Code memory dirs so history is preserved..."

if (Test-Path $mem_fastapi_old) {
    if (Test-Path $mem_project_new) {
        Warn "$mem_project_new already exists - moving old memory INSIDE as legacy/"
        $legacy = Join-Path $mem_project_new 'legacy_c--Users-mamoo-projects-loudrr-fastapi'
        Move-Item $mem_fastapi_old $legacy
    } else {
        Rename-Item -Path $mem_fastapi_old -NewName 'c--Users-mamoo-projects-loudrr-project'
        Ok "loudrr-fastapi memory -> loudrr-project memory"
    }
} else {
    Warn "no loudrr-fastapi memory dir found at $mem_fastapi_old"
}

if (Test-Path $mem_analytics_old) {
    if (Test-Path $mem_nested_new) {
        Warn "$mem_nested_new already exists - moving old memory INSIDE as legacy/"
        $legacy = Join-Path $mem_nested_new 'legacy_c--Users-mamoo-projects-loudrr-analytics-service'
        Move-Item $mem_analytics_old $legacy
    } else {
        Rename-Item -Path $mem_analytics_old -NewName 'c--Users-mamoo-projects-loudrr-project-loudrr-analytics-service'
        Ok "loudrr-analytics-service memory -> loudrr-project-loudrr-analytics-service memory"
    }
} else {
    Warn "no loudrr-analytics-service memory dir found at $mem_analytics_old"
}


# =============================================================================
# 8. Cleanup snapshot
# =============================================================================
Info "Cleaning snapshot dir..."
Remove-Item -Recurse -Force $snapshot
Ok "removed $snapshot"


# =============================================================================
# 9. Final sanity: layout summary + git status
# =============================================================================
Info "Final layout:"
Get-ChildItem $fresh_monorepo -Depth 1 | Where-Object { $_.PSIsContainer -or $_.Name -in @('README.md','MIGRATION.md','COOLIFY_DEPLOY.md','.gitignore') } | Sort-Object PSIsContainer, Name | Format-Table Mode, LastWriteTime, Name

Info "Git status:"
Set-Location $fresh_monorepo
git status --short | Select-Object -First 40
git log --oneline -3

Write-Host ""
Write-Host "REORG COMPLETE." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps (do these YOURSELF, script won't push):" -ForegroundColor Cyan
Write-Host "  1. cd $fresh_monorepo" -ForegroundColor White
Write-Host "  2. Review 'git status' - you'll see all the moved files as new (fresh .git doesn't know about them)" -ForegroundColor White
Write-Host "  3. git add -A" -ForegroundColor White
Write-Host "  4. git commit -m 'reorg: local layout matches monorepo (loudrr-fastapi/ + loudrr-analytics-service/ subdirs)'" -ForegroundColor White
Write-Host "  5. git push origin main" -ForegroundColor White
Write-Host ""
Write-Host "Then reopen Claude Code in $fresh_monorepo - memory is preserved." -ForegroundColor Cyan

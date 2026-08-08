# Morning handoff — 2026-08-08 overnight work

Everything below is committed LOCALLY only. **Nothing pushed to GitHub** (per
your ask). Two commits ahead of `origin/main`:
```
61ccfe0 quality: tier fallback tests + mypy fixes + top-level dev-all.ps1 + reorg script
c01e8cf docs: COOLIFY_DEPLOY.md — handoff after Coolify API provisioning   <- already pushed earlier
```
(The 61ccfe0 one is the new overnight commit; c01e8cf was your last push before sleep.)

---

## Do this first thing when you wake up

### Step 1 — Local folder reorg (5 minutes)

Your local still has 3 folders (`loudrr-fastapi/`, `loudrr-analytics-service/`,
`loudrr-project/`) but GitHub is the correct single monorepo. To consolidate
locally while preserving Claude Code's memory for both workspaces:

1. **Close ALL editors + Claude Code windows** that have any of those three
   folders open. (Windows blocks renaming an in-use dir.)
2. Open a **fresh** PowerShell (Start menu → PowerShell, don't reuse an
   existing terminal).
3. `cd C:\` (do NOT stand inside any of the three project dirs).
4. Run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File 'C:\Users\mamoo\projects\loudrr-project\scripts\reorg-local.ps1'
   ```
5. When it finishes:
   ```powershell
   cd C:\Users\mamoo\projects\loudrr-project
   git status                # sanity check
   git add -A
   git commit -m "reorg: local layout matches monorepo"
   git push origin main
   ```
6. Reopen Claude Code in `C:\Users\mamoo\projects\loudrr-project`. Your
   fastapi-side memory is preserved (the script renamed the `.claude/projects/`
   memory dir to match the new path). To work only on analytics, open Claude
   Code in `C:\Users\mamoo\projects\loudrr-project\loudrr-analytics-service`
   — that memory was also moved to match the new nested path.

The script has safety checks (refuses to run if you're standing inside one
of the target dirs, refuses if any file is locked). If it aborts, close more
editors and rerun.

### Step 2 — Push the overnight commit + review

The `61ccfe0` commit is local-only right now. After the reorg script's
`git push origin main` above, both commits land together.

### Step 3 — Coolify: finish the deploy handoff (COOLIFY_DEPLOY.md § "Before you hit deploy")

Reminder: the 5 apps + Postgres + Redis + 65 env vars are already provisioned
on `server1.flawlex.co`. You just need to:
- Attach real domains (api.loudrr.com, app.loudrr.com)
- Update 4 placeholder env vars in backend+worker (SITE_URL, X_OAUTH_CALLBACK_URL,
  MINIAPP_URL, CORS_ALLOWED_ORIGINS)
- Set `ENVIRONMENT=prod` (flips the prod-guard on)
- Force-rebuild loudrr-frontend so Next bakes BACKEND_ORIGIN into the bundle
- Click Deploy in the right order (backend → analytics-api → frontend → worker → analytics-scrape)

### Step 4 — Vercel: swap coming-soon to the main branch (MIGRATION.md § 2)

- Production Branch: `landing` → `main`
- Root Directory: `coming-soon`
- Redeploy

---

## What I did while you slept

### Tests added (tier.py coverage: 45% → ~90%)

`backend/tests/test_tier_load_from_settings.py` — 6 tests covering all fallback
paths of `load_tiers_from_settings()`:
- Full valid override → TIERS rebuilt in place, ordering preserved
- Missing THRESHOLD row → returns False, warns, keeps hardcoded defaults
- Missing MULTIPLIER row → same fallback
- Invalid type (garbage string) → catches ValueError, keeps defaults
- Out-of-order settings → always sorted highest-threshold-first
- ANON threshold forced to 0 even if a bogus row exists

All 6 pass. Snapshot-and-restore pattern prevents test-order-dependent flakes.

### Type-check clean everywhere

- **mypy**: 89 source files, 0 errors. Fixed 2 warnings:
  - `app/api/admin.py:324` — Optional narrowing (`if row is not None`)
  - `app/services/tier.py:94` — `int(str(x))` cast for the sentinel `object` type
- **tsc --noEmit** on both `loudrr-fastapi/frontend/` and `coming-soon/` → exit 0

### E2E telegram test — verified live

Reset your waitlist entry in the DB, POST /waitlist/register/ with real
`telegram_id=6451704338` + only `x_link` (no email — proves the email removal
worked end-to-end). Watched the outbox: `status: pending → sent`, and your
Telegram inbox got the waitlist DM. Full flow works prod-shape.

### New scripts

- **`scripts/reorg-local.ps1`** — the one you'll run in Step 1 above.
- **`scripts/dev-all.ps1`** — monorepo-root dev launcher. Spawns 7 Windows
  Terminal tabs at once (Postgres, Redis, uvicorn, arq, Next, cloudflared,
  shell). Analytics tab is opt-in via a flag at the top of the script
  because its Postgres would clash with the backend's on port 5432.
  You still have the per-service `loudrr-fastapi/scripts/dev.ps1` if you
  only want the fastapi stack.

### Full test pass counts (verified from the new monorepo location)

| Suite | Count | Status |
|---|---|---|
| backend pytest | 421 tests | 421 passed, 0 failed, 89% coverage |
| analytics pytest (unit) | 45 tests | 41 passed, 4 skipped (need DB), 0 failed |
| backend mypy | 89 files | 0 errors |
| frontend tsc --noEmit | — | exit 0 |
| coming-soon tsc --noEmit | — | exit 0 |
| E2E telegram waitlist_submitted | 1 flow | outbox → sent, DM received |

---

## Things I didn't touch (your call in the morning)

- **`git push origin main`** — I have 1 unpushed commit (`61ccfe0`) waiting.
- **Coolify deploys** — apps + DBs + env vars are provisioned but not yet
  deployed (see COOLIFY_DEPLOY.md § "Before you hit deploy").
- **Vercel reconfig** for coming-soon (see MIGRATION.md § 2).
- **Local folder consolidation** — needs your fresh terminal per Step 1
  above (I can't do it in this session because renaming an in-use dir on
  Windows fails).

---

## Where to find everything

- `README.md` — monorepo overview
- `MIGRATION.md` — migration handoff (Coolify + Vercel + local cleanup)
- `COOLIFY_DEPLOY.md` — Coolify infra state + pre-deploy checklist
- `scripts/reorg-local.ps1` — local folder reorg script (Step 1 above)
- `scripts/dev-all.ps1` — monorepo dev launcher
- `scripts/coolify-migrate.sh` — bulk repoint script (for future rename ops)
- `loudrr-fastapi/scripts/dev.ps1` — fastapi-only dev launcher (per-service)

Good morning — everything's ready.

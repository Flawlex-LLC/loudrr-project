# Monorepo migration — handoff checklist

Migration executed on 2026-08-07. Everything committed + pushed. The rest is
UI / infra steps only you can do — this doc is the exact tick-through list.

## What's done (in git + on GitHub)

- [x] Old repos preserved: `Flawlex-LLC/loudrr-project@archive/pre-monorepo-fastapi`
      (loudrr-fastapi tip, incl. 12 pre-migration commits) and
      `Flawlex-LLC/loudrr-analytics-service@archive/pre-monorepo-analytics`
      (analytics tip + WIP snapshot). Nothing is truly lost.
- [x] GitHub repo renamed: `loudrr-core` → `loudrr-project`.
- [x] New monorepo pushed to `Flawlex-LLC/loudrr-project@main` (single init
      commit `5093d15`) with the three subdirs (`loudrr-fastapi/`,
      `loudrr-analytics-service/`, `coming-soon/`).
- [x] Local remote URL updated in the source `loudrr-fastapi/` checkout.
- [x] Verified: 415/415 pytest green from the new location, analytics + coming-soon
      import + build clean.

## What YOU need to do (order matters)

### 1. Coolify — repoint every application (~5 min)

Every app's git URL + `base_directory` needs updating. Two options:

**A. Scripted (recommended)** — one bash script hits Coolify's API:
1. Open `scripts/coolify-migrate.sh` in this repo.
2. In the Coolify UI: **Keys & Tokens** → create a token with **write + deploy**
   permissions. Copy the `<id>|<secret>` string.
3. Get every app's UUID by running the script with `--list`:
   ```bash
   COOLIFY_HOST=https://your-coolify-host \
   COOLIFY_TOKEN='<id>|<secret>' \
   bash scripts/coolify-migrate.sh --list
   ```
4. Fill in the `APPS` array in the script with each app's UUID + its new
   base_directory (comments show the mapping). Uncomment those lines.
5. Run the script:
   ```bash
   COOLIFY_HOST=https://your-coolify-host \
   COOLIFY_TOKEN='<id>|<secret>' \
   bash scripts/coolify-migrate.sh
   ```
6. Watch each app's **Deployments** tab in the Coolify UI. A failed rebuild
   leaves the previous container running on the old image, so nothing goes
   dark until the new build succeeds.

**B. UI (if the script fails)** — per app:
- **Configuration → General → Source** → change **Repository URL** to
  `https://github.com/Flawlex-LLC/loudrr-project` → **Save**.
- **Configuration → Build** → change **Base Directory**:
  - Backend:  `/loudrr-fastapi/backend`
  - Frontend: `/loudrr-fastapi/frontend`
  - Analytics (all RUN_MODE apps): `/loudrr-analytics-service`
- **Save** → **Deploy → Force Rebuild**.

### 2. Vercel — reconfig coming-soon (~2 min)

The coming-soon page moved from the `landing` branch to `main` under a subfolder.
In the Vercel project settings for coming-soon:
1. **Settings → Git** → change the **Production Branch** from `landing` to `main`.
   (Repo URL should auto-update via GitHub's rename redirect; if not, change it
   manually to `Flawlex-LLC/loudrr-project`.)
2. **Settings → Build & Deployment** → set **Root Directory** to `coming-soon`.
3. Trigger a redeploy: **Deployments → latest → Redeploy → Use existing Build Cache: no**.
4. Verify at your dev URL (e.g. `https://loudrr.com`) that the landing loads.
5. Once verified: delete the `landing` branch from GitHub if you want (or keep
   it as an archive; it's harmless).

### 3. Archive the old analytics-service GitHub repo (~30s)

The old `Flawlex-LLC/loudrr-analytics-service` should go read-only so no one
accidentally pushes to it. Run:
```bash
gh repo archive Flawlex-LLC/loudrr-analytics-service --yes
```

### 4. Local dev — swap to the new dir (~1 min)

Both `.venv`s stay at the OLD locations for now (they're 200MB+ each; not worth
copying). Two paths forward:
- **Easy**: Keep using the old venvs by absolute path — `dev.ps1` in the
  monorepo already resolves the venv relative to its own directory, so it
  auto-picks up whatever `.venv/` exists at `loudrr-project/loudrr-fastapi/.venv`.
  Just copy or symlink your existing `.venv` into the new location:
  ```powershell
  # from an admin PowerShell:
  New-Item -ItemType SymbolicLink -Path 'C:\Users\mamoo\projects\loudrr-project\loudrr-fastapi\.venv' `
    -Target 'C:\Users\mamoo\projects\loudrr-fastapi\.venv'
  ```
- **Clean**: fresh venv in the new location:
  ```powershell
  cd C:\Users\mamoo\projects\loudrr-project\loudrr-fastapi
  py -3.14 -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -r backend\requirements.txt
  ```

### 5. Once everything is green — delete the OLD local dirs (~30s)

After ~1 week of stable Coolify + Vercel deploys from the monorepo:
```powershell
Remove-Item -Recurse -Force C:\Users\mamoo\projects\loudrr-fastapi
Remove-Item -Recurse -Force C:\Users\mamoo\projects\loudrr-analytics-service
```
The archive branches on GitHub remain as forever-fallbacks.

## Where's my old stuff?

| Old location | Where it lives now |
|---|---|
| `Flawlex-LLC/loudrr-core@main` (25 commits) | `Flawlex-LLC/loudrr-project@main` (init commit) |
| `loudrr-fastapi` local + master (37 commits total, 12 unpushed) | `archive/pre-monorepo-fastapi` on loudrr-project |
| `loudrr-analytics-service` local + `feat/top-cutoff-engagement-source` (60 commits + WIP) | `archive/pre-monorepo-analytics` on loudrr-analytics-service (which becomes read-only after step 3) |
| `Flawlex-LLC/loudrr-core@landing` (coming-soon) | `Flawlex-LLC/loudrr-project@main:/coming-soon/` |
| `Flawlex-LLC/loudrr-core@archive/django` | Same location, untouched |

## Reference — repo layout

```
Flawlex-LLC/loudrr-project (main)
├── loudrr-fastapi/
│   ├── backend/            # FastAPI + arq + alembic
│   ├── frontend/           # Next.js miniapp + admin
│   └── scripts/dev.ps1     # local Windows Terminal launcher (7 tabs)
├── loudrr-analytics-service/
│   ├── app/                # scoring API, engagement crawler, mindshare
│   ├── web/                # analytics dashboard
│   └── scripts/            # crawl / score / calibrate CLI
├── coming-soon/            # Next.js pre-launch landing (Vercel)
├── scripts/coolify-migrate.sh   # the script mentioned in step 1
├── README.md
├── MIGRATION.md            # this file
└── .gitignore              # covers all three subdirs
```

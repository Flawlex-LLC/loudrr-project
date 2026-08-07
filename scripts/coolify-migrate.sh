#!/usr/bin/env bash
# coolify-migrate.sh — repoint every Coolify application to the new monorepo.
#
# BEFORE the monorepo migration:
#   - Backend app     -> git: loudrr-core, base: /backend
#   - Frontend app    -> git: loudrr-core, base: /frontend
#   - Analytics apps  -> git: loudrr-analytics-service, base: /
#
# AFTER (what this script does):
#   - Backend app     -> git: loudrr-project, base: /loudrr-fastapi/backend
#   - Frontend app    -> git: loudrr-project, base: /loudrr-fastapi/frontend
#   - Analytics apps  -> git: loudrr-project, base: /loudrr-analytics-service
#
# The `landing` branch stays on loudrr-project (untouched) so Vercel keeps
# deploying the coming-soon page — no Coolify service touches it.
#
# ============================================================================
# USAGE:
#   1. In Coolify UI: Keys & Tokens -> create a token with write + deploy
#      permissions. Copy the "<id>|<secret>" string.
#   2. Fill in the vars below. UUIDs are visible in each app's Coolify URL:
#      https://<coolify-host>/project/<proj>/environment/<env>/application/<uuid>
#   3. Chmod +x this file and run it. It will PATCH each app then trigger a
#      forced rebuild via ?force=true. Watch the Coolify UI for the deploy
#      log — a failed deploy leaves the app in the old state until re-deployed,
#      so a botched migration is recoverable by just running with the old
#      values re-patched.
# ============================================================================

set -euo pipefail

# ---- CONFIG ----
COOLIFY_HOST="${COOLIFY_HOST:-https://coolify.example.com}"   # OVERRIDE ME
COOLIFY_TOKEN="${COOLIFY_TOKEN:-YOUR_ID|YOUR_SECRET}"         # OVERRIDE ME
NEW_GIT_REPO="https://github.com/Flawlex-LLC/loudrr-project"

# app_uuid|new_base_directory  — one per Coolify application
APPS=(
  # loudrr-fastapi/backend  (FastAPI + arq worker)
  # "REPLACE_WITH_BACKEND_UUID|/loudrr-fastapi/backend"

  # loudrr-fastapi/frontend  (Next.js miniapp + admin)
  # "REPLACE_WITH_FRONTEND_UUID|/loudrr-fastapi/frontend"

  # loudrr-analytics-service — one entry per RUN_MODE app
  # "REPLACE_WITH_ANALYTICS_API_UUID|/loudrr-analytics-service"
  # "REPLACE_WITH_ANALYTICS_MINDSHARE_UUID|/loudrr-analytics-service"
  # "REPLACE_WITH_ANALYTICS_ENGAGEMENT_UUID|/loudrr-analytics-service"
  # "REPLACE_WITH_ANALYTICS_TWITTERSCORE_UUID|/loudrr-analytics-service"
  # "REPLACE_WITH_ANALYTICS_BACKFILL_UUID|/loudrr-analytics-service"
  # "REPLACE_WITH_ANALYTICS_ENRICH_UUID|/loudrr-analytics-service"
  # "REPLACE_WITH_ANALYTICS_REPAIR_UUID|/loudrr-analytics-service"
  # "REPLACE_WITH_ANALYTICS_SCRAPE_UUID|/loudrr-analytics-service"
)

# ---- SANITY CHECKS ----
[[ "$COOLIFY_HOST" == "https://coolify.example.com" ]] && { echo "Set COOLIFY_HOST." >&2; exit 1; }
[[ "$COOLIFY_TOKEN" == *"YOUR_ID"* ]] && { echo "Set COOLIFY_TOKEN." >&2; exit 1; }
(( ${#APPS[@]} == 0 )) && { echo "APPS array is empty — fill in your Coolify app UUIDs first." >&2; exit 1; }
command -v curl >/dev/null || { echo "curl required." >&2; exit 1; }
command -v jq   >/dev/null || echo "warning: jq not installed — deploy IDs won't be parsed nicely." >&2

# ---- OPTIONAL: list applications so the user can find their UUIDs ----
if [[ "${1:-}" == "--list" ]]; then
  echo "Fetching all applications from $COOLIFY_HOST ..."
  curl -sS -H "Authorization: Bearer $COOLIFY_TOKEN" \
       "$COOLIFY_HOST/api/v1/applications" | \
    (command -v jq >/dev/null && jq -r '.[] | "\(.uuid)  \(.name)  git=\(.git_repository)  base=\(.base_directory)"' || cat)
  exit 0
fi

# ---- MIGRATE ----
for entry in "${APPS[@]}"; do
  uuid="${entry%%|*}"
  base="${entry##*|}"
  echo ""
  echo "==> $uuid  ->  base_directory=$base"

  patch_resp=$(curl -sS -w "\n%{http_code}" -X PATCH \
    -H "Authorization: Bearer $COOLIFY_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$(printf '{"git_repository":"%s","base_directory":"%s","instant_deploy":true}' "$NEW_GIT_REPO" "$base")" \
    "$COOLIFY_HOST/api/v1/applications/$uuid")
  body=$(echo "$patch_resp" | head -n -1)
  code=$(echo "$patch_resp" | tail -n1)
  if [[ "$code" != 2* ]]; then
    echo "  PATCH failed (HTTP $code): $body" >&2
    exit 1
  fi
  echo "  PATCH ok. Triggering forced rebuild..."

  deploy_resp=$(curl -sS -H "Authorization: Bearer $COOLIFY_TOKEN" \
    "$COOLIFY_HOST/api/v1/deploy?uuid=$uuid&force=true")
  echo "  $deploy_resp"
done

echo ""
echo "All apps patched + rebuild triggered. Watch each app's Deployment tab in"
echo "the Coolify UI — a failed rebuild leaves the running container up on the"
echo "old image, so nothing goes dark until the new build actually succeeds."

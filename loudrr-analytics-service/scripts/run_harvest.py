"""ONE-TIME TweetScout/Sorsa 10k-request harvest orchestrator (docs/harvest_plan.md).

Spends the irreversible 10k budget to reconstruct their ranked smart-set M + scores.
Two modes so we never auto-blow the budget:

    python -m scripts.run_harvest --pilot    # phases 0-2 (~510 req), prints the GATE, STOPS
    python -m scripts.run_harvest --full      # executes the gated main spend (resumable)

Crash-safe: cumulative API spend is restored from the call log; the snowball checkpoints
queried ids and records-before-spend so a crash never double-spends. Resume = re-run.

PREREQUISITES (checked before ANY API spend): Postgres reachable with tables created
(`python -m scripts.init_db`) and a funded key (`/key-usage-info` remaining > 0).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from sqlalchemy import text

from app.clients.sorsa import (
    SorsaBudgetExhausted,
    SorsaClient,
    SorsaQuotaError,
    SorsaTransientError,
)
from app.core.config import settings
from app.db.session import SessionLocal
from app.services import calibration as C
from app.services import harvest as H
from app.services.seed import handles_from_csv

STATE = H.STATE_DIR
SEED_FILE = os.path.join(STATE, "seed_ids.json")
PILOT_FILE = os.path.join(STATE, "pilot.json")
CG_HANDLES = os.path.join("data", "coingecko_handles.txt")   # from scripts/seed_coingecko
VC_HANDLES = os.path.join("data", "vc_handles.txt")          # curated crypto-VC anchors
TS100_HANDLES = os.path.join("data", "twitterscore_top100.txt")  # TwitterScore top-100
KAITO_HANDLES = os.path.join("data", "kaito_mindshare.txt")      # Kaito mindshare (top-engaged)


def _file_handles(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    return [ln.strip() for ln in open(path, encoding="utf-8") if ln.strip()]


def _coingecko_handles() -> list[str]:
    """Project handles (project neighborhoods) fetched by scripts/seed_coingecko."""
    return _file_handles(CG_HANDLES)


def _vc_handles() -> list[str]:
    """Curated crypto-VC anchors — seed the VC neighborhood directly (data/vc_handles.txt)."""
    return _file_handles(VC_HANDLES)

# High-out-degree anchor accounts across niches (seed the heap so multiple core regions
# are present from the start — mitigates clique-trapping per the refuted-assumption fix).
MEGAS = [
    "VitalikButerin", "cz_binance", "binance", "ethereum", "solana", "coinbase",
    "a16z", "paradigm", "Cointelegraph", "opensea", "Uniswap", "aave", "chainlink",
    "arbitrum", "optimismFND", "CoinbaseWallet", "krakenfx", "Polygon", "0xPolygon",
    "Cosmos", "dYdX", "MagicEden", "blur_io", "fridotech", "tier10k", "santimentfeed",
]
MEGA_PRIOR = 1_000_000.0   # ensure megas expand before unknown-prior bulk seeds
BULK_PRIOR = 1_000.0       # moderate prior for KOL/project seeds (real scores overtake)


async def _db_ok() -> bool:
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1 FROM harvested_scores LIMIT 1"))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  ! DB preflight failed ({e}). Run: python -m scripts.init_db")
        return False


def _chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


async def phase0_preflight(client: SorsaClient) -> bool:
    print("=== Phase 0 — preflight (DB + balance + spend-reconcile + linchpin) ===")
    if not await _db_ok():
        return False
    try:
        usage = await client.key_usage_info()
    except SorsaQuotaError as e:
        print(f"  ! key unusable: {e}")
        return False
    print(f"  balance: {usage}")
    if usage.get("remaining_requests", 0) < 1000:
        print("  ! < 1000 requests remaining — refusing to start a 10k plan.")
        return False
    # AUTHORITATIVE spend restore: trust the SERVER's key_requests over the local log
    # (immune to CWD/log-rotation/crash-between-send-and-log). Reconcile, keep the larger.
    server_spent = int(usage.get("key_requests", 0))
    client.requests_spent = max(client.requests_spent, server_spent)
    print(f"  reconciled cumulative spend = {client.requests_spent} (server key_requests={server_spent})")
    try:
        probe = await client.probe_top_followers_score("cz_binance")
    except (SorsaTransientError, SorsaBudgetExhausted) as e:
        print(f"  ! linchpin probe errored transiently: {e} — retry preflight later.")
        return False
    print(f"  linchpin: top-follower score usable = {probe['usable']} (value={probe.get('score_field_value')})")
    if not probe["usable"]:
        print("  ! LINCHPIN FAILED — /top-followers has no usable score. Abort; see plan fallback.")
        return False
    return True


async def phase1_resolve(client: SorsaClient, csv_path: str | None) -> list[dict]:
    # Full desired seed set = mega anchors + KOL CSV + CoinGecko projects + VC anchors.
    desired = list(dict.fromkeys(
        MEGAS
        + (handles_from_csv(csv_path, link_col=1, skip_rows=2) if csv_path else [])
        + _coingecko_handles()
        + _vc_handles()
        + _file_handles(TS100_HANDLES)
        + _file_handles(KAITO_HANDLES)
    ))
    # Reuse cached resolutions; resolve only the handles we haven't resolved yet (so
    # adding CoinGecko later costs ~15 info_batch calls, not a full re-resolve).
    cached = json.load(open(SEED_FILE)) if os.path.exists(SEED_FILE) else []
    have = {str(s.get("username", "")).lower() for s in cached}
    todo = [h for h in desired if h.lower() not in have]
    if not todo:
        print(f"=== Phase 1 — reuse {len(cached)} resolved seeds (cached, complete) ===")
        return cached

    print(f"=== Phase 1 — resolve {len(todo)} NEW handles (+{len(cached)} cached) ===")
    new_seeds: list[dict] = []
    clean = True
    first = not cached  # only run the format sanity-check when we have no trusted cache
    for chunk in _chunks(todo, 100):
        try:
            got = await client.info_batch(usernames=chunk)
        except (SorsaQuotaError, SorsaBudgetExhausted) as e:
            print(f"  ! stopping resolve (budget): {e}"); clean = False; break
        except SorsaTransientError as e:
            print(f"  ! transient on a chunk, skipping it: {e}"); clean = False; continue
        if first:
            first = False
            if len(got) < max(1, len(chunk) // 2):
                print(f"  ! info_batch resolved only {len(got)}/{len(chunk)} — format issue.")
                return cached
        new_seeds += got
    megaset = {m.lower() for m in MEGAS}
    for s in new_seeds:
        s["_prior"] = MEGA_PRIOR if str(s.get("username", "")).lower() in megaset else BULK_PRIOR
    merged = cached + new_seeds
    os.makedirs(STATE, exist_ok=True)
    if clean:  # cache only a COMPLETE resolution
        json.dump(merged, open(SEED_FILE, "w"))
    print(f"  resolved {len(new_seeds)} new (+{len(cached)} cached = {len(merged)}); "
          f"spent now {client.requests_spent}")
    return merged


async def phase2_pilot(client: SorsaClient, seeds: list[dict], pool: int) -> dict:
    if os.path.exists(PILOT_FILE):
        g = json.load(open(PILOT_FILE))
        print(f"=== Phase 2 — reuse cached pilot (N={g['novelty']['n']}) ===")
        return g
    print("=== Phase 2 — instrumented pilot (gates the main spend) ===")
    handles = [s["username"] for s in seeds if s.get("username")]
    # stratified-ish: megas + a spread of the bulk list, capped to ~300 targets
    targets = MEGAS[:40] + handles[40:40 + 260]
    g = await H.run_pilot(client, targets[:300], pool=pool)
    os.makedirs(STATE, exist_ok=True)
    json.dump(g, open(PILOT_FILE, "w"))
    print(f"  pilot: N={g['novelty']['n']}/call, edge_local={g['edge_local']}, "
          f"unique={g['unique_discovered']}, split={g['gate']}")
    return g


def _mark_pilot_queried(seeds: list[dict]) -> int:
    """The pilot queried ~286 accounts (pilot_done.txt, by username) without writing
    queried_ids.txt. Map those usernames -> ids via the seeds and mark them queried so
    the breadth discovery doesn't waste ~286 calls re-querying them."""
    done_f = os.path.join(STATE, "pilot_done.txt")
    if not os.path.exists(done_f):
        return 0
    done = {ln.strip().lower() for ln in open(done_f, encoding="utf-8") if ln.strip()}
    uname2id = {str(s.get("username", "")).lower(): str(s["id"])
                for s in seeds if str(s.get("id", "")).isdigit()}
    qpath = H._queried_path(STATE)
    existing = H._load_set(qpath)
    added = 0
    with open(qpath, "a", encoding="utf-8") as f:
        for u in done:
            uid = uname2id.get(u)
            if uid and uid not in existing:
                f.write(uid + "\n"); added += 1
    return added


def _frontier(seeds: list[dict]) -> tuple[list[tuple[float, str]], list[str]]:
    frontier = [(float(s.get("_prior", BULK_PRIOR)), str(s["id"]))
                for s in seeds if str(s.get("id", "")).isdigit()]
    diversity = [str(s["id"]) for s in seeds
                 if str(s.get("id", "")).isdigit() and s.get("_prior") == BULK_PRIOR]
    return frontier, diversity


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="run phases 0-2 then STOP (print gate)")
    ap.add_argument("--full", action="store_true", help="run the full gated 10k plan")
    ap.add_argument("--csv", default=r"..\scraper-twitter\AnkhLabs_KOLs_1.5K_enriched.csv")
    args = ap.parse_args()
    if not (args.pilot or args.full):
        ap.error("choose --pilot (safe first) or --full")

    ceiling = settings.sorsa_request_ceiling
    # hard_ceiling is a client-level kill-switch so retries/bookend calls can't breach it
    client = SorsaClient(hard_ceiling=ceiling)
    client.requests_spent = H.count_api_calls_in_log()  # local pre-seed; phase0 reconciles
    CAL_RESERVE = 700

    if not await phase0_preflight(client):  # also reconciles spend from server key_requests
        return
    seeds = await phase1_resolve(client, args.csv if os.path.exists(args.csv) else None)
    if not seeds:
        print("  ! no seeds resolved — fix info_batch/seed source before continuing."); return

    gate = await phase2_pilot(client, seeds, max(0, ceiling - client.requests_spent - CAL_RESERVE))
    n = gate["novelty"]["n"]
    # finding #12: size the pool AFTER the pilot has spent, so pilot calls aren't double-counted
    pool = max(0, ceiling - client.requests_spent - CAL_RESERVE)

    if args.pilot:
        print("\n>>> PILOT COMPLETE. Review below, then run --full to execute.")
        print(f">>> N={n}/call (diverse), edge_local={gate['edge_local']}, discovery pool={pool}")
        return

    # ---- FULL: BREADTH discovery (the bulk) + tiers + bootstrap ----
    # LIVE LESSON: best-first re-mines the densely-inter-followed high-score core (~0.1
    # new/call); breadth over diverse accounts gives ~4 new/call. So one breadth phase
    # over the whole pool, soft-stopping when genuinely saturated.
    print(f"  pre-marking {_mark_pilot_queried(seeds)} pilot-queried accounts (skip re-query)")
    frontier, diversity = _frontier(seeds)
    disc_ceiling = min(ceiling, client.requests_spent + pool)
    print(f"=== Discovery — BREADTH /top-followers (+{pool} req, soft-stop on saturation) ===")
    # soft-stop only when GENUINELY dry. Breadth steady-states ~1-1.5 new/call, which is
    # still productive (1/call x thousands = thousands of new M), so stop only below ~0.3
    # over a wide 400-call window — otherwise just run to the budget ceiling.
    print(f"  {await H.snowball_harvest(client, frontier, ceiling=disc_ceiling, diversity_seeds=diversity, strategy='breadth', soft_stop_threshold=0.3, soft_stop_window=400)}")

    print("=== Tiers (FREE dashboard reads) + bootstrap M ===")
    tier_handles = MEGAS + [s['username'] for s in seeds[:200] if s.get('username')]
    print(f"  tiers: {await C.learn_tiers(tier_handles, client=client)}")
    print(f"  bootstrapped M: {await C.bootstrap_smart_set_from_harvest()}")

    try:
        print(f"=== Done. Final balance: {await client.key_usage_info()} ===")
    except SorsaQuotaError as e:
        print(f"=== Done (quota state: {e}) ===")


if __name__ == "__main__":
    asyncio.run(main())

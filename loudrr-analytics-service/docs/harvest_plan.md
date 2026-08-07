# Harvest Plan — 10k TweetScout requests (adversarially verified)

> Output of the `tweetscout-10k-harvest-plan` workflow (54 agents; 4 strategies, yield model, 3-vote refutation, synthesis). Run ID wf_5deca993-17d, 2026-06-15. This is the canonical spec for `scripts/run_harvest.py`.

Confirmed: `/top-followers` accepts `user_id`. I have everything needed. Here is the final plan.

---

# FINAL ADAPTIVE PLAN — Spending EXACTLY 10,000 TweetScout/Sorsa API Requests

## Refuted assumptions dropped (and how the plan changed)

Four assumptions were refuted (2/3 or 3/3 skeptics). The plan is rebuilt around their absence:

1. **"Megas' top-followers ≈ the global top of M"** (refuted 3/3). Reason: `/top-followers` is follow-gated (only accounts that follow X) and the score is in-edge based, so low-out-degree apex accounts (ethereum, solana, opensea — projects that follow almost nobody) are **structurally unreachable** as top-followers no matter who you query. → **Consequence: do NOT expect to enumerate all of M; seed broadly across mid-tier accounts, not just megas; treat the high-out-degree influencer core as the harvestable target and accept project/VC apex accounts will be under-surfaced.**

2. **"Capturing the top 10–30k core reproduces MOST users' scores"** (refuted 3/3). Reason: typical scored users are mid/small; their score is decided at the margin by *mid-tier* M-members, exactly the part core-only capture starves; a single isotonic map can't correct a position-dependent omission bias. → **Consequence: the breadth/tail phase is NOT optional polish — it is required for parity on the modal user. Budget materially shifts toward mid-tier breadth.**

3. **"Best-first converges on the global top of M"** — the strong reading is dropped; only "best-first front-loads the dense high-out-degree influencer core" survives. → **Consequence: frontier is best-first but with mandatory diversity injection to avoid clique-trapping, and the soft-stop is interpreted carefully (flattening ≠ M-coverage).**

4. **"DEDUP CORRECTNESS already holds in code"** (refuted 2/2). Reason: `harvest()` has **no pre-call dedup, no frontier/heap, no queried-id set, no per-call checkpoint, no hard request counter**, falls back to username in the PK, and spends ~5 calls/account. → **Consequence: a NEW orchestrator (`snowball_harvest`) must be built; the existing `harvest()` is reused only for the calibration/pilot accounts where the 5-call bundle is actually wanted.**

Assumptions kept (not refuted) and relied on: `/top-followers` carries a numeric `score` (linchpin — still pilot-gated on first call); dashboard reads are FREE (confirmed live, server-rendered SSR); `/info-batch` resolves ~100 handles/call; scales are monotone (isotonic valid); 10k cap is the only hard constraint but **429 must be handled** (rate "purely non-binding" was the one refute on the rate assumption — pace conservatively).

---

## 1. Exact request allocation (sums to 10,000)

| Phase | Requests | Endpoint(s) | Purpose |
|---|---:|---|---|
| **0. Guardrail + linchpin** | 12 | `key-usage-info` ×2, `top-followers` ×3, `score` ×4, `followers-stats` ×3 | Confirm balance; confirm `TopFollower.score` live+numeric+populated; settle global-vs-edge-local (next row); abort to fallback if null. |
| **1. Seed resolution** | 110 | `info-batch` (~100/call) | Resolve ~11,000 seed handles → stable `user_id` + bios (1,520 AnkhLabs KOLs + ~80 megas + ~9,400 CoinGecko projects). Dedup key = numeric id. |
| **2. Instrumented pilot** | 388 | `top-followers` ×300, `score` ×80, `followers-stats` ×8 | MEASURE τ, f, scale, 20-cap, global-vs-edge-local. **Sets phase-3/5 split.** |
| **3. Best-first calibrated snowball** | 6,800* | `top-followers` (user_id) | Core M reconstruction + ~20 (id→score) labels/call. *Gated by pilot.* |
| **4. Calibration + validation** | 1,290 | `score` ×680, `followers-stats` ×300, `score-changes` ×30, `info-batch` ×280 | Scale map (paired w/ FREE dashboard), category labels, volatility, resolve ~28k discovered handles→ids, leakage-free holdout. |
| **5. Breadth tail-fill** | 1,360* | `top-followers` (diverse unqueried seeds) | Raise floor f; mid/low-tail M + bottom of calibration curve. *Gated.* |
| **Reserve** | 40 | any (429/5xx retries, `key-usage-info` checks) | Buffer against the irreversible cap. Working ceiling = **9,960**. |
| **TOTAL** | **10,000** | | |

\* **Phase 3 and Phase 5 share a 8,160-request pool**; the pilot GATE sets the split (see §2). Default split shown (6,800 / 1,360); rebalanced after the pilot.

---

## 2. THE PILOT (388 requests) — what it measures and the decision rules

**Targets (300 `/top-followers` calls), stratified by prior score band:**
- ~40 mega-core (cz_binance, VitalikButerin, binance, ethereum, solana, coinbase, a16z, paradigm, Cointelegraph, opensea …)
- ~200 mid-tier KOLs (random sample from the 1,520 AnkhLabs list)
- ~60 long-tail / CoinGecko project handles

**Measurements:**

1. **Per-call unique yield & novelty curve.** Track net-new-unique `user_id`s per call. Fit `new(c) = (g−f)·exp(−c/τ) + f` over calls 200–300 (the steady-state window, after seed novelty settles). Record g (gross usable/call), f (floor), τ (decay). **Report per stratum, not pooled** — mega-stratum overlap over-estimates redundancy.
2. **Which scale `/top-followers.score` uses.** For 80 accounts that appear *both* as a top-follower entry AND get a direct `/score` call, compare. Establishes whether top-follower score ≈ `/score` (~1.25), the swagger ~100 scale, or dashboard thousands.
3. **GLOBAL vs EDGE-LOCAL (scale-free test).** For accounts appearing as top-followers of ≥2 different targets, check whether the *same* `user_id` carries the *same* score across different parents. **Cross-target invariance (not equality to `/score`) is the real test.** If invariant → global → `func.greatest` upsert is sound. If it varies by parent → edge-local → abort the global-label thesis, store `(parent_id, follower_id, score)` edges instead and pivot to a per-edge model.
4. **20-cap & count distribution.** Record `len(users)` per call; confirm ≤20 and that small accounts return <20 (affects yield math). Record whether array is score-sorted (non-load-bearing — we read every entry's score regardless).

**GATE (free, computed on pilot data) — sets the Phase-3/5 split:**

- **N (mid-stratum net-new/call over calls 200–300) ≥ 8:** core is rich → **Phase 3 = 7,200, Phase 5 = 960** (deep snowball).
- **3 ≤ N < 8:** moderate overlap → **Phase 3 = 6,000, Phase 5 = 2,160** (cap snowball, divert to breadth — this is the expected case given the refuted "core is enough" assumption).
- **N < 3:** core small/saturated fast → **Phase 3 = 4,000, Phase 5 = 4,160** (stop deep snowball early, pour into mid-tier breadth + calibration).
- **Linchpin FAIL (`score` null/non-numeric):** abort snowball entirely. Pivot: use `/top-followers` purely for M *discovery* (still ~20 ids/call, no labels) for ~3,000 calls, then spend remaining ~6,500 on direct `/score` per highest-frequency discovered account (1 label/call). Expected unique-labeled M collapses to ~6–7k.
- **EDGE-LOCAL:** switch HarvestedScore from `greatest`-merged global table to a `(parent, follower, score)` edge table; calibration uses per-target-averaged scores.

---

## 3. Frontier algorithm for the snowball

**Seed set:** Phase-1-resolved `user_id`s — 1,520 KOLs + ~80 megas + ~9,400 CoinGecko projects, **pre-bucketed by bio into sub-niches** (L1/L2/DeFi/NFT/gaming/memecoin/RWA/AI/infra/regional) and **by inferred region/language** so multiple core regions are on the heap from the start (mitigates clique-trapping and the regional blind spot).

**Data structures (persisted to disk every call):**
- `frontier`: max-heap keyed by `best_observed_score(user_id)`.
- `queried_ids`: set of `user_id`s already expanded (never re-query).
- `seen_ids`: every discovered id (for novelty accounting).

**Loop:**
```
while api_requests_spent < phase3_ceiling and frontier not empty:
    if (call_index % 12 == 0):           # diversity injection every 12th call
        node = pop next unqueried SEED from a different sub-niche (round-robin)
    else:
        node = heappop(frontier)         # highest best-observed-score unqueried node
    if node.user_id in queried_ids: continue
    if node.protected: continue          # protected => no top-followers
    resp = sorsa.top_followers(user_id=node.user_id)   # 1 request
    queried_ids.add(node.user_id); spent += 1
    rows = []
    for tf in resp:                      # up to 20
        uid = tf["id"]
        if not uid.isdigit():  continue  # reject non-numeric ids (no username fallback into PK)
        if tf.get("score") is None: continue
        rows.append({user_id: uid, username: tf["username"], sorsa_score: float(tf["score"]),
                     category: categorize(tf["display_name"], tf["description"]),
                     source: "top_follower"})
        if uid not in seen_ids:
            seen_ids.add(uid); heappush(frontier, (-score, uid))
        else:
            update best_observed_score(uid)   # re-prioritize, never re-query
    upsert_harvested(rows)               # ON CONFLICT: greatest(score), seen_count+1
    checkpoint(frontier, queried_ids, seen_ids)   # atomic write every call
```

**Dedup/upsert:** keep the existing `_upsert_harvested` (PK=`user_id`, `func.greatest`, `seen_count+1`). **Pre-call dedup on `queried_ids` is the budget-critical addition** — never spend a call on an already-expanded id. Reject non-digit ids rather than falling back to username.

**Holdout for leakage-free validation:** deterministically exclude `user_id` where `int(uid) % 20 == 0` from the calibration FIT (≈5%); used in Phase 4 for honest Spearman/Kendall/MAE.

**Stop criteria:**
- Hard: `spent == phase3_ceiling`.
- Soft (redirect remainder to Phase 5 breadth): rolling net-new-unique/call over last 200 calls **< 2.5** AND frontier max score below the Tier-2 ("Noted", ~289 dashboard-equiv) threshold. **A flattening curve is interpreted as "core drained," NOT "M covered"** (per refuted assumption #2/#3) — remaining budget therefore goes to *breadth into new seed neighborhoods*, not deeper diving.

---

## 4. API↔dashboard scale mapping + tier thresholds (FREE dashboard reads)

Confirmed live: `app.sorsa.io/profile/<handle>` is **server-side rendered** (score + "Tier N. Name" in raw HTML), a different host from `api.sorsa.io`, sends **no `ApiKey` header** → **zero API quota**.

**Procedure (Phase 4, the API side costs quota; dashboard side is free):**
1. Select ~600 accounts **stratified across 5 dashboard bands** (use harvested top-follower scores as the strata key) and across the 3 categories.
2. For each: spend 1 `/score` call (API) **and** 1 dashboard fetch (free, same session, same day to avoid drift). Store `dashboard_score`, `dashboard_tier`, `sorsa_score` on `GroundTruth`.
3. Fit two monotone maps via isotonic regression:
   - `top_follower_score → dashboard_score` (the bulk labels → parity target)
   - `api_score → dashboard_score` (cross-check)
4. **Tier thresholds:** `learn_thresholds()` on the `(dashboard_score → tier_name)` pairs — the dashboard band edges between Tier1…Tier5 (Supreme/Significant/Noted/…). These set `app/core/tiers.py` cutoffs empirically rather than the provisional 250/800/1600/2800.

**Robustness fixes (from the verdicts):** harden `dashboard_score()` to parse the Next.js RSC `__next_f` JSON payload rather than regexing hashed CSS class names; drop any pair where dashboard score is 0/"Loading"/"Upgrade" (gated SPA placeholder); pace dashboard reads slowly (a few hundred only) to avoid Cloudflare bot-blocking; re-pull anchors fresh (scores drift: cz 4105→4184, Vitalik 4892→5095).

---

## 5. Honest expected yield + top risks

**Expected yield (central, 3≤N<8 gate, hybrid snowball+breadth):**
- **Unique M accounts: ~45,000–52,000** (central ~48k). Concentrated in the high-out-degree influencer core (near-complete) + a strong stratified mid-tail. **NOT all of M** — the 20-cap makes popular accounts' full follower-M unreachable, and low-out-degree apex projects/VCs are under-surfaced (accepted structural limit).
- **Score observations: ~150k–165k**, collapsing to ~48k unique per-account labels (multi-parent re-observation averages noise).
- **Calibration:** measured 2-scale map + empirical tier thresholds; leakage-free out-of-sample Spearman/Kendall/MAE on the ~5% id-hash holdout.

**Top risks & mitigations:**
1. **Linchpin null** (low prob, catastrophic) → Phase 0 fails fast on 3 calls; fallback path defined (§2).
2. **Edge-local score** (low-med, poisons global table) → pilot cross-target-invariance test; pivot to edge table if it fails.
3. **Core-only under-covers modal users** (high prob — refuted assumption) → mandatory breadth phase + diversity injection; mid-tier seeds prioritized.
4. **Heavy overlap → ~38k uniques** (med) → diverse seeding raises floor f; breadth phase absorbs it.
5. **Irreversible spend on crash/double-query** (med) → per-call atomic checkpoint of `(frontier, queried_ids, seen_ids)`; hard local request counter stopping at 9,960; resume reads `queried_ids` to skip.
6. **429 throttling wastes quota** (med) → add AsyncLimiter (~5 req/s start, ≤15) + tenacity retry on 429/5xx with `Retry-After`; never let a 429 fall into the silent `except: continue`.
7. **Dashboard SPA/Cloudflare** (med, calibration-only) → RSC-JSON parse, drop placeholders, low volume, fresh pulls.

---

## 6. BUILD SPEC

**`app/clients/sorsa.py`:**
- Add `user_id` support: refactor `_get(path, username)` → `_get(path, *, username=None, user_id=None, user_link=None)` passing exactly one query param. Add `top_followers(self, *, user_id=None, username=None)`.
- Add `info_batch(self, usernames=None, user_ids=None) -> list[dict]` → GET `/info-batch` with repeated `usernames`/`user_ids` query params (≤100), returns `users[]` (id, username, description bio).
- Add an `AsyncLimiter(rate=5/s)` and a tenacity retry (429/5xx, honor `Retry-After`) around `_get`; map 429 to a new transient error, not `SorsaQuotaError`.
- Harden `dashboard_score()` to parse the `self.__next_f` RSC payload; return `None` on gated/placeholder pages.
- Keep auto call-logging.

**`app/services/calibration.py`:**
- **NEW `async def snowball_harvest(seed_ids, *, ceiling, checkpoint_path, holdout_mod=20) -> dict`** — implements §3: heap frontier, `queried_ids`/`seen_ids` sets, per-call atomic checkpoint (`pickle`/`json` to `data/harvest_state/`), hard request counter, diversity injection, one `/top-followers` call per expansion, `_upsert_harvested` with non-digit-id rejection. Returns `{spent, unique_ids, observations, novelty_curve}`.
- **NEW `async def run_pilot(stratified_targets) -> dict`** — §2 measurements: per-stratum novelty fit (τ, f, N), scale comparison, cross-target invariance, count distribution. Returns the GATE verdict + recommended `(phase3_ceiling, phase5_ceiling)`.
- **NEW `async def learn_scale_and_tiers() -> dict`** — §4: stratified `/score` + free dashboard pairs → two isotonic maps + `learn_thresholds()` for `app/core/tiers.py`; writes `data/scale_map.json`.
- **Modify `_pairs()`** — exclude `int(uid) % holdout_mod == 0` from FIT; add `_holdout_pairs()` for `parity_report`.
- **Reuse existing `harvest()`** ONLY for the ≤80 pilot/calibration accounts where the full 5-call bundle (`score`/stats/changes/top-followers/dashboard) is genuinely wanted — NOT for the snowball.

**NEW script `scripts/run_harvest.py`** — orchestrates the 6 phases in order with a single shared `request_counter` enforcing the 9,960 ceiling: Phase 0 guardrail+probe → Phase 1 `info_batch` resolution → `run_pilot` → apply GATE → `snowball_harvest(phase3_ceiling)` → `learn_scale_and_tiers` + holdout resolution → `snowball_harvest` breadth (phase5_ceiling, diverse unqueried seeds) → final `key_usage_info`. Persists state every call; resumable.

**Relevant files:** `c:\Users\mamoo\projects\loudrr-analytics-service\app\services\calibration.py`, `c:\Users\mamoo\projects\loudrr-analytics-service\app\clients\sorsa.py`, `c:\Users\mamoo\projects\loudrr-analytics-service\app\db\models.py` (HarvestedScore/GroundTruth — add nothing unless edge-local pivot), `c:\Users\mamoo\projects\loudrr-analytics-service\app\core\tiers.py` (thresholds), new `c:\Users\mamoo\projects\loudrr-analytics-service\scripts\run_harvest.py`, state dir `c:\Users\mamoo\projects\loudrr-analytics-service\data\harvest_state\`.
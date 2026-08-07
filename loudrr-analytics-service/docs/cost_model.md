# loudrr-analytics-service — Cost Model (twitterapi.io path)

> **Date:** 2026-06-14
> **Scope:** Full v1 = a Sorsa.io-style crypto influence-scoring layer, built on **twitterapi.io**
> as the sole data source (Pulse/native scraping ignored for now, per owner).
> **Companion:** `django-app/credit_service/docs/scoring_layer_research.md` (the research).

---

## 0. Headline

| | One-time cold start | Recurring (per month) |
|---|---|---|
| **20k smart set** | ~$150 (IDs) / ~$300 (profiles) | ~$150–250 + infra |
| **50k smart set (recommended)** | **~$360 (IDs) / ~$750 (profiles)** | **~$200–400 + ~$25–50 infra** |
| **100k smart set** | ~$700 (IDs) / ~$1,500 (profiles) | ~$350–700 + infra |

> IDs vs profiles = whether a `followings_ids` bulk endpoint exists. **UPDATE
> (2026-06-18): the verified endpoint catalog has no such endpoint** — it lists
> `/user/followers`, `/user/verifiedFollowers`, and `/user/followings` (profiles)
> only. So the **profiles path is THE plan, not a fallback**; the "IDs" column below
> is retained only as a hypothetical floor if twitterapi.io ever ships it. See §2.
> **Profiles path is ~$750 one-time @ 50k** — an order of magnitude under the
> research doc's $8–17k. (Our actual harvested list is ~7.4k, so the real one-time
> crawl is far smaller — pilot-measure it before extrapolating; see below.)

Plus **~$2 of Sorsa quota** (on a topped key) for ground-truth calibration. That's it.

> **The research doc's "$8–17k cold start" is wrong by ~20×.** It priced follower data at
> the **$0.15/1k tweet rate**. Follower/following data is **$0.01/1k (profiles)** or
> **$0.0045/1k (IDs)** — 15–33× cheaper. Full v1 is a few hundred dollars one-time.

---

## 1. twitterapi.io pricing facts (verified 2026-06-14)

Source: `https://twitterapi.io/pricing`, `https://docs.twitterapi.io`, and the owner's
local notes `scraper-twitter/_twitterapiio_stream.md`.

- **1 USD = 100,000 credits.** Pay-as-you-go, no monthly minimum, credits never expire.
- Tweet: 15 credits = **$0.15/1k**.
- User profile (single `/user/info`): 18 credits = **$0.18/1k**; batch ≥100 ids = 10 credits = **$0.10/1k**.
- **Following / Followers (profiles)** — tiered by page size, max 200/page:
  - 200/page → **1 credit = $0.01/1k**
  - 100–199 → 2 credits = $0.02/1k
  - 20–99 → 3 credits = $0.03/1k
  - min 60 credits/call ($0.0006)
- **Following IDs / Follower IDs** — tiered, max 5,000/page:
  - 4,000–5,000/page → **0.45 credits = $0.0045/1k ($4.50/M)**
  - 200–3,999 → 1 credit = $0.01/1k
  - 50–199 → 2 credits = $0.02/1k
- QPS scales with wallet balance: ≥50k credits ($0.50) → **20 QPS**.

---

## 2. Why following-**IDs** is the right crawl

The reverse index only needs to know **which M-members follow X** — a set of `(m → followee_id)`
edges. We don't need the followee's profile at crawl time:

- M-members' own profiles → fetched once via batch ($0.10/1k, ~$5 for 50k).
- A queried account X's profile card → one cheap on-demand `/user/info` ($0.00018), cached.
- M-internal graph (for PageRank) → just intersect each following-ID set with M's ID set.

So the bulk crawl prefers **`followings_ids` at the 5,000/page bulk tier = $4.50 per million edges.**
(Profiles path is 2.2× the cost for data we'd mostly throw away.)

> **RESOLVED (2026-06-18):** the verified endpoint catalog
> (`.agents/skills/twitterapi-io/references/endpoints.md`, checked against the live
> backend) does **not** include any `followings_ids` / following-IDs route. It lists
> only `/user/followers`, `/user/verifiedFollowers`, and `/user/followings` (full
> profiles). Combined with the earlier 404 on the guessed slug, we treat the bulk-IDs
> fast-path as **nonexistent** and the crawler is **profiles-only** — the speculative
> IDs code was removed from `clients/twitterapi.py` and `services/crawl.py`. Each
> followee profile already carries its `id`, so an edge needs no extra resolution.
> **Crawl cost = profiles path ($0.01/1k @ 200/page).** The pilot (`run_crawl --pilot`)
> measures the true per-member price off the wallet balance delta — trust that over
> this estimate.

---

## 3. Cold-start breakdown (50k smart set)

Assumes **avg 1,500 following/member** (the dominant knob — crypto projects often follow
<300, KOLs/VCs 1k–5k; 1,500 is a conservative midpoint).

| Step | Volume | Unit cost | Cost |
|---|---|---|---|
| Resolve seed handles → user IDs (CoinGecko ~15k + Lists) | ~15k profiles (batch) | $0.10/1k | ~$1.5 |
| Crawl seed following to expand M (~2k seed × 1.5k) | 3M IDs | $0.0045/1k | ~$14 |
| **Crawl full M following lists (50k × 1.5k)** | **75M IDs** | **$0.0045/1k** | **~$338** |
| Batch profiles for 50k M-members (categorization) | 50k | $0.10/1k | ~$5 |
| **Total** | | | **~$360** |

Scale sensitivity (full-M crawl term only):

| M size \ avg following | 800 | 1,500 | 2,500 |
|---|---|---|---|
| 20k | $72 | $135 | $225 |
| 50k | $180 | $338 | $563 |
| 100k | $360 | $675 | $1,125 |

---

## 4. Recurring (monthly)

- **Graph refresh** — following lists are low-churn. Smart-tiered (high-weight/high-churn
  members weekly, long tail monthly) ≈ **half a full re-crawl** → **~$170–340/mo** for 50k.
  Naive full monthly re-crawl = ~$338/mo.
- **Hosting** — Postgres (~10–20 GB for the 75M-row edge index) + Redis cache; NetworkX
  PageRank runs in-process on the small M-internal subgraph → **~$25–50/mo** managed.
- **On-demand query reads** — cached profile cards; ~$18 per 100k queries. Negligible.
- **Sorsa calibration** — periodic ground-truth recheck, a few $ of quota.

> Long term, moving the refresh crawl to the native/Pulse pool (own X accounts + proxies)
> trades per-call cost for ~$5–15/account fixed cost — the lever to cut recurring spend later.
> Out of scope for now (twitterapi.io only).

---

## 5. Optional later features (not in base v1)

- **Bot / fake-follower %** — needs the *target's* follower sample (~1–2k), classified.
  ~$0.01–0.05 per audited account. Gate behind paywall; sample, never enumerate.
- **Engagement %, trust score, named tiers** — derived from sampled tweets; per-account read cost.
- **bulk_scores_check, followers_score_sum, alpha feed** — pure compute over the index, ~free.

---

## 6. What spends money (build-time gates)

Everything in the scaffold (code, schema, clients, endpoints) is **$0**. Real spend begins
**only** at:
1. The seed-resolution + seed-following crawl (~$15) — cheap, validates the pipeline.
2. The full-M following crawl (~$338 @ 50k) — the one real cost; gate on funded wallet.

A funded wallet of **≥$0.50 (50k credits)** also unlocks 20 QPS, which the crawl needs to
finish in reasonable wall-clock time.

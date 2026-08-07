# Parity Strategy — scoring "almost exactly like TweetScout"

> **Goal:** our score should reproduce TweetScout/Sorsa's number with high fidelity,
> not merely be "Sorsa-style." This doc is the plan to get there.
> **Date:** 2026-06-14. **Companion:** `cost_model.md`, `scoring_layer_research.md`.

## 0. Key facts that make this tractable

- **TweetScout == Sorsa.** `tweetscout.io` 301-redirects to `sorsa.io` (same company,
  rebranded 2025). Our `SORSA_KEY` *is* the TweetScout API. (TwitterScore.io is a
  **different** company — do not conflate.)
- **Their API leaks exactly what we must copy.** This is the whole unlock:
  - `/score` → their score for an account (normalized scale).
  - `/top-followers` → 20 accounts **each carrying their internal score** → a stream
    of `(account → their score)` labels, ~20 per call.
  - `/followers-stats` → their category counts (influencers/projects/VCs).
  - `/top-following`, `/new-followers-7d` → more labeled accounts + their M membership.
- So by querying many accounts we **reconstruct a large slice of their curated set M,
  their category labels, and a big supervised set of their scores** — then fit our
  engine to reproduce those numbers.

## 1. Honest parity ceiling

Bit-exact is unlikely: their exact formula is proprietary, part of M is hidden, and our
follow-graph is a different-time snapshot. **Realistically achievable:**
- **Near-exact** for accounts whose top-followers we've harvested (we have their inputs).
- **Strong rank-correlation** (target Spearman ρ ≥ ~0.9) + **tier-match accuracy** across
  the broad set, via supervised calibration.
That is what "almost accurately" means here, and it's a strong, sellable product.

## 2. The replication loop

```
            ┌─────────────────────────────────────────────────────────┐
            │ 1. HARVEST (their API)  — app/services/calibration.py     │
            │    for each account: /score, /followers-stats,            │
            │    /top-followers, /top-following                         │
            │      → GroundTruth rows (their score+categories+deltas)   │
            │      → HarvestedScore rows: (account → their score),      │
            │        incl. every top-follower entry  ⇒ reconstructs M   │
            └─────────────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────▼─────────────────────────────────┐
            │ 2. BOOTSTRAP our M from theirs                            │
            │    promote harvested accounts into smart_set with their  │
            │    category + their score as a prior  → crawl their      │
            │    following → our reverse index mirrors their graph     │
            └───────────────────────┬─────────────────────────────────┘
                                    │
            ┌───────────────────────▼─────────────────────────────────┐
            │ 3. SCORE (ours)  — personalized PageRank, personalization │
            │    vector = their harvested scores (semi-supervised)      │
            └───────────────────────┬─────────────────────────────────┘
                                    │
            ┌───────────────────────▼─────────────────────────────────┐
            │ 4. CALIBRATE & MEASURE  — fit our_raw → their_score       │
            │    IsotonicRegression (monotonic), report Spearman/MAE/   │
            │    tier-match on a holdout. Persist the mapping.          │
            └───────────────────────┬─────────────────────────────────┘
                                    │  (repeat: pull more, refit)
                                    ▼  until parity target met
```

### Why each step buys parity
- **Step 1** gives ground-truth labels — without it we're guessing.
- **Step 2** makes our *inputs* match theirs: if our M ≈ their M and we weight by their
  scores, our PageRank output tracks theirs by construction. This is the biggest lever.
- **Step 4** absorbs the residual scale/shape difference. Isotonic regression is the
  right tool: it's monotonic (preserves ranking) and non-parametric (no formula assumed),
  so if our ranking is right, it maps our numbers onto their scale with minimal error.

## 3. Data to pull, and the budget

Seed accounts to harvest = our 1,520 KOLs (AnkhLabs CSV) + CoinGecko projects (~15k) +
every account surfaced in their top-followers responses (snowballs M).

Per account: `/score` + `/followers-stats` + `/top-followers` ≈ 3 requests.
- 16k accounts × 3 ≈ **48k requests**. At Sorsa Pro ($199/100k) ≈ **~$95**; Starter
  ($49/10k) would need ~5 packs (~$235). Rate limit ~20 req/sec ⇒ 48k reqs ≈ 40 min wall.
- Each call to `/top-followers` yields ~20 labeled `(account→score)` pairs ⇒ ~320k score
  observations for ~16k calls — a large supervised set for cheap.

> **Critical path for parity = a funded TweetScout/Sorsa key with enough quota.** The
> harness (below) is built and ready; it runs the instant the key is live. The current
> key was out of quota on 2026-06-14.

## 4. Calibration mechanics (app/services/calibration.py)

- **Fit:** `IsotonicRegression(out_of_bounds="clip")` on pairs `(our_raw_score(X),
  their_score(X))` for accounts present in both datasets. Monotonic ⇒ rank-preserving.
- **Validate (holdout):** Spearman ρ, Kendall τ, MAE on the (rescaled) score, and
  tier-match accuracy once tier thresholds are known (see research, filling `TIERS`).
- **Apply:** serve `display_score = isotonic(our_raw)`. Persist the fitted knots so the
  API doesn't refit per request.
- **Fixed test points** (public TweetScout dashboard scores) to sanity-check absolute
  scale: Cointelegraph ≈ 2905, opensea ≈ 4224, PublicAI ≈ 445. (Verify live; research
  will confirm/extend.)

## 5. What the algo research (2026-06-15) confirmed vs. left open

**Confirmed & encoded:**
- Sorsa = TweetScout rebrand (Scouts Labs Inc). Exactly **3 categories** (influencers /
  projects / venture_capitals) — `followers_stats()` collapses our 8 → these 3.
- **Tiers 1–5 ascending**, derived from the *dashboard* (thousands) score: Tier 5
  "Supreme" (~2905–3835+), Tier 4 "Significant", Tier 2 "Noted" (~289). Encoded in
  `app/core/tiers.py` with provisional thresholds + `learn_thresholds()` to fit the real
  cut-offs from harvested `(dashboard_score → tier)` pairs. Tier 1 & 3 names unconfirmed.
- Known dashboard anchors saved to `data/known_scores.json` (drift over time — re-pull).

**No shortcut exists:** the exact algorithm is undisclosed and **no public clone /
reverse-engineering exists**. PageRank is *inferred*. Parity is therefore purely
empirical — fit our output to harvested labels. This validates the harness approach.

**Two empirical unknowns the first live pull MUST settle** (run `scripts/probe_apis.py`):
1. **🔴 Linchpin — does `/top-followers` return a usable per-account score?** Their docs
   contradict themselves (schema says yes w/ a copy-paste bug; other pages say no).
   `SorsaClient.probe_top_followers_score()` settles it. If NO → harvest labels via
   direct `/score` per discovered account (costlier; budget accordingly).
2. **Scale mapping:** three undocumented scales — API `/score` (~1.25), top-follower
   `score` (~100), dashboard (~2905). Pull API + dashboard for the same accounts and
   regress. We target the **dashboard score** (what users see); `harvest()` stores it in
   `GroundTruth.dashboard_score`, and `fit()` should target it for tier-correct parity.

Still unknown (not needed for v1): single-pass vs iterative; exact damping/normalization;
category-assignment methodology; M size / refresh cadence; verified rate-limit & pricing.

## 6. Status

Harness (harvest + store + fit + measure) is built and unit-safe. Blocked only on a
funded TweetScout/Sorsa key. Tier thresholds + exact normalization pending research.

# Kaito Mindshare Arena — Reverse-Engineering Reference

Goal: rebuild Kaito's mindshare system "literally like them" on **our own data** (the loudrr
gateway follow-graph + PageRank we already have). This doc is the map: every endpoint we'd need,
the full taxonomy, how to extract their **smart sets per niche**, their scoring model, and the
plan to replicate it. Everything here was verified live 2026-06-30 unless marked *(inferred)*.

Companion code: `app/clients/kaito.py` (Camoufox reader), `scripts/scrape_kaito.py` (hourly sweep),
tables `kaito_captures` / `kaito_mindshare` (`app/db/models.py`). Method memory:
`kaito-mindshare-scrape-cracked`.

---

## 1. Access / anti-bot (how we get in)

**Headline: the data API needs no browser.** The `/voices/*` JSON endpoints on
`https://hub.kaito.ai/api/v1` (the bundle's `YAPS_API_ENDPOINT`) are **not** Cloudflare-challenged
and need **no** `cf_clearance` cookie, session, or proof-of-work. A plain request with browser-like
headers (`Origin`/`Referer`/`User-Agent` = `https://kaito.ai`) and a Chrome TLS fingerprint
(`curl_cffi impersonate=chrome124`) returns `200` JSON — verified through **datacenter (Webshare)
proxies**, which work precisely because there is no challenge to fail. → production path is
`KaitoHTTPClient` (curl_cffi + rotating proxies, async).

- **Cloudflare** only fronts the `kaito.ai` **HTML pages** (the managed JS challenge curl can't
  pass). We don't need them — everything renders from the API. `KaitoClient` (Camoufox: headful,
  click Turnstile → `cf_clearance`) is kept **only as a fallback** if they ever gate the API host.
- **Proof-of-work** (`x-challenge`/`x-nonce`/`x-hash` from `/analysis/session/validate`,
  `sha256(challenge:nonce)`, fractional difficulty, decrypted challenge) gates **only the legacy**
  `/kol/mindshare*` + `/tickers/mindshare*` paths. The live `/voices/*` need none of it.
- **API contract:** `limit` caps at **100** (101→400); no pagination (`offset` 400s; `page/cursor`
  ignored → always top-100). Leaderboard durations `{24h,7d,30d,3m,6m,12m}`; `followers_change`
  rejects `12m`. The complete per-niche roster = **union of the top-100 across all durations**.

---

## 2. Taxonomy

**Verticals** (companies side calls "Trading" `equity`; voices side calls it `trading`):

| UI vertical | companies path | voices path |
|---|---|---|
| Crypto | `crypto` | `crypto` |
| AI | `ai` | `ai` |
| Trading / Stocks | `equity` | `trading` |
| Pre-IPO | `preipo` *(companies only)* | — |

**Sectors** (`sector=` sub-section; from bundle `HISTORICAL_SECTORS`; `disabled` = "coming soon"):

- companies/crypto: `ALL`, `PRETGE` (Pre-TGE), `INFOMKT` (Info Markets), `EXCHANGE` *(+`VC` disabled)*
- voices/crypto: `ALL`, `TopicPerpDEX` (Perp DEX), `PredictionMarkets` (Info Market) *(+Stablecoin/RWA/DeFi/ZK/Privacy/GameFi disabled)*
- ai (both): `ALL`, `foundation_model`, `coding_agents`, `image_gen`, `video_gen`, `robotics`, `vibe_apps` *(+voice_ai/hardware/search/gen_apps/enterprise_video/music_gen disabled)*
- trading/equity: `ALL` only

**Durations** (bundle `TickerDateArray`): `24h`, `48h`, `7d`, `30d`, `3m`, `6m`, `12m`.
Note: "Project Leaderboards" is a separate campaign tab, **not** a sector — out of scope per spec.

---

## 3. API surface — full reference

Base: `GET https://hub.kaito.ai/api/v1`. The mindshare data lives under `/voices/{vertical}/…`.
The **companies** kind uses the `company_`-prefixed names; the **voices/KOL** kind the bare names.

### 3a. Mindshare arena (the system we replicate)

| Endpoint (voices / companies) | Params | Returns |
|---|---|---|
| `sector_leaderboard` / `company_sector_leaderboard` | `sector,duration,limit` | **ranked leaderboard** (the niche roster) |
| `mindshare_heatmap` / `company_mindshare_heatmap` | `sector,duration,top_n` | treemap weights (`mindshare`,`current`,`delta`,`rank`) |
| `mindshare_delta_all` / `company_mindshare_delta_all` | `sector,limit` | movers w/ per-window history (`last_{24h,7d,30d,3m,6m,12m}_mindshare`, `change_*`, `*_ratio`) |
| `mindshare_ratio_all` / `company_mindshare_ratio_all` | `sector` | share ratios across windows |
| `mindshare_language` / `company_mindshare_language` | `language,duration,limit` | leaderboard filtered by tweet language |
| `followers_change` / `company_followers_change` | `type=sf,duration,limit` | smart-follower gainers/losers |
| `batch_mindshare_line` / `batch_company_mindshare_line` | `user_ids=`/`entities=`,`sector,duration` | mindshare time-series for a set |
| `hot_topics_overview`, `hot_topics_by_date`, `hot_topic_detail` | `limit` / date | trending narratives in the niche |
| `github_repos`, `github_repo_detail` *(ai only)* | `duration,limit` | dev-activity leaderboard |

**Row shapes (verified):**
- companies `sector_leaderboard`: `{rank, company_id, name, logo, mindshare, mindshare_delta}`
  (`company_id` like `BTC`, `STOCK_ENTITY_SPCX`, `AI_ENTITY_Anthropic`).
- voices `sector_leaderboard`: `{rank, user_id, name, username, avatar, mindshare, mindshare_delta}`
  ← **`user_id` + `username` = the smart-set roster** (§4).

### 3b. KOL (per-creator) — legacy, PoW-gated

`/kol/mindshare/{id}` (a creator's mindshare), `/kol/mindshare-line` (their series),
`/kol/user_cross_leaderboard?twitter_user_id=&community_tier=` (**which niches/communities a KOL
ranks in** — useful to reconstruct roster membership), `/kol/mindshare/top-leaderboard`,
`/kol/mindshare/top-pre-tge`. `/tickers/mindshare`,`/tickers/mindshare-line`,`/tickers/engagement`.

### 3c. Yaps / smart-following (the scoring + roster layer)

`/yapper/feeds`, `/yapper/leaderboard/mindshare_history`,
`/yapper/public-smart-following-detail` *(roster/edges of smart followers — high value)*,
`/yapper/fetch-tickers-official-accounts` (official account per ticker),
`/yapper/yaps-trend`, `/yapper/users/hodler-score`, `/yapper/connect-stats`,
`/yapper/public-search-post`, `/users/twitter-stats?topic_id=` (per-topic twitter stats).

### 3d. Out of scope (mapped, not needed)

`/capital/*`, `/capital-launchpad/*` (token-sale/airdrop campaigns); `/api/v1/{passkeys,oauth,
siwe,siws,wallets,sessions,users/me,…}` (Privy auth/wallet). Ignore for mindshare.

---

## 4. Their smart sets per niche (the moat)

For each (vertical, sector) the **voices `sector_leaderboard`** returns the ranked tracked accounts
with `user_id` + `username`. Observations:
- Voices niches return **exactly `limit`** rows (100 → truncated) → real roster is **>100**.
  To get the *complete* roster, request a large `limit` (test 500/1000) and **union across all
  durations** (24h/7d/30d/3m/12m surface different members) — an account absent this window but
  present in a longer one is still in the set.
- Companies sectors return natural sizes (crypto EXCHANGE=26, INFOMKT=17, PRETGE=69) → those are
  full. ALL=100 is truncated; raise `limit` for the long tail.
- Cross-check / fill via `/kol/user_cross_leaderboard` (per-KOL community membership) and
  `/yapper/public-smart-following-detail`.

`scripts/scrape_kaito.py` already persists every roster row into `kaito_mindshare`
(`entity_id`=user_id, `handle`=username). Bumping `--limit` to ~1000 captures the full set; the
hourly union grows it to the complete moat. **This roster is exactly the curated set M we feed our
own PageRank** — i.e. Kaito just handed us per-niche labels.

---

## 5. Kaito's scoring model (reverse-engineered)

- **Mindshare** = normalized share-of-voice in a niche over a window: each tracked account's
  weighted mention/engagement volume ÷ niche total, so a niche's `mindshare` sums to ~1.0
  (verified: leaderboard values sum to ≈1). `mindshare_delta` = change vs the prior window.
- **Yaps composite** (literal, from the bundle):
  `score = round(100*yaps + smartFollowers + nfts*realTimeHolderExchangeRate)` — yaps (their
  attention/quality metric) dominate, plus smart-follower count, plus an NFT-holding term.
- **Smart followers** per account: `{smart_followers, smart_followers_last_30_days, changeRatio}`
  — followers that are themselves in the smart set (our `edges` give us this directly).
- *(inferred)* "Yaps" weight a tweet by author smart-follower reach × engagement × originality,
  decayed over the window — i.e. a reach-weighted, smart-audience-filtered engagement score.

---

## 6. Our rebuild on the gateway (replicate-and-beat)

We already have the hard parts (see memory `rescore-v2-98k-complete`, `twitterapi-crawl-engine-built`):
a 98k smart-set follow-graph (`edges`) and PageRank weights. Kaito's pieces map onto ours:

| Kaito concept | Our equivalent |
|---|---|
| Smart set per niche | scraped roster (§4) ∩ our `smart_set`; PageRank gives each member a weight |
| Smart followers of X | `edges` reverse index — who in M follows X (already O(log n)) |
| Mindshare of X in niche N | Σ over N's roster of (PageRank weight × that account's reach-weighted engagement on X) ÷ niche total |
| Yaps of a creator | reach-weighted engagement of their tweets, audience-filtered to M |
| Movers / deltas | snapshot diff of our hourly `kaito_mindshare`-style table |

Plan:
1. **Roster** — scrape Kaito's per-niche smart sets (§4) as ground-truth membership; reconcile to
   `smart_set`; crawl any missing handles via the gateway (`/user/info`, `/user/followings_ids`).
2. **Signal** — pull recent tweets + engagement for the niche (gateway/twitterapi advanced search
   by the roster handles), weight each by author PageRank × smart-audience engagement.
3. **Mindshare** — normalize per niche per window → our own leaderboard. Calibrate against the
   scraped Kaito numbers (same parity harness we used for Sorsa) until the ordering/spacing matches,
   then **re-weight (momentum/engagement) to beat them** — same playbook as the Sorsa work.
4. **Serve** — hourly cron writes snapshots; expose `/mindshare/{vertical}/{sector}` + movers.

---

## 7. Scrape system (operational)

- `scripts/scrape_kaito.py`: async fan of (kind × vertical × sector × duration × endpoint) via
  `KaitoHTTPClient` (curl_cffi + Webshare rotation, concurrency ~8). Full crypto sweep ≈ **75 s,
  ~3.1k rows** (323 companies + 711 KOLs). Raw → `kaito_captures`, parsed leaderboard rows →
  `kaito_mindshare`, JSON dumps + `manifest.json` → `data/kaito/<run_id>/` (`--from-dump` re-loads
  without scraping). `--browser` switches to the Camoufox fallback.
- **Proxy**: rotating **datacenter is fine** (no CF on the API). `data/proxies/webshare.txt`
  (`host:port:user:pass`), gitignored. Rotate per request; retry 429/5xx/network on a fresh proxy.
- **Cadence**: plain hourly cron on the Coolify box — no browser/display needed.
- Endpoints can rotate; `kaito_captures` keeps raw fidelity so we re-parse without re-scraping.

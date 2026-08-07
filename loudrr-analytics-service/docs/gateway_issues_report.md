# Loudrr Gateway — Data-Accuracy & Reliability Report

**To:** Gateway team
**From:** Analytics (follow-graph crawl)
**Endpoint under test:** `GET /twitter/user/followings` on `https://gateway.loudrr.com` (drop-in twitterapi.io)
**Context:** We crawl each smart-set member's *complete* following list to build an influence
graph. **Accuracy is mission-critical** — a truncated following list silently corrupts the
graph and produces wrong influence scores. We discovered the gateway does NOT reliably return
complete data under our workload. Details and evidence below.

---

## TL;DR — the three problems

1. **Silent truncation (CRITICAL / accuracy).** Under concurrency the endpoint returns a
   **short or empty list with HTTP 200 OK and `has_next_page: false`** — i.e. it *claims* the
   list is complete when it is not. There is no error status, so a client cannot tell a real
   "follows few people" from a truncated response. This silently corrupts data.
2. **Concurrency degradation.** The truncation correlates directly with concurrency. At 64
   concurrent requests (throttled to a shared 20 QPS) the endpoint truncates heavily; at a
   single connection the same accounts paginate correctly.
3. **Short pages.** `pageSize=200` is requested but the endpoint returns **~50–70 items per
   page**, inflating the request count ~3–4× and slowing every crawl.

---

## Evidence

### 1. Silent truncation under concurrency (HTTP 200, `has_next_page=false`, short body)

Crawl run at **concurrency 64** (all requests throttled under one 20 QPS limiter). For each
member we independently read the profile's true `following` count via `/user/info`, then
compared it to what `/user/followings` returned:

| account | profile `following` | captured via gateway | % captured |
|---|---|---|---|
| `@davemcclure` | 21,423 | 1,318 | **6%** |
| `@dhaber` | 8,860 | 469 | **5%** |
| `@bitcoinprophet1` | 8,828 | 469 | **5%** |
| `@Alts_Anonymous` | 3,403 | 1,211 | 36% |

Across the whole run, **no member returned more than 1,988 followings** — every large account
was silently cut off. These came back as **HTTP 200 with `has_next_page: false`** (a clean
"end of list"), not as errors — so without our own cross-check against `/user/info` we would
have written a 5%-complete follow graph and never known.

### 2. Same accounts paginate correctly at a single connection

We paginated `@davemcclure` (21,423 following) over **one connection**, no concurrency:

```
page 1: +70  (total 70)    has_next=True
page 2: +50  (total 120)   has_next=True
page 3: +49  (total 169)   has_next=True
... has_next stayed True, list kept growing past 12,000 ...
```

`has_next_page` stayed `True` and the list climbed past 12k (our test's own 200-page safety
stopped it, not the gateway). **So the data exists and is retrievable — the gateway only
truncates under concurrency.** The truncation is a load/concurrency artifact, not a hard cap.

### 3. Short pages (pageSize not honored)

Every page above requested `pageSize=200` but returned **49–70 items**. This is consistent
across the endpoint. Effect: a 20k-following account needs ~300–400 pages instead of ~100,
tripling request volume, latency, and cost for every crawl.

### 4. Throughput collapses under concurrency

At concurrency 64 (client-capped at 20 QPS) we measured an **effective ~4 requests/sec** —
far below the 20 QPS cap — with the truncation above. This suggests the gateway sheds load by
degrading responses (short pages / premature end) rather than cleanly queueing or returning
429s.

---

## Why this is severe for us

We build a follow-graph where each member's followings are *edges* that confer influence.
A response that is **wrong but looks successful** is the worst failure mode: it passes every
normal check (200 OK, valid JSON, `has_next_page:false`) and silently poisons the dataset.
We only caught it by independently fetching each account's `following` count and refusing any
capture below 90% of it — a costly workaround that re-crawls and burns extra requests.

---

## What we need from the gateway

1. **Never return a truncated list as a successful end-of-pagination.** If the upstream can't
   deliver the full page set, return a **non-2xx (429 or 5xx)** so clients retry — do **not**
   return HTTP 200 with `has_next_page:false` and a short body.
2. **Pagination correctness must be concurrency-independent.** `has_next_page` / `next_cursor`
   must stay correct regardless of how many concurrent requests are in flight. A request
   should never see a premature `has_next_page:false` because the gateway is under load.
3. **Honor `pageSize` (return up to 200/page),** or document the real maximum so we can plan
   request budgets. ~60/page tripled our crawl size.
4. **Publish — and ideally raise — the real concurrency and per-endpoint rate limits.** Tell
   us the safe concurrency for `/user/followings`; if the safe number is low, we need higher
   limits to crawl at scale.
5. **Confirm whether any followings-retrieval cap exists** (Twitter-style 5k/blue limits,
   etc.). Our single-connection test suggests none, but please confirm.

## How we're working around it today (so you see the cost)

- Drop concurrency from 64 → **10** (much slower, but pages come back complete).
- For **every** member: fetch `/user/info`, then **reject** any `/user/followings` capture
  below 90% of the profile's `following` count and re-crawl. This roughly **doubles** our
  request volume (one profile call + repeated followings passes per member) purely to defend
  against silent truncation. A correct gateway would let us delete this entirely.

---

# ADDENDUM — Direct vs Gateway, same request (definitive reproduction)

We ran the **identical** request against `api.twitterapi.io` (direct, our own key) and
`gateway.loudrr.com` (gateway key), same account, same params, paginating through the cursors:

```
GET /twitter/user/followings?userName=balajis&pageSize=200&cursor=<next_cursor>
```

| target | items returned per page | pages to finish @balajis (follows 3,913) | outcome |
|---|---|---|---|
| **api.twitterapi.io (DIRECT)** | **200, 200, 200, 200, 199, 200 … 200, 113** (last page) | **20** | complete: 3,912 captured ✅ |
| **gateway.loudrr.com** | **70, 50, 50, 50, 49, 50, 50, 48, 50, 50, 49, 50 …** | **~78** | ~4× the requests ❌ |

**Conclusion:** twitterapi.io itself honors `pageSize=200` perfectly and paginates to completion.
The gateway, for the *same* request, returns only **~50 items/page**. This is a **gateway-side
defect**, not an upstream limitation.

### The 3 concrete gateway fixes (in priority order)

1. **Honor `pageSize` on `/twitter/user/followings` (and `/followers`).**
   The gateway is returning ~50/page when 200 is requested. It must pass `pageSize` through to
   twitterapi.io **verbatim** and return the full page it gets back — not cap or post-trim it.
   *Impact: instant 4× fewer requests → 4× faster + 4× cheaper for every crawl.*

2. **Fix pagination correctness under concurrency.**
   At higher concurrency the gateway returns **premature `has_next_page:false` / empty pages**
   (HTTP 200), silently truncating lists (e.g. davemcclure: 21,423 following → 1,318 returned).
   Direct does not do this. The gateway must keep `has_next_page`/`next_cursor` correct
   regardless of in-flight request count, and **never return a truncated list as success** —
   surface a 429/5xx so clients retry instead.

3. **Stability under load.**
   During a concurrent crawl the gateway intermittently returns **HTTP 502** (observed live).
   Direct served the same load cleanly. The gateway proxy needs to handle concurrent
   `/user/followings` traffic without 502-ing.

### How the gateway team can reproduce
Run the two-target pagination above with a direct twitterapi.io key vs a gateway key and diff
the per-page counts — the ~50-vs-200 gap reproduces immediately on any account.

*Prepared from a live crawl + isolated pagination tests on 2026-06-20. Raw logs and the
per-account capture-vs-expected numbers available on request.*

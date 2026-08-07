# Gateway bug: `followings_ids` returns HTTP 503 on **protected (locked) accounts** instead of a clean empty response

## TL;DR
When a userId belongs to a **protected/private** X account, the gateway's
`/twitter/user/followings_ids` endpoint returns **HTTP 503** (looks like a transient
server error). Crawlers treat 503 as "retry with backoff", so they **retry-loop for
hours** on accounts whose follow-graph can never be read. **twitterapi.io handles the
same accounts gracefully** — it returns **HTTP 200 with an empty list** (`msg: "success"`),
which lets the client immediately skip the account.

Please make the gateway match twitterapi.io: for a protected account, return
**200 + empty list** (ideally with a `protected: true` marker), not 503.

## Reproduction
Endpoint: `GET /twitter/user/followings_ids?userId=<id>&pageSize=5000`

These 5 userIds reproduce it 100% of the time (all are `protected=true`):

| handle | userId | following |
|---|---|---|
| @beleevens | `123000274` | 5,717 |
| @KadunaBull | `1103404363861684236` | 2,064 |
| @Subli_Defi | `1399600570432905217` | 2,568 |
| @carlosjmelgar | `151378244` | 5,164 |
| @cuffyCapital | `2886401296` | 1,471 |

It is **not** size/pagination related — these accounts have 1.5k–5.7k following (below our
p90 of ~6k), and accounts with 8k+ following crawl fine. The only common factor is
`protected=true`.

## Gateway behavior (current)
```
GET /twitter/user/followings_ids?userId=123000274&pageSize=5000
-> HTTP 503   (repeated on every retry; never succeeds)
```
Client effect: 503 is retried with Retry-After/backoff up to the max-attempt cap, then the
member is deferred and re-attempted on the next pass — i.e. an infinite, capacity-burning
loop on accounts that are unreadable by design.

## twitterapi.io behavior (the desired behavior)
Same accounts, against `api.twitterapi.io` with the same auth model:

```
GET /twitter/user/info?userName=beleevens
-> 200  { data: { userName: "beleevens", protected: true, following: 5717, followers: 5895, ... } }

GET /twitter/user/followings?userName=beleevens&pageSize=200
-> 200  { followings: [], has_next_page: false, msg: "success", code: 0 }

GET /twitter/user/followers?userName=beleevens&pageSize=200
-> 200  { followers: [], has_next_page: false, msg: "success", code: 0 }
```

Control (a public account, same call) returns data normally:
```
GET /twitter/user/followings?userName=APompliano&pageSize=200
-> 200  { followings: [ ...200 users... ], has_next_page: true, msg: "success" }
```

So twitterapi.io returns **200 + empty + `success`** for protected accounts. The client sees
`has_next_page=false` and an empty page, marks the account complete-with-zero, and moves on —
**no retries, no wasted capacity.**

## Requested fix
For protected/private accounts on `followings_ids` (and `followers_ids`/`followings`/`followers`):
1. Return **HTTP 200** with an **empty list** and `has_next_page: false` (matches twitterapi.io), and
2. Optionally include `protected: true` (or `code`/`msg`) in the body so clients can label the
   account explicitly rather than inferring from an empty result.

This single change stops crawlers from retry-looping on locked accounts and frees the X-account
pool capacity those retries were consuming.

## Bonus: how clients can pre-detect (if useful on your side)
`GET /twitter/user/info?userName=<h>` already returns `protected: true` for these accounts —
so the gateway has the signal available to short-circuit `followings_ids` to an empty 200.

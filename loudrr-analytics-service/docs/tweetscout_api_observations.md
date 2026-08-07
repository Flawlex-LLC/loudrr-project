# TweetScout / Sorsa API — observed shape (study for our own API)

> Living record of TweetScout/Sorsa's API (`https://api.sorsa.io/v3`) — request +
> **response shapes**, auth/error contract, and design patterns — so we mirror a
> proven structure in our own API. Schemas from the public `swagger.json` (no key
> needed); live behaviors from actual calls. Live calls also auto-append to
> `data/tweetscout_calls.jsonl` (see `app/clients/sorsa.py`).
>
> **Date:** 2026-06-15. **Auth header:** `ApiKey: <key>`. 40 endpoints, 8 tags.

## 1. Auth / error contract (observed live) — copy this for our API

The gate runs **before routing or param validation** — even `/nonexistent` returns the
payment error, not 404. Order inferred: **header present → key paid → quota → route →
validate params → execute**. Three distinct 403 states (all `403`, JSON `{"message": …}`):

| Condition | Status | Body |
|---|---|---|
| Missing/blank `ApiKey` header | 403 | `{"message":"missing key in request header"}` |
| Key recognized but **unpaid** | 403 | `{"message":"api key not payed"}` ← our current key |
| Key paid but **quota exhausted** | 403 | `{"message":"request limit exceeded"}` ← prior key |

Takeaways for our API: single flat error envelope `{"message": str}`; auth/billing
checked first (cheap rejects); same 403 for all auth/billing failures (no info leak about
whether a route exists). We may want distinct codes (401 vs 402 vs 429) — see §5.

## 2. Balance check — `GET /key-usage-info`

Response shape (from swagger): `{ key_requests, remaining_requests, total_requests,
valid_until }`. **This is how we verify balance before harvesting** — `remaining_requests
> 0` and `valid_until` in the future. (Currently 403 "api key not payed".)

## 3. The scoring product (7 endpoints) + the linchpin resolution

| Endpoint | Method | Params | Response |
|---|---|---|---|
| `/score` | GET | `user_link`\|`username`\|`user_id` | `{ score: number }` |
| `/score-changes` | GET | ″ | `{ week_delta, month_delta }` |
| `/followers-stats` | GET | ″ | `{ followers_count, influencers_count, projects_count, venture_capitals_count, user_protected }` |
| `/top-followers` | GET | ″ | `{ users: [**TopFollower**] }` |
| `/top-following` | GET | ″ | `{ users: [Follower] }` |
| `/new-followers-7d` | GET | ″ | `{ users: [Follower] }` |
| `/new-following-7d` | GET | ″ | `{ users: [Follower] }` |

**🟢 Linchpin RESOLVED from the schema** (the research couldn't settle this from prose):
- **`TopFollower`** (only `/top-followers`) HAS a `score` field → per-account ground-truth
  labels are real. Full fields: `id, username, display_name, description, followers_count,
  followings_count, tweets_count, verified, protected, can_dm, created_at,
  profile_image_url, profile_background_image_url, **score**`.
- **`Follower`** (`/top-following`, `/new-*-7d`) has **no** `score` — instead
  `followerDate` (+ `favourites_count, media_count, bio_urls, pinned_tweet_ids,
  possibly_sensitive`). So those endpoints can't be used for score labels.
- ⇒ **Harvest score labels ONLY from `/top-followers`.** (Still worth 1 live confirmation
  when funded — there's a known doc copy-paste bug where `score`'s *description* reads
  "Whether the account accepts direct messages"; the field itself is numeric.)

## 4. Full endpoint catalog (request → response top-level shape)

Generic X-data endpoints are ~1:1 with twitterapi.io (we serve these ourselves).

| Method | Path | Params / Body | Response top-level |
|---|---|---|---|
| GET | `/about` | user_link\|username\|user_id | `{country, last_username_change_at, username_change_count}` |
| GET | `/info` | user_link\|username\|user_id | full profile (`id, username, display_name, description, followers_count, followings_count, tweets_count, favourites_count, media_count, verified, protected, location, created_at, bio_urls, pinned_tweet_ids, can_dm, possibly_sensitive, profile_image_url, profile_background_image_url`) |
| GET | `/info-batch` | usernames, user_ids | `{users:[...]}` |
| GET | `/followers` | user(+next_cursor) | `{next_cursor, users:[...]}` |
| GET | `/follows` | user(+next_cursor) | `{next_cursor, users:[...]}` |
| GET | `/verified-followers` | user(+next_cursor) | `{next_cursor, users:[...]}` |
| GET | `/link-to-id` | link | `{id}` |
| GET | `/id-to-username/{user_id}` | path | `{handle}` |
| GET | `/username-to-id/{user_handle}` | path | `{id}` |
| GET | `/spaces` | id\|link | rich Space obj (creator, participants, settings, stats) |
| GET | `/trends` | woeid | `{trends:[...]}` |
| GET | `/list-members` | list_id(+next_cursor) | `{next_cursor, users:[...]}` |
| GET | `/list-followers` | list_link(+next_cursor) | `{next_cursor, users:[...]}` |
| GET | `/list-tweets` | list_id(+next_cursor) | `{next_cursor, tweets:[...]}` |
| GET | `/check-comment` | tweet_link + user | `{commented, tweet}` |
| POST | `/article` | BODY tweet_link | article obj (full_text, counts, author, …) |
| POST | `/tweet-info` | BODY tweet_link | full tweet obj (counts, entities, user, quoted/retweeted) |
| POST | `/tweet-info-bulk` | BODY tweet_links | `{tweets:[...]}` |
| POST | `/user-tweets` | BODY user(+next_cursor) | `{next_cursor, tweets:[...]}` |
| POST | `/comments` | BODY tweet_link(+next_cursor, order_by) | `{next_cursor, tweets:[...]}` |
| POST | `/quotes` | BODY tweet_link(+next_cursor) | `{next_cursor, tweets:[...]}` |
| POST | `/retweeters` | BODY tweet_link(+next_cursor) | `{next_cursor, users:[...]}` |
| POST | `/mentions` | BODY query(+filters,next_cursor) | `{next_cursor, tweets:[...]}` |
| POST | `/search-tweets` | BODY query(+order,next_cursor) | `{next_cursor, tweets:[...]}` |
| POST | `/search-users` | BODY query(+next_cursor) | `{next_cursor, users:[...]}` |
| POST | `/check-follow` | BODY two users | `{follow, user_protected}` |
| POST | `/check-retweet` | BODY tweet+user(+next_cursor) | `{retweet, user_protected, next_cursor}` |
| POST | `/check-quoted` | BODY tweet+user | `{status, text, date, user_protected}` |
| POST | `/check-community-member` | BODY community+user | `{is_member}` |
| POST | `/community-members` | BODY community_link(+next_cursor) | `{next_cursor, users:[...]}` |
| POST | `/community-tweets` | BODY community_id(+order,next_cursor) | `{next_cursor, tweets:[...]}` |
| POST | `/community-search-tweets` | BODY community+query | `{next_cursor, tweets:[...]}` |

## 5. Design patterns to adopt / improve for OUR API

- **Param flexibility:** all user-addressed endpoints accept `user_link` | `username` |
  `user_id` (one-of). Mirror this — friendly + cache-key-able.
- **Cursor pagination:** uniform `next_cursor` in/out on list endpoints. Mirror.
- **Verb split:** GET for single-key lookups, POST (JSON body) for query/multi-param.
- **Flat error envelope** `{"message": str}` — simple. *Improvement:* use proper status
  codes — `401` missing key, `402` unpaid, `429` quota (+ `Retry-After`) — instead of a
  blanket `403`, and add a machine-readable `code`.
- **Out-API them (from research §4):** expose what their API hides — bot%, engagement%,
  trust score, named tier on `/score`, batch `/scores`, an aggregated smart-follower
  weight, and full score history (they give only 7d/30d deltas).

## 5b. 🔑 LIVE findings (funded key f6b772f7, 2026-06-15)

Confirmed against the live API (key has 10,000 requests, valid until 2026-07-15;
`/key-usage-info` appears NOT to decrement quota — `key_requests` stayed 0 after it):

- **Linchpin TRUE:** `/top-followers` returns a numeric per-account `score` for every
  entry (e.g. a cz_binance top-follower scored 5161.41). 20 scored M-members/request.
- **ALL THREE SCALES ARE ONE (thousands).** For cz_binance: API `/score` = **4183.88**,
  dashboard = **4184.0** (Tier 5 Supreme), and top-follower scores are the same
  magnitude (5161). The docs' `score:1.25` and TopFollower `score:100` are PLACEHOLDER
  examples, not the real scale. ⇒ **No API↔dashboard scale regression needed.** `/score`
  already returns the dashboard number; `/top-followers` scores are dashboard-scale ranks.
- **Consequences for the 10k plan:** (1) harvested top-follower scores are directly the
  ranked-M scores we calibrate to — no rescale; (2) the ~680 requests budgeted for
  scale-mapping are freed → redirect to snowball/breadth; (3) tier thresholds can be
  learned from FREE dashboard reads (score+tier), spending ~0 API quota; (4) still verify
  GLOBAL-vs-edge-local in the pilot (cz's /score matching dashboard strongly implies the
  score is a global per-account value, so the `func.greatest` global table is sound).

## 6. Live-call log

Every live call auto-appends a shape record to `data/tweetscout_calls.jsonl`
(method, path, params, status, response keys+types, message). Seeded patterns above are
from manual calls on 2026-06-15 (unpaid key). Re-run `scripts/probe_apis.py` once funded
to capture live 200 bodies.

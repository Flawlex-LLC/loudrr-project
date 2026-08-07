"""Harvest EVERYTHING Sorsa exposes, not just scores — so we can diff our whole model against
theirs and close the gap.

Why each endpoint matters:
  * /followers-stats -> influencer/project/VC counts. CAREFUL: its `followers_count` is the
    account's TOTAL X followers, NOT a smart-follower count (verified against the gateway:
    Sorsa says 240,848,562 for @elonmusk = his real follower count). The comparable-to-us
    number is influencers+projects+vcs. That sum for @elonmusk is 42,878 — matching Sorsa's
    own UI ("42,840 top followers") — vs our 55,600 elite followers. So THEIR voter set is
    ~42.8k and ours is 98k, and we over-count worst on small accounts (@0xblest_: 561 theirs
    vs 1,801 ours = 3.2x). This pass measures that gap across the whole board.
  * /top-followers  -> THEIR actual voter identities (20 per account, with profiles). Across
    thousands of accounts these repeat heavily, so this reconstructs a large slice of Sorsa's
    voter universe — the thing we can't otherwise see.
  * /score-changes  -> week/month deltas (cheap to grab alongside; useful for momentum work).

Quota-aware: /score is the calibration ground truth and gets first claim on the budget; the
rest spend what's left. Resumable per endpoint. Writes to prod tables AND local CSVs.

    SORSA_KEY=... python -m scripts.harvest_sorsa_full stats
    SORSA_KEY=... python -m scripts.harvest_sorsa_full topfollowers
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import sys

sys.path.insert(0, ".")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.clients.sorsa import SorsaClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("sorsa_full")
logging.getLogger("httpx").setLevel(logging.WARNING)

KEY = os.environ.get("SORSA_KEY") or ""
CONC = int(os.environ.get("SORSA_CONCURRENCY") or "14")
QPS = int(os.environ.get("SORSA_QPS") or "14")
RESERVE = 500
# score-changes is a SECOND billable call per account — it would double the stats pass
# (33,891 -> 67,782) and starve the top-followers pass. Deltas are the least valuable of the
# three, so they're opt-in.
WITH_DELTAS = os.environ.get("SORSA_DELTAS") == "1"

DDL_STATS = """
CREATE TABLE IF NOT EXISTS sorsa_stats (
    username              varchar(64) PRIMARY KEY,
    user_id               varchar(32),
    followers_count       integer,   -- TOTAL X followers (NOT smart) - verified vs gateway
    influencers_count     integer,
    projects_count        integer,
    venture_capitals_count integer,
    user_protected        boolean,
    week_delta            double precision,
    month_delta           double precision,
    our_elite_followers   integer,   -- ours, for a direct diff
    raw                   jsonb,
    fetched_at            timestamp DEFAULT now()
)
"""

DDL_TF = """
CREATE TABLE IF NOT EXISTS sorsa_top_followers (
    account      varchar(64),
    position     integer,
    follower_id  varchar(32),
    follower     varchar(64),
    display_name varchar(128),
    followers_count integer,
    followings_count integer,
    verified     boolean,
    raw          jsonb,
    PRIMARY KEY (account, position)
)
"""


def quota() -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.get("https://api.sorsa.io/v3/key-usage-info", headers={"ApiKey": KEY})
        return r.json() if r.status_code == 200 else {}


async def run(mode: str) -> None:
    # pool_pre_ping + recycle: the public DB proxy silently drops long-lived connections
    # ("connection is closed" killed a 70%-complete run). Pre-ping revalidates a pooled
    # connection before use, and recycling caps how long any single one lives.
    eng = create_async_engine(os.environ["DATABASE_URL"], connect_args={"command_timeout": 300},
                              pool_pre_ping=True, pool_recycle=300)
    async with eng.begin() as c:
        await c.execute(text(DDL_STATS))
        await c.execute(text(DDL_TF))

    # scores are the ground truth -> reserve budget for whatever's still missing there
    async with eng.connect() as c:
        missing_scores = (await c.execute(text("""
            select count(*) from ranked_accounts r
            left join sorsa_scores s on s.username=r.username where s.username is null"""))).scalar() or 0
        if mode == "stats":
            todo = (await c.execute(text("""
                select r.username, r.user_id, r.elite_followers
                from ranked_accounts r
                left join sorsa_stats s on s.username=r.username
                where s.username is null order by r.rank"""))).all()
        else:
            todo = (await c.execute(text("""
                select r.username, r.user_id, r.elite_followers
                from ranked_accounts r
                where r.username not in (select distinct account from sorsa_top_followers)
                order by r.rank"""))).all()

    q = quota()
    remaining = int(q.get("remaining_requests", 0))
    budget = max(0, remaining - RESERVE - missing_scores)
    log.info("quota: %s remaining | scores still owed: %s | budget for %s: %s",
             f"{remaining:,}", f"{missing_scores:,}", mode, f"{budget:,}")
    log.info("%s accounts pending for %s", f"{len(todo):,}", mode)
    todo = todo[:budget]
    if not todo:
        log.info("nothing to do (no budget or already complete)")
        await eng.dispose()
        return
    log.info("-> fetching %s accounts", f"{len(todo):,}")

    client = SorsaClient(api_key=KEY, qps=QPS, max_connections=CONC)
    sem = asyncio.Semaphore(CONC)
    buf: list = []
    stats = {"ok": 0, "err": 0}
    stop = asyncio.Event()

    os.makedirs("data/exports", exist_ok=True)
    csv_path = f"data/exports/sorsa_{mode}.csv"
    new = not os.path.exists(csv_path)
    fh = open(csv_path, "a", newline="", encoding="utf-8")
    w = csv.writer(fh)
    if new:
        w.writerow(["account", "position", "follower", "follower_id", "followers_count"]
                   if mode == "topfollowers" else
                   ["username", "sorsa_followers", "influencers", "projects", "vcs",
                    "our_elite_followers", "week_delta", "month_delta"])

    async def one(uname, uid, ours):
        if stop.is_set():
            return
        async with sem:
            try:
                if mode == "stats":
                    d = await client.followers_stats(uname)
                    ch = {}
                    if WITH_DELTAS:
                        try:
                            ch = await client.score_changes(uname)
                        except Exception:  # noqa: BLE001 — deltas are a bonus, never fatal
                            ch = {}
                    buf.append(("stats", uname, uid, d, ch, ours))
                else:
                    users = await client.top_followers(uname)
                    buf.append(("tf", uname, users))
                stats["ok"] += 1
            except Exception as e:  # noqa: BLE001
                if "Quota" in type(e).__name__ or "Budget" in type(e).__name__:
                    log.warning("quota exhausted — stopping cleanly")
                    stop.set()
                    return
                stats["err"] += 1

    async def flush():
        """ONE executemany per batch — per-row inserts over the public proxy throttled the
        harvest to ~2 req/s (the API allows ~18)."""
        if not buf:
            return
        rows = list(buf)
        buf.clear()
        stat_params, tf_params = [], []
        for r in rows:
            if r[0] == "stats":
                _, uname, uid, d, ch, ours = r
                stat_params.append({
                    "u": uname, "i": uid, "f": d.get("followers_count"),
                    "inf": d.get("influencers_count"), "p": d.get("projects_count"),
                    "v": d.get("venture_capitals_count"), "pr": d.get("user_protected"),
                    "wd": ch.get("week_delta"), "md": ch.get("month_delta"),
                    "oe": ours, "raw": json.dumps({**d, **ch})})
                w.writerow([uname, d.get("followers_count"), d.get("influencers_count"),
                            d.get("projects_count"), d.get("venture_capitals_count"), ours,
                            ch.get("week_delta"), ch.get("month_delta")])
            else:
                _, uname, users = r
                for pos, u in enumerate(users):
                    tf_params.append({
                        "a": uname, "p": pos, "fi": str(u.get("id") or ""),
                        "f": (u.get("username") or "").lower(),
                        "d": (u.get("display_name") or "")[:128],
                        "fc": u.get("followers_count"), "fg": u.get("followings_count"),
                        "v": bool(u.get("verified")), "raw": json.dumps(u)})
                    w.writerow([uname, pos, (u.get("username") or "").lower(),
                                u.get("id"), u.get("followers_count")])
        # a dropped proxy connection must NOT kill a multi-hour harvest: retry, and only then
        # give up on this batch (the CSV mirror below still has the rows either way)
        for attempt in range(4):
            try:
                async with eng.begin() as c:
                    if stat_params:
                        await c.execute(text("""
                            insert into sorsa_stats (username,user_id,followers_count,influencers_count,
                              projects_count,venture_capitals_count,user_protected,week_delta,month_delta,
                              our_elite_followers,raw)
                            values (:u,:i,:f,:inf,:p,:v,:pr,:wd,:md,:oe,cast(:raw as jsonb))
                            on conflict (username) do update set followers_count=excluded.followers_count,
                              raw=excluded.raw, fetched_at=now()"""), stat_params)
                    if tf_params:
                        await c.execute(text("""
                            insert into sorsa_top_followers (account,position,follower_id,follower,
                              display_name,followers_count,followings_count,verified,raw)
                            values (:a,:p,:fi,:f,:d,:fc,:fg,:v,cast(:raw as jsonb))
                            on conflict (account,position) do nothing"""), tf_params)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    log.warning("db write failed after retries (%s) — rows kept in CSV",
                                type(e).__name__)
                    break
                await eng.dispose()
                await asyncio.sleep(2 ** attempt)
        fh.flush()

    CH = 300
    for i in range(0, len(todo), CH):
        if stop.is_set():
            break
        await asyncio.gather(*(one(u, i_, e) for u, i_, e in todo[i:i + CH]))
        await flush()
        log.info("  %s/%s  ok=%s err=%s  spent=%s",
                 min(i + CH, len(todo)), len(todo), stats["ok"], stats["err"],
                 client.requests_spent)
    await flush()
    await client.aclose()
    fh.close()
    await eng.dispose()
    log.info("DONE %s: ok=%s err=%s | local=%s", mode, stats["ok"], stats["err"], csv_path)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if mode not in ("stats", "topfollowers"):
        raise SystemExit("usage: harvest_sorsa_full.py [stats|topfollowers]")
    asyncio.run(run(mode))

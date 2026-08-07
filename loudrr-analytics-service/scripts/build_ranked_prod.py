"""Build the public serving table `ranked_accounts` on PROD from the real graph.

The fix for the voter/discovered score split: EVERY account gets one honest score on one
scale — score_for(X) = Σ PageRank weight of the smart-set members following X (app.services.
score.score_for), mapped through the locked loudrr_score() calibration. No CSV rescale.

Proxy-safe (reuses scripts/rescore_sql fire-and-poll): heavy aggregates run server-side and
survive client drops; only the ~130k named+scored rows transfer, in id-keyset batches.

    python -m scripts.build_ranked_prod
"""
from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from app.core.loudrr_score import loudrr_score
from scripts.rescore_sql import (  # proven proxy-safe helpers
    DROP_ERRORS as _DROP,
    connect,
    create_and_wait,
    _retry,
    reconnect,
)

PROF_CSV = Path("data/exports/profiles_enriched.csv")
INS_BATCH = 500


async def _regexists(h, table) -> bool:
    return (await _retry(h, "fetchval", f"SELECT to_regclass('public.{table}')")) is not None


async def _push_profiles(h) -> int:
    """Load profiles_enriched (usernames/names for discovered accounts) into a prod temp
    table so non-member followees can be named. Members are already named via smart_set."""
    await _retry(h, "execute", "DROP TABLE IF EXISTS prof_import")
    await _retry(h, "execute", """
        CREATE TABLE prof_import (
            user_id text PRIMARY KEY, username text, name text,
            followers bigint, following bigint, verified bool, bio text)""")
    rows = []
    with open(PROF_CSV, encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            uid = (r.get("user_id") or "").strip()
            un = (r.get("username") or "").strip()
            if not uid or not un:
                continue
            def i(v):
                try: return int(float(v)) if v not in (None, "", "NIL") else None
                except (TypeError, ValueError): return None
            rows.append((uid, un, r.get("name") or None, i(r.get("followers")),
                         i(r.get("following")),
                         str(r.get("blue_verified") or "0") in ("1", "True", "true"),
                         (r.get("description") or None)))
    for i in range(0, len(rows), INS_BATCH):
        await _retry(h, "execute",
                     "INSERT INTO prof_import VALUES " +
                     ",".join(f"(${j*7+1},${j*7+2},${j*7+3},${j*7+4},${j*7+5},${j*7+6},${j*7+7})"
                              for j in range(len(rows[i:i+INS_BATCH]))) +
                     " ON CONFLICT (user_id) DO NOTHING",
                     *[x for row in rows[i:i+INS_BATCH] for x in row])
    return len(rows)


async def main() -> None:
    h = {"c": await connect()}
    print("connected to prod")

    # verify smart_set scores intact + drop any leftover rescore intermediates
    mx = await _retry(h, "fetchval", "SELECT MAX(score) FROM smart_set")
    print(f"smart_set max score = {mx} (expect 1000.0 — scores intact)")
    await _retry(h, "execute", "DROP TABLE IF EXISTS m2m_raw, m2m, od_t, pr_t, pr_new")

    n = await _push_profiles(h)
    print(f"prof_import: {n:,} discovered-account names pushed")

    # 1) score_for for every NAMEABLE account — driven FROM the ~130k targets via the
    #    ix_edges_followee index, in resumable batches (a full-table GROUP BY over 282M
    #    edges won't finish on this small container while the crawl writes concurrently).
    #    edges.follower_id is always a smart-set member, so SUM(m.score) over a followee's
    #    edges IS its raw score_for; COUNT is its smart-follower count.
    await _retry(h, "execute", "DROP TABLE IF EXISTS targets")
    await _retry(h, "execute", """CREATE TABLE targets AS
        SELECT user_id AS uid FROM smart_set UNION SELECT user_id FROM prof_import""")
    await _retry(h, "execute", "CREATE INDEX ON targets(uid)")

    # ONE sequential-scan hash aggregate (NOT per-target index lookups — random I/O on this
    # container's disk is death). enable_nestloop=off forces: seq-scan edges once, hash-probe
    # against the 98k targets, hash-join smart_set, hash-agg 98k groups. All hashes fit RAM;
    # only the sequential edge read costs. Crawl is paused so no I/O contention.
    for attempt in range(6):
        try:
            # NB: rescore_sql's connect() sets command_timeout=240 — that 4-min client
            # timeout was cancelling the heavy CREATE. Give this statement a real budget.
            await h["c"].execute("SET enable_nestloop=off")
            await h["c"].execute("SET work_mem='512MB'")
            await h["c"].execute("SET max_parallel_workers_per_gather=0")
            await h["c"].execute("DROP TABLE IF EXISTS ra_scored", timeout=120)
            print("building ra_scored (single seq-scan hash-agg, timeout=1h)…", flush=True)
            await h["c"].execute("""
                CREATE TABLE ra_scored AS
                SELECT e.followee_id AS uid, SUM(s.score) AS raw, COUNT(*) AS elite
                FROM edges e
                JOIN targets t     ON t.uid = e.followee_id
                JOIN smart_set s   ON s.user_id = e.follower_id
                GROUP BY e.followee_id""", timeout=3600)
            break
        except _DROP:
            print("   client dropped during CREATE; polling server-side completion…", flush=True)
            import time as _t
            t0 = _t.time()
            while _t.time() - t0 < 5400:
                await asyncio.sleep(10)
                try:
                    if await _regexists(h, "ra_scored"):
                        break
                except _DROP:
                    await reconnect(h)
            if await _regexists(h, "ra_scored"):
                break
            await reconnect(h)  # re-issue
    n_scored = await _retry(h, "fetchval", "SELECT COUNT(*) FROM ra_scored")
    print(f"ra_scored built — {n_scored:,} accounts with >=1 smart follower")

    # 2) attach names/profile (members via smart_set, else discovered via prof_import;
    #    categories from the vendor table) into a compact table to transfer.
    await create_and_wait(h, "ranked_named", """
        CREATE TABLE ranked_named AS
        SELECT s.uid, s.raw, s.elite,
               COALESCE(ss.username, pi.username)              AS uname,
               COALESCE(ss.display_name, pi.name)              AS name,
               COALESCE(ss.followers_count, pi.followers)      AS followers,
               COALESCE(ss.following_count, pi.following)      AS following,
               pi.bio                                          AS bio,
               COALESCE(pi.verified, false)                    AS verified,
               ts.categories                                   AS categories
        FROM ra_scored s
        LEFT JOIN smart_set ss ON ss.user_id = s.uid
        LEFT JOIN prof_import pi ON pi.user_id = s.uid
        LEFT JOIN twitterscore_accounts ts ON ts.user_id = s.uid
        WHERE COALESCE(ss.username, pi.username) IS NOT NULL
    """, poll_timeout=1800)

    # 3) pull the named+scored set in id-keyset batches (proxy-safe), score in Python
    rows: list[tuple] = []
    last = ""
    while True:
        batch = await _retry(h, "fetch",
            "SELECT uid, raw, elite, uname, name, followers, following, bio, verified, categories "
            "FROM ranked_named WHERE uid > $1 ORDER BY uid LIMIT 5000", last)
        if not batch:
            break
        for r in batch:
            rows.append(r)
        last = batch[-1]["uid"]
        if len(rows) % 25000 < 5000:
            print(f"  pulled {len(rows):,} rows…")
    print(f"pulled {len(rows):,} named+scored accounts")

    # 4) global ranking by the calibrated score
    scored = []
    for r in rows:
        raw = float(r["raw"] or 0.0)
        scored.append((r, raw, int(round(loudrr_score(raw)))))
    scored.sort(key=lambda x: (-x[2], -(x[0]["elite"] or 0)))

    # 5) rewrite ranked_accounts (batched upsert; TRUNCATE first for a clean rebuild)
    await _retry(h, "execute", "TRUNCATE ranked_accounts")
    cols = ("user_id", "rank", "username", "display_username", "name", "bio", "followers",
            "following", "verified", "elite_followers", "raw_score", "score", "categories")
    payload = []
    for i, (r, raw, score) in enumerate(scored, start=1):
        payload.append((str(r["uid"]), i, (r["uname"] or "").lower(), r["uname"], r["name"],
                        (r["bio"] or None), r["followers"], r["following"], bool(r["verified"]),
                        int(r["elite"] or 0), raw, score, r["categories"]))
    for i in range(0, len(payload), INS_BATCH):
        chunk = payload[i:i+INS_BATCH]
        vals = ",".join("(" + ",".join(f"${k*13+j+1}" for j in range(13)) + ")"
                        for k in range(len(chunk)))
        await _retry(h, "execute",
                     f"INSERT INTO ranked_accounts ({','.join(cols)}) VALUES {vals} "
                     "ON CONFLICT (user_id) DO NOTHING",
                     *[x for row in chunk for x in row])
        if i % 25000 == 0:
            print(f"  wrote {i:,}/{len(payload):,}")

    top = scored[:8]
    print(f"\nDONE — ranked_accounts rebuilt: {len(payload):,} accounts")
    print("top 8:", ", ".join(f"{r['uname']}({sc})" for r, _, sc in top))
    await _retry(h, "execute", "DROP TABLE IF EXISTS ranked_named, ra_scored, targets, prof_import")
    await h["c"].close()


if __name__ == "__main__":
    asyncio.run(main())

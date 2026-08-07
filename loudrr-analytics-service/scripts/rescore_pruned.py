"""Rescore with the founder-approved voter prune (drop spray-follow voters, out-degree > 5,000)
AND a Sorsa-fit calibration, on PROD. Dry-run by default — set WRITE=1 to commit.

Why both at once: the voter prune fixes ORDERING (removes follow-back noise), the recalibration
fixes SPREAD (86.8% of accounts were crushed into a 105-pt floor band). Applying only one leaves
the founder's complaint half-fixed. Calibration is refit to real Sorsa scores on the NEW raw.

    DATABASE_URL=<prod> python -m scripts.rescore_pruned         # dry run: show before/after
    DATABASE_URL=<prod> WRITE=1 python -m scripts.rescore_pruned # commit new ranked_accounts

Proxy-safe: heavy aggregates run server-side (fire-and-poll via rescore_sql); only compact
result sets transfer.
"""
from __future__ import annotations

import asyncio
import csv
import math
import os
import sqlite3
import statistics as st
from pathlib import Path

from scripts.rescore_sql import (
    DROP_ERRORS as _DROP,
    connect,
    create_and_wait,
    _retry,
    reconnect,
)

OUTDEG_CUT = 10_000_000    # KEEP spray-follow voters (founder call 2026-07-26): Sorsa counts them
                           # too, so we match their bar. Effectively no out-degree cut.
TOP_N = 40000              # smart set = top-40k by pr_rank (audited best-agreement + coverage)
FLOOR, CAP = 0.0, 6000.0   # no floor: an account with no smart backing scores 0, like Sorsa
PROF_CSV = Path("data/exports/profiles_enriched.csv")
INS_BATCH = 500


async def _regexists(h, table) -> bool:
    return (await _retry(h, "fetchval", f"SELECT to_regclass('public.{table}')")) is not None


async def _fire_and_wait(h, create_sql: str, table: str, timeout=3600, poll=5400):
    """Run a heavy CREATE TABLE, polling for server-side completion across client drops."""
    for _ in range(6):
        try:
            await h["c"].execute("SET enable_nestloop=off")
            await h["c"].execute("SET work_mem='512MB'")
            await h["c"].execute("SET max_parallel_workers_per_gather=0")
            await h["c"].execute(f"DROP TABLE IF EXISTS {table}", timeout=120)
            print(f"building {table} (server-side, timeout={timeout}s)…", flush=True)
            await h["c"].execute(create_sql, timeout=timeout)
            return
        except _DROP:
            print(f"   client dropped; polling for {table}…", flush=True)
            import time as _t
            t0 = _t.time()
            while _t.time() - t0 < poll:
                await asyncio.sleep(10)
                try:
                    if await _regexists(h, table):
                        return
                except _DROP:
                    await reconnect(h)
            if await _regexists(h, table):
                return
            await reconnect(h)


async def _push_profiles(h) -> int:
    await _retry(h, "execute", "DROP TABLE IF EXISTS prof_import")
    await _retry(h, "execute", """
        CREATE TABLE prof_import (user_id text PRIMARY KEY, username text, name text,
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
            rows.append((uid, un, r.get("name") or None, i(r.get("followers")), i(r.get("following")),
                         str(r.get("blue_verified") or "0") in ("1", "True", "true"),
                         (r.get("description") or None)))
    for k in range(0, len(rows), INS_BATCH):
        chunk = rows[k:k + INS_BATCH]
        await _retry(h, "execute",
                     "INSERT INTO prof_import VALUES " +
                     ",".join(f"(${j*7+1},${j*7+2},${j*7+3},${j*7+4},${j*7+5},${j*7+6},${j*7+7})"
                              for j in range(len(chunk))) + " ON CONFLICT (user_id) DO NOTHING",
                     *[x for row in chunk for x in row])
    return len(rows)


def fit_calibration(pairs):
    """QUANTILE map: our raw distribution -> Sorsa's score distribution, percentile for
    percentile. Rank-preserving and shape-exact — the i-th percentile of our raw gets the
    i-th percentile Sorsa score, so the top lands where Sorsa's top lands (~5k) and the
    mid-tier isn't inflated. The previous binned-median curve fit the middle well but
    over/under-shot both extremes (elon 4662 vs 5162, blest 820 vs 541)."""
    raws = sorted(p[0] for p in pairs if p[0] > 0 and p[1] > 0)
    sors = sorted(p[1] for p in pairs if p[0] > 0 and p[1] > 0)
    if not raws:
        return [(0.0, FLOOR), (1.0, CAP)]
    knots, nb = [], 200                      # fine-grained percentiles
    for i in range(nb + 1):
        ri = raws[i * (len(raws) - 1) // nb]
        si = sors[i * (len(sors) - 1) // nb]
        knots.append((math.log(ri), si))
    out, best = [], -1e9                     # enforce monotone
    for lr, sc in knots:
        best = max(best, sc)
        out.append((lr, best))
    # collapse duplicate x (equal raws) so the interpolator never divides by zero
    dedup = []
    for lr, sc in out:
        if dedup and abs(lr - dedup[-1][0]) < 1e-12:
            dedup[-1] = (lr, sc)
        else:
            dedup.append((lr, sc))
    return dedup if len(dedup) > 1 else [(dedup[0][0], dedup[0][1]), (dedup[0][0] + 1, CAP)]


def make_scorer(knots):
    def score(raw):
        if raw is None or raw <= 0:
            return FLOOR
        x = math.log(raw)
        if x <= knots[0][0]:
            (x0, y0), (x1, y1) = knots[0], knots[1]
            v = y0 + (y1 - y0) / (x1 - x0) * (x - x0)
        elif x >= knots[-1][0]:
            (x0, y0), (x1, y1) = knots[-2], knots[-1]
            v = y1 + (y1 - y0) / (x1 - x0) * (x - x1)
        else:
            v = knots[-1][1]
            for i in range(len(knots) - 1):
                x0, y0 = knots[i]; x1, y1 = knots[i + 1]
                if x0 <= x <= x1:
                    v = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                    break
        return max(FLOOR, min(CAP, v))
    return score


async def main() -> None:
    write = os.environ.get("WRITE") == "1"
    h = {"c": await connect()}
    print(f"connected to prod | mode = {'WRITE' if write else 'DRY-RUN'}")

    mx = await _retry(h, "fetchval", "SELECT MAX(score) FROM smart_set")
    print(f"smart_set max score = {mx} (expect 1000.0)")
    await _retry(h, "execute", "DROP TABLE IF EXISTS voter_outdeg, kept_voters, ra_scored, targets, ranked_named, prof_import")

    # 1) out-degree per voter (one seq-scan) -> keep only voters with out-degree <= CUT
    await _fire_and_wait(h,
        "CREATE TABLE voter_outdeg AS SELECT follower_id AS uid, COUNT(*) AS od FROM edges GROUP BY follower_id",
        "voter_outdeg")
    await _retry(h, "execute", "CREATE INDEX ON voter_outdeg(uid)")
    total_v = await _retry(h, "fetchval", "SELECT COUNT(*) FROM smart_set")
    kept_v = await _retry(h, "fetchval",
        "SELECT COUNT(*) FROM smart_set s JOIN voter_outdeg v ON v.uid=s.user_id "
        "WHERE v.od <= $1 AND s.pr_rank <= $2", OUTDEG_CUT, TOP_N)
    print(f"voters: {total_v:,} total -> {kept_v:,} kept "
          f"(top-{TOP_N:,} by pr_rank AND out-degree <= {OUTDEG_CUT:,}); "
          f"cut {total_v - kept_v:,} (spray-followers + tail)")

    n = await _push_profiles(h)
    print(f"prof_import: {n:,} names")
    await _retry(h, "execute", "DROP TABLE IF EXISTS targets")
    await _retry(h, "execute",
        "CREATE TABLE targets AS SELECT user_id AS uid FROM smart_set UNION SELECT user_id FROM prof_import")
    await _retry(h, "execute", "CREATE INDEX ON targets(uid)")

    # 2) pruned raw: SUM(voter PageRank) over KEPT voters only
    await _fire_and_wait(h, f"""
        CREATE TABLE ra_scored AS
        SELECT e.followee_id AS uid, SUM(s.score) AS raw, COUNT(*) AS elite
        FROM edges e
        JOIN targets t       ON t.uid = e.followee_id
        JOIN voter_outdeg vo ON vo.uid = e.follower_id AND vo.od <= {OUTDEG_CUT}
        JOIN smart_set s     ON s.user_id = e.follower_id AND s.pr_rank <= {TOP_N}
        GROUP BY e.followee_id""", "ra_scored")
    n_scored = await _retry(h, "fetchval", "SELECT COUNT(*) FROM ra_scored")
    print(f"ra_scored: {n_scored:,} accounts with >=1 kept voter")

    await create_and_wait(h, "ranked_named", """
        CREATE TABLE ranked_named AS
        SELECT s.uid, s.raw, s.elite,
               COALESCE(ss.username, pi.username)         AS uname,
               COALESCE(ss.display_name, pi.name)         AS name,
               COALESCE(ss.followers_count, pi.followers) AS followers,
               COALESCE(ss.following_count, pi.following) AS following,
               pi.bio AS bio, COALESCE(pi.verified,false) AS verified, ts.categories AS categories
        FROM ra_scored s
        LEFT JOIN smart_set ss ON ss.user_id = s.uid
        LEFT JOIN prof_import pi ON pi.user_id = s.uid
        LEFT JOIN twitterscore_accounts ts ON ts.user_id = s.uid
        WHERE COALESCE(ss.username, pi.username) IS NOT NULL""", poll_timeout=1800)

    rows, last = [], ""
    while True:
        batch = await _retry(h, "fetch",
            "SELECT uid, raw, elite, uname, name, followers, following, bio, verified, categories "
            "FROM ranked_named WHERE uid > $1 ORDER BY uid LIMIT 5000", last)
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1]["uid"]
    print(f"pulled {len(rows):,} named+scored accounts")

    raw_by_uname = {(r["uname"] or "").lower(): float(r["raw"] or 0) for r in rows}

    # 3) fit calibration to real Sorsa scores on the NEW (pruned) raw
    con = sqlite3.connect("data/harvest.db")
    sref = {u.lower(): s for u, s in con.execute(
        "select username, sorsa_score from harvested_scores where username is not null and sorsa_score>0")}
    con.close()
    strat = Path("C:/Users/mamoo/AppData/Local/Temp/claude/"
                 "c--Users-mamoo-projects-loudrr-analytics-service/"
                 "60942ef3-0311-4d3f-bca6-c62f45662b9d/scratchpad/sorsa_stratified.csv")
    if strat.exists():
        for r in csv.DictReader(open(strat, encoding="utf-8")):
            sref[r["username"].lower()] = float(r["sorsa"])  # unbiased live overrides
    pairs = [(raw_by_uname[u], s) for u, s in sref.items() if u in raw_by_uname and raw_by_uname[u] > 0]
    knots = fit_calibration(pairs)
    score_fn = make_scorer(knots)
    print(f"calibration fit on {len(pairs):,} (raw_new, sorsa) pairs")

    # 4) before/after report
    async def old_score(u):
        return await _retry(h, "fetchval",
            "SELECT score FROM ranked_accounts WHERE lower(username)=$1", u)

    print("\n=== FLAGGED ACCOUNTS: sorsa | old | NEW ===")
    truth = {"zaynavax": 198, "0xunclebeanz": 312, "cypherweb31": 459, "0xblest_": 541, "saifmr20": 566}
    for u, sor in truth.items():
        raw = raw_by_uname.get(u)
        old = await old_score(u)
        new = score_fn(raw) if raw else None
        print(f"  @{u:<14} sorsa={sor:<5} old={str(old):<6} NEW={new:.0f}" if new else f"  @{u:<14} (dropped)")

    print("\n=== TOP ACCOUNTS: sorsa | old | NEW ===")
    for u, sor in (("elonmusk", 5162), ("vitalikbuterin", 5092), ("cz_binance", 4179),
                   ("garyvee", 4292), ("zachxbt", 4104), ("johncena", 2550)):
        raw = raw_by_uname.get(u); old = await old_score(u)
        print(f"  @{u:<16} sorsa={sor:<5} old={str(old):<6} NEW={score_fn(raw):.0f}" if raw else f"  @{u}: n/a")

    allnew = [score_fn(float(r["raw"] or 0)) for r in rows]
    floorpin = sum(1 for v in allnew if v <= FLOOR + 1)
    print(f"\n=== SPREAD: floor-pinned accounts old=86.8%  NEW={floorpin/len(allnew)*100:.1f}%")
    # accuracy on the unbiased sample
    if pairs:
        rel = [abs(score_fn(r) - s) / s for r, s in pairs]
        print(f"    median rel err vs Sorsa (fit sample): {st.median(rel)*100:.1f}%")

    if not write:
        print("\nDRY-RUN complete. Re-run with WRITE=1 to commit ranked_accounts + calibration.")
        await _retry(h, "execute", "DROP TABLE IF EXISTS voter_outdeg, ra_scored, targets, ranked_named, prof_import")
        await h["c"].close()
        return

    # 5) rank + rewrite ranked_accounts
    scored = sorted(((r, float(r["raw"] or 0), int(round(score_fn(float(r["raw"] or 0))))) for r in rows),
                    key=lambda x: (-x[2], -(x[0]["elite"] or 0)))
    await _retry(h, "execute", "TRUNCATE ranked_accounts")
    cols = ("user_id", "rank", "username", "display_username", "name", "bio", "followers",
            "following", "verified", "elite_followers", "raw_score", "score", "categories")
    payload = [(str(r["uid"]), i, (r["uname"] or "").lower(), r["uname"], r["name"], (r["bio"] or None),
                r["followers"], r["following"], bool(r["verified"]), int(r["elite"] or 0), raw, score,
                r["categories"]) for i, (r, raw, score) in enumerate(scored, start=1)]
    for k in range(0, len(payload), INS_BATCH):
        chunk = payload[k:k + INS_BATCH]
        vals = ",".join("(" + ",".join(f"${c*13+j+1}" for j in range(13)) + ")" for c in range(len(chunk)))
        await _retry(h, "execute",
            f"INSERT INTO ranked_accounts ({','.join(cols)}) VALUES {vals} ON CONFLICT (user_id) DO NOTHING",
            *[x for row in chunk for x in row])
    print(f"\nDONE — ranked_accounts rebuilt: {len(payload):,} accounts")
    print("top 8:", ", ".join(f"{r['uname']}({sc})" for r, _, sc in scored[:8]))
    # persist calibration knots so the live API scores UNRANKED accounts on the same curve
    Path("data/loudrr_calibration_knots.json").write_text(
        __import__("json").dumps({"knots": [[round(a, 6), round(b, 2)] for a, b in knots],
                                  "floor": FLOOR, "cap": CAP}))
    print("wrote data/loudrr_calibration_knots.json (wire into loudrr_score + redeploy API)")
    await _retry(h, "execute", "DROP TABLE IF EXISTS voter_outdeg, ra_scored, targets, ranked_named, prof_import")
    await h["c"].close()


if __name__ == "__main__":
    asyncio.run(main())

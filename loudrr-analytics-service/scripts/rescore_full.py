"""Proxy-safe full-graph PageRank re-score over the FULL ~98k-voter member->member graph.

The single server-side cursor (rescore.py) dies on the public-port proxy at this edge volume
(~282M edges) — the proxy resets large transfers. This version streams edges in small
member-batches with auto-reconnect+retry (the backup_db.py pattern): every edge has a member
follower, so iterating members in batches of BATCH covers all edges with bounded transfers.

PageRank math is IDENTICAL to rescore.py (alpha=0.85, dangling handling, 50 iters, 1e-7 tol,
1000*pr/max normalization) so the new scores stay directly comparable to the prior calibration.
Writes voting-power -> smart_set.score and prints the Obama/greg/waleswoosh unified-score audit.
"""
import asyncio, asyncpg, os, csv, time
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]
BATCH = 30  # members per edges query. Combined with the server-side member->member filter
# (JOIN to smart_set on followee), each batch transfers only the m2m subset — small enough to
# clear the public-port proxy (backup_db.py proved ~20-member raw batches survive).
DROP_ERRORS = (asyncpg.exceptions.ConnectionDoesNotExistError,
               asyncpg.exceptions.InterfaceError, ConnectionResetError, OSError, asyncio.TimeoutError)


async def connect(tries=20):
    for _ in range(tries):
        try:
            return await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres",
                                         password=PW, database="loudrr_analytics", timeout=30, command_timeout=180)
        except Exception:
            await asyncio.sleep(6)
    raise RuntimeError("no DB connection")


async def fetch_retry(h, query, *args, tries=10):
    for a in range(tries):
        try:
            return await h["c"].fetch(query, *args)
        except DROP_ERRORS:
            print(f"   conn dropped, reconnecting ({a+1}/{tries})...", flush=True)
            try:
                await h["c"].close()
            except Exception:
                pass
            await asyncio.sleep(2 + a)
            h["c"] = await connect()
    raise RuntimeError("batch failed after retries")


def test_ids():
    m = {}
    try:
        for r in csv.DictReader(open("data/exports/discovered_accounts.csv", encoding="utf-8")):
            m[(r["username"] or "").lower()] = r["user_id"]
    except FileNotFoundError:
        pass
    want = ["barackobama", "greg16676935420", "waleswoosh", "0xblest_", "karpathy", "openai", "vitalikbuterin", "elonmusk"]
    return [(w, m[w]) for w in want if w in m]


async def main():
    t0 = time.time()
    h = {"c": await connect()}
    print("connected.", flush=True)

    nodes = [r["user_id"] for r in await fetch_retry(h, "SELECT user_id FROM smart_set")]
    idx = {int(u): i for i, u in enumerate(nodes)}
    N = len(nodes)
    adj = [[] for _ in range(N)]
    print(f"nodes (voters): {N:,}", flush=True)

    # stream edges in member-batches (proxy-safe), keep only member->member
    print("streaming edges in member-batches (proxy-safe)...", flush=True)
    m2m = 0
    scanned = 0
    nb = (N + BATCH - 1) // BATCH
    for bi, i in enumerate(range(0, N, BATCH)):
        batch = nodes[i:i + BATCH]
        # server-side member->member filter: JOIN followee to smart_set PK so only m2m edges
        # cross the proxy (follower is always a member). ~halves transfer + keeps batches small.
        rows = await fetch_retry(
            h, """SELECT e.follower_id, e.followee_id FROM edges e
                  JOIN smart_set s ON s.user_id = e.followee_id
                  WHERE e.follower_id = ANY($1::text[])""", batch)
        for r in rows:
            fi = idx.get(int(r[0]))
            ti = idx.get(int(r[1]))
            if fi is not None and ti is not None:
                adj[fi].append(ti)
                m2m += 1
        scanned += len(rows)
        if bi % 100 == 0 or i + BATCH >= N:
            print(f"  batch {bi+1}/{nb}  members {min(i+BATCH,N):,}  scanned {scanned:,}  m2m {m2m:,}  ({time.time()-t0:.0f}s)", flush=True)
    print(f"member->member edges: {m2m:,}  (scanned {scanned:,} total edges) ({time.time()-t0:.0f}s)", flush=True)

    # PageRank (identical to rescore.py)
    print("running PageRank...", flush=True)
    alpha = 0.85
    pr = [1.0 / N] * N
    outdeg = [len(a) for a in adj]
    dangling = [i for i in range(N) if outdeg[i] == 0]
    for it in range(1, 51):
        dsum = alpha * sum(pr[d] for d in dangling) / N
        base = (1 - alpha) / N + dsum
        new = [base] * N
        for i in range(N):
            a = adj[i]
            if a:
                share = alpha * pr[i] / len(a)
                for t in a:
                    new[t] += share
        diff = sum(abs(new[i] - pr[i]) for i in range(N))
        pr = new
        if it % 5 == 0 or diff < 1e-7:
            print(f"  iter {it}: L1 {diff:.2e} ({time.time()-t0:.0f}s)", flush=True)
        if diff < 1e-7:
            break

    mx = max(pr)
    scores = [round(1000.0 * pr[i] / mx, 3) for i in range(N)]

    # write voting-power -> smart_set.score (retry the small write block on drop)
    print("writing scores -> smart_set.score ...", flush=True)
    for attempt in range(8):
        try:
            c = h["c"]
            await c.execute("DROP TABLE IF EXISTS _sc")
            await c.execute("CREATE TEMP TABLE _sc(uid varchar, sc double precision)")
            await c.copy_records_to_table("_sc", records=[(nodes[i], scores[i]) for i in range(N)])
            await c.execute("UPDATE smart_set s SET score=_sc.sc FROM _sc WHERE s.user_id=_sc.uid")
            break
        except DROP_ERRORS:
            print(f"   write conn dropped, reconnecting ({attempt+1}/8)...", flush=True)
            try:
                await h["c"].close()
            except Exception:
                pass
            await asyncio.sleep(2 + attempt)
            h["c"] = await connect()
    else:
        raise RuntimeError("score write failed after retries")

    hmap = {r["user_id"]: r["username"] for r in await fetch_retry(h, "SELECT user_id, username FROM smart_set")}
    order = sorted(range(N), key=lambda i: -scores[i])[:25]
    print(f"\nTOP 25 voters by NEW PageRank ({N:,}-voter graph):", flush=True)
    for rank, i in enumerate(order, 1):
        print(f"  {rank:>2} @{(hmap.get(nodes[i]) or nodes[i]):<20} {scores[i]:.0f}", flush=True)

    print("\nUNIFIED score (weighted in-degree, NEW graph) — Obama vs greg vs waleswoosh:", flush=True)
    for name, uid in test_ids():
        u = await fetch_retry(h, '''SELECT coalesce(sum(s.score),0) sc, count(*) c
            FROM edges e JOIN smart_set s ON e.follower_id=s.user_id WHERE e.followee_id=$1''', uid)
        row = u[0]
        print(f"  @{name:<18} unified={row['sc']:>13.0f}  from {row['c']:,} elite followers", flush=True)
    await h["c"].close()
    print(f"\nDONE in {time.time()-t0:.0f}s.", flush=True)


asyncio.run(main())

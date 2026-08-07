"""Phase 4 re-score (robust): PageRank over the full 33,822-voter member->member graph.
Streams edges via a server-side cursor (no giant fetch), integer node keys (low memory),
creates the reverse-index index the API needs, writes voting-power, tests Obama-vs-greg."""
import asyncio, asyncpg, os, csv, time
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]


async def conn(tries=20):
    for _ in range(tries):
        try:
            return await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres",
                                         password=PW, database="loudrr_analytics", timeout=20, command_timeout=900)
        except Exception:
            await asyncio.sleep(6)
    raise RuntimeError("no DB connection")


def test_ids():
    m = {}
    try:
        for r in csv.DictReader(open("data/exports/discovered_accounts.csv", encoding="utf-8")):
            m[(r["username"] or "").lower()] = r["user_id"]
    except FileNotFoundError:
        pass
    want = ["barackobama", "greg16676935420", "waleswoosh", "0xblest_", "karpathy", "openai"]
    return [(w, m[w]) for w in want if w in m]


async def main():
    t0 = time.time()
    pg = await conn()
    print("connected.", flush=True)

    # NOTE: do NOT create idx_edges_follower / idx_edges_followee here. Both are redundant and
    # together wasted 6.2GB on a 96GB disk (they filled it and took prod down, 2026-07-08):
    #   * edges_pkey is btree(follower_id, followee_id) -> already serves follower_id lookups
    #     via its leading column, so a separate follower_id index buys nothing.
    #   * the Edge model already declares ix_edges_followee on (followee_id).
    # The access paths this script needs are covered by those two. Keep it that way.

    # node index map
    nodes = [r["user_id"] for r in await pg.fetch("SELECT user_id FROM smart_set")]
    idx = {int(u): i for i, u in enumerate(nodes)}
    N = len(nodes)
    adj = [[] for _ in range(N)]

    # stream member->member edges (cursor; no giant fetch)
    print("streaming member->member edges (cursor)...", flush=True)
    m2m = 0
    async with pg.transaction():
        cur = await pg.cursor('''SELECT e.follower_id, e.followee_id FROM edges e
            JOIN smart_set a ON a.user_id=e.follower_id JOIN smart_set b ON b.user_id=e.followee_id''')
        while True:
            batch = await cur.fetch(100000)
            if not batch:
                break
            for r in batch:
                fi = idx.get(int(r[0])); ti = idx.get(int(r[1]))
                if fi is not None and ti is not None:
                    adj[fi].append(ti)
            m2m += len(batch)
            if m2m % 1000000 == 0:
                print(f"  ...{m2m:,} edges ({time.time()-t0:.0f}s)", flush=True)
    print(f"member->member edges: {m2m:,} ({time.time()-t0:.0f}s)", flush=True)

    # PageRank (index arrays, power iteration)
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

    print("writing scores -> smart_set.score ...", flush=True)
    await pg.execute("DROP TABLE IF EXISTS _sc")
    await pg.execute("CREATE TEMP TABLE _sc(uid varchar, sc double precision)")
    await pg.copy_records_to_table("_sc", records=[(nodes[i], scores[i]) for i in range(N)])
    await pg.execute("UPDATE smart_set s SET score=_sc.sc FROM _sc WHERE s.user_id=_sc.uid")

    hmap = {r["user_id"]: r["username"] for r in await pg.fetch("SELECT user_id, username FROM smart_set")}
    order = sorted(range(N), key=lambda i: -scores[i])[:25]
    print(f"\nTOP 25 voters by NEW PageRank (33,822-voter graph):", flush=True)
    for rank, i in enumerate(order, 1):
        print(f"  {rank:>2} @{(hmap.get(nodes[i]) or nodes[i]):<18} {scores[i]:.0f}")

    print("\nUNIFIED score (weighted in-degree, NEW graph) — did Obama climb?", flush=True)
    for name, uid in test_ids():
        u = await pg.fetchrow('''SELECT coalesce(sum(s.score),0) sc, count(*) c
            FROM edges e JOIN smart_set s ON e.follower_id=s.user_id WHERE e.followee_id=$1''', uid)
        print(f"  @{name:<18} unified={u['sc']:>11.0f}  from {u['c']:,} elite followers")
    await pg.close()
    print(f"\nDONE in {time.time()-t0:.0f}s.", flush=True)


asyncio.run(main())

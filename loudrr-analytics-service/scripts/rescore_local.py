"""PageRank re-score that finally fits the constraints: the slow prod Postgres does ONE pass
(emit a compact integer adjacency — one int[] of followees per node, ~98k rows instead of 114M),
we transfer that once over the proxy in keyset chunks, then run ALL PageRank iterations LOCALLY
with scipy.sparse (milliseconds/iter). Math identical to rescore.py (alpha=0.85, dangling
redistribution, 1000*pr/max). Resumable: nodemap/adj_t are reused if present.
"""
import asyncio, asyncpg, os, time
import numpy as np
import scipy.sparse as sp
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]
ALPHA = 0.85
DROP_ERRORS = (asyncpg.exceptions.ConnectionDoesNotExistError, asyncpg.exceptions.InterfaceError,
               asyncpg.exceptions.QueryCanceledError, ConnectionResetError, OSError, asyncio.TimeoutError)


async def connect(tries=80):
    for i in range(tries):
        try:
            c = await asyncio.wait_for(
                asyncpg.connect(host="213.199.54.248", port=5433, user="postgres", password=PW,
                                database="loudrr_analytics", timeout=10, command_timeout=900), timeout=15)
            try:
                await c.execute("SET work_mem='384MB'")
                await c.execute("SET max_parallel_workers_per_gather=0")
            except Exception:
                pass
            return c
        except Exception:
            await asyncio.sleep(3)
    raise RuntimeError("no DB connection")


async def reconnect(h):
    try:
        await asyncio.wait_for(h["c"].close(), timeout=5)
    except Exception:
        pass
    h["c"] = await connect()


async def R(h, kind, sql, *args, tries=25):
    for a in range(tries):
        try:
            c = h["c"]
            if kind == "execute":
                return await c.execute(sql, *args)
            if kind == "fetchval":
                return await c.fetchval(sql, *args)
            return await c.fetch(sql, *args)
        except DROP_ERRORS:
            await asyncio.sleep(1)
            await reconnect(h)
    raise RuntimeError("stmt failed after retries")


async def exists(h, t):
    return (await R(h, "fetchval", f"SELECT to_regclass('public.{t}')")) is not None


async def create_and_wait(h, table, sql, poll=3600):
    if await exists(h, table):
        return
    await R(h, "execute", f"DROP TABLE IF EXISTS {table}")
    t0 = time.time()
    try:
        await h["c"].execute(sql)
        return
    except DROP_ERRORS:
        print(f"   [{table}] client dropped; polling server-side completion...", flush=True)
    while time.time() - t0 < poll:
        await asyncio.sleep(10)
        try:
            if await exists(h, table):
                print(f"   [{table}] committed after {time.time()-t0:.0f}s", flush=True)
                return
        except DROP_ERRORS:
            await reconnect(h)
    raise RuntimeError(f"{table} did not complete in {poll}s")


async def main():
    t0 = time.time()
    h = {"c": await connect()}
    print("connected.", flush=True)

    # free disk: m2m_raw is redundant (m2m is the deduped version)
    await R(h, "execute", "DROP TABLE IF EXISTS m2m_raw, od_t, pr_t, pr_new")

    # integer node map (0..N-1), ordered by user_id for stable idx
    await create_and_wait(h, "nodemap",
        "CREATE UNLOGGED TABLE nodemap AS SELECT user_id, (row_number() OVER (ORDER BY user_id))::int - 1 AS idx FROM smart_set")
    await R(h, "execute", "CREATE INDEX IF NOT EXISTS nodemap_uid ON nodemap(user_id)")
    await R(h, "execute", "ANALYZE nodemap")
    await R(h, "execute", "ANALYZE m2m")
    N = await R(h, "fetchval", "SELECT count(*) FROM nodemap")
    print(f"nodes: {N:,}", flush=True)

    # ONE server-side pass: compact integer adjacency (int[] of followee-idx per source-idx)
    print("building adj_t (one pass over m2m, server-side)...", flush=True)
    await create_and_wait(h, "adj_t",
        """CREATE UNLOGGED TABLE adj_t AS
           SELECT a.idx AS fi, array_agg(b.idx) AS ts
           FROM m2m m JOIN nodemap a ON a.user_id=m.f JOIN nodemap b ON b.user_id=m.t
           GROUP BY a.idx""")
    nrows = await R(h, "fetchval", "SELECT count(*) FROM adj_t")
    print(f"adj_t rows (non-dangling nodes): {nrows:,} ({time.time()-t0:.0f}s)", flush=True)

    # transfer adj_t in keyset chunks, accumulate edges into numpy arrays
    print("transferring compact adjacency...", flush=True)
    src_parts, dst_parts = [], []
    last = -1
    got = 0
    edges = 0
    while True:
        rows = await R(h, "fetch",
                       "SELECT fi, ts FROM adj_t WHERE fi > $1 ORDER BY fi LIMIT 3000", last)
        if not rows:
            break
        for r in rows:
            fi = r["fi"]; ts = r["ts"]
            if ts:
                src_parts.append(np.full(len(ts), fi, dtype=np.int32))
                dst_parts.append(np.asarray(ts, dtype=np.int32))
                edges += len(ts)
        last = rows[-1]["fi"]
        got += len(rows)
        if got % 30000 == 0:
            print(f"  transferred {got:,}/{nrows:,} nodes, {edges:,} edges ({time.time()-t0:.0f}s)", flush=True)
    print(f"transferred all: {got:,} nodes, {edges:,} edges ({time.time()-t0:.0f}s)", flush=True)

    src = np.concatenate(src_parts) if src_parts else np.array([], dtype=np.int32)
    dst = np.concatenate(dst_parts) if dst_parts else np.array([], dtype=np.int32)
    del src_parts, dst_parts

    # ── local PageRank (scipy sparse) ──
    print("local PageRank...", flush=True)
    outdeg = np.bincount(src, minlength=N).astype(np.float64)
    w = 1.0 / outdeg[src]
    M = sp.csr_matrix((w, (dst, src)), shape=(N, N))  # M[t,f] = 1/outdeg[f]
    dangling = outdeg == 0
    pr = np.full(N, 1.0 / N)
    for it in range(1, 201):
        dsum = pr[dangling].sum()
        new = (1 - ALPHA) / N + ALPHA * dsum / N + ALPHA * (M @ pr)
        diff = np.abs(new - pr).sum()
        pr = new
        if it % 10 == 0 or diff < 1e-10:
            print(f"  iter {it}: L1 {diff:.2e} ({time.time()-t0:.0f}s)", flush=True)
        if diff < 1e-10:
            break
    scores = np.round(1000.0 * pr / pr.max(), 3)
    print(f"PageRank done, converged ({time.time()-t0:.0f}s)", flush=True)

    # map idx -> user_id and write scores back
    nm = await R(h, "fetch", "SELECT user_id, idx FROM nodemap")
    uid_by_idx = [None] * N
    for r in nm:
        uid_by_idx[r["idx"]] = r["user_id"]
    print("writing scores -> smart_set.score ...", flush=True)
    for attempt in range(8):
        try:
            c = h["c"]
            await c.execute("DROP TABLE IF EXISTS _sc")
            await c.execute("CREATE TEMP TABLE _sc(uid varchar, sc double precision)")
            await c.copy_records_to_table("_sc", records=[(uid_by_idx[i], float(scores[i])) for i in range(N)])
            await c.execute("UPDATE smart_set s SET score=_sc.sc FROM _sc WHERE s.user_id=_sc.uid")
            break
        except DROP_ERRORS:
            await reconnect(h)
    else:
        raise RuntimeError("score write failed")

    top = await R(h, "fetch", "SELECT username,user_id,score FROM smart_set ORDER BY score DESC NULLS LAST LIMIT 25")
    print(f"\nTOP 25 voters by NEW PageRank ({N:,}-voter graph):", flush=True)
    for rank, r in enumerate(top, 1):
        print(f"  {rank:>2} @{(r['username'] or r['user_id']):<20} {r['score']:.0f}", flush=True)

    print("\nUNIFIED score (weighted in-degree) — Obama vs greg vs waleswoosh:", flush=True)
    for name in ["barackobama", "greg16676935420", "waleswoosh", "0xblest_", "karpathy", "openai", "VitalikButerin", "elonmusk"]:
        r = (await R(h, "fetch",
             """SELECT coalesce(sum(s.score),0) sc, count(*) c FROM edges e JOIN smart_set s ON e.follower_id=s.user_id
                WHERE e.followee_id=(SELECT user_id FROM smart_set WHERE lower(username)=lower($1) LIMIT 1)""", name))[0]
        print(f"  @{name:<18} unified={r['sc']:>13.0f}  from {r['c']:,} elite followers", flush=True)

    await R(h, "execute", "DROP TABLE IF EXISTS adj_t, nodemap, m2m")
    await h["c"].close()
    print(f"\nDONE in {time.time()-t0:.0f}s.", flush=True)


asyncio.run(main())

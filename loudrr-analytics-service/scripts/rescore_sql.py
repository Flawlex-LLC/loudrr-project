"""Server-side PageRank re-score — proxy-proof + resumable.

Two hard facts about the public-port proxy:
  1. It throttles large transfers, so the graph can't be streamed to the client.
  2. Its idle timeout is SHORTER than our big server-side statements (dedup, each PageRank
     iteration), so a long CREATE TABLE AS drops the *client* mid-statement even though the
     *server* finishes it.

So: build everything server-side, and for every long statement use FIRE-AND-POLL — issue the
CREATE, and if the client drops, reconnect and poll until the result table commits (the server
ran it to completion). Every step is idempotent + resumable: m2m_raw / m2m / od_t / pr_t are
reused if present, and PageRank continues from whatever pr_t holds (power iteration converges to
the same fixed point from any positive start).

Math identical to rescore.py (alpha=0.85, dangling redistribution, 1000*pr/max)."""
import asyncio, asyncpg, os, time
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]
ALPHA = 0.85
INS_BATCH = 300
CMD_TIMEOUT = 240
DROP_ERRORS = (asyncpg.exceptions.ConnectionDoesNotExistError, asyncpg.exceptions.InterfaceError,
               asyncpg.exceptions.QueryCanceledError, ConnectionResetError, OSError, asyncio.TimeoutError)


async def connect(tries=80):
    for i in range(tries):
        try:
            conn = await asyncio.wait_for(
                asyncpg.connect(host="213.199.54.248", port=5433, user="postgres", password=PW,
                                database="loudrr_analytics", timeout=10, command_timeout=CMD_TIMEOUT), timeout=15)
            # keep the 114M-edge hash-aggregate in RAM (default work_mem disk-spills => minutes/iter);
            # NO parallel workers — they allocate dynamic shared memory in the container's small
            # /dev/shm and blow it out (DiskFullError). work_mem uses regular backend memory.
            try:
                await conn.execute("SET work_mem='384MB'")
                await conn.execute("SET max_parallel_workers_per_gather=0")
            except Exception:
                pass
            return conn
        except Exception:
            await asyncio.sleep(3)
    raise RuntimeError("no DB connection")


async def reconnect(h):
    try:
        await asyncio.wait_for(h["c"].close(), timeout=5)
    except Exception:
        pass
    h["c"] = await connect()


async def _retry(h, kind, sql, *args, tries=25):
    """Short statements: simple reconnect-retry."""
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
    raise RuntimeError("statement failed after retries")


async def exists(h, table):
    return (await _retry(h, "fetchval", f"SELECT to_regclass('public.{table}')")) is not None


async def create_and_wait(h, table, create_sql, poll_timeout=2400):
    """Long CREATE TABLE AS that survives a proxy drop: issue it; if the client connection dies
    mid-statement, the server keeps running it, so reconnect and poll until <table> commits."""
    if await exists(h, table):
        return
    await _retry(h, "execute", f"DROP TABLE IF EXISTS {table}")
    t0 = time.time()
    try:
        await h["c"].execute(create_sql)
        return
    except DROP_ERRORS:
        print(f"   [{table}] client dropped during CREATE; polling server-side completion...", flush=True)
    start = time.time()
    while time.time() - start < poll_timeout:
        await asyncio.sleep(8)
        try:
            if await exists(h, table):
                print(f"   [{table}] committed server-side after {time.time()-t0:.0f}s", flush=True)
                return
        except DROP_ERRORS:
            await reconnect(h)
    print(f"   [{table}] not seen in {poll_timeout}s — re-issuing", flush=True)
    await _retry(h, "execute", f"DROP TABLE IF EXISTS {table}")
    await create_and_wait(h, table, create_sql, poll_timeout)


async def main():
    t0 = time.time()
    h = {"c": await connect()}
    print("connected.", flush=True)
    nodes = [r["user_id"] for r in await _retry(h, "fetch", "SELECT user_id FROM smart_set")]
    N = len(nodes)
    print(f"nodes (voters): {N:,}", flush=True)

    # ── PHASE 1: member->member edges, server-side + resumable ──
    await _retry(h, "execute", "CREATE UNLOGGED TABLE IF NOT EXISTS m2m_raw (f varchar, t varchar)")
    done = set(r["f"] for r in await _retry(h, "fetch", "SELECT DISTINCT f FROM m2m_raw"))
    todo = [u for u in nodes if u not in done]
    print(f"phase1: {len(done):,} built, {len(todo):,} to go", flush=True)
    nb = (len(todo) + INS_BATCH - 1) // INS_BATCH
    for bi, i in enumerate(range(0, len(todo), INS_BATCH)):
        await _retry(h, "execute",
                     """INSERT INTO m2m_raw (f,t) SELECT e.follower_id, e.followee_id FROM edges e
                        JOIN smart_set s ON s.user_id=e.followee_id WHERE e.follower_id = ANY($1::text[])""",
                     todo[i:i + INS_BATCH])
        if bi % 25 == 0 or i + INS_BATCH >= len(todo):
            print(f"  insert {bi+1}/{nb} ({time.time()-t0:.0f}s)", flush=True)

    # dedup (fire-and-poll; skipped if m2m already exists from a prior run)
    print("dedup -> m2m ...", flush=True)
    await create_and_wait(h, "m2m", "CREATE UNLOGGED TABLE m2m AS SELECT DISTINCT f, t FROM m2m_raw")
    m2m_count = await _retry(h, "fetchval", "SELECT count(*) FROM m2m")
    print(f"member->member edges: {m2m_count:,} ({time.time()-t0:.0f}s)", flush=True)

    # outdegree + pr (fire-and-poll for the heavy aggregate)
    await create_and_wait(h, "od_t", "CREATE UNLOGGED TABLE od_t AS SELECT f, count(*)::int c FROM m2m GROUP BY f")
    await _retry(h, "execute", "CREATE INDEX IF NOT EXISTS od_t_f ON od_t(f)")
    if not await exists(h, "pr_t"):
        await _retry(h, "execute", f"CREATE UNLOGGED TABLE pr_t AS SELECT user_id id, (1.0/{N})::float8 p FROM smart_set")
        await _retry(h, "execute", "CREATE INDEX IF NOT EXISTS pr_t_id ON pr_t(id)")
        print("pr_t initialized to 1/N", flush=True)
    else:
        print("pr_t exists — continuing PageRank from current state", flush=True)

    # CRITICAL: CREATE TABLE AS leaves tables with NO stats -> planner picks a nested-loop
    # disaster (8+ min/iter). ANALYZE gives the hash-join plan (~3.5 min/iter on this server).
    print("ANALYZE m2m / od_t / pr_t ...", flush=True)
    for tb in ("m2m", "od_t", "pr_t"):
        await _retry(h, "execute", f"ANALYZE {tb}")

    # ── PHASE 2: power-iteration PageRank (fire-and-poll per iter) ──
    print("running PageRank...", flush=True)
    for it in range(1, 61):
        dsum = await _retry(h, "fetchval",
                            "SELECT coalesce(sum(p.p),0) FROM pr_t p LEFT JOIN od_t o ON o.f=p.id WHERE o.f IS NULL")
        base = (1 - ALPHA) / N + ALPHA * dsum / N
        await create_and_wait(h, "pr_new",
                              f"""CREATE UNLOGGED TABLE pr_new AS
                                  SELECT p.id, ({base})::float8 + ({ALPHA})::float8*coalesce(a.c,0) AS p
                                  FROM pr_t p LEFT JOIN (
                                     SELECT m.t id, sum(pr_t.p/od_t.c) c FROM m2m m
                                     JOIN pr_t ON pr_t.id=m.f JOIN od_t ON od_t.f=m.f GROUP BY m.t
                                  ) a ON a.id=p.id""")
        diff = await _retry(h, "fetchval", "SELECT sum(abs(a.p-b.p)) FROM pr_t a JOIN pr_new b ON a.id=b.id")
        await _retry(h, "execute", "DROP TABLE pr_t")
        await _retry(h, "execute", "ALTER TABLE pr_new RENAME TO pr_t")
        await _retry(h, "execute", "CREATE INDEX IF NOT EXISTS pr_t_id ON pr_t(id)")
        await _retry(h, "execute", "ANALYZE pr_t")  # keep the good plan every iteration (cheap, ~1s)
        print(f"  iter {it}: L1 {diff:.2e} ({time.time()-t0:.0f}s)", flush=True)
        if diff is not None and diff < 1e-4:  # ranking-grade: scores final to <1pt on the 0-6000 scale
            break

    print("writing scores -> smart_set.score ...", flush=True)
    mx = await _retry(h, "fetchval", "SELECT max(p) FROM pr_t")
    await _retry(h, "execute",
                 f"UPDATE smart_set s SET score=round((1000.0*pr_t.p/{mx})::numeric,3) FROM pr_t WHERE s.user_id=pr_t.id")

    top = await _retry(h, "fetch", "SELECT username,user_id,score FROM smart_set ORDER BY score DESC NULLS LAST LIMIT 25")
    print(f"\nTOP 25 voters by NEW PageRank ({N:,}-voter graph):", flush=True)
    for rank, r in enumerate(top, 1):
        print(f"  {rank:>2} @{(r['username'] or r['user_id']):<20} {r['score']:.0f}", flush=True)

    print("\nUNIFIED score (weighted in-degree) — Obama vs greg vs waleswoosh:", flush=True)
    for name in ["barackobama", "greg16676935420", "waleswoosh", "0xblest_", "karpathy", "openai", "VitalikButerin", "elonmusk"]:
        r = (await _retry(h, "fetch",
             """SELECT coalesce(sum(s.score),0) sc, count(*) c FROM edges e JOIN smart_set s ON e.follower_id=s.user_id
                WHERE e.followee_id=(SELECT user_id FROM smart_set WHERE lower(username)=lower($1) LIMIT 1)""", name))[0]
        print(f"  @{name:<18} unified={r['sc']:>13.0f}  from {r['c']:,} elite followers", flush=True)

    await _retry(h, "execute", "DROP TABLE IF EXISTS m2m_raw, m2m, od_t, pr_t, pr_new")
    await h["c"].close()
    print(f"\nDONE in {time.time()-t0:.0f}s.", flush=True)


if __name__ == "__main__":       # guard: importing this module for its helpers (build_ranked_prod
    asyncio.run(main())          # imports connect/create_and_wait/etc) must NOT trigger a full re-score

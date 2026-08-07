"""Data-integrity audit of the crawl: coverage + per-account edge-vs-following cross-check.
Read-only, light (sampled) so it can run alongside the re-score. Answers: did we miss any
accounts, is any data corrupted/truncated, do our edge counts match real following numbers?"""
import asyncio, asyncpg, os, statistics
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]


async def connect(tries=40):
    for i in range(tries):
        try:
            return await asyncio.wait_for(
                asyncpg.connect(host="213.199.54.248", port=5433, user="postgres", password=PW,
                                database="loudrr_analytics", timeout=10, command_timeout=120), timeout=15)
        except Exception:
            await asyncio.sleep(3)
    raise RuntimeError("no DB connection")


async def main():
    c = await connect()
    print("== COVERAGE ==", flush=True)
    r = await c.fetchrow("""SELECT count(*) total,
        count(*) FILTER (WHERE last_crawled_at IS NOT NULL) crawled,
        count(*) FILTER (WHERE following_count IS NOT NULL) has_fc,
        count(*) FILTER (WHERE protected) prot,
        count(*) FILTER (WHERE following_count=0) zero_fc,
        count(*) FILTER (WHERE username IS NULL) no_username
        FROM smart_set""")
    print(f"  total={r['total']:,}  crawled={r['crawled']:,}  ({100*r['crawled']/r['total']:.2f}%)", flush=True)
    print(f"  uncrawled(queue)={r['total']-r['crawled']:,}  protected={r['prot']:,}  following_count populated={r['has_fc']:,}", flush=True)
    print(f"  following_count=0: {r['zero_fc']:,}   username NULL: {r['no_username']:,}", flush=True)

    # sampled cross-check: our edge count vs their real following_count
    print("\n== CROSS-CHECK: our edges vs real following_count (random sample of crawled, non-protected, fc>0) ==", flush=True)
    sample = await c.fetch("""SELECT user_id, username, following_count, protected
        FROM smart_set
        WHERE last_crawled_at IS NOT NULL AND following_count IS NOT NULL AND following_count > 0
        ORDER BY random() LIMIT 250""")
    ratios, empties, low, ok = [], [], 0, 0
    for s in sample:
        ec = await c.fetchval("SELECT count(*) FROM edges WHERE follower_id=$1", s["user_id"])
        fc = s["following_count"]
        ratio = ec / fc if fc else 0
        ratios.append(ratio)
        if ec == 0 and not s["protected"]:
            empties.append((s["username"], fc))
        if ratio < 0.5:
            low += 1
        elif ratio >= 0.8:
            ok += 1
    ratios.sort()
    print(f"  sample n={len(ratios)}", flush=True)
    print(f"  edge/following ratio: median={statistics.median(ratios):.2f}  mean={statistics.mean(ratios):.2f}  p10={ratios[len(ratios)//10]:.2f}  p90={ratios[9*len(ratios)//10]:.2f}", flush=True)
    print(f"  healthy (ratio>=0.8): {ok}/{len(ratios)} ({100*ok/len(ratios):.0f}%)   suspicious (ratio<0.5): {low}", flush=True)
    print(f"  EMPTY despite following>0 & not protected: {len(empties)}", flush=True)
    for u, fc in empties[:10]:
        print(f"     @{u}  following={fc:,}  but 0 edges", flush=True)

    # protected accounts should be ~empty (sanity)
    print("\n== PROTECTED sanity (should have ~0 edges) ==", flush=True)
    psample = await c.fetch("SELECT user_id, username FROM smart_set WHERE protected AND last_crawled_at IS NOT NULL ORDER BY random() LIMIT 40")
    pnonzero = 0
    for s in psample:
        ec = await c.fetchval("SELECT count(*) FROM edges WHERE follower_id=$1", s["user_id"])
        if ec > 0:
            pnonzero += 1
    print(f"  protected sample n={len(psample)}  with >0 edges: {pnonzero}", flush=True)

    await c.close()


asyncio.run(main())

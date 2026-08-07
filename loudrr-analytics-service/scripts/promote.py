"""Promote the gated discovered accounts into smart_set as new voters (is_seed=False,
last_crawled_at=NULL so Phase-3 picks them up). Non-destructive: INSERT ON CONFLICT DO NOTHING."""
import asyncio, asyncpg, csv, os
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]
DROP = (asyncpg.exceptions.ConnectionDoesNotExistError, asyncpg.exceptions.InterfaceError,
        ConnectionResetError, OSError, asyncio.TimeoutError)
SRC = "promotion_2026_06_22"


async def connect():
    return await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres",
                                 password=PW, database="loudrr_analytics", timeout=120, command_timeout=120)


async def run(h, fn):
    for a in range(8):
        try:
            return await fn(h["c"])
        except DROP:
            print(f"   conn dropped, reconnect ({a+1})...", flush=True)
            try: await h["c"].close()
            except Exception: pass
            await asyncio.sleep(2 + a)
            h["c"] = await connect()
    raise RuntimeError("failed after retries")


async def main():
    prof = {r["user_id"]: r for r in csv.DictReader(open("data/exports/profiles_enriched.csv", encoding="utf-8"))}
    gated = list(csv.DictReader(open("data/exports/gated_promotion.csv", encoding="utf-8")))
    h = {"c": await connect()}
    cols = {r["column_name"]: r["data_type"] for r in await run(h, lambda c: c.fetch(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='smart_set'"))}
    seed_bool = cols.get("is_seed", "").startswith("bool")
    before = await run(h, lambda c: c.fetchval("SELECT count(*) FROM smart_set"))
    print(f"smart_set before: {before:,} | is_seed type: {cols.get('is_seed')}", flush=True)

    rows = []
    for g in gated:
        p = prof.get(g["user_id"], {})
        fol = int(p["followers"]) if p.get("followers", "").isdigit() else None
        flw = int(p["following"]) if p.get("following", "").isdigit() else None
        rows.append((g["user_id"], g["username"], (p.get("name") or "")[:200],
                     "promoted", 0.0, (False if seed_bool else 0), False, SRC, flw, fol))

    q = ("INSERT INTO smart_set (user_id, username, display_name, category, score, is_seed, protected, "
         "seed_source, following_count, followers_count, added_at, last_crawled_at) "
         "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, now(), NULL) ON CONFLICT (user_id) DO NOTHING")
    B = 1000
    for i in range(0, len(rows), B):
        chunk = rows[i:i + B]
        await run(h, lambda c, ch=chunk: c.executemany(q, ch))
        print(f"  inserted {min(i+B, len(rows)):,}/{len(rows):,}", flush=True)

    after = await run(h, lambda c: c.fetchval("SELECT count(*) FROM smart_set"))
    seeds = await run(h, lambda c: c.fetchval("SELECT count(*) FROM smart_set WHERE is_seed"))
    tocrawl = await run(h, lambda c: c.fetchval("SELECT count(*) FROM smart_set WHERE last_crawled_at IS NULL"))
    await h["c"].close()
    print(f"\nsmart_set after : {after:,}  (+{after-before:,})", flush=True)
    print(f"  seed voters    : {seeds:,}", flush=True)
    print(f"  to crawl (last_crawled_at NULL): {tocrawl:,}", flush=True)


asyncio.run(main())

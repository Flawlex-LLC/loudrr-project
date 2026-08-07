"""Expansion v2: promote all non-voter accounts followed by >=150 elite into smart_set
(server-side INSERT..SELECT, no client round-trip). Bare rows (user_id + placeholders);
usernames/following resolved post-crawl. Idempotent via ON CONFLICT DO NOTHING."""
import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]
MIN_ELITE = 150


async def conn(tries=20):
    for _ in range(tries):
        try:
            return await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres",
                                         password=PW, database="loudrr_analytics", timeout=20, command_timeout=900)
        except Exception:
            await asyncio.sleep(6)
    raise RuntimeError("no DB connection")


async def main():
    pg = await conn()
    before = await pg.fetchval("SELECT count(*) FROM smart_set")
    print(f"smart_set before: {before:,}", flush=True)
    print("promoting >=150-elite non-voters (server-side, ~3-5 min)...", flush=True)
    res = await pg.execute(f'''
        INSERT INTO smart_set (user_id, category, score, is_seed, protected, seed_source, added_at, last_crawled_at)
        SELECT ind.followee_id, 'promoted_v2', 0, false, false, 'expand_v2_ge{MIN_ELITE}', now(), NULL
        FROM (SELECT followee_id, count(*) c FROM edges GROUP BY followee_id) ind
        WHERE ind.c >= {MIN_ELITE}
          AND NOT EXISTS (SELECT 1 FROM smart_set m WHERE m.user_id = ind.followee_id)
        ON CONFLICT (user_id) DO NOTHING''')
    print(f"  {res}", flush=True)
    after = await pg.fetchval("SELECT count(*) FROM smart_set")
    queue = await pg.fetchval("SELECT count(*) FROM smart_set WHERE last_crawled_at IS NULL")
    await pg.close()
    print(f"\nsmart_set: {before:,} -> {after:,}  (+{after-before:,} new voters)", flush=True)
    print(f"crawl queue (to crawl): {queue:,}", flush=True)


asyncio.run(main())

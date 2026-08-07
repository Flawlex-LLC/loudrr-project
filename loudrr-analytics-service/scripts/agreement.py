"""Agreement of our NEW (33,822-voter) scores vs Sorsa + TwitterScore on the overlap set."""
import asyncio, asyncpg, os, sqlite3
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]


def spearman(xs, ys):
    n = len(xs)
    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i]); r = [0.0] * n; i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]: j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1): r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys); mx = sum(rx) / n; my = sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n)) ** .5
    vy = sum((ry[i] - my) ** 2 for i in range(n)) ** .5
    return cov / (vx * vy) if vx * vy else 0.0


async def conn(tries=18):
    for _ in range(tries):
        try:
            return await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres",
                                         password=PW, database="loudrr_analytics", timeout=20, command_timeout=180)
        except Exception:
            await asyncio.sleep(6)
    raise RuntimeError("no conn")


async def main():
    cur = sqlite3.connect("data/harvest.db").cursor()
    rows = cur.execute("""SELECT user_id, sorsa_score, twitterscore FROM master_candidates
        WHERE sorsa_score IS NOT NULL AND twitterscore IS NOT NULL AND user_id IS NOT NULL""").fetchall()
    ids = [str(r[0]) for r in rows]
    pg = await conn(); print("connected, scoring overlap...", flush=True)
    recs = await pg.fetch('''SELECT e.followee_id fid, sum(s.score) sc FROM edges e
        JOIN smart_set s ON e.follower_id=s.user_id WHERE e.followee_id = ANY($1::text[])
        GROUP BY e.followee_id''', ids)
    await pg.close()
    score = {r["fid"]: float(r["sc"]) for r in recs}

    data = []
    for uid, so, ts in rows:
        s = str(uid)
        if s in score and score[s] > 0:
            data.append((score[s], float(so), float(ts)))
    ours = [d[0] for d in data]; sor = [d[1] for d in data]; tss = [d[2] for d in data]
    print(f"\nAGREEMENT on {len(data):,} overlap accounts (NEW 33,822-voter scores):")
    print(f"  OURS vs Sorsa        = {spearman(ours, sor):.3f}   (v1 was 0.839)")
    print(f"  OURS vs TwitterScore = {spearman(ours, tss):.3f}   (v1 was 0.948)")
    print(f"  Sorsa vs TwitterScore= {spearman(sor, tss):.3f}   (baseline: how much THEY agree)")


asyncio.run(main())

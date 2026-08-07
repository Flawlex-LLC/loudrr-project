"""3-way comparison: Loudrr (calibrated) vs Sorsa vs TwitterScore on the top ~150 accounts by
Sorsa (recognizable names, one light query — no chunk fragility). Uses the locked calibration.
Prints the table, agreement coefficients on this set, biggest disagreements, + saifmr20 spot-check.
"""
import asyncio, asyncpg, os, sqlite3, statistics
from dotenv import load_dotenv
import calibrate_v3 as cal
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]
OBAMA = "813286"; SORSA_OBAMA = 3094.426
EXTRA = {"saifmr20": ("1572093940106670080", 561)}  # (id, known Sorsa from user; not in our harvest)
TOPN = 150


def spearman(xs, ys):
    n = len(xs)
    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i]); r = [0.0]*n; i = 0
        while i < n:
            j = i
            while j+1 < n and v[order[j+1]] == v[order[i]]: j += 1
            avg = (i+j)/2.0+1
            for k in range(i, j+1): r[order[k]] = avg
            i = j+1
        return r
    rx, ry = ranks(xs), ranks(ys); mx = sum(rx)/n; my = sum(ry)/n
    cov = sum((rx[i]-mx)*(ry[i]-my) for i in range(n))
    vx = sum((rx[i]-mx)**2 for i in range(n))**.5; vy = sum((ry[i]-my)**2 for i in range(n))**.5
    return cov/(vx*vy) if vx*vy else 0.0


async def conn(tries=20):
    for _ in range(tries):
        try:
            c = await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres",
                                      password=PW, database="loudrr_analytics", timeout=20, command_timeout=600)
            try:
                await c.execute("SET work_mem='256MB'")
                await c.execute("SET max_parallel_workers_per_gather=0")
            except Exception:
                pass
            return c
        except Exception:
            await asyncio.sleep(6)
    raise RuntimeError("no conn")


async def raw_chunked(pg_holder, ids, chunk=40):
    """Fetch unified raw scores in small reconnect-safe chunks (the proxy drops heavy/long queries)."""
    out = {}
    for i in range(0, len(ids), chunk):
        part = ids[i:i+chunk]
        for attempt in range(6):
            try:
                recs = await pg_holder["c"].fetch('''SELECT e.followee_id fid, sum(s.score) sc FROM edges e
                    JOIN smart_set s ON e.follower_id=s.user_id WHERE e.followee_id = ANY($1::text[])
                    GROUP BY e.followee_id''', part)
                for r in recs: out[r["fid"]] = float(r["sc"])
                break
            except Exception as e:
                print(f"  chunk {i//chunk+1} retry {attempt+1}: {type(e).__name__}", flush=True)
                try: await pg_holder["c"].close()
                except Exception: pass
                pg_holder["c"] = await conn()
        print(f"  ...{min(i+chunk,len(ids))}/{len(ids)}", flush=True)
    return out


async def main():
    c = sqlite3.connect("data/harvest.db").cursor()
    rows = c.execute(f"""SELECT user_id, username, sorsa_score, twitterscore FROM master_candidates
        WHERE sorsa_score IS NOT NULL AND twitterscore IS NOT NULL AND user_id IS NOT NULL
        ORDER BY sorsa_score DESC LIMIT {TOPN}""").fetchall()
    ids = list(dict.fromkeys([str(r[0]) for r in rows] + [OBAMA] + [v[0] for v in EXTRA.values()]))
    h = {"c": await conn()}; print(f"connected; scoring {len(ids)} accounts (chunked)...", flush=True)
    raw = await raw_chunked(h, ids)
    try: await h["c"].close()
    except Exception: pass

    if OBAMA in raw and raw[OBAMA] > 0:
        A = (SORSA_OBAMA * cal.UPLIFT) / raw[OBAMA]
    else:  # robust fallback: median(sorsa/raw) * uplift over the set
        rr = [(raw[str(u)], so) for u, un, so, ts in rows if str(u) in raw and raw[str(u)] > 0]
        A = cal.UPLIFT * statistics.median(s/r for r, s in rr)
    print(f"anchor A={A:.5f}", flush=True)

    data = []
    for uid, un, so, ts in rows:
        s = str(uid)
        if s in raw and raw[s] > 0:
            data.append((un or s, cal.loudrr(A, raw[s]), float(so), float(ts)))
    ours = [d[1] for d in data]; sor = [d[2] for d in data]; tss = [d[3] for d in data]
    print(f"\n=== AGREEMENT on these {len(data)} accounts ===")
    print(f"  Loudrr vs Sorsa        = {spearman(ours,sor):.3f}")
    print(f"  Loudrr vs TwitterScore = {spearman(ours,tss):.3f}")
    print(f"  Sorsa  vs TwitterScore = {spearman(sor,tss):.3f}")

    data.sort(key=lambda d: -d[1])
    print(f"\n=== {len(data)} ACCOUNTS  (rank | handle | Loudrr | Sorsa | TwitterScore | L/Sorsa) ===")
    for i, (un, lo, so, ts) in enumerate(data, 1):
        print(f"{i:>3} @{un:<22}{lo:>7.0f}{so:>7.0f}{ts:>8.0f}{lo/so*100:>7.0f}%")

    rl = {d[0]: i for i, d in enumerate(sorted(data, key=lambda d:-d[1]))}
    rs = {d[0]: i for i, d in enumerate(sorted(data, key=lambda d:-d[2]))}
    print(f"\n=== biggest Loudrr-vs-Sorsa rank gaps ===")
    for un, lo, so, ts in sorted(data, key=lambda d: -(abs(rl[d[0]]-rs[d[0]])))[:10]:
        print(f"  @{un:<22} Loudrr {lo:>5.0f}(#{rl[un]+1})  Sorsa {so:>5.0f}(#{rs[un]+1})  TS {ts:>5.0f}")

    print(f"\n=== spot-check (off-harvest) ===")
    for un, (uid, sk) in EXTRA.items():
        r = raw.get(uid, 0.0)
        print(f"  @{un}: Loudrr={cal.loudrr(A,r):.0f}  Sorsa={sk}  (L/Sorsa={cal.loudrr(A,r)/sk*100:.0f}%)  voters-raw={r:.0f}")


if __name__ == "__main__":
    asyncio.run(main())

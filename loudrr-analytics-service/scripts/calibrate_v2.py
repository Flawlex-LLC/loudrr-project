"""Ratio-preserving calibration: fit Loudrr = A * raw^B (log-log regression of Sorsa on our raw
unified score), instead of quantile-mapping. A power-law preserves local ratios far better than
the quantile map, so the displayed waleswoosh/Obama spacing tracks the raw spacing (which already
matches Sorsa). Reports the new Loudrr scores + spacing, side by side with the old quantile map.
"""
import asyncio, asyncpg, os, csv, sqlite3, bisect
import numpy as np
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]
UPLIFT = 3400.0 / 3000.0  # Loudrr reads higher than Sorsa (your spec)


async def conn(tries=18):
    for _ in range(tries):
        try:
            return await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres",
                                         password=PW, database="loudrr_analytics", timeout=20, command_timeout=300)
        except Exception:
            await asyncio.sleep(6)
    raise RuntimeError("no conn")


async def raw_scores(pg, ids):
    recs = await pg.fetch('''SELECT e.followee_id fid, sum(s.score) sc FROM edges e
        JOIN smart_set s ON e.follower_id=s.user_id WHERE e.followee_id = ANY($1::text[])
        GROUP BY e.followee_id''', ids)
    return {r["fid"]: float(r["sc"]) for r in recs}


async def main():
    cur = sqlite3.connect("data/harvest.db").cursor()
    over = cur.execute("SELECT user_id, sorsa_score FROM master_candidates WHERE sorsa_score IS NOT NULL AND user_id IS NOT NULL").fetchall()
    pg = await conn(); print("connected.", flush=True)
    raw = await raw_scores(pg, [str(u) for u, _ in over])
    pairs = [(raw[str(u)], float(so)) for u, so in over if str(u) in raw and raw[str(u)] > 0]
    rs = np.array([p[0] for p in pairs]); ss = np.array([p[1] for p in pairs])
    n = len(pairs)

    # ── power-law fit: log(sorsa) = logA + B*log(raw) ──
    B, logA = np.polyfit(np.log(rs), np.log(ss), 1)
    A = np.exp(logA)
    print(f"fit on {n:,} overlap: Loudrr = {A:.4g} * raw^{B:.3f}  (x{UPLIFT:.3f} uplift)", flush=True)
    # quality of fit
    pred = A * np.power(rs, B)
    r2 = 1 - np.sum((np.log(ss) - np.log(pred)) ** 2) / np.sum((np.log(ss) - np.log(ss).mean()) ** 2)
    print(f"log-log R^2 = {r2:.3f}", flush=True)

    def loudrr_pow(r):
        return A * (r ** B) * UPLIFT if r > 0 else 0.0

    # old quantile map for comparison
    so = np.sort(rs); sso = np.sort(ss)
    def loudrr_q(r):
        if r <= 0: return 0.0
        p = bisect.bisect_right(so, r) / n
        x = p * (n - 1); lo = int(x); hi = min(lo + 1, n - 1); frac = x - lo
        return (sso[lo] + frac * (sso[hi] - sso[lo])) * UPLIFT

    name2id = {r["username"].lower(): r["user_id"] for r in await pg.fetch("SELECT user_id, username FROM smart_set WHERE username IS NOT NULL")}
    for row in csv.DictReader(open("data/exports/discovered_accounts.csv", encoding="utf-8")):
        name2id.setdefault((row["username"] or "").lower(), row["user_id"])
    sorsa_known = {str(u): float(s) for u, s in over}
    tests = ["elonmusk", "vitalikbuterin", "cz_binance", "barackobama", "greg16676935420", "karpathy", "waleswoosh", "0xblest_", "saylor", "punk6529"]
    tids = [(t, name2id[t]) for t in tests if t in name2id]
    traw = await raw_scores(pg, [u for _, u in tids])
    await pg.close()

    print(f"\n{'handle':<17}{'NEW(pow)':>9}{'OLD(quant)':>11}{'Sorsa':>8}", flush=True)
    print("-" * 45, flush=True)
    rec = {}
    for name, uid in tids:
        r = traw.get(uid, 0.0)
        rec[name] = loudrr_pow(r)
        sk = sorsa_known.get(uid)
        print(f"@{name:<16}{loudrr_pow(r):>9.0f}{loudrr_q(r):>11.0f}{(sk if sk else 0):>8.0f}", flush=True)

    ob = rec.get("barackobama", 1)
    print(f"\nSPACING (NEW power-law):  wale/Obama = {rec.get('waleswoosh',0)/ob*100:.0f}%   greg/Obama = {rec.get('greg16676935420',0)/ob*100:.0f}%", flush=True)
    print(f"SPACING (Sorsa actual) :  wale/Obama = 86%   greg/Obama = 99%", flush=True)


asyncio.run(main())

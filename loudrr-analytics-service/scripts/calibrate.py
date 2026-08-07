"""Calibrate raw unified scores -> Loudrr Score (Sorsa 0-5000 scale) via quantile mapping.
Preserves our ranking; reshapes the distribution to match Sorsa so the number looks native."""
import asyncio, asyncpg, os, csv, sqlite3, bisect
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]


async def conn(tries=18):
    for _ in range(tries):
        try:
            return await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres",
                                         password=PW, database="loudrr_analytics", timeout=20, command_timeout=180)
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
    over = cur.execute("""SELECT user_id, sorsa_score FROM master_candidates
        WHERE sorsa_score IS NOT NULL AND user_id IS NOT NULL""").fetchall()
    pg = await conn(); print("connected.", flush=True)
    raw = await raw_scores(pg, [str(u) for u, _ in over])

    pairs = [(raw[str(u)], float(so)) for u, so in over if str(u) in raw and raw[str(u)] > 0]
    sorted_ours = sorted(p[0] for p in pairs)
    sorted_sorsa = sorted(p[1] for p in pairs)
    n = len(sorted_ours)
    print(f"calibration fit on {n:,} overlap accounts (Sorsa range {sorted_sorsa[0]:.0f}-{sorted_sorsa[-1]:.0f})", flush=True)

    UPLIFT = 3400.0 / 3000.0  # per spec: Loudrr should read higher than Sorsa (Loudrr 3400 = Sorsa 3000)

    def loudrr(r):
        if r <= 0:
            return 0.0
        p = bisect.bisect_right(sorted_ours, r) / n            # our percentile
        x = p * (n - 1)
        lo = int(x); hi = min(lo + 1, n - 1); frac = x - lo    # interpolate on the Sorsa quantiles
        sorsa_equiv = sorted_sorsa[lo] + frac * (sorted_sorsa[hi] - sorted_sorsa[lo])
        return sorsa_equiv * UPLIFT

    # test set: recognizable accounts (resolve uids from smart_set + discovered csv)
    name2id = {r["username"].lower(): r["user_id"] for r in await pg.fetch("SELECT user_id, username FROM smart_set WHERE username IS NOT NULL")}
    for row in csv.DictReader(open("data/exports/discovered_accounts.csv", encoding="utf-8")):
        name2id.setdefault((row["username"] or "").lower(), row["user_id"])
    sorsa_known = {str(u): float(so) for u, so in over}
    tests = ["elonmusk", "vitalikbuterin", "cz_binance", "barackobama", "greg16676935420",
             "karpathy", "openai", "waleswoosh", "0xblest_", "saylor", "punk6529"]
    tids = [(t, name2id[t]) for t in tests if t in name2id]
    traw = await raw_scores(pg, [uid for _, uid in tids])
    await pg.close()

    print(f"\n{'handle':<18}{'LOUDRR':>8}{'(actual Sorsa)':>16}")
    print("-" * 44)
    for name, uid in tids:
        r = traw.get(uid, 0.0)
        ls = loudrr(r)
        sk = sorsa_known.get(uid)
        print(f"@{name:<17}{ls:>8.0f}{('  ' + format(sk, '.0f')) if sk else '  (not in Sorsa)':>16}")

    print("\nmapping at percentiles (raw -> Loudrr):")
    for p in [0.5, 0.9, 0.99, 0.999, 1.0]:
        idx = min(n - 1, int(p * n))
        print(f"  p{p*100:>5.1f}: raw {sorted_ours[idx]:>10.0f} -> Loudrr {loudrr(sorted_ours[idx]):.0f}")


asyncio.run(main())

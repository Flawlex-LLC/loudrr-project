"""Voter-quality exponent sweep: does weighting the unified score by voter_score^p (p>1)
de-compress the mid-tier (waleswoosh vs Obama) and improve Sorsa agreement? One pass over the
overlap set computing sum(score^p) for several p, then Spearman vs Sorsa + the wale/Obama ratio."""
import asyncio, asyncpg, os, csv, sqlite3
from dotenv import load_dotenv
from scipy.stats import spearmanr
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]
PS = [1.0, 1.5, 2.0, 2.5, 3.0]


async def main():
    cur = sqlite3.connect("data/harvest.db").cursor()
    over = [(str(u), float(so)) for u, so in cur.execute(
        "SELECT user_id, sorsa_score FROM master_candidates WHERE sorsa_score IS NOT NULL AND user_id IS NOT NULL").fetchall()]
    ids = [u for u, _ in over]
    sorsa = {u: so for u, so in over}

    pg = await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres", password=PW,
                               database="loudrr_analytics", timeout=20, command_timeout=600)
    await pg.execute("SET work_mem='384MB'"); await pg.execute("SET max_parallel_workers_per_gather=0")
    print("connected; computing sum(score^p) for all p in one pass...", flush=True)
    sel = ", ".join([f"sum(power(s.score, {p})) p{i}" for i, p in enumerate(PS)])
    rows = await pg.fetch(f"""SELECT e.followee_id fid, {sel}
        FROM edges e JOIN smart_set s ON e.follower_id=s.user_id
        WHERE e.followee_id = ANY($1::text[]) GROUP BY e.followee_id""", ids)
    by = {r["fid"]: r for r in rows}

    # resolve test-account ids
    name2id = {r["username"].lower(): r["user_id"] for r in await pg.fetch("SELECT user_id, username FROM smart_set WHERE username IS NOT NULL")}
    for row in csv.DictReader(open("data/exports/discovered_accounts.csv", encoding="utf-8")):
        name2id.setdefault((row["username"] or "").lower(), row["user_id"])
    tests = {n: name2id.get(n) for n in ["barackobama", "greg16676935420", "waleswoosh"]}
    tvals = await pg.fetch(f"""SELECT e.followee_id fid, {sel}
        FROM edges e JOIN smart_set s ON e.follower_id=s.user_id
        WHERE e.followee_id = ANY($1::text[]) GROUP BY e.followee_id""", [v for v in tests.values() if v])
    tby = {r["fid"]: r for r in tvals}
    await pg.close()

    print(f"\nSorsa: waleswoosh/Obama = {sorsa.get(tests['waleswoosh'],2654)/sorsa.get(tests['barackobama'],3094)*100:.0f}% (target)" if False else "")
    print(f"{'p':>5} {'Spearman vs Sorsa':>18} {'wale/Obama':>12} {'greg/Obama':>12}", flush=True)
    print("-" * 50, flush=True)
    for i, p in enumerate(PS):
        ours, sors = [], []
        for u in ids:
            r = by.get(u)
            if r and r[f"p{i}"] and r[f"p{i}"] > 0:
                ours.append(float(r[f"p{i}"])); sors.append(sorsa[u])
        rho = spearmanr(ours, sors).correlation
        ob = float(tby.get(tests["barackobama"], {f"p{i}": 1})[f"p{i}"] or 1)
        wa = float(tby.get(tests["waleswoosh"], {f"p{i}": 0})[f"p{i}"] or 0)
        gr = float(tby.get(tests["greg16676935420"], {f"p{i}": 0})[f"p{i}"] or 0)
        print(f"{p:>5} {rho:>18.4f} {wa/ob*100:>11.0f}% {gr/ob*100:>11.0f}%", flush=True)
    print("\n(Sorsa reference: wale/Obama=86%, greg/Obama=99%->actually 3050/3094=99%? check)", flush=True)
    print(f"Sorsa actual: wale/Obama={2654/3094*100:.0f}%  greg/Obama={3050/3094*100:.0f}%", flush=True)


asyncio.run(main())

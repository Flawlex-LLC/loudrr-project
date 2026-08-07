"""Fit the smart-followers vs reach blend to the vendors' real scores (data-driven, no guessing)."""
import asyncio, asyncpg, csv, sqlite3, os, bisect
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


def pctfn(sorted_arr):
    n = len(sorted_arr)
    return lambda v: bisect.bisect_right(sorted_arr, v) / n


async def main():
    cur = sqlite3.connect('data/harvest.db').cursor()
    rows = cur.execute("""SELECT user_id, username, sorsa_score, twitterscore, followers
        FROM master_candidates WHERE sorsa_score IS NOT NULL AND twitterscore IS NOT NULL
        AND followers IS NOT NULL AND followers > 0 AND user_id IS NOT NULL""").fetchall()
    ids = [str(r[0]) for r in rows]
    pg = await asyncpg.connect(host='213.199.54.248', port=5433, user='postgres',
                               password=PW, database='loudrr_analytics', timeout=90)
    recs = await pg.fetch("""SELECT e.followee_id fid, sum(s.score) sc FROM edges e
        JOIN smart_set s ON e.follower_id=s.user_id WHERE e.followee_id = ANY($1::text[])
        GROUP BY e.followee_id""", ids)
    await pg.close()
    crypto = {r['fid']: float(r['sc']) for r in recs}

    data = []  # (username, sorsa, ts, reach, cryptoscore)
    for uid, un, so, ts, fol in rows:
        s = str(uid)
        if s in crypto and crypto[s] > 0:
            data.append((un, float(so), float(ts), float(fol), crypto[s]))
    cs = sorted(d[4] for d in data); rc = sorted(d[3] for d in data)
    pc, pr = pctfn(cs), pctfn(rc)
    cs_pct = [pc(d[4]) for d in data]; rc_pct = [pr(d[3]) for d in data]
    so = [d[1] for d in data]; ts = [d[2] for d in data]

    print(f"calibration set: {len(data)} accounts (vendor scores + reach + our crypto score)\n")
    print(f"{'reach wt':>9}{'crypto wt':>10}{'Spear vs Sorsa':>16}{'Spear vs TS':>13}")
    print('-' * 48)
    best = None
    for a in [round(0.1 * i, 1) for i in range(11)]:  # a = crypto weight
        blend = [a * cs_pct[i] + (1 - a) * rc_pct[i] for i in range(len(data))]
        ss, st = spearman(blend, so), spearman(blend, ts)
        avg = (ss + st) / 2
        mark = ''
        if best is None or avg > best[0]:
            best = (avg, a, ss, st); mark = ''
        print(f"{1-a:>9.1f}{a:>10.1f}{ss:>16.3f}{st:>13.3f}")
    _, A, ss, st = best
    print(f"\nBEST BLEND: crypto={A:.1f} / reach={1-A:.1f}  -> Sorsa {ss:.3f}, TS {st:.3f}")
    print(f"(pure crypto was: Sorsa 0.839, TS 0.948)\n")

    # Obama vs greg under this blend
    dd = {r['username'].lower(): r for r in csv.DictReader(open('data/exports/discovered_accounts.csv', encoding='utf-8'))}
    print("Obama vs greg at each blend (crypto_pct / reach_pct -> blended_pct):")
    for un in ('barackobama', 'greg16676935420'):
        r = dd[un]; c = float(r['unified_score']); reach = float(r['x_followers'])
        cp, rp = pc(c), pr(reach)
        print(f"  @{r['username']:<16} crypto_pct={cp:.3f}  reach_pct={rp:.3f}")
        line = '    '
        for a in (1.0, 0.7, A, 0.4, 0.2):
            line += f"a={a:.1f}:{a*cp+(1-a)*rp:.3f}  "
        print(line)
    print("\n(when Obama's blended_pct > greg's, we match the vendors)")


asyncio.run(main())

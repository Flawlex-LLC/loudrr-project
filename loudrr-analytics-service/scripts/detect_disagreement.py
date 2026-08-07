"""Detect crazy-disagreement accounts among the top-5k-of-both seed candidates.
For each candidate present in BOTH vendors, compute its percentile in each and the gap.
Big gap = the two vendors strongly disagree => a disputed anchor we'd drop from the SEED
(it stays a scoreable target). python -m scripts.detect_disagreement
"""
import bisect
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TOPN = 5000
con = sqlite3.connect("data/harvest.db")
c = con.cursor()
sorsa = {u: s for u, s in c.execute(
    "SELECT user_id, sorsa_score FROM harvested_scores WHERE sorsa_score IS NOT NULL")}
ts = {u: (s, un, cat) for u, s, un, cat in c.execute(
    "SELECT user_id, twitterscore, username, categories FROM twitterscore_accounts WHERE twitterscore IS NOT NULL")}
con.close()

top_s = set(sorted(sorsa, key=lambda u: -sorsa[u])[:TOPN])
top_t = set(sorted(ts, key=lambda u: -ts[u][0])[:TOPN])
union = top_s | top_t
both = [u for u in union if u in sorsa and u in ts]
need_fill = len(union) - len(both)

s_all = sorted(sorsa.values()); t_all = sorted(v[0] for v in ts.values())
pct = lambda v, a: bisect.bisect_left(a, v) / max(1, len(a)) * 100

gaps = []
for u in both:
    ps, pt = pct(sorsa[u], s_all), pct(ts[u][0], t_all)
    gaps.append((abs(ps - pt), u, ts[u][1], sorsa[u], ts[u][0], ps, pt, ts[u][2]))
gaps.sort(reverse=True)

print(f"seed candidates (top {TOPN} of each, unioned): {len(union):,}")
print(f"  have BOTH scores (gap computable now): {len(both):,}")
print(f"  missing one score (need cross-vendor fill first): {need_fill:,}")
print("\ngap distribution (percentile-point disagreement):")
for thr in (20, 30, 40, 50, 60):
    n = sum(1 for g in gaps if g[0] > thr)
    print(f"  gap > {thr:>2}:  {n:>5}  ({n/max(1,len(both))*100:4.1f}% of candidates would be cut)")

CRAZY = 45
crazy = [g for g in gaps if g[0] > CRAZY]
print(f"\n=== CRAZY-DISAGREEMENT (gap > {CRAZY}) — {len(crazy)} accounts, would DROP from seed ===")
for g, u, un, ss, tt, ps, pt, cat in crazy[:20]:
    who = "SORSA loves" if ps > pt else "TS loves"
    print(f"  @{(un or '?'):<18} gap={g:4.0f}  sorsa~{ps:>3.0f}pct ts~{pt:>3.0f}pct  ({who:<11}) {cat or ''}")
print(f"\n  -> clean seed (gap <= {CRAZY}) would be ~{len(both)-len(crazy):,} of the {len(both):,} dual-scored candidates")
print("  (plus the cross-vendor-filled ones once we score the missing side)")

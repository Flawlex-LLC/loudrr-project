"""Differentiation analysis: Sorsa vs TwitterScore — where they agree/disagree.
Scales differ (Sorsa ~0-5000+, TwitterScore 0-1000), so we compare PERCENTILE rank within
each vendor. python -m scripts.vendor_diff
"""
import bisect
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("data/harvest.db")
c = con.cursor()

sorsa = {u: s for u, s in c.execute(
    "SELECT user_id, sorsa_score FROM harvested_scores WHERE sorsa_score IS NOT NULL")}
ts = {u: (s, un, cat) for u, s, un, cat in c.execute(
    "SELECT user_id, twitterscore, username, categories FROM twitterscore_accounts WHERE twitterscore IS NOT NULL")}
con.close()

both = set(sorsa) & set(ts)
print("=" * 70)
print("COVERAGE")
print("=" * 70)
print(f"  Sorsa-scored accounts      : {len(sorsa):,}")
print(f"  TwitterScore-scored        : {len(ts):,}")
print(f"  BOTH (overlap)             : {len(both):,}")
print(f"  Sorsa-only (need TS)       : {len(set(sorsa)-set(ts)):,}")
print(f"  TwitterScore-only (need Sorsa): {len(set(ts)-set(sorsa)):,}")

s_sorted = sorted(sorsa[u] for u in both)
t_sorted = sorted(ts[u][0] for u in both)
n = len(both)


def pct(val, arr):
    return bisect.bisect_left(arr, val) / max(1, len(arr))


rows = []
for u in both:
    ps, pt = pct(sorsa[u], s_sorted), pct(ts[u][0], t_sorted)
    rows.append((u, ts[u][1], sorsa[u], ts[u][0], ps, pt, ps - pt, ts[u][2]))

# Spearman ≈ Pearson on percentile ranks
import statistics as st
xs = [r[4] for r in rows]; ys = [r[5] for r in rows]
mx, my = st.mean(xs), st.mean(ys)
cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
den = (sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys)) ** 0.5
rho = cov/den if den else 0
mad = st.mean(abs(r[6]) for r in rows)
print("\n" + "=" * 70)
print("AGREEMENT")
print("=" * 70)
print(f"  rank correlation (Spearman ~): {rho:.3f}   (1=identical ranking, 0=unrelated)")
print(f"  mean |percentile gap|        : {mad*100:.1f} points")

print("\n" + "=" * 70)
print("SORSA RATES HIGHER than TwitterScore (top disagreements)")
print("=" * 70)
for u, un, ss, tt, ps, ptv, d, cat in sorted(rows, key=lambda r: -r[6])[:12]:
    print(f"  @{(un or '?'):<18} sorsa~{ps*100:>4.0f}pct  ts~{ptv*100:>4.0f}pct  (sorsa={ss:.0f} ts={tt:.0f}) {cat or ''}")
print("\n" + "=" * 70)
print("TWITTERSCORE RATES HIGHER than Sorsa (top disagreements)")
print("=" * 70)
for u, un, ss, tt, ps, ptv, d, cat in sorted(rows, key=lambda r: r[6])[:12]:
    print(f"  @{(un or '?'):<18} sorsa~{ps*100:>4.0f}pct  ts~{ptv*100:>4.0f}pct  (sorsa={ss:.0f} ts={tt:.0f}) {cat or ''}")

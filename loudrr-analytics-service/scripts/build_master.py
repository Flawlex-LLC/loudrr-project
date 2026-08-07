"""Build the MASTER seed table: union of top-5k-Sorsa and top-5k-TwitterScore, every row
carrying BOTH vendor scores + percentiles + gap + a COMBINED quality score, plus
TwitterScore's category/tags/followers and both seen_counts.

Quality gate (two stages, both keep rows as scoreable targets — only in_seed changes):
  1. crazy-disagreement (gap > CRAZY): the two vendors wildly contradict each other -> not an
     anchor.
  2. quality cut: of what's left, keep only the top SEED_CUT by COMBINED percentile (mean of
     both vendors). The union's bottom is one-vendor-only filler (high in one, ~0 in the
     other); ranking by combined quality drops it so every anchor is strong in BOTH vendors.

Writes TWO tables:
  master_seed       — ONLY the best SEED_CUT cross-validated anchors (the clean master list).
  master_candidates — the full union pool (backup / scoreable targets), with in_seed flags.

Re-runnable: drops + rebuilds both. python -m scripts.build_master [SEED_CUT]
"""
import bisect
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TOPN = 5000
CRAZY = 40        # percentile-point gap above which the two vendors "crazy-disagree"
MIN_INDEG = 3     # min curated-influencer in-degree to corroborate a no-category account
SEED_CUT = int(sys.argv[1]) if len(sys.argv) > 1 else 3000  # keep this many top-quality anchors

con = sqlite3.connect("data/harvest.db")
c = con.cursor()

sorsa = {u: (s, un, cat, sc) for u, s, un, cat, sc in c.execute(
    "SELECT user_id, sorsa_score, username, category, seen_count FROM harvested_scores "
    "WHERE sorsa_score IS NOT NULL")}
ts = {u: row for u, *rest in [(r[0], *r[1:]) for r in c.execute(
    "SELECT user_id, twitterscore, username, name, categories, tags, followers, "
    "smart_followers, seen_count FROM twitterscore_accounts WHERE twitterscore IS NOT NULL")]
    for row in [rest]}

top_s = set(sorted(sorsa, key=lambda u: -sorsa[u][0])[:TOPN])
top_t = set(sorted(ts, key=lambda u: -ts[u][0])[:TOPN])
union = top_s | top_t

s_all = sorted(v[0] for v in sorsa.values())
t_all = sorted(v[0] for v in ts.values())
pct = lambda v, a: (bisect.bisect_left(a, v) / max(1, len(a)) * 100) if v is not None else None

# ---- assemble every candidate with a combined quality score ----
cand = []
for u in union:
    s = sorsa.get(u); t = ts.get(u)
    ss = s[0] if s else None
    tt = t[0] if t else None
    un = (t[1] if t else None) or (s[1] if s else None)
    name = t[2] if t else None
    cat = (t[3] if t else None) or (s[2] if s else None)   # TS categories, else Sorsa category
    tags = t[4] if t else None
    fol = t[5] if t else None
    smf = t[6] if t else None
    ts_seen = t[7] if t else None
    sorsa_seen = s[3] if s else None
    ps, pt = pct(ss, s_all), pct(tt, t_all)
    gap = abs(ps - pt) if (ps is not None and pt is not None) else None
    pcts = [p for p in (ps, pt) if p is not None]
    combined = sum(pcts) / len(pcts) if pcts else 0.0
    cand.append(dict(u=u, un=un, name=name, ss=ss, tt=tt, ps=ps, pt=pt, gap=gap,
                     cat=cat, tags=tags, fol=fol, smf=smf, ts_seen=ts_seen,
                     sorsa_seen=sorsa_seen, combined=combined))

# Corroboration gate: a vendor score alone is biased (Sorsa over-rates NFT/degen, giving
# random ~8k-follower accounts an 85th-pct score). Require an INDEPENDENT signal of real
# influence — a real TwitterScore category, OR being followed by >=MIN_INDEG curated
# influencers in our follow graph (in-degree). The follow graph is ground truth: known elite
# sit at in-degree 42-1695, the randoms at exactly 1.
def corroborated(r):
    cat = (r["cat"] or "").strip()
    has_cat = bool(cat) and cat not in ("No category", "unknown")
    return has_cat or (r["ts_seen"] or 0) >= MIN_INDEG

is_crazy = lambda r: r["gap"] is not None and r["gap"] > CRAZY
# rank ONLY corroborated, non-crazy candidates by combined quality; top SEED_CUT anchor
eligible = [r for r in cand if not is_crazy(r) and corroborated(r)]
eligible.sort(key=lambda r: -r["combined"])
seed_ids = {r["u"] for r in eligible[:SEED_CUT]}
keep = eligible

# master_candidates = the FULL union pool (backup / scoreable targets, with flags);
# master_seed      = ONLY the best SEED_CUT cross-validated anchors (the clean master).
_COLS = """user_id TEXT PRIMARY KEY, username TEXT, name TEXT,
    sorsa_score REAL, twitterscore REAL, sorsa_pct REAL, ts_pct REAL, gap REAL,
    combined_pct REAL, quality_rank INTEGER,
    category TEXT, tags TEXT, followers INTEGER, smart_followers INTEGER,
    sorsa_seen INTEGER, ts_seen INTEGER,
    in_seed INTEGER, drop_reason TEXT, need_fill TEXT"""
for tbl in ("master_candidates", "master_seed"):
    c.execute(f"DROP TABLE IF EXISTS {tbl}")
    c.execute(f"CREATE TABLE {tbl} ({_COLS})")

rank_of = {r["u"]: i + 1 for i, r in enumerate(keep)}
n_in = n_crazy = n_cut = n_uncorr = n_fill = 0
for r in cand:
    u = r["u"]
    qrank = rank_of.get(u)
    if is_crazy(r):
        in_seed, reason = 0, f"crazy-disagreement gap={r['gap']:.0f}"; n_crazy += 1
    elif not corroborated(r):
        in_seed, reason = 0, "no-corroboration (vendor-score only)"; n_uncorr += 1
    elif u not in seed_ids:
        in_seed, reason = 0, "below-quality-cut"; n_cut += 1
    else:
        in_seed, reason = 1, None; n_in += 1
    fill = None
    if r["ss"] is None:
        fill = "needs-sorsa-score"; n_fill += 1
    elif r["tt"] is None:
        fill = "needs-ts-score"; n_fill += 1
    vals = (u, r["un"], r["name"], r["ss"], r["tt"], r["ps"], r["pt"], r["gap"],
            r["combined"], qrank, r["cat"], r["tags"], r["fol"], r["smf"],
            r["sorsa_seen"], r["ts_seen"], in_seed, reason, fill)
    ph = "(" + ",".join("?" * len(vals)) + ")"
    c.execute(f"INSERT INTO master_candidates VALUES {ph}", vals)
    if in_seed:
        c.execute(f"INSERT INTO master_seed VALUES {ph}", vals)

con.commit()
# floor of the seed (lowest-quality anchor that still made the cut)
floor = keep[min(SEED_CUT, len(keep)) - 1]
con.close()
print(f"master_seed built: {len(union):,} candidates")
print(f"  IN-SEED anchors (corroborated, top {SEED_CUT} by quality): {n_in:,}")
print(f"  dropped — crazy-disagreement (gap>{CRAZY})           : {n_crazy:,}")
print(f"  dropped — no corroboration (vendor-score-only random): {n_uncorr:,}")
print(f"  dropped — below quality cut                         : {n_cut:,}")
print(f"  (rows still missing a 2nd vendor score              : {n_fill:,})")
print(f"  seed floor: @{floor['un']} combined~{floor['combined']:.0f}pct "
      f"(sorsa~{(floor['ps'] or 0):.0f} ts~{(floor['pt'] or 0):.0f})")
print(f"\n  master_seed       = {n_in:,} clean anchors (the master list)")
print(f"  master_candidates = {len(union):,} full pool (backup / scoreable targets)")
print("  -> SELECT * FROM master_seed ORDER BY quality_rank")

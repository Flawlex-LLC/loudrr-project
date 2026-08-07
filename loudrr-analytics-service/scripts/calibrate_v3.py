"""CANONICAL Loudrr calibration (locked).

raw unified score (= sum of voter PageRank weights of an account's elite followers)  ->  Loudrr 0..6000.

Shape: soft-FLOOR (bottom) + LINEAR (mid-tier) + log soft-CAP (top).
  - linear mid-tier preserves the TRUE score gaps, so the displayed spacing == our model's spacing
    == Sorsa's (e.g. waleswoosh = 87% of Obama, matching Sorsa's 86%).
  - log soft-cap keeps the mega-accounts ordered (Elon>Vitalik>cz) under CAP instead of flattening.
  - soft-floor lifts tiny accounts to FLOOR (mirrors Sorsa's ~550 floor) so nothing crashes to ~0;
    with the uplift, small accounts read ~13% above their Sorsa value (e.g. blest 536 -> 607).
A is anchored so a known reference account (Obama) maps to Sorsa(Obama) * UPLIFT.
Calibration is monotonic => rank-agreement with Sorsa (0.823) is unchanged by any of this.
"""
import asyncio, asyncpg, os, csv, math
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]

UPLIFT = 3400.0 / 3000.0   # Loudrr reads ~13% above Sorsa (spec: Loudrr 3400 == Sorsa 3000)
CAP = 6000.0
KHI = 4000.0               # top soft-cap knee
S = 1000.0                 # soft-cap log scale
FLOOR = 550.0              # minimum displayed score
KLO = 666.0                # bottom soft-floor knee


def loudrr(A, r):
    if r <= 0:
        return FLOOR
    x = A * r
    if x < KLO:
        return FLOOR + (x / KLO) * (KLO - FLOOR)          # soft-floor
    if x <= KHI:
        return x                                          # linear mid-tier (true spacing)
    return KHI + S * math.log(1 + (x - KHI) / S)          # log soft-cap top


# reference Sorsa scores for anchoring + display
SORSA = {"elonmusk": 5165, "vitalikbuterin": 5096, "cz_binance": 4185, "punk6529": 4273,
         "saylor": 3682, "karpathy": 3309, "barackobama": 3094, "greg16676935420": 3050,
         "waleswoosh": 2654, "0xblest_": 536}
ANCHOR = "barackobama"


async def main():
    m = {}
    for r in csv.DictReader(open("data/exports/discovered_accounts.csv", encoding="utf-8")):
        u = (r.get("username") or "").lower()
        if u:
            m[u] = r.get("user_id")
    c = await asyncpg.connect(host="213.199.54.248", port=5433, user="postgres", password=PW,
                              database="loudrr_analytics", timeout=15, command_timeout=120)
    raw = {}
    for n in SORSA:
        uid = m.get(n.lower()) or await c.fetchval("SELECT user_id FROM smart_set WHERE lower(username)=lower($1) LIMIT 1", n)
        raw[n] = float(await c.fetchval(
            "SELECT coalesce(sum(s.score),0) FROM edges e JOIN smart_set s ON e.follower_id=s.user_id WHERE e.followee_id=$1", uid) or 0)
    await c.close()

    A = (SORSA[ANCHOR] * UPLIFT) / raw[ANCHOR]   # anchor the linear region on the reference account
    print(f"A={A:.5f}  FLOOR={FLOOR:.0f} KLO={KLO:.0f} KHI={KHI:.0f} CAP~{CAP:.0f}  uplift={UPLIFT:.3f}")
    print(f"{'handle':<17}{'Loudrr':>8}{'Sorsa':>8}{'L/Sorsa':>9}")
    print("-" * 42)
    L = {}
    for n in SORSA:
        L[n] = loudrr(A, raw[n])
        print(f"@{n:<16}{L[n]:>8.0f}{SORSA[n]:>8}{L[n]/SORSA[n]*100:>8.0f}%")
    ob = L[ANCHOR]
    print(f"\nspacing: wale/Obama={L['waleswoosh']/ob*100:.0f}%  greg/Obama={L['greg16676935420']/ob*100:.0f}%   (Sorsa: 86%, 99%)")


if __name__ == "__main__":
    asyncio.run(main())
